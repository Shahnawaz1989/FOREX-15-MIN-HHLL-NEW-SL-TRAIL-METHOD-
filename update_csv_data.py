import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta

PAIRS = [
    "AUDCAD.ecn", "AUDUSD.ecn", "EURAUD.ecn", "EURCAD.ecn",
    "EURUSD.ecn", "EURGBP.ecn", "GBPAUD.ecn", "GBPCAD.ecn",
    "GBPUSD.ecn", "NZDCAD.ecn", "NZDUSD.ecn",
]
TIMEFRAME = mt5.TIMEFRAME_M15
START_DATE = "2026-05-01"
END_DATE = "2026-05-30"


def init_mt5():
    if not mt5.initialize():
        print("MT5 initialization failed")
        return False
    print(f"MT5 initialized")
    return True


def fetch_and_save(symbol, timeframe, start_date, end_date):
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)

    print(f"\n[{symbol}] Fetching from {start_dt.date()} to {end_dt.date()}...")

    rates = mt5.copy_rates_range(symbol, timeframe, start_dt, end_dt)

    if rates is None or len(rates) == 0:
        print(f"  ❌ No data for {symbol}")
        return False

    df = pd.DataFrame(rates)
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    df = df[['datetime', 'open', 'high', 'low', 'close']]

    output_file = f"_temp_{symbol.replace('.', '_')}.csv"
    df.to_csv(output_file, index=False)

    print(f"  ✅ {len(df)} rows saved to {output_file}")
    return True


def main():
    if not init_mt5():
        return

    success_count = 0

    try:
        for pair in PAIRS:
            if fetch_and_save(pair, TIMEFRAME, START_DATE, END_DATE):
                success_count += 1

        print(f"\n{'='*60}")
        print(f"Completed: {success_count}/{len(PAIRS)} pairs updated")
        print(f"{'='*60}")

    finally:
        mt5.shutdown()
        print("MT5 shutdown")


if __name__ == "__main__":
    main()
