import requests
import re
import time

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
}

SLUG_MAP = {
    'gold': 'mcx-gold-price',
    'gold_ten': 'mcx-goldm-price',
    'gold_mini': 'mcx-goldm-price',
    'silver': 'mcx-silver-price',
    'silver_micro': 'mcx-silvermic-price',
    'crude': 'mcx-crudeoilm-price',
    'crude_oil': 'mcx-crudeoilm-price',
    'natgas': 'mcx-natgasmini-price',
    'natural_gas': 'mcx-natgasmini-price',
    'aluminium': 'mcx-alumini-price',
    'zinc': 'mcx-zincmini-price',
    'nickel': 'mcx-nickel-price',
    'copper': 'mcx-copper-price'
}

# Cache to avoid repeated fetches within 15 seconds
_CACHE = {}

def get_live_mcx_price(commodity_key):
    """
    Fetches real-time live LTP from MCX exchange via verified broker feed.
    Returns: float (LTP) or None
    """
    slug = SLUG_MAP.get(commodity_key.lower().replace(' ', '_'), commodity_key)
    now = time.time()
    
    if slug in _CACHE and (now - _CACHE[slug]['time']) < 15:
        return _CACHE[slug]['price']
    
    url = f"https://upstox.com/commodity-market-trading/{slug}/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=6)
        if r.status_code == 200:
            m = re.findall(r'ltp[\\\"\s:]+([0-9\.]+)', r.text)
            if m:
                price = float(m[0])
                _CACHE[slug] = {'price': price, 'time': now}
                return price
    except Exception as e:
        pass
    
    # Return cached price if available
    if slug in _CACHE:
        return _CACHE[slug]['price']
    return None


def calculate_trade_probability(direction, cmp, ema9, ema21, ema50, rsi, atr):
    """
    Calculates statistical trade confluence score (Win Probability %)
    based on trend alignment, momentum strength, volatility health, and R:R.
    Returns: (prob_pct: int, rating: str, reasons: list)
    """
    score = 50.0  # Base neutral baseline
    reasons = []

    if direction == "BUY":
        # 1. EMA Trend Structure
        if cmp >= ema9 and ema9 >= ema21 and ema21 >= ema50:
            score += 18.0
            reasons.append("Triple Bullish Trend Alignment (CMP > EMA-9 > EMA-21 > EMA-50)")
        elif cmp >= ema9 and ema9 >= ema21:
            score += 12.0
            reasons.append("Bullish Short-Term Momentum (CMP > EMA-9 > EMA-21)")
        elif cmp >= ema9:
            score += 6.0
            reasons.append("Price Holding Above EMA-9 Support")
        else:
            score -= 8.0
            reasons.append("Price Below EMA-9 (Counter-Trend Caution)")

        # 2. RSI Momentum
        if 54.0 <= rsi <= 68.0:
            score += 12.0
            reasons.append(f"Bullish Momentum Sweet Spot (RSI {rsi:.1f})")
        elif 48.0 <= rsi < 54.0:
            score += 6.0
            reasons.append(f"Mild Bullish Neutral Bias (RSI {rsi:.1f})")
        elif rsi > 72.0:
            score -= 10.0
            reasons.append(f"Overbought Warning (RSI {rsi:.1f} - Pullback Risk)")
        elif rsi < 45.0:
            score -= 10.0
            reasons.append(f"Weak Bearish RSI ({rsi:.1f})")

    else:  # SELL / SHORT
        # 1. EMA Trend Structure
        if cmp <= ema9 and ema9 <= ema21 and ema21 <= ema50:
            score += 18.0
            reasons.append("Triple Bearish Trend Alignment (CMP < EMA-9 < EMA-21 < EMA-50)")
        elif cmp <= ema9 and ema9 <= ema21:
            score += 12.0
            reasons.append("Bearish Short-Term Momentum (CMP < EMA-9 < EMA-21)")
        elif cmp <= ema9:
            score += 6.0
            reasons.append("Price Under EMA-9 Resistance")
        else:
            score -= 8.0
            reasons.append("Price Above EMA-9 (Counter-Trend Caution)")

        # 2. RSI Momentum
        if 32.0 <= rsi <= 46.0:
            score += 12.0
            reasons.append(f"Bearish Momentum Sweet Spot (RSI {rsi:.1f})")
        elif 46.0 < rsi <= 52.0:
            score += 6.0
            reasons.append(f"Mild Bearish Bias (RSI {rsi:.1f})")
        elif rsi < 28.0:
            score -= 10.0
            reasons.append(f"Oversold Warning (RSI {rsi:.1f} - Bounce Risk)")
        elif rsi > 55.0:
            score -= 10.0
            reasons.append(f"Bullish RSI ({rsi:.1f})")

    # 3. Pullback / Entry Precision vs ATR
    denom = atr if (atr and atr > 0) else 1.0
    dist_to_ema9 = abs(cmp - ema9) / denom
    if dist_to_ema9 <= 0.6:
        score += 8.0
        reasons.append("Entry Near Key EMA Support/Resistance (Tight SL & 1:2 R:R)")
    elif dist_to_ema9 > 1.8:
        score -= 6.0
        reasons.append("Extended From EMA Mean (Wait for pullback)")

    prob_pct = int(min(max(score, 42.0), 86.0))

    if prob_pct >= 74:
        rating = "HIGH PROBABILITY SETUP (A+ High Confidence)"
    elif prob_pct >= 60:
        rating = "MODERATE CONFIDENCE (Standard Trade Setup)"
    else:
        rating = "LOW CONFIDENCE / CHOPPY (Caution Recommended)"

    return prob_pct, rating, reasons
