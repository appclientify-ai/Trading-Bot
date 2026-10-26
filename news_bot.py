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

from macro_analyzer import analyze_macro_sentiment, fetch_macro_indicators, fetch_news_feed

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
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
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
        resp = requests.post(url, json=payload, timeout=10)
        res_json = resp.json()
        if not res_json.get("ok") and parse_mode:
            payload.pop("parse_mode", None)
            return requests.post(url, json=payload, timeout=10).json()
        return res_json
    except Exception as e:
        print(f"[-] News bot message error: {e}")
        return None


def get_keyboard():
    return {
        "keyboard": [
            [{"text": "🌍 All Macro & News Radar"}],
            [{"text": "🛢️ Crude Oil & War News"}, {"text": "🪙 Silver, Gold & Fed News"}],
            [{"text": "💵 Dollar (DXY) & Yields"}, {"text": "❓ Help"}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False
    }


def handle_message(bot_token, chat_id, user_text, user_name):
    save_subscriber(chat_id)
    clean_text = user_text.strip().lower()

    if clean_text in ["/start", "/help", "❓ help"]:
        welcome_msg = (
            f"👋 <b>Namaste {user_name}!</b>\n\n"
            f"📰 <b>MCX Global Macro & Commodity News Bot</b> mein aapka swagat hai.\n\n"
            f"Yahan aapko live world news, US Fed interest rates, war/geopolitics aur "
            f"unka Silver aur Crude Oil par asar (sentiment analysis) direct milega.\n\n"
            f"🔍 <b>Quick Commands:</b>\n"
            f"• <code>/news</code> : Poora Global Macro Radar aur Top Headlines dekhein\n"
            f"• <code>/crude</code> : Crude Oil, OPEC aur Middle East War News\n"
            f"• <code>/silver</code> : Silver, Gold, US Fed Rate Decisions & Inflation\n"
            f"• <code>/dxy</code> : US Dollar Index aur Bond Yields\n\n"
            f"Niche diye gaye buttons par tap karein 👇"
        )
        send_message(bot_token, chat_id, welcome_msg, reply_markup=get_keyboard())
        return

    # ALL NEWS / RADAR
    if clean_text in ["/news", "/macro", "🌍 all macro & news radar"]:
        send_message(bot_token, chat_id, "⏳ <i>Live Global Macro & News scan ho raha hai...</i>")
        report = analyze_macro_sentiment()
        send_message(bot_token, chat_id, report, reply_markup=get_keyboard())
        return

    # CRUDE & WAR NEWS
    if clean_text in ["/crude", "/war", "/opec", "🛢️ crude oil & war news"]:
        send_message(bot_token, chat_id, "⏳ <i>Crude Oil & Geopolitical News fetch ho rahi hai...</i>")
        articles = fetch_news_feed("crude")
        card = ["🛢️ <b>CRUDE OIL & GEOPOLITICAL WAR NEWS</b>\n═" * 20]
        if not articles:
            card.append("Filhaal koi new headline nahi mili.")
        else:
            for i, a in enumerate(articles[:5], 1):
                clean_title = a['title'].replace('<', '&lt;').replace('>', '&gt;')
                card.append(f"<b>{i}. {clean_title}</b>")
                card.append(f"   📅 <i>{a['pub_date']}</i> | 🏢 <b>Source:</b> {a['source']}")
                card.append(f"   🔗 <a href='{a['link']}'>Read Article</a>\n")
        card.append("💡 <i>Rule: Middle East tension ya OPEC production cut = Crude Bullish.</i>")
        send_message(bot_token, chat_id, "\n".join(card), reply_markup=get_keyboard())
        return

    # SILVER & FED NEWS
    if clean_text in ["/silver", "/gold", "/fed", "🪙 silver, gold & fed news"]:
        send_message(bot_token, chat_id, "⏳ <i>Silver, Gold & US Fed Interest Rate News fetch ho rahi hai...</i>")
        articles = fetch_news_feed("bullion")
        card = ["🪙 <b>SILVER, GOLD & US FED RATE OUTLOOK</b>\n═" * 20]
        if not articles:
            card.append("Filhaal koi new headline nahi mili.")
        else:
            for i, a in enumerate(articles[:5], 1):
                clean_title = a['title'].replace('<', '&lt;').replace('>', '&gt;')
                card.append(f"<b>{i}. {clean_title}</b>")
                card.append(f"   📅 <i>{a['pub_date']}</i> | 🏢 <b>Source:</b> {a['source']}")
                card.append(f"   🔗 <a href='{a['link']}'>Read Article</a>\n")
        card.append("💡 <i>Rule: US Fed Rate Cut + Dollar Index girna = Silver & Gold Rocket.</i>")
        send_message(bot_token, chat_id, "\n".join(card), reply_markup=get_keyboard())
        return

    # DOLLAR (DXY) & YIELDS
    if clean_text in ["/dxy", "/yield", "💵 dollar (dxy) & yields"]:
        ind = fetch_macro_indicators()
        card = [
            "💵 <b>US DOLLAR INDEX (DXY) & US 10-YR YIELD</b>\n═" * 20,
            f"• <b>DXY Index Level   :</b> <code>{ind['dxy']}</code>",
            f"• <b>Dollar Status      :</b> {ind['dxy_status']}\n",
            f"• <b>US 10-Yr Bond Yield:</b> <code>{ind['yield_10y']}%</code>",
            f"• <b>Fed Rate Sentiment :</b> {ind['rate_sentiment']}\n",
            "📌 <b>Trading Impact:</b>",
            "• Dollar Index girna Silver aur Gold ke liye strong BUY signal hota hai.",
            "• Bond Yield girna commodity market me liquidity badhata hai."
        ]
        send_message(bot_token, chat_id, "\n".join(card), reply_markup=get_keyboard())
        return

    # Fallback
    send_message(
        bot_token, chat_id,
        "❓ <i>Command samajh nahi aayi.</i>\n\nKripya buttons dabayein ya <code>/news</code> type karein.",
        reply_markup=get_keyboard()
    )


def start_news_bot():
    print("=" * 60)
    print("📰 MCX COMMODITIES GLOBAL NEWS & MACRO SENTIMENT BOT")
    print("=" * 60)

    token = load_news_token()
    if not token:
        print("\n[!] TELEGRAM_NEWS_BOT_TOKEN abhi .env me set nahi hai.")
        print("Jab aap @BotFather se token le lenge, .env file me add karein:")
        print("TELEGRAM_NEWS_BOT_TOKEN=your_token_here\n")
        print("Bot token ka intezaar kar raha hai...")

        # Keep checking every 10 seconds until user adds the token
        while True:
            time.sleep(10)
            token = load_news_token()
            if token:
                print("\n✅ New Token detected! Starting News Bot...")
                break

    load_subscribers()

    try:
        check_url = f"https://api.telegram.org/bot{token}/getMe"
        resp = requests.get(check_url, timeout=10).json()
        if not resp.get("ok"):
            print(f"❌ Error: Invalid News Bot Token: {resp.get('description')}")
            return
        bot_info = resp["result"]
        print(f"✅ Connected successfully as @{bot_info.get('username')} ({bot_info.get('first_name')})")
        print("🚀 News Bot is now LIVE and listening for Telegram messages...\n")
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
                        print(f"[+] News Bot Message from {user_name} ({chat_id}): {user_text}")
                        handle_message(token, chat_id, user_text, user_name)

        except requests.exceptions.RequestException:
            time.sleep(3)
        except Exception as e:
            print(f"Error in news bot loop: {e}")
            time.sleep(2)


if __name__ == "__main__":
    start_news_bot()
