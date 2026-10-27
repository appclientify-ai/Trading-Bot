import sys
import os
import pandas as pd
import numpy as np

# Add repo to sys.path
sys.path.insert(0, os.path.abspath('.'))

from mcx_feed import get_intraday_candles, get_live_mcx_price
from smc_engine import COMMODITY_SPECS, calculate_rsi, find_swing_pivots, find_order_blocks

def test_mtf(comm_key):
    spec = COMMODITY_SPECS[comm_key]
    sym = spec['sym']
    cmp = get_live_mcx_price(comm_key) or 6000.0
    
    print(f"\n=================== TESTING {comm_key.upper()} (CMP: {cmp}) ===================")
    
    # 1. 1-Hour
    df_1h = get_intraday_candles(sym, period="14d", interval="1h")
    if not df_1h.empty:
        c1h = float(df_1h['Close'].iloc[-1])
        scale_1h = cmp / c1h
        close_1h = df_1h['Close'] * scale_1h
        ema20_1h = float(close_1h.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50_1h = float(close_1h.ewm(span=50, adjust=False).mean().iloc[-1])
        trend_1h = "BULLISH 🟢" if ema20_1h > ema50_1h else "BEARISH 🔴"
        print(f"1H Trend: {trend_1h} (EMA20: {ema20_1h:.1f}, EMA50: {ema50_1h:.1f})")
    
    # 2. 30-Minute
    df_30m = get_intraday_candles(sym, period="7d", interval="30m")
    if not df_30m.empty:
        c30 = float(df_30m['Close'].iloc[-1])
        scale_30 = cmp / c30
        for col in ['Open', 'High', 'Low', 'Close']:
            df_30m[col] = df_30m[col] * scale_30
        sh_30, sl_30 = find_swing_pivots(df_30m, window=4)
        bull_30, bear_30 = find_order_blocks(df_30m, sh_30, sl_30)
        print(f"30m Range: OBs Bull={len(bull_30)}, Bear={len(bear_30)}")
        if bear_30:
            print(f"  Nearest 30m Bearish OB (Supply Ceiling): Top={bear_30[-1]['top']:.1f}, Bottom={bear_30[-1]['bottom']:.1f}")
        if bull_30:
            print(f"  Nearest 30m Bullish OB (Demand Floor): Top={bull_30[-1]['top']:.1f}, Bottom={bull_30[-1]['bottom']:.1f}")
            
    # 3. 15-Minute
    df_15m = get_intraday_candles(sym, period="5d", interval="15m")
    if not df_15m.empty:
        c15 = float(df_15m['Close'].iloc[-1])
        scale_15 = cmp / c15
        for col in ['Open', 'High', 'Low', 'Close']:
            df_15m[col] = df_15m[col] * scale_15
        tr = np.maximum(df_15m['High'] - df_15m['Low'], 
                        np.maximum(abs(df_15m['High'] - df_15m['Close'].shift(1)), 
                                   abs(df_15m['Low'] - df_15m['Close'].shift(1))))
        atr14 = float(tr.rolling(14).mean().iloc[-1])
        last_range = float(df_15m['High'].iloc[-1] - df_15m['Low'].iloc[-1])
        vol_ratio = last_range / max(atr14, 1e-4)
        sharp_vol = vol_ratio >= 2.2
        print(f"15m ATR: {atr14:.2f}, Last Candle Range: {last_range:.2f} (Ratio: {vol_ratio:.2f}x) -> Sharp Vol: {sharp_vol}")
        
    # 4. 5-Minute
    df_5m = get_intraday_candles(sym, period="2d", interval="5m")
    if not df_5m.empty:
        c5 = float(df_5m['Close'].iloc[-1])
        scale_5 = cmp / c5
        close_5 = df_5m['Close'] * scale_5
        ema9_5 = float(close_5.ewm(span=9, adjust=False).mean().iloc[-1])
        ema21_5 = float(close_5.ewm(span=21, adjust=False).mean().iloc[-1])
        rsi_5 = float(calculate_rsi(close_5).iloc[-1])
        m_bias = "BULLISH 🟢" if ema9_5 >= ema21_5 else "BEARISH 🔴"
        print(f"5m Micro: {m_bias} (EMA9: {ema9_5:.1f}, EMA21: {ema21_5:.1f}, RSI: {rsi_5:.1f})")

test_mtf('crude')
test_mtf('silver')
test_mtf('natgas')
test_mtf('gold')
