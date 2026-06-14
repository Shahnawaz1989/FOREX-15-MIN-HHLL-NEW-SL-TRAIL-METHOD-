import pandas as pd
from datetime import timedelta
from strategy_calculator import StrategyCalculator
from live_data_mt5 import fetch_live_1m


RESET = "\033[0m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RED = "\033[91m"


def fetch_m1_data_for_window(engine, start_time, end_time):
    try:
        df_m1 = fetch_live_1m(
            engine.pair,
            start=start_time - timedelta(minutes=1),
            end=end_time + timedelta(minutes=1),
        )
    except Exception as e:
        print(YELLOW + f" -> M1 fetch failed ({e})" + RESET)
        return pd.DataFrame()

    if df_m1 is None or df_m1.empty:
        return pd.DataFrame()

    df_m1 = df_m1.copy()
    df_m1["time"] = pd.to_datetime(df_m1["time"])
    df_m1 = df_m1.sort_values("time").reset_index(drop=True)

    return df_m1[(df_m1["time"] >= start_time) & (df_m1["time"] <= end_time)].copy()


def compute_m1_mae_after_entry(
    engine,
    side: str,
    entry_time,
    exit_time,
    actual_entry: float,
    lot_size: float,
):
    try:
        m1_df = fetch_m1_data_for_window(engine, entry_time, exit_time)
    except Exception:
        return 0.0, 0.0

    if m1_df is None or m1_df.empty:
        return 0.0, 0.0

    pip_value = StrategyCalculator.get_pip_value_per_lot(
        engine.pair, actual_entry)
    pip_multiplier = 100.0 if engine.pair.endswith("JPY") else 10000.0

    max_adverse_pips = 0.0
    max_adverse_amount = 0.0

    for _, row in m1_df.iterrows():
        candle_time = row["time"]
        if candle_time <= entry_time:
            continue

        if side == "B":
            adverse_pips = max(
                0.0, (actual_entry - float(row["low"])) * pip_multiplier)
        else:
            adverse_pips = max(
                0.0, (float(row["high"]) - actual_entry) * pip_multiplier)

        adverse_amount = adverse_pips * pip_value * lot_size
        if adverse_amount > max_adverse_amount:
            max_adverse_amount = adverse_amount
            max_adverse_pips = adverse_pips

    return round(max_adverse_pips, 1), round(max_adverse_amount, 2)


def _simulate_trade_on_m1(
    engine,
    side: str,
    entry_time,
    actual_entry: float,
    sl: float,
    tp: float,
    lot_size: float,
    tp_mode: str = "",
):
    side = "B" if str(side).upper().startswith("B") else "S"
    tp_mode = str(tp_mode or "").strip().upper()
    is_early_mode = tp_mode in {"TP_EARLY", "EARLY", "TPEARLY"}

    original_sl = float(sl)
    original_tp = float(tp)

    total_target_dist = abs(original_tp - actual_entry)
    be_trigger_price = None
    lock_price = None

    lock_profit_pct = 0.15 if is_early_mode else 0.30

    if total_target_dist > 0:
        if side == "B":
            be_trigger_price = actual_entry + (total_target_dist * 0.80)
            lock_price = actual_entry + (total_target_dist * lock_profit_pct)
        else:
            be_trigger_price = actual_entry - (total_target_dist * 0.80)
            lock_price = actual_entry - (total_target_dist * lock_profit_pct)

    entry_day = entry_time.date()
    sim_end_time = entry_time + timedelta(days=3)

    m1_df = fetch_m1_data_for_window(engine, entry_time, sim_end_time)
    if m1_df is None or m1_df.empty:
        return {
            "result": "session_exit",
            "exit_time": entry_time,
            "exit_price": actual_entry,
            "sl": original_sl,
            "tp": original_tp,
            "be_applied": False,
            "be_reason": "",
            "lock_profit_pct": lock_profit_pct,
        }

    m1_df = m1_df.copy()
    m1_df["time"] = pd.to_datetime(m1_df["time"])
    m1_df = m1_df.sort_values("time").reset_index(drop=True)

    be_applied = False
    be_reason = ""
    tp_adjusted = False
    entry_found = False
    entry_fill_time = None
    last_close = actual_entry
    last_time = entry_time

    for _, row in m1_df.iterrows():
        row_time = row["time"]
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])

        if side == "B":
            entry_hit = high >= actual_entry
        else:
            entry_hit = low <= actual_entry

        if not entry_found:
            if not entry_hit:
                continue

            entry_found = True
            entry_fill_time = row_time
            last_close = close
            last_time = row_time
            continue

        if row_time <= entry_fill_time:
            last_close = close
            last_time = row_time
            continue

        if not be_applied and be_trigger_price is not None:
            if side == "B" and high >= be_trigger_price:
                sl = max(sl, lock_price)
                be_applied = True
                be_reason = f"EARLY_80_LOCK_{int(lock_profit_pct * 100)}"
            elif side == "S" and low <= be_trigger_price:
                sl = min(sl, lock_price)
                be_applied = True
                be_reason = f"EARLY_80_LOCK_{int(lock_profit_pct * 100)}"

        if ((row_time - entry_fill_time) >= timedelta(hours=10)) and (not be_applied):
            if lock_price is not None:
                if side == "B":
                    sl = max(sl, lock_price)
                else:
                    sl = min(sl, lock_price)
                be_applied = True
                be_reason = f"TIME_10H_LOCK_{int(lock_profit_pct * 100)}"

        if (row_time.date() != entry_day) and (not tp_adjusted):
            if side == "B":
                orig_tp_dist = tp - actual_entry
            else:
                orig_tp_dist = actual_entry - tp

            if orig_tp_dist > 0:
                new_tp_dist = orig_tp_dist * 0.75
                if side == "B":
                    tp = actual_entry + new_tp_dist
                else:
                    tp = actual_entry - new_tp_dist
                tp_adjusted = True

        if side == "B":
            tp_hit = high >= tp
            sl_hit = low <= sl
        else:
            tp_hit = low <= tp
            sl_hit = high >= sl

        if tp_hit and sl_hit:
            return {
                "result": "sl_lock30" if be_applied else "sl",
                "exit_time": row_time,
                "exit_price": sl,
                "sl": sl,
                "tp": tp,
                "be_applied": be_applied,
                "be_reason": be_reason,
                "lock_profit_pct": lock_profit_pct,
            }

        if tp_hit:
            return {
                "result": "tp",
                "exit_time": row_time,
                "exit_price": tp,
                "sl": sl,
                "tp": tp,
                "be_applied": be_applied,
                "be_reason": be_reason,
                "lock_profit_pct": lock_profit_pct,
            }

        if sl_hit:
            return {
                "result": "sl_lock30" if be_applied else "sl",
                "exit_time": row_time,
                "exit_price": sl,
                "sl": sl,
                "tp": tp,
                "be_applied": be_applied,
                "be_reason": be_reason,
                "lock_profit_pct": lock_profit_pct,
            }

        last_close = close
        last_time = row_time

    if not entry_found:
        return {
            "result": "session_exit",
            "exit_time": entry_time,
            "exit_price": actual_entry,
            "sl": original_sl,
            "tp": original_tp,
            "be_applied": False,
            "be_reason": "",
            "lock_profit_pct": lock_profit_pct,
        }

    return {
        "result": "session_exit",
        "exit_time": last_time,
        "exit_price": last_close,
        "sl": sl,
        "tp": tp,
        "be_applied": be_applied,
        "be_reason": be_reason,
        "lock_profit_pct": lock_profit_pct,
    }


def resolve_same_candle_exit_with_m1(
    engine,
    side: str,
    entry_time,
    actual_entry: float,
    sl: float,
    tp: float,
):
    resolved = _simulate_trade_on_m1(
        engine=engine,
        side=side,
        entry_time=entry_time,
        actual_entry=actual_entry,
        sl=sl,
        tp=tp,
        lot_size=0.0,
        tp_mode="",
    )

    mapped_result = resolved["result"]
    if mapped_result not in ("tp", "sl", "sl_lock30", "session_exit"):
        mapped_result = "session_exit"

    return {
        "result": mapped_result,
        "exit_time": resolved["exit_time"],
        "exit_price": resolved["exit_price"],
    }


def simulate_trade(
    engine,
    df: pd.DataFrame,
    setup: dict,
    entry_idx,
    actual_entry: float,
):
    side = setup["side"]
    side = "B" if str(side).upper().startswith("B") else "S"

    sl = float(setup["sl"])
    tp = float(setup["tp"])
    lot_size = float(setup["lot_size"])
    entry_mode = setup.get("entry_mode", "")
    tp_mode = setup.get("tp_mode", "")

    entry_row = df.loc[entry_idx]
    entry_time = pd.to_datetime(entry_row["time"])

    resolved = _simulate_trade_on_m1(
        engine=engine,
        side=side,
        entry_time=entry_time,
        actual_entry=actual_entry,
        sl=sl,
        tp=tp,
        lot_size=lot_size,
        tp_mode=tp_mode,
    )

    exit_price = float(resolved["exit_price"])
    exit_time = pd.to_datetime(resolved["exit_time"])
    result = resolved["result"]
    sl = float(resolved["sl"])
    tp = float(resolved["tp"])
    be_applied = bool(resolved.get("be_applied", False))
    be_reason = resolved.get("be_reason", "")
    lock_profit_pct = float(resolved.get("lock_profit_pct", 0.30))

    pip_value = StrategyCalculator.get_pip_value_per_lot(
        engine.pair, actual_entry)
    pip_multiplier = 100.0 if engine.pair.endswith("JPY") else 10000.0

    if side == "B":
        pnl_pips = (exit_price - actual_entry) * pip_multiplier
    else:
        pnl_pips = (actual_entry - exit_price) * pip_multiplier

    pnl_amount = pnl_pips * pip_value * lot_size

    balance_before_trade = engine.current_fund
    engine.current_fund += pnl_amount
    engine.equity_high = max(engine.equity_high, engine.current_fund)

    drawdown = engine.equity_high - engine.current_fund
    engine.max_drawdown = max(engine.max_drawdown, drawdown)

    if engine.current_fund <= 0:
        engine.stop_requested = True

    m1_mae_pips, m1_mae_amount = compute_m1_mae_after_entry(
        engine=engine,
        side=side,
        entry_time=entry_time,
        exit_time=exit_time,
        actual_entry=actual_entry,
        lot_size=lot_size,
    )

    min_available_balance_during_trade = balance_before_trade - m1_mae_amount

    trade_record = {
        "date": entry_time.date(),
        "pair": engine.pair,
        "side": side,
        "entry_time": entry_time,
        "entry_price": actual_entry,
        "sl": round(float(sl), 5),
        "tp": round(float(tp), 5),
        "exit_time": exit_time,
        "exit_price": exit_price,
        "result": result,
        "pnl_pips": round(pnl_pips, 1),
        "pnl_amount": round(pnl_amount, 2),
        "fund_after": round(engine.current_fund, 2),
        "max_adverse_pips": round(m1_mae_pips, 1),
        "max_adverse_amount": round(m1_mae_amount, 2),
        "balance_before_trade": round(balance_before_trade, 2),
        "min_available_balance_during_trade": round(min_available_balance_during_trade, 2),
        "entry_mode": entry_mode,
        "tp_mode": tp_mode,
        "sl_mode": be_reason if be_applied else "NORMAL",
    }

    engine.trades.append(trade_record)
    engine.total_trades += 1
    return trade_record
