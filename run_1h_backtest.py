from backtest_engine_1h_orb import (
    BacktestEngine1HORB,
    generatelivedualsignalsforlatestday,
)
from live_data_mt5 import fetch_live_15m
from order_mt5 import init_mt5, shutdown_mt5
import os
import pandas as pd

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

START_DAY = "2026-05-20"
END_DAY = "2026-05-29"

SIGNAL_DIR = r"C:\Users\Uzair Khan\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5\Files"
MAX_SPREAD_POINTS = 25
MAX_SLIPPAGE_POINTS = 15


def print_final_summary(engine):
    total_trades = len(engine.trades)
    wins_tp = sum(1 for t in engine.trades if str(
        t.get("result", "")).lower() == "tp")
    losses_sl = sum(1 for t in engine.trades if str(
        t.get("result", "")).lower() == "sl")
    be_sl = sum(1 for t in engine.trades if str(
        t.get("result", "")).lower() == "sllock10")
    expired = sum(1 for t in engine.trades if str(
        t.get("result", "")).lower() == "orderexpired1930")
    session_exit = sum(1 for t in engine.trades if str(
        t.get("result", "")).lower() == "sessionexit")

    net_pnl = float(engine.current_fund) - float(engine.initial_fund)

    print("\n" + "=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)
    print(f"Initial Fund : {engine.initial_fund:.2f}")
    print(f"Final Fund   : {engine.current_fund:.2f}")
    print(f"Net PnL      : {net_pnl:.2f}")
    print(f"Total Trades : {total_trades}")
    print(f"Total TP     : {wins_tp}")
    print(f"Total SL     : {losses_sl}")
    print(f"Total BE-SL  : {be_sl}")
    print(f"Expired      : {expired}")
    print(f"Session Exit : {session_exit}")
    print(f"Max DD       : {engine.max_drawdown:.2f}")
    print("=" * 70)


def main():
    init_mt5()

    engine = BacktestEngine1HORB(
        initial_fund=INITIAL_FUND,
        initial_risk_percent=INITIAL_RISK,
        pair=DEFAULT_PAIR,
    )

    engine.use_live_equity_sizing = True
    engine.live_source_fund = None
    engine.live_strategy_start_fund = INITIAL_FUND

    os.makedirs(SIGNAL_DIR, exist_ok=True)

    start_day_obj = pd.to_datetime(START_DAY).date()
    end_day_obj = pd.to_datetime(END_DAY).date()

    if start_day_obj > end_day_obj:
        raise ValueError("START_DAY cannot be after END_DAY")

    day_range = pd.date_range(start_day_obj, end_day_obj, freq="D")

    for day_ts in day_range:
        target_day = day_ts.date()

        print("\n" + "#" * 80)
        print(f"PROCESSING DAY = {target_day}")
        print("#" * 80)

        day_open_balance = engine.current_fund
        day_trade_count = 0
        day_tp_hits = 0

        for pair in PAIRS:
            print("\n" + "-" * 60)
            print(f"Pair = {pair} | Day = {target_day}")

            try:
                df_15m = fetch_live_15m(pair, lookback_days=15)
            except Exception as e:
                print(f"  -> Failed to fetch 15m data for {pair}: {e}")
                continue

            if df_15m is None or df_15m.empty:
                print("  -> No MT5 15m data")
                continue

            df_15m = df_15m.copy()
            df_15m["datetime"] = pd.to_datetime(
                df_15m["datetime"], errors="coerce")
            df_15m = df_15m.dropna(subset=["datetime"]).sort_values(
                "datetime").reset_index(drop=True)

            # <= target day so that latest day inside filtered df becomes target_day
            df_for_day = df_15m[df_15m["datetime"].dt.date <=
                                target_day].copy()

            if df_for_day.empty:
                print(f"  -> No data up to {target_day}")
                continue

            latest_in_filtered = df_for_day["datetime"].dt.date.max()
            if latest_in_filtered != target_day:
                print(
                    f"  -> Skipping {pair}, latest available filtered day is {latest_in_filtered}, not {target_day}")
                continue

            trades_before = len(engine.trades)

            try:
                result = generatelivedualsignalsforlatestday(
                    engine=engine,
                    pair=pair,
                    df15m=df_for_day,
                    signalfile=None,
                    signaldir=SIGNAL_DIR,
                    maxspreadpoints=MAX_SPREAD_POINTS,
                    maxslippagepoints=MAX_SLIPPAGE_POINTS,
                )
                print(f"  -> Result for {pair}: {result}")
            except Exception as e:
                print(f"  -> Dual signal generation failed for {pair}: {e}")
                continue

            trades_after = len(engine.trades)
            new_trades = trades_after - trades_before
            if new_trades > 0:
                day_trade_count += new_trades
                recent = engine.trades[-new_trades:]
                day_tp_hits += sum(1 for t in recent if str(
                    t.get("result", "")).lower() == "tp")

        day_close_balance = engine.current_fund
        day_profit = day_close_balance - day_open_balance

        print("\n" + "=" * 60)
        print(f"DAY SUMMARY {target_day}")
        print(f"Open Balance : {day_open_balance:.2f}")
        print(f"Close Balance: {day_close_balance:.2f}")
        print(f"PnL          : {day_profit:.2f}")
        print(f"Trades       : {day_trade_count}")
        print(f"TP Hits      : {day_tp_hits}")
        print("=" * 60)

    print_final_summary(engine)
    shutdown_mt5()


if __name__ == "__main__":
    main()
