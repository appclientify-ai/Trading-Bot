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
from mcx_feed import get_live_mcx_price

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SUBSCRIBERS_FILE = os.path.join(BASE_DIR, "subscribers.json")
CONFIG_FILE = os.path.join(BASE_DIR, "scanner_config.json")

# Scanner Memory & State
active_subscribers = set()
active_tracked_trades = {}
alert_cooldowns = {}

# 1. Cloud Health Server (Render.com compatibility)
def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    class HealthHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"MCX Futures Trading & Auto-Scanner Bot is LIVE 24/7!")

        def log_message(self, format, *args):
            pass

    try:
        with socketserver.TCPServer(("", port), HealthHandler) as httpd:
            print(f"[+] Cloud Health Server active on port {port} (Render ready)")
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
    if os.path.exists(SUBSCRIBERS_FILE):
        try:
            with open(SUBSCRIBERS_FILE, "r") as f:
                data = json.load(f)
                active_subscribers = set(data)
        except Exception:
            active_subscribers = set()


def save_subscriber(chat_id):
    global active_subscribers
    if chat_id not in active_subscribers:
        active_subscribers.add(chat_id)
        try:
            with open(SUBSCRIBERS_FILE, "w") as f:
                json.dump(list(active_subscribers), f)
        except Exception:
            pass


def get_min_profit():
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
        with open(CONFIG_FILE, "w") as f:
            json.dump({"min_profit": float(val)}, f)
        return True
    except Exception:
        return False


# 3. MCX Market Hours Check (IST: UTC + 5:30)
def is_mcx_open():
    ist = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist)
    # Mon-Fri: 09:00 AM to 11:35 PM
    if now.weekday() >= 5: # Saturday or Sunday
        return False
    hour, minute = now.hour, now.minute
    if 9 <= hour < 23:
        return True
    if hour == 23 and minute <= 35:
        return True
    return False


# 4. Telegram Messaging
def send_telegram_message(bot_token, chat_id, text, parse_mode="HTML", reply_markup=None):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        resp = requests.post(url, json=payload, timeout=10)
        res_json = resp.json()
        if not res_json.get("ok") and parse_mode:
            payload.pop("parse_mode", None)
            fb = requests.post(url, json=payload, timeout=10)
            return fb.json()
        return res_json
    except Exception as e:
        print(f"[-] Telegram message error: {e}")
        return None


def broadcast_alert(bot_token, text):
    """Sends high priority alert to all active traders"""
    load_subscribers()
    for cid in active_subscribers:
        send_telegram_message(bot_token, cid, text, parse_mode="HTML")


def get_keyboard():
    return {
        "keyboard": [
            [{"text": "🪙 Silver Micro (1 Kg)"}, {"text": "🥇 Gold Ten (10g)"}],
            [{"text": "🛢️ Crude Oil Mini (10 BBL)"}, {"text": "🔥 Nat Gas Mini (250)"}],
            [{"text": "⚙️ Aluminium Mini (1 MT)"}, {"text": "🔩 Zinc Mini (1 MT)"}],
            [{"text": "🪙 Nickel (250 Kg)"}, {"text": "⚡ Electricity (50 MWh)"}],
            [{"text": "🔍 Run Live Scan Now"}, {"text": "📊 Bot & Alert Status"}],
            [{"text": "❓ Help & Commands"}]
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


# 5. Background Auto-Scanner Engine (Every 3 minutes)
def run_scanner_cycle(bot_token, force=False):
    global active_tracked_trades, alert_cooldowns
    min_profit = get_min_profit()
    now_ts = time.time()

    if not force and not is_mcx_open():
        return

    advisors = [
        ("Silver Micro (1 Kg)", analyze_silver_trade),
        ("Gold Ten (10 Grams)", analyze_gold_trade),
        ("Crude Oil Mini (10 BBL)", analyze_crude_trade),
        ("Natural Gas Mini (250 mmBtu)", analyze_natgas_trade),
        ("Aluminium Mini (1 MT)", analyze_aluminium_trade),
        ("Zinc Mini (1 MT)", analyze_zinc_trade),
        ("Nickel (250 Kg)", analyze_nickel_trade),
        ("Electricity (50 MWh)", analyze_electricity_trade)
    ]

    for name, advisor_fn in advisors:
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
                if not trade.get('t1_hit'):
                    if (trade['direction'] == "BUY" and cmp >= trade['t1']) or (trade['direction'] == "SELL" and cmp <= trade['t1']):
                        trade['t1_hit'] = True
                        msg = (
                            f"🎉 <b>BINGO! TARGET 1 HIT!</b> 🎉\n\n"
                            f"• Commodity : <b>{name}</b>\n"
                            f"• Direction : <b>{trade['direction']}</b>\n"
                            f"• Target 1  : <b>₹{trade['t1']:,.1f} REACHED!</b>\n"
                            f"• Profit    : <b>+₹{trade['t1_profit']:,.0f} Booked per 1 Lot!</b> 🔥\n\n"
                            f"📋 <b>Next Action:</b> 50% quantity book karein aur Stop-Loss ko turant "
                            f"Entry Cost (₹{trade['entry']:,.1f}) par shift kar dein (Zero-Risk Trade)!"
                        )
                        broadcast_alert(bot_token, msg)

                if not trade.get('sl_hit'):
                    if (trade['direction'] == "BUY" and cmp <= trade['sl']) or (trade['direction'] == "SELL" and cmp >= trade['sl']):
                        trade['sl_hit'] = True
                        msg = (
                            f"🛑 <b>STOP-LOSS TRIGGERED!</b>\n\n"
                            f"• Commodity : <b>{name}</b>\n"
                            f"• Direction : <b>{trade['direction']}</b>\n"
                            f"• SL Level  : ₹{trade['sl']:,.1f} touched.\n"
                            f"Exit trade to protect capital."
                        )
                        broadcast_alert(bot_token, msg)
                        del active_tracked_trades[name]
                        continue

            # 2. Check Cooldown
            last_alert_time = alert_cooldowns.get(name, 0)
            if (now_ts - last_alert_time) < (45 * 60):  # 45 mins cooldown
                continue

            # 3. High Profit & High Probability Filter
            if prob_pct >= 75 and t1_profit >= min_profit:
                alert_cooldowns[name] = now_ts
                active_tracked_trades[name] = {
                    'direction': direction,
                    'entry': cmp,
                    't1': t1,
                    'sl': sl,
                    't1_profit': t1_profit,
                    'time': now_ts,
                    't1_hit': False,
                    'sl_hit': False
                }

                alert_msg = (
                    f"🚨 <b>HIGH-PROFIT AUTO SIGNAL ALERT!</b> 🚨\n\n"
                    f"• <b>Commodity  :</b> {name}\n"
                    f"• <b>Direction  :</b> <b>{direction}</b>\n"
                    f"• <b>Entry Zone :</b> ₹{cmp:,.1f}\n"
                    f"• <b>Win Chance :</b> <b>{prob_pct}% [HIGH CONFIDENCE]</b>\n"
                    f"-----------------------------------------\n"
                    f"🛑 <b>Strict SL :</b> ₹{sl:,.1f} (Risk: ₹{res['sl_risk_inr']:,.0f})\n"
                    f"🎯 <b>Target 1  :</b> ₹{t1:,.1f} (<b>+₹{t1_profit:,.0f} Profit / 1 Lot</b>) 🔥\n"
                    f"🎯 <b>Target 2  :</b> ₹{res['t2_price']:,.1f} (+₹{t1_profit * 2:,.0f})\n"
                    f"🎯 <b>Target 3  :</b> ₹{res['t3_price']:,.1f} (+₹{t1_profit * 3:,.0f})\n"
                    f"-----------------------------------------\n"
                    f"💡 <b>Reason:</b> {res['reasons'][0] if res.get('reasons') else 'High Confluence Trend'}\n"
                    f"📌 <i>Target 1 hit hone par automatic trailing update aayega!</i>"
                )
                print(f"[!] Auto-Scanner Triggered Alert for {name}: +₹{t1_profit} Target 1 Profit")
                broadcast_alert(bot_token, alert_msg)

        except Exception as e:
            print(f"[-] Scanner error on {name}: {e}")


def background_scanner_loop(bot_token):
    time.sleep(10)  # Initial warm up
    print("🚀 Background Auto-Scanner Engine Started (Scanning every 3 mins for ₹1,500+ A+ Setups)...")
    while True:
        try:
            run_scanner_cycle(bot_token)
        except Exception as e:
            print(f"[-] Scanner loop error: {e}")
        time.sleep(180)  # Scan every 3 minutes


# 6. Incoming Message Dispatcher
def handle_message(bot_token, chat_id, user_text, user_name):
    save_subscriber(chat_id)
    clean_text = user_text.strip().lower()

    if clean_text in ["/start", "/help", "❓ help & commands"]:
        min_p = get_min_profit()
        welcome_msg = (
            f"👋 <b>Namaste {user_name}!</b>\n\n"
            f"🎯 <b>MCX Futures Live Advisory & Auto-Scanner Bot</b> mein aapka swagat hai.\n\n"
            f"🔔 <b>Automated Background Scanner:</b> ACTIVE ✅\n"
            f"• <i>Jab bhi kisi commodity me Target 1 ka profit <b>₹{min_p:,.0f}+</b> aur Win Chance <b>75%+</b> hoga, aapko bina button dabaye automatic alert aayega!</i>\n\n"
            f"⚙️ <b>Commands & Tools:</b>\n"
            f"• <code>/setprofit 1500</code> : Minimum Target 1 profit filter set karein\n"
            f"• <code>/scan</code> : Abhi turant live market scan run karein\n"
            f"• <code>/status</code> : Scanner aur active alerts ka status dekhein\n\n"
            f"📊 <b>Instant Commodity Analysis:</b>\n"
            f"• <code>/silver</code> (1 Kg) | <code>/gold</code> (10g)\n"
            f"• <code>/crude</code> (10 BBL) | <code>/natgas</code> (250 mmBtu)\n"
            f"• <code>/aluminium</code> (1 MT) | <code>/zinc</code> (1 MT)\n"
            f"• <code>/nickel</code> (250 Kg) | <code>/electricity</code> (50 MWh)\n\n"
            f"Niche quick buttons se tap karein 👇"
        )
        send_telegram_message(bot_token, chat_id, welcome_msg, parse_mode="HTML", reply_markup=get_keyboard())
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

    # MANUAL SCAN COMMAND
    if clean_text in ["/scan", "🔍 run live scan now"]:
        send_telegram_message(bot_token, chat_id, "🔍 <i>Sabhi 8 commodities ka live chart scan ho raha hai...</i>", parse_mode="HTML")
        run_scanner_cycle(bot_token, force=True)
        send_telegram_message(bot_token, chat_id, "✅ <i>Scan complete! Koi qualifying ₹1,500+ A+ setup hone par card bhej diya gaya hai.</i>", parse_mode="HTML", reply_markup=get_keyboard())
        return

    # STATUS COMMAND
    if clean_text in ["/status", "📊 bot & alert status"]:
        min_p = get_min_profit()
        is_open = is_mcx_open()
        status_msg = (
            f"📊 <b>BOT & SCANNER LIVE STATUS</b>\n\n"
            f"• <b>MCX Market Status :</b> {'🟢 OPEN' if is_open else '🔴 CLOSED / SLEEP'}\n"
            f"• <b>Auto-Scan Interval :</b> Every 3 Minutes\n"
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

    # TEST ALERT COMMAND
    if clean_text == "/testalert":
        test_msg = (
            f"🚨 <b>SAMPLE HIGH-PROFIT SIGNAL ALERT!</b> 🚨\n\n"
            f"• <b>Commodity  :</b> 🛢️ CRUDE OIL MINI (10 BBL)\n"
            f"• <b>Direction  :</b> <b>BUY / LONG</b>\n"
            f"• <b>Entry Zone :</b> ₹9,650 - ₹9,660\n"
            f"• <b>Win Chance :</b> <b>82% [HIGH PROBABILITY SETUP]</b>\n"
            f"-----------------------------------------\n"
            f"🛑 <b>Strict SL :</b> ₹9,590 (Risk: ₹600)\n"
            f"🎯 <b>Target 1  :</b> ₹9,820 (<b>+₹1,700 Profit / 1 Lot</b>) 🔥\n"
            f"🎯 <b>Target 2  :</b> ₹9,990 (+₹3,400 Profit / 1 Lot)\n"
            f"-----------------------------------------\n"
            f"💡 <b>Reason:</b> Triple Bullish Trend Alignment + RSI 61.2\n"
            f"📌 <i>Target 1 hit hone par automatic trailing update aayega!</i>"
        )
        send_telegram_message(bot_token, chat_id, test_msg, parse_mode="HTML", reply_markup=get_keyboard())
        return

    # COMMODITY HANDLERS
    if clean_text.startswith("/silver") or clean_text.startswith("silver") or "silver micro" in clean_text:
        send_telegram_message(bot_token, chat_id, "⏳ <i>Silver Micro (1 Kg) analyze ho raha hai...</i>", parse_mode="HTML")
        custom_cmp = extract_custom_cmp(clean_text)
        card_text = analyze_silver_trade(custom_cmp=custom_cmp)
        safe_card = html.escape(card_text)
        formatted_html = f"<b>🎯 MCX SILVER MICRO (1 KG)</b>\n<pre>{safe_card}</pre>"
        send_telegram_message(bot_token, chat_id, formatted_html, parse_mode="HTML", reply_markup=get_keyboard())
        return

    if clean_text.startswith("/gold") or clean_text.startswith("gold") or "gold ten" in clean_text:
        send_telegram_message(bot_token, chat_id, "⏳ <i>Gold Ten (10 Grams) analyze ho raha hai...</i>", parse_mode="HTML")
        custom_cmp = extract_custom_cmp(clean_text)
        card_text = analyze_gold_trade(custom_cmp=custom_cmp)
        safe_card = html.escape(card_text)
        formatted_html = f"<b>🎯 MCX GOLD TEN (10 GRAMS)</b>\n<pre>{safe_card}</pre>"
        send_telegram_message(bot_token, chat_id, formatted_html, parse_mode="HTML", reply_markup=get_keyboard())
        return

    if clean_text.startswith("/crude") or clean_text.startswith("crude") or "crude oil" in clean_text:
        send_telegram_message(bot_token, chat_id, "⏳ <i>Crude Oil Mini (10 BBL) analyze ho raha hai...</i>", parse_mode="HTML")
        custom_cmp = extract_custom_cmp(clean_text)
        card_text = analyze_crude_trade(custom_cmp=custom_cmp)
        safe_card = html.escape(card_text)
        formatted_html = f"<b>🎯 MCX CRUDE OIL MINI (10 BBL)</b>\n<pre>{safe_card}</pre>"
        send_telegram_message(bot_token, chat_id, formatted_html, parse_mode="HTML", reply_markup=get_keyboard())
        return

    if clean_text.startswith("/natgas") or clean_text.startswith("/ng") or "nat gas" in clean_text or "natural gas" in clean_text:
        send_telegram_message(bot_token, chat_id, "⏳ <i>Natural Gas Mini (250 mmBtu) analyze ho raha hai...</i>", parse_mode="HTML")
        custom_cmp = extract_custom_cmp(clean_text)
        card_text = analyze_natgas_trade(custom_cmp=custom_cmp)
        safe_card = html.escape(card_text)
        formatted_html = f"<b>🎯 MCX NATURAL GAS MINI (250 MMBTU)</b>\n<pre>{safe_card}</pre>"
        send_telegram_message(bot_token, chat_id, formatted_html, parse_mode="HTML", reply_markup=get_keyboard())
        return

    if clean_text.startswith("/aluminium") or clean_text.startswith("/aluminum") or clean_text.startswith("/al") or "aluminium" in clean_text or "aluminum" in clean_text:
        send_telegram_message(bot_token, chat_id, "⏳ <i>Aluminium Mini (1000 Kg) analyze ho raha hai...</i>", parse_mode="HTML")
        custom_cmp = extract_custom_cmp(clean_text)
        card_text = analyze_aluminium_trade(custom_cmp=custom_cmp)
        safe_card = html.escape(card_text)
        formatted_html = f"<b>🎯 MCX ALUMINIUM MINI (1000 KG)</b>\n<pre>{safe_card}</pre>"
        send_telegram_message(bot_token, chat_id, formatted_html, parse_mode="HTML", reply_markup=get_keyboard())
        return

    if clean_text.startswith("/zinc") or clean_text.startswith("/zn") or "zinc" in clean_text:
        send_telegram_message(bot_token, chat_id, "⏳ <i>Zinc Mini (1000 Kg) analyze ho raha hai...</i>", parse_mode="HTML")
        custom_cmp = extract_custom_cmp(clean_text)
        card_text = analyze_zinc_trade(custom_cmp=custom_cmp)
        safe_card = html.escape(card_text)
        formatted_html = f"<b>🎯 MCX ZINC MINI (1000 KG)</b>\n<pre>{safe_card}</pre>"
        send_telegram_message(bot_token, chat_id, formatted_html, parse_mode="HTML", reply_markup=get_keyboard())
        return

    if clean_text.startswith("/nickel") or clean_text.startswith("/ni") or "nickel" in clean_text:
        send_telegram_message(bot_token, chat_id, "⏳ <i>Nickel (250 Kg) analyze ho raha hai...</i>", parse_mode="HTML")
        custom_cmp = extract_custom_cmp(clean_text)
        card_text = analyze_nickel_trade(custom_cmp=custom_cmp)
        safe_card = html.escape(card_text)
        formatted_html = f"<b>🎯 MCX NICKEL (250 KG)</b>\n<pre>{safe_card}</pre>"
        send_telegram_message(bot_token, chat_id, formatted_html, parse_mode="HTML", reply_markup=get_keyboard())
        return

    if clean_text.startswith("/electricity") or clean_text.startswith("/power") or clean_text.startswith("/elec") or "electricity" in clean_text or "power" in clean_text:
        send_telegram_message(bot_token, chat_id, "⏳ <i>Electricity Base Load (50 MWh) analyze ho raha hai...</i>", parse_mode="HTML")
        custom_cmp = extract_custom_cmp(clean_text)
        card_text = analyze_electricity_trade(custom_cmp=custom_cmp)
        safe_card = html.escape(card_text)
        formatted_html = f"<b>🎯 MCX ELECTRICITY BASE LOAD (50 MWH)</b>\n<pre>{safe_card}</pre>"
        send_telegram_message(bot_token, chat_id, formatted_html, parse_mode="HTML", reply_markup=get_keyboard())
        return

    # UNKNOWN COMMAND
    fallback_msg = (
        "❓ <i>Command samajh nahi aayi.</i>\n\n"
        "Kripya niche diye gaye buttons dabayein ya yeh type karein:\n"
        "• <code>/scan</code> : Run Live Market Scan\n"
        "• <code>/status</code> : Check Alert & Scanner Status\n"
        "• <code>/silver</code>, <code>/gold</code>, <code>/crude</code>\n"
        "• <code>/natgas</code>, <code>/aluminium</code>, <code>/zinc</code>\n"
        "• <code>/nickel</code>, <code>/electricity</code>"
    )
    send_telegram_message(bot_token, chat_id, fallback_msg, parse_mode="HTML", reply_markup=get_keyboard())


def start_bot():
    print("=" * 60)
    print("🤖 MCX FUTURES TRADING ENGINE + 24/7 AUTO-SCANNER")
    print("🪙 Silver | 🥇 Gold | 🛢️ Crude | 🔥 NatGas | ⚙️ Alum | 🔩 Zinc | 🪙 Ni | ⚡ Elec")
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
    except Exception as e:
        print(f"❌ Connection error: {e}")
        return

    # Start Background Auto-Scanner Thread
    scanner_thread = threading.Thread(target=background_scanner_loop, args=(token,), daemon=True)
    scanner_thread.start()

    last_update_id = 0

    while True:
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates?offset={last_update_id + 1}&timeout=30"
            res = requests.get(url, timeout=35).json()

            if res.get("ok"):
                for update in res.get("result", []):
                    last_update_id = update["update_id"]
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
