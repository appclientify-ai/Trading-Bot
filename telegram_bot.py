import os
import sys
import time
import json
import html
import requests
import warnings
import atexit
import signal
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
from macro_analyzer import (
    analyze_macro_sentiment,
    analyze_commodity_news,
    get_usd_trend_for_commodities,
    format_usd_trend_report,
    fetch_news_feed,
    analyze_headline_price_impact
)
from swing_advisor import analyze_btst_swing
from smc_engine import analyze_smc_setup, check_active_trade_reversal, get_commodity_news_confluence, COMMODITY_SPECS
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
eod_hold_advisory_sent_date = None

USER_POSITIONS_FILE = os.path.join(BASE_DIR, "user_active_positions.json")

# User Active Position Locking (chat_id -> active position dict)
# When a user enters a trade, general SMC scanning across other commodities is PAUSED!
# The bot focuses 100% on analyzing and monitoring this trade for Target, SL, and Trend Reversal.
user_active_positions = {}
user_awaiting_price_entry = {}  # chat_id -> trade_id when awaiting user's actual execution fill price
bot_running = False
_shutdown_sent = False

def load_user_positions():
    global user_active_positions
    if os.path.exists(USER_POSITIONS_FILE):
        try:
            with open(USER_POSITIONS_FILE, "r", encoding="utf-8") as f:
                user_active_positions = json.load(f)
        except Exception as e:
            print(f"[-] Error loading user positions: {e}")
            user_active_positions = {}
    return user_active_positions

def save_user_positions():
    try:
        with open(USER_POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(user_active_positions, f, indent=2)
    except Exception as e:
        print(f"[-] Error saving user positions: {e}")

load_user_positions()


# 1. Operating Hours & Market Window Check (08:00 AM - 12:00 AM Midnight IST)
def is_bot_active_hours():
    """
    Bot Active Operating Window:
    08:00 AM to 12:00 AM (Midnight, 24:00 IST)
    Monday through Friday for MCX trading & international pre/post market.
    """
    ist = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist)
    if now.weekday() >= 5:  # Saturday or Sunday (MCX closed)
        return False
    return 8 <= now.hour < 24


def is_mcx_open():
    """Returns True if within bot active operating window (8:00 AM - 12:00 AM IST on weekdays)"""
    return is_bot_active_hours()


# 2. Cloud Health Server (Render.com 24/7 compatibility & Keep-Alive)
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
                "operating_hours": "08:00 AM - 12:00 AM IST",
                "is_active_window": is_mcx_open(),
                "uptime_check": "OK",
                "time": time.time()
            }
            self.wfile.write(json.dumps(status_data).encode("utf-8"))

        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()

        def log_message(self, format, *args):
            pass

    try:
        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.TCPServer(("", port), HealthHandler) as httpd:
            print(f"[+] Cloud Health Server active on port {port} (Render & UptimeRobot ready | 08:00 AM - 12:00 AM IST)")
            httpd.serve_forever()
    except Exception as e:
        print(f"[-] Health server startup notice: {e}")

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


# 3. Market Window Reference (08:00 AM - 12:00 AM IST managed via is_bot_active_hours)


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


def broadcast_good_morning(bot_token, force=False):
    """
    Broadcasts Good Morning / Bot Online alert to all subscribers at market open (09:00 AM IST)
    or on bot startup during active trading window.
    """
    ist = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist)
    today_str = now.strftime("%Y-%m-%d")
    last_sent = get_db_config("good_morning_sent_date", "")
    if not force and last_sent == today_str:
        return False

    set_db_config("good_morning_sent_date", today_str)

    active_wl = get_active_watchlist()
    wl_names = ", ".join([s.capitalize() for s in active_wl])

    msg = (
        "🌅 <b>GOOD MORNING TRADERS! ☀️</b>\n\n"
        "🟢 <b>MCX TRADING BOT IS ONLINE & ACTIVE!</b>\n\n"
        "⏰ <b>Market Session  :</b> <code>09:00 AM - 11:30 PM IST</code>\n"
        "🏛️ <b>Strategy Model   :</b> <b>Smart Money Concepts (SMC)</b>\n"
        "🔍 <b>Scanner Engine   :</b> <i>Order Blocks, FVG & Liquidity Sweeps Active</i>\n"
        f"🎯 <b>Active Watchlist ({len(active_wl)}/8) :</b> <i>{wl_names}</i>\n"
        "📰 <b>Macro Confluence :</b> <i>Real-time News & Sentiment Confluence Active</i>\n\n"
        "💡 <i>'Plan your trade, trade your plan. Execute with strict risk management!'</i> 🚀\n\n"
        "👉 <i>Type <code>/scan</code> for live market scan or <code>/status</code> to check setups.</i>"
    )
    print(f"[+] 🌅 Broadcasting Good Morning / Bot Online alert for {today_str}...")
    broadcast_alert(bot_token, msg, reply_markup=get_keyboard())
    return True


def broadcast_good_night(bot_token, force=False, reason="session_close"):
    """
    Broadcasts Good Night / Bot Offline alert to all subscribers at market close (23:55 IST)
    or when the bot is stopped/turned off.
    """
    ist = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist)
    today_str = now.strftime("%Y-%m-%d")
    last_sent = get_db_config("good_night_sent_date", "")
    if not force and reason == "session_close" and last_sent == today_str:
        return False

    if reason == "session_close":
        set_db_config("good_night_sent_date", today_str)

    if reason == "shutdown":
        msg = (
            "🔴 <b>BOT OFFLINE / SHUTDOWN ALERT</b>\n\n"
            "🌙 <b>Trading Bot is now OFF.</b>\n\n"
            "• <b>Status :</b> Auto-Scanner Paused.\n"
            "• <b>System :</b> Process stopped or maintenance in progress.\n\n"
            "😴 <i>Good Night traders! Bot will resume scanning on next startup.</i>"
        )
    else:
        msg = (
            "🌙 <b>GOOD NIGHT TRADERS! 😴✨</b>\n\n"
            "🔴 <b>MCX MARKET SESSION CLOSED | BOT GOING TO SLEEP</b>\n\n"
            "⏰ <b>Session Status :</b> MCX Trading Session Concluded\n"
            "🔒 <b>Scanner State  :</b> Background Scanners Paused until 09:00 AM IST\n"
            "📊 <b>Daily Audit    :</b> Trades logged to database (type <code>/report</code>)\n\n"
            "💤 <i>'Today's trading session has ended. Rest well, review your trades, and recharge!'</i>\n\n"
            "🌅 <i>Bot kal subah 09:00 AM IST par wapas Good Morning message ke sath active hoga. Shubh Ratri!</i>"
        )
    print(f"[+] 🌙 Broadcasting Good Night / Bot Offline alert (reason: {reason})...")
    broadcast_alert(bot_token, msg, reply_markup=get_keyboard())
    return True


def handle_bot_shutdown():
    """Triggered on graceful shutdown / process termination"""
    global _shutdown_sent, bot_running
    if _shutdown_sent or not bot_running:
        return
    _shutdown_sent = True
    token = load_token()
    if token:
        try:
            broadcast_good_night(token, force=True, reason="shutdown")
        except Exception as e:
            print(f"[-] Shutdown notification error: {e}")

atexit.register(handle_bot_shutdown)


def check_daily_kill_switch(bot_token):
    """
    Checks if today's realized losses have exceeded the user-configured daily loss limit.
    If limit is hit, triggers the Kill-Switch to pause new setup alerts and protect capital.
    """
    ist = timezone(timedelta(hours=5, minutes=30))
    today_str = datetime.now(ist).strftime("%Y-%m-%d")

    # Check if kill switch is currently active
    active_date = get_db_config("kill_switch_active_date", "")
    if active_date == today_str:
        return True

    try:
        max_limit = float(get_db_config("max_daily_loss", "5000.0"))
    except Exception:
        max_limit = 5000.0

    audit = load_daily_audit()
    today_trades = [t for t in audit if t.get("date") == today_str]
    user_trades = [t for t in today_trades if t.get("user_taken")]
    trades_to_eval = user_trades if user_trades else today_trades

    total_loss = 0.0
    for t in trades_to_eval:
        st = str(t.get("status", "")).upper()
        if "SL_HIT" in st or "LOSS" in st:
            total_loss += float(t.get("sl_risk", 0.0))

    if total_loss >= max_limit:
        set_db_config("kill_switch_active_date", today_str)
        msg = (
            "🛑 <b>DAILY MAX LOSS KILL-SWITCH ACTIVATED!</b> 🛑\n\n"
            f"• <b>Daily Max Loss Limit  :</b> ₹{max_limit:,.0f}\n"
            f"• <b>Today's Realized Loss :</b> <b>-₹{total_loss:,.0f} 🔴</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🛡️ <b>CAPITAL & DISCIPLINE PROTECTION MODE:</b>\n"
            "<i>Revenge trading aur overtrading se aapka account protect karne ke liye aaj ke naye auto-scan alerts <b>PAUSE</b> kar diye gaye hain.</i>\n\n"
            "💡 <i>'Trading me sabse badi skill hai ye janna ki kab rukna hai. Loss limit hit hone par screen close karein aur kal fresh mind se aayein!'</i>\n\n"
            "👉 <i>Agar aap emergency me override karna chahein, to <code>/killswitch off</code> type karein.</i>"
        )
        print(f"[!] 🛑 Daily Max Loss Kill-Switch Activated: -₹{total_loss:,.0f} >= ₹{max_limit:,.0f}")
        broadcast_alert(bot_token, msg, reply_markup=get_keyboard())
        return True
    return False


def broadcast_eod_hold_advisory(bot_token):
    """
    15 Minutes before MCX session close (23:40 IST):
    Scans running active trades and gives explicit HOLD OVERNIGHT (BTST) vs SQUARE OFF advisory.
    """
    load_user_positions()
    if user_active_positions:
        for chat_id_str, pos in list(user_active_positions.items()):
            try:
                cid = int(chat_id_str)
            except Exception:
                cid = chat_id_str

            comm_key = pos.get('comm_key', 'crude')
            direction = pos.get('direction', 'BUY')
            entry = pos.get('entry', 0.0)
            sl = pos.get('sl', 0.0)
            t1 = pos.get('t1', 0.0)
            trade_id = pos.get('trade_id', 'POS')
            comm_name = pos.get('name', comm_key.upper())

            chk = check_active_trade_reversal(comm_key, direction, entry, sl, t1)
            cmp = chk.get('cmp', entry)
            pnl_inr = chk.get('pnl_inr', 0.0)
            pnl_sign = "+" if pnl_inr >= 0 else "-"
            pnl_str = f"{pnl_sign}₹{abs(pnl_inr):,.0f}"

            is_profitable = (pnl_inr > 0)
            has_reversal = chk.get('reversal_flag', False)

            if is_profitable and not has_reversal:
                rec_badge = "🟢 <b>HOLD OVERNIGHT (BTST / SWING RUNNER)</b>"
                rec_desc = (
                    f"Position profit me chal rahi hai (<b>{pnl_str} / Lot</b>) aur institutional trend structure intact hai. "
                    f"Overnight carry kar sakte hain, par <b>Stop-Loss ko strict Entry Cost (₹{entry:,.2f})</b> par trail karke rakhein taaki subah koi loss na ho."
                )
            else:
                rec_badge = "🔴 <b>SQUARE OFF / EXIT INTRADAY NOW</b>"
                rec_desc = (
                    f"Position middle ya flat/loss me hai (<b>{pnl_str} / Lot</b>). "
                    f"Overnight international session gap-risk aur opening tick volatility se capital protect karne ke liye session close se pehle exit karna safest hai."
                )

            msg = (
                f"⏰ <b>15 MINUTES TO MCX CLOSE — OVERNIGHT HOLD ADVISORY!</b>\n\n"
                f"• <b>Active Position :</b> <b>{comm_name}</b> ({direction})\n"
                f"• <b>Entry Price     :</b> ₹{entry:,.2f}\n"
                f"• <b>Running CMP     :</b> <b>₹{cmp:,.2f}</b>\n"
                f"• <b>Live P&L        :</b> <b>{pnl_str} per Lot</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📋 <b>BOT RECOMMENDATION :</b>\n{rec_badge}\n\n"
                f"💡 <b>Reason:</b>\n<i>{rec_desc}</i>\n\n"
                f"👉 <i>Position close karne ke liye niche button dabayein:</i>"
            )
            markup = {
                "inline_keyboard": [
                    [{"text": "🔴 Confirm Exit / Square Off Now", "callback_data": f"exit_confirm:{trade_id}"}],
                    [{"text": "🔄 Refresh Live P&L", "callback_data": f"refresh_pos:{trade_id}"}]
                ]
            }
            send_telegram_message(bot_token, cid, msg, parse_mode="HTML", reply_markup=markup)
    else:
        msg = (
            "⏰ <b>15 MINUTES TO MCX SESSION CLOSE (23:40 IST)</b>\n\n"
            "• <b>Session Notice :</b> MCX trading session concludes in 15 minutes.\n"
            "• <b>Advisory       :</b> Sabhi intraday positions ko square-off karein ya overnight trades me strict Cost-to-Cost SL maintain karein.\n\n"
            "📊 <i>Market band hone ke baad 23:55 IST par detailed EOD P&L audit report broadcast hogi.</i>"
        )
        broadcast_alert(bot_token, msg, reply_markup=get_keyboard())


def check_economic_calendar_events(bot_token):
    """
    Monitors recurring high-impact economic calendar events:
    - EIA Crude Oil Stocks (Wednesdays at 20:00 / 20:30 IST)
    - US CPI Inflation (Monthly 10th-14th around 18:00 / 18:30 IST)
    - US Non-Farm Payrolls / NFP (First Friday of month at 18:00 / 18:30 IST)
    Sends 15-minute advance alert to traders.
    """
    ist = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist)
    today_str = now.strftime("%Y-%m-%d")
    weekday = now.weekday()
    hour = now.hour
    minute = now.minute

    # 1. EIA Crude Oil Inventory: Every Wednesday at 20:00 IST (Alert at 19:45 IST)
    if weekday == 2 and hour == 19 and 45 <= minute <= 58:
        alert_key = f"eia_alert_{today_str}"
        if get_db_config(alert_key, "") != today_str:
            set_db_config(alert_key, today_str)
            eia_msg = (
                "⚠️ <b>HIGH-IMPACT EVENT ALERT: EIA CRUDE OIL INVENTORY IN 15 MINS!</b> ⚠️\n\n"
                "⏰ <b>Release Time :</b> <code>08:00 PM IST (Today)</code>\n"
                "🎯 <b>Impact Asset :</b> <b>Crude Oil Mini & Natural Gas</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚡ <b>Expected Volatility:</b> ±₹150 to ₹300 per barrel spike!\n"
                "💡 <b>SMC TRADING RULES:</b>\n"
                "• Data release se 15 min pehle fresh aggressive entries <b>AVOID</b> karein.\n"
                "• Running positions ka Stop-Loss turant <b>Cost-to-Cost (Entry)</b> par trail karein.\n"
                "• Data aane ke baad first 15m candle confirmation par hi clean trade karein."
            )
            print("[+] ⚠️ Broadcasting EIA Crude Oil Inventory 15-minute warning...")
            broadcast_alert(bot_token, eia_msg, reply_markup=get_keyboard())

    # 2. US NFP (First Friday of Month) at 18:00 IST (Alert at 17:45 IST)
    if weekday == 4 and now.day <= 7 and hour == 17 and 45 <= minute <= 58:
        alert_key = f"nfp_alert_{today_str}"
        if get_db_config(alert_key, "") != today_str:
            set_db_config(alert_key, today_str)
            nfp_msg = (
                "⚠️ <b>HIGH-IMPACT EVENT ALERT: US NON-FARM PAYROLLS (NFP) IN 15 MINS!</b> ⚠️\n\n"
                "⏰ <b>Release Time :</b> <code>06:00 PM IST (Today)</code>\n"
                "🎯 <b>Impact Asset :</b> <b>Gold Ten, Silver Micro, USD (DXY)</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚡ <b>Expected Volatility:</b> Silver me ±₹1,500 to ₹3,000 / kg move!\n"
                "💡 <b>SMC TRADING RULES:</b>\n"
                "• NFP release ke waqt massive algorithmic whipsaw hota hai.\n"
                "• Fresh entries pause rakhein aur existing trades me SL Cost par trail karein."
            )
            print("[+] ⚠️ Broadcasting US NFP 15-minute warning...")
            broadcast_alert(bot_token, nfp_msg, reply_markup=get_keyboard())

    # 3. US CPI Inflation (10th-14th of Month) at 18:00 IST (Alert at 17:45 IST)
    if (weekday in [1, 2, 3]) and 10 <= now.day <= 14 and hour == 17 and 45 <= minute <= 58:
        alert_key = f"cpi_alert_{today_str}"
        if get_db_config(alert_key, "") != today_str:
            set_db_config(alert_key, today_str)
            cpi_msg = (
                "⚠️ <b>HIGH-IMPACT EVENT ALERT: US CPI INFLATION DATA IN 15 MINS!</b> ⚠️\n\n"
                "⏰ <b>Release Time :</b> <code>06:00 PM IST (Today)</code>\n"
                "🎯 <b>Impact Asset :</b> <b>Gold, Silver, Crude Oil, DXY</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚡ <b>Expected Volatility:</b> Dollar Index & Bullion massive realignment!\n"
                "💡 <b>SMC TRADING RULES:</b>\n"
                "• Data aate hi initial spike par chase na karein.\n"
                "• First 15m candle close hone dein taaki institutional order flow confirm ho."
            )
            print("[+] ⚠️ Broadcasting US CPI Inflation 15-minute warning...")
            broadcast_alert(bot_token, cpi_msg, reply_markup=get_keyboard())




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
            [{"text": "🏛️ SMC Institutional Setups"}, {"text": "🔍 Run Live SMC Scan"}],
            [{"text": "🎯 Active Position & Live P&L"}, {"text": "🔴 Confirm Exit / Close Trade"}],
            [{"text": "💵 USD (DXY) Trend Radar"}, {"text": "🌙 BTST / Swing Advisory"}],
            [{"text": "⚙️ Aluminium Mini (1 MT)"}, {"text": "🔩 Zinc Mini (1 MT)"}],
            [{"text": "🎯 Alert Watchlist (Filter)"}, {"text": "📋 Daily Audit & P&L"}],
            [{"text": "📊 Bot & Alert Status"}, {"text": "❓ Help & Commands"}]
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

    # Live USD (DXY) Trend Radar for Silver, Gold & Crude Oil
    if any(k in comm_name for k in ["SILVER", "GOLD", "CRUDE"]):
        try:
            usd_data = get_usd_trend_for_commodities()
            card.append(f"{usd_data.get('short_badge', '')}")
        except Exception:
            pass

    if usd_ref:
        card.append(f"🌐 <b>Global Ref:</b> <code>{usd_ref}</code>")

    card.append("━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(card)


def send_commodity_tip(bot_token, chat_id, comm_key, custom_cmp=None):
    if comm_key not in COMMODITY_SPECS and comm_key not in COMMODITY_ADVISORS:
        send_telegram_message(bot_token, chat_id, f"⚠️ Unknown commodity: {comm_key}", parse_mode="HTML")
        return

    name_label = COMMODITY_SPECS.get(comm_key, {}).get('name', comm_key.capitalize())
    send_telegram_message(bot_token, chat_id, f"⏳ <i>{name_label} ka Big Chart SMC + Live News Confluence analyze ho raha hai...</i>", parse_mode="HTML")

    # Force tip generates the institutional setup (Order Block / FVG / Dealing Range) even if no live bar sweep
    res = analyze_smc_setup(comm_key, custom_cmp=custom_cmp, force_tip=True)
    if not isinstance(res, dict) or not res.get('card_text'):
        # Fallback to advisor if error
        name_label, advisor_fn = COMMODITY_ADVISORS.get(comm_key, (comm_key, None))
        if advisor_fn:
            adv_res = advisor_fn(custom_cmp=custom_cmp, return_data=True)
            usd_ref = get_live_dollar_price(comm_key)
            card_text = format_mobile_trading_card(adv_res, usd_ref=usd_ref)
            send_telegram_message(bot_token, chat_id, card_text, parse_mode="HTML")
        return

    now_ts = time.time()
    trade_id = f"SMC_{comm_key[:3].upper()}_{int(now_ts)}"
    card_text = res['card_text']

    today_str = datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d")
    trade_data = {
        'trade_id': trade_id,
        'name': f"{name_label} [SMC]",
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
        'prob_pct': res.get('prob_pct', 82),
        'reasons_str': res.get('reasons', ['SMC Institutional Setup'])[0],
        'status': 'ACTIVE',
        'user_taken': False,
        'comm_key': comm_key
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
                {"text": "🔄 Refresh CMP & SMC", "callback_data": f"refresh:{comm_key}"},
                {"text": "🔴 Confirm Exit / Close", "callback_data": f"exit_confirm:{trade_id}"}
            ]
        ]
    }

    send_telegram_message(bot_token, chat_id, card_text, parse_mode="HTML", reply_markup=markup)


# 6. End-of-Day (EOD) Comprehensive Post-Market Audit & P&L Report
def generate_eod_report(today_str=None):
    try:
        evaluate_and_sync_active_trades(is_eod_close=True)
    except Exception as e:
        print(f"[-] EOD evaluate_and_sync_active_trades error: {e}")

    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist)

    if not today_str:
        today_str = now_ist.strftime("%Y-%m-%d")

    audit = load_daily_audit()
    today_trades = [t for t in audit if t.get("date") == today_str]

    # Smart late-night / early-morning session recovery:
    # If run between 00:00 and 08:59 IST and today has no trades yet, display yesterday's closed session
    if not today_trades and now_ist.hour < 9:
        yesterday_str = (now_ist - timedelta(days=1)).strftime("%Y-%m-%d")
        yesterday_trades = [t for t in audit if t.get("date") == yesterday_str]
        if yesterday_trades:
            today_str = yesterday_str
            today_trades = yesterday_trades

    if not today_trades:
        return (
            f"📊 <b>MCX POST-MARKET EXECUTIVE AUDIT & P&L REPORT</b>\n"
            f"📅 <b>Date:</b> <code>{today_str}</code> | <b>Session:</b> <code>CLOSED 🏁</code>\n\n"
            f"ℹ️ <i>Aaj ke session me koi qualifying trade record nahi mila ya market holiday tha.</i>\n"
            f"📌 <i>Subah 09:00 AM MCX open hote hi live scanner dubara active ho jayega!</i>"
        )

    # In case any trade somehow retained ACTIVE or IN PROGRESS, ensure it gets closed at CMP immediately:
    for t in today_trades:
        st = str(t.get("status", "")).upper()
        if st in ["ACTIVE", "IN PROGRESS", "RUNNING", "PENDING"]:
            try:
                from mcx_feed import get_live_mcx_price
                raw_name = t.get('name', '').lower()
                slug = 'silver'
                if 'crude' in raw_name: slug = 'crude'
                elif 'gold' in raw_name: slug = 'gold'
                elif 'nat' in raw_name or 'gas' in raw_name: slug = 'natgas'
                elif 'alum' in raw_name: slug = 'aluminium'
                elif 'zinc' in raw_name: slug = 'zinc'
                elif 'nick' in raw_name: slug = 'nickel'
                elif 'elec' in raw_name: slug = 'electricity'

                cmp_val = get_live_mcx_price(slug) or float(t.get('entry', 0))
                is_buy = "BUY" in str(t.get("direction", "BUY")).upper()
                entry_val = float(t.get('entry', 0))
                pnl_pts = (cmp_val - entry_val) if is_buy else (entry_val - cmp_val)
                spec = COMMODITY_SPECS.get(slug, {})
                tick_val = spec.get('tick_val', 1.0)
                pnl_inr = round(pnl_pts * tick_val, 0)

                if pnl_inr > 0:
                    t['status'] = f"EOD_PROFIT:+₹{pnl_inr:,.0f}"
                    t['t1_profit'] = pnl_inr
                    t['t1_hit'] = True
                elif pnl_inr < 0:
                    t['status'] = f"EOD_LOSS:-₹{abs(pnl_inr):,.0f}"
                    t['sl_risk'] = abs(pnl_inr)
                    t['sl_hit'] = True
                else:
                    t['status'] = "EOD_BREAKEVEN:₹0"
            except Exception:
                t['status'] = "EOD_BREAKEVEN:₹0"

    total_signals = len(today_trades)
    t1_hits = sum(1 for t in today_trades if t.get("status") == "T1_HIT" or "EOD_PROFIT" in str(t.get("status", "")))
    sl_hits = sum(1 for t in today_trades if t.get("status") == "SL_HIT" or "EOD_LOSS" in str(t.get("status", "")))
    eod_closed = sum(1 for t in today_trades if "EOD_" in str(t.get("status", "")))

    theo_profit = sum(float(t.get("t1_profit", 0) or 0) for t in today_trades if t.get("status") == "T1_HIT" or "EOD_PROFIT" in str(t.get("status", "")))
    theo_loss = sum(float(t.get("sl_risk", 0) or 0) for t in today_trades if t.get("status") == "SL_HIT" or "EOD_LOSS" in str(t.get("status", "")))
    net_theo_pnl = theo_profit - theo_loss

    # User taken trades
    user_taken = [t for t in today_trades if t.get("user_taken")]
    user_total = len(user_taken)
    user_t1 = sum(1 for t in user_taken if t.get("status") == "T1_HIT" or "EOD_PROFIT" in str(t.get("status", "")))
    user_sl = sum(1 for t in user_taken if t.get("status") == "SL_HIT" or "EOD_LOSS" in str(t.get("status", "")))
    user_eod = sum(1 for t in user_taken if "EOD_" in str(t.get("status", "")))
    user_profit = sum(float(t.get("t1_profit", 0) or 0) for t in user_taken if t.get("status") == "T1_HIT" or "EOD_PROFIT" in str(t.get("status", "")))
    user_loss = sum(float(t.get("sl_risk", 0) or 0) for t in user_taken if t.get("status") == "SL_HIT" or "EOD_LOSS" in str(t.get("status", "")))
    net_user_pnl = user_profit - user_loss

    closed_trades = t1_hits + sl_hits
    win_rate = (t1_hits / closed_trades * 100) if closed_trades > 0 else 0.0

    user_closed = user_t1 + user_sl
    user_win_rate = (user_t1 / user_closed * 100) if user_closed > 0 else 0.0

    theo_str = f"+₹{net_theo_pnl:,.0f}" if net_theo_pnl >= 0 else f"-₹{abs(net_theo_pnl):,.0f}"
    user_str = f"+₹{net_user_pnl:,.0f}" if net_user_pnl >= 0 else f"-₹{abs(net_user_pnl):,.0f}"

    COMMODITY_ICONS = {
        'crude': '🛢️', 'oil': '🛢️',
        'silver': '🪙',
        'gold': '🥇',
        'natgas': '🔥', 'gas': '🔥',
        'aluminium': '⚙️',
        'zinc': '🔩',
        'nickel': '🪙',
        'electricity': '⚡'
    }

    trade_items = []
    for i, t in enumerate(today_trades, 1):
        c_name = t.get("name", "Commodity")
        c_lower = c_name.lower()
        icon = '📊'
        for k, v in COMMODITY_ICONS.items():
            if k in c_lower:
                icon = v
                break

        dirn = t.get("direction", "BUY")
        st = str(t.get("status", "EOD_BREAKEVEN:₹0"))
        t_prof = float(t.get("t1_profit", 0) or 0)
        s_risk = float(t.get("sl_risk", 0) or 0)
        taken_badge = " • 👤 <b>Aapka Trade</b>" if t.get("user_taken") else ""

        if st == "T1_HIT":
            outcome = f"🟢 <b>WIN +₹{t_prof:,.0f}</b>"
            diag = t.get("reasons_str") or "SMC Liquidity Sweep + MTF Trend Alignment"
        elif st == "SL_HIT":
            outcome = f"🔴 <b>LOSS -₹{s_risk:,.0f}</b>"
            diag = t.get("fail_reason") or "Sharp Volatility Spike & Reversal Trap"
        elif "EOD_PROFIT" in st:
            outcome = f"🏁 <b>CLOSED AT EOD (+₹{t_prof:,.0f} Profit)</b>"
            diag = "Valued at Final Market Closing CMP"
        elif "EOD_LOSS" in st:
            outcome = f"🏁 <b>CLOSED AT EOD (-₹{s_risk:,.0f} Loss)</b>"
            diag = "Valued at Final Market Closing CMP"
        else:
            outcome = "🏁 <b>CLOSED AT EOD (Breakeven ₹0)</b>"
            diag = "Valued at Final Market Closing CMP"

        # Compact, high-contrast, mobile-first 2-line trade card
        trade_items.append(
            f"<b>{i:02d}.</b> {icon} <b>{c_name.upper()}</b> ({dirn}){taken_badge}\n"
            f"     ↳ {outcome} | <i>{diag[:55]}</i>"
        )

    log_str = "\n".join(trade_items)

    report = (
        f"📊 <b>MCX POST-MARKET EXECUTIVE AUDIT & P&L REPORT</b>\n"
        f"📅 <b>Date:</b> <code>{today_str}</code> | <b>Session:</b> <code>CLOSED 🏁</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 <b>PERFORMANCE SCORECARD:</b>\n"
        f"• Total Signals Generated : <b>{total_signals} Signals</b>\n"
        f"• Target 1 Hit (Wins)     : <b>{t1_hits} 🟢</b>\n"
        f"• Stop-Loss Hit (Losses)   : <b>{sl_hits} 🔴</b>\n"
        f"• Closed at EOD Valuation : <b>{eod_closed} 🏁</b>\n"
        f"• 📈 Overall Win Rate     : <b>{win_rate:.1f}%</b>\n"
        f"• 💰 Theoretical P&L      : <b>{theo_str}</b> (1-Lot Base)\n"
    )

    if user_total > 0:
        report += (
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💼 <b>AAPKA PERSONAL REALIZED P&L:</b>\n"
            f"• 👤 Trades Taken      : <b>{user_total} Trades</b> ({user_t1} Won / {user_sl} Lost)\n"
            f"• 📈 Personal Win Rate : <b>{user_win_rate:.1f}%</b>\n"
            f"• 💵 Realized Net P&L  : <b>{user_str} 🔥</b>\n"
        )

    report += (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>TRADE-BY-TRADE AUDIT & REASON ANALYSIS:</b>\n"
        f"{log_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛡️ <b>SYSTEM UPGRADE ACTIVE:</b>\n"
        f"• <b>4-Angle MTF (1H+30m+15m+5m)</b> + <b>News Confluence</b> filter active.\n"
        f"• <b>Sharp Volatility & Reversal Traps</b> are pre-warned and blocked.\n"
        f"📌 <i>Next Session Live Institutional Scanner begins at 09:00 AM IST.</i>"
    )

    return report


# 6. Dedicated Active Trade Tracker + Pure SMC Institutional Scanner
def run_scanner_cycle(bot_token, force=False):
    global active_tracked_trades, alert_cooldowns, user_active_positions
    now_ts = time.time()
    ist = timezone(timedelta(hours=5, minutes=30))
    today_str = datetime.now(ist).strftime("%Y-%m-%d")

    if not force and not is_mcx_open():
        return

    # ─────────────────────────────────────────────────────────────────────────────
    # STEP 1: DEDICATED ACTIVE TRADE TRACKING & INSTANT REVERSAL DETECTION
    # Agar user ne kisi trade me entry li hai, to baki sari commodities ka auto-scan PAUSE!
    # Bot 100% focus us active position ke Target, SL aur Trend Reversal (CHoCH) par karega.
    # ─────────────────────────────────────────────────────────────────────────────
    load_user_positions()
    if user_active_positions:
        for chat_id_str, pos in list(user_active_positions.items()):
            try:
                chat_id = int(chat_id_str)
            except Exception:
                chat_id = chat_id_str

            comm_key = pos.get('comm_key', 'crude')
            direction = pos.get('direction', 'BUY')
            entry = pos.get('entry', 0.0)
            sl = pos.get('sl', 0.0)
            t1 = pos.get('t1', 0.0)
            trade_id = pos.get('trade_id', f"POS_{int(now_ts)}")
            comm_name = pos.get('name', comm_key.upper())

            # Evaluate against live tick and 15m candle structure
            chk = check_active_trade_reversal(comm_key, direction, entry, sl, t1)
            reversal_flag = chk.get('reversal_flag', False)
            reason = chk.get('reason', '')
            action_required = chk.get('action_required', 'HOLD POSITION')
            cmp = chk.get('cmp', entry)
            pnl_pts = chk.get('pnl_pts', 0.0)
            pnl_inr = chk.get('pnl_inr', 0.0)
            is_sl = chk.get('is_sl', False)
            is_t1 = chk.get('is_t1', False)

            pos['last_cmp'] = cmp
            pos['pnl_inr'] = pnl_inr
            save_user_positions()

            pnl_sign = "+" if pnl_inr >= 0 else "-"
            pnl_str = f"{pnl_sign}₹{abs(pnl_inr):,.0f}"

            # 1. Target 1 Hit Alert
            if is_t1 and not pos.get('t1_alerted'):
                pos['t1_alerted'] = True
                save_user_positions()
                t1_msg = (
                    f"🎉 <b>BINGO! TARGET 1 HIT!</b> 🎉\n\n"
                    f"• <b>Commodity :</b> <b>{comm_name}</b>\n"
                    f"• <b>Direction :</b> <b>{direction}</b>\n"
                    f"• <b>Target 1  :</b> <b>₹{t1:,.2f} REACHED!</b>\n"
                    f"• <b>Live P&L  :</b> <b>{pnl_str} per 1 Lot!</b> 🔥\n\n"
                    f"🎯 <b>MULTI-LOT SCALING STRATEGY:</b>\n"
                    f"• <b>Single Lot Trader :</b> Full profit book karke session close karein (<b>{pnl_str} / Lot</b>)!\n"
                    f"• <b>Multi-Lot Trader  :</b> <b>50% Lots book karein</b> (Profit locked) aur balance runner lots ka <b>Stop-Loss turant Entry Cost (₹{entry:,.2f}) par shift kar dein</b> for Target 2 runner!\n\n"
                    f"<i>Agar aap poori position exit kar rahe hain, to niche button dabayein:</i>"
                )
                markup = {
                    "inline_keyboard": [
                        [{"text": "🔴 Confirm Exit / Trade Closed", "callback_data": f"exit_confirm:{trade_id}"}],
                        [{"text": "🔄 Refresh Live P&L", "callback_data": f"refresh_pos:{trade_id}"}]
                    ]
                }
                send_telegram_message(bot_token, chat_id, t1_msg, parse_mode="HTML", reply_markup=markup)

            # 1B. Trailing SL / Cost-to-Cost Risk-Free Alert (50%+ Progress to Target 1)
            if chk.get('trailing_cost_to_cost') and not pos.get('trailing_alerted') and not is_t1:
                pos['trailing_alerted'] = True
                save_user_positions()
                prog_pct = chk.get('progress_pct', 50.0)
                trail_msg = (
                    f"🛡️ <b>TRAILING SL ALERT: MOVE TO COST-TO-COST!</b> 🛡️\n\n"
                    f"• <b>Commodity   :</b> <b>{comm_name}</b> ({direction})\n"
                    f"• <b>Entry Price :</b> ₹{entry:,.2f}\n"
                    f"• <b>Target 1    :</b> ₹{t1:,.2f} (<b>{prog_pct:.0f}% Completed!</b>)\n"
                    f"• <b>Current CMP :</b> <b>₹{cmp:,.2f}</b>\n"
                    f"• <b>Running P&L :</b> <b>{pnl_str} / Lot</b> 🟢\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔒 <b>CAPITAL PROTECTION RULE:</b>\n"
                    f"Trade 50%+ Target ki taraf move kar chuka hai! Apna Stop-Loss turant initial level (₹{sl:,.2f}) se hata kar <b>Entry Cost (₹{entry:,.2f})</b> par shift kar dein.\n\n"
                    f"✨ <i>Ab ye trade <b>100% Zero-Risk (Cost-to-Cost)</b> ho chuki hai — market reverse bhi ho to 1 rupee ka loss nahi hoga!</i>"
                )
                markup = {
                    "inline_keyboard": [
                        [{"text": "🔄 Refresh Live P&L", "callback_data": f"refresh_pos:{trade_id}"}],
                        [{"text": "🔴 Exit Trade Now", "callback_data": f"exit_confirm:{trade_id}"}]
                    ]
                }
                send_telegram_message(bot_token, chat_id, trail_msg, parse_mode="HTML", reply_markup=markup)

            # 2. Stop-Loss Triggered Alert
            if is_sl and not pos.get('sl_alerted'):
                pos['sl_alerted'] = True
                save_user_positions()
                sl_msg = (
                    f"🛑 <b>STOP-LOSS TRIGGERED! EXIT NOW!</b>\n\n"
                    f"• <b>Commodity :</b> <b>{comm_name}</b>\n"
                    f"• <b>Direction :</b> <b>{direction}</b>\n"
                    f"• <b>SL Level  :</b> ₹{sl:,.2f} touched (CMP: ₹{cmp:,.2f})\n"
                    f"• <b>Risk/Loss :</b> {pnl_str} per Lot\n\n"
                    f"⚠️ <i>Capital protect karne ke liye turant position close karein.</i>"
                )
                markup = {
                    "inline_keyboard": [
                        [{"text": "🔴 Confirm Exit / Trade Closed", "callback_data": f"exit_confirm:{trade_id}"}]
                    ]
                }
                send_telegram_message(bot_token, chat_id, sl_msg, parse_mode="HTML", reply_markup=markup)

            # 3. INSTANT TREND REVERSAL / CHoCH ALERT
            if reversal_flag and not pos.get('reversal_alerted'):
                pos['reversal_alerted'] = True
                save_user_positions()
                rev_msg = (
                    f"🚨 <b>URGENT SMC EXIT ALERT! TREND REVERSED!</b> 🚨\n\n"
                    f"• <b>Commodity   :</b> <b>{comm_name}</b>\n"
                    f"• <b>Your Position :</b> <b>{direction} @ ₹{entry:,.2f}</b>\n"
                    f"• <b>Current CMP   :</b> <b>₹{cmp:,.2f}</b>\n"
                    f"• <b>Live P&L     :</b> <b>{pnl_str}</b>\n"
                    f"• <b>Reversal Signal:</b> {reason}\n"
                    f"• <b>Action Required:</b> <b>{action_required}</b>\n\n"
                    f"⚠️ <i>Institutional Change of Character (CHoCH) breakdown detected! Institutional liquidity opposite direction me shift ho chuki hai. Capital bachane ke liye turant exit karein.</i>\n\n"
                    f"👉 <i>Exit karte hi niche button dabayein taaki bot wapas multi-commodity SMC scanning shuru kar sake:</i>"
                )
                markup = {
                    "inline_keyboard": [
                        [{"text": "🔴 Confirm Exit / Trade Closed", "callback_data": f"exit_confirm:{trade_id}"}],
                        [{"text": "🔄 Refresh Live P&L", "callback_data": f"refresh_pos:{trade_id}"}]
                    ]
                }
                send_telegram_message(bot_token, chat_id, rev_msg, parse_mode="HTML", reply_markup=markup)

        # Active trade ongoing -> Halt general scanning across other commodities
        return

    # Check Daily Max Loss Kill-Switch before generating new alerts
    if check_daily_kill_switch(bot_token):
        return

    # ─────────────────────────────────────────────────────────────────────────────
    # STEP 2: ONLY SMART MONEY CONCEPTS (SMC) INSTITUTIONAL SCANNING
    # Legacy indicator scanning is removed. The engine scans strictly for:
    # 1. Liquidity Sweeps (BSL/SSL Stop-Hunts)
    # 2. Demand/Supply Order Blocks (OB) + Fair Value Gaps (FVG)
    # 3. Mandatory Real-Time News Confluence Filter
    # ─────────────────────────────────────────────────────────────────────────────
    active_watchlist = get_active_watchlist()
    commodities_order = ["silver", "crude", "gold", "natgas", "aluminium", "zinc", "nickel", "electricity"]

    for slug in commodities_order:
        if slug not in active_watchlist:
            continue

        try:
            smc_res = analyze_smc_setup(slug, force_tip=False)
            if smc_res and smc_res.get('has_setup') and 'LIQUIDITY SWEEP' in smc_res.get('setup_type', ''):
                mtf = smc_res.get('mtf_res', {})
                if mtf and mtf.get('is_sharp_volatility'):
                    continue
                is_buy = "BUY" in smc_res.get('direction', 'BUY')
                if is_buy and not mtf.get('is_safe_buy', True):
                    continue
                if not is_buy and not mtf.get('is_safe_sell', True):
                    continue

                comm_name = smc_res.get('commodity', slug.upper())
                last_smc_time = alert_cooldowns.get(comm_name, 0)
                if force or (now_ts - last_smc_time) >= (45 * 60):
                    alert_cooldowns[comm_name] = now_ts
                    smc_trade_id = f"SMC_{slug[:3].upper()}_{int(now_ts)}"

                    smc_markup = {
                        "inline_keyboard": [
                            [
                                {"text": "🚀 Entry Taken (Track Live)", "callback_data": f"take:{smc_trade_id}"},
                                {"text": "⏭️ Skip Setup", "callback_data": f"skip:{smc_trade_id}"}
                            ]
                        ]
                    }

                    smc_trade_data = {
                        'trade_id': smc_trade_id,
                        'name': comm_name,
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
                        'prob_pct': smc_res.get('prob_pct', 85),
                        'reasons_str': smc_res['reasons'][0] if smc_res.get('reasons') else smc_res['setup_type'],
                        'status': 'ACTIVE',
                        'user_taken': False,
                        'comm_key': slug
                    }
                    active_tracked_trades[comm_name] = smc_trade_data
                    save_audit_trade(smc_trade_data)

                    print(f"[!] 🏛️ SMC Institutional Alert Triggered for {comm_name}: {smc_res['setup_type']}")
                    broadcast_alert(bot_token, smc_res['card_text'], reply_markup=smc_markup)
        except Exception as e:
            print(f"[-] SMC scan error on {slug}: {e}")



def background_scanner_loop(bot_token):
    global eod_report_sent_date, eod_hold_advisory_sent_date
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

            # 2. Check high-impact economic calendar events (EIA Crude, US NFP, US CPI 15-min pre-warnings)
            try:
                check_economic_calendar_events(bot_token)
            except Exception as e:
                print(f"[-] check_economic_calendar_events error: {e}")

            ist = timezone(timedelta(hours=5, minutes=30))
            now_ist = datetime.now(ist)
            today_str = now_ist.strftime("%Y-%m-%d")

            # 3. 15-Minute Pre-Close EOD Hold/Exit Advisory at 23:40 IST (Market Closes at 23:55)
            if now_ist.weekday() < 5 and now_ist.hour == 23 and 40 <= now_ist.minute < 55:
                if eod_hold_advisory_sent_date != today_str:
                    eod_hold_advisory_sent_date = today_str
                    try:
                        print(f"[+] Broadcasting 15-Minute Pre-Close EOD Hold/Exit Advisory for {today_str}...")
                        broadcast_eod_hold_advisory(bot_token)
                    except Exception as e:
                        print(f"[-] broadcast_eod_hold_advisory error: {e}")

            # 4. Run market scanner cycle
            run_scanner_cycle(bot_token)

            # Auto Good Morning Broadcast at 09:00 AM IST on weekdays (Market Open)
            if now_ist.weekday() < 5 and now_ist.hour == 9 and 0 <= now_ist.minute <= 5:
                broadcast_good_morning(bot_token, force=False)

            # Auto EOD Broadcast & Good Night at 23:55 IST (Right before Midnight Market Close)
            if now_ist.weekday() < 5 and now_ist.hour == 23 and now_ist.minute >= 55:
                if eod_report_sent_date != today_str:
                    eod_report_sent_date = today_str
                    report = generate_eod_report(today_str)
                    print(f"[+] Broadcasting End-of-Day Audit Report for {today_str}...")
                    broadcast_alert(bot_token, report)
                broadcast_good_night(bot_token, force=False, reason="session_close")

        except Exception as e:
            print(f"[-] Scanner loop error: {e}")

        # Dynamic scan speed from DB (default 45s, min 20s to prevent rate limits)
        try:
            interval_sec = max(20, int(get_db_config("scan_interval", "45")))
        except Exception:
            interval_sec = 45
        time.sleep(interval_sec)


# 6B. Automatic Real-Time News & Macro Impact Broadcaster (08:00 AM - 12:00 AM)
def format_breaking_news_alert(article, impact, usd_trend=None):
    ist = timezone(timedelta(hours=5, minutes=30))
    time_str = datetime.now(ist).strftime("%I:%M %p")
    card = [
        "🚨 <b>BREAKING NEWS & MCX PRICE IMPACT ALERT</b> 🚨",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"📰 <b>Headline :</b> <b>{html.escape(article['title'])}</b>",
        f"🏢 <b>Source   :</b> <i>{html.escape(article.get('source', 'Financial Wire'))}</i>  |  ⏰ <code>{time_str} IST</code>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🎯 <b>COMMODITY IMPACT :</b> <b>{impact['commodity']}</b>",
        f"🧭 <b>PRICE DIRECTION  :</b> <b>{impact['direction']}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🧐 <b>MARKET MEANING:</b>\n{impact['meaning']}\n",
        f"📈 <b>MCX PRICE EFFECT:</b>\n{impact['price_effect']}\n",
        f"💰 <b>EXPECTED MOVE:</b>\n{impact['expected_move']}\n",
        f"💡 <b>ACTION PLAN:</b>\n{impact['strategy']}",
        "━━━━━━━━━━━━━━━━━━━━━━"
    ]
    if usd_trend:
        card.append(f"{usd_trend.get('short_badge', '')}")
        card.append("━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(card)


def background_news_monitor(bot_token, news_token=None):
    """
    Automatic Background News & Macro Impact Monitor:
    - Runs from 08:00 AM to 12:00 AM IST.
    - Monitors real-time financial RSS news every 180 seconds for Gold, Silver, Crude Oil, NatGas & Macro.
    - Automatically analyzes price impact and broadcasts instant alerts to all subscribers.
    """
    print("🚀 Automatic Real-Time News Monitor Started (8:00 AM - 12:00 AM IST)...")
    seen_file = os.path.join(BASE_DIR, "seen_news_headlines.json")
    seen_titles = set()
    if os.path.exists(seen_file):
        try:
            with open(seen_file, "r", encoding="utf-8") as f:
                seen_titles = set(json.load(f))
        except Exception:
            seen_titles = set()

    is_initial_seed = (len(seen_titles) == 0)
    time.sleep(15)  # initial boot delay

    while True:
        try:
            ist = timezone(timedelta(hours=5, minutes=30))
            now_ist = datetime.now(ist)
            # Active window: 8:00 AM to 12:00 AM IST (hour 8 to 23)
            in_window = (8 <= now_ist.hour < 24)

            if in_window:
                articles = fetch_news_feed("all")
                new_alerts = []

                for art in articles:
                    title = art.get('title', '').strip()
                    if not title or title in seen_titles:
                        continue

                    if is_initial_seed:
                        seen_titles.add(title)
                        continue

                    seen_titles.add(title)
                    impact = analyze_headline_price_impact(title, art.get('comm_type'))
                    if impact.get('sentiment_score', 0) != 0 or "RANGE-BOUND" not in impact.get('direction', ''):
                        new_alerts.append((art, impact))

                if is_initial_seed:
                    is_initial_seed = False
                    try:
                        with open(seen_file, "w", encoding="utf-8") as f:
                            json.dump(list(seen_titles)[-300:], f)
                    except Exception:
                        pass

                if new_alerts:
                    usd_trend = get_usd_trend_for_commodities()
                    for art, impact in new_alerts[:2]:
                        alert_card = format_breaking_news_alert(art, impact, usd_trend)
                        # STRICT USER RULE: News alerts sent ONLY to dedicated News Bot, NOT to Trading Bot
                        if news_token:
                            try:
                                from news_bot import news_subscribers, send_message as send_news_msg
                                for cid in list(news_subscribers):
                                    send_news_msg(news_token, cid, alert_card)
                                print(f"[!] 📰 Breaking News sent to News Bot subscribers: {art['title'][:40]}...")
                            except Exception as e:
                                print(f"[-] News bot broadcast error: {e}")
                        time.sleep(1.0)

                    try:
                        with open(seen_file, "w", encoding="utf-8") as f:
                            json.dump(list(seen_titles)[-300:], f)
                    except Exception:
                        pass

        except Exception as e:
            print(f"[-] News monitor loop error: {e}")

        time.sleep(180)


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

        # Determine commodity key
        comm_key = found.get('comm_key', '') if found else ''
        if not comm_key:
            for k in COMMODITY_SPECS:
                if k.upper() in (found.get('name', '') if found else '').upper() or k[:3].upper() in trade_id.upper():
                    comm_key = k
                    break
        if not comm_key:
            comm_key = 'crude'

        # LOCK DEDICATED POSITION TRACKING
        user_active_positions[str(chat_id)] = {
            'trade_id': trade_id,
            'name': found.get("name", comm_key.upper()) if found else comm_key.upper(),
            'comm_key': comm_key,
            'direction': found.get("direction", "BUY") if found else "BUY",
            'entry': found.get("entry", 0.0) if found else 0.0,
            't1': found.get("t1", 0.0) if found else 0.0,
            'sl': found.get("sl", 0.0) if found else 0.0,
            't1_profit': found.get("t1_profit", 1500.0) if found else 1500.0,
            'sl_risk': found.get("sl_risk", 700.0) if found else 700.0,
            'time': time.time(),
            't1_alerted': False,
            'sl_alerted': False,
            'reversal_alerted': False
        }
        save_user_positions()
        user_awaiting_price_entry[str(chat_id)] = trade_id

        answer_callback_query(bot_token, cq_id, text="🚀 Position Locked! Dedicated Tracking Active.", show_alert=True)

        comm_name = user_active_positions[str(chat_id)]['name']
        entry_p = user_active_positions[str(chat_id)]['entry']
        t1_p = user_active_positions[str(chat_id)]['t1']
        sl_p = user_active_positions[str(chat_id)]['sl']
        profit = user_active_positions[str(chat_id)]['t1_profit']
        direction = user_active_positions[str(chat_id)]['direction']

        conf_msg = (
            f"🚀 <b>POSITION ENTERED & DEDICATED TRACKING LOCKED!</b>\n\n"
            f"• <b>Commodity :</b> <b>{comm_name}</b>\n"
            f"• <b>Direction :</b> <b>{direction}</b>\n"
            f"• <b>Alert CMP :</b> ₹{entry_p:,.2f}\n"
            f"• <b>Target 1  :</b> ₹{t1_p:,.2f} (<b>+₹{profit:,.0f} Profit / Lot</b>)\n"
            f"• <b>Stop-Loss :</b> ₹{sl_p:,.2f}\n\n"
            f"❓ <b>AAPNE KIS EXACT PRICE PAR ENTRY LI HAI?</b>\n"
            f"• <i>Agar aapne isi alert CMP (₹{entry_p:,.2f}) par buy/sell kiya hai, to niche <b>[✅ Confirm Alert Price]</b> dabayein.</i>\n"
            f"• <i>Agar aapka fill price thoda alag hai, to <b>direct chat me sirf price type karke bhej dein</b> (jaise: <code>{entry_p}</code> ya <code>/entry {entry_p}</code>).</i>\n\n"
            f"🔒 <b>DEDICATED MONITORING MODE ACTIVE:</b>\n"
            f"<i>Baki sabhi commodities ka auto-scan PAUSE ho chuka hai. Bot 100% focus is trade ke live trend aur price action par kar raha hai.</i>\n\n"
            f"⚡ <b>Instant Trend Reversal Protection:</b>\n"
            f"<i>Agar institutional Change of Character (CHoCH) ya trend badalta hai, to TURANT URGENT EXIT MSG bhej kar alert kiya jayega!</i>\n\n"
            f"👉 <i>Trade exit karne ke baad niche diya gaya button dabayein taaki bot wapas SMC scanning shuru kare:</i>"
        )
        markup = {
            "inline_keyboard": [
                [{"text": f"✅ Confirm Alert Price (₹{entry_p:,.2f})", "callback_data": f"confirm_entry_cmp:{trade_id}"}],
                [{"text": "🔴 Confirm Exit / Trade Closed", "callback_data": f"exit_confirm:{trade_id}"}],
                [{"text": "🔄 Refresh Live P&L", "callback_data": f"refresh_pos:{trade_id}"}]
            ]
        }
        send_telegram_message(bot_token, chat_id, conf_msg, parse_mode="HTML", reply_markup=markup)

    elif data.startswith("confirm_entry_cmp:"):
        trade_id = data.split(":", 1)[1]
        user_awaiting_price_entry.pop(str(chat_id), None)
        pos = user_active_positions.get(str(chat_id))
        entry_val = pos['entry'] if pos else 0.0
        answer_callback_query(bot_token, cq_id, text=f"✅ Entry Price Confirmed: ₹{entry_val:,.2f}", show_alert=True)
        if pos:
            ack_msg = (
                f"✅ <b>ENTRY PRICE CONFIRMED: ₹{pos['entry']:,.2f}</b>\n\n"
                f"• <b>Position  :</b> <b>{pos.get('name', '')} ({pos.get('direction', '')})</b>\n"
                f"• <b>Target 1   :</b> ₹{pos.get('t1', 0.0):,.2f} (+₹{pos.get('t1_profit', 0.0):,.0f})\n"
                f"• <b>Stop-Loss  :</b> ₹{pos.get('sl', 0.0):,.2f} (-₹{pos.get('sl_risk', 0.0):,.0f})\n\n"
                f"🔒 <i>Dedicated tracking active! Har moment live P&L aur trend reversal is exact price se calculate hoga.</i>"
            )
            markup = {
                "inline_keyboard": [
                    [{"text": "🔴 Confirm Exit / Trade Closed", "callback_data": f"exit_confirm:{pos['trade_id']}"}],
                    [{"text": "🔄 Refresh Live P&L", "callback_data": f"refresh_pos:{pos['trade_id']}"}]
                ]
            }
            send_telegram_message(bot_token, chat_id, ack_msg, parse_mode="HTML", reply_markup=markup)

    elif data.startswith("exit_confirm:") or data == "exit_current":
        pos = user_active_positions.pop(str(chat_id), None)
        user_awaiting_price_entry.pop(str(chat_id), None)
        save_user_positions()
        answer_callback_query(bot_token, cq_id, text="✅ Exit Confirmed! Trade closed.", show_alert=True)
        comm_title = pos.get('name', 'Trade') if pos else "Aapka Trade"
        exit_msg = (
            f"✅ <b>EXIT CONFIRMED! TRADE CLOSED!</b>\n\n"
            f"• <b>Position :</b> {comm_title} Closed.\n"
            f"• <b>Dedicated Tracking :</b> DEACTIVATED.\n\n"
            f"🏛️ <b>Multi-Commodity SMC Auto-Scanner resumed!</b>\n"
            f"Market me agla institutional setup milte hi aapko instant alert mil jayega."
        )
        send_telegram_message(bot_token, chat_id, exit_msg, parse_mode="HTML", reply_markup=get_keyboard())

    elif data.startswith("refresh_pos:"):
        pos = user_active_positions.get(str(chat_id))
        if pos:
            chk = check_active_trade_reversal(
                pos['comm_key'], pos['direction'], pos['entry'], pos['sl'], pos['t1']
            )
            cmp = chk.get('cmp', pos['entry'])
            pnl_inr = chk.get('pnl_inr', 0.0)
            act = chk.get('action_required', 'HOLD POSITION')
            pnl_sign = "+" if pnl_inr >= 0 else "-"
            alert_text = f"CMP: ₹{cmp:,.2f} | P&L: {pnl_sign}₹{abs(pnl_inr):,.0f} | {act}"
            answer_callback_query(bot_token, cq_id, text=alert_text, show_alert=True)
        else:
            answer_callback_query(bot_token, cq_id, text="Koi active trade nahi mila.", show_alert=False)

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
        cur_sec = get_db_config("scan_interval", "45")
        cur_loss_limit = float(get_db_config("max_daily_loss", "5000.0"))
        welcome_msg = (
            f"👋 <b>Namaste {user_name}!</b>\n\n"
            f"🎯 <b>MCX Futures Institutional SMC Trading & Position Tracker Bot</b>\n\n"
            f"🏛️ <b>1. PURE SMC INSTITUTIONAL SCANNER:</b> ACTIVE ✅\n"
            f"• <i>Strictly <b>Smart Money Concepts (SMC)</b> par scanning:</i>\n"
            f"  - <b>Liquidity Sweeps (BSL/SSL Stop-Hunts)</b>\n"
            f"  - <b>Demand & Supply Order Blocks (OB) + FVGs</b>\n"
            f"  - <b>Mandatory Real-Time Macro News Confluence Check</b>\n\n"
            f"🔒 <b>2. DEDICATED POSITION TRACKER & ADVANCED RISK SHIELD:</b>\n"
            f"• <b>Dedicated Tracking :</b> Setup accept karte hi baki scan pause, 100% focus aapke active trade par!\n"
            f"• <b>🛡️ Cost-to-Cost Trailing :</b> Target 1 ka 50% move aate hi SL cost par shift karne ka alert (100% Risk-Free)!\n"
            f"• <b>🎯 Multi-Lot Scaling :</b> Target 1 par 50% lots book karein aur balance runner lot ka SL Entry Cost par trail karein!\n"
            f"• <b>🛑 Max Loss Kill-Switch :</b> Daily ₹{cur_loss_limit:,.0f} limit hit hote hi alerts pause taaki revenge trading na ho!\n"
            f"• <b>⏰ 15-Min EOD Hold Advisory :</b> 23:40 IST par overnight hold vs square-off clear recommendation!\n"
            f"• <b>📅 US & EIA Event Warning :</b> EIA Crude, US NFP & CPI data se theek 15 min pehle advance alert!\n"
            f"• <b>📊 CMP Closing Valuation :</b> EOD audit me bina 'ACTIVE' likhe sabhi trades exact closing CMP se value hote hain!\n\n"
            f"⚙️ <b>Trading & Risk Commands:</b>\n"
            f"• <code>/entry [price]</code> : Set/calibrate your exact execution fill price\n"
            f"• <code>/position</code> : Check active trade CMP, live P&L & trend health\n"
            f"• <code>/exit</code> : Confirm trade exit & resume multi-commodity SMC scan\n"
            f"• <code>/maxloss [amount]</code> : Set daily loss kill-switch (Current: ₹{cur_loss_limit:,.0f})\n"
            f"• <code>/killswitch off</code> : Manually reset/override daily kill-switch\n"
            f"• <code>/calendar</code> : View US & EIA economic releases & warning schedule\n"
            f"• <code>/advisory</code> : Check 15-min pre-close overnight hold recommendation\n"
            f"• <code>/morning</code> / <code>/night</code> : Manual Good Morning / Good Night broadcast\n"
            f"• <code>/scan</code> : Run instant live SMC scan across watchlist\n"
            f"• <code>/watchlist</code> : Select commodities for SMC alerts\n"
            f"• <code>/report</code> : View daily closing audit & personal P&L scorecard\n\n"
            f"📊 <b>Instant Big Chart SMC Advisors:</b>\n"
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

    # DAILY MAX LOSS KILL-SWITCH CONFIGURATION
    if clean_text.startswith("/maxloss") or clean_text.startswith("/setloss") or clean_text.startswith("/losslimit"):
        parts = clean_text.split()
        if len(parts) > 1:
            raw_amt = parts[1].replace("₹", "").replace("rs", "").replace(",", "").strip()
            try:
                new_amt = float(raw_amt)
                if new_amt <= 0:
                    raise ValueError
                set_db_config("max_daily_loss", str(new_amt))
                msg = (
                    f"🛡️ <b>DAILY MAX LOSS KILL-SWITCH UPDATED!</b>\n\n"
                    f"• <b>New Loss Limit :</b> <b>₹{new_amt:,.0f}</b>\n\n"
                    f"💡 <i>Agar aaj ka total loss ₹{new_amt:,.0f} touch ho jata hai, to bot turant Kill-Switch active karke new setup alerts PAUSE kar dega taaki account protect rahe.</i>"
                )
            except Exception:
                msg = "⚠️ Format: <code>/maxloss 5000</code> ya <code>/maxloss 10000</code>"
        else:
            cur_limit = float(get_db_config("max_daily_loss", "5000.0"))
            active_date = get_db_config("kill_switch_active_date", "")
            ist = timezone(timedelta(hours=5, minutes=30))
            today_str = datetime.now(ist).strftime("%Y-%m-%d")
            status_str = "🛑 <b>ACTIVE (ALERTS PAUSED)</b>" if active_date == today_str else "🟢 <b>INACTIVE (NORMAL SCANNING)</b>"
            msg = (
                f"🛡️ <b>DAILY MAX LOSS KILL-SWITCH STATUS</b>\n\n"
                f"• <b>Configured Limit :</b> <b>₹{cur_limit:,.0f}</b>\n"
                f"• <b>Current State   :</b> {status_str}\n\n"
                f"Naya loss limit set karne ke liye type karein:\n"
                f"• <code>/maxloss 3000</code> (Conservative)\n"
                f"• <code>/maxloss 5000</code> (Standard Recommended)\n"
                f"• <code>/maxloss 10000</code> (Aggressive)\n\n"
                f"👉 <i>Kill-Switch manually reset karne ke liye:</i> <code>/killswitch off</code>"
            )
        send_telegram_message(bot_token, chat_id, msg, parse_mode="HTML", reply_markup=get_keyboard())
        return

    # KILL-SWITCH MANUAL OVERRIDE / RESET
    if clean_text in ["/killswitch", "/resetswitch", "/killswitch off", "/killswitch reset"] or clean_text.startswith("/killswitch"):
        ist = timezone(timedelta(hours=5, minutes=30))
        today_str = datetime.now(ist).strftime("%Y-%m-%d")
        if "off" in clean_text or "reset" in clean_text:
            set_db_config("kill_switch_active_date", "")
            msg = (
                "✅ <b>KILL-SWITCH RESET SUCCESSFUL!</b>\n\n"
                "• <b>Status :</b> <b>Normal Scanning Resumed 🟢</b>\n"
                "• <i>Auto-Scanner ab naye SMC A+ setups generate karne ke liye tayar hai. Safe trading!</i>"
            )
        else:
            cur_date = get_db_config("kill_switch_active_date", "")
            is_active = (cur_date == today_str)
            cur_limit = float(get_db_config("max_daily_loss", "5000.0"))
            state_str = "🛑 <b>ACTIVE (ALERTS PAUSED)</b>" if is_active else "🟢 <b>INACTIVE (SCANNING ACTIVE)</b>"
            msg = (
                f"🛡️ <b>KILL-SWITCH DASHBOARD</b>\n\n"
                f"• <b>Current State :</b> {state_str}\n"
                f"• <b>Daily Limit   :</b> ₹{cur_limit:,.0f}\n\n"
                f"👉 <i>Kill-switch deactivate karne ke liye:</i> <code>/killswitch off</code>"
            )
        send_telegram_message(bot_token, chat_id, msg, parse_mode="HTML", reply_markup=get_keyboard())
        return

    # US & EIA ECONOMIC CALENDAR DASHBOARD
    if clean_text in ["/calendar", "/events", "/economic", "/eia", "/nfp", "📅 economic calendar"]:
        cal_msg = (
            "📅 <b>US & EIA HIGH-IMPACT ECONOMIC CALENDAR</b> ⚠️\n\n"
            "Bot high-impact releases se theek <b>15 Minutes pehle advance warning alert</b> bhejta hai:\n\n"
            "🛢️ <b>1. EIA Crude Oil Stocks Report</b>\n"
            "• <b>Schedule :</b> Har Wednesday at <code>08:00 PM IST</code>\n"
            "• <b>Impact   :</b> Crude Oil & Natural Gas (±₹150-₹300 Move)\n"
            "• <b>Alert    :</b> <code>07:45 PM IST</code> par automated warning\n\n"
            "💼 <b>2. US Non-Farm Payrolls (NFP)</b>\n"
            "• <b>Schedule :</b> Month ka 1st Friday at <code>06:00 PM IST</code>\n"
            "• <b>Impact   :</b> Gold, Silver, DXY (Silver me ±₹1,500-₹3,000 Move)\n"
            "• <b>Alert    :</b> <code>05:45 PM IST</code> par automated warning\n\n"
            "📊 <b>3. US CPI Inflation Data</b>\n"
            "• <b>Schedule :</b> Monthly (10th-14th) at <code>06:00 PM IST</code>\n"
            "• <b>Impact   :</b> All Commodities & USD Index\n"
            "• <b>Alert    :</b> <code>05:45 PM IST</code> par automated warning\n\n"
            "🛡️ <b>Trading Protocol:</b> Data aane se 15 min pehle aggressive fresh trades hold karein aur running trades ka SL <b>Cost-to-Cost</b> trail karein!"
        )
        send_telegram_message(bot_token, chat_id, cal_msg, parse_mode="HTML", reply_markup=get_keyboard())
        return

    # 15-MIN PRE-CLOSE OVERNIGHT HOLD ADVISORY ON DEMAND
    if clean_text in ["/advisory", "/hold", "/eodadvisory", "⏰ 15-min hold advisory"]:
        send_telegram_message(bot_token, chat_id, "⏳ <i>15-Minute Pre-Close Hold vs Square-Off Advisory analyze ho rahi hai...</i>", parse_mode="HTML")
        broadcast_eod_hold_advisory(bot_token)
        return

    # GOOD MORNING & GOOD NIGHT MANUAL COMMANDS
    if clean_text in ["/morning", "/goodmorning", "/on", "good morning", "gm", "bot on"]:
        broadcast_good_morning(bot_token, force=True)
        send_telegram_message(bot_token, chat_id, "🌅 <i>Good Morning / Bot Online alert sabhi active traders ko broadcast ho gaya hai!</i>", parse_mode="HTML", reply_markup=get_keyboard())
        return

    if clean_text in ["/night", "/goodnight", "/off", "good night", "gn", "bot off"]:
        broadcast_good_night(bot_token, force=True, reason="session_close")
        send_telegram_message(bot_token, chat_id, "🌙 <i>Good Night / Bot Offline alert sabhi active traders ko broadcast ho gaya hai!</i>", parse_mode="HTML", reply_markup=get_keyboard())
        return

    # CUSTOM ENTRY / FILL PRICE CALIBRATION
    # Triggers on "/entry <price>", "/price <price>", or when user simply types a numeric price while awaiting price or holding active trade
    is_entry_cmd = clean_text.startswith("/entry") or clean_text.startswith("/price") or clean_text.startswith("entry ") or clean_text.startswith("price ")
    has_active_pos = str(chat_id) in user_active_positions
    is_awaiting = str(chat_id) in user_awaiting_price_entry

    custom_price = None
    if is_entry_cmd:
        raw_val = clean_text.replace("/entry", "").replace("/price", "").replace("entry", "").replace("price", "").replace("rs.", "").replace("rs", "").replace("₹", "").replace(",", "").strip()
        try:
            custom_price = float(raw_val)
        except ValueError:
            pass
    elif is_awaiting or has_active_pos:
        # Check if the whole message is a single number (e.g. 9528 or 9528.50 or 288.5)
        raw_val = clean_text.replace("rs.", "").replace("rs", "").replace("₹", "").replace(",", "").strip()
        try:
            custom_price = float(raw_val)
            if custom_price <= 0:
                custom_price = None
        except ValueError:
            pass

    if custom_price is not None:
        if has_active_pos:
            pos = user_active_positions[str(chat_id)]
            old_entry = pos['entry']
            direction = pos['direction'].upper()
            comm_key = pos['comm_key']

            old_sl = pos.get('sl', 0.0)
            old_t1 = pos.get('t1', 0.0)

            # Preserve structure risk & reward points
            if direction == "BUY":
                risk_pts = max(old_entry - old_sl, old_entry * 0.005) if (old_sl > 0 and old_entry > old_sl) else old_entry * 0.005
                reward_pts = max(old_t1 - old_entry, risk_pts * 2.0) if (old_t1 > 0 and old_t1 > old_entry) else risk_pts * 2.0
                new_sl = round(custom_price - risk_pts, 2)
                new_t1 = round(custom_price + reward_pts, 2)
            else: # SELL
                risk_pts = max(old_sl - old_entry, old_entry * 0.005) if (old_sl > 0 and old_entry > old_sl) else old_entry * 0.005
                reward_pts = max(old_entry - old_t1, risk_pts * 2.0) if (old_t1 > 0 and old_entry > old_t1) else risk_pts * 2.0
                new_sl = round(custom_price + risk_pts, 2)
                new_t1 = round(custom_price - reward_pts, 2)

            spec = COMMODITY_SPECS.get(comm_key, {})
            lot_size = spec.get('lot_size', 1.0)
            new_t1_profit = round(reward_pts * lot_size, 2)
            new_sl_risk = round(risk_pts * lot_size, 2)

            pos['entry'] = custom_price
            pos['sl'] = new_sl
            pos['t1'] = new_t1
            pos['t1_profit'] = new_t1_profit
            pos['sl_risk'] = new_sl_risk

            save_user_positions()
            user_awaiting_price_entry.pop(str(chat_id), None)

            p_conf = (
                f"🎯 <b>EXECUTION PRICE CALIBRATED TO ₹{custom_price:,.2f}!</b>\n\n"
                f"• <b>Commodity   :</b> <b>{pos.get('name', comm_key.upper())} ({direction})</b>\n"
                f"• <b>Aapka Entry :</b> <b>₹{custom_price:,.2f}</b> (Signal Alert CMP: ₹{old_entry:,.2f})\n"
                f"• <b>New Target 1:</b> <b>₹{new_t1:,.2f}</b> (<b>+₹{new_t1_profit:,.0f} / Lot</b>)\n"
                f"• <b>New Stop-Loss:</b> <b>₹{new_sl:,.2f}</b> (-₹{new_sl_risk:,.0f} / Lot)\n\n"
                f"🔒 <b>DEDICATED MONITORING ACTIVE:</b>\n"
                f"<i>Live P&L aur Trend Reversal ab aapke actual execution price ₹{custom_price:,.2f} ke mutabik monitor ho raha hai!</i>\n\n"
                f"👉 <i>Trade exit karne par niche button dabayein:</i>"
            )
            markup = {
                "inline_keyboard": [
                    [{"text": "🔴 Confirm Exit / Trade Closed", "callback_data": f"exit_confirm:{pos['trade_id']}"}],
                    [{"text": "🔄 Refresh Live P&L", "callback_data": f"refresh_pos:{pos['trade_id']}"}]
                ]
            }
            send_telegram_message(bot_token, chat_id, p_conf, parse_mode="HTML", reply_markup=markup)
            return
        elif is_entry_cmd:
            no_pos_msg = (
                "⚠️ <b>Abhi aapka koi active trade locked nahi hai!</b>\n\n"
                "• <i>Pehle kisi setup alert par <b>'🚀 Entry Taken'</b> dabayein.</i>\n"
                "• <i>Uske baad aap apna custom execution price enter kar sakte hain.</i>"
            )
            send_telegram_message(bot_token, chat_id, no_pos_msg, parse_mode="HTML", reply_markup=get_keyboard())
            return

    # ACTIVE POSITION & LIVE P&L TRACKING COMMAND
    if clean_text in ["/position", "/positions", "/trade", "/active", "🎯 active position & live p&l"]:
        load_user_positions()
        pos = user_active_positions.get(str(chat_id))
        if pos:
            chk = check_active_trade_reversal(
                pos['comm_key'], pos['direction'], pos['entry'], pos['sl'], pos['t1']
            )
            reversal_flag = chk.get('reversal_flag', False)
            reason = chk.get('reason', '')
            act = chk.get('action_required', 'HOLD POSITION')
            cmp = chk.get('cmp', pos['entry'])
            pnl_pts = chk.get('pnl_pts', 0.0)
            pnl_inr = chk.get('pnl_inr', 0.0)
            is_sl = chk.get('is_sl', False)
            is_t1 = chk.get('is_t1', False)

            pnl_sign = "+" if pnl_inr >= 0 else "-"
            pnl_str = f"{pnl_sign}₹{abs(pnl_inr):,.0f}"
            status_card = (
                f"🎯 <b>ACTIVE TRADE DEDICATED TRACKING</b>\n\n"
                f"• <b>Commodity   :</b> <b>{pos.get('name', pos['comm_key'].upper())}</b>\n"
                f"• <b>Position    :</b> <b>{pos['direction']}</b> @ ₹{pos['entry']:,.2f}\n"
                f"• <b>Current CMP :</b> <b>₹{cmp:,.2f}</b>\n"
                f"• <b>Target 1    :</b> ₹{pos['t1']:,.2f} (+₹{pos['t1_profit']:,.0f})\n"
                f"• <b>Stop-Loss   :</b> ₹{pos['sl']:,.2f} (-₹{pos['sl_risk']:,.0f})\n"
                f"• <b>Live P&L   :</b> <b>{pnl_str} per 1 Lot</b> 🔥\n"
                f"• <b>Trend Status:</b> <b>{act}</b>\n"
                f"• <b>Signal Note :</b> <i>{reason}</i>\n\n"
                f"🔒 <i>Multi-commodity auto-scan paused hai. Bot sirf is position ko monitor kar raha hai. Exit karne ke liye niche button dabayein:</i>"
            )
            markup = {
                "inline_keyboard": [
                    [{"text": "🔴 Confirm Exit / Trade Closed", "callback_data": f"exit_confirm:{pos['trade_id']}"}],
                    [{"text": "🔄 Refresh Live P&L", "callback_data": f"refresh_pos:{pos['trade_id']}"}]
                ]
            }
            send_telegram_message(bot_token, chat_id, status_card, parse_mode="HTML", reply_markup=markup)
        else:
            no_pos_msg = (
                "ℹ️ <b>Abhi aapka koi active trade locked nahi hai!</b>\n\n"
                "• <i>Bot multi-commodity SMC mode me background scan kar raha hai.</i>\n"
                "• <i>Jab bhi kisi setup alert par aap '🚀 Entry Taken' dabayenge, bot 100% dedicated monitoring mode me switch ho jayega!</i>"
            )
            send_telegram_message(bot_token, chat_id, no_pos_msg, parse_mode="HTML", reply_markup=get_keyboard())
        return

    # EXIT CONFIRMATION COMMAND
    if clean_text in ["/exit", "/close", "/exit_confirm", "🔴 confirm exit / close trade", "exit", "close"]:
        load_user_positions()
        pos = user_active_positions.pop(str(chat_id), None)
        user_awaiting_price_entry.pop(str(chat_id), None)
        save_user_positions()
        if pos:
            exit_msg = (
                f"✅ <b>EXIT CONFIRMED! TRADE CLOSED!</b>\n\n"
                f"• <b>Position :</b> {pos.get('name', 'Trade')} Closed.\n"
                f"• <b>Dedicated Tracking :</b> DEACTIVATED.\n\n"
                f"🏛️ <b>Multi-Commodity SMC Auto-Scanner resumed!</b>\n"
                f"Market me agla institutional setup bante hi aapko alert mil jayega."
            )
        else:
            exit_msg = (
                "ℹ️ <b>Koi active trade locked nahi tha.</b>\n\n"
                "🏛️ <i>SMC Institutional Auto-Scanner already active hai!</i>"
            )
        send_telegram_message(bot_token, chat_id, exit_msg, parse_mode="HTML", reply_markup=get_keyboard())
        return

    # MANUAL SMC SCAN COMMAND
    if clean_text in ["/scan", "/smcscan", "🔍 run live smc scan", "🔍 run live scan now"]:
        send_telegram_message(bot_token, chat_id, "🔍 <i>Sabhi commodities ka Institutional SMC (Liquidity Sweeps & Order Blocks) scan ho raha hai...</i>", parse_mode="HTML")
        run_scanner_cycle(bot_token, force=True)
        send_telegram_message(bot_token, chat_id, "✅ <i>SMC Scan complete! High-probability setup detect hone par card bhej diya gaya hai.</i>", parse_mode="HTML", reply_markup=get_keyboard())
        return

    # STATUS COMMAND (GENERAL OR PARTICULAR COMMODITY STATUS)
    if clean_text.startswith("/status") or clean_text == "📊 bot & alert status":
        parts = clean_text.split()
        if len(parts) > 1:
            req_comm = parts[1].strip()
            alias_map = {
                "ng": "natgas", "gas": "natgas", "naturalgas": "natgas",
                "elec": "electricity", "power": "electricity",
                "alum": "aluminium", "aluminum": "aluminium",
                "al": "aluminium", "zn": "zinc", "ni": "nickel"
            }
            resolved_slug = alias_map.get(req_comm, req_comm)
            if resolved_slug in COMMODITY_SPECS or resolved_slug in COMMODITY_ADVISORS:
                custom_cmp_val = extract_custom_cmp(clean_text)
                send_commodity_tip(bot_token, chat_id, resolved_slug, custom_cmp=custom_cmp_val)
                return

        # General Bot Status
        is_open = is_mcx_open()
        active_wl = get_active_watchlist()
        wl_str = ", ".join([s.capitalize() for s in active_wl])
        cur_int = get_db_config("scan_interval", "45")
        load_user_positions()
        user_pos = user_active_positions.get(str(chat_id))

        status_msg = (
            f"📊 <b>BOT & SMC SCANNER LIVE STATUS</b>\n\n"
            f"• <b>Operating Schedule :</b> ⏰ <b>08:00 AM - 12:00 AM IST</b>\n"
            f"• <b>Current Bot State  :</b> {'🟢 ACTIVE & SCANNING' if is_open else '🔴 CLOSED / SLEEP'}\n"
            f"• <b>Scanning Model     :</b> 🏛️ <b>ONLY Smart Money Concepts (SMC)</b>\n"
            f"• <b>News Isolation     :</b> 📰 News alerts sent strictly to News Bot\n"
            f"• <b>Dedicated Trade    :</b> {'🔒 ACTIVE (' + user_pos.get('name', '') + ')' if user_pos else '⚪ None (Scanning All)'}\n"
            f"• <b>Active Watchlist   :</b> {wl_str} ({len(active_wl)}/8 Active)\n"
            f"• <b>Auto-Scan Interval :</b> Every {cur_int} Seconds ⚡\n"
            f"• <b>Active Subscribers :</b> {len(active_subscribers)}\n"
        )
        if user_pos:
            status_msg += (
                f"\n🎯 <b>Your Active Position:</b>\n"
                f"• {user_pos.get('name')}: {user_pos.get('direction')} @ ₹{user_pos.get('entry'):,.2f} | T1: ₹{user_pos.get('t1'):,.2f}\n"
                f"<i>Type /position to check live P&L or /exit to close.</i>\n"
            )
        send_telegram_message(bot_token, chat_id, status_msg, parse_mode="HTML", reply_markup=get_keyboard())
        return

    # USD & DXY TREND RADAR FOR SILVER, GOLD & CRUDE OIL
    if clean_text in ["/usd", "/dxy", "/dollar", "💵 usd (dxy) trend radar", "💵 usd trend radar", "usd", "dxy"]:
        send_telegram_message(bot_token, chat_id, "⏳ <i>Live US Dollar Index (DXY) aur Silver, Gold, Crude correlation scan ho raha hai...</i>", parse_mode="HTML")
        report = format_usd_trend_report()
        send_telegram_message(bot_token, chat_id, report, parse_mode="HTML", reply_markup=get_keyboard())
        return

    # GLOBAL MACRO & COMMODITY NEWS PRICE IMPACT COMMAND
    if clean_text in ["/news", "/macro", "news", "macro", "📰 global news radar"]:
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
    global bot_running
    bot_running = True

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
            {"command": "morning", "description": "Good Morning / Bot Online Broadcast"},
            {"command": "night", "description": "Good Night / Bot Offline Broadcast"},
            {"command": "entry", "description": "Set Custom Fill / Execution Price for Active Trade"},
            {"command": "position", "description": "View Active Position CMP & Live P&L"},
            {"command": "exit", "description": "Confirm Trade Exit & Resume SMC Auto-Scan"},
            {"command": "usd", "description": "USD (DXY) Trend & Silver, Gold, Crude Impact"},
            {"command": "news", "description": "Global Macro & News Price Impact Analysis"},
            {"command": "watchlist", "description": "Select Commodities for Auto-Scan & SMC Alerts"},
            {"command": "maxloss", "description": "Configure Daily Max Loss Kill-Switch (e.g. 5000)"},
            {"command": "killswitch", "description": "Check or Reset Kill-Switch (/killswitch off)"},
            {"command": "calendar", "description": "US & EIA High-Impact Economic Calendar Warnings"},
            {"command": "advisory", "description": "15-Min Pre-Close Overnight Hold vs Square-Off Advisory"},
            {"command": "export", "description": "Weekly/Monthly Excel (.csv) Audit Report"},
            {"command": "smc", "description": "Smart Money Concepts Institutional Analysis"},
            {"command": "btst", "description": "On-Demand BTST / STBT & Swing Analysis"},
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

    # Broadcast Good Morning if starting up within weekday market open hours (08:30 - 12:00 IST)
    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist)
    if now_ist.weekday() < 5 and 8 <= now_ist.hour < 12:
        try:
            broadcast_good_morning(token, force=False)
        except Exception as e:
            print(f"[-] Startup Good Morning broadcast notice: {e}")

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

    # Start Background Real-time News Monitor & Alert Broadcaster Thread (08:00 AM - 12:00 AM)
    news_monitor_thread = threading.Thread(target=background_news_monitor, args=(token, news_tok), daemon=True)
    news_monitor_thread.start()

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
