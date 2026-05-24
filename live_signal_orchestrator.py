import os
from typing import Dict

import pandas as pd


def is_existing_from_old_day(existing_payload, current_day):
    if existing_payload is None:
        return False

    existing_signal_id = str(existing_payload.get("signal_id", "")).strip()
    existing_expiry = str(existing_payload.get("expiry_server", "")).strip()
    day_str = str(current_day)

    return day_str not in existing_signal_id and day_str not in existing_expiry


def choose_live_setup_for_day(engine, day_df: pd.DataFrame, fund: float, risk_percent: float):
    high_setup = engine._build_high_setup_for_day(day_df, fund, risk_percent)
    low_setup = engine._build_low_setup_for_day(day_df, fund, risk_percent)

    candidates = []
    if high_setup:
        candidates.append(high_setup)
    if low_setup:
        candidates.append(low_setup)

    if not candidates:
        return None

    candidates.sort(key=lambda s: s["trigger_time"])
    return candidates[-1]


def generate_live_dual_signals_for_latest_day(
    engine,
    terminal_filled_statuses,
    pair: str,
    df_15m: pd.DataFrame,
    signal_file: str = None,
    signal_dir: str = None,
    max_spread_points: int = 25,
    max_slippage_points: int = 15,
):
    engine.pair = pair

    df = df_15m.copy()
    df["time"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("time").reset_index(drop=True)

    if "atr" not in df.columns:
        df = engine._add_atr_column(df)

    engine._reconcile_open_registry_signals_with_market_data(pair=pair, df=df)

    day = df["time"].dt.date.max()
    day_df = df[df["time"].dt.date == day].copy()

    print(f"\n[HL Live] {pair} latest day = {day}")
    print(f"  -> Rows in day_df: {len(day_df)}")

    if signal_dir is None:
        if signal_file is not None:
            signal_dir = os.path.dirname(signal_file)
        else:
            raise ValueError("signal_dir or signal_file required")

    buy_file = os.path.join(signal_dir, f"live_signal_{pair}_BUY.txt")
    sell_file = os.path.join(signal_dir, f"live_signal_{pair}_SELL.txt")

    _, existing_buy = engine._read_existing_live_signal(buy_file)
    _, existing_sell = engine._read_existing_live_signal(sell_file)

    if is_existing_from_old_day(existing_buy, day):
        print(
            f"  -> Existing BUY file belongs to old day, treating as stale: {buy_file}")
        existing_buy = None

    if is_existing_from_old_day(existing_sell, day):
        print(
            f"  -> Existing SELL file belongs to old day, treating as stale: {sell_file}")
        existing_sell = None

    existing_buy_status = str(existing_buy.get(
        "status", "")).upper() if existing_buy else ""
    existing_sell_status = str(existing_sell.get(
        "status", "")).upper() if existing_sell else ""

    if day_df.empty or not engine._validate_day(day_df):
        print("  -> Day invalid")

        if existing_buy_status not in terminal_filled_statuses:
            engine._cancel_existing_signal_strict(
                pair=pair,
                day=day,
                signal_file=buy_file,
                existing=existing_buy,
                max_spread_points=max_spread_points,
                max_slippage_points=max_slippage_points,
                reason="CANCELLEDEOD",
            )

        if existing_sell_status not in terminal_filled_statuses:
            engine._cancel_existing_signal_strict(
                pair=pair,
                day=day,
                signal_file=sell_file,
                existing=existing_sell,
                max_spread_points=max_spread_points,
                max_slippage_points=max_slippage_points,
                reason="CANCELLEDEOD",
            )

        return {"buy": None, "sell": None}

    fund = engine._get_live_fund_for_sizing()
    print(f"  -> Live sizing fund selected = {fund:.2f}")
    risk_percent = engine.base_risk_percent

    high_setup = engine._build_high_setup_for_day(day_df, fund, risk_percent)
    low_setup = engine._build_low_setup_for_day(day_df, fund, risk_percent)

    buy_setup = low_setup
    sell_setup = high_setup

    if buy_setup and engine._is_setup_in_hhll_disable_window(buy_setup):
        print(
            f"  -> {pair} BUY suppressed: setup trigger in HH/LL disable window")
        buy_setup = None

    if sell_setup and engine._is_setup_in_hhll_disable_window(sell_setup):
        print(
            f"  -> {pair} SELL suppressed: setup trigger in HH/LL disable window")
        sell_setup = None

    if buy_setup and engine._is_same_completed_trade_prices(
        pair=pair,
        day=day,
        side="B",
        entry=float(buy_setup.get("entry", 0.0)),
        sl=float(buy_setup.get("sl", 0.0)),
        tp=float(buy_setup.get("tp", 0.0)),
    ):
        print(
            f"  -> {pair} {day} BUY suppressed: same completed setup already closed")
        buy_setup = None

    if sell_setup and engine._is_same_completed_trade_prices(
        pair=pair,
        day=day,
        side="S",
        entry=float(sell_setup.get("entry", 0.0)),
        sl=float(sell_setup.get("sl", 0.0)),
        tp=float(sell_setup.get("tp", 0.0)),
    ):
        print(
            f"  -> {pair} {day} SELL suppressed: same completed setup already closed")
        sell_setup = None

    if buy_setup and engine._has_active_registry_signal_for_pair_day_side(pair, day, "B"):
        print(
            f"  -> {pair} {day} BUY suppressed: active registry signal already exists")
        buy_setup = None

    if sell_setup and engine._has_active_registry_signal_for_pair_day_side(pair, day, "S"):
        print(
            f"  -> {pair} {day} SELL suppressed: active registry signal already exists")
        sell_setup = None

    reg = engine._load_live_registry()
    day_str = str(day)

    buy_completed = False
    sell_completed = False

    for _, row in reg.items():
        row_pair = str(row.get("pair", "")).strip()
        row_day = str(row.get("day", "")).strip()
        row_side = str(row.get("side", "")).strip().upper()
        row_completed = bool(row.get("completed", False))
        row_status = str(row.get("registry_status", "")).strip().upper()

        if row_pair != pair or row_day != day_str:
            continue

        if row_side == "B" and (row_completed or row_status == "COMPLETED"):
            buy_completed = True

        if row_side == "S" and (row_completed or row_status == "COMPLETED"):
            sell_completed = True

    if buy_completed:
        print(f"  -> {pair} {day} BUY already completed, suppress BUY export")
        buy_setup = None

    if sell_completed:
        print(f"  -> {pair} {day} SELL already completed, suppress SELL export")
        sell_setup = None

    buy_payload = None
    sell_payload = None

    if buy_setup:
        print(f"[GEN DBG] {pair} BUY setup = {buy_setup}")
        buy_payload = engine._write_fresh_signal_after_strict_delete(
            pair=pair,
            day=day,
            signal_file=buy_file,
            setup=buy_setup,
            existing=existing_buy,
            existing_status=existing_buy_status,
            max_spread_points=max_spread_points,
            max_slippage_points=max_slippage_points,
            reason="CANCELLEDNEWHHLL",
        )
        print(f"[GEN DBG] {pair} BUY payload returned = {buy_payload}")
    else:
        print(f"  -> {pair} BUY: no setup")
        if existing_buy_status not in terminal_filled_statuses:
            engine._cancel_existing_signal_strict(
                pair=pair,
                day=day,
                signal_file=buy_file,
                existing=existing_buy,
                max_spread_points=max_spread_points,
                max_slippage_points=max_slippage_points,
                reason="CANCELLEDNEWHHLL",
            )

    if sell_setup:
        print(f"[GEN DBG] {pair} SELL setup = {sell_setup}")
        sell_payload = engine._write_fresh_signal_after_strict_delete(
            pair=pair,
            day=day,
            signal_file=sell_file,
            setup=sell_setup,
            existing=existing_sell,
            existing_status=existing_sell_status,
            max_spread_points=max_spread_points,
            max_slippage_points=max_slippage_points,
            reason="CANCELLEDNEWHHLL",
        )
        print(f"[GEN DBG] {pair} SELL payload returned = {sell_payload}")
    else:
        print(f"  -> {pair} SELL: no setup")
        if existing_sell_status not in terminal_filled_statuses:
            engine._cancel_existing_signal_strict(
                pair=pair,
                day=day,
                signal_file=sell_file,
                existing=existing_sell,
                max_spread_points=max_spread_points,
                max_slippage_points=max_slippage_points,
                reason="CANCELLEDNEWHHLL",
            )

    return {"buy": buy_payload, "sell": sell_payload}
