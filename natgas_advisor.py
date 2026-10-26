import sys
import pandas as pd
import yfinance as yf
from datetime import datetime
import warnings

warnings.filterwarnings('ignore')

from mcx_feed import get_live_mcx_price, calculate_trade_probability


def analyze_natgas_trade(custom_cmp=None, return_data=False):
    user_override_cmp = custom_cmp
    if user_override_cmp is None and len(sys.argv) > 1:
        try:
            user_override_cmp = float(sys.argv[1].replace(',', '').replace('₹', ''))
            print(f"\n[+] Broker Live CMP received: ₹{user_override_cmp:,.1f} (Anchoring targets to this exact price)...")
        except ValueError:
            user_override_cmp = None

    if user_override_cmp is None:
        print("\n[+] MCX NATURAL GAS MINI (250 mmBtu) Live Targets & Setup Analyze ho raha hai...\n")
    
    # 1. Fetch benchmark data (15-Minute Intraday Candlesticks for Henry Hub NG)
    try:
        df = yf.download("NG=F", period="5d", interval="15m", progress=False)
    except Exception as e:
        err_msg = f"Error fetching data: {e}"
        print(err_msg)
        return err_msg

    if df.empty:
        err_msg = "Data nahi mila. Internet check karein."
        print(err_msg)
        return err_msg

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # 2. Fetch live USD/INR exchange rate
    try:
        usdinr = float(yf.Ticker("USDINR=X").fast_info['lastPrice'])
    except Exception:
        usdinr = 95.20

    cmp_usd = float(df['Close'].iloc[-1].item() if hasattr(df['Close'].iloc[-1], 'item') else df['Close'].iloc[-1])

    # 3. Live MCX Market Price Determination (Exchange Feed > Custom > Calibrated)
    market_feed_price = get_live_mcx_price('natgas')
    if user_override_cmp is not None:
        target_cmp = user_override_cmp
        price_source = "User Broker Input"
    elif market_feed_price is not None:
        target_cmp = market_feed_price
        price_source = "MCX Live Exchange Feed"
    else:
        target_cmp = 240.60
        price_source = "Calibrated MCX Active Contract"

    # Scale intraday series to match exact MCX CMP
    curr_close = float(df['Close'].iloc[-1])
    scale_ratio = target_cmp / curr_close
    for col in ['Open', 'High', 'Low', 'Close']:
        df[col] = df[col] * scale_ratio

    # 4. Technical Indicators
    df['EMA_9'] = df['Close'].ewm(span=9, adjust=False).mean()
    df['EMA_21'] = df['Close'].ewm(span=21, adjust=False).mean()
    df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()

    # RSI (14)
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))

    # ATR (14)
    high_low = df['High'] - df['Low']
    high_close = (df['High'] - df['Close'].shift()).abs()
    low_close = (df['Low'] - df['Close'].shift()).abs()
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    df['ATR'] = true_range.rolling(14).mean()

    # Current Bar Values
    cmp = float(df['Close'].iloc[-1])
    ema9 = float(df['EMA_9'].iloc[-1])
    ema21 = float(df['EMA_21'].iloc[-1])
    ema50 = float(df['EMA_50'].iloc[-1])
    rsi = float(df['RSI'].iloc[-1])
    atr = float(df['ATR'].iloc[-1])

    prev_ema9 = float(df['EMA_9'].iloc[-2])
    prev_ema21 = float(df['EMA_21'].iloc[-2])

    # Volatility Sizing (1.2x ATR)
    sl_points = round(max(atr * 1.2, 2.0), 1)
    t1_points = sl_points
    t2_points = round(sl_points * 2, 1)
    t3_points = round(sl_points * 3, 1)

    # Trend Bias
    is_bullish = ema9 > ema21 and cmp > ema9
    if prev_ema9 <= prev_ema21 and ema9 > ema21 and rsi > 48:
        trade_bias = "BUY (FRESH BULLISH CROSSOVER)"
        action_msg = "Fresh BUY ban raha hai. Dip par buy karein."
    elif prev_ema9 >= prev_ema21 and ema9 < ema21 and rsi < 52:
        trade_bias = "SELL (FRESH BEARISH BREAKDOWN)"
        action_msg = "Fresh SELL ban raha hai. Rise par sell karein."
    elif is_bullish:
        trade_bias = "BUY ON DIPS (UPTREND)"
        action_msg = "Market Uptrend me hai. EMA-9/21 ke support par BUY karein."
    else:
        trade_bias = "SELL ON RISE (DOWNTREND)"
        action_msg = "Market Downtrend me hai. Resistance par SELL karein."

    if "BUY" in trade_bias:
        direction = "BUY / LONG"
        dir_key = "BUY"
        entry_range = f"₹{cmp - 0.8:.1f} - ₹{cmp:.1f}"
        sl_price = round(cmp - sl_points, 1)
        t1_price = round(cmp + t1_points, 1)
        t2_price = round(cmp + t2_points, 1)
        t3_price = round(cmp + t3_points, 1)
    else:
        direction = "SELL / SHORT"
        dir_key = "SELL"
        entry_range = f"₹{cmp:.1f} - ₹{cmp + 0.8:.1f}"
        sl_price = round(cmp + sl_points, 1)
        t1_price = round(cmp - t1_points, 1)
        t2_price = round(cmp - t2_points, 1)
        t3_price = round(cmp - t3_points, 1)

    # Calculate Statistical Win Probability
    prob_pct, rating, reasons = calculate_trade_probability(dir_key, cmp, ema9, ema21, ema50, rsi, atr)

    # Format advisory card
    card = []
    card.append("=" * 76)
    card.append("🎯 MCX NATURAL GAS MINI (250 mmBtu LOT) - LIVE TRADE CARD")
    card.append("=" * 76)
    card.append(f"• NatGas Mini Running CMP    : ₹{cmp:.1f}  (1 Lot = 250 mmBtu | Source: {price_source})")
    card.append(f"• Market Trade Bias          : >> {trade_bias} <<")
    card.append(f"• Recommended Entry Zone     : {entry_range}")
    card.append(f"• Global Henry Hub Benchmark : ${cmp_usd:.3f}/mmBtu  (USD/INR: ₹{usdinr:.2f})")
    card.append("-" * 76)
    card.append(f"⚡ RECOMMENDED TRADE DIRECTION: {direction}")
    card.append(f"🎲 PREDICTION CHANCE / WIN PROBABILITY: {prob_pct}% [{rating}]")
    card.append("   • Confluence Analysis:")
    for r in reasons:
        card.append(f"     ✓ {r}")
    card.append("-" * 76)
    card.append(f"🛑 STRICT STOP-LOSS          : ₹{sl_price:.1f}  (Risk: {sl_points:.1f} pts | ₹{sl_points * 250:,.0f} per lot)")
    card.append(f"🎯 TARGET 1 (Safe / 50%)     : ₹{t1_price:.1f}  (+{t1_points:.1f} pts | Profit: ₹{t1_points * 250:,.0f} per lot)")
    card.append(f"🎯 TARGET 2 (Main 1:2 R:R)   : ₹{t2_price:.1f}  (+{t2_points:.1f} pts | Profit: ₹{t2_points * 250:,.0f} per lot)")
    card.append(f"🎯 TARGET 3 (Jackpot Run)    : ₹{t3_price:.1f}  (+{t3_points:.1f} pts | Profit: ₹{t3_points * 250:,.0f} per lot)")
    card.append("-" * 76)
    card.append("💰 NATGAS MINI (250 mmBtu) P&L MATRIX (1 Point Move = ₹250.00):")
    card.append(f"   • 1 Lot  (250 mmBtu) -> Max Loss: -₹{sl_points * 250:,.0f} | T1: +₹{t1_points * 250:,.0f} | T2: +₹{t2_points * 250:,.0f} | T3: +₹{t3_points * 250:,.0f}")
    card.append(f"   • 2 Lots (500 mmBtu) -> Max Loss: -₹{sl_points * 500:,.0f} | T1: +₹{t1_points * 500:,.0f} | T2: +₹{t2_points * 500:,.0f} | T3: +₹{t3_points * 500:,.0f}")
    card.append(f"   • 4 Lots (1000 Mega) -> Max Loss: -₹{sl_points * 1000:,.0f}| T1: +₹{t1_points * 1000:,.0f}| T2: +₹{t2_points * 1000:,.0f}| T3: +₹{t3_points * 1000:,.0f}")
    card.append("-" * 76)
    card.append("📋 TARGET BOOKING STRATEGY (Trailing Rules):")
    card.append(f"   1. Target 1 (₹{t1_price:.1f}) hit hote hi 50% lot book karein.")
    card.append(f"   2. Remaining lot ka Stop-Loss turant Entry Cost (₹{cmp:.1f}) par shift karein (Zero-Risk Trade).")
    card.append(f"   3. Target 2 (₹{t2_price:.1f}) aane par 30% quantity book karein aur SL ko Target 1 par trail karein.")
    card.append(f"   4. Target 3 (₹{t3_price:.1f}) par full exit karein.")
    card.append("-" * 76)
    card.append(f"💡 Technical Analysis        : EMA-9: ₹{ema9:.1f} | EMA-21: ₹{ema21:.1f} | RSI: {rsi:.1f}")
    card.append(f"📌 Guidance                  : {action_msg}")
    card.append("=" * 76 + "\n")

    card_text = "\n".join(card)
    if not return_data:
        print(card_text)
        return card_text

    return {
        "commodity": "Natural Gas Mini (250 mmBtu)",
        "cmp": cmp,
        "direction": dir_key,
        "trade_bias": trade_bias,
        "prob_pct": prob_pct,
        "rating": rating,
        "reasons": reasons,
        "sl_price": sl_price,
        "t1_price": t1_price,
        "t2_price": t2_price,
        "t3_price": t3_price,
        "t1_profit_inr": t1_points * 250.0,
        "sl_risk_inr": sl_points * 250.0,
        "card_text": card_text
    }


if __name__ == "__main__":
    analyze_natgas_trade()
