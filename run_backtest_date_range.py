# python run_backtest_date_range.py
import os
import io
import re
from datetime import datetime, timedelta
from contextlib import redirect_stdout

import MetaTrader5 as mt5
import pandas as pd

from backtest_engine_1h_orb import BacktestEngine1HORB


TOTAL_PAIRS_LIST = [
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

ENABLE_PAIR_LIST = [

    "AUDCAD.ecn",
    "AUDUSD.ecn",
    "EURAUD.ecn",
    "EURCAD.ecn",
    "EURUSD.ecn",
    "EURGBP.ecn",
    "GBPAUD.ecn",
    "GBPCAD.ecn",
    "GBPUSD.ecn",
    "NZDCAD.ecn",
    "NZDUSD.ecn",
    "USDCAD.ecn",

]

DISABLE_PAIR_LIST = [
    pair for pair in TOTAL_PAIRS_LIST
    if pair not in ENABLE_PAIR_LIST
]

invalid_enabled = [
    pair for pair in ENABLE_PAIR_LIST
    if pair not in TOTAL_PAIRS_LIST
]
if invalid_enabled:
    raise ValueError(f"Invalid enabled pairs: {invalid_enabled}")

PAIRS = ENABLE_PAIR_LIST.copy()


INITIAL_FUND = 100.0
INITIAL_RISK = 8.0

DATA_DIR = "."
START_DATE = "2026-06-15"
END_DATE = "2026-06-15"

EXPORT_NAME = f"backtest_{START_DATE}_to_{END_DATE}.xlsx"
TIMEFRAME = mt5.TIMEFRAME_M15

SHOW_FILTERED_1H_LINES = False
SHOW_CAPTURED_ON_ERROR = True

FILTER_KEYWORDS = [
    "ATR gate check",
    "prev1hatr",
    "prev1HATR",
    "prev_1h_atr",
    "reason=prev1hatrinvalid",
    "reason=prev_1h_atr_invalid",
    "breakout candidate ATR gate failed",
    "LOW pattern:",
    "HIGH pattern:",
]


def pair_to_temp_csv(pair: str) -> str:
    pair_name = pair.replace(".", "_")
    return f"_temp_{pair_name}.csv"


def init_mt5() -> bool:
    if not mt5.initialize():
        print("MT5 initialization failed")
        return False

    terminal = mt5.terminal_info()
    print("MT5 initialized")
    if terminal:
        print(f"Terminal: {terminal.name}")
    return True


def fetch_pair_data(pair: str, start_date: str, end_date: str) -> bool:
    start_dt = datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=7)
    end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)

    print(f"\n[{pair}] Fetching data from {start_dt.date()} to {end_dt.date()}")

    rates = mt5.copy_rates_range(pair, TIMEFRAME, start_dt, end_dt)

    if rates is None or len(rates) == 0:
        print(f"  -> No MT5 data returned for {pair}")
        return False

    df = pd.DataFrame(rates)
    df["datetime"] = pd.to_datetime(df["time"], unit="s")
    df = df[["datetime", "open", "high", "low", "close"]].copy()

    output_file = os.path.join(DATA_DIR, pair_to_temp_csv(pair))
    df.to_csv(output_file, index=False)

    print(f"  -> Saved {len(df)} rows to {output_file}")
    print(f"  -> Range: {df['datetime'].min()} to {df['datetime'].max()}")

    return True


def refresh_csv_data(pairs: list[str], start_date: str, end_date: str) -> list[str]:
    updated_pairs = []

    print("\n" + "=" * 70)
    print("REFRESHING CSV DATA FROM MT5")
    print("=" * 70)

    for pair in pairs:
        ok = fetch_pair_data(pair, start_date, end_date)
        if ok:
            updated_pairs.append(pair)

    print("\n" + "=" * 70)
    print(
        f"CSV refresh complete: {len(updated_pairs)}/{len(pairs)} pairs updated")
    print("=" * 70)

    return updated_pairs


def build_specs(data_dir: str, pairs: list[str]) -> list[dict]:
    specs = []
    missing = []

    for pair in pairs:
        filename = pair_to_temp_csv(pair)
        csv_path = os.path.join(data_dir, filename)

        if not os.path.exists(csv_path):
            missing.append((pair, csv_path))
            continue

        specs.append({
            "pair": pair,
            "csv": csv_path,
        })

    if missing:
        print("\nMissing CSVs for these pairs:")
        for pair, path in missing:
            print(f"  - {pair} -> expected: {path}")

    print("\nResolved CSV mapping:")
    for spec in specs:
        print(f"  {spec['pair']} -> {spec['csv']}")

    return specs


def enrich_prev_h1_time(line: str) -> str:
    prev_time_match = re.search(
        r"prev1htime([0-9:\-\s]+)", line, flags=re.IGNORECASE)
    if not prev_time_match:
        prev_time_match = re.search(
            r"prev_1h_time=([0-9:\-\s]+)", line, flags=re.IGNORECASE)

    if not prev_time_match:
        return line

    prev_time = prev_time_match.group(1).strip()

    if "prev1HATR" in line and "(time=" not in line:
        line = re.sub(
            r"(prev1HATR[0-9\.]+)",
            rf"\1 (time={prev_time})",
            line,
            count=1,
            flags=re.IGNORECASE,
        )

    if "prev_1H_ATR=" in line and "(time=" not in line:
        line = re.sub(
            r"(prev_1H_ATR=[0-9\.]+)",
            rf"\1 (time={prev_time})",
            line,
            count=1,
            flags=re.IGNORECASE,
        )

    if "prev_1h_atr=" in line and "(time=" not in line:
        line = re.sub(
            r"(prev_1h_atr=[0-9\.]+)",
            rf"\1 (time={prev_time})",
            line,
            count=1,
            flags=re.IGNORECASE,
        )

    return line


def print_filtered_output(text: str):
    if not SHOW_FILTERED_1H_LINES:
        return

    seen = set()
    last_prev_1h_time = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        m = re.search(r"prev1htime([0-9:\-\s]+)", line, flags=re.IGNORECASE)
        if not m:
            m = re.search(
                r"prev_1h_time=([0-9:\-\s]+)", line, flags=re.IGNORECASE)
        if m:
            last_prev_1h_time = m.group(1).strip()

        if any(keyword.lower() in line.lower() for keyword in FILTER_KEYWORDS):
            if (
                last_prev_1h_time
                and "(time=" not in line
                and ("prev1HATR" in line or "prev_1H_ATR=" in line or "prev_1h_atr=" in line)
            ):
                if "prev1HATR" in line:
                    line = re.sub(
                        r"(prev1HATR[0-9\.]+)",
                        rf"\1 (time={last_prev_1h_time})",
                        line,
                        count=1,
                        flags=re.IGNORECASE,
                    )
                elif "prev_1H_ATR=" in line:
                    line = re.sub(
                        r"(prev_1H_ATR=[0-9\.]+)",
                        rf"\1 (time={last_prev_1h_time})",
                        line,
                        count=1,
                        flags=re.IGNORECASE,
                    )
                elif "prev_1h_atr=" in line:
                    line = re.sub(
                        r"(prev_1h_atr=[0-9\.]+)",
                        rf"\1 (time={last_prev_1h_time})",
                        line,
                        count=1,
                        flags=re.IGNORECASE,
                    )

            line = enrich_prev_h1_time(line)

            if line not in seen:
                print(line)
                seen.add(line)


def run_with_filtered_stdout(func, *args, **kwargs):
    buffer = io.StringIO()

    try:
        with redirect_stdout(buffer):
            result = func(*args, **kwargs)

        captured = buffer.getvalue()
        print_filtered_output(captured)
        return result

    except Exception:
        captured = buffer.getvalue()

        if captured.strip():
            print_filtered_output(captured)

        if SHOW_CAPTURED_ON_ERROR and captured.strip():
            print("\n" + "=" * 70)
            print("CAPTURED INTERNAL OUTPUT BEFORE ERROR")
            print("=" * 70)
            print(captured[-12000:])
            print("=" * 70)

        raise


def main():
    if not init_mt5():
        raise RuntimeError("MT5 not initialized. Open MT5 and login first.")

    try:
        print("\n" + "=" * 70)
        print("PAIR CONFIGURATION")
        print("=" * 70)
        print(f"Total pairs   : {len(TOTAL_PAIRS_LIST)}")
        print(f"Enabled pairs : {len(ENABLE_PAIR_LIST)} -> {ENABLE_PAIR_LIST}")
        print(
            f"Disabled pairs: {len(DISABLE_PAIR_LIST)} -> {DISABLE_PAIR_LIST}")

        updated_pairs = refresh_csv_data(PAIRS, START_DATE, END_DATE)

        if not updated_pairs:
            raise RuntimeError(
                "No pair data fetched from MT5 for selected date range."
            )

        engine = BacktestEngine1HORB(
            initial_fund=INITIAL_FUND,
            initial_risk_percent=INITIAL_RISK,
            pair=updated_pairs[0],
        )

        engine.start_date = datetime.strptime(START_DATE, "%Y-%m-%d").date()
        engine.end_date = datetime.strptime(END_DATE, "%Y-%m-%d").date()

        specs = build_specs(DATA_DIR, updated_pairs)

        if not specs:
            raise RuntimeError(
                "No matching CSV files found for selected pairs."
            )

        print("\n" + "=" * 70)
        print("RUNNING BACKTEST")
        print(f"Date range: {engine.start_date} -> {engine.end_date}")
        print(f"Pairs loaded: {len(specs)}")
        print("=" * 70)

        engine.run_backtest(specs)
        engine.export_to_excel(EXPORT_NAME)

        print("\nBacktest complete.")
        print(f"Trades: {len(engine.trades)}")
        print(f"Final fund: {engine.current_fund:.2f}")
        print(f"Excel exported: backtests/{EXPORT_NAME}")

    finally:
        mt5.shutdown()
        print("MT5 shutdown")


if __name__ == "__main__":
    main()
