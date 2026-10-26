import requests
import xml.etree.ElementTree as ET
import yfinance as yf
from datetime import datetime
import warnings

warnings.filterwarnings('ignore')


def fetch_macro_indicators():
    """Fetches real-time US Dollar Index (DXY) and US 10-Year Treasury Yield"""
    indicators = {
        'dxy': 101.8,
        'yield_10y': 4.98,
        'dxy_status': 'Stable',
        'rate_sentiment': 'Neutral'
    }
    try:
        dxy_val = float(yf.Ticker("DX-Y.NYB").fast_info['lastPrice'])
        indicators['dxy'] = round(dxy_val, 2)
        if dxy_val < 102.5:
            indicators['dxy_status'] = "Weakening 🔻 (Strong Bullish for Silver & Gold)"
        elif dxy_val > 105.0:
            indicators['dxy_status'] = "Strong 🔺 (Pressure on Bullion)"
        else:
            indicators['dxy_status'] = "Moderate / Range-bound"
    except Exception:
        pass

    try:
        y10 = float(yf.Ticker("^TNX").fast_info['lastPrice'])
        indicators['yield_10y'] = round(y10, 2)
        if y10 < 4.2:
            indicators['rate_sentiment'] = "Dovish / Rate Cut Anticipated (Bullish Bullion)"
        elif y10 > 4.7:
            indicators['rate_sentiment'] = "Hawkish / High Rates For Longer"
        else:
            indicators['rate_sentiment'] = "Balanced Expectations"
    except Exception:
        pass

    return indicators


def fetch_news_feed(topic="all"):
    """
    Fetches real-time financial news headlines via Google News RSS
    Topics: 'crude', 'bullion', 'all'
    """
    feeds = []
    if topic in ["crude", "all"]:
        feeds.append(("Crude Oil & Geopolitics", "https://news.google.com/rss/search?q=crude+oil+middle+east+opec+when:2d&hl=en-IN&gl=IN&ceid=IN:en"))
    if topic in ["bullion", "all"]:
        feeds.append(("Silver, Gold & US Fed", "https://news.google.com/rss/search?q=silver+gold+fed+rate+interest+inflation+when:2d&hl=en-IN&gl=IN&ceid=IN:en"))

    articles = []
    for category, url in feeds:
        try:
            r = requests.get(url, timeout=6)
            if r.status_code == 200:
                root = ET.fromstring(r.content)
                items = root.findall('.//item')[:4]
                for item in items:
                    title_elem = item.find('title')
                    link_elem = item.find('link')
                    pub_elem = item.find('pubDate')
                    source_elem = item.find('source')

                    title = title_elem.text if title_elem is not None else ""
                    link = link_elem.text if link_elem is not None else ""
                    pub = pub_elem.text[:16] if pub_elem is not None else ""
                    source = source_elem.text if source_elem is not None else "Financial Wire"

                    articles.append({
                        'category': category,
                        'title': title,
                        'link': link,
                        'pub_date': pub,
                        'source': source
                    })
        except Exception as e:
            print(f"[-] News fetch error for {category}: {e}")

    return articles


def analyze_macro_sentiment():
    """
    Conducts comprehensive fundamental & sentiment analysis
    and formats complete news card with sources.
    """
    indicators = fetch_macro_indicators()
    news_items = fetch_news_feed("all")

    # Sentiment Keyword Scoring
    crude_bullish_words = ['escalat', 'tension', 'strike', 'attack', 'missile', 'supply cut', 'opec cut', 'deficit', 'war', 'threat', 'disrupt']
    crude_bearish_words = ['slump', 'inventory build', 'surplus', 'recession', 'slowdown', 'truce', 'peace', 'oversupply']

    silver_bullish_words = ['rate cut', 'dovish', 'inflation rise', 'safe haven', 'dollar drop', 'weak dollar', 'stimulus', 'gold rally', 'silver surge']
    silver_bearish_words = ['rate hike', 'hawkish', 'dollar rally', 'strong dollar', 'sticky inflation', 'higher for longer']

    crude_score = 0
    silver_score = 0

    for a in news_items:
        t_low = a['title'].lower()
        if any(w in t_low for w in crude_bullish_words):
            crude_score += 1
        if any(w in t_low for w in crude_bearish_words):
            crude_score -= 1
        if any(w in t_low for w in silver_bullish_words):
            silver_score += 1
        if any(w in t_low for w in silver_bearish_words):
            silver_score -= 1

    # Classify Crude Bias
    if crude_score > 0:
        crude_bias = "BULLISH 🔺 (Supply Risk / Geopolitical Premium)"
        crude_action = "Dips par buying support. Short selling risky."
    elif crude_score < 0:
        crude_bias = "BEARISH 🔻 (Demand Concerns / High Inventories)"
        crude_action = "Rallies par resistance face karega."
    else:
        crude_bias = "BALANCED / RANGE-BOUND ⚖️"
        crude_action = "Breakout ka wait karein."

    # Classify Silver/Gold Bias
    if silver_score >= 0 and indicators['dxy'] < 103.0:
        silver_bias = "STRONGLY BULLISH 🚀 (Dollar Weakness + Safe Haven)"
        silver_action = "Long positions favored. Dollar index continuously down."
    elif silver_score < 0:
        silver_bias = "MILD BEARISH / CONSOLIDATION 🔻"
        silver_action = "Higher levels par profit booking expected."
    else:
        silver_bias = "MODERATE BULLISH 📈 (Rate Cut Support)"
        silver_action = "Support levels par buy on dip strategy."

    # Build Report
    card = []
    card.append("🌍 <b>GLOBAL MACRO INTELLIGENCE & NEWS RADAR</b>")
    card.append("═" * 38)
    card.append(f"💵 <b>US Dollar Index (DXY) :</b> <code>{indicators['dxy']}</code>")
    card.append(f"   <i>Status: {indicators['dxy_status']}</i>\n")
    card.append(f"🏛️ <b>US 10-Yr Bond Yield   :</b> <code>{indicators['yield_10y']}%</code>")
    card.append(f"   <i>Fed Outlook: {indicators['rate_sentiment']}</i>\n")
    card.append("═" * 38)
    card.append("🎯 <b>FUNDAMENTAL MARKET BIAS:</b>\n")
    card.append(f"• <b>🛢️ Crude Oil Bias :</b> <b>{crude_bias}</b>")
    card.append(f"  👉 <i>Guidance: {crude_action}</i>\n")
    card.append(f"• <b>🪙 Silver & Gold Bias :</b> <b>{silver_bias}</b>")
    card.append(f"  👉 <i>Guidance: {silver_action}</i>\n")
    card.append("═" * 38)
    card.append("📰 <b>TOP BREAKING HEADLINES & SOURCES:</b>\n")

    for i, item in enumerate(news_items[:6], 1):
        clean_title = item['title'].replace('<', '&lt;').replace('>', '&gt;')
        card.append(f"<b>{i}. {clean_title}</b>")
        card.append(f"   📅 <i>{item['pub_date']}</i> | 🏢 <b>Source:</b> {item['source']}")
        card.append(f"   🔗 <a href='{item['link']}'>Read Full Story</a>\n")

    card.append("═" * 38)
    card.append("💡 <i>Macro Tip: Jab World News aur Technical Chart dono ek direction me hon, tab trade success rate 85%+ hota hai.</i>")

    return "\n".join(card)


if __name__ == "__main__":
    report = analyze_macro_sentiment()
    print(report)
