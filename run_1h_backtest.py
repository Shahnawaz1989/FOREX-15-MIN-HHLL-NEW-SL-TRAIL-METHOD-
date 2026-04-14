# run_1h_backtest.py

from backtest_engine_1h_orb import BacktestEngine1HORB
from live_data_mt5 import fetch_live_1h
import pandas as pd

"""
BACKTEST / LIVE CHECK CONFIGURATION
"""

# Date Range (MT5 data ke andar se filter)
START_DATE = "2026-03-06"   # YYYY-MM-DD
END_DATE = "2026-04-13"   # YYYY-MM-DD

# Trading Parameters
INITIAL_FUND = 30.0         # Starting capital in dollars
INITIAL_RISK = 8.0          # Risk percentage per trade

# Multi-pair list
PAIRS = [
    "AUDCAD.raw",
    "AUDUSD.raw",
    "EURAUD.raw",
    "EURCAD.raw",
    "EURUSD.raw",
    "EURGBP.raw",
    "GBPAUD.raw",
    "GBPCAD.raw",
    "GBPUSD.raw",
    "NZDCAD.raw",
    "NZDUSD.raw",
]


# MT5 se kitne din ka raw data laye (START_DATE–END_DATE ko cover kare)
LOOKBACK_DAYS = 360

# Output
EXCEL_FILENAME = "backtest_1h_orb_results.xlsx"


# ============================================
# NO NEED TO EDIT BELOW THIS LINE
# ============================================

if __name__ == "__main__":
    print("=" * 60)
    print("1H ORB BACKTEST / LIVE CHECK (MT5 DATA)")
    print("=" * 60)
    print(f"Date Range:    {START_DATE} to {END_DATE}")
    print(f"Initial Fund:  ${INITIAL_FUND}")
    print(f"Base Risk:     8.0% (weekly ramp)")
    print(f"Pairs:         {', '.join(PAIRS)}")
    print(f"LookbackDays:  {LOOKBACK_DAYS}")
    print(f"Output Excel:  {EXCEL_FILENAME}")
    print("=" * 60)

    # Date helpers
    start_dt = pd.to_datetime(START_DATE)
    end_dt = pd.to_datetime(END_DATE)
    total_weeks = ((end_dt - start_dt).days // 7) + 1
    print(f"Total weeks in range: {total_weeks}")
    print(
        "Risk ramp: Week1=8%, Week2=9%, "
        f"... Week{total_weeks}={8 + total_weeks - 1}%"
    )

    # Engine init (pair placeholder, har spec me overwrite hoga)
    engine = BacktestEngine1HORB(
        initial_fund=INITIAL_FUND,
        initial_risk_percent=8.0,   # base risk (Week 1)
        pair="DUMMY",
    )
    # date range info engine ko do (weekly calc ke liye)
    engine.start_date = start_dt.date()
    engine.end_date = end_dt.date()
    engine.base_risk_percent = 8.0  # ensure field set

    specs = []

    for pair in PAIRS:
        temp_csv = f"_temp_{pair.replace('.', '_')}.csv"
        print(f"\nFetching live 1H data for {pair} from MT5...")
        df = fetch_live_1h(pair, lookback_days=LOOKBACK_DAYS)

        df["datetime"] = pd.to_datetime(df["datetime"])
        mask = (
            (df["datetime"].dt.date >= start_dt.date())
            & (df["datetime"].dt.date <= end_dt.date())
        )
        df = df.loc[mask].reset_index(drop=True)
        df.to_csv(temp_csv, index=False)

        specs.append({"pair": pair, "csv": temp_csv})

    engine.run_backtest(specs)
    engine.export_to_excel(EXCEL_FILENAME)

    print("\nDone on MT5 live 1H data for selected date range.")
