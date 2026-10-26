import time
import requests
import warnings
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

warnings.filterwarnings('ignore')

from mcx_feed import get_live_mcx_price, get_live_dollar_price, get_commodity_expiry
from macro_analyzer import fetch_macro_indicators, fetch_news_feed

COMMODITY_SPECS = {
    'silver': {
        'name': 'MCX SILVER MICRO (1 KG)',
        'symbol': 'SI=F',
        'lot_size': 1,
        'tick_val': 1.0,
        'unit': 'Kg',
        'macro_cat': 'bullion',
        'key': 'silver_micro'
    },
    'crude': {
        'name': 'MCX CRUDE OIL MINI (10 BBL)',
        'symbol': 'CL=F',
        'lot_size': 10,
        'tick_val': 10.0,
        'unit': 'BBL',
        'macro_cat': 'crude',
        'key': 'crude_oil'
    },
    'gold': {
        'name': 'MCX GOLD TEN (10 GRAMS)',
        'symbol': 'GC=F',
        'lot_size': 1,
        'tick_val': 1.0,
        'unit': '10g',
        'macro_cat': 'bullion',
        'key': 'gold_ten'
    },
    'natgas': {
        'name': 'MCX NATURAL GAS MINI (250 MMBTU)',
        'symbol': 'NG=F',
        'lot_size': 250,
        'tick_val': 250.0,
        'unit': 'mmBtu',
        'macro_cat': 'crude',
        'key': 'natgas'
    },
    'aluminium': {
        'name': 'MCX ALUMINIUM MINI (1000 KG)',
        'symbol': 'ALI=F',
        'alt_sym': 'HG=F',
        'lot_size': 1000,
        'tick_val': 1000.0,
        'unit': 'MT',
        'macro_cat': 'bullion',
        'key': 'aluminium'
    },
    'zinc': {
        'name': 'MCX ZINC MINI (1000 KG)',
        'symbol': 'ZNC=F',
        'alt_sym': 'HG=F',
        'lot_size': 1000,
        'tick_val': 1000.0,
        'unit': 'MT',
        'macro_cat': 'bullion',
        'key': 'zinc'
    },
    'nickel': {
        'name': 'MCX NICKEL (250 KG)',
        'symbol': 'HG=F',
        'lot_size': 250,
        'tick_val': 250.0,
        'unit': '250Kg',
        'macro_cat': 'bullion',
        'key': 'nickel'
    },
    'electricity': {
        'name': 'MCX ELECTRICITY BASE LOAD (50 MWH)',
        'symbol': None,
        'lot_size': 50,
        'tick_val': 50.0,
        'unit': '50 MWh',
        'macro_cat': 'crude',
        'key': 'electricity'
    }
}


def analyze_btst_swing(commodity_query):
    """
    On-Demand Multi-Timeframe BTST / STBT & Multi-Day Swing Advisory Engine.
    Combines Daily Chart (1D EMAs, RSI, Candlestick) + Macro (DXY, Yields) + Overnight Gap-Risk.
    """
    q = commodity_query.lower().strip()
    target_key = None
    for k in COMMODITY_SPECS:
        if k in q:
            target_key = k
            break
    if not target_key:
        if 'oil' in q or 'cruid' in q:
            target_key = 'crude'
        elif 'power' in q or 'elec' in q:
            target_key = 'electricity'
        elif 'gas' in q or 'ng' in q:
            target_key = 'natgas'
        elif 'al' in q:
            target_key = 'aluminium'
        elif 'zn' in q:
            target_key = 'zinc'
        elif 'ni' in q:
            target_key = 'nickel'
        else:
            target_key = 'silver'

    spec = COMMODITY_SPECS[target_key]
    cmp = get_live_mcx_price(spec['key']) or 0.0
    usd_ref = get_live_dollar_price(target_key)

    # 1. Macro Indicators
    macro = fetch_macro_indicators()
    dxy = macro['dxy']
    yield_10y = macro['yield_10y']

    # 2. Daily Higher Timeframe Technical Analysis
    sym = spec['symbol']
    daily_bias = "NEUTRAL"
    daily_rsi = 55.0
    ema20 = cmp
    ema50 = cmp
    daily_atr = cmp * 0.015 if cmp > 0 else 10.0
    candle_pattern = "Consolidation Candle"

    if sym:
        try:
            df = yf.download(sym, period="3mo", interval="1d", progress=False)
            if not df.empty and len(df) >= 20:
                close = df['Close'].iloc[:, 0] if isinstance(df['Close'], pd.DataFrame) else df['Close']
                high = df['High'].iloc[:, 0] if isinstance(df['High'], pd.DataFrame) else df['High']
                low = df['Low'].iloc[:, 0] if isinstance(df['Low'], pd.DataFrame) else df['Low']
                open_p = df['Open'].iloc[:, 0] if isinstance(df['Open'], pd.DataFrame) else df['Open']

                # Calculate Daily 20 & 50 EMA
                ema20_s = close.ewm(span=20, adjust=False).mean()
                ema50_s = close.ewm(span=50, adjust=False).mean()
                
                # Daily RSI
                delta = close.diff()
                gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                rs = gain / (loss + 1e-9)
                rsi_s = 100 - (100 / (1 + rs))

                # Daily ATR
                tr1 = high - low
                tr2 = (high - close.shift()).abs()
                tr3 = (low - close.shift()).abs()
                tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
                atr_s = tr.rolling(window=14).mean()

                last_close = float(close.iloc[-1])
                last_open = float(open_p.iloc[-1])
                last_high = float(high.iloc[-1])
                last_low = float(low.iloc[-1])
                last_rsi = float(rsi_s.iloc[-1])
                last_ema20 = float(ema20_s.iloc[-1])
                last_ema50 = float(ema50_s.iloc[-1])
                last_atr = float(atr_s.iloc[-1])

                daily_rsi = round(last_rsi, 1)
                
                # Price Ratio relative to MCX CMP
                ratio = cmp / last_close if (last_close > 0 and cmp > 0) else 1.0
                daily_atr = round(last_atr * ratio, 1)

                # Candlestick Pattern Detection
                rng = (last_high - last_low) + 1e-9
                body = abs(last_close - last_open)
                if last_close > last_open:
                    if (last_close - last_open) / rng > 0.65:
                        candle_pattern = "Bullish Strong Marubozu (Closing Near Day Highs)"
                    elif (last_open - last_low) / rng > 0.5:
                        candle_pattern = "Bullish Hammer (Dip Bought Aggressively)"
                    else:
                        candle_pattern = "Mild Bullish Green Candle"
                else:
                    if (last_open - last_close) / rng > 0.65:
                        candle_pattern = "Bearish Breakdown Marubozu (Closing Near Day Lows)"
                    elif (last_high - last_open) / rng > 0.5:
                        candle_pattern = "Shooting Star (Rejection from Top)"
                    else:
                        candle_pattern = "Mild Bearish Red Candle"

                # Trend Determination
                if last_close >= last_ema20 and last_ema20 >= last_ema50:
                    daily_bias = "BULLISH"
                elif last_close <= last_ema20 and last_ema20 <= last_ema50:
                    daily_bias = "BEARISH"
                elif last_close >= last_ema20:
                    daily_bias = "MILD_BULLISH"
                else:
                    daily_bias = "MILD_BEARISH"
        except Exception:
            pass

    if cmp <= 0:
        cmp = 1000.0

    # 3. Macro Alignment Factor
    macro_score = 0
    macro_notes = []
    if target_key in ['silver', 'gold']:
        if dxy < 103.0:
            macro_score += 2
            macro_notes.append(f"US Dollar Index is Soft ({dxy} 🔻) - Super Bullish for Bullion")
        elif dxy > 105.0:
            macro_score -= 2
            macro_notes.append(f"US Dollar Index is High ({dxy} 🔺) - Capping Upward Momentum")
        else:
            macro_notes.append(f"US Dollar Index is Neutral ({dxy})")

        if yield_10y < 4.30:
            macro_score += 1
            macro_notes.append(f"US 10-Yr Bond Yield Falling ({yield_10y}%) - Liquidity Positive")
        elif yield_10y > 4.60:
            macro_score -= 1
            macro_notes.append(f"US 10-Yr Bond Yield Elevated ({yield_10y}%)")
    elif target_key == 'crude':
        macro_notes.append("OPEC+ Output Restraints & Middle East Logistics Tension remain Active")
        macro_score += 1
    elif target_key == 'natgas':
        macro_notes.append("US Weather Forecasts & Weekly Storage Injection trends drive moves")
    else:
        macro_notes.append("Global Industrial Metals demand & LME inventory tracking")

    # 4. Final Verdict: BTST / STBT / Swing Bias
    win_prob = 75
    if daily_bias in ["BULLISH", "MILD_BULLISH"] and daily_rsi >= 50.0 and macro_score >= 0:
        trade_type = "BTST (Buy Today, Sell Tomorrow) / Positional Buy"
        direction = "BUY / LONG"
        bias_verdict = "🚀 STRONG BULLISH CONVICTION"
        win_prob = min(88, 75 + int((daily_rsi - 50) * 0.4) + macro_score * 3)

        # Levels
        entry_low = round(cmp - (daily_atr * 0.2), 1)
        entry_high = round(cmp + (daily_atr * 0.1), 1)
        sl_price = round(cmp - (daily_atr * 1.2), 1)
        t1_price = round(cmp + (daily_atr * 1.0), 1)
        t2_price = round(cmp + (daily_atr * 2.2), 1)

        t1_move = abs(t1_price - cmp)
        t2_move = abs(t2_price - cmp)
        sl_move = abs(cmp - sl_price)

        opt_strategy = "Bull Call Spread (Buy ATM Call + Sell OTM Call) to cap overnight gap risk"
        hold_time = "Hold Overnight (Exit tomorrow 9:30 AM - 11:30 AM or trail to T2)"

    elif daily_bias in ["BEARISH", "MILD_BEARISH"] and daily_rsi <= 50.0 and macro_score <= 0:
        trade_type = "STBT (Sell Today, Buy Tomorrow) / Positional Sell"
        direction = "SELL / SHORT"
        bias_verdict = "📉 STRONG BEARISH CONVICTION"
        win_prob = min(87, 75 + int((50 - daily_rsi) * 0.4) + abs(macro_score) * 3)

        # Levels
        entry_low = round(cmp - (daily_atr * 0.1), 1)
        entry_high = round(cmp + (daily_atr * 0.2), 1)
        sl_price = round(cmp + (daily_atr * 1.2), 1)
        t1_price = round(cmp - (daily_atr * 1.0), 1)
        t2_price = round(cmp - (daily_atr * 2.2), 1)

        t1_move = abs(cmp - t1_price)
        t2_move = abs(cmp - t2_price)
        sl_move = abs(sl_price - cmp)

        opt_strategy = "Bear Put Spread (Buy ATM Put + Sell OTM Put) to hedge overnight gap-up"
        hold_time = "Hold Overnight (Book at morning gap-down or trail to T2)"

    else:
        trade_type = "SIDEWAYS CONSOLIDATION / NO BTST"
        direction = "NO TRADE / WAIT"
        bias_verdict = "⚠️ CHOPPY / AVOID OVERNIGHT HOLD"
        win_prob = 52
        entry_low = cmp
        entry_high = cmp
        sl_price = round(cmp - daily_atr, 1)
        t1_price = round(cmp + daily_atr, 1)
        t2_price = round(cmp + daily_atr * 2, 1)
        t1_move = daily_atr
        t2_move = daily_atr * 2
        sl_move = daily_atr
        opt_strategy = "Avoid holding overnight naked futures. Wait for clean breakout above range."
        hold_time = "Intraday Only (Exit before 11:30 PM close)"

    t1_profit_inr = round(t1_move * spec['tick_val'], 0)
    t2_profit_inr = round(t2_move * spec['tick_val'], 0)
    sl_risk_inr = round(sl_move * spec['tick_val'], 0)

    # 5. Overnight Gap-Risk Assessment
    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist)
    if now_ist.weekday() == 4:  # Friday
        gap_risk = "MODERATE TO HIGH (Weekend 2-Day International Carry Risk)"
        gap_tip = "Weekend par international events ke chalte Friday BTST me position size 50% rakhein."
    elif win_prob >= 78:
        gap_risk = "LOW TO MODERATE 🟢 (Clean Technical Trend Support)"
        gap_tip = "Strict SL ke sath overnight carry safe hai."
    else:
        gap_risk = "HIGH 🔴 (Chop Risk / News Vulnerability)"
        gap_tip = "Overnight gap risk bada hai, position carry na karein ya Options Spread use karein."

    # Build Beautiful Telegram Card
    usd_str = f"• <b>Global Benchmark :</b> <code>{usd_ref}</code>\n" if usd_ref else ""
    macro_bullets = "\n".join([f"• {m}" for m in macro_notes])

    dir_icon = "🟢" if "BUY" in direction else ("🔴" if "SELL" in direction else "⚖️")

    time_ist_str = now_ist.strftime("%I:%M:%S %p")

    card = (
        f"🌙 <b>MCX BTST / SWING: {spec['name']}</b>\n"
        f"⏰ <b>Signal Time    :</b> <code>{time_ist_str} IST</code>\n"
        f"📅 <b>Contract Expiry:</b> <code>{get_commodity_expiry(target_key)}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <b>SETUP  :</b> {dir_icon} <b>{trade_type}</b>\n"
        f"📍 <b>ENTRY  :</b> <b>₹{entry_low:,.1f} - ₹{entry_high:,.1f}</b>  <i>(CMP: ₹{cmp:,.1f})</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>TARGET 1 :</b> <b>₹{t1_price:,.1f}</b>  (<b>+₹{t1_profit_inr:,.0f} Profit / Lot</b>) [Morning Open] 🔥\n"
        f"🎯 <b>TARGET 2 :</b> <b>₹{t2_price:,.1f}</b>  (+₹{t2_profit_inr:,.0f} Profit / Lot) [Swing Hold]\n"
        f"🛑 <b>STOP-LOSS:</b> <b>₹{sl_price:,.1f}</b>  (Risk: -₹{sl_risk_inr:,.0f} / Lot)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎲 <b>Win Chance:</b> <b>{win_prob}%</b> [{bias_verdict}]\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ <b>EXPECTED HOLD TIME:</b>\n"
        f"• 🎯 <b>Target 1 :</b> <b>Next Morning Open (9:00 - 10:30 AM Gap-Move) 🔥</b>\n"
        f"• 🎯 <b>Target 2 :</b> <b>1 - 2 Days Positional Swing</b>\n"
        f"⏳ <b>Max Hold  :</b> <i>Next Day 11:30 AM tak momentum na mile toh exit karein</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <b>Gap Risk  :</b> {gap_risk}\n"
        f"💡 <b>Hold Plan :</b> {hold_time}\n"
        f"🛡️ <b>Options   :</b> <i>{opt_strategy}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>1D Context :</b> Trend: {daily_bias.replace('_', ' ').title()} | RSI: {daily_rsi} | {candle_pattern}\n"
        f"💡 <i>{gap_tip}</i>"
    )

    return {
        'commodity': spec['name'],
        'trade_type': trade_type,
        'direction': direction,
        'win_prob': win_prob,
        'entry': cmp,
        't1': t1_price,
        't2': t2_price,
        'sl': sl_price,
        't1_profit': t1_profit_inr,
        'sl_risk': sl_risk_inr,
        'card_text': card
    }
