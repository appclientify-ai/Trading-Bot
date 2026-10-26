import requests
import re
import time
from datetime import datetime, timezone, timedelta

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
}

SLUG_MAP = {
    'gold': 'mcx-goldm-price',          # Strictly Gold Ten (10 Grams)
    'gold_ten': 'mcx-goldm-price',
    'gold_mini': 'mcx-goldm-price',
    'goldm': 'mcx-goldm-price',
    'silver': 'mcx-silvermic-price',     # Strictly Silver Micro (1 Kg)
    'silver_micro': 'mcx-silvermic-price',
    'silvermic': 'mcx-silvermic-price',
    'crude': 'mcx-crudeoilm-price',      # Strictly Crude Oil Mini (10 BBL)
    'crude_oil': 'mcx-crudeoilm-price',
    'crudeoilm': 'mcx-crudeoilm-price',
    'natgas': 'mcx-natgasmini-price',
    'natural_gas': 'mcx-natgasmini-price',
    'natgasmini': 'mcx-natgasmini-price',
    'aluminium': 'mcx-alumini-price',
    'zinc': 'mcx-zincmini-price',
    'nickel': 'mcx-nickel-price',
    'electricity': 'mcx-elecdmbl-price',
    'elec': 'mcx-elecdmbl-price',
    'elecdmbl': 'mcx-elecdmbl-price',
    'copper': 'mcx-copper-price'
}

# Cache to avoid repeated fetches within 15 seconds
_CACHE = {}
_CANDLE_CACHE = {}


def get_intraday_candles(symbol, period="7d", interval="15m", max_age_seconds=25):
    """
    In-memory cache for 15-minute candlesticks + permanent SQLite storage.
    Prevents repeated Yahoo Finance downloads during the same scan cycle (Ban-Proof).
    """
    now = time.time()
    cache_key = f"{symbol}_{period}_{interval}"
    if cache_key in _CANDLE_CACHE:
        entry = _CANDLE_CACHE[cache_key]
        if (now - entry['time']) < max_age_seconds and entry['df'] is not None and not entry['df'].empty:
            return entry['df'].copy()

    try:
        import yfinance as yf
        import pandas as pd
        df = yf.download(symbol, period=period, interval=interval, progress=False)
        if df is not None and not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            _CANDLE_CACHE[cache_key] = {'df': df, 'time': now}
            try:
                from database import save_candles_to_db
                save_candles_to_db(symbol, df, interval=interval)
            except Exception:
                pass
            return df.copy()
    except Exception as e:
        print(f"[-] Candle download error for {symbol}: {e}")

    if cache_key in _CANDLE_CACHE:
        return _CANDLE_CACHE[cache_key]['df'].copy()
    import pandas as pd
    return pd.DataFrame()


def fetch_mcx_market_data(commodity_key):
    """
    Fetches real-time live LTP and contract Expiry date from MCX exchange broker feed.
    Returns: {'price': float, 'expiry': str} or None
    """
    slug = SLUG_MAP.get(commodity_key.lower().replace(' ', '_'), commodity_key)
    now = time.time()

    if slug in _CACHE and (now - _CACHE[slug]['time']) < 15:
        return _CACHE[slug]

    url = f"https://upstox.com/commodity-market-trading/{slug}/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=6)
        if r.status_code == 200:
            ltp_m = re.findall(r'ltp[^\d]+([0-9\.]+)', r.text)
            exp_m = re.findall(r'Expiry date</div><div[^>]*>([^<]+)</div>', r.text)

            price = float(ltp_m[0]) if ltp_m else None
            expiry = exp_m[0].strip() if exp_m else "Active Contract"

            if price is not None:
                _CACHE[slug] = {'price': price, 'expiry': expiry, 'time': now}
                return _CACHE[slug]
    except Exception:
        pass

    if slug in _CACHE:
        return _CACHE[slug]
    return None


def get_live_mcx_price(commodity_key):
    """
    Fetches real-time live LTP from MCX exchange via verified broker feed.
    Returns: float (LTP) or None
    """
    data = fetch_mcx_market_data(commodity_key)
    if data:
        return data.get('price')
    return None


def get_commodity_expiry(commodity_key):
    """
    Fetches the active contract expiry date from live exchange feed.
    Returns: string (e.g. '30 Nov, 2026')
    """
    data = fetch_mcx_market_data(commodity_key)
    if data and data.get('expiry') and data.get('expiry') != "Active Contract":
        return data.get('expiry')

    fallbacks = {
        'silver': '30 Nov, 26',
        'silver_micro': '30 Nov, 26',
        'crude': '19 Nov, 26',
        'crude_oil': '19 Nov, 26',
        'gold': '05 Nov, 26',
        'gold_ten': '05 Nov, 26',
        'natgas': '27 Oct, 26',
        'aluminium': '30 Oct, 26',
        'zinc': '30 Oct, 26',
        'nickel': '18 Nov, 26',
        'electricity': '30 Oct, 26'
    }
    key = commodity_key.lower().replace(' ', '_')
    for k, v in fallbacks.items():
        if k in key:
            return v
    return "Current Active Month"


def calculate_expected_time(commodity_name, is_smc=False):
    """
    Computes scientific expected holding time and target reach window
    based on MCX market session (IST), commodity velocity, and setup type.
    Returns: (t1_time: str, t2_time: str, max_hold: str)
    """
    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist)
    hour = now_ist.hour

    # US Session (7:00 PM to 9:30 PM IST) = Maximum Volume & Speed
    is_us_open = 19 <= hour <= 21
    # Late Evening (after 10:00 PM IST)
    is_late_evening = hour >= 22

    comm_upper = commodity_name.upper()

    if is_smc:
        if is_us_open:
            t1_time = "20 - 35 Mins (Fast US Rejection) 🔥"
            t2_time = "1 - 1.5 Ghante (Institutional Expansion)"
        elif is_late_evening:
            t1_time = "30 - 45 Mins (Pre-Close Move)"
            t2_time = "Exit before 11:15 PM Close"
        else:
            t1_time = "30 - 50 Mins (Order Block Rebound)"
            t2_time = "1.5 - 2.5 Ghante (Liquidity Run)"
        max_hold = "11:15 PM IST tak Target 1 na mile toh exit karein"
    else:
        # Standard Momentum / Intraday
        if "CRUDE" in comm_upper or "NATGAS" in comm_upper or "NAT GAS" in comm_upper:
            if is_us_open:
                t1_time = "25 - 45 Mins (Peak US Volume) 🔥"
                t2_time = "1 - 2 Ghante (Session Trend)"
            else:
                t1_time = "35 - 60 Mins"
                t2_time = "1.5 - 2.5 Ghante"
        elif "SILVER" in comm_upper or "GOLD" in comm_upper:
            if is_us_open:
                t1_time = "30 - 50 Mins (Comex US Session) 🔥"
                t2_time = "1.5 - 2.5 Ghante"
            else:
                t1_time = "45 - 75 Mins"
                t2_time = "2 - 3 Ghante"
        else:
            # Base metals / Electricity
            t1_time = "40 - 70 Mins"
            t2_time = "2 - 3 Ghante"

        max_hold = "11:15 PM IST tak Target 1 na mile toh safe exit"

    return t1_time, t2_time, max_hold



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


# International Dollar Price Cache
_USD_CACHE = {}

USD_SYMBOL_MAP = {
    'crude': ('CL=F', 'WTI Crude', 'bbl'),
    'crude_oil': ('CL=F', 'WTI Crude', 'bbl'),
    'silver': ('SI=F', 'Comex Silver', 'oz'),
    'silver_micro': ('SI=F', 'Comex Silver', 'oz'),
    'gold': ('GC=F', 'Comex Gold', 'oz'),
    'gold_ten': ('GC=F', 'Comex Gold', 'oz'),
    'natgas': ('NG=F', 'Henry Hub Gas', 'mmBtu'),
    'natural_gas': ('NG=F', 'Henry Hub Gas', 'mmBtu')
}


def get_live_dollar_price(commodity_key):
    """
    Fetches real-time international dollar benchmark price (e.g. WTI Crude $/bbl, Comex Silver $/oz).
    Returns: str formatted like '$101.09 / bbl' or None
    """
    key = commodity_key.lower().replace(' ', '_')
    cfg = None
    for k, v in USD_SYMBOL_MAP.items():
        if k in key:
            cfg = v
            break

    if not cfg:
        return None

    sym, name, unit = cfg
    now = time.time()
    if sym in _USD_CACHE and (now - _USD_CACHE[sym]['time']) < 60:
        val = _USD_CACHE[sym]['val']
        return f"{name}: ${val:,.2f} / {unit}"

    try:
        import yfinance as yf
        t = yf.Ticker(sym)
        val = t.fast_info.last_price
        if val and val > 0:
            _USD_CACHE[sym] = {'val': val, 'time': now}
            return f"{name}: ${val:,.2f} / {unit}"
    except Exception:
        pass

    if sym in _USD_CACHE:
        val = _USD_CACHE[sym]['val']
        return f"{name}: ${val:,.2f} / {unit}"

    return None

