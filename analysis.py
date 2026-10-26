import time
import pandas as pd
import yfinance as yf
import warnings

warnings.filterwarnings('ignore')




def analyze_commodity(ticker='SI=F', name='SILVER'):
  # 5-day historical data (15-minute timeframe)
  df = yf.download(ticker, period='5d', interval='15m', progress=False)

  if df.empty:
    print(f'Data not available for {name}')
    return

  # Fix multi-index agar yfinance format alag de
  if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)

  # Technical Indicators
  df['EMA_20'] = df['Close'].ewm(span=20, adjust=False).mean()
  df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()

  # RSI (14)
  delta = df['Close'].diff()
  gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
  loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
  rs = gain / loss
  df['RSI'] = 100 - (100 / (1 + rs))

  # Recent candles for crossover detection
  current = df.iloc[-1]
  prev = df.iloc[-2]

  cmp = float(current['Close'])
  ema20 = float(current['EMA_20'])
  ema50 = float(current['EMA_50'])
  rsi = float(current['RSI'])

  prev_ema20 = float(prev['EMA_20'])
  prev_ema50 = float(prev['EMA_50'])

  # --- Decision & Reason Engine ---
  signal = 'NO TRADE / WAIT'
  reason = 'Price is consolidating. No strong breakout or momentum.'

  # Bullish Logic
  if prev_ema20 <= prev_ema50 and ema20 > ema50:
    if rsi < 65:
      signal = 'BUY / LONG'
      reason = f'Fresh Bullish Crossover (EMA 20 crossed above EMA 50) and RSI ({rsi:.1f}) has room to run.'
    else:
      signal = 'WAIT'
      reason = f'Bullish Crossover happened, but RSI is overbought ({rsi:.1f}). High risk of reversal.'

  # Bearish Logic
  elif prev_ema20 >= prev_ema50 and ema20 < ema50:
    if rsi > 35:
      signal = 'SELL / SHORT'
      reason = f'Fresh Bearish Crossover (EMA 20 crossed below EMA 50) with confirming downward RSI ({rsi:.1f}).'
    else:
      signal = 'WAIT'
      reason = (
          f'Bearish Crossover happened, but RSI is oversold ({rsi:.1f}).'
          ' Pullback expected.'
      )

  # Continuing Trend
  elif ema20 > ema50:
    signal = 'BULLISH BIAS'
    reason = f'Uptrend intact (EMA 20 > EMA 50). RSI is at {rsi:.1f}.'
  elif ema20 < ema50:
    signal = 'BEARISH BIAS'
    reason = f'Downtrend intact (EMA 20 < EMA 50). RSI is at {rsi:.1f}.'

  # --- Clean Terminal Display ---
  print('\n' + '=' * 60)
  print(f'📊 COMMODITY ANALYSIS: {name} (CMP: ₹{cmp:,.2f})')
  print('=' * 60)
  print(f'• EMA 20       : {ema20:.2f}')
  print(f'• EMA 50       : {ema50:.2f}')
  print(f'• RSI (14)     : {rsi:.2f}')
  print(f'• TREND        : {"UPTREND" if ema20 > ema50 else "DOWNTREND"}')
  print('-' * 60)
  print(f'⚡ SIGNAL      : {signal}')
  print(f'💡 PYTHON REASON: {reason}')
  print('=' * 60)


if __name__ == '__main__':
  # Tickers: Silver = 'SI=F', Gold = 'GC=F', Crude Oil = 'CL=F'
  commodities = [('SI=F', 'SILVER'), ('GC=F', 'GOLD'), ('CL=F', 'CRUDE OIL')]

  print('Starting Commodity Analysis Engine...')
  for ticker, name in commodities:
    analyze_commodity(ticker, name)