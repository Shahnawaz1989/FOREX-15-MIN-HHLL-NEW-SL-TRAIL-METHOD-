from live_data_mt5 import fetch_live_1h

PAIR = "EURUSD.ecn"

df = fetch_live_1h(PAIR, lookback_days=10)
print(df.tail())
print("Rows:", len(df))
