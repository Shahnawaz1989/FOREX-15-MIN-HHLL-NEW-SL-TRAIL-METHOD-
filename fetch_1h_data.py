# save as fetch_1h_data.py

import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime

PAIR = "EURUSD"
SYMBOL = "EURUSD.raw"   # ya jo bhi broker ka exact symbol hai
CSV_OUT = "eurusd_1h_backtest.csv"

if not mt5.initialize():
    print("MT5 init failed")
    quit()

start = datetime(2026, 2, 1)   # server time start
end = datetime(2026, 3, 15)  # server time end

rates = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_H1, start, end)
if rates is None or len(rates) == 0:
    print("No data")
    mt5.shutdown()
    quit()

df = pd.DataFrame(rates)
df["datetime"] = pd.to_datetime(df["time"], unit="s")  # server time
df_clean = df[["datetime", "open", "high", "low", "close"]].copy()
df_clean.to_csv(CSV_OUT, index=False)

mt5.shutdown()
print(f"Saved {len(df_clean)} candles to {CSV_OUT}")
