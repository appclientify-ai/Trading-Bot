import sys
import os
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.abspath('.'))

from mcx_feed import get_intraday_candles, get_live_mcx_price
from smc_engine import COMMODITY_SPECS, calculate_rsi, find_swing_pivots, find_order_blocks

def analyze_4angle_mtf(spec_key, target_cmp, sym):
    spec = COMMODITY_SPECS.get(spec_key, {})
    buffer_pts = spec.get('buffer', 10.0)

    # 1. 1-Hour (1H) Institutional Macro Trend
    trend_1h = "NEUTRAL ⚪"
    ema20_1h = target_cmp
    ema50_1h = target_cmp
    try:
        df_1h = get_intraday_candles(sym, period="14d", interval="1h")
        if df_1h is not None and not df_1h.empty:
            c1h = float(df_1h['Close'].iloc[-1])
            scale_1h = target_cmp / c1h
            close_1h = df_1h['Close'] * scale_1h
            ema20_1h = float(close_1h.ewm(span=20, adjust=False).mean().iloc[-1])
            ema50_1h = float(close_1h.ewm(span=50, adjust=False).mean().iloc[-1])
            if target_cmp > ema20_1h > ema50_1h:
                trend_1h = "BULLISH 🟢 (EMA 20/50 Uptrend)"
            elif target_cmp < ema20_1h < ema50_1h:
                trend_1h = "BEARISH 🔴 (EMA 20/50 Downtrend)"
            elif target_cmp >= ema50_1h:
                trend_1h = "BULLISH_BIAS 🟢 (Above EMA 50)"
            else:
                trend_1h = "BEARISH_BIAS 🔴 (Below EMA 50)"
    except Exception as e:
        trend_1h = "UNAVAILABLE ⚪"

    is_1h_bull = "BULL" in trend_1h
    is_1h_bear = "BEAR" in trend_1h

    # 2. 30-Minute (30m) Dealing Range & Reversal Traps
    range_high_30 = target_cmp * 1.01
    range_low_30 = target_cmp * 0.99
    eq_30 = target_cmp
    bull_obs_30 = []
    bear_obs_30 = []
    reversal_trap_buy = False
    reversal_trap_sell = False
    try:
        df_30 = get_intraday_candles(sym, period="7d", interval="30m")
        if df_30 is not None and not df_30.empty:
            c30 = float(df_30['Close'].iloc[-1])
            scale_30 = target_cmp / c30
            for col in ['Open', 'High', 'Low', 'Close']:
                df_30[col] = df_30[col] * scale_30
            sh_30, sl_30 = find_swing_pivots(df_30, window=3)
            bull_obs_30, bear_obs_30 = find_order_blocks(df_30, sh_30, sl_30)
            if sh_30: range_high_30 = max(sh['price'] for sh in sh_30[-3:])
            if sl_30: range_low_30 = min(sl['price'] for sl in sl_30[-3:])
            eq_30 = (range_high_30 + range_low_30) / 2.0

            # Trap check: Is CMP inside or directly below 30m Supply OB?
            for ob in bear_obs_30:
                if (target_cmp >= ob['bottom'] - buffer_pts * 0.5) and (target_cmp <= ob['top'] + buffer_pts * 0.5):
                    reversal_trap_buy = True
                    break
            # Trap check: Is CMP inside or directly above 30m Demand OB?
            for ob in bull_obs_30:
                if (target_cmp <= ob['top'] + buffer_pts * 0.5) and (target_cmp >= ob['bottom'] - buffer_pts * 0.5):
                    reversal_trap_sell = True
                    break
    except Exception:
        pass

    # 3. 15-Minute (15m) ATR Volatility Spike & Wick Exhaustion
    atr14 = max(buffer_pts, 1.0)
    vol_ratio = 1.0
    is_sharp_volatility = False
    erratic_wick = None
    try:
        df_15 = get_intraday_candles(sym, period="5d", interval="15m")
        if df_15 is not None and not df_15.empty:
            c15 = float(df_15['Close'].iloc[-1])
            scale_15 = target_cmp / c15
            for col in ['Open', 'High', 'Low', 'Close']:
                df_15[col] = df_15[col] * scale_15

            tr = np.maximum(df_15['High'] - df_15['Low'], 
                            np.maximum(abs(df_15['High'] - df_15['Close'].shift(1)), 
                                       abs(df_15['Low'] - df_15['Close'].shift(1))))
            atr14 = float(tr.rolling(14).mean().iloc[-1])
            last_bar_range = float(df_15['High'].iloc[-1] - df_15['Low'].iloc[-1])
            vol_ratio = last_bar_range / max(atr14, 1e-4)
            is_sharp_volatility = (vol_ratio >= 2.2)

            b_high = float(df_15['High'].iloc[-1])
            b_low = float(df_15['Low'].iloc[-1])
            b_open = float(df_15['Open'].iloc[-1])
            b_close = float(df_15['Close'].iloc[-1])
            span = max(b_high - b_low, 1e-4)
            u_wick = b_high - max(b_open, b_close)
            l_wick = min(b_open, b_close) - b_low
            if (u_wick / span) >= 0.65:
                erratic_wick = "UPPER_WICK_REJECTION"
            elif (l_wick / span) >= 0.65:
                erratic_wick = "LOWER_WICK_REJECTION"
    except Exception:
        pass

    # 4. 5-Minute (5m) Micro Trigger (MSS Confirmation)
    ema9_5 = target_cmp
    ema21_5 = target_cmp
    rsi_5 = 50.0
    trigger_5m_bull = False
    trigger_5m_bear = False
    try:
        df_5 = get_intraday_candles(sym, period="2d", interval="5m")
        if df_5 is not None and not df_5.empty:
            c5 = float(df_5['Close'].iloc[-1])
            scale_5 = target_cmp / c5
            close_5 = df_5['Close'] * scale_5
            ema9_5 = float(close_5.ewm(span=9, adjust=False).mean().iloc[-1])
            ema21_5 = float(close_5.ewm(span=21, adjust=False).mean().iloc[-1])
            rsi_5 = float(calculate_rsi(close_5).iloc[-1])
            trigger_5m_bull = (ema9_5 >= ema21_5 or close_5.iloc[-1] >= (df_5['Open'].iloc[-1] * scale_5)) and (rsi_5 >= 42.0)
            trigger_5m_bear = (ema9_5 <= ema21_5 or close_5.iloc[-1] <= (df_5['Open'].iloc[-1] * scale_5)) and (rsi_5 <= 58.0)
    except Exception:
        pass

    # Pre-Warnings & Trap Assessment
    buy_traps = []
    sell_traps = []

    if is_1h_bear:
        buy_traps.append(f"1H Institutional Trend is BEARISH (EMA20: ₹{ema20_1h:.1f} < EMA50: ₹{ema50_1h:.1f}) — Counter-trend Trap!")
    if is_1h_bull:
        sell_traps.append(f"1H Institutional Trend is BULLISH (EMA20: ₹{ema20_1h:.1f} > EMA50: ₹{ema50_1h:.1f}) — Counter-trend Trap!")

    if reversal_trap_buy:
        buy_traps.append("Price hitting 30m Institutional Supply Base / Overhead Resistance!")
    if reversal_trap_sell:
        sell_traps.append("Price hitting 30m Institutional Demand Base / Floor Support!")

    if is_sharp_volatility:
        buy_traps.append(f"15m Volatility Spike ({vol_ratio:.1f}x ATR) — High risk of erratic whipsaw!")
        sell_traps.append(f"15m Volatility Spike ({vol_ratio:.1f}x ATR) — High risk of erratic whipsaw!")

    if erratic_wick == "UPPER_WICK_REJECTION":
        buy_traps.append("15m Long Upper Rejection Wick (65%+ of candle) — Heavy institutional selling!")
    elif erratic_wick == "LOWER_WICK_REJECTION":
        sell_traps.append("15m Long Lower Rejection Wick (65%+ of candle) — Heavy institutional buying!")

    if not trigger_5m_bull:
        buy_traps.append(f"5m Micro Momentum is falling (EMA9 < EMA21, RSI: {rsi_5:.1f}) — Falling knife trap!")
    if not trigger_5m_bear:
        sell_traps.append(f"5m Micro Momentum is surging (EMA9 > EMA21, RSI: {rsi_5:.1f}) — Squeeze trap!")

    vol_badge = "⚠️ SPIKE" if is_sharp_volatility else "🟢 CLEAN"
    m5_badge = "🟢 BULL" if trigger_5m_bull else ("🔴 BEAR" if trigger_5m_bear else "⚪ MIXED")
    regime_30 = "🟢 DISCOUNT" if target_cmp < eq_30 else "🔴 PREMIUM"

    radar_table = (
        f"🎯 <b>4-ANGLE MULTI-TIMEFRAME RADAR:</b>\n"
        f"• <b>1-Hour (1H)  :</b> {trend_1h}\n"
        f"• <b>30-Min (30m) :</b> {regime_30} (Eq: ₹{eq_30:,.1f})\n"
        f"• <b>15-Min (15m) :</b> {vol_badge} (Vol: {vol_ratio:.2f}x ATR | Wick: {erratic_wick or 'Normal'})\n"
        f"• <b>5-Min (5m)   :</b> {m5_badge} (EMA9: ₹{ema9_5:.1f} | RSI: {rsi_5:.1f})"
    )

    return {
        'trend_1h': trend_1h,
        'is_1h_bull': is_1h_bull,
        'is_1h_bear': is_1h_bear,
        'range_high_30m': range_high_30,
        'range_low_30m': range_low_30,
        'equilibrium_30m': eq_30,
        'bull_obs_30m': len(bull_obs_30),
        'bear_obs_30m': len(bear_obs_30),
        'reversal_trap_buy': reversal_trap_buy,
        'reversal_trap_sell': reversal_trap_sell,
        'atr14': atr14,
        'vol_ratio': vol_ratio,
        'is_sharp_volatility': is_sharp_volatility,
        'erratic_wick': erratic_wick,
        'trigger_5m_bull': trigger_5m_bull,
        'trigger_5m_bear': trigger_5m_bear,
        'buy_traps': buy_traps,
        'sell_traps': sell_traps,
        'is_safe_buy': len(buy_traps) == 0,
        'is_safe_sell': len(sell_traps) == 0,
        'radar_table': radar_table
    }

for comm in ['crude', 'silver', 'natgas']:
    cmp = get_live_mcx_price(comm) or 6000.0
    res = analyze_4angle_mtf(comm, cmp, COMMODITY_SPECS[comm]['sym'])
    print(f"\n=================== {comm.upper()} ===================")
    print(res['radar_table'])
    print(f"Safe to BUY?  {res['is_safe_buy']} | Traps: {res['buy_traps']}")
    print(f"Safe to SELL? {res['is_safe_sell']} | Traps: {res['sell_traps']}")
