import sys
import pandas as pd
import yfinance as yf
from datetime import datetime
import warnings

warnings.filterwarnings('ignore')

from mcx_feed import get_live_mcx_price, calculate_trade_probability


def analyze_silver_trade(custom_cmp=None):
    user_override_cmp = custom_cmp
    if user_override_cmp is None and len(sys.argv) > 1:
        try:
            user_override_cmp = float(sys.argv[1].replace(',', '').replace('₹', ''))
            print(f"\n[+] Broker Live CMP received: ₹{user_override_cmp:,.0f} (Anchoring targets to this exact price)...")
        except ValueError:
            user_override_cmp = None

    if user_override_cmp is None:
        print("\n[+] MCX SILVER MICRO (1 KG) Live Targets & Setup Analyze ho raha hai...\n")
    
    # 1. Fetch benchmark data (15-Minute Intraday Candlesticks)
    try:
        df = yf.download("SI=F", period="5d", interval="15m", progress=False)
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

    # Global benchmark price in USD
    cmp_usd = float(df['Close'].iloc[-1].item() if hasattr(df['Close'].iloc[-1], 'item') else df['Close'].iloc[-1])

    # 3. Live MCX Market Price Determination (Exchange Feed > Custom > Calibrated)
    market_feed_price = get_live_mcx_price('silver_micro')
    if user_override_cmp is not None:
        target_cmp = user_override_cmp
        price_source = "User Broker Input"
    elif market_feed_price is not None:
        target_cmp = market_feed_price
        price_source = "MCX Live Exchange Feed"
    else:
        target_cmp = cmp_usd * usdinr * (1000.0 / 31.1034768) * 1.1953
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

    # Volatility Sizing (1.2x ATR for SL)
    sl_points = round(max(atr * 1.2, 250.0))
    t1_points = sl_points
    t2_points = sl_points * 2
    t3_points = sl_points * 3

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

    # Direction Levels
    if "BUY" in trade_bias:
        direction = "BUY / LONG"
        dir_key = "BUY"
        entry_range = f"₹{cmp - 150:,.0f} - ₹{cmp:,.0f}"
        sl_price = cmp - sl_points
        t1_price = cmp + t1_points
        t2_price = cmp + t2_points
        t3_price = cmp + t3_points
    else:
        direction = "SELL / SHORT"
        dir_key = "SELL"
        entry_range = f"₹{cmp:,.0f} - ₹{cmp + 150:,.0f}"
        sl_price = cmp + sl_points
        t1_price = cmp - t1_points
        t2_price = cmp - t2_points
        t3_price = cmp - t3_points

    # Calculate Statistical Win Probability
    prob_pct, rating, reasons = calculate_trade_probability(dir_key, cmp, ema9, ema21, ema50, rsi, atr)

    # Format advisory card
    card = []
    card.append("=" * 76)
    card.append("🎯 MCX SILVER MICRO (1 KG LOT) - LIVE TARGET & TRADE CARD")
    card.append("=" * 76)
    card.append(f"• Silver Micro Running CMP : ₹{cmp:,.0f}  (1 Lot = 1 Kg | Source: {price_source})")
    card.append(f"• Market Trade Bias        : >> {trade_bias} <<")
    card.append(f"• Recommended Entry Zone   : {entry_range}")
    card.append(f"• Global COMEX Benchmark   : ${cmp_usd:.2f}/oz  (USD/INR: ₹{usdinr:.2f})")
    card.append("-" * 76)
    card.append(f"⚡ RECOMMENDED TRADE DIRECTION: {direction}")
    card.append(f"🎲 PREDICTION CHANCE / WIN PROBABILITY: {prob_pct}% [{rating}]")
    card.append("   • Confluence Analysis:")
    for r in reasons:
        card.append(f"     ✓ {r}")
    card.append("-" * 76)
    card.append(f"🛑 STRICT STOP-LOSS        : ₹{sl_price:,.0f}  (Risk: {sl_points:,.0f} pts | ₹{sl_points:,.0f} per lot)")
    card.append(f"🎯 TARGET 1 (Safe / 50%)   : ₹{t1_price:,.0f}  (+{t1_points:,.0f} pts | Profit: ₹{t1_points:,.0f} per lot)")
    card.append(f"🎯 TARGET 2 (Main 1:2 R:R) : ₹{t2_price:,.0f}  (+{t2_points:,.0f} pts | Profit: ₹{t2_points:,.0f} per lot)")
    card.append(f"🎯 TARGET 3 (Jackpot Run)  : ₹{t3_price:,.0f}  (+{t3_points:,.0f} pts | Profit: ₹{t3_points:,.0f} per lot)")
    card.append("-" * 76)
    card.append("💰 SILVER MICRO (1 KG) P&L MATRIX (1 Point Move = ₹1.00):")
    card.append(f"   • 1 Lot (1 Kg)  -> Max Loss: -₹{sl_points:,.0f}  | T1: +₹{t1_points:,.0f} | T2: +₹{t2_points:,.0f} | T3: +₹{t3_points:,.0f}")
    card.append(f"   • 2 Lots (2 Kg) -> Max Loss: -₹{sl_points * 2:,.0f} | T1: +₹{t1_points * 2:,.0f} | T2: +₹{t2_points * 2:,.0f} | T3: +₹{t3_points * 2:,.0f}")
    card.append(f"   • 5 Lots (5 Kg) -> Max Loss: -₹{sl_points * 5:,.0f} | T1: +₹{t1_points * 5:,.0f} | T2: +₹{t2_points * 5:,.0f} | T3: +₹{t3_points * 5:,.0f}")
    card.append("-" * 76)
    card.append("📋 TARGET BOOKING STRATEGY (Trailing Rules):")
    card.append(f"   1. Target 1 (₹{t1_price:,.0f}) aate hi 50% quantity book karein.")
    card.append(f"   2. Remaining lot ka Stop-Loss turant Cost (₹{cmp:,.0f}) par shift karein (Zero-Risk Trade).")
    card.append(f"   3. Target 2 (₹{t2_price:,.0f}) par 30% book karein aur SL ko Target 1 par trail karein.")
    card.append(f"   4. Target 3 (₹{t3_price:,.0f}) par full exit karein.")
    card.append("-" * 76)
    card.append(f"💡 Technical Analysis      : EMA-9: ₹{ema9:,.0f} | EMA-21: ₹{ema21:,.0f} | RSI: {rsi:.1f}")
    card.append(f"📌 Guidance                : {action_msg}")
    card.append("=" * 76 + "\n")

    card_text = "\n".join(card)
    print(card_text)
    return card_text


if __name__ == "__main__":
    analyze_silver_trade()