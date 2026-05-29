from datetime import timedelta
from typing import Dict, Optional

import numpy as np

from strategy_calculator import StrategyCalculator


def select_entry_target_from_gate_and_39(
    side: str,
    gate_touched: bool,
    r39_touched: bool,
    at: float,
    t1: float,
    t15: float,
) -> Dict:
    side = str(side).upper().strip()
    t05 = round((float(at) + float(t1)) / 2.0, 5)

    if gate_touched:
        entry_price = float(t1)
        tp_price = float(t15)
        target_mode = "T15"
        entry_mode = "BUY_T1" if side == "BUY" else "SELL_T1"
    else:
        entry_price = float(at)
        if r39_touched:
            tp_price = float(t05)
            target_mode = "T05"
        else:
            tp_price = float(t15)
            target_mode = "T15"
        entry_mode = "BUY_AT" if side == "BUY" else "SELL_AT"

    return {
        "entry_price": round(entry_price, 5),
        "tp_price": round(tp_price, 5),
        "entry_mode": entry_mode,
        "target_mode": target_mode,
    }


def build_low_setup_for_day(
    engine,
    day_df,
    fund: float,
    risk_percent: float,
) -> Optional[Dict]:
    """
    LOW-side pattern (BUY setup)

    - Use global day low as base.
    - Wait for close > low_high AND breakout candle makes HH wick.
    - Check 1.5h sustain window; if a new lower low appears -> invalid.
    - derived = low_high + ATR * 3.9
    - Gann input = low_high
    - 1*ATR gate:
        - If gate passed:
            - derived < BUY_AT  -> BUY_T1
            - else              -> BUY_AT
        - If gate fails:
            - force BUY_T1
    """
    if day_df.empty:
        return None

    day_df = day_df.sort_values("time").reset_index(drop=True)

    min_idx = day_df["low"].astype(float).idxmin()
    low_row = day_df.loc[min_idx]

    day_low = float(low_row["low"])
    low_high = float(low_row["high"])
    picked_time = low_row["time"]

    low_atr = float(low_row.get("atr", 0.0))
    if not np.isfinite(low_atr) or low_atr <= 0:
        found_atr = None
        for k in range(min_idx - 1, -1, -1):
            atr_val = float(day_df.iloc[k].get("atr", 0.0))
            if np.isfinite(atr_val) and atr_val > 0:
                found_atr = atr_val
                break

        if found_atr is not None:
            low_atr = found_atr
            print(
                f"  -> LOW pattern: ATR at day low invalid, "
                f"using previous valid ATR={low_atr:.5f}"
            )
        else:
            print("  -> LOW pattern: no valid ATR found before day low, skip")
            return None

    breakout_idx = None
    breakout_time = None
    breakout_close = None

    for j in range(min_idx + 1, len(day_df)):
        r = day_df.iloc[j]
        c = float(r["close"])
        h = float(r["high"])
        prev_high = float(day_df.iloc[j - 1]["high"]) if j > 0 else h

        if c > low_high and h > low_high and h > prev_high:
            breakout_idx = j
            breakout_time = r["time"]
            breakout_close = c
            break

    if breakout_idx is None:
        print("  -> LOW pattern: no valid HH close-break > low_high after day low, skip")
        return None

    sustain_end_time = breakout_time + timedelta(minutes=15 * 6)
    sustain_mask = (
        (day_df["time"] > breakout_time) &
        (day_df["time"] <= sustain_end_time)
    )
    sustain_df = day_df.loc[sustain_mask]

    if not sustain_df.empty:
        min_low_in_window = float(sustain_df["low"].min())
        if min_low_in_window < day_low:
            print(
                f"  -> LOW pattern: new lower low {min_low_in_window:.5f} "
                f"during sustain (base={day_low:.5f}), skip"
            )
            return None

    derived = low_high + low_atr * 3.9

    gann_input = low_high
    gann_levels = engine._get_gann_from_lookup(gann_input)
    if not gann_levels:
        print("  -> Gann lookup failed for LOW pattern")
        return None

    levels = StrategyCalculator._extract_levels(gann_levels)
    buy_at = float(levels["buy_at"])

    gate_level = low_high + low_atr
    gate_passed = False
    after_breakout = day_df.loc[day_df["time"] >= breakout_time].copy()
    if not after_breakout.empty:
        gate_passed = bool(
            (after_breakout["close"].astype(float) >= gate_level).any())

    if gate_passed:
        if derived < buy_at:
            setup = StrategyCalculator.build_buy_from_buy_t1(
                gann_levels, fund, risk_percent, engine.pair
            )
            entry_mode = "BUY_T1"
        else:
            setup = StrategyCalculator.build_buy_from_buyat(
                gann_levels, fund, risk_percent, engine.pair
            )
            entry_mode = "BUY_AT"
    else:
        setup = StrategyCalculator.build_buy_from_buy_t1(
            gann_levels, fund, risk_percent, engine.pair
        )
        entry_mode = "BUY_T1"

    setup["trigger_time"] = breakout_time
    setup["picked_candle_time"] = picked_time
    setup["breakout_candle_time"] = breakout_time
    setup["breakout_close"] = breakout_close
    setup["compare_level"] = low_high
    setup["pivot_low"] = round(day_low, 5)
    setup["atr"] = round(low_atr, 5)
    setup["derived"] = round(derived, 5)
    setup["gate_level"] = round(gate_level, 5)
    setup["gate_passed"] = bool(gate_passed)
    setup["entry_mode"] = entry_mode

    print(
        f"  -> LOW pattern: picked_time={picked_time}, "
        f"low={day_low:.5f}, high={low_high:.5f}, "
        f"breakout_time={breakout_time}, breakout_close={breakout_close:.5f} > {low_high:.5f}, "
        f"ATR={low_atr:.5f}, derived={derived:.5f}, BUYAT={buy_at:.5f}, "
        f"gate_level={gate_level:.5f}, gate_passed={gate_passed}, "
        f"mode={entry_mode}, side={setup['side']}, "
        f"entry={setup['entry']:.5f}, SL={setup['sl']:.5f}, TP={setup['tp']:.5f}"
    )

    return setup


def build_high_setup_for_day(
    engine,
    day_df,
    fund: float,
    risk_percent: float,
) -> Optional[Dict]:
    """
    HIGH-side pattern (SELL setup)

    - Use global day high as base.
    - Wait for close < high_low AND breakout candle makes LL wick.
    - Check 1.5h sustain window; if a new higher high appears -> invalid.
    - derived = high_low - ATR * 3.9
    - Gann input = high_low
    - 1*ATR gate:
        - If gate passed:
            - derived > SELL_AT -> SELL_T1
            - else              -> SELL_AT
        - If gate fails:
            - force SELL_T1
    """
    if day_df.empty:
        return None

    day_df = day_df.sort_values("time").reset_index(drop=True)

    max_idx = day_df["high"].astype(float).idxmax()
    high_row = day_df.loc[max_idx]

    day_high = float(high_row["high"])
    high_low = float(high_row["low"])
    picked_time = high_row["time"]

    high_atr = float(high_row.get("atr", 0.0))
    if not np.isfinite(high_atr) or high_atr <= 0:
        found_atr = None
        for k in range(max_idx - 1, -1, -1):
            atr_val = float(day_df.iloc[k].get("atr", 0.0))
            if np.isfinite(atr_val) and atr_val > 0:
                found_atr = atr_val
                break

        if found_atr is not None:
            high_atr = found_atr
            print(
                f"  -> HIGH pattern: ATR at day high invalid, "
                f"using previous valid ATR={high_atr:.5f}"
            )
        else:
            print("  -> HIGH pattern: no valid ATR found before day high, skip")
            return None

    breakout_idx = None
    breakout_time = None
    breakout_close = None

    for j in range(max_idx + 1, len(day_df)):
        r = day_df.iloc[j]
        c = float(r["close"])
        l = float(r["low"])
        prev_low = float(day_df.iloc[j - 1]["low"]) if j > 0 else l

        if c < high_low and l < high_low and l < prev_low:
            breakout_idx = j
            breakout_time = r["time"]
            breakout_close = c
            break

    if breakout_idx is None:
        print("  -> HIGH pattern: no valid LL close-break < high_low after day high, skip")
        return None

    sustain_end_time = breakout_time + timedelta(minutes=15 * 6)
    sustain_mask = (
        (day_df["time"] > breakout_time) &
        (day_df["time"] <= sustain_end_time)
    )
    sustain_df = day_df.loc[sustain_mask]

    if not sustain_df.empty:
        max_high_in_window = float(sustain_df["high"].max())
        if max_high_in_window > day_high:
            print(
                f"  -> HIGH pattern: new higher high {max_high_in_window:.5f} "
                f"during sustain (base={day_high:.5f}), skip"
            )
            return None

    derived = high_low - high_atr * 3.9

    gann_input = high_low
    gann_levels = engine._get_gann_from_lookup(gann_input)
    if not gann_levels:
        print("  -> Gann lookup failed for HIGH pattern")
        return None

    levels = StrategyCalculator._extract_levels(gann_levels)
    sell_at = float(levels["sell_at"])

    gate_level = high_low - high_atr
    gate_passed = False
    after_breakout = day_df.loc[day_df["time"] >= breakout_time].copy()
    if not after_breakout.empty:
        gate_passed = bool(
            (after_breakout["close"].astype(float) <= gate_level).any())

    if gate_passed:
        if derived > sell_at:
            setup = StrategyCalculator.build_sell_from_sell_t1(
                gann_levels, fund, risk_percent, engine.pair
            )
            entry_mode = "SELL_T1"
        else:
            setup = StrategyCalculator.build_sell_from_sellat(
                gann_levels, fund, risk_percent, engine.pair
            )
            entry_mode = "SELL_AT"
    else:
        setup = StrategyCalculator.build_sell_from_sell_t1(
            gann_levels, fund, risk_percent, engine.pair
        )
        entry_mode = "SELL_T1"

    setup["trigger_time"] = breakout_time
    setup["picked_candle_time"] = picked_time
    setup["breakout_candle_time"] = breakout_time
    setup["breakout_close"] = breakout_close
    setup["compare_level"] = high_low
    setup["pivot_high"] = round(day_high, 5)
    setup["atr"] = round(high_atr, 5)
    setup["derived"] = round(derived, 5)
    setup["gate_level"] = round(gate_level, 5)
    setup["gate_passed"] = bool(gate_passed)
    setup["entry_mode"] = entry_mode

    print(
        f"  -> HIGH pattern: picked_time={picked_time}, "
        f"high={day_high:.5f}, low={high_low:.5f}, "
        f"breakout_time={breakout_time}, breakout_close={breakout_close:.5f} < {high_low:.5f}, "
        f"ATR={high_atr:.5f}, derived={derived:.5f}, SELLAT={sell_at:.5f}, "
        f"gate_level={gate_level:.5f}, gate_passed={gate_passed}, "
        f"mode={entry_mode}, side={setup['side']}, "
        f"entry={setup['entry']:.5f}, SL={setup['sl']:.5f}, TP={setup['tp']:.5f}"
    )

    return setup
