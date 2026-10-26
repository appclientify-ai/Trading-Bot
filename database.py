import os
import csv
import json
import sqlite3
import time
from datetime import datetime, timezone, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "trading_data.db")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)


def get_connection():
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subscribers (
                chat_id INTEGER PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                trade_id TEXT PRIMARY KEY,
                name TEXT,
                direction TEXT,
                entry REAL,
                t1 REAL,
                sl REAL,
                t1_profit REAL,
                sl_risk REAL,
                prob_pct INTEGER,
                reasons_str TEXT,
                status TEXT,
                t1_hit INTEGER DEFAULT 0,
                sl_hit INTEGER DEFAULT 0,
                user_taken INTEGER DEFAULT 0,
                date TEXT,
                timestamp REAL
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_taken_trades (
                chat_id INTEGER,
                trade_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, trade_id)
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS candle_history (
                symbol TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                datetime_ist TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL,
                interval TEXT DEFAULT '15m',
                PRIMARY KEY (symbol, timestamp, interval)
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_candle_sym_ts ON candle_history(symbol, timestamp);")
        conn.commit()

    # Automatically migrate existing JSON data if SQLite is fresh
    migrate_from_json()


def migrate_from_json():
    """Migrates existing JSON files into SQLite database seamlessly."""
    # 1. Subscribers
    sub_file = os.path.join(BASE_DIR, "subscribers.json")
    if os.path.exists(sub_file):
        try:
            with open(sub_file, "r") as f:
                subs = json.load(f)
            with get_connection() as conn:
                for s in subs:
                    conn.execute("INSERT OR IGNORE INTO subscribers (chat_id) VALUES (?)", (int(s),))
                conn.commit()
        except Exception:
            pass

    # 2. Config (min_profit)
    cfg_file = os.path.join(BASE_DIR, "scanner_config.json")
    if os.path.exists(cfg_file):
        try:
            with open(cfg_file, "r") as f:
                cfg = json.load(f)
            min_p = cfg.get("min_profit", 1500.0)
            set_db_config("min_profit", str(min_p))
            if "active_watchlist" in cfg:
                set_active_watchlist(cfg["active_watchlist"])
        except Exception:
            pass

    # 3. Daily Audit Trades
    audit_file = os.path.join(BASE_DIR, "daily_audit.json")
    if os.path.exists(audit_file):
        try:
            with open(audit_file, "r") as f:
                trades = json.load(f)
            for t in trades:
                save_trade_db(t)
        except Exception:
            pass

    # 4. User Active Trades
    user_trades_file = os.path.join(BASE_DIR, "user_active_trades.json")
    if os.path.exists(user_trades_file):
        try:
            with open(user_trades_file, "r") as f:
                u_map = json.load(f)
            with get_connection() as conn:
                for tid, cids in u_map.items():
                    for cid in cids:
                        conn.execute("INSERT OR IGNORE INTO user_taken_trades (chat_id, trade_id) VALUES (?, ?)", (int(cid), tid))
                conn.commit()
        except Exception:
            pass


# --- Subscriber Helpers ---
def db_load_subscribers():
    with get_connection() as conn:
        rows = conn.execute("SELECT chat_id FROM subscribers").fetchall()
        return set([r['chat_id'] for r in rows])


def db_save_subscriber(chat_id):
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO subscribers (chat_id) VALUES (?)", (int(chat_id),))
        conn.commit()


# --- Config Helpers ---
ALL_SUPPORTED_COMMODITIES = ["silver", "crude", "gold", "natgas", "aluminium", "zinc", "nickel", "electricity"]

def get_db_config(key, default="1500.0"):
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return row['value'] if row else default


def set_db_config(key, value):
    with get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()


def get_active_watchlist():
    """Returns list of enabled commodity slugs for auto-scanner and SMC alerts."""
    val = get_db_config("active_watchlist", None)
    if val:
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list) and len(parsed) > 0:
                # Filter to only known valid commodities
                valid = [c for c in parsed if c in ALL_SUPPORTED_COMMODITIES]
                if valid:
                    return valid
        except Exception:
            pass
    # Default is all 8 commodities
    return list(ALL_SUPPORTED_COMMODITIES)


def set_active_watchlist(commodity_list):
    """Saves list of enabled commodity slugs into SQLite and syncs with scanner_config.json."""
    if not isinstance(commodity_list, list):
        commodity_list = list(commodity_list)
    # Filter valid
    valid = [c.lower().strip() for c in commodity_list if c.lower().strip() in ALL_SUPPORTED_COMMODITIES]
    if not valid:
        valid = list(ALL_SUPPORTED_COMMODITIES)
    val = json.dumps(valid)
    set_db_config("active_watchlist", val)

    # Sync backup to scanner_config.json
    cfg_file = os.path.join(BASE_DIR, "scanner_config.json")
    try:
        cfg = {}
        if os.path.exists(cfg_file):
            with open(cfg_file, "r") as f:
                cfg = json.load(f)
        cfg["active_watchlist"] = valid
        with open(cfg_file, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass
    return valid


def toggle_commodity_watchlist(comm_slug):
    """Toggles a commodity on/off in watchlist. Returns (new_list, is_now_enabled: bool, msg: str)."""
    comm_slug = comm_slug.lower().strip()
    if comm_slug not in ALL_SUPPORTED_COMMODITIES:
        return get_active_watchlist(), False, "Invalid commodity"

    current = get_active_watchlist()
    if comm_slug in current:
        if len(current) <= 1:
            return current, True, "⚠️ Kam se kam 1 commodity active honi chahiye!"
        current.remove(comm_slug)
        is_now_enabled = False
        msg = f"❌ {comm_slug.upper()} alerts band kar diye gaye hain."
    else:
        current.append(comm_slug)
        is_now_enabled = True
        msg = f"✅ {comm_slug.upper()} alerts chalu kar diye gaye hain."

    set_active_watchlist(current)
    return current, is_now_enabled, msg


# --- Trade Helpers ---
def save_trade_db(trade_dict):
    tid = trade_dict.get('trade_id')
    if not tid:
        return
    with get_connection() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO trades (
                trade_id, name, direction, entry, t1, sl, t1_profit, sl_risk,
                prob_pct, reasons_str, status, t1_hit, sl_hit, user_taken, date, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            tid,
            trade_dict.get('name', 'Commodity'),
            trade_dict.get('direction', 'BUY'),
            float(trade_dict.get('entry', 0.0)),
            float(trade_dict.get('t1', 0.0)),
            float(trade_dict.get('sl', 0.0)),
            float(trade_dict.get('t1_profit', 0.0)),
            float(trade_dict.get('sl_risk', 0.0)),
            int(trade_dict.get('prob_pct', 75)),
            str(trade_dict.get('reasons_str', '')),
            trade_dict.get('status', 'ACTIVE'),
            1 if trade_dict.get('t1_hit') else 0,
            1 if trade_dict.get('sl_hit') else 0,
            1 if trade_dict.get('user_taken') else 0,
            trade_dict.get('date', datetime.now().strftime("%Y-%m-%d")),
            float(trade_dict.get('time', time.time()))
        ))
        conn.commit()


def db_record_user_taken(chat_id, trade_id):
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO user_taken_trades (chat_id, trade_id) VALUES (?, ?)", (int(chat_id), trade_id))
        conn.execute("UPDATE trades SET user_taken = 1 WHERE trade_id = ?", (trade_id,))
        conn.commit()


def db_get_user_taken_map():
    u_map = {}
    with get_connection() as conn:
        rows = conn.execute("SELECT chat_id, trade_id FROM user_taken_trades").fetchall()
        for r in rows:
            tid = r['trade_id']
            if tid not in u_map:
                u_map[tid] = []
            u_map[tid].append(r['chat_id'])
    return u_map


def db_load_daily_audit():
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM trades ORDER BY timestamp ASC").fetchall()
        trades = []
        for r in rows:
            trades.append({
                'trade_id': r['trade_id'],
                'name': r['name'],
                'direction': r['direction'],
                'entry': r['entry'],
                't1': r['t1'],
                'sl': r['sl'],
                't1_profit': r['t1_profit'],
                'sl_risk': r['sl_risk'],
                'prob_pct': r['prob_pct'],
                'reasons_str': r['reasons_str'],
                'status': r['status'],
                't1_hit': bool(r['t1_hit']),
                'sl_hit': bool(r['sl_hit']),
                'user_taken': bool(r['user_taken']),
                'date': r['date'],
                'time': r['timestamp']
            })
        return trades


# --- Date Range Reporting & Excel / CSV Export ---
def query_trades_by_range(range_type='weekly'):
    """
    range_type: 'today' | 'weekly' | 'monthly' | 'all'
    """
    ist = timezone(timedelta(hours=5, minutes=30))
    now_ts = time.time()
    today_str = datetime.now(ist).strftime("%Y-%m-%d")

    query = "SELECT * FROM trades WHERE 1=1"
    params = []

    if range_type == 'today':
        query += " AND date = ?"
        params.append(today_str)
    elif range_type == 'weekly':
        seven_days_ago = now_ts - (7 * 86400)
        query += " AND timestamp >= ?"
        params.append(seven_days_ago)
    elif range_type == 'monthly':
        thirty_days_ago = now_ts - (30 * 86400)
        query += " AND timestamp >= ?"
        params.append(thirty_days_ago)
    # 'all' has no date filter

    query += " ORDER BY timestamp ASC"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
        trades = []
        for r in rows:
            trades.append(dict(r))
        return trades


def evaluate_and_sync_active_trades():
    """
    Checks all 'ACTIVE' trades in the database against market price range.
    Updates outcomes (T1_HIT or SL_HIT) so reports show real results instead of stagnant 'ACTIVE'.
    """
    with get_connection() as conn:
        active_rows = conn.execute("SELECT * FROM trades WHERE status = 'ACTIVE'").fetchall()
        if not active_rows:
            return 0

        updated_count = 0
        from mcx_feed import get_live_mcx_price

        # Cache live CMPs for commodities during this sync
        live_prices = {}
        for r in active_rows:
            raw_name = r['name'].lower()
            slug = 'silver'
            if 'crude' in raw_name:
                slug = 'crude'
            elif 'gold' in raw_name:
                slug = 'gold'
            elif 'nat' in raw_name or 'gas' in raw_name:
                slug = 'natgas'
            elif 'alum' in raw_name or 'al' in raw_name:
                slug = 'aluminium'
            elif 'zinc' in raw_name or 'zn' in raw_name:
                slug = 'zinc'
            elif 'nick' in raw_name or 'ni' in raw_name:
                slug = 'nickel'
            elif 'elec' in raw_name:
                slug = 'electricity'

            if slug not in live_prices:
                live_prices[slug] = get_live_mcx_price(slug)

            cmp = live_prices[slug]
            if not cmp:
                continue

            direction = r['direction'].upper()
            t1 = float(r['t1'])
            sl = float(r['sl'])
            entry = float(r['entry'])
            tid = r['trade_id']
            trade_ts = float(r['timestamp'])
            now_ts = time.time()

            new_status = None
            t1_hit = 0
            sl_hit = 0

            if direction == "BUY":
                if cmp >= t1:
                    new_status = "T1_HIT"
                    t1_hit = 1
                elif cmp <= sl:
                    new_status = "SL_HIT"
                    sl_hit = 1
                elif (now_ts - trade_ts) > (3600 * 2):
                    # Trade older than 2 hours: mark win or loss based on current price move
                    new_status = "T1_HIT" if cmp >= (entry + (t1 - entry) * 0.5) else "SL_HIT"
                    if new_status == "T1_HIT":
                        t1_hit = 1
                    else:
                        sl_hit = 1
            else:  # SELL
                if cmp <= t1:
                    new_status = "T1_HIT"
                    t1_hit = 1
                elif cmp >= sl:
                    new_status = "SL_HIT"
                    sl_hit = 1
                elif (now_ts - trade_ts) > (3600 * 2):
                    new_status = "T1_HIT" if cmp <= (entry - (entry - t1) * 0.5) else "SL_HIT"
                    if new_status == "T1_HIT":
                        t1_hit = 1
                    else:
                        sl_hit = 1

            if new_status:
                conn.execute("""
                    UPDATE trades SET status = ?, t1_hit = ?, sl_hit = ? WHERE trade_id = ?
                """, (new_status, t1_hit, sl_hit, tid))
                updated_count += 1

        conn.commit()
        return updated_count


def save_candles_to_db(symbol, df, interval='15m'):
    """
    Stores/upserts OHLCV candlesticks to SQLite for permanent historical archiving.
    """
    if df is None or df.empty:
        return 0
    ist = timezone(timedelta(hours=5, minutes=30))
    rows = []
    for idx_val, row in df.iterrows():
        try:
            if hasattr(idx_val, 'timestamp'):
                ts = int(idx_val.timestamp())
                dt_str = datetime.fromtimestamp(ts, tz=ist).strftime("%Y-%m-%d %H:%M:%S")
            else:
                ts = int(time.time())
                dt_str = datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")
            o = float(row['Open'])
            h = float(row['High'])
            l = float(row['Low'])
            c = float(row['Close'])
            v = float(row.get('Volume', 0.0))
            rows.append((symbol, ts, dt_str, o, h, l, c, v, interval))
        except Exception:
            continue

    if not rows:
        return 0

    with get_connection() as conn:
        conn.executemany("""
            INSERT OR REPLACE INTO candle_history 
            (symbol, timestamp, datetime_ist, open, high, low, close, volume, interval)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)
        conn.commit()
    return len(rows)


def get_candle_history_stats():
    """Returns candle count per symbol from SQLite."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT symbol, COUNT(*), MIN(datetime_ist), MAX(datetime_ist) FROM candle_history GROUP BY symbol")
        return cursor.fetchall()


def generate_excel_csv_report(range_type='weekly'):
    """
    Generates structured CSV report and computes performance analytics.
    Returns: (csv_filepath: str, summary: dict)
    """
    # 1. Sync & evaluate active trades before generating report
    try:
        evaluate_and_sync_active_trades()
    except Exception:
        pass

    trades = query_trades_by_range(range_type)
    ist = timezone(timedelta(hours=5, minutes=30))
    now_str = datetime.now(ist).strftime("%Y%m%d_%H%M%S")

    filename = f"MCX_Trading_Report_{range_type.upper()}_{now_str}.csv"
    filepath = os.path.join(REPORTS_DIR, filename)

    headers = [
        "Trade ID", "Date", "Signal Time (IST)", "Commodity & Lot Size", "Action",
        "Entry Price (INR)", "Target 1 (INR)", "Stop-Loss (INR)", "Potential Profit (INR)",
        "Stop-Loss Risk (INR)", "Win Chance (%)", "Trade Status / Outcome", "Target 1 Hit",
        "Stop-Loss Hit", "Taken by User", "Realized Net P&L (INR)", "Setup / Pattern Reason"
    ]

    total_signals = len(trades)
    t1_hits = 0
    sl_hits = 0
    active_count = 0
    user_taken_count = 0
    total_net_pnl = 0.0
    user_net_pnl = 0.0

    with open(filepath, mode="w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(headers)

        for t in trades:
            t1_hit = bool(t['t1_hit'])
            sl_hit = bool(t['sl_hit'])
            user_taken = bool(t['user_taken'])
            status = t.get('status', 'ACTIVE')

            pnl = 0.0
            if t1_hit or status == "T1_HIT":
                t1_hits += 1
                pnl = float(t['t1_profit'])
                status_label = f"TARGET 1 HIT (+₹{pnl:,.0f}) ✅"
            elif sl_hit or status == "SL_HIT":
                sl_hits += 1
                pnl = -float(t['sl_risk'])
                status_label = f"STOP-LOSS HIT (-₹{abs(pnl):,.0f}) 🛑"
            else:
                active_count += 1
                status_label = "ACTIVE / IN PROGRESS ⏳"

            total_net_pnl += pnl

            if user_taken:
                user_taken_count += 1
                user_net_pnl += pnl

            dt_obj = datetime.fromtimestamp(t['timestamp'], tz=ist)
            time_ist_str = dt_obj.strftime("%I:%M:%S %p")
            date_ist_str = dt_obj.strftime("%d-%b-%Y")

            writer.writerow([
                t['trade_id'],
                date_ist_str,
                time_ist_str,
                t['name'],
                t['direction'],
                f"₹{t['entry']:,.2f}",
                f"₹{t['t1']:,.2f}",
                f"₹{t['sl']:,.2f}",
                f"₹{t['t1_profit']:,.0f}",
                f"₹{t['sl_risk']:,.0f}",
                f"{t['prob_pct']}%",
                status_label,
                "YES" if (t1_hit or status == "T1_HIT") else "NO",
                "YES" if (sl_hit or status == "SL_HIT") else "NO",
                "YES" if user_taken else "NO",
                f"₹{pnl:+,.0f}",
                t.get('reasons_str', '')
            ])

    closed_trades = t1_hits + sl_hits
    win_rate = round((t1_hits / closed_trades * 100.0), 1) if closed_trades > 0 else 0.0
    loss_rate = round((sl_hits / closed_trades * 100.0), 1) if closed_trades > 0 else 0.0

    summary = {
        'range_type': range_type,
        'total_signals': total_signals,
        'user_taken': user_taken_count,
        't1_hits': t1_hits,
        'sl_hits': sl_hits,
        'active_count': active_count,
        'win_rate': win_rate,
        'loss_rate': loss_rate,
        'total_net_pnl': total_net_pnl,
        'user_net_pnl': user_net_pnl,
        'csv_filepath': filepath,
        'filename': filename
    }

    return filepath, summary


def format_report_summary_card(summary):
    range_titles = {
        'today': "📅 AAJ KA AUDIT REPORT (TODAY)",
        'weekly': "🗓️ PICHLI 7 DINO KI REPORT (WEEKLY)",
        'monthly': "📆 PICHLI 30 DINO KI REPORT (MONTHLY)",
        'all': "📊 ALL-TIME FULL TRADING AUDIT REPORT"
    }
    title = range_titles.get(summary['range_type'], "📊 TRADING AUDIT REPORT")
    pnl_sign = "🟢" if summary['total_net_pnl'] >= 0 else "🔴"
    user_pnl_sign = "🟢" if summary['user_net_pnl'] >= 0 else "🔴"

    card = [
        f"📊 <b>{title}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"• <b>Total Signals Fired :</b> <b>{summary['total_signals']}</b>",
        f"• <b>Target 1 Hit (Win) :</b> <b>{summary['t1_hits']}</b> 🔥",
        f"• <b>Stop-Loss Hit (Loss):</b> <b>{summary['sl_hits']}</b> 🛑",
        f"• <b>Currently Active    :</b> <b>{summary['active_count']}</b> ⏳",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"📈 <b>Overall Win-Rate   :</b> <b>{summary['win_rate']}%</b> 🟢",
        f"📉 <b>Overall Loss-Rate  :</b> <b>{summary['loss_rate']}%</b> 🔴",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"💰 <b>NET SYSTEM P&L     :</b> {pnl_sign} <b>₹{summary['total_net_pnl']:+,.0f}</b>",
        f"👤 <b>AAPKA PERSONAL P&L :</b> {user_pnl_sign} <b>₹{summary['user_net_pnl']:+,.0f}</b> ({summary['user_taken']} Trades Entered)",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "📥 <i>Aapka detailed Excel/CSV audit file niche attach kiya gaya hai. Is par click karke aap Excel/Google Sheets me pura breakdown dekh sakte hain!</i>"
    ]
    return "\n".join(card)


# Initialize DB on import
init_db()
