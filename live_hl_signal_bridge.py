from datetime import datetime
import os
import json
import pandas as pd

from backtest_engine_1h_orb import BacktestEngine1HORB
from live_data_mt5 import fetch_live_15m
from order_mt5 import init_mt5, shutdown_mt5
from live_cleanup import run_startup_cleanup


HEARTBEAT_FILE = r"C:\trading_bot\heartbeats\live_runner_heartbeat.json"


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


PAIRS = [
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

INITIAL_FUND = 30.0
INITIAL_RISK = 8.0
DEFAULT_PAIR = PAIRS[0]
LOOKBACK_DAYS = 30
MAX_SPREAD_POINTS = 25
MAX_SLIPPAGE_POINTS = 15

SIGNAL_DIR = r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5\Files"
REGISTRY_FILE = r"live_registry/hl_live_registry.json"


def process_pair(engine: BacktestEngine1HORB, pair: str):
    write_heartbeat("processing_pair", {"pair": pair})

    print("\n" + "=" * 60)
    print(f"Processing live HL dual signals for {pair}")

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

    print(f"  -> {pair} max datetime in 15m df = {df['datetime'].max()}")
    print(df[["datetime", "open", "high", "low", "close"]].tail(5))

    engine.generate_live_dual_signals_for_latest_day(
        pair=pair,
        df_15m=df,
        signal_dir=SIGNAL_DIR,
        max_spread_points=MAX_SPREAD_POINTS,
        max_slippage_points=MAX_SLIPPAGE_POINTS,
    )


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
        engine.live_source_fund = 70.0
        engine.live_strategy_start_fund = 30.0

        write_heartbeat("startup_reconcile_begin")
        try:
            engine.reconcile_open_registry_signals_with_market_data()
        except Exception as e:
            print(f"  -> Startup reconcile failed: {e}")
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
