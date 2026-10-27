import requests
import re
import json

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
}

print("=== 1. Testing Moneycontrol ===")
try:
    url = "https://priceapi.moneycontrol.com/pricefeed/mcx/commodityfutures/CRUDEOIL?expiry=2026-11-19"
    r = requests.get(url, headers=headers, timeout=5)
    print("Moneycontrol Crude:", r.status_code, r.json().get('data', {}).get('lastPrice'))
except Exception as e:
    print("Moneycontrol Error:", e)

print("\n=== 2. Testing Groww Commodity ===")
try:
    url = "https://groww.in/v1/api/stocks_data/v1/accord_points/exchange/MCX/segment/FUTCOM/latest_prices_fo"
    # Or groww public page
    r = requests.get("https://groww.in/commodities/crude-oil", headers=headers, timeout=5)
    print("Groww Crude Page:", r.status_code)
    m = re.findall(r'₹\s*([0-9,]+(?:\.[0-9]+)?)', r.text)
    if m:
        print("Groww matches:", m[:5])
except Exception as e:
    print("Groww Error:", e)

print("\n=== 3. Testing MCX India ===")
try:
    r = requests.get("https://www.mcxindia.com/market-data/get-market-watch", headers=headers, timeout=5)
    print("MCX India:", r.status_code)
except Exception as e:
    print("MCX India Error:", e)

print("\n=== 4. Testing Yahoo Finance MCX Tickers ===")
try:
    import yfinance as yf
    for t in ["CRUDEOIL24NOVFUT.MCX", "CRUDEOIL.MCX", "SILVER.MCX", "NATURALGAS.MCX"]:
        p = yf.Ticker(t).fast_info.get('lastPrice')
        print(f"Yahoo {t}:", p)
except Exception as e:
    print("Yahoo MCX Error:", e)
