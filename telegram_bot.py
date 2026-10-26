import os
import sys
import time
import html
import requests
import warnings
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

import http.server
import socketserver
import threading


def start_health_server():
    """Lightweight HTTP server required by Render.com free web service"""
    port = int(os.environ.get("PORT", 8080))
    class HealthHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"MCX Futures Telegram Bot is LIVE 24/7!")

        def log_message(self, format, *args):
            pass  # Suppress health check spam logs

    try:
        with socketserver.TCPServer(("", port), HealthHandler) as httpd:
            print(f"[+] Cloud Health Server active on port {port} (Render ready)")
            httpd.serve_forever()
    except Exception as e:
        pass


threading.Thread(target=start_health_server, daemon=True).start()


def load_token():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    env_file = os.path.join(base_dir, ".env")
    load_dotenv(dotenv_path=env_file, override=True)
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if token and token.strip() and token != "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        return token.strip()
    return None


def send_telegram_message(bot_token, chat_id, text, parse_mode="HTML", reply_markup=None):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        resp = requests.post(url, json=payload, timeout=10)
        res_json = resp.json()
        if not res_json.get("ok"):
            print(f"[-] Telegram API Error: {res_json.get('description')}")
            # Automatic Fallback: If HTML parsing fails, send plain text
            if parse_mode:
                print("[*] Retrying sending in plain text mode...")
                payload.pop("parse_mode", None)
                fb_resp = requests.post(url, json=payload, timeout=10)
                return fb_resp.json()
        return res_json
    except Exception as e:
        print(f"Send message exception: {e}")
        return None


def get_keyboard():
    return {
        "keyboard": [
            [{"text": "🪙 Silver Micro (1 Kg)"}, {"text": "🥇 Gold Ten (10g)"}],
            [{"text": "🛢️ Crude Oil Mini (10 BBL)"}, {"text": "🔥 Nat Gas Mini (250)"}],
            [{"text": "⚙️ Aluminium Mini (1 MT)"}, {"text": "🔩 Zinc Mini (1 MT)"}],
            [{"text": "🪙 Nickel (250 Kg)"}, {"text": "⚡ Electricity (50 MWh)"}],
            [{"text": "❓ Help & Commands"}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False
    }


def extract_custom_cmp(text):
    """Safely extracts integer or float custom price (e.g. /natgas 240.50 or /silver 197500)"""
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


def handle_message(bot_token, chat_id, user_text, user_name):
    clean_text = user_text.strip().lower()

    if clean_text in ["/start", "/help", "❓ help & commands"]:
        welcome_msg = (
            f"👋 <b>Namaste {user_name}!</b>\n\n"
            f"🎯 <b>MCX Futures Live Advisory Engine</b> mein aapka swagat hai.\n\n"
            f"⚡ <b>Real-Time Exchange Prices + Prediction Win Probability %:</b>\n\n"
            f"• <b>🪙 Silver Micro:</b> 1 Lot = 1 Kg (₹1/pt)\n"
            f"  👉 <code>/silver</code> | Custom: <code>/silver 197500</code>\n\n"
            f"• <b>🥇 Gold Ten:</b> 1 Lot = 10 Grams (₹1/pt)\n"
            f"  👉 <code>/gold</code> | Custom: <code>/gold 141200</code>\n\n"
            f"• <b>🛢️ Crude Oil Mini:</b> 1 Lot = 10 BBL (₹10/pt)\n"
            f"  👉 <code>/crude</code> | Custom: <code>/crude 9650</code>\n\n"
            f"• <b>🔥 Natural Gas Mini:</b> 1 Lot = 250 mmBtu (₹250/pt)\n"
            f"  👉 <code>/natgas</code> | Custom: <code>/natgas 240.6</code>\n\n"
            f"• <b>⚙️ Aluminium Mini:</b> 1 Lot = 1,000 Kg (₹1,000/pt)\n"
            f"  👉 <code>/aluminium</code> | Custom: <code>/aluminium 330.5</code>\n\n"
            f"• <b>🔩 Zinc Mini:</b> 1 Lot = 1,000 Kg (₹1,000/pt)\n"
            f"  👉 <code>/zinc</code> | Custom: <code>/zinc 432.0</code>\n\n"
            f"• <b>🪙 Nickel:</b> 1 Lot = 250 Kg (₹250/pt)\n"
            f"  👉 <code>/nickel</code> | Custom: <code>/nickel 1504</code>\n\n"
            f"• <b>⚡ Electricity:</b> 1 Lot = 50 MWh (₹50/pt)\n"
            f"  👉 <code>/electricity</code> | Custom: <code>/electricity 4850</code>\n\n"
            f"Niche quick buttons se tap karein 👇"
        )
        send_telegram_message(bot_token, chat_id, welcome_msg, parse_mode="HTML", reply_markup=get_keyboard())
        return

    # SILVER HANDLER
    if clean_text.startswith("/silver") or clean_text.startswith("silver") or "silver micro" in clean_text:
        send_telegram_message(bot_token, chat_id, "⏳ <i>Silver Micro (1 Kg) analyze ho raha hai...</i>", parse_mode="HTML")
        custom_cmp = extract_custom_cmp(clean_text)
        card_text = analyze_silver_trade(custom_cmp=custom_cmp)
        safe_card = html.escape(card_text)
        formatted_html = f"<b>🎯 MCX SILVER MICRO (1 KG)</b>\n<pre>{safe_card}</pre>"
        send_telegram_message(bot_token, chat_id, formatted_html, parse_mode="HTML", reply_markup=get_keyboard())
        return

    # GOLD HANDLER
    if clean_text.startswith("/gold") or clean_text.startswith("gold") or "gold ten" in clean_text:
        send_telegram_message(bot_token, chat_id, "⏳ <i>Gold Ten (10 Grams) analyze ho raha hai...</i>", parse_mode="HTML")
        custom_cmp = extract_custom_cmp(clean_text)
        card_text = analyze_gold_trade(custom_cmp=custom_cmp)
        safe_card = html.escape(card_text)
        formatted_html = f"<b>🎯 MCX GOLD TEN (10 GRAMS)</b>\n<pre>{safe_card}</pre>"
        send_telegram_message(bot_token, chat_id, formatted_html, parse_mode="HTML", reply_markup=get_keyboard())
        return

    # CRUDE HANDLER
    if clean_text.startswith("/crude") or clean_text.startswith("crude") or "crude oil" in clean_text:
        send_telegram_message(bot_token, chat_id, "⏳ <i>Crude Oil Mini (10 BBL) analyze ho raha hai...</i>", parse_mode="HTML")
        custom_cmp = extract_custom_cmp(clean_text)
        card_text = analyze_crude_trade(custom_cmp=custom_cmp)
        safe_card = html.escape(card_text)
        formatted_html = f"<b>🎯 MCX CRUDE OIL MINI (10 BBL)</b>\n<pre>{safe_card}</pre>"
        send_telegram_message(bot_token, chat_id, formatted_html, parse_mode="HTML", reply_markup=get_keyboard())
        return

    # NATURAL GAS MINI HANDLER
    if clean_text.startswith("/natgas") or clean_text.startswith("/ng") or "nat gas" in clean_text or "natural gas" in clean_text:
        send_telegram_message(bot_token, chat_id, "⏳ <i>Natural Gas Mini (250 mmBtu) analyze ho raha hai...</i>", parse_mode="HTML")
        custom_cmp = extract_custom_cmp(clean_text)
        card_text = analyze_natgas_trade(custom_cmp=custom_cmp)
        safe_card = html.escape(card_text)
        formatted_html = f"<b>🎯 MCX NATURAL GAS MINI (250 MMBTU)</b>\n<pre>{safe_card}</pre>"
        send_telegram_message(bot_token, chat_id, formatted_html, parse_mode="HTML", reply_markup=get_keyboard())
        return

    # ALUMINIUM MINI HANDLER
    if clean_text.startswith("/aluminium") or clean_text.startswith("/aluminum") or clean_text.startswith("/al") or "aluminium" in clean_text or "aluminum" in clean_text:
        send_telegram_message(bot_token, chat_id, "⏳ <i>Aluminium Mini (1000 Kg) analyze ho raha hai...</i>", parse_mode="HTML")
        custom_cmp = extract_custom_cmp(clean_text)
        card_text = analyze_aluminium_trade(custom_cmp=custom_cmp)
        safe_card = html.escape(card_text)
        formatted_html = f"<b>🎯 MCX ALUMINIUM MINI (1000 KG)</b>\n<pre>{safe_card}</pre>"
        send_telegram_message(bot_token, chat_id, formatted_html, parse_mode="HTML", reply_markup=get_keyboard())
        return

    # ZINC MINI HANDLER
    if clean_text.startswith("/zinc") or clean_text.startswith("/zn") or "zinc" in clean_text:
        send_telegram_message(bot_token, chat_id, "⏳ <i>Zinc Mini (1000 Kg) analyze ho raha hai...</i>", parse_mode="HTML")
        custom_cmp = extract_custom_cmp(clean_text)
        card_text = analyze_zinc_trade(custom_cmp=custom_cmp)
        safe_card = html.escape(card_text)
        formatted_html = f"<b>🎯 MCX ZINC MINI (1000 KG)</b>\n<pre>{safe_card}</pre>"
        send_telegram_message(bot_token, chat_id, formatted_html, parse_mode="HTML", reply_markup=get_keyboard())
        return

    # NICKEL HANDLER
    if clean_text.startswith("/nickel") or clean_text.startswith("/ni") or "nickel" in clean_text:
        send_telegram_message(bot_token, chat_id, "⏳ <i>Nickel (250 Kg) analyze ho raha hai...</i>", parse_mode="HTML")
        custom_cmp = extract_custom_cmp(clean_text)
        card_text = analyze_nickel_trade(custom_cmp=custom_cmp)
        safe_card = html.escape(card_text)
        formatted_html = f"<b>🎯 MCX NICKEL (250 KG)</b>\n<pre>{safe_card}</pre>"
        send_telegram_message(bot_token, chat_id, formatted_html, parse_mode="HTML", reply_markup=get_keyboard())
        return

    # ELECTRICITY HANDLER
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
        "• <code>/silver</code>\n"
        "• <code>/gold</code>\n"
        "• <code>/crude</code>\n"
        "• <code>/natgas</code>\n"
        "• <code>/aluminium</code>\n"
        "• <code>/zinc</code>\n"
        "• <code>/nickel</code>\n"
        "• <code>/electricity</code>"
    )
    send_telegram_message(bot_token, chat_id, fallback_msg, parse_mode="HTML", reply_markup=get_keyboard())


def start_bot():
    print("=" * 60)
    print("🤖 MCX FUTURES ALL-COMMODITY TELEGRAM BOT ENGINE")
    print("🪙 Silver | 🥇 Gold | 🛢️ Crude | 🔥 NatGas | ⚙️ Alum | 🔩 Zinc | 🪙 Ni | ⚡ Elec")
    print("=" * 60)

    token = load_token()
    if not token:
        print("\n[!] TELEGRAM_BOT_TOKEN aapki .env file me missing hai!")
        print("Kripya .env file kholein aur apna token paste karein:")
        print("TELEGRAM_BOT_TOKEN=your_bot_token_here\n")
        return

    # Test Bot Token
    try:
        check_url = f"https://api.telegram.org/bot{token}/getMe"
        resp = requests.get(check_url, timeout=10).json()
        if not resp.get("ok"):
            print(f"\n❌ Error: Invalid Bot Token in .env: {resp.get('description')}")
            return
        bot_info = resp["result"]
        print(f"\n✅ Connected successfully as @{bot_info.get('username')} ({bot_info.get('first_name')})")
        print("🚀 Bot is now LIVE and listening for Telegram messages...")
        print("Apne Telegram app me jakar @{} ko message karein: /start\n".format(bot_info.get('username')))
    except Exception as e:
        print(f"❌ Connection error: {e}")
        return

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
                        print(f"[+] Message received from {user_name}: {user_text}")
                        handle_message(token, chat_id, user_text, user_name)

        except requests.exceptions.RequestException:
            time.sleep(3)
        except Exception as e:
            print(f"Error in bot loop: {e}")
            time.sleep(2)


if __name__ == "__main__":
    start_bot()
