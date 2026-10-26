import os
import sys
import time
import json
import html
import requests
import warnings
import threading
import urllib3.util.connection as urllib3_cn
from dotenv import load_dotenv

# Fix Indian ISP IPv6 routing issues with Telegram API
urllib3_cn.HAS_IPV6 = False

warnings.filterwarnings('ignore')

from macro_analyzer import (
    analyze_macro_sentiment,
    analyze_commodity_news,
    analyze_custom_news_query,
    fetch_macro_indicators
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
NEWS_SUBSCRIBERS_FILE = os.path.join(BASE_DIR, "news_subscribers.json")

news_subscribers = set()


def load_news_token():
    env_file = os.path.join(BASE_DIR, ".env")
    load_dotenv(dotenv_path=env_file, override=True)
    token = os.getenv("TELEGRAM_NEWS_BOT_TOKEN")
    if token and token.strip() and token != "YOUR_NEWS_BOT_TOKEN_HERE":
        return token.strip()
    return None


def load_subscribers():
    global news_subscribers
    if os.path.exists(NEWS_SUBSCRIBERS_FILE):
        try:
            with open(NEWS_SUBSCRIBERS_FILE, "r") as f:
                news_subscribers = set(json.load(f))
        except Exception:
            news_subscribers = set()


def save_subscriber(chat_id):
    global news_subscribers
    if chat_id not in news_subscribers:
        news_subscribers.add(chat_id)
        try:
            with open(NEWS_SUBSCRIBERS_FILE, "w") as f:
                json.dump(list(news_subscribers), f)
        except Exception:
            pass


def send_message(bot_token, chat_id, text, parse_mode="HTML", reply_markup=None):
    """
    Sends message to Telegram with automatic chunking if text > 3900 characters
    to prevent Telegram's 4096 character limit errors.
    """
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    if len(text) <= 3900:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            resp = requests.post(url, json=payload, timeout=12)
            res_json = resp.json()
            if not res_json.get("ok") and parse_mode:
                payload.pop("parse_mode", None)
                return requests.post(url, json=payload, timeout=12).json()
            return res_json
        except Exception as e:
            print(f"[-] News bot message error: {e}")
            return None

    # Chunk long messages cleanly at double newlines or lines
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
        last_resp = send_message(bot_token, chat_id, c, parse_mode=parse_mode, reply_markup=markup)
        time.sleep(0.35)
    return last_resp


def get_keyboard():
    return {
        "keyboard": [
            [{"text": "🌍 All Macro & News Radar"}],
            [{"text": "🛢️ Crude Oil & War News"}, {"text": "🪙 Silver, Gold & Fed News"}],
            [{"text": "🔥 NatGas & Weather News"}, {"text": "💵 Dollar (DXY) & Yields"}],
            [{"text": "❓ Help & Custom News"}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False
    }


def handle_message(bot_token, chat_id, user_text, user_name):
    save_subscriber(chat_id)
    clean_text = user_text.strip()
    low_text = clean_text.lower()

    if low_text in ["/start", "/help", "❓ help", "❓ help & custom news"]:
        welcome_msg = (
            f"👋 <b>Namaste {user_name}!</b>\n\n"
            f"📰 <b>MCX Global Macro & Commodity News Price-Impact Bot</b> mein aapka swagat hai.\n\n"
            f"⚡ <b>Sirf News Nahi, Deep Institutional Analysis:</b>\n"
            f"Har news headline ke sath aapko direct milega:\n"
            f"• 🧐 <b>Iska Asli Matlab:</b> Parde ke peeche kya supply/demand ho raha hai\n"
            f"• 🎯 <b>Price Direction:</b> Bullish Surge ya Bearish Breakdown\n"
            f"• 💰 <b>Expected Move:</b> MCX par kitne ₹ points ya kitna profit/lot move ho sakta hai\n"
            f"• 💡 <b>Trader Action Plan:</b> Buy on dips karein ya Short karein ya Avoid karein\n\n"
            f"🔍 <b>Quick Commands:</b>\n"
            f"• <code>/news</code> : Poora Global Macro Radar aur Top Stories ka Price Impact\n"
            f"• <code>/crude</code> : Crude Oil, OPEC aur Middle East War News Impact\n"
            f"• <code>/silver</code> : Silver Micro, Gold, US Fed Rate Decisions & Inflation\n"
            f"• <code>/natgas</code> : Natural Gas, US Weather Freeze & Storage Report\n"
            f"• <code>/dxy</code> : US Dollar Index aur Bond Yields ka MCX par asar\n\n"
            f"💬 <b>Custom News Query:</b>\n"
            f"Aap koi bhi news headline ya sawal seedhe yahan type karke bhej sakte hain (jaise: <i>'Iran Israel war update'</i> ya <i>'Fed cut rates by 50 bps'</i>), bot turant price effect batayega!\n\n"
            f"Niche diye gaye buttons se tap karein 👇"
        )
        send_message(bot_token, chat_id, welcome_msg, reply_markup=get_keyboard())
        return

    # ALL NEWS / RADAR
    if low_text in ["/news", "/macro", "🌍 all macro & news radar"]:
        send_message(bot_token, chat_id, "⏳ <i>Live Global Macro & News scan ho raha hai... Sabhi stories ka price effect analyze ho raha hai...</i>")
        report = analyze_macro_sentiment()
        send_message(bot_token, chat_id, report, reply_markup=get_keyboard())
        return

    # CRUDE & WAR NEWS
    if low_text in ["/crude", "/war", "/opec", "/oil", "🛢️ crude oil & war news"]:
        send_message(bot_token, chat_id, "⏳ <i>Crude Oil & Geopolitical News fetch ho rahi hai... MCX Crude Mini price impact calculate ho raha hai...</i>")
        report = analyze_commodity_news("crude")
        send_message(bot_token, chat_id, report, reply_markup=get_keyboard())
        return

    # SILVER & FED NEWS
    if low_text in ["/silver", "/gold", "/fed", "/bullion", "🪙 silver, gold & fed news"]:
        send_message(bot_token, chat_id, "⏳ <i>Silver, Gold & US Fed Rate News fetch ho rahi hai... MCX Silver Micro impact calculate ho raha hai...</i>")
        report = analyze_commodity_news("silver")
        send_message(bot_token, chat_id, report, reply_markup=get_keyboard())
        return

    # NATGAS NEWS
    if low_text in ["/natgas", "/gas", "/ng", "🔥 natgas & weather news"]:
        send_message(bot_token, chat_id, "⏳ <i>Natural Gas & Weather News fetch ho rahi hai... MCX NatGas price impact calculate ho raha hai...</i>")
        report = analyze_commodity_news("natgas")
        send_message(bot_token, chat_id, report, reply_markup=get_keyboard())
        return

    # DOLLAR (DXY) & YIELDS
    if low_text in ["/dxy", "/yield", "💵 dollar (dxy) & yields"]:
        ind = fetch_macro_indicators()
        card = [
            "💵 <b>US DOLLAR INDEX (DXY) & US 10-YR YIELD IMPACT RADAR</b>",
            "═" * 38,
            f"• <b>DXY Index Level   :</b> <code>{ind['dxy']}</code>",
            f"• <b>Dollar Status      :</b> {ind['dxy_status']}",
            f"• <b>MCX Price Effect   :</b> {ind['dxy_impact']}\n",
            f"• <b>US 10-Yr Bond Yield:</b> <code>{ind['yield_10y']}%</code>",
            f"• <b>Fed Rate Sentiment :</b> {ind['rate_sentiment']}",
            f"• <b>Bullion Liquidity  :</b> {ind['yield_impact']}\n",
            "═" * 38,
            "📌 <b>TRADING GOLDEN RULES:</b>",
            "• <b>Dollar 102.5 ke niche:</b> Silver & Gold me massive buying hoti hai (+₹1,500 to +₹3,000 in Silver).",
            "• <b>Bond Yield 4.2% ke niche:</b> Rate cut anticipation se commodity markets rocket ban jaate hain.",
            "• <b>Dollar 105.0 ke upar:</b> Bullion aur Crude par short-term pullbacks aate hain."
        ]
        send_message(bot_token, chat_id, "\n".join(card), reply_markup=get_keyboard())
        return

    # CUSTOM NEWS QUERY / NATURAL LANGUAGE
    send_message(bot_token, chat_id, f"🔍 <i>Aapke news text ka MCX price effect analyze ho raha hai: \"{clean_text[:40]}...\"</i>")
    impact_report = analyze_custom_news_query(clean_text)
    send_message(bot_token, chat_id, impact_report, reply_markup=get_keyboard())


def start_news_bot():
    print("=" * 60)
    print("📰 MCX COMMODITIES GLOBAL NEWS & MACRO SENTIMENT BOT")
    print("=" * 60)

    token = load_news_token()
    if not token:
        print("\n[!] TELEGRAM_NEWS_BOT_TOKEN abhi .env me set nahi hai.")
        print("Bot token ka intezaar kar raha hai...")

        while True:
            time.sleep(10)
            token = load_news_token()
            if token:
                print("\n✅ New Token detected! Starting News Bot...")
                break

    load_subscribers()

    # Connect with retry
    bot_info = None
    for attempt in range(1, 6):
        try:
            check_url = f"https://api.telegram.org/bot{token}/getMe"
            resp = requests.get(check_url, timeout=25).json()
            if not resp.get("ok"):
                print(f"❌ Error: Invalid News Bot Token: {resp.get('description')}")
                return
            bot_info = resp["result"]
            print(f"✅ Connected successfully as @{bot_info.get('username')} ({bot_info.get('first_name')})")
            print("🚀 News Bot is now LIVE and listening for Telegram messages...\n")
            
            # Register Telegram Menu Commands
            news_commands = [
                {"command": "start", "description": "Start News Bot & Open Menu"},
                {"command": "news", "description": "Global Macro Radar with Deep Price Impact"},
                {"command": "crude", "description": "Crude Oil & War News Price Analysis"},
                {"command": "silver", "description": "Silver Micro & US Fed Rate Analysis"},
                {"command": "gold", "description": "Gold & Bullion News Impact"},
                {"command": "natgas", "description": "Natural Gas & Weather News Impact"},
                {"command": "dxy", "description": "Dollar Index & 10Y Yields Impact"},
                {"command": "help", "description": "Help & Custom News Query Guide"}
            ]
            try:
                requests.post(f"https://api.telegram.org/bot{token}/setMyCommands", json={"commands": news_commands}, timeout=6)
            except Exception:
                pass
            break

        except Exception as e:
            print(f"[!] News Bot connection attempt {attempt}/5 failed ({e}). Retrying in 5s...")
            time.sleep(5)

    if not bot_info:
        print("❌ Could not connect to Telegram API after 5 attempts. Exiting.")
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
                        print(f"[+] News Bot Message from {user_name} ({chat_id}): {user_text}")
                        handle_message(token, chat_id, user_text, user_name)

        except requests.exceptions.RequestException:
            time.sleep(3)
        except Exception as e:
            print(f"Error in news bot loop: {e}")
            time.sleep(2)


if __name__ == "__main__":
    start_news_bot()
