import os
import sys
import time
import json
import html
import requests
import warnings
import http.server
import socketserver
import threading
from datetime import datetime, timezone, timedelta
import urllib3.util.connection as urllib3_cn
from dotenv import load_dotenv

# Fix Indian ISP IPv6 routing issues with Telegram API
urllib3_cn.HAS_IPV6 = False

warnings.filterwarnings('ignore')

from silver_advisor import analyze_silver_trade
from cruid_advisor import analyze_crude_trade
from gold_advisor import analyze_gold_trade
from natgas_advisor import analyze_natgas_trade
from aluminium_advisor import analyze_aluminium_trade
from zinc_advisor import analyze_zinc_trade
from nickel_advisor import analyze_nickel_trade
from electricity_advisor import analyze_electricity_trade
from mcx_feed import get_live_mcx_price, get_live_dollar_price, get_commodity_expiry, calculate_expected_time
from swing_advisor import analyze_btst_swing
from macro_analyzer import analyze_macro_sentiment, analyze_commodity_news
from smc_engine import analyze_smc_setup
from database import (
    save_trade_db, db_load_daily_audit, db_record_user_taken,
    db_get_user_taken_map, db_load_subscribers, db_save_subscriber,
    get_db_config, set_db_config, generate_excel_csv_report, format_report_summary_card,
    get_active_watchlist, set_active_watchlist, toggle_commodity_watchlist, ALL_SUPPORTED_COMMODITIES,
    evaluate_and_sync_active_trades
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SUBSCRIBERS_FILE = os.path.join(BASE_DIR, "subscribers.json")
CONFIG_FILE = os.path.join(BASE_DIR, "scanner_config.json")
AUDIT_FILE = os.path.join(BASE_DIR, "daily_audit.json")
USER_TRADES_FILE = os.path.join(BASE_DIR, "user_active_trades.json")

# Scanner Memory & State
active_subscribers = set()
active_tracked_trades = {}
alert_cooldowns = {}
eod_report_sent_date = None


# 1. Cloud Health Server (Render.com 24/7 compatibility)
def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    class HealthHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            status_data = {
                "status": "healthy",
                "service": "MCX Futures Trading & Auto-Scanner Engine",
                "uptime_check": "OK",
                "mcx_open": is_mcx_open(),
                "time": time.time()
            }
            self.wfile.write(json.dumps(status_data).encode("utf-8"))

        def log_message(self, format, *args):
            pass

    try:
        with socketserver.TCPServer(("", port), HealthHandler) as httpd:
            print(f"[+] Cloud Health Server active on port {port} (Render & UptimeRobot ready)")
            httpd.serve_forever()
    except Exception:
        pass

threading.Thread(target=start_health_server, daemon=True).start()


# 2. Persistence Helpers
def load_token():
    env_file = os.path.join(BASE_DIR, ".env")
    load_dotenv(dotenv_path=env_file, override=True)
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if token and token.strip() and token != "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        return token.strip()
    return None


def load_subscribers():
    global active_subscribers
    try:
        active_subscribers = db_load_subscribers()
    except Exception:
        pass
    if not active_subscribers and os.path.exists(SUBSCRIBERS_FILE):
        try:
            with open(SUBSCRIBERS_FILE, "r") as f:
                active_subscribers = set(json.load(f))
        except Exception:
            pass


def save_subscriber(chat_id):
    global active_subscribers
    active_subscribers.add(chat_id)
    try:
        db_save_subscriber(chat_id)
    except Exception:
        pass
    try:
        with open(SUBSCRIBERS_FILE, "w") as f:
            json.dump(list(active_subscribers), f)
    except Exception:
        pass


def get_min_profit():
    try:
        return float(get_db_config("min_profit", "1500.0"))
    except Exception:
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    cfg = json.load(f)
                    return float(cfg.get("min_profit", 1500.0))
            except Exception:
                pass
        return 1500.0


def set_min_profit(val):
    try:
        set_db_config("min_profit", str(val))
    except Exception:
        pass
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump({"min_profit": float(val)}, f)
        return True
    except Exception:
        return False


def load_daily_audit():
    try:
        return db_load_daily_audit()
    except Exception:
        if os.path.exists(AUDIT_FILE):
            try:
                with open(AUDIT_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return []


def save_audit_trade(trade_dict):
    try:
        save_trade_db(trade_dict)
    except Exception:
        pass
    # JSON file sync backup
    try:
        audit = []
        if os.path.exists(AUDIT_FILE):
            with open(AUDIT_FILE, "r") as f:
                audit = json.load(f)
        tid = trade_dict.get("trade_id")
        found = False
        for idx, t in enumerate(audit):
            if t.get("trade_id") == tid:
                audit[idx] = trade_dict
                found = True
                break
        if not found:
            audit.append(trade_dict)
        with open(AUDIT_FILE, "w") as f:
            json.dump(audit, f, indent=2)
    except Exception:
        pass


def load_user_taken_trades():
    try:
        return db_get_user_taken_map()
    except Exception:
        if os.path.exists(USER_TRADES_FILE):
            try:
                with open(USER_TRADES_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}


def record_user_trade_taken(chat_id, trade_id):
    try:
        db_record_user_taken(chat_id, trade_id)
    except Exception:
        pass
    try:
        trades = load_user_taken_trades()
        if trade_id not in trades:
            trades[trade_id] = []
        if chat_id not in trades[trade_id]:
            trades[trade_id].append(chat_id)
        with open(USER_TRADES_FILE, "w") as f:
            json.dump(trades, f, indent=2)
    except Exception:
        pass


# 3. MCX Market Hours Check (IST: UTC + 5:30)
def is_mcx_open():
    ist = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist)
    if now.weekday() >= 5:  # Saturday or Sunday
        return False
    hour, minute = now.hour, now.minute
    if 9 <= hour < 23:
        return True
    if hour == 23 and minute <= 35:
        return True
    return False


# 4. Telegram API Messaging & Callback Helpers
def send_telegram_message(bot_token, chat_id, text, parse_mode="HTML", reply_markup=None):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    if len(text) <= 3900:
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            resp = requests.post(url, json=payload, timeout=12)
            res_json = resp.json()
            if not res_json.get("ok") and parse_mode:
                payload.pop("parse_mode", None)
                fb = requests.post(url, json=payload, timeout=12)
                return fb.json()
            return res_json
        except Exception as e:
            print(f"[-] Telegram message error: {e}")
            return None

    # Chunk long messages
    chunks = []
    blocks = text.split("\n\n")
    cur = ""
    for b in blocks:
        if len(cur) + len(b) + 2 < 3800:
            cur += (b + "\n\n")
        else:
            if cur.strip():
                chunks.append(cur.strip())
            cur = b + "\n\n"
    if cur.strip():
        chunks.append(cur.strip())

    last_resp = None
    for idx, c in enumerate(chunks):
        markup = reply_markup if idx == len(chunks) - 1 else None
        last_resp = send_telegram_message(bot_token, chat_id, c, parse_mode=parse_mode, reply_markup=markup)
        time.sleep(0.35)
    return last_resp


def send_telegram_document(bot_token, chat_id, file_path, caption=None):
    """Sends a local file (.csv, .xlsx, etc.) directly to Telegram as a downloadable attachment"""
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
        data["parse_mode"] = "HTML"
    try:
        with open(file_path, "rb") as f:
            resp = requests.post(url, data=data, files={"document": f}, timeout=25)
            return resp.json()
    except Exception as e:
        print(f"[-] Telegram sendDocument error: {e}")
        return None


def answer_callback_query(bot_token, callback_query_id, text=None, show_alert=False):
    url = f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    if show_alert:
        payload["show_alert"] = show_alert
    try:
        requests.post(url, json=payload, timeout=6)
    except Exception:
        pass


def edit_telegram_message(bot_token, chat_id, message_id, text, reply_markup=None, parse_mode="HTML"):
    """Edits an existing telegram message and its inline keyboard in place"""
    url = f"https://api.telegram.org/bot{bot_token}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        resp = requests.post(url, json=payload, timeout=8)
        return resp.json()
    except Exception as e:
        print(f"[-] edit_telegram_message error: {e}")
        return None


def broadcast_alert(bot_token, text, reply_markup=None):
    """Sends high priority alert to all active traders"""
    load_subscribers()
    for cid in active_subscribers:
        send_telegram_message(bot_token, cid, text, parse_mode="HTML", reply_markup=reply_markup)


COMMODITY_DISPLAY = [
    ("silver", "🪙 Silver Micro"),
    ("crude", "🛢️ Crude Oil"),
    ("gold", "🥇 Gold Ten"),
    ("natgas", "🔥 Nat Gas"),
    ("aluminium", "⚙️ Aluminium"),
    ("zinc", "🔩 Zinc"),
    ("nickel", "🪙 Nickel"),
    ("electricity", "⚡ Electricity")
]


def build_watchlist_keyboard(enabled_list=None):
    if enabled_list is None:
        enabled_list = get_active_watchlist()

    rows = []
    row = []
    for slug, label in COMMODITY_DISPLAY:
        is_active = slug in enabled_list
        icon = "✅" if is_active else "❌"
        row.append({
            "text": f"{icon} {label}",
            "callback_data": f"wl_toggle:{slug}"
        })
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    # Quick Action Rows
    rows.append([
        {"text": "🌟 Only Silver & Crude", "callback_data": "wl_set:priority"},
        {"text": "✅ Select All (All 8)", "callback_data": "wl_set:all"}
    ])
    rows.append([
        {"text": "💾 Confirm & Save Watchlist", "callback_data": "wl_done"}
    ])
    return {"inline_keyboard": rows}


def format_watchlist_card(enabled_list=None):
    if enabled_list is None:
        enabled_list = get_active_watchlist()

    active_labels = []
    for slug, label in COMMODITY_DISPLAY:
        if slug in enabled_list:
            active_labels.append(f"• ✅ <b>{label}</b> (Auto-Scan & SMC Active)")
        else:
            active_labels.append(f"• ❌ <s>{label}</s> (Alerts OFF)")

    active_str = "\n".join(active_labels)
    total_active = len(enabled_list)

    card = (
        "🎯 <b>AUTO-SCAN & SMC COMMODITY WATCHLIST</b>\n\n"
        "<i>Auto-Scanner aur SMC Institutional Alerts ab sirf aapki chuni gayi commodities ke aayenge. Baki commodities skip rahengi!</i>\n\n"
        f"📊 <b>Current Watchlist Status ({total_active}/8 Enabled):</b>\n"
        f"{active_str}\n\n"
        "👉 <i>Niche buttons par tap karke instant <b>ON (✅)</b> ya <b>OFF (❌)</b> karein:</i>"
    )
    return card


def get_keyboard():
    return {
        "keyboard": [
            [{"text": "🪙 Silver Micro (1 Kg)"}, {"text": "🛢️ Crude Oil Mini (10 BBL)"}],
            [{"text": "🥇 Gold Ten (10g)"}, {"text": "🔥 Nat Gas Mini (250)"}],
            [{"text": "⚙️ Aluminium Mini (1 MT)"}, {"text": "🔩 Zinc Mini (1 MT)"}],
            [{"text": "🪙 Nickel (250 Kg)"}, {"text": "⚡ Electricity (50 MWh)"}],
            [{"text": "🏛️ SMC Institutional Setups"}, {"text": "🌙 BTST / Swing Advisory"}],
            [{"text": "🎯 Alert Watchlist (Filter)"}, {"text": "🔍 Run Live Scan Now"}],
            [{"text": "📋 Daily Audit & P&L"}, {"text": "📥 Export Excel Report"}],
            [{"text": "📊 Bot & Alert Status"}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False
    }


def extract_custom_cmp(text):
    parts = text.split()
    for p in parts[1:]:
        p_clean = p.replace("₹", "").replace(",", "").strip()
        try:
            val = float(p_clean)
            if val > 0:
                return val
        except ValueError:
            continue
    return None


# 5. Mobile-First Trading Tip Card Generator & Dispatcher
COMMODITY_ADVISORS = {
    'silver': ('Silver Micro (1 Kg)', analyze_silver_trade),
    'crude': ('Crude Oil Mini (10 BBL)', analyze_crude_trade),
    'gold': ('Gold Ten (10g)', analyze_gold_trade),
    'natgas': ('Natural Gas Mini (250 mmBtu)', analyze_natgas_trade),
    'aluminium': ('Aluminium Mini (1 MT)', analyze_aluminium_trade),
    'zinc': ('Zinc Mini (1 MT)', analyze_zinc_trade),
    'nickel': ('Nickel (250 Kg)', analyze_nickel_trade),
    'electricity': ('Electricity Base Load (50 MWh)', analyze_electricity_trade)
}


def format_mobile_trading_card(res, usd_ref=None, is_alert=False, trade_id=None, expiry=None):
    """
    Mobile-First Ultra-Clean Trading Tip Card.
    - 2-second decision card: Action, Entry, T1, T2, SL, Expiry
    - Big bold numbers, high contrast emojis
    - No monospace horizontal scrolling
    """
    comm_name = res.get('commodity', 'MCX Commodity').upper()
    cmp_val = res.get('cmp', 0.0)
    direction = res.get('direction', 'BUY').upper()
    is_buy = "BUY" in direction

    dir_badge = "🟢 <b>BUY / LONG</b>" if is_buy else "🔴 <b>SELL / SHORT</b>"

    # Expiry date determination
    expiry_str = expiry or res.get('expiry')
    if not expiry_str or expiry_str == "Active Contract":
        expiry_str = get_commodity_expiry(comm_name)

    # Smart number formatting
    if cmp_val >= 1000:
        fmt_cmp = f"₹{cmp_val:,.0f}"
        fmt_sl = f"₹{res.get('sl_price', 0):,.0f}"
        fmt_t1 = f"₹{res.get('t1_price', 0):,.0f}"
        fmt_t2 = f"₹{res.get('t2_price', 0):,.0f}"
    elif cmp_val >= 100:
        fmt_cmp = f"₹{cmp_val:,.1f}"
        fmt_sl = f"₹{res.get('sl_price', 0):,.1f}"
        fmt_t1 = f"₹{res.get('t1_price', 0):,.1f}"
        fmt_t2 = f"₹{res.get('t2_price', 0):,.1f}"
    else:
        fmt_cmp = f"₹{cmp_val:,.2f}"
        fmt_sl = f"₹{res.get('sl_price', 0):,.2f}"
        fmt_t1 = f"₹{res.get('t1_price', 0):,.2f}"
        fmt_t2 = f"₹{res.get('t2_price', 0):,.2f}"

    if res.get('entry_range'):
        entry_band = res.get('entry_range')
    else:
        if "ELECTRICITY" in comm_name:
            band = 6.0
        elif "CRUDE" in comm_name:
            band = 25.0
        elif "SILVER" in comm_name:
            band = 150.0
        elif "GOLD" in comm_name:
            band = 100.0
        elif "NATGAS" in comm_name or "NAT GAS" in comm_name:
            band = 1.0
        elif "ALUMINIUM" in comm_name or "ZINC" in comm_name:
            band = 1.5
        elif "NICKEL" in comm_name:
            band = 8.0
        else:
            band = max(round(cmp_val * 0.002, 1), 0.5)

        if cmp_val >= 1000:
            entry_band = f"₹{cmp_val - band:,.0f} - ₹{cmp_val:,.0f}" if is_buy else f"₹{cmp_val:,.0f} - ₹{cmp_val + band:,.0f}"
        elif cmp_val >= 100:
            entry_band = f"₹{cmp_val - band:,.1f} - ₹{cmp_val:,.1f}" if is_buy else f"₹{cmp_val:,.1f} - ₹{cmp_val + band:,.1f}"
        else:
            entry_band = f"₹{cmp_val - band:,.2f} - ₹{cmp_val:,.2f}" if is_buy else f"₹{cmp_val:,.2f} - ₹{cmp_val + band:,.2f}"

    t1_profit = res.get('t1_profit_inr', 0.0)
    t2_profit = t1_profit * 2.0
    sl_risk = res.get('sl_risk_inr', 0.0)
    prob_pct = res.get('prob_pct', 75)

    reasons = res.get('reasons', ['Technical Momentum Alignment'])
    clean_reason = reasons[0] if reasons else "Bullish Technical Alignment"
    clean_reason = clean_reason.replace("Confluence Analysis:", "").strip()

    title_badge = "🚨 <b>A+ AUTO-SCANNER ALERT</b>" if is_alert else "🎯 <b>MCX INTRADAY TRADE TIP</b>"
    ist = timezone(timedelta(hours=5, minutes=30))
    time_ist_str = datetime.now(ist).strftime("%I:%M:%S %p")

    card = [
        f"{title_badge}",
        f"<b>{comm_name}</b>",
        f"⏰ <b>Signal Time    :</b> <code>{time_ist_str} IST</code>",
        f"📅 <b>Contract Expiry:</b> <code>{expiry_str}</code>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"⚡ <b>ACTION :</b> {dir_badge}",
        f"📍 <b>ENTRY  :</b> <b>{entry_band}</b>",
        f"   <i>(Current CMP: {fmt_cmp})</i>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🎯 <b>TARGET 1 :</b> <b>{fmt_t1}</b>  (<b>+₹{t1_profit:,.0f} Profit / Lot</b>) 🔥",
        f"🎯 <b>TARGET 2 :</b> <b>{fmt_t2}</b>  (+₹{t2_profit:,.0f} Profit / Lot)",
        f"🛑 <b>STOP-LOSS:</b> <b>{fmt_sl}</b>  (Risk: -₹{sl_risk:,.0f} / Lot)",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"⏱️ <b>EXPECTED TARGET TIME:</b>",
        f"• 🎯 <b>Target 1 :</b> <b>{calculate_expected_time(comm_name, is_smc=False)[0]}</b>",
        f"• 🎯 <b>Target 2 :</b> <b>{calculate_expected_time(comm_name, is_smc=False)[1]}</b>",
        f"⏳ <b>Max Hold  :</b> <i>{calculate_expected_time(comm_name, is_smc=False)[2]}</i>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🎲 <b>Win Chance:</b> <b>{prob_pct}%</b> [High Probability]",
        f"💡 <b>Setup     :</b> {clean_reason}",
        f"📋 <b>Strategy  :</b> T1 aate hi 50% book karein, SL Cost par trail karein."
    ]

    if usd_ref:
        card.append(f"🌐 <b>Global Ref:</b> <code>{usd_ref}</code>")

    card.append("━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(card)


def send_commodity_tip(bot_token, chat_id, comm_key, custom_cmp=None):
    if comm_key not in COMMODITY_ADVISORS:
        return

    name_label, advisor_fn = COMMODITY_ADVISORS[comm_key]
    send_telegram_message(bot_token, chat_id, f"⏳ <i>{name_label} analyze ho raha hai...</i>", parse_mode="HTML")
    res = advisor_fn(custom_cmp=custom_cmp, return_data=True)
    if not isinstance(res, dict):
        send_telegram_message(bot_token, chat_id, f"⚠️ Error fetching data for {comm_key}. Kripya thodi der baad try karein.", parse_mode="HTML")
        return

    now_ts = time.time()
    trade_id = f"{comm_key[:3].upper()}_{int(now_ts)}"
    usd_ref = get_live_dollar_price(comm_key)
    expiry_val = res.get('expiry') or get_commodity_expiry(comm_key)

    card_text = format_mobile_trading_card(res, usd_ref=usd_ref, is_alert=False, trade_id=trade_id, expiry=expiry_val)

    today_str = datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d")
    trade_data = {
        'trade_id': trade_id,
        'name': res.get('commodity', name_label),
        'direction': res.get('direction', 'BUY'),
        'entry': res.get('cmp', 0.0),
        't1': res.get('t1_price', 0.0),
        'sl': res.get('sl_price', 0.0),
        't1_profit': res.get('t1_profit_inr', 0.0),
        'sl_risk': res.get('sl_risk_inr', 0.0),
        'time': now_ts,
        'date': today_str,
        't1_hit': False,
        'sl_hit': False,
        'prob_pct': res.get('prob_pct', 75),
        'reasons_str': res.get('reasons', ['Technical Momentum Alignment'])[0],
        'status': 'ACTIVE',
        'user_taken': False
    }
    active_tracked_trades[comm_key] = trade_data
    save_audit_trade(trade_data)

    markup = {
        "inline_keyboard": [
            [
                {"text": "🚀 Entry Taken (Track Live)", "callback_data": f"take:{trade_id}"},
                {"text": "⏭️ Skip Setup", "callback_data": f"skip:{trade_id}"}
            ],
            [
                {"text": "🔄 Refresh CMP", "callback_data": f"refresh:{comm_key}"}
            ]
        ]
    }

    send_telegram_message(bot_token, chat_id, card_text, parse_mode="HTML", reply_markup=markup)


# 6. End-of-Day (EOD) Comprehensive Post-Market Audit & P&L Report
def generate_eod_report(today_str=None):
    try:
        evaluate_and_sync_active_trades()
    except Exception as e:
        print(f"[-] EOD evaluate_and_sync_active_trades error: {e}")

    if not today_str:
        ist = timezone(timedelta(hours=5, minutes=30))
        today_str = datetime.now(ist).strftime("%Y-%m-%d")

    audit = load_daily_audit()
    today_trades = [t for t in audit if t.get("date") == today_str]

    if not today_trades:
        return (
            f"📊 <b>MCX POST-MARKET DAILY AUDIT & P&L REPORT</b>\n"
            f"📅 <b>Date:</b> {today_str}\n\n"
            f"ℹ️ <i>Aaj ke session me koi qualifying A+ signal trigger nahi hua ya market closed tha.</i>"
        )

    total_signals = len(today_trades)
    t1_hits = sum(1 for t in today_trades if t.get("status") == "T1_HIT")
    sl_hits = sum(1 for t in today_trades if t.get("status") == "SL_HIT")
    active = sum(1 for t in today_trades if t.get("status") == "ACTIVE")

    theo_profit = sum(t.get("t1_profit", 0) for t in today_trades if t.get("status") == "T1_HIT")
    theo_loss = sum(t.get("sl_risk", 0) for t in today_trades if t.get("status") == "SL_HIT")
    net_theo_pnl = theo_profit - theo_loss

    # User taken trades
    user_taken = [t for t in today_trades if t.get("user_taken")]
    user_total = len(user_taken)
    user_t1 = sum(1 for t in user_taken if t.get("status") == "T1_HIT")
    user_sl = sum(1 for t in user_taken if t.get("status") == "SL_HIT")
    user_profit = sum(t.get("t1_profit", 0) for t in user_taken if t.get("status") == "T1_HIT")
    user_loss = sum(t.get("sl_risk", 0) for t in user_taken if t.get("status") == "SL_HIT")
    net_user_pnl = user_profit - user_loss

    closed_trades = t1_hits + sl_hits
    win_rate = (t1_hits / closed_trades * 100) if closed_trades > 0 else 0.0
    loss_rate = (sl_hits / closed_trades * 100) if closed_trades > 0 else 0.0

    user_closed = user_t1 + user_sl
    user_win_rate = (user_t1 / user_closed * 100) if user_closed > 0 else 0.0
    user_loss_rate = (user_sl / user_closed * 100) if user_closed > 0 else 0.0

    theo_str = f"+₹{net_theo_pnl:,.0f}" if net_theo_pnl >= 0 else f"-₹{abs(net_theo_pnl):,.0f}"
    user_str = f"+₹{net_user_pnl:,.0f}" if net_user_pnl >= 0 else f"-₹{abs(net_user_pnl):,.0f}"

    breakdown_lines = []
    for i, t in enumerate(today_trades, 1):
        st = t.get("status", "ACTIVE")
        c_name = t.get("name", "Commodity")
        dirn = t.get("direction", "BUY")
        t_prof = t.get("t1_profit", 0)
        s_risk = t.get("sl_risk", 0)
        taken_tag = " [✅ <i>Aapne Liya</i>]" if t.get("user_taken") else ""

        if st == "T1_HIT":
            status_badge = f"✅ <b>TARGET 1 HIT (+₹{t_prof:,.0f})</b>"
            reason = t.get("reasons_str", "Super Trend Momentum Alignment")
        elif st == "SL_HIT":
            status_badge = f"🛑 <b>STOP-LOSS (-₹{s_risk:,.0f})</b>"
            reason = t.get("fail_reason", "Sharp Volatility & Reversal Breakdown")
        else:
            status_badge = f"⏳ <b>ACTIVE / IN PROGRESS</b>"
            reason = t.get("reasons_str", "Trend Setup Active")

        breakdown_lines.append(
            f"{i}. <b>{c_name}</b> ({dirn}){taken_tag}\n"
            f"   • Status : {status_badge}\n"
            f"   • Reason : {reason}"
        )

    audit_text = "\n\n".join(breakdown_lines)

    report = (
        f"📊 <b>MCX POST-MARKET DAILY AUDIT & P&L REPORT</b>\n"
        f"📅 <b>Date:</b> {today_str} | <b>Session:</b> CLOSED\n\n"
        f"🎯 <b>1. OVERALL SYSTEM SCORECARD (All Signals):</b>\n"
        f"• Total Signals Generated : <b>{total_signals}</b>\n"
        f"• Target 1 Hit (Wins)     : <b>{t1_hits} 🟢</b>\n"
        f"• Stop-Loss Hit (Losses)   : <b>{sl_hits} 🔴</b>\n"
        f"• Active / In Progress    : <b>{active} ⏳</b>\n"
        f"• 📈 <b>Overall Win Rate  :</b> <b>{win_rate:.1f}% 🟢</b>\n"
        f"• 📉 <b>Overall Loss Rate :</b> <b>{loss_rate:.1f}% 🔴</b>\n"
        f"• Theoretical System P&L  : <b>{theo_str}</b> (per 1 Lot)\n\n"
        f"💼 <b>2. AAPKA PERSONAL P&L (Trades Taken):</b>\n"
        f"• Total Trades Taken      : <b>{user_total}</b>\n"
        f"• Trades Won (Target 1)   : <b>{user_t1} 🟢</b>\n"
        f"• Trades Lost (SL Hit)    : <b>{user_sl} 🔴</b>\n"
        f"• Personal Win Rate       : <b>{user_win_rate:.1f}% 🟢</b>\n"
        f"• Personal Loss Rate      : <b>{user_loss_rate:.1f}% 🔴</b>\n"
        f"• <b>Your Realized P&L    : {user_str} 🔥</b>\n\n"
        f"🔍 <b>3. TRADE-BY-TRADE AUDIT & REASON ANALYSIS:</b>\n"
        f"{audit_text}\n\n"
        f"📌 <i>Subah 9:00 AM MCX open hote hi scanner dubara live active ho jayega!</i>"
    )
    return report


# 6. Background Auto-Scanner Engine (Priority: Silver -> Crude -> Others)
def run_scanner_cycle(bot_token, force=False):
    global active_tracked_trades, alert_cooldowns
    min_profit = get_min_profit()
    now_ts = time.time()
    ist = timezone(timedelta(hours=5, minutes=30))
    today_str = datetime.now(ist).strftime("%Y-%m-%d")

    if not force and not is_mcx_open():
        return

    active_watchlist = get_active_watchlist()

    # User Specified Priority: 1st Silver, 2nd Crude, then remaining commodities
    advisors = [
        ("Silver Micro (1 Kg)", analyze_silver_trade, "silver"),
        ("Crude Oil Mini (10 BBL)", analyze_crude_trade, "crude"),
        ("Gold Ten (10 Grams)", analyze_gold_trade, "gold"),
        ("Natural Gas Mini (250 mmBtu)", analyze_natgas_trade, "natgas"),
        ("Aluminium Mini (1 MT)", analyze_aluminium_trade, "aluminium"),
        ("Zinc Mini (1 MT)", analyze_zinc_trade, "zinc"),
        ("Nickel (250 Kg)", analyze_nickel_trade, "nickel"),
        ("Electricity (50 MWh)", analyze_electricity_trade, "electricity")
    ]

    for name, advisor_fn, slug in advisors:
        is_enabled = slug in active_watchlist
        is_tracked = (name in active_tracked_trades) or (f"{name} [SMC]" in active_tracked_trades)

        # Skip commodity if disabled in user watchlist and no open trade is currently being monitored
        if not is_enabled and not is_tracked:
            continue

        try:
            res = advisor_fn(return_data=True)
            if not isinstance(res, dict):
                continue

            cmp = res['cmp']
            direction = res['direction']
            t1 = res['t1_price']
            sl = res['sl_price']
            prob_pct = res['prob_pct']
            t1_profit = res['t1_profit_inr']

            # 1. Check Active Follow-up Tracking (Target 1 & SL Hit)
            if name in active_tracked_trades:
                trade = active_tracked_trades[name]
                trade_id = trade.get('trade_id', '')
                user_map = load_user_taken_trades()
                users_who_took = user_map.get(trade_id, [])

                # Target 1 Check
                if not trade.get('t1_hit'):
                    if (trade['direction'] == "BUY" and cmp >= trade['t1']) or (trade['direction'] == "SELL" and cmp <= trade['t1']):
                        trade['t1_hit'] = True
                        trade['status'] = 'T1_HIT'
                        save_audit_trade(trade)

                        # ONLY alert if the user explicitly clicked 'Maine Trade Le Liya'
                        if users_who_took:
                            msg = (
                                f"🎉 <b>BINGO! TARGET 1 HIT!</b> 🎉\n\n"
                                f"• <b>Commodity :</b> {name}\n"
                                f"• <b>Direction :</b> <b>{trade['direction']}</b>\n"
                                f"• <b>Target 1  :</b> <b>₹{trade['t1']:,.1f} REACHED!</b>\n"
                                f"• <b>Profit    :</b> <b>+₹{trade['t1_profit']:,.0f} Booked per 1 Lot!</b> 🔥\n\n"
                                f"📋 <b>Next Action:</b> 50% quantity book karein aur Stop-Loss ko turant "
                                f"Entry Cost (₹{trade['entry']:,.1f}) par shift kar dein (Zero-Risk Trade)!"
                            )
                            for uid in users_who_took:
                                send_telegram_message(bot_token, uid, msg, parse_mode="HTML")

                # Stop-Loss Check
                if not trade.get('sl_hit'):
                    if (trade['direction'] == "BUY" and cmp <= trade['sl']) or (trade['direction'] == "SELL" and cmp >= trade['sl']):
                        trade['sl_hit'] = True
                        trade['status'] = 'SL_HIT'
                        trade['fail_reason'] = "Momentum breakdown below key EMA support on high volume"
                        save_audit_trade(trade)

                        # ONLY alert if the user explicitly clicked 'Maine Trade Le Liya'
                        if users_who_took:
                            msg = (
                                f"🛑 <b>STOP-LOSS TRIGGERED!</b>\n\n"
                                f"• <b>Commodity :</b> {name}\n"
                                f"• <b>Direction :</b> <b>{trade['direction']}</b>\n"
                                f"• <b>SL Level  :</b> ₹{trade['sl']:,.1f} touched.\n"
                                f"• <b>Risk/Loss :</b> -₹{trade.get('sl_risk', 0):,.0f} per Lot.\n\n"
                                f"Capital protect karne ke liye position exit karein."
                            )
                            for uid in users_who_took:
                                send_telegram_message(bot_token, uid, msg, parse_mode="HTML")
                        del active_tracked_trades[name]
                        continue

            # 2. Check Cooldown (45 mins per commodity)
            last_alert_time = alert_cooldowns.get(name, 0)
            if (now_ts - last_alert_time) < (45 * 60):
                continue

            # 3. High Profit & High Probability Filter (Only if commodity enabled in watchlist)
            if is_enabled and prob_pct >= 75 and t1_profit >= min_profit:
                alert_cooldowns[name] = now_ts
                trade_id = f"{name[:3].upper()}_{int(now_ts)}"

                # Fetch International Dollar ($) Benchmark
                usd_ref = get_live_dollar_price(name)
                expiry_val = res.get('expiry') or get_commodity_expiry(name)
                alert_msg = format_mobile_trading_card(res, usd_ref=usd_ref, is_alert=True, trade_id=trade_id, expiry=expiry_val)

                inline_markup = {
                    "inline_keyboard": [
                        [
                            {"text": "🚀 Entry Taken (Track Live)", "callback_data": f"take:{trade_id}"},
                            {"text": "⏭️ Skip Setup", "callback_data": f"skip:{trade_id}"}
                        ]
                    ]
                }

                reasons_list = res.get('reasons', ['Technical Momentum Alignment'])
                reasons_str = reasons_list[0] if reasons_list else 'Technical Momentum Alignment'

                trade_data = {
                    'trade_id': trade_id,
                    'name': name,
                    'direction': direction,
                    'entry': cmp,
                    't1': t1,
                    'sl': sl,
                    't1_profit': t1_profit,
                    'sl_risk': res['sl_risk_inr'],
                    'time': now_ts,
                    'date': today_str,
                    't1_hit': False,
                    'sl_hit': False,
                    'prob_pct': prob_pct,
                    'reasons_str': reasons_str,
                    'status': 'ACTIVE',
                    'user_taken': False
                }
                active_tracked_trades[name] = trade_data
                save_audit_trade(trade_data)

                print(f"[!] Auto-Scanner Triggered Alert for {name}: +₹{t1_profit} Target 1 Profit")
                broadcast_alert(bot_token, alert_msg, reply_markup=inline_markup)

        except Exception as e:
            print(f"[-] Scanner error on {name}: {e}")

        # 4. SMART MONEY CONCEPTS (SMC) INSTITUTIONAL SCANNER
        # Bypasses the 3 regular indicator/profit rules: If an SMC setup is detected, alert immediately!
        # Only runs if commodity is enabled in user watchlist
        if is_enabled:
            try:
                comm_slug = slug
                smc_res = analyze_smc_setup(comm_slug)
                if smc_res and smc_res.get('has_setup') and 'LIQUIDITY SWEEP' in smc_res.get('setup_type', ''):
                    smc_name = f"{name} [SMC]"
                    last_smc_time = alert_cooldowns.get(smc_name, 0)
                    if force or (now_ts - last_smc_time) >= (45 * 60):
                        alert_cooldowns[smc_name] = now_ts
                        smc_trade_id = f"SMC_{comm_slug[:3].upper()}_{int(now_ts)}"

                        smc_markup = {
                            "inline_keyboard": [
                                [
                                    {"text": "🏛️ Entry Taken (Track Live)", "callback_data": f"take:{smc_trade_id}"},
                                    {"text": "⏭️ Skip Setup", "callback_data": f"skip:{smc_trade_id}"}
                                ]
                            ]
                        }

                        smc_trade_data = {
                            'trade_id': smc_trade_id,
                            'name': smc_name,
                            'direction': smc_res['direction'],
                            'entry': smc_res['cmp'],
                            't1': smc_res['t1_price'],
                            'sl': smc_res['sl_price'],
                            't1_profit': smc_res['t1_profit_inr'],
                            'sl_risk': smc_res['sl_risk_inr'],
                            'time': now_ts,
                            'date': today_str,
                            't1_hit': False,
                            'sl_hit': False,
                            'prob_pct': smc_res.get('prob_pct', 82),
                            'reasons_str': smc_res['reasons'][0] if smc_res.get('reasons') else smc_res['setup_type'],
                            'status': 'ACTIVE',
                            'user_taken': False
                        }
                        active_tracked_trades[smc_name] = smc_trade_data
                        save_audit_trade(smc_trade_data)

                        print(f"[!] 🏛️ SMC Alert Triggered for {name}: {smc_res['setup_type']}")
                        broadcast_alert(bot_token, smc_res['card_text'], reply_markup=smc_markup)
            except Exception as e:
                print(f"[-] SMC scan error on {name}: {e}")


def background_scanner_loop(bot_token):
    global eod_report_sent_date
    time.sleep(10)  # Initial warm up
    try:
        init_interval = max(20, int(get_db_config("scan_interval", "45")))
    except Exception:
        init_interval = 45
    print(f"🚀 Background Auto-Scanner Engine Started (Scanning every {init_interval}s for ₹1,500+ A+ Setups)...")
    while True:
        try:
            # 1. Sync & evaluate all ongoing active trades against live prices
            try:
                evaluate_and_sync_active_trades()
            except Exception as e:
                print(f"[-] evaluate_and_sync_active_trades error: {e}")

            # 2. Run market scanner cycle
            run_scanner_cycle(bot_token)

            # Auto EOD Broadcast at 23:35 IST (MCX Market Close)
            ist = timezone(timedelta(hours=5, minutes=30))
            now_ist = datetime.now(ist)
            today_str = now_ist.strftime("%Y-%m-%d")
            if now_ist.hour == 23 and now_ist.minute >= 35 and eod_report_sent_date != today_str:
                eod_report_sent_date = today_str
                report = generate_eod_report(today_str)
                print(f"[+] Broadcasting End-of-Day Audit Report for {today_str}...")
                broadcast_alert(bot_token, report)

        except Exception as e:
            print(f"[-] Scanner loop error: {e}")

        # Dynamic scan speed from DB (default 45s, min 20s to prevent rate limits)
        try:
            interval_sec = max(20, int(get_db_config("scan_interval", "45")))
        except Exception:
            interval_sec = 45
        time.sleep(interval_sec)


# 7. Inline Button Callback Query Handler
def handle_callback(bot_token, cq):
    cq_id = cq["id"]
    from_user = cq.get("from", {})
    msg_obj = cq.get("message", {})
    message_id = msg_obj.get("message_id") if "message" in cq else None
    chat_id = msg_obj["chat"]["id"] if "message" in cq and "chat" in msg_obj else from_user.get("id")
    data = cq.get("data", "")

    if data.startswith("take:"):
        trade_id = data.split(":", 1)[1]
        record_user_trade_taken(chat_id, trade_id)

        # Update user_taken flag in daily audit
        audit = load_daily_audit()
        found = None
        for t in audit:
            if t.get("trade_id") == trade_id:
                t["user_taken"] = True
                found = t
                break
        if found:
            save_audit_trade(found)

        answer_callback_query(bot_token, cq_id, text="🚀 Position Entered! Live trailing active.", show_alert=True)

        comm_name = found.get("name", "Commodity") if found else "Selected Setup"
        entry_p = found.get("entry", 0.0) if found else 0.0
        t1_p = found.get("t1", 0.0) if found else 0.0
        profit = found.get("t1_profit", 1500.0) if found else 1500.0
        sl_p = found.get("sl", 0.0) if found else 0.0

        conf_msg = (
            f"🚀 <b>POSITION ENTERED & LIVE TRACKING ACTIVE!</b>\n\n"
            f"• <b>Commodity :</b> {comm_name}\n"
            f"• <b>Entry     :</b> ₹{entry_p:,.1f}\n"
            f"• <b>Target 1  :</b> ₹{t1_p:,.1f} (<b>+₹{profit:,.0f} Profit / Lot</b>)\n"
            f"• <b>Stop-Loss :</b> ₹{sl_p:,.1f}\n\n"
            f"🎯 <i>Aapka trade VIP trailing radar me add ho chuka hai. Target 1 aate hi 50% book karne aur SL cost par trail karne ka instant alert aayega!</i>"
        )
        send_telegram_message(bot_token, chat_id, conf_msg, parse_mode="HTML")

    elif data.startswith("skip:"):
        answer_callback_query(bot_token, cq_id, text="Trade skipped. Koi live update nahi aayega.", show_alert=False)
        skip_msg = "⏭️ <i>Aapne yeh trade skip kiya. Iska koi bhi live update bhej kar aapko disturb nahi kiya jayega.</i>"
        send_telegram_message(bot_token, chat_id, skip_msg, parse_mode="HTML")

    elif data.startswith("btst:"):
        comm = data.split(":", 1)[1]
        answer_callback_query(bot_token, cq_id, text=f"Analyzing {comm.upper()} BTST Setup...", show_alert=False)
        send_telegram_message(bot_token, chat_id, f"⏳ <i>{comm.upper()} ka Daily Chart + Macro + Overnight Gap Risk analyze ho raha hai...</i>", parse_mode="HTML")
        res = analyze_btst_swing(comm)
        send_telegram_message(bot_token, chat_id, res['card_text'], parse_mode="HTML", reply_markup=get_keyboard())

    elif data.startswith("smc:"):
        comm = data.split(":", 1)[1]
        answer_callback_query(bot_token, cq_id, text=f"Scanning {comm.upper()} SMC Structure...", show_alert=False)
        send_telegram_message(bot_token, chat_id, f"⏳ <i>{comm.upper()} ka Order Blocks + FVG + Liquidity Sweeps scan ho raha hai...</i>", parse_mode="HTML")
        smc_res = analyze_smc_setup(comm)
        send_telegram_message(bot_token, chat_id, smc_res['card_text'], parse_mode="HTML", reply_markup=get_keyboard())

    elif data.startswith("refresh:"):
        comm = data.split(":", 1)[1]
        answer_callback_query(bot_token, cq_id, text=f"Refreshing {comm.upper()} CMP...", show_alert=False)
        send_commodity_tip(bot_token, chat_id, comm)

    elif data.startswith("export:"):
        range_key = data.split(":", 1)[1]
        answer_callback_query(bot_token, cq_id, text=f"Generating {range_key.upper()} Excel Report...", show_alert=False)
        send_telegram_message(bot_token, chat_id, f"⏳ <i>SQLite database se {range_key.upper()} audit extract ho rahi hai aur Excel (.csv) file generate ho rahi hai...</i>", parse_mode="HTML")
        try:
            filepath, summary = generate_excel_csv_report(range_key)
            card = format_report_summary_card(summary)
            # 1. Send telegram summary card
            send_telegram_message(bot_token, chat_id, card, parse_mode="HTML")
            # 2. Send actual downloadable Excel CSV file!
            caption = f"📊 <b>MCX Trading Audit Report ({range_key.upper()})</b>\n<i>Download karke Excel / Sheets me kholein.</i>"
            send_telegram_document(bot_token, chat_id, filepath, caption=caption)
        except Exception as e:
            send_telegram_message(bot_token, chat_id, f"⚠️ Error generating export: {e}", parse_mode="HTML")

    elif data.startswith("wl_toggle:"):
        slug = data.split(":", 1)[1]
        new_list, is_on, status_text = toggle_commodity_watchlist(slug)
        answer_callback_query(bot_token, cq_id, text=status_text, show_alert=False)
        card_text = format_watchlist_card(new_list)
        kb = build_watchlist_keyboard(new_list)
        if message_id:
            edit_telegram_message(bot_token, chat_id, message_id, card_text, reply_markup=kb)

    elif data == "wl_set:priority":
        new_list = set_active_watchlist(["silver", "crude"])
        answer_callback_query(bot_token, cq_id, text="Watchlist set: Only Silver & Crude! 🌟", show_alert=True)
        card_text = format_watchlist_card(new_list)
        kb = build_watchlist_keyboard(new_list)
        if message_id:
            edit_telegram_message(bot_token, chat_id, message_id, card_text, reply_markup=kb)

    elif data == "wl_set:all":
        new_list = set_active_watchlist(["silver", "crude", "gold", "natgas", "aluminium", "zinc", "nickel", "electricity"])
        answer_callback_query(bot_token, cq_id, text="All 8 Commodities Enabled! ✅", show_alert=True)
        card_text = format_watchlist_card(new_list)
        kb = build_watchlist_keyboard(new_list)
        if message_id:
            edit_telegram_message(bot_token, chat_id, message_id, card_text, reply_markup=kb)

    elif data == "wl_done":
        answer_callback_query(bot_token, cq_id, text="Watchlist Saved! ✅", show_alert=False)
        active_wl = get_active_watchlist()
        names = ", ".join([s.capitalize() for s in active_wl])
        done_msg = (
            f"✅ <b>WATCHLIST SAVED & ACTIVE!</b>\n\n"
            f"• <b>Active Commodities ({len(active_wl)}/8) :</b> {names}\n\n"
            f"🚀 <i>Auto-Scanner aur SMC Institutional Alerts ab sirf inhi commodities ke aayenge! Baki sab filter out rahenge.</i>"
        )
        if message_id:
            edit_telegram_message(bot_token, chat_id, message_id, done_msg, reply_markup=None)
        else:
            send_telegram_message(bot_token, chat_id, done_msg, parse_mode="HTML")



# 8. Incoming Message Dispatcher
def handle_message(bot_token, chat_id, user_text, user_name):
    save_subscriber(chat_id)
    clean_text = user_text.strip().lower()

    if clean_text in ["/start", "/help", "❓ help & commands"]:
        min_p = get_min_profit()
        cur_sec = get_db_config("scan_interval", "45")
        welcome_msg = (
            f"👋 <b>Namaste {user_name}!</b>\n\n"
            f"🎯 <b>MCX Futures Institutional Advisory & Auto-Scanner Bot</b> mein aapka swagat hai.\n\n"
            f"🔔 <b>Automated Background Scanner:</b> ACTIVE ✅\n"
            f"• <i>Har {cur_sec} seconds me super-fast scan (Silver & Crude Priority). Target 1 profit <b>₹{min_p:,.0f}+</b> aur Win Chance <b>75%+</b> hone par instant alert aayega!</i>\n"
            f"• 🏛️ <b>SMC Sniper Trigger:</b> <i>Sirf <b>88% High-Win Rate Liquidity Sweeps (BSL/SSL Stop-Hunts)</b> bante hi instant alert aayega (Strict 5-step cross-verification ke sath)!</i>\n\n"
            f"🏛️ <b>Smart Money Concepts (SMC):</b>\n"
            f"• <code>/smc silver</code> | <code>/smc crude</code> | <code>/smc electricity</code>\n\n"
            f"🌙 <b>BTST / STBT & Swing Trading:</b>\n"
            f"• <code>/btst silver</code> | <code>/btst crude</code> | <code>/swing gold</code>\n\n"
            f"⚙️ <b>Commands & Tools:</b>\n"
            f"• <code>/watchlist</code> : Auto-Scan & SMC ke liye commodities select karein (e.g. Only Silver & Crude)\n"
            f"• <code>/export</code> : Weekly / Monthly Audit Report in Excel (.csv) File\n"
            f"• <code>/scaninterval 45</code> : Scanner speed set karein (e.g. 45s, 60s, 30s)\n"
            f"• <code>/news</code> : Global Macro & News Price Impact Report (Effect & Expected ₹ Move)\n"
            f"• <code>/report</code> ya <code>/pnl</code> : Daily Audit & Personal P&L Report dekhein\n"
            f"• <code>/setprofit 1500</code> : Minimum Target 1 profit filter set karein\n"
            f"• <code>/scan</code> : Abhi turant live market scan run karein\n"
            f"• <code>/status</code> : Scanner aur active alerts ka status dekhein\n"
            f"• <code>/testalert</code> : Sample Alert + Interactive Buttons test karein\n\n"
            f"📊 <b>Instant Intraday Advisors:</b>\n"
            f"• <code>/silver</code> (1 Kg) | <code>/crude</code> (10 BBL)\n"
            f"• <code>/gold</code> (10g) | <code>/natgas</code> (250 mmBtu)\n"
            f"• <code>/aluminium</code> (1 MT) | <code>/zinc</code> (1 MT)\n"
            f"• <code>/nickel</code> (250 Kg) | <code>/electricity</code> (50 MWh)\n\n"
            f"Niche quick buttons se tap karein 👇"
        )
        send_telegram_message(bot_token, chat_id, welcome_msg, parse_mode="HTML", reply_markup=get_keyboard())
        return

    # WATCHLIST / COMMODITY FILTER COMMAND & KEYBOARD BUTTON
    if clean_text in [
        "/watchlist", "/commodities", "/alerts", "/filter", "/settings",
        "🎯 alert watchlist (filter)", "watchlist", "filter"
    ]:
        card = format_watchlist_card()
        kb = build_watchlist_keyboard()
        send_telegram_message(bot_token, chat_id, card, parse_mode="HTML", reply_markup=kb)
        return

    # SET COMMODITIES DIRECT COMMAND (e.g. /setcommodities silver, crude OR /watchlist silver crude)
    if clean_text.startswith("/setcommodities") or (clean_text.startswith("/watchlist") and len(clean_text.split()) > 1):
        raw_args = clean_text.replace("/setcommodities", "").replace("/watchlist", "").replace(",", " ").strip()
        requested = [p.strip().lower() for p in raw_args.split() if p.strip()]
        alias_map = {
            "ng": "natgas", "gas": "natgas", "naturalgas": "natgas",
            "elec": "electricity", "power": "electricity",
            "alum": "aluminium", "aluminum": "aluminium",
            "al": "aluminium", "zn": "zinc", "ni": "nickel"
        }
        resolved = []
        for r in requested:
            mapped = alias_map.get(r, r)
            if mapped in ALL_SUPPORTED_COMMODITIES and mapped not in resolved:
                resolved.append(mapped)

        if resolved:
            set_active_watchlist(resolved)
            names = ", ".join([s.capitalize() for s in resolved])
            conf = (
                f"✅ <b>WATCHLIST UPDATED SUCCESSFULLY!</b>\n\n"
                f"• <b>Active Commodities ({len(resolved)}/8) :</b> {names}\n\n"
                f"🚀 <i>Auto-Scanner aur SMC Institutional Alerts ab sirf inhi commodities ke aayenge! Baki sab filter out rahenge.</i>"
            )
        else:
            conf = (
                f"⚠️ <b>Format:</b> <code>/setcommodities silver, crude</code>\n\n"
                f"Available Commodities: <code>silver, crude, gold, natgas, aluminium, zinc, nickel, electricity</code>"
            )
        send_telegram_message(bot_token, chat_id, conf, parse_mode="HTML", reply_markup=get_keyboard())
        return

    # SMART MONEY CONCEPTS (SMC) COMMAND & KEYBOARD BUTTON
    if clean_text.startswith("/smc") or clean_text in ["🏛️ smc institutional setups", "smc"]:
        parts = clean_text.split()
        if len(parts) > 1:
            comm = parts[1].strip()
            send_telegram_message(bot_token, chat_id, f"⏳ <i>{comm.upper()} ka Smart Money Concept (Order Blocks, FVG & Liquidity Pools) scan ho raha hai...</i>", parse_mode="HTML")
            smc_res = analyze_smc_setup(comm)
            send_telegram_message(bot_token, chat_id, smc_res['card_text'], parse_mode="HTML", reply_markup=get_keyboard())
            return
        else:
            smc_kb = {
                "inline_keyboard": [
                    [
                        {"text": "🪙 Silver SMC", "callback_data": "smc:silver"},
                        {"text": "🛢️ Crude SMC", "callback_data": "smc:crude"}
                    ],
                    [
                        {"text": "🥇 Gold SMC", "callback_data": "smc:gold"},
                        {"text": "🔥 NatGas SMC", "callback_data": "smc:natgas"}
                    ],
                    [
                        {"text": "⚡ Electricity SMC", "callback_data": "smc:electricity"},
                        {"text": "⚙️ Aluminium SMC", "callback_data": "smc:aluminium"}
                    ]
                ]
            }
            msg = (
                "🏛️ <b>SMART MONEY CONCEPTS (SMC) INSTITUTIONAL RADAR</b>\n\n"
                "• <b>Order Blocks (OB):</b> Institutional demand & supply bases\n"
                "• <b>Fair Value Gaps (FVG):</b> Unfilled price imbalance rebalances\n"
                "• <b>Liquidity Sweeps:</b> BSL & SSL retail stop-loss hunts\n\n"
                "⚡ <b>Auto-Alert Rule:</b> Jab bhi market me active SMC Sniper Setup banta hai, "
                "bot turant automatic tip bhejta hai (indicators ke 3 rules ka wait kiye bina)!\n\n"
                "Niche kisi commodity par tap karein ya command type karein:\n"
                "• <code>/smc silver</code> | <code>/smc crude</code>\n"
                "• <code>/smc gold</code>   | <code>/smc natgas</code>\n"
                "• <code>/smc electricity</code>"
            )
            send_telegram_message(bot_token, chat_id, msg, parse_mode="HTML", reply_markup=smc_kb)
            return

    # BTST / STBT / SWING COMMAND
    if clean_text.startswith("/btst") or clean_text.startswith("/stbt") or clean_text.startswith("/swing") or clean_text in ["🌙 btst / swing advisory", "btst", "swing"]:
        parts = clean_text.split()
        if len(parts) > 1:
            comm = parts[1].strip()
            send_telegram_message(bot_token, chat_id, f"⏳ <i>{comm.upper()} ka Daily (1D) Chart + Global Macro + Gap Risk analyze ho raha hai...</i>", parse_mode="HTML")
            res = analyze_btst_swing(comm)
            send_telegram_message(bot_token, chat_id, res['card_text'], parse_mode="HTML", reply_markup=get_keyboard())
            return
        else:
            # Inline selection menu for quick tap
            btst_kb = {
                "inline_keyboard": [
                    [
                        {"text": "🪙 Silver BTST", "callback_data": "btst:silver"},
                        {"text": "🛢️ Crude BTST", "callback_data": "btst:crude"}
                    ],
                    [
                        {"text": "🥇 Gold Swing", "callback_data": "btst:gold"},
                        {"text": "🔥 NatGas STBT", "callback_data": "btst:natgas"}
                    ],
                    [
                        {"text": "⚙️ Aluminium", "callback_data": "btst:aluminium"},
                        {"text": "🔩 Zinc Swing", "callback_data": "btst:zinc"}
                    ]
                ]
            }
            msg = (
                "🌙 <b>MCX ON-DEMAND BTST / STBT & SWING ADVISORY</b>\n\n"
                "Overnight ya multi-day hold karne se pehle Daily (1D) Chart, Global Macro aur Overnight Gap-Risk check karein.\n\n"
                "Niche kisi commodity par tap karein ya seedhe command type karein:\n"
                "• <code>/btst silver</code> | <code>/btst crude</code>\n"
                "• <code>/swing gold</code>   | <code>/stbt natgas</code>\n"
                "• <code>/swing aluminium</code> | <code>/btst zinc</code>"
            )
            send_telegram_message(bot_token, chat_id, msg, parse_mode="HTML", reply_markup=btst_kb)
            return

    # EXPORT AUDIT REPORT (EXCEL / CSV + TELEGRAM SUMMARY)
    if clean_text in ["/export", "/download", "/excel", "/csv", "📥 export excel report", "export"]:
        export_kb = {
            "inline_keyboard": [
                [
                    {"text": "📅 Aaj Ka (Today)", "callback_data": "export:today"},
                    {"text": "🗓️ Is Hafte Ka (Weekly - 7 Days)", "callback_data": "export:weekly"}
                ],
                [
                    {"text": "📆 Is Mahine Ka (Monthly - 30 Days)", "callback_data": "export:monthly"},
                    {"text": "📊 All-Time Full Audit", "callback_data": "export:all"}
                ]
            ]
        }
        prompt_msg = (
            "📥 <b>EXCEL & TELEGRAM TRADING AUDIT EXPORT</b>\n\n"
            "Aapko kis date range ki report chahiye? Niche se chuniye:\n\n"
            "• <b>📅 Aaj Ka (Today) :</b> Aaj ke session ke sabhi trades\n"
            "• <b>🗓️ Is Hafte Ka (Weekly) :</b> Pichle 7 dino ka complete audit\n"
            "• <b>📆 Is Mahine Ka (Monthly) :</b> Pichle 30 dino ka P&L breakdown\n"
            "• <b>📊 All-Time History :</b> Bot shuru hone se ab tak ka complete data\n\n"
            "💡 <i>Aapko Telegram par instant scorecard + detailed downloadable Excel (.csv) file dono milenge!</i>"
        )
        send_telegram_message(bot_token, chat_id, prompt_msg, parse_mode="HTML", reply_markup=export_kb)
        return

    # DAILY AUDIT & PNL REPORT
    if clean_text in ["/report", "/pnl", "/audit", "📋 daily audit & p&l"]:
        send_telegram_message(bot_token, chat_id, "⏳ <i>Aaj ke session ka comprehensive audit report generate ho raha hai...</i>", parse_mode="HTML")
        report_msg = generate_eod_report()
        send_telegram_message(bot_token, chat_id, report_msg, parse_mode="HTML", reply_markup=get_keyboard())
        return

    # SET PROFIT COMMAND
    if clean_text.startswith("/setprofit"):
        parts = clean_text.split()
        if len(parts) > 1 and parts[1].isdigit():
            new_val = float(parts[1])
            set_min_profit(new_val)
            msg = f"✅ <b>Minimum Auto-Alert Profit Filter updated to: ₹{new_val:,.0f} per 1 Lot!</b>"
        else:
            msg = "⚠️ Format: <code>/setprofit 1500</code> ya <code>/setprofit 2000</code>"
        send_telegram_message(bot_token, chat_id, msg, parse_mode="HTML", reply_markup=get_keyboard())
        return

    # SET SCAN INTERVAL COMMAND
    if clean_text.startswith("/scaninterval") or clean_text.startswith("/interval") or clean_text.startswith("/speed"):
        parts = clean_text.split()
        if len(parts) > 1 and parts[1].isdigit():
            sec_val = int(parts[1])
            if sec_val < 20:
                msg = "⚠️ Minimum scan interval <b>20 seconds</b> hai taaki exchange/feed rate-limit na karein aur bot safe rahe."
            else:
                set_db_config("scan_interval", str(sec_val))
                msg = f"⚡ <b>Scanner Cycle Speed Updated:</b> Har <b>{sec_val} seconds</b> me Auto-Scan & Active Trade Tracking chalega! 🚀"
        else:
            cur_int = get_db_config("scan_interval", "45")
            msg = (
                f"⚡ <b>AUTO-SCANNER INTERVAL CONFIG</b>\n\n"
                f"• <b>Current Speed :</b> Har <code>{cur_int} seconds</code> me auto-scan\n\n"
                f"Naya interval set karne ke liye type karein:\n"
                f"• <code>/scaninterval 45</code> (Recommended Fast 45s)\n"
                f"• <code>/scaninterval 60</code> (1 Minute)\n"
                f"• <code>/scaninterval 30</code> (Super Fast 30s - Safe with Candle Caching)"
            )
        send_telegram_message(bot_token, chat_id, msg, parse_mode="HTML", reply_markup=get_keyboard())
        return

    # MANUAL SCAN COMMAND
    if clean_text in ["/scan", "🔍 run live scan now"]:
        send_telegram_message(bot_token, chat_id, "🔍 <i>Sabhi 8 commodities ka live chart scan ho raha hai (Silver & Crude 1st)...</i>", parse_mode="HTML")
        run_scanner_cycle(bot_token, force=True)
        send_telegram_message(bot_token, chat_id, "✅ <i>Scan complete! Koi qualifying ₹1,500+ A+ setup hone par card bhej diya gaya hai.</i>", parse_mode="HTML", reply_markup=get_keyboard())
        return

    # STATUS COMMAND
    if clean_text in ["/status", "📊 bot & alert status"]:
        min_p = get_min_profit()
        is_open = is_mcx_open()
        active_wl = get_active_watchlist()
        wl_str = ", ".join([s.capitalize() for s in active_wl])
        cur_int = get_db_config("scan_interval", "45")
        status_msg = (
            f"📊 <b>BOT & SCANNER LIVE STATUS</b>\n\n"
            f"• <b>MCX Market Status :</b> {'🟢 OPEN' if is_open else '🔴 CLOSED / SLEEP'}\n"
            f"• <b>Active Watchlist   :</b> {wl_str} ({len(active_wl)}/8 Active)\n"
            f"• <b>Auto-Scan Interval :</b> Every {cur_int} Seconds ⚡\n"
            f"• <b>Priority Hierarchy :</b> Silver Micro (1 Kg) ➡️ Crude Oil (10 BBL) ➡️ Gold Ten (10g) ➡️ Others\n"
            f"• <b>Target 1 Filter    :</b> ₹{min_p:,.0f}+ per Lot\n"
            f"• <b>Min Win Chance     :</b> 75%+\n"
            f"• <b>Active Subscribers :</b> {len(active_subscribers)}\n"
            f"• <b>Active Tracked Trades :</b> {len(active_tracked_trades)}\n"
        )
        if active_tracked_trades:
            status_msg += "\n🎯 <b>Currently Monitored Trades:</b>\n"
            for k, v in active_tracked_trades.items():
                status_msg += f"• {k}: {v['direction']} @ ₹{v['entry']:,.1f} | T1: ₹{v['t1']:,.1f} (+₹{v['t1_profit']:,.0f})\n"
        send_telegram_message(bot_token, chat_id, status_msg, parse_mode="HTML", reply_markup=get_keyboard())
        return

    # GLOBAL MACRO & COMMODITY NEWS PRICE IMPACT COMMAND
    if clean_text in ["/news", "/macro", "news", "macro"]:
        send_telegram_message(bot_token, chat_id, "⏳ <i>Global Macro & News Radar scan ho raha hai... Sabhi stories ka MCX price effect analyze ho raha hai...</i>", parse_mode="HTML")
        report = analyze_macro_sentiment()
        send_telegram_message(bot_token, chat_id, report, parse_mode="HTML", reply_markup=get_keyboard())
        return

    if clean_text.startswith("/crude_news") or clean_text == "/news crude":
        send_telegram_message(bot_token, chat_id, "⏳ <i>Crude Oil News & MCX Mini price impact report generate ho rahi hai...</i>", parse_mode="HTML")
        report = analyze_commodity_news("crude")
        send_telegram_message(bot_token, chat_id, report, parse_mode="HTML", reply_markup=get_keyboard())
        return

    if clean_text.startswith("/silver_news") or clean_text == "/news silver":
        send_telegram_message(bot_token, chat_id, "⏳ <i>Silver Micro News & US Fed price impact report generate ho rahi hai...</i>", parse_mode="HTML")
        report = analyze_commodity_news("silver")
        send_telegram_message(bot_token, chat_id, report, parse_mode="HTML", reply_markup=get_keyboard())
        return

    # TEST ALERT COMMAND (WITH INTERACTIVE BUTTONS & DOLLAR BENCHMARK)
    if clean_text == "/testalert":
        test_id = f"TEST_CRUDE_{int(time.time())}"
        ist = timezone(timedelta(hours=5, minutes=30))
        today_str = datetime.now(ist).strftime("%Y-%m-%d")
        usd_ref = get_live_dollar_price("crude") or "WTI Crude: $101.17 / bbl"

        sample_res = {
            'commodity': 'Crude Oil Mini (10 BBL)',
            'expiry': '19 Nov, 26',
            'cmp': 9650.0,
            'direction': 'BUY',
            'sl_price': 9580.0,
            't1_price': 9810.0,
            't2_price': 9960.0,
            't1_profit_inr': 1600.0,
            'sl_risk_inr': 700.0,
            'prob_pct': 82,
            'reasons': ['Super Bullish EMA Cross (9 > 21) + RSI 61']
        }

        test_data = {
            'trade_id': test_id,
            'name': 'Crude Oil Mini (10 BBL)',
            'direction': 'BUY',
            'entry': 9650.0,
            't1': 9810.0,
            'sl': 9580.0,
            't1_profit': 1600.0,
            'sl_risk': 700.0,
            'time': time.time(),
            'date': today_str,
            't1_hit': False,
            'sl_hit': False,
            'prob_pct': 82,
            'reasons_str': "Super Bullish EMA Cross + RSI 61",
            'status': 'ACTIVE',
            'user_taken': False
        }
        save_audit_trade(test_data)

        test_msg = format_mobile_trading_card(sample_res, usd_ref=usd_ref, is_alert=True, trade_id=test_id)
        test_markup = {
            "inline_keyboard": [
                [
                    {"text": "🚀 Entry Taken (Track Live)", "callback_data": f"take:{test_id}"},
                    {"text": "⏭️ Skip Setup", "callback_data": f"skip:{test_id}"}
                ]
            ]
        }
        send_telegram_message(bot_token, chat_id, test_msg, parse_mode="HTML", reply_markup=test_markup)
        return

    # COMMODITY HANDLERS (MOBILE-FIRST ULTRA CLEAN CARDS)
    if clean_text.startswith("/silver") or clean_text.startswith("silver") or "silver micro" in clean_text:
        send_commodity_tip(bot_token, chat_id, 'silver', custom_cmp=extract_custom_cmp(clean_text))
        return

    if clean_text.startswith("/gold") or clean_text.startswith("gold") or "gold ten" in clean_text:
        send_commodity_tip(bot_token, chat_id, 'gold', custom_cmp=extract_custom_cmp(clean_text))
        return

    if clean_text.startswith("/crude") or clean_text.startswith("crude") or "crude oil" in clean_text:
        send_commodity_tip(bot_token, chat_id, 'crude', custom_cmp=extract_custom_cmp(clean_text))
        return

    if clean_text.startswith("/natgas") or clean_text.startswith("/ng") or "nat gas" in clean_text or "natural gas" in clean_text:
        send_commodity_tip(bot_token, chat_id, 'natgas', custom_cmp=extract_custom_cmp(clean_text))
        return

    if clean_text.startswith("/aluminium") or clean_text.startswith("/aluminum") or clean_text.startswith("/al") or "aluminium" in clean_text or "aluminum" in clean_text:
        send_commodity_tip(bot_token, chat_id, 'aluminium', custom_cmp=extract_custom_cmp(clean_text))
        return

    if clean_text.startswith("/zinc") or clean_text.startswith("/zn") or "zinc" in clean_text:
        send_commodity_tip(bot_token, chat_id, 'zinc', custom_cmp=extract_custom_cmp(clean_text))
        return

    if clean_text.startswith("/nickel") or clean_text.startswith("/ni") or "nickel" in clean_text:
        send_commodity_tip(bot_token, chat_id, 'nickel', custom_cmp=extract_custom_cmp(clean_text))
        return

    if clean_text.startswith("/electricity") or clean_text.startswith("/power") or clean_text.startswith("/elec") or "electricity" in clean_text or "power" in clean_text:
        send_commodity_tip(bot_token, chat_id, 'electricity', custom_cmp=extract_custom_cmp(clean_text))
        return

    # UNKNOWN COMMAND
    fallback_msg = (
        "❓ <i>Command samajh nahi aayi.</i>\n\n"
        "Kripya niche diye gaye buttons dabayein ya yeh type karein:\n"
        "• <code>/report</code> : Check Daily Audit & P&L Report\n"
        "• <code>/scan</code> : Run Live Market Scan\n"
        "• <code>/status</code> : Check Alert & Scanner Status\n"
        "• <code>/testalert</code> : Test Instant Alert with Buttons\n"
        "• <code>/silver</code>, <code>/crude</code>, <code>/gold</code>"
    )
    send_telegram_message(bot_token, chat_id, fallback_msg, parse_mode="HTML", reply_markup=get_keyboard())


def start_bot():
    print("=" * 60)
    print("🤖 MCX FUTURES TRADING ENGINE + 24/7 AUTO-SCANNER")
    print("🪙 Silver | 🛢️ Crude | 🥇 Gold | 🔥 NatGas | ⚙️ Alum | 🔩 Zinc | 🪙 Ni | ⚡ Elec")
    print("=" * 60)

    token = load_token()
    if not token:
        print("\n[!] TELEGRAM_BOT_TOKEN aapki .env file me missing hai!\n")
        return

    load_subscribers()

    # Test Bot Token
    try:
        check_url = f"https://api.telegram.org/bot{token}/getMe"
        resp = requests.get(check_url, timeout=10).json()
        if not resp.get("ok"):
            print(f"\n❌ Error: Invalid Bot Token in .env: {resp.get('description')}")
            return
        bot_info = resp["result"]
        print(f"\n✅ Connected successfully as @{bot_info.get('username')} ({bot_info.get('first_name')})")
        print("🚀 Trading Bot is LIVE with Background Scanner...")

        # Register Telegram Menu Commands
        trading_commands = [
            {"command": "start", "description": "Start Bot & Open Control Panel"},
            {"command": "watchlist", "description": "Select Commodities for Auto-Scan & SMC Alerts"},
            {"command": "export", "description": "Weekly/Monthly Excel (.csv) Audit Report"},
            {"command": "smc", "description": "Smart Money Concepts Institutional Analysis"},
            {"command": "btst", "description": "On-Demand BTST / STBT & Swing Analysis"},
            {"command": "news", "description": "Global Macro & News Price Impact Analysis"},
            {"command": "report", "description": "Daily Audit & Personal P&L Report"},
            {"command": "scan", "description": "Run Live Scan for A+ Setups"},
            {"command": "status", "description": "Check Scanner & Active Trades"},
            {"command": "setprofit", "description": "Set Min Profit Filter (e.g. 1500)"},
            {"command": "testalert", "description": "Test Instant Alert Notification"},
            {"command": "silver", "description": "MCX Silver Micro (1 Kg) Analysis"},
            {"command": "crude", "description": "MCX Crude Oil Mini (10 BBL) Analysis"},
            {"command": "gold", "description": "MCX Gold Ten (10g) Analysis"},
            {"command": "natgas", "description": "MCX Natural Gas Mini (250) Analysis"},
            {"command": "aluminium", "description": "MCX Aluminium Mini (1 MT) Analysis"},
            {"command": "zinc", "description": "MCX Zinc Mini (1 MT) Analysis"},
            {"command": "nickel", "description": "MCX Nickel (250 Kg) Analysis"},
            {"command": "electricity", "description": "MCX Electricity (50 MWh) Analysis"}
        ]
        try:
            requests.post(f"https://api.telegram.org/bot{token}/setMyCommands", json={"commands": trading_commands}, timeout=6)
        except Exception:
            pass

    except Exception as e:
        print(f"❌ Connection error: {e}")
        return

    # Start Background Auto-Scanner Thread
    scanner_thread = threading.Thread(target=background_scanner_loop, args=(token,), daemon=True)
    scanner_thread.start()

    # Launch Dedicated News Bot in parallel if TELEGRAM_NEWS_BOT_TOKEN is provided
    news_tok = os.getenv("TELEGRAM_NEWS_BOT_TOKEN")
    if news_tok and news_tok.strip() and news_tok != "YOUR_NEWS_BOT_TOKEN_HERE":
        try:
            from news_bot import start_news_bot
            news_thread = threading.Thread(target=start_news_bot, daemon=True)
            news_thread.start()
            print("🚀 Dual-Bot Mode: Dedicated News Bot successfully launched in background thread!")
        except Exception as e:
            print(f"[-] News bot thread startup error: {e}")

    last_update_id = 0

    while True:
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates?offset={last_update_id + 1}&timeout=30"
            res = requests.get(url, timeout=35).json()

            if res.get("ok"):
                for update in res.get("result", []):
                    last_update_id = update["update_id"]

                    # 1. Handle Inline Button Callback Queries
                    cq = update.get("callback_query")
                    if cq:
                        handle_callback(token, cq)
                        continue

                    # 2. Handle Text Messages
                    msg = update.get("message")
                    if msg and "text" in msg:
                        chat_id = msg["chat"]["id"]
                        user_text = msg["text"]
                        user_name = msg["from"].get("first_name", "Trader")
                        print(f"[+] Message received from {user_name} (Chat {chat_id}): {user_text}")
                        handle_message(token, chat_id, user_text, user_name)

        except requests.exceptions.RequestException:
            time.sleep(3)
        except Exception as e:
            print(f"Error in bot loop: {e}")
            time.sleep(2)


if __name__ == "__main__":
    start_bot()
