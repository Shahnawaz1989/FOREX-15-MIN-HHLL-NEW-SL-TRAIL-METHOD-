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
    if day_df.empty:
        return None

    day_df = day_df.sort_values("time").reset_index(drop=True)

    min_idx = day_df["low"].idxmin()
    low_row = day_df.loc[min_idx]

    day_low = float(low_row["low"])
    low_high = float(low_row["high"])
    picked_time = low_row["time"]
    low_atr = float(low_row.get("atr", 0.0))

    if not np.isfinite(low_atr) or low_atr <= 0:
        print(" -> LOW pattern: ATR invalid at day low, skip")
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
        print(" -> LOW pattern: no valid HH close-break > low_high after day low, skip")
        return None

    sustain_end_time = breakout_time + timedelta(minutes=15 * 6)
    sustain_mask = (day_df["time"] > breakout_time) & (
        day_df["time"] <= sustain_end_time)
    sustain_df = day_df.loc[sustain_mask]

    if not sustain_df.empty:
        min_low_in_window = float(sustain_df["low"].min())
        if min_low_in_window < day_low:
            print(
                f" -> LOW pattern: new lower low {min_low_in_window:.5f} "
                f"during sustain (base={day_low:.5f}), skip"
            )
            return None

    gann_input = low_high
    gann_levels = engine._get_gann_from_lookup(gann_input)
    if not gann_levels:
        print(" -> Gann lookup failed for LOW pattern")
        return None

    levels = StrategyCalculator._extract_levels(gann_levels)

    buy_at = float(levels["buy_at"])
    buy_t1 = float(levels["buy_t1"])
    buy_t15 = float(levels["buy_t15"])

    gate_level = low_high + low_atr
    r39_level = low_high + low_atr * 3.9

    gate_touched = gate_level > buy_at
    r39_touched = r39_level > buy_at

    print(
        f" -> LOW DEBUG: low_high={low_high:.5f}, AT={buy_at:.5f}, "
        f"gate_level={gate_level:.5f}, gate_touched={gate_touched}, "
        f"r39_level={r39_level:.5f}, r39_touched={r39_touched}"
    )

    selection = select_entry_target_from_gate_and_39(
        side="BUY",
        gate_touched=gate_touched,
        r39_touched=r39_touched,
        at=buy_at,
        t1=buy_t1,
        t15=buy_t15,
    )

    sl_price = buy_at if selection["entry_mode"] == "BUY_T1" else float(
        levels["sell_at"])

    setup = StrategyCalculator.build_custom_setup(
        side="B",
        entry=float(selection["entry_price"]),
        sl=sl_price,
        tp=float(selection["tp_price"]),
        fund=fund,
        risk_percent=risk_percent,
        pair=engine.pair,
        entry_mode=selection["entry_mode"],
        target_mode=selection["target_mode"],
    )

    setup["trigger_time"] = breakout_time
    setup["picked_candle_time"] = picked_time
    setup["breakout_candle_time"] = breakout_time
    setup["breakout_close"] = breakout_close
    setup["compare_level"] = low_high
    setup["gate_level"] = round(gate_level, 5)
    setup["r39_level"] = round(r39_level, 5)
    setup["gate_touched"] = bool(gate_touched)
    setup["r39_touched"] = bool(r39_touched)
    setup["atr"] = round(low_atr, 5)

    print(
        f" -> LOW pattern: picked_time={picked_time}, "
        f"low={day_low:.5f}, high={low_high:.5f}, "
        f"breakout_time={breakout_time}, breakout_close={breakout_close:.5f}, "
        f"ATR={low_atr:.5f}, gate_level={gate_level:.5f}, gate_touched={gate_touched}, "
        f"r39_level={r39_level:.5f}, r39_touched={r39_touched}, "
        f"mode={setup['entry_mode']}, target_mode={setup['target_mode']}, "
        f"entry={setup['entry']:.5f}, SL={setup['sl']:.5f}, TP={setup['tp']:.5f}"
    )

    return setup


def build_high_setup_for_day(
    engine,
    day_df,
    fund: float,
    risk_percent: float,
) -> Optional[Dict]:
    if day_df.empty:
        return None

    day_df = day_df.sort_values("time").reset_index(drop=True)

    max_idx = day_df["high"].idxmax()
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
                f" -> HIGH pattern: ATR at day high invalid, using previous valid ATR={high_atr:.5f}"
            )
        else:
            print(" -> HIGH pattern: no valid ATR found before day high, skip")
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
        print(" -> HIGH pattern: no valid LL close-break < high_low after day high, skip")
        return None

    sustain_end_time = breakout_time + timedelta(minutes=15 * 6)
    sustain_mask = (day_df["time"] > breakout_time) & (
        day_df["time"] <= sustain_end_time)
    sustain_df = day_df.loc[sustain_mask]

    if not sustain_df.empty:
        max_high_in_window = float(sustain_df["high"].max())
        if max_high_in_window > day_high:
            print(
                f" -> HIGH pattern: new higher high {max_high_in_window:.5f} "
                f"during sustain (base={day_high:.5f}), skip"
            )
            return None

    gann_input = high_low
    gann_levels = engine._get_gann_from_lookup(gann_input)
    if not gann_levels:
        print(" -> Gann lookup failed for HIGH pattern")
        return None

    levels = StrategyCalculator._extract_levels(gann_levels)

    sell_at = float(levels["sell_at"])
    sell_t1 = float(levels["sell_t1"])
    sell_t15 = float(levels["sell_t15"])

    gate_level = high_low - high_atr
    r39_level = high_low - high_atr * 3.9

    gate_touched = gate_level < sell_at
    r39_touched = r39_level < sell_at

    print(
        f" -> HIGH DEBUG: high_low={high_low:.5f}, AT={sell_at:.5f}, "
        f"gate_level={gate_level:.5f}, gate_touched={gate_touched}, "
        f"r39_level={r39_level:.5f}, r39_touched={r39_touched}"
    )

    selection = select_entry_target_from_gate_and_39(
        side="SELL",
        gate_touched=gate_touched,
        r39_touched=r39_touched,
        at=sell_at,
        t1=sell_t1,
        t15=sell_t15,
    )

    sl_price = sell_at if selection["entry_mode"] == "SELL_T1" else float(
        levels["buy_at"])

    setup = StrategyCalculator.build_custom_setup(
        side="S",
        entry=float(selection["entry_price"]),
        sl=sl_price,
        tp=float(selection["tp_price"]),
        fund=fund,
        risk_percent=risk_percent,
        pair=engine.pair,
        entry_mode=selection["entry_mode"],
        target_mode=selection["target_mode"],
    )

    setup["trigger_time"] = breakout_time
    setup["picked_candle_time"] = picked_time
    setup["breakout_candle_time"] = breakout_time
    setup["breakout_close"] = breakout_close
    setup["compare_level"] = high_low
    setup["gate_level"] = round(gate_level, 5)
    setup["r39_level"] = round(r39_level, 5)
    setup["gate_touched"] = bool(gate_touched)
    setup["r39_touched"] = bool(r39_touched)
    setup["atr"] = round(high_atr, 5)

    print(
        f" -> HIGH pattern: picked_time={picked_time}, "
        f"high={day_high:.5f}, low={high_low:.5f}, "
        f"breakout_time={breakout_time}, breakout_close={breakout_close:.5f}, "
        f"ATR={high_atr:.5f}, gate_level={gate_level:.5f}, gate_touched={gate_touched}, "
        f"r39_level={r39_level:.5f}, r39_touched={r39_touched}, "
        f"mode={setup['entry_mode']}, target_mode={setup['target_mode']}, "
        f"entry={setup['entry']:.5f}, SL={setup['sl']:.5f}, TP={setup['tp']:.5f}"
    )

    return setup
