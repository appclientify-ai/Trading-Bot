import re
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
        'dxy_status': 'Stable / Range-bound',
        'rate_sentiment': 'Neutral',
        'dxy_impact': 'Moderate impact on MCX commodities',
        'yield_impact': 'Normal liquidity environment'
    }
    try:
        dxy_val = float(yf.Ticker("DX-Y.NYB").fast_info['lastPrice'])
        indicators['dxy'] = round(dxy_val, 2)
        if dxy_val < 102.5:
            indicators['dxy_status'] = "Weakening 🔻 (Heavy Inflows into Bullion & Oil)"
            indicators['dxy_impact'] = "Dollar fall se Silver, Gold aur Crude tezi se rally karte hain (+₹1,500+ in Silver, +₹120+ in Crude)."
        elif dxy_val > 105.0:
            indicators['dxy_status'] = "Strong 🔺 (Headwind for Bullion)"
            indicators['dxy_impact'] = "Strong Dollar se bullion par short-term selling pressure banta hai."
        else:
            indicators['dxy_status'] = "Moderate / Range-bound (102.5 - 105.0)"
            indicators['dxy_impact'] = "Dollar neutral hai, commodity price news aur charts ke hisab se chalegi."
    except Exception:
        pass

    try:
        y10 = float(yf.Ticker("^TNX").fast_info['lastPrice'])
        indicators['yield_10y'] = round(y10, 2)
        if y10 < 4.2:
            indicators['rate_sentiment'] = "Dovish / Aggressive Rate Cuts Anticipated"
            indicators['yield_impact'] = "Bond yield girne se Silver/Gold zero-yield assets me institutional liquidity flood hoti hai."
        elif y10 > 4.7:
            indicators['rate_sentiment'] = "Hawkish / High Rates For Longer"
            indicators['yield_impact'] = "Yields high rehne se Bullion me profit-booking rallies par aati hai."
        else:
            indicators['rate_sentiment'] = "Balanced Central Bank Expectations"
            indicators['yield_impact'] = "Market rate cuts ki pacing ka wait kar raha hai."
    except Exception:
        pass

    return indicators


def fetch_news_feed(topic="all"):
    """
    Fetches real-time financial news headlines via Google News RSS
    Topics: 'crude', 'bullion', 'natgas', 'all'
    """
    feeds = []
    if topic in ["crude", "all"]:
        feeds.append(("Crude Oil & Geopolitics", "https://news.google.com/rss/search?q=crude+oil+middle+east+opec+when:2d&hl=en-IN&gl=IN&ceid=IN:en", "crude"))
    if topic in ["bullion", "all", "silver", "gold"]:
        feeds.append(("Silver, Gold & US Fed", "https://news.google.com/rss/search?q=silver+gold+fed+rate+interest+inflation+when:2d&hl=en-IN&gl=IN&ceid=IN:en", "bullion"))
    if topic in ["natgas", "all"]:
        feeds.append(("Natural Gas & Weather", "https://news.google.com/rss/search?q=natural+gas+inventory+weather+lng+when:3d&hl=en-IN&gl=IN&ceid=IN:en", "natgas"))

    articles = []
    seen_titles = set()

    for category_name, url, comm_type in feeds:
        try:
            r = requests.get(url, timeout=7)
            if r.status_code == 200:
                root = ET.fromstring(r.content)
                items = root.findall('.//item')[:4]
                for item in items:
                    title_elem = item.find('title')
                    link_elem = item.find('link')
                    pub_elem = item.find('pubDate')
                    source_elem = item.find('source')

                    title = title_elem.text.strip() if title_elem is not None and title_elem.text else ""
                    if not title or title in seen_titles:
                        continue
                    seen_titles.add(title)

                    link = link_elem.text if link_elem is not None and link_elem.text else ""
                    pub = pub_elem.text[:16] if pub_elem is not None and pub_elem.text else datetime.now().strftime("%d %b %H:%M")
                    source = source_elem.text if source_elem is not None and source_elem.text else "Financial Wire"

                    articles.append({
                        'category': category_name,
                        'comm_type': comm_type,
                        'title': title,
                        'link': link,
                        'pub_date': pub,
                        'source': source
                    })
        except Exception as e:
            print(f"[-] News fetch error for {category_name}: {e}")

    return articles


def analyze_headline_price_impact(headline, default_comm_type=None):
    """
    In-depth analysis of a news story to determine:
    1. Underlying meaning / mechanism
    2. Primary MCX commodity affected
    3. Price Direction (Bullish / Bearish / Volatile)
    4. Expected Price Move in ₹ Points / Lot
    5. Actionable Trading Strategy
    """
    text = headline.lower()

    # Default baseline
    res = {
        'commodity': 'MCX Commodities (Crude & Bullion)',
        'direction': '⚖️ RANGE-BOUND / CONSOLIDATION',
        'sentiment_score': 0,
        'meaning': 'Market headline me general industry updates ya regular reporting hai.',
        'price_effect': 'Price par immediate one-sided breakout ka asar kam hai, price current support/resistance range me trade karegi.',
        'expected_move': 'Normal intraday fluctuation (±0.5% to ±1.0%)',
        'strategy': 'Levels confirmation ke bina fresh trade na karein. Support par buy aur Resistance par sell karein.'
    }

    # 1. WAR / MIDDLE EAST / GEOPOLITICAL ESCALATION
    war_triggers = [
        'escalat', 'tension', 'strike', 'attack', 'missile', 'drone', 'war', 'threat',
        'hormuz', 'red sea', 'iran', 'israel', 'houthi', 'yemen', 'russia', 'conflict',
        'shut-in', 'closure', 'blockade', 'sanction', 'crisis', 'retaliat'
    ]
    if any(w in text for w in war_triggers):
        res['commodity'] = 'Crude Oil Mini (10 BBL) & Silver/Gold'
        res['direction'] = '🚀 STRONG BULLISH SURGE (Risk Premium)'
        res['sentiment_score'] = 3
        res['meaning'] = (
            "Middle East aur shipping routes (Strait of Hormuz / Red Sea) par geopolitics badhne se "
            "duniya ki 20%+ physical crude oil supply disrupt hone ka direct khatra ban gaya hai. "
            "Saath hi global funds safe-haven assets (Gold aur Silver) me paisa move karte hain."
        )
        res['price_effect'] = (
            "MCX Crude Oil me continuous buying spike aur overnight gap up ka heavy chance banta hai. "
            "Silver aur Gold bhi safe-haven demand ke chalte strong support le kar upar jump karenge."
        )
        res['expected_move'] = (
            "• Crude Oil Mini: +₹180 se +₹350 / barrel rally (₹1,800 - ₹3,500 Profit / Lot)\n"
            "• Silver Micro: +₹1,500 se +₹3,200 / kg rally (₹1,500 - ₹3,200 Profit / Lot)\n"
            "• Gold Ten: +₹700 se +₹1,500 / 10g surge"
        )
        res['strategy'] = (
            "Short selling bilkul AVOID karein! 15-min chart par jab bhi chhota dip aaye, "
            "Support zone ya VWAP ke paas se BUY ON DIPS karein aur trailing SL tight rakhein."
        )
        return res

    # 2. OPEC+ CUTS / SUPPLY REDUCTION
    opec_triggers = ['opec cut', 'output cut', 'production cut', 'supply deficit', 'quota cut', 'voluntary cut', 'curb output']
    if any(w in text for w in opec_triggers):
        res['commodity'] = 'Crude Oil Mini (10 BBL)'
        res['direction'] = '🔺 BULLISH SUPPLY SQUEEZE'
        res['sentiment_score'] = 2
        res['meaning'] = (
            "OPEC+ countries ne crude production quotas kam kiye hain ya cuts extend kiye hain. "
            "Isse global crude markets me physical shortage aati hai aur spot demand future contracts ko push karti hai."
        )
        res['price_effect'] = (
            "Crude oil prices higher support levels par hold karenge aur intermediate resistance tod kar rally karenge."
        )
        res['expected_move'] = (
            "• Crude Oil Mini: +₹140 se +₹280 / barrel rally (₹1,400 - ₹2,800 Profit / Lot)"
        )
        res['strategy'] = (
            "Resistance breakout ke confirmation par Buy karein. Stop-Loss previous 1-hour swing low par rakhein."
        )
        return res

    # 3. US INVENTORY SURPLUS / CRUDE GLUT / HIGH STOCKPILES / DEMAND WEAKNESS
    crude_bear_triggers = ['inventory build', 'crude inventory rose', 'stockpile rise', 'surplus', 'oversupply', 'glut', 'crude fall', 'oil drops', 'slumps']
    if any(w in text for w in crude_bear_triggers):
        res['commodity'] = 'Crude Oil Mini (10 BBL)'
        res['direction'] = '🔻 BEARISH CORRECTION / DEMAND DRAG'
        res['sentiment_score'] = -2
        res['meaning'] = (
            "US EIA/API data me crude storage badhi hai ya refinery consumption kam hui hai. "
            "Market me supply demand se zyada hone ke signs hain."
        )
        res['price_effect'] = (
            "MCX Crude Oil par upper levels par heavy profit-booking aur rejection aayega. Pullbacks jaldi sell ho jayenge."
        )
        res['expected_move'] = (
            "• Crude Oil Mini: -₹120 se -₹250 / barrel drop (-₹1,200 se -₹2,500 per Lot)"
        )
        res['strategy'] = (
            "Aggressive fresh buying se bachein. Resistance level (R1/R2) ke paas SELL ON RISE ya Put options trade karein."
        )
        return res

    # 4. US FED RATE CUT / DOVISH POWELL / COOLING INFLATION (CPI DROP)
    fed_dovish_triggers = ['rate cut', 'fed cut', 'dovish', 'cooling inflation', 'cpi slowed', 'fed ease', 'rate reduction', 'powell cut']
    if any(w in text for w in fed_dovish_triggers):
        res['commodity'] = 'Silver Micro (1 Kg) #1 & Gold Ten'
        res['direction'] = '🚀 MASSIVE BULLISH EXPANSION'
        res['sentiment_score'] = 3
        res['meaning'] = (
            "US Federal Reserve dwara interest rates ghatane ya dovish stance lene se US Dollar aur US Bond Yields crash hote hain. "
            "Duniya bhar ke FIIs aur institutions zero-yield metals (Silver aur Gold) me massive capital deploy karte hain."
        )
        res['price_effect'] = (
            "Silver Micro me aggressive short covering aur multi-day breakout rally trigger hoti hai. Gap up hone ke chances 85%+ hote hain."
        )
        res['expected_move'] = (
            "• Silver Micro: +₹2,000 se +₹4,500 / kg rally (₹2,000 - ₹4,500 Profit / Micro Lot)\n"
            "• Gold Ten: +₹800 se +₹1,800 / 10g rally"
        )
        res['strategy'] = (
            "Har dip par Silver Micro BUY karein! Yeh BTST aur Swing Long trades ke liye highest win rate setup hota hai."
        )
        return res

    # 5. HAWKISH FED / RATE HIKE / STICKY INFLATION / HIGHER FOR LONGER
    fed_hawkish_triggers = ['rate hike', 'hawkish', 'sticky inflation', 'hot cpi', 'higher for longer', 'delay rate cut', 'fed pause']
    if any(w in text for w in fed_hawkish_triggers):
        res['commodity'] = 'Silver Micro (1 Kg) & Gold Ten'
        res['direction'] = '🔻 BEARISH PULLBACK / LIQUIDATION'
        res['sentiment_score'] = -2
        res['meaning'] = (
            "US Fed ke rates high rakhne ya rate cuts delay karne se Dollar Index strong hota hai aur US bond yields badhti hain. "
            "Metals me long positions liquidate hoti hain."
        )
        res['price_effect'] = (
            "MCX Silver aur Gold higher levels par sustain nahi kar payenge aur correction/pullback dekhne ko milega."
        )
        res['expected_move'] = (
            "• Silver Micro: -₹1,500 se -₹3,000 / kg correction (-₹1,500 se -₹3,000 per Micro Lot)\n"
            "• Gold Ten: -₹600 se -₹1,300 / 10g correction"
        )
        res['strategy'] = (
            "Overnight Long positions hold na karein. Bounce par profit book karein aur jab tak chart par reversal candle na bane tab tak wait karein."
        )
        return res

    # 6. WEAK DOLLAR (DXY DROP)
    dollar_weak_triggers = ['dollar drops', 'dollar slide', 'weak dollar', 'dollar falls', 'dxy slump']
    if any(w in text for w in dollar_weak_triggers):
        res['commodity'] = 'Silver Micro & Crude Oil'
        res['direction'] = '📈 BULLISH LIFT ACROSS ASSETS'
        res['sentiment_score'] = 2
        res['meaning'] = "US Dollar kamzor hone se dollar-priced commodities international kharidaron ke liye cheap ho jaati hain."
        res['price_effect'] = "MCX par Silver aur Crude dono me seedha buying support milta hai."
        res['expected_move'] = (
            "• Silver Micro: +₹1,200 se +₹2,600 / kg rally\n"
            "• Crude Oil Mini: +₹80 se +₹180 / bbl rally"
        )
        res['strategy'] = "Bullish setups ko priority dein. Dips par entry banayein."
        return res

    # 7. TRUCE / PEACE TALKS / CEASEFIRE
    peace_triggers = ['truce', 'ceasefire', 'peace talk', 'hostage deal', 'diplomatic solution', 'de-escalat']
    if any(w in text for w in peace_triggers):
        res['commodity'] = 'Crude Oil Mini & Gold'
        res['direction'] = '🔻 SHARP CORRECTION / GAP DOWN DANGER'
        res['sentiment_score'] = -3
        res['meaning'] = "Geopolitical war risk premium jo Crude aur Gold me priced in tha, tezi se evaporate ho jata hai."
        res['price_effect'] = "MCX Crude Oil me sudden heavy fall ya next day bada gap down ho sakta hai."
        res['expected_move'] = (
            "• Crude Oil Mini: -₹180 se -₹350 / barrel drop (-₹1,800 se -₹3,500 per Lot)\n"
            "• Gold Ten: -₹800 se -₹1,800 / 10g fall"
        )
        res['strategy'] = "Overnight LONG positions bilkul carry na karein (BTST Avoid). Support tootne par trailing short ride karein."
        return res

    # 8. NATURAL GAS / WINTER FREEZE / COLD WAVE / STORAGE DEFICIT
    gas_triggers = ['natural gas', 'lng', 'cold wave', 'winter freeze', 'heating demand', 'gas storage']
    if any(w in text for w in gas_triggers):
        res['commodity'] = 'Natural Gas Mini (250 mmBtu)'
        if any(b in text for b in ['freeze', 'cold', 'surge', 'rally', 'deficit', 'jump', 'spike', 'outage']):
            res['direction'] = '⚡ SUPER BULLISH SPIKE'
            res['sentiment_score'] = 2
            res['meaning'] = "Cold weather ya export terminal outage se heating demand badhti hai aur pipeline supply tight hoti hai."
            res['price_effect'] = "Natural Gas me aggressive high-momentum spikes aate hain."
            res['expected_move'] = "• Natural Gas Mini: +₹12 se +₹28 / mmBtu jump (+₹3,000 se +₹7,000 / Lot)"
            res['strategy'] = "Strict Stop-Loss rakhein, 15m breakout candles ke sath scalp karein."
        else:
            res['direction'] = '🔻 BEARISH PRESSURE / MILD WEATHER'
            res['sentiment_score'] = -1
            res['meaning'] = "Warm weather forecast se heating demand kam rehti hai."
            res['price_effect'] = "Gas inventory badhne se prices pressure me rehte hain."
            res['expected_move'] = "• Natural Gas Mini: -₹8 se -₹18 / mmBtu drop"
            res['strategy'] = "Pullback par sell karein ya consolidation ka wait karein."
        return res

    # 9. CHINA STIMULUS / INDUSTRIAL RECOVERY
    china_triggers = ['china stimulus', 'china demand', 'pboc cut', 'infrastructure spending', 'industrial demand']
    if any(w in text for w in china_triggers):
        res['commodity'] = 'Silver Micro (Industrial 60%), Aluminium & Crude'
        res['direction'] = '🚀 BULLISH INDUSTRIAL BOOM'
        res['sentiment_score'] = 2
        res['meaning'] = "China sabse bada commodity consumer hai. Stimulus se solar, electronics aur energy demand badhti hai."
        res['price_effect'] = "Silver aur Base Metals me continuous buying trend shuru hota hai."
        res['expected_move'] = (
            "• Silver Micro: +₹1,500 se +₹3,200 / kg rally\n"
            "• Aluminium / Zinc: +₹4 se +₹9 / kg rally"
        )
        res['strategy'] = "Industrial commodities me Swing Long positions hold karein."
        return res

    # Generic Fallbacks based on Commodity Type
    if default_comm_type == "crude" or 'oil' in text or 'crude' in text or 'energy' in text:
        res['commodity'] = 'Crude Oil Mini (10 BBL)'
        if any(w in text for w in ['rise', 'rally', 'surge', 'gain', 'high', 'boost', 'climb']):
            res['direction'] = '🔺 MILD BULLISH EXPANSION'
            res['sentiment_score'] = 1
            res['meaning'] = "Global energy market me positive demand cues aur supportive physical market factors dikh rahe hain."
            res['price_effect'] = "MCX Crude intraday dips par support hold karega."
            res['expected_move'] = "• Crude Oil Mini: +₹70 se +₹160 / barrel move (₹700 - ₹1,600 / Lot)"
            res['strategy'] = "15-minute moving average ke paas buying opportunity dekhein."
        elif any(w in text for w in ['fall', 'drop', 'slump', 'pressure', 'decline', 'loss', 'low']):
            res['direction'] = '🔻 MILD BEARISH PRESSURE'
            res['sentiment_score'] = -1
            res['meaning'] = "Economic indicators ya inventory cues se temporary demand weakness dikh rahi hai."
            res['price_effect'] = "MCX Crude higher levels todne me struggle karega."
            res['expected_move'] = "• Crude Oil Mini: -₹70 se -₹150 / barrel dip"
            res['strategy'] = "Rallies par profit book karein aur fresh long breakout ke baad hi lein."

    elif default_comm_type in ["bullion", "silver", "gold"] or 'silver' in text or 'gold' in text:
        res['commodity'] = 'Silver Micro (1 Kg) & Gold Ten'
        if any(w in text for w in ['rise', 'rally', 'surge', 'record', 'gain', 'high', 'climb']):
            res['direction'] = '🔺 MILD BULLISH MOMENTUM'
            res['sentiment_score'] = 1
            res['meaning'] = "Macro support aur currency dynamics ke chalte precious metals me buying interest barkarar hai."
            res['price_effect'] = "Silver aur Gold major support levels ko defend karte hue upar jayenge."
            res['expected_move'] = "• Silver Micro: +₹800 se +₹1,800 / kg rally\n• Gold Ten: +₹400 se +₹800 / 10g"
            res['strategy'] = "Support trendline par buy on dip maintain karein."
        elif any(w in text for w in ['fall', 'drop', 'slip', 'pressure', 'retreat', 'decline']):
            res['direction'] = '🔻 SHORT-TERM CONSOLIDATION PULLBACK'
            res['sentiment_score'] = -1
            res['meaning'] = "Higher levels par profit-taking aur bond yields ke short-term spike ka asar hai."
            res['price_effect'] = "Silver higher levels se thoda cooling down dikha sakta hai."
            res['expected_move'] = "• Silver Micro: -₹800 se -₹1,800 / kg pullback"
            res['strategy'] = "Aggressive buying na karein, support confirmation ka wait karein."

    return res


def format_story_impact_card(article, index=1):
    """Formats an individual news story into a comprehensive price impact card"""
    title = article['title'].replace('<', '&lt;').replace('>', '&gt;')
    impact = analyze_headline_price_impact(article['title'], default_comm_type=article.get('comm_type'))

    card = [
        f"<b>{index}. {title}</b>",
        f"   📅 <i>{article['pub_date']}</i> | 🏢 <b>Source:</b> {article['source']}",
        f"   🎯 <b>Impact On:</b> <code>{impact['commodity']}</code>",
        f"   🧭 <b>Direction :</b> <b>{impact['direction']}</b>",
        f"   🧐 <b>Iska Asli Matlab:</b> {impact['meaning']}",
        f"   📈 <b>MCX Price Par Asar:</b> {impact['price_effect']}",
        f"   💰 <b>Expected Move:</b>\n   {impact['expected_move'].replace(chr(10), chr(10) + '   ')}",
        f"   💡 <b>Action Plan:</b> <i>{impact['strategy']}</i>",
        f"   🔗 <a href='{article['link']}'>Read Full Story</a>\n"
    ]
    return "\n".join(card)


def analyze_macro_sentiment():
    """
    Comprehensive Macro Intelligence & News Radar Report
    with complete price impact analysis for every top story!
    """
    indicators = fetch_macro_indicators()
    news_items = fetch_news_feed("all")

    # Score aggregate sentiment
    total_crude_score = 0
    total_silver_score = 0
    analyzed_cards = []

    for i, a in enumerate(news_items[:3], 1):
        imp = analyze_headline_price_impact(a['title'], default_comm_type=a.get('comm_type'))
        if 'Crude' in imp['commodity']:
            total_crude_score += imp['sentiment_score']
        if 'Silver' in imp['commodity'] or 'Gold' in imp['commodity']:
            total_silver_score += imp['sentiment_score']
        analyzed_cards.append(format_story_impact_card(a, index=i))

    # Overall crude bias
    if total_crude_score > 0:
        crude_bias = "BULLISH 🔺 (Supply Risk & Geopolitical Premium)"
        crude_action = "Dips par BUYING favored. Short selling high risk!"
    elif total_crude_score < 0:
        crude_bias = "BEARISH 🔻 (Inventory Build & Demand Concerns)"
        crude_action = "Higher levels par resistance. Sell on rise favored."
    else:
        crude_bias = "RANGE-BOUND ⚖️ (Mixed Cues)"
        crude_action = "15m chart ke breakout levels ka wait karein."

    # Overall silver bias
    if total_silver_score > 0 and indicators['dxy'] < 103.0:
        silver_bias = "STRONGLY BULLISH 🚀 (Dollar Weakness + Safe Haven)"
        silver_action = "Long positions priority. Dips par aggressive buy."
    elif total_silver_score < 0:
        silver_bias = "MILD CONSOLIDATION / PULLBACK 🔻"
        silver_action = "Higher levels par partial profit book karein."
    else:
        silver_bias = "MODERATE BULLISH 📈 (Rate Cut Sentiment)"
        silver_action = "Key support par Buy on Dips strategy."

    # Build Complete Report
    report = [
        "🌍 <b>GLOBAL MACRO INTELLIGENCE & PRICE IMPACT RADAR</b>",
        "═" * 38,
        f"💵 <b>US Dollar Index (DXY) :</b> <code>{indicators['dxy']}</code>",
        f"   <i>Status: {indicators['dxy_status']}</i>",
        f"   👉 <i>Impact: {indicators['dxy_impact']}</i>\n",
        f"🏛️ <b>US 10-Yr Bond Yield   :</b> <code>{indicators['yield_10y']}%</code>",
        f"   <i>Fed Outlook: {indicators['rate_sentiment']}</i>",
        f"   👉 <i>Impact: {indicators['yield_impact']}</i>\n",
        "═" * 38,
        "🎯 <b>MACRO MARKET PRICE BIAS:</b>\n",
        f"• <b>🛢️ Crude Oil Mini (10 BBL) :</b> <b>{crude_bias}</b>",
        f"  👉 <i>Trader Action: {crude_action}</i>\n",
        f"• <b>🪙 Silver Micro (1 Kg) #1   :</b> <b>{silver_bias}</b>",
        f"  👉 <i>Trader Action: {silver_action}</i>\n",
        "═" * 38,
        "📰 <b>TOP NEWS STORIES & MCX PRICE IMPACT ANALYSIS:</b>\n"
    ]

    if analyzed_cards:
        report.extend(analyzed_cards)
    else:
        report.append("<i>Filhaal koi breaking news headline nahi mili.</i>\n")

    report.append("═" * 38)
    report.append(
        "💡 <b>Institutional Rule:</b> News aane par pehle 5 minute me retail panic karta hai, "
        "lekin asli paisa 15-min candle confirmation ke baad price effect ke direction me banta hai."
    )

    return "\n".join(report)


def analyze_commodity_news(commodity="crude"):
    """
    Dedicated in-depth news & price impact report for a specific commodity:
    - 'crude'
    - 'silver'
    - 'gold'
    - 'natgas'
    """
    comm_lower = commodity.lower()
    topic = "crude" if "crude" in comm_lower or "oil" in comm_lower else (
        "natgas" if "gas" in comm_lower else "bullion"
    )

    articles = fetch_news_feed(topic)
    indicators = fetch_macro_indicators()

    header_icon = "🛢️" if topic == "crude" else ("🔥" if topic == "natgas" else "🪙")
    comm_title = "CRUDE OIL & GEOPOLITICS" if topic == "crude" else (
        "NATURAL GAS & WEATHER" if topic == "natgas" else "SILVER, GOLD & US FED RATES"
    )

    card = [
        f"{header_icon} <b>{comm_title} - NEWS & PRICE IMPACT REPORT</b>",
        "═" * 38,
        f"💵 <b>Dollar (DXY):</b> <code>{indicators['dxy']}</code> | 🏛️ <b>10Y Yield:</b> <code>{indicators['yield_10y']}%</code>\n"
    ]

    if not articles:
        card.append("<i>Filhaal koi new headline nahi mili. Market normal chal raha hai.</i>")
    else:
        for i, a in enumerate(articles[:4], 1):
            card.append(format_story_impact_card(a, index=i))

    card.append("═" * 38)
    if topic == "crude":
        card.append("💡 <b>Crude Golden Rule:</b> Middle East war tension ya OPEC cut = Crude Bullish (+₹150 to +₹350/bbl). US inventory surge = Bearish.")
    elif topic == "natgas":
        card.append("💡 <b>NatGas Golden Rule:</b> Cold wave / freeze forecast = Bullish surge. Mild winter = Heavy sell-off.")
    else:
        card.append("💡 <b>Silver Golden Rule:</b> US Fed Rate Cut + Dollar Index drop = Silver Rocket (+₹2,000 to +₹4,500/kg).")

    return "\n".join(card)


def analyze_custom_news_query(query_text):
    """
    Analyzes any user-provided headline or topic and explains
    its direct price impact on MCX commodities.
    """
    impact = analyze_headline_price_impact(query_text)
    indicators = fetch_macro_indicators()

    card = [
        "🔍 <b>CUSTOM NEWS / EVENT PRICE IMPACT ANALYSIS</b>",
        "═" * 38,
        f"📝 <b>News Query:</b> <i>\"{query_text}\"</i>\n",
        f"🎯 <b>Primary Commodity Affected:</b> <code>{impact['commodity']}</code>",
        f"🧭 <b>Price Direction Expected   :</b> <b>{impact['direction']}</b>\n",
        f"🧐 <b>Iska Asli Matlab (Analysis):</b>\n{impact['meaning']}\n",
        f"📈 <b>MCX Price Par Asar:</b>\n{impact['price_effect']}\n",
        f"💰 <b>Expected Price Move (Target Range):</b>\n{impact['expected_move']}\n",
        f"💡 <b>Actionable Trading Strategy:</b>\n{impact['strategy']}\n",
        "═" * 38,
        f"📊 <i>Live Macro Context: DXY = {indicators['dxy']} | US 10Y Yield = {indicators['yield_10y']}%</i>"
    ]
    return "\n".join(card)


if __name__ == "__main__":
    rep = analyze_macro_sentiment()
    print(rep[:1500])
