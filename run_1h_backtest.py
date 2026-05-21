# run_1h_backtest.py

from backtest_engine_1h_orb import BacktestEngine1HORB
from live_data_mt5 import fetch_live_15m
from order_mt5 import init_mt5, shutdown_mt5
import pandas as pd
"""
BACKTEST / LIVE CHECK CONFIGURATION
"""

# Date Range (MT5 data ke andar se filter)
START_DATE = "2026-04-01"   # YYYY-MM-DD
END_DATE = "2026-04-30"     # YYYY-MM-DD

# Trading Parameters
INITIAL_FUND = 30.0        # Starting capital in dollars
INITIAL_RISK = 8.0         # Risk percentage per trade

# ============================================
# PAIR CONFIGURATION
# ============================================

# Master pair list (all available pairs)
MASTER_PAIRS = [
    "AUDCAD.ecn",
    "AUDUSD.ecn",
    "AUDCHF.ecn",
    "CADCHF.ecn",
    "EURAUD.ecn",
    "EURCAD.ecn",
    "EURCHF.ecn",
    "EURUSD.ecn",
    "EURGBP.ecn",
    "GBPAUD.ecn",
    "GBPCAD.ecn",
    "GBPCHF.ecn",
    "GBPUSD.ecn",
    "NZDCAD.ecn",
    "NZDUSD.ecn",
    "NZDCHF.ecn",
    "USDCAD.ecn",
    "USDCHF.ecn",
]

# Enable list
# Agar empty chhodo ge to system MASTER_PAIRS me se sab lega
# except jo DISABLE_PAIRS me honge
ENABLE_PAIRS = [
    "AUDCAD.ecn",
    "AUDUSD.ecn",
    "AUDCHF.ecn",
    "CADCHF.ecn",
    "EURAUD.ecn",
    "EURCAD.ecn",
    "EURCHF.ecn",
    "EURUSD.ecn",
    "EURGBP.ecn",
    "GBPAUD.ecn",
    "GBPCAD.ecn",
    "GBPCHF.ecn",
    "GBPUSD.ecn",
    "NZDCAD.ecn",
    "NZDUSD.ecn",
    "NZDCHF.ecn",
    "USDCAD.ecn",
    "USDCHF.ecn",
]

# Disable list
DISABLE_PAIRS = [

]

# Final active pair list
if ENABLE_PAIRS:
    PAIRS = [p for p in ENABLE_PAIRS if p in MASTER_PAIRS and p not in DISABLE_PAIRS]
else:
    PAIRS = [p for p in MASTER_PAIRS if p not in DISABLE_PAIRS]

# MT5 se kitne din ka ecn data laye (START_DATE–END_DATE ko cover kare)
LOOKBACK_DAYS = 720

# Output
EXCEL_FILENAME = "backtest_15m_session_orb_results.xlsx"

# ============================================
# NO NEED TO EDIT BELOW THIS LINE
# ============================================

if __name__ == "__main__":
    if not PAIRS:
        raise ValueError(
            "No pairs selected. Please check ENABLE_PAIRS / DISABLE_PAIRS.")

    unknown_enabled = [p for p in ENABLE_PAIRS if p not in MASTER_PAIRS]
    unknown_disabled = [p for p in DISABLE_PAIRS if p not in MASTER_PAIRS]

    if unknown_enabled:
        raise ValueError(
            f"These ENABLE_PAIRS are not in MASTER_PAIRS: {unknown_enabled}")

    if unknown_disabled:
        raise ValueError(
            f"These DISABLE_PAIRS are not in MASTER_PAIRS: {unknown_disabled}")

    enabled_pairs = PAIRS
    disabled_pairs = [p for p in MASTER_PAIRS if p not in PAIRS]

    print("=" * 60)
    print("15M SESSION ORB BACKTEST / LIVE CHECK (MT5 DATA)")
    print("=" * 60)
    print(f"Date Range:     {START_DATE} to {END_DATE}")
    print(f"Initial Fund:   ${INITIAL_FUND}")
    print(f"Base Risk:      {INITIAL_RISK:.1f}% (weekly ramp)")
    print(f"Enabled Pairs:  {', '.join(enabled_pairs)}")
    print(f"Disabled Pairs: {', '.join(disabled_pairs)}")
    print(f"LookbackDays:   {LOOKBACK_DAYS}")
    print(f"Output Excel:   {EXCEL_FILENAME}")
    print("=" * 60)

    # Date helpers
    start_dt = pd.to_datetime(START_DATE)
    end_dt = pd.to_datetime(END_DATE)
    total_weeks = ((end_dt - start_dt).days // 7) + 1
    print(f"Total weeks in range: {total_weeks}")

    final_risk_preview = min(INITIAL_RISK + (total_weeks - 1) * 0.5, 5.0)
    week2_preview = min(INITIAL_RISK + 0.5, 5.0)

    print(
        f"Risk ramp: Week1={INITIAL_RISK:.1f}%, "
        f"Week2={week2_preview:.1f}%, "
        f"... Week{total_weeks}={final_risk_preview:.1f}%"
    )

    init_mt5()
    try:
        engine = BacktestEngine1HORB(
            initial_fund=INITIAL_FUND,
            initial_risk_percent=INITIAL_RISK,
            pair="DUMMY",
        )

        engine.start_date = start_dt.date()
        engine.end_date = end_dt.date()

        specs = []

        for pair in PAIRS:
            temp_csv = f"_temp_{pair.replace('.', '_')}.csv"
            print(f"\nFetching live 15M data for {pair} from MT5...")
            df = fetch_live_15m(pair, lookback_days=LOOKBACK_DAYS)

            df["datetime"] = pd.to_datetime(df["datetime"])
            mask = (
                (df["datetime"].dt.date >= start_dt.date())
                & (df["datetime"].dt.date <= end_dt.date())
            )
            df = df.loc[mask].reset_index(drop=True)

            if df.empty:
                print(f"  -> {pair}: no data in selected date range, skipping")
                continue

            df.to_csv(temp_csv, index=False)
            specs.append({"pair": pair, "csv": temp_csv})

        engine.run_backtest(specs)
        engine.export_to_excel(EXCEL_FILENAME)

        print("\nDone on MT5 live 15M data for selected date range.")
    finally:
        shutdown_mt5()
