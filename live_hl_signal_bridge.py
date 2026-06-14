from datetime import datetime
import os
import json
import pandas as pd

from backtest_engine_1h_orb import BacktestEngine1HORB
from live_data_mt5 import fetch_live_15m, fetch_live_1h, fetch_live_1m
from mt5_atr_bridge import fetch_mt5_h1_m15_atr
from order_mt5 import init_mt5, shutdown_mt5
from live_cleanup import run_startup_cleanup
from live_registry_file_sync import sync_registry_from_mt5_files_for_pair_day


HEARTBEAT_FILE = r"C:\trading_bot\heartbeats\live_runner_heartbeat.json"

PAIRS = [

    "EURUSD.ecn",

]

INITIAL_FUND = 100.0
INITIAL_RISK = 8.0
DEFAULT_PAIR = PAIRS[0]
LOOKBACK_DAYS = 30
MAX_SPREAD_POINTS = 25
MAX_SLIPPAGE_POINTS = 15

SIGNAL_DIR = r"C:\Users\Uzair Khan\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5\Files"
REGISTRY_FILE = r"live_registry/hl_live_registry.json"


def write_heartbeat(stage="alive", extra=None):
    os.makedirs(os.path.dirname(HEARTBEAT_FILE), exist_ok=True)

    payload = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stage": stage,
        "pid": os.getpid(),
    }

    if extra and isinstance(extra, dict):
        payload.update(extra)

    tmp = HEARTBEAT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)

    os.replace(tmp, HEARTBEAT_FILE)


def process_pair(engine: BacktestEngine1HORB, pair: str):
    write_heartbeat("processing_pair", {"pair": pair})

    print("\n" + "=" * 60)
    print(f"Processing live HL dual signals for {pair}")

    # 1) Raw 15M OHLC from MT5
    df = fetch_live_15m(pair, lookback_days=LOOKBACK_DAYS)

    if df is None or df.empty:
        print("  -> No MT5 15m data")
        return

    df = df.copy()
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df.dropna(subset=["datetime"]).sort_values(
        "datetime").reset_index(drop=True)

    if df.empty:
        print("  -> Data empty after datetime parsing")
        return

    latest_day = df["datetime"].dt.date.max()
    print(f"  -> {pair} max datetime in 15m df = {df['datetime'].max()}")
    print(df[["datetime", "open", "high", "low", "close"]].tail(5))

    # 2) MT5 ATR bridge fetch for selected/latest day
    atr_pack = fetch_mt5_h1_m15_atr(
        symbol=pair,
        day=datetime.combine(latest_day, datetime.min.time()),
        atr_period=14,
        timeout_sec=30,
    )

    m15atr = atr_pack.get("m15", pd.DataFrame())
    h1atr = atr_pack.get("h1", pd.DataFrame())

    if m15atr is None or m15atr.empty:
        print("  -> No MT5 15m ATR bridge data")
        return

    if h1atr is None or h1atr.empty:
        print("  -> No MT5 1h ATR bridge data")
        return

    # 3) Normalize bridge data
    m15atr = m15atr.copy()
    h1atr = h1atr.copy()

    # Normalize time column to 'datetime'
    if "time" in m15atr.columns and "datetime" not in m15atr.columns:
        m15atr = m15atr.rename(columns={"time": "datetime"})
    if "time" in h1atr.columns and "datetime" not in h1atr.columns:
        h1atr = h1atr.rename(columns={"time": "datetime"})

    m15atr["datetime"] = pd.to_datetime(m15atr["datetime"], errors="coerce")
    h1atr["datetime"] = pd.to_datetime(h1atr["datetime"], errors="coerce")

    m15atr = m15atr.dropna(subset=["datetime"]).sort_values(
        "datetime").reset_index(drop=True)
    h1atr = h1atr.dropna(subset=["datetime"]).sort_values(
        "datetime").reset_index(drop=True)

    # 4) Merge M15 ATR into 15m df
    m15_merge_cols = ["datetime"]
    if "atr_mt5" in m15atr.columns:
        m15_merge_cols.append("atr_mt5")
    elif "atr" in m15atr.columns:
        m15atr["atr_mt5"] = m15atr["atr"]
        m15_merge_cols.append("atr_mt5")
    else:
        raise RuntimeError(
            f"{pair} M15 ATR bridge data missing atr/atr_mt5 column")

    df = df.merge(
        m15atr[m15_merge_cols],
        on="datetime",
        how="left",
    )

    # Ensure all expected ATR aliases exist for engine live mode
    if "atr_mt5" not in df.columns:
        raise RuntimeError(
            f"{pair} df merge failed: atr_mt5 column not present after merge")

    df["atr"] = df["atr_mt5"]
    df["m15_atr"] = df["atr_mt5"]

    # Optional debug
    print(f"  -> df_15m columns after ATR merge: {df.columns.tolist()}")
    print(df[["datetime", "open", "high", "low", "close", "atr_mt5"]].tail(5))

    df["time"] = pd.to_datetime(df["datetime"], errors="coerce")

    # 5) H1 ATR aliases & assign to engine
    if "atr_mt5" not in h1atr.columns and "atr" in h1atr.columns:
        h1atr["atr_mt5"] = h1atr["atr"]
    if "atr" not in h1atr.columns and "atr_mt5" in h1atr.columns:
        h1atr["atr"] = h1atr["atr_mt5"]

    engine.h1_atr_df = h1atr.rename(columns={"datetime": "time"}).copy()
    engine.h1_atr_df["time"] = pd.to_datetime(
        engine.h1_atr_df["time"], errors="coerce")
    engine.h1_atr_df = engine.h1_atr_df.dropna(
        subset=["time"]).sort_values("time").reset_index(drop=True)
    engine.h1atrdf = engine.h1_atr_df.copy()

    # 6) Reconcile old/open rows
    try:
        engine._reconcile_open_registry_signals_with_market_data(
            pair=pair,
            df=df,
        )
    except Exception as e:
        print(f"  -> First reconcile failed for {pair}: {e}")

    # 7) Generate / refresh latest day signals
    result = engine.generate_live_dual_signals_for_latest_day(
        pair=pair,
        df_15m=df,
        signal_dir=SIGNAL_DIR,
        max_spread_points=MAX_SPREAD_POINTS,
        max_slippage_points=MAX_SLIPPAGE_POINTS,
    )

    # 8) Reconcile newly generated rows
    try:
        engine._reconcile_open_registry_signals_with_market_data(
            pair=pair,
            df=df,
        )
    except Exception as e:
        print(f"  -> Second reconcile failed for {pair}: {e}")

    # 9) Direct CANCEL write for completed rows
    try:
        reg = engine._load_live_registry()

        for signal_id, row in reg.items():
            row_pair = str(row.get("pair", "")).strip()
            row_day = str(row.get("day", "")).strip()
            row_status = str(row.get("registry_status", "")).strip().upper()
            row_completed = bool(row.get("completed", False))
            side = str(row.get("side", "")).strip().upper()

            if row_pair != pair or row_day != str(latest_day):
                continue

            if not (row_completed or row_status == "COMPLETED"):
                continue

            if side not in ("B", "S"):
                continue

            row_day_obj = pd.to_datetime(row_day, errors="coerce")
            if pd.isna(row_day_obj):
                print(
                    f"  -> Skipping CANCEL for {signal_id}: invalid row_day={row_day}")
                continue
            row_day_obj = row_day_obj.date()

            suffix = "BUY" if side == "B" else "SELL"
            signal_file = os.path.join(
                SIGNAL_DIR,
                f"live_signal_{pair}_{suffix}.txt",
            )

            cancel_payload = engine._build_live_cancel_payload(
                pair=pair,
                day=row_day_obj,
                max_spread_points=MAX_SPREAD_POINTS,
                max_slippage_points=MAX_SLIPPAGE_POINTS,
            )

            cancel_payload["signal_id"] = signal_id
            cancel_payload["symbol"] = pair
            cancel_payload["side"] = side
            cancel_payload["status"] = "NEW"

            print(f"  -> Writing direct CANCEL for completed {signal_id}")
            engine._write_live_signal_file(signal_file, cancel_payload)

    except Exception as e:
        print(f"  -> Direct completed CANCEL write failed for {pair}: {e}")

    # 10) Snapshot
    try:
        reg = engine._load_live_registry()
        active_count = 0
        completed_count = 0

        for _, row in reg.items():
            row_pair = str(row.get("pair", "")).strip()
            row_day = str(row.get("day", "")).strip()
            row_status = str(row.get("registry_status", "")).strip().upper()
            row_completed = bool(row.get("completed", False))

            if row_pair != pair or row_day != str(latest_day):
                continue

            if row_completed or row_status == "COMPLETED":
                completed_count += 1
            else:
                active_count += 1

        print(
            f"  -> Registry snapshot for {pair} day={latest_day}: "
            f"active={active_count}, completed={completed_count}"
        )
    except Exception as e:
        print(f"  -> Registry snapshot failed for {pair}: {e}")

    print(f"  -> Result for {pair}: {result}")


def main():
    write_heartbeat("startup")

    try:
        run_startup_cleanup(REGISTRY_FILE, SIGNAL_DIR)

        init_mt5()

        engine = BacktestEngine1HORB(
            initial_fund=INITIAL_FUND,
            initial_risk_percent=INITIAL_RISK,
            pair=DEFAULT_PAIR,
        )

        engine.use_live_equity_sizing = True
        engine.live_source_fund = None
        engine.live_strategy_start_fund = INITIAL_FUND

        write_heartbeat("startup_reconcile_begin")
        print(
            "  -> Startup reconcile skipped (per-pair reconcile will run in process_pair)")
        write_heartbeat("startup_reconcile_done")

        write_heartbeat("cycle_start")

        for pair in PAIRS:
            try:
                process_pair(engine, pair)
            except Exception as e:
                write_heartbeat("exception", {"pair": pair, "error": str(e)})
                print(f"  -> Failed for {pair}: {e}")

        write_heartbeat("cycle_done")

    except Exception as e:
        write_heartbeat("exception", {"error": str(e)})
        print(f"Runner fatal exception: {e}")
        raise
    finally:
        try:
            shutdown_mt5()
        except Exception:
            pass


if __name__ == "__main__":
    main()
