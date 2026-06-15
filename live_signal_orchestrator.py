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
    return engine._build_setup_for_day(day_df, fund, risk_percent)


def evaluate_setup_state(engine, pair: str, day, side: str, setup: Dict):
    result = {
        "setup": setup,
        "suppressed": False,
        "suppression_reason": "",
        "blocked_by_disable_window": False,
        "replace_old_active": False,
        "active_id": None,
        "active_row": None,
    }

    if not setup:
        result["suppressed"] = True
        result["suppression_reason"] = "NO_SETUP"
        return result

    existing_order = None
    try:
        existing_order = engine._find_mt5_pending_order_for_setup(
            pair=pair, setup=setup
        )
    except Exception as e:
        print(f"  -> MT5 pending lookup failed for {pair} {side}: {e}")

    existing_position = None
    try:
        existing_position = engine._find_mt5_open_position_for_setup(
            pair=pair, setup=setup
        )
    except Exception as e:
        print(f"  -> MT5 open-position lookup failed for {pair} {side}: {e}")

    already_closed = False
    try:
        already_closed = bool(
            engine._has_mt5_closed_trade_for_setup(
                pair=pair, day=day, setup=setup
            )
        )
    except Exception as e:
        print(f"  -> MT5 history lookup failed for {pair} {side}: {e}")

    if already_closed:
        print(
            f"  -> {pair} {day} {side} suppressed: same setup already closed in MT5 history"
        )
        result["suppressed"] = True
        result["suppression_reason"] = "MT5_HISTORY_CLOSED"
        return result

    if existing_position:
        print(
            f"  -> {pair} {day} {side} suppressed: matching MT5 open position exists"
        )
        result["suppressed"] = True
        result["suppression_reason"] = "MT5_OPEN_POSITION"
        result["active_row"] = existing_position
        return result

    if existing_order:
        print(
            f"  -> {pair} {day} {side} suppressed: matching MT5 pending order exists"
        )
        result["suppressed"] = True
        result["suppression_reason"] = "MT5_PENDING_ORDER"
        result["active_row"] = existing_order
        return result

    return result


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
        if hasattr(engine, "_add_atr_column"):
            df = engine._add_atr_column(df)
        else:
            raise ValueError(
                "df_15m must contain atr column or engine must provide _add_atr_column"
            )

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
            f"  -> Existing BUY file belongs to old day, treating as stale: {buy_file}"
        )
        existing_buy = None

    if is_existing_from_old_day(existing_sell, day):
        print(
            f"  -> Existing SELL file belongs to old day, treating as stale: {sell_file}"
        )
        existing_sell = None

    existing_buy_status = (
        str(existing_buy.get("status", "")).upper() if existing_buy else ""
    )
    existing_sell_status = (
        str(existing_sell.get("status", "")).upper() if existing_sell else ""
    )

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

    setup = choose_live_setup_for_day(engine, day_df, fund, risk_percent)

    buy_setup = None
    sell_setup = None

    buy_state = {
        "setup": None,
        "suppressed": True,
        "suppression_reason": "NO_SETUP",
        "blocked_by_disable_window": False,
        "replace_old_active": False,
        "active_id": None,
        "active_row": None,
    }
    sell_state = {
        "setup": None,
        "suppressed": True,
        "suppression_reason": "NO_SETUP",
        "blocked_by_disable_window": False,
        "replace_old_active": False,
        "active_id": None,
        "active_row": None,
    }

    chosen_setups = []
    if setup:
        if isinstance(setup, dict) and "chosen_setups" in setup:
            chosen_setups = setup.get("chosen_setups") or []
        elif isinstance(setup, list):
            chosen_setups = setup
        elif isinstance(setup, dict) and setup.get("side"):
            chosen_setups = [setup]

    for one_setup in chosen_setups:
        side = str(one_setup.get("side", "")).upper().strip()

        if side in {"BUY", "B"} and buy_setup is None:
            buy_state = evaluate_setup_state(engine, pair, day, "B", one_setup)
            buy_setup = None if buy_state["suppressed"] else buy_state["setup"]

        elif side in {"SELL", "S"} and sell_setup is None:
            sell_state = evaluate_setup_state(
                engine, pair, day, "S", one_setup)
            sell_setup = None if sell_state["suppressed"] else sell_state["setup"]

    buy_payload = None
    sell_payload = None

    if buy_setup:
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
    else:
        if buy_state["suppressed"] and buy_state.get("suppression_reason") in {
            "MT5_HISTORY_CLOSED",
            "MT5_OPEN_POSITION",
            "MT5_PENDING_ORDER",
        }:
            print(
                f"  -> {pair} BUY: setup suppressed by {buy_state['suppression_reason']}, skip cancel/write"
            )
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
    else:
        if sell_state["suppressed"] and sell_state.get("suppression_reason") in {
            "MT5_HISTORY_CLOSED",
            "MT5_OPEN_POSITION",
            "MT5_PENDING_ORDER",
        }:
            print(
                f"  -> {pair} SELL: setup suppressed by {sell_state['suppression_reason']}, skip cancel/write"
            )
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
