import pandas as pd
from datetime import timedelta

from strategy_calculator import StrategyCalculator
from live_data_mt5 import fetch_live_1m


RESET = "\033[0m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RED = "\033[91m"


def resolve_same_candle_exit_with_m1(
    engine,
    side: str,
    entry_time,
    actual_entry: float,
    sl: float,
    tp: float,
):
    from_time = entry_time
    to_time = entry_time + timedelta(hours=1)

    try:
        m1_df = fetch_live_1m(engine.pair, from_time, to_time)
    except Exception as e:
        print(
            YELLOW + f" -> M1 fetch failed ({e}), keeping bar-level result as-is" + RESET)
        return None

    if m1_df is None or m1_df.empty:
        print(YELLOW + " -> M1 empty, keeping bar-level result as-is" + RESET)
        return None

    m1_df["time"] = pd.to_datetime(m1_df["time"])
    m1_df = m1_df[
        (m1_df["time"] >= from_time) &
        (m1_df["time"] <= to_time)
    ].copy()
    m1_df = m1_df.sort_values("time").reset_index(drop=True)

    entry_found = False

    for _, row in m1_df.iterrows():
        h = float(row["high"])
        l = float(row["low"])
        t = row["time"]

        if side == "B":
            entry_hit = h >= actual_entry
        else:
            entry_hit = l <= actual_entry

        if not entry_found:
            if not entry_hit:
                continue

            entry_found = True
            print(CYAN + f" -> M1 entry confirmed at/after {t}" + RESET)

            if side == "B":
                tp_hit = h >= tp
                sl_hit = l <= sl
            else:
                tp_hit = l <= tp
                sl_hit = h >= sl

            if tp_hit and sl_hit:
                print(
                    YELLOW +
                    " -> Same M1 candle me entry + TP/SL ambiguity, conservative SL applied" +
                    RESET
                )
                return {
                    "result": "sl",
                    "exit_time": t,
                    "exit_price": sl,
                }

            if tp_hit:
                return {
                    "result": "tp",
                    "exit_time": t,
                    "exit_price": tp,
                }

            if sl_hit:
                return {
                    "result": "sl",
                    "exit_time": t,
                    "exit_price": sl,
                }

            continue

        if side == "B":
            tp_hit = h >= tp
            sl_hit = l <= sl
        else:
            tp_hit = l <= tp
            sl_hit = h >= sl

        if tp_hit and sl_hit:
            print(
                YELLOW +
                f" -> Post-entry same M1 ambiguity at {t}, conservative SL applied" +
                RESET
            )
            return {
                "result": "sl",
                "exit_time": t,
                "exit_price": sl,
            }

        if tp_hit:
            return {
                "result": "tp",
                "exit_time": t,
                "exit_price": tp,
            }

        if sl_hit:
            return {
                "result": "sl",
                "exit_time": t,
                "exit_price": sl,
            }

    if not entry_found:
        print(YELLOW + " -> M1 could not confirm entry, keeping bar-level result as-is" + RESET)
        return None

    last_row = m1_df.iloc[-1]
    return {
        "result": "session_exit",
        "exit_time": last_row["time"],
        "exit_price": float(last_row["close"]),
    }


def fetch_m1_data_for_window(engine, start_time, end_time):
    try:
        df_m1 = fetch_live_1m(
            engine.pair,
            start=start_time - timedelta(minutes=1),
            end=end_time + timedelta(minutes=1),
        )
    except Exception as e:
        print(
            YELLOW + f" -> Failed to fetch M1 data for MAE window: {e}" + RESET)
        return pd.DataFrame()

    if df_m1 is None or df_m1.empty:
        return pd.DataFrame()

    df_m1 = df_m1.copy()
    df_m1["time"] = pd.to_datetime(df_m1["time"])
    df_m1 = df_m1.sort_values("time").reset_index(drop=True)

    return df_m1[
        (df_m1["time"] >= start_time) &
        (df_m1["time"] <= end_time)
    ].copy()


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
    except Exception as e:
        print(YELLOW + f" -> M1 MAE fetch failed: {e}" + RESET)
        return 0.0, 0.0

    if m1_df is None or m1_df.empty:
        print(YELLOW + " -> No M1 data in window for MAE, returning 0" + RESET)
        return 0.0, 0.0

    print(
        CYAN +
        f" -> M1 MAE window {engine.pair} {entry_time} -> {exit_time}, rows={len(m1_df)}" +
        RESET
    )

    pip_value = StrategyCalculator.get_pip_value_per_lot(
        engine.pair, actual_entry)
    pip_multiplier = 100.0 if engine.pair.endswith("JPY") else 10000.0

    max_adverse_pips = 0.0
    max_adverse_amount = 0.0

    for _, row in m1_df.iterrows():
        candle_time = row["time"]

        if candle_time < entry_time:
            continue

        if side == "B":
            adverse_pips = max(
                0.0,
                (actual_entry - float(row["low"])) * pip_multiplier
            )
        else:
            adverse_pips = max(
                0.0,
                (float(row["high"]) - actual_entry) * pip_multiplier
            )

        adverse_amount = adverse_pips * pip_value * lot_size

        if adverse_amount > max_adverse_amount:
            max_adverse_amount = adverse_amount
            max_adverse_pips = adverse_pips

    return round(max_adverse_pips, 1), round(max_adverse_amount, 2)


def simulate_trade(
    engine,
    df: pd.DataFrame,
    setup: dict,
    entry_idx,
    actual_entry: float,
):
    side = setup["side"]
    sl = setup["sl"]
    tp = setup["tp"]
    lot_size = setup["lot_size"]
    entry_mode = setup.get("entry_mode", "")

    entry_row = df.loc[entry_idx]
    entry_time = entry_row["time"]
    entry_day = entry_time.date()

    try:
        pair_str = getattr(engine, "pair", "UNKNOWN")
    except Exception:
        pair_str = "UNKNOWN"

    print(
        CYAN +
        f" -> {pair_str} {side} TRADE | "
        f"Entry={entry_time} @ {actual_entry:.5f} | "
        f"LotSize={lot_size:.2f}" +
        RESET
    )

    tp_adjusted = False
    be_applied = False

    pos = df.index.get_loc(entry_idx)
    idx = pos

    exit_price = actual_entry
    exit_time = entry_time
    result = "session_exit"

    pip_value = StrategyCalculator.get_pip_value_per_lot(
        engine.pair, actual_entry)
    pip_multiplier = 100.0 if engine.pair.endswith("JPY") else 10000.0

    while idx < len(df):
        row = df.iloc[idx]
        row_time = row["time"]
        high = row["high"]
        low = row["low"]

        if ((row_time - entry_time) >= timedelta(hours=10)) and (not be_applied):
            if side == "B":
                orig_tp_dist = tp - actual_entry
            else:
                orig_tp_dist = actual_entry - tp

            if orig_tp_dist > 0:
                lock_dist = orig_tp_dist * 0.10
                new_tp_dist = orig_tp_dist * 0.80

                if side == "B":
                    new_sl = actual_entry + lock_dist
                    new_tp = actual_entry + new_tp_dist
                else:
                    new_sl = actual_entry - lock_dist
                    new_tp = actual_entry - new_tp_dist

                print(
                    CYAN +
                    f" -> 10h passed, SL_BE+TP80 applied at {row_time}: "
                    f"SL {sl:.5f} -> {new_sl:.5f}, TP {tp:.5f} -> {new_tp:.5f}" +
                    RESET
                )

                sl = new_sl
                tp = new_tp
                be_applied = True

        if (row_time.date() != entry_day) and (not tp_adjusted):
            if side == "B":
                orig_tp_dist = tp - actual_entry
            else:
                orig_tp_dist = actual_entry - tp

            if orig_tp_dist > 0:
                new_tp_dist = orig_tp_dist * 0.75
                old_tp = tp

                if side == "B":
                    tp = actual_entry + new_tp_dist
                else:
                    tp = actual_entry - new_tp_dist

                tp_adjusted = True
                print(
                    CYAN +
                    f" -> Day changed, TP reduced from {old_tp:.5f} to {tp:.5f} (~1:1.5) at {row_time}" +
                    RESET
                )

        hit_same_candle = False
        hit_type = None

        if side == "B":
            if high >= tp:
                exit_price = tp
                exit_time = row_time
                result = "tp"
                if row_time == entry_time:
                    hit_same_candle = True
                    hit_type = "tp"
                else:
                    break

            if low <= sl:
                exit_price = sl
                exit_time = row_time
                result = "sl_lock10" if be_applied else "sl"
                if row_time == entry_time:
                    hit_same_candle = True
                    hit_type = "sl"
                else:
                    break
        else:
            if low <= tp:
                exit_price = tp
                exit_time = row_time
                result = "tp"
                if row_time == entry_time:
                    hit_same_candle = True
                    hit_type = "tp"
                else:
                    break

            if high >= sl:
                exit_price = sl
                exit_time = row_time
                result = "sl_lock10" if be_applied else "sl"
                if row_time == entry_time:
                    hit_same_candle = True
                    hit_type = "sl"
                else:
                    break

        if hit_same_candle and hit_type is not None:
            print(
                CYAN +
                f" -> Same bar {hit_type.upper()} at {row_time}, checking M1 sequence..." +
                RESET
            )

            resolved = resolve_same_candle_exit_with_m1(
                engine=engine,
                side=side,
                entry_time=entry_time,
                actual_entry=actual_entry,
                sl=sl,
                tp=tp,
            )

            if resolved is None:
                print(
                    YELLOW +
                    " -> M1 could not confirm entry, keeping bar-level result as-is" +
                    RESET
                )
                break

            m1_result = resolved.get("result")
            m1_exit_time = resolved.get("exit_time")
            m1_exit_price = resolved.get("exit_price")

            if m1_result not in ("tp", "sl", "session_exit") or m1_exit_time is None:
                print(
                    YELLOW +
                    " -> M1 returned invalid result, keeping bar-level result as-is" +
                    RESET
                )
                break

            if m1_result != hit_type and m1_result in ("tp", "sl"):
                print(
                    YELLOW +
                    f" -> M1 sequence override: bar said {hit_type.upper()}, "
                    f"actual is {m1_result.upper()} at {m1_exit_time}" +
                    RESET
                )

            if m1_result == "tp":
                exit_price = m1_exit_price if m1_exit_price is not None else tp
                result = "tp"
            elif m1_result == "sl":
                exit_price = m1_exit_price if m1_exit_price is not None else sl
                result = "sl_lock10" if be_applied else "sl"
            else:
                exit_price = m1_exit_price
                result = "session_exit"

            exit_time = m1_exit_time
            break

        idx += 1

    if result == "session_exit":
        last_row = df.iloc[-1]
        exit_price = last_row["close"]
        exit_time = last_row["time"]

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
        print(
            YELLOW +
            f" -> Fund depleted (fund={engine.current_fund:.2f}), stopping further trades" +
            RESET
        )
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
        "sl": sl,
        "tp": tp,
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
        "sl_mode": "LOCK10_TP80" if be_applied else "NORMAL",
        "lot_size": round(lot_size, 2),
    }

    engine.trades.append(trade_record)
    engine.total_trades += 1
    return trade_record
