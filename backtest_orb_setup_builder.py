from typing import Dict, Optional, List
import numpy as np
import pandas as pd
from strategy_calculator import StrategyCalculator


def _dbg(verbose: bool, msg: str):
    if verbose:
        print(msg)


def _round5(x):
    return round(float(x), 5)


def _gann_lookup_price(x: float) -> float:
    return np.floor(float(x) * 10000) / 10000.0


def _record_attempt(
    all_attempts: List[Dict],
    side: str,
    picked_time,
    status: str,
    reason: str,
    extra: Optional[Dict] = None,
):
    row = {
        "side": str(side).upper().strip(),
        "picked_candle_time": pd.Timestamp(picked_time) if picked_time is not None else None,
        "status": str(status).upper().strip(),
        "reason": str(reason),
    }
    if extra:
        row.update(extra)
    all_attempts.append(row)


def _find_nearest_buy_target(
    levels: Dict[str, float],
    target_price: float,
    entry_price: float,
) -> Optional[Dict]:
    order = ["buy_t05", "buy_t1", "buy_t125", "buy_t15", "buy_t2"]
    picks = []

    for key in order:
        px = levels.get(key)
        if px is None:
            continue
        px = float(px)

        if px <= float(entry_price):
            continue

        picks.append((abs(px - float(target_price)), px, key))

    if not picks:
        return None

    picks.sort(key=lambda x: (x[0], x[1]))
    _, px, key = picks[0]
    return {"target_key": key, "target_price": _round5(px)}


def _find_nearest_sell_target(
    levels: Dict[str, float],
    target_price: float,
    entry_price: float,
) -> Optional[Dict]:
    order = ["sell_t05", "sell_t1", "sell_t125", "sell_t15", "sell_t2"]
    picks = []

    for key in order:
        px = levels.get(key)
        if px is None:
            continue
        px = float(px)

        if px >= float(entry_price):
            continue

        picks.append((abs(px - float(target_price)), px, key))

    if not picks:
        return None

    picks.sort(key=lambda x: (x[0], x[1]))
    _, px, key = picks[0]
    return {"target_key": key, "target_price": _round5(px)}


def _is_15m_to_1h_positive(
    engine,
    candidate_atr: float,
    breakout_time,
    verbose: bool = False,
) -> Dict:
    h1 = getattr(engine, "h1_atr_df", pd.DataFrame())
    if (
        h1 is None
        or h1.empty
        or "time" not in h1.columns
        or "atr" not in h1.columns
    ):
        _dbg(verbose, " -> H1 ATR data missing")
        return {
            "valid": False,
            "h1_atr_raw": None,
            "h1_atr_cmp": None,
            "result_atr": None,
            "candidate_atr": _round5(candidate_atr) if candidate_atr is not None else None,
            "candidate_atr_cmp": None,
            "atr_last_filter_enabled": False,
            "atr_last_filter_valid": None,
            "atr_last_filter_threshold": None,
            "atr_last_filter_value": None,
        }

    xh1 = h1.copy()
    xh1["time"] = pd.to_datetime(xh1["time"], errors="coerce")
    xh1["atr"] = pd.to_numeric(xh1["atr"], errors="coerce")
    xh1 = (
        xh1.dropna(subset=["time", "atr"])
        .sort_values("time")
        .reset_index(drop=True)
    )
    if xh1.empty:
        return {
            "valid": False,
            "h1_atr_raw": None,
            "h1_atr_cmp": None,
            "result_atr": None,
            "candidate_atr": _round5(candidate_atr) if candidate_atr is not None else None,
            "candidate_atr_cmp": None,
            "atr_last_filter_enabled": False,
            "atr_last_filter_valid": None,
            "atr_last_filter_threshold": None,
            "atr_last_filter_value": None,
        }

    bt = pd.Timestamp(breakout_time)
    eligible = xh1.loc[xh1["time"] <= bt]
    h1_row = eligible.iloc[-1] if not eligible.empty else xh1.iloc[0]

    h1_atr_raw = float(h1_row["atr"])
    m15_atr_raw = float(candidate_atr)

    h1_atr_cmp = round(h1_atr_raw * 100000, 0)
    m15_atr_cmp = round(m15_atr_raw * 100000, 0)

    if h1_atr_cmp <= 150:
        result_atr_cmp = h1_atr_cmp / 2.5
    else:
        result_atr_cmp = h1_atr_cmp / 2.70

    result_atr_cmp = round(float(result_atr_cmp), 2)
    base_ok = m15_atr_cmp > result_atr_cmp

    last_filter_enabled = False
    last_filter_value = None
    last_filter_threshold = None
    last_filter_ok = True

    ok = base_ok

    last_filter_value_str = f"{last_filter_value:.2f}" if last_filter_value is not None else "NA"
    last_filter_threshold_str = f"{last_filter_threshold:.2f}" if last_filter_threshold is not None else "NA"

    _dbg(
        verbose,
        (
            " -> ATR_CHECK | "
            f"time={bt} | "
            f"H1_raw={h1_atr_raw:.5f} | "
            f"H1_cmp={h1_atr_cmp:.0f} | "
            f"H1_result_cmp={result_atr_cmp:.2f} | "
            f"M15_candidate={m15_atr_raw:.5f} | "
            f"M15_cmp={m15_atr_cmp:.0f} | "
            f"LAST_FILTER_ENABLED={last_filter_enabled} | "
            f"LAST_FILTER_VALUE={last_filter_value_str} | "
            f"LAST_FILTER_THRESHOLD={last_filter_threshold_str} | "
            f"LAST_FILTER_VALID={last_filter_ok} | "
            f"valid={ok}"
        ),
    )

    return {
        "valid": ok,
        "h1_atr_raw": round(h1_atr_raw, 5),
        "h1_atr_cmp": float(h1_atr_cmp),
        "result_atr": float(result_atr_cmp),
        "candidate_atr": round(m15_atr_raw, 5),
        "candidate_atr_cmp": float(m15_atr_cmp),
        "atr_last_filter_enabled": bool(last_filter_enabled),
        "atr_last_filter_valid": bool(last_filter_ok) if last_filter_enabled else None,
        "atr_last_filter_threshold": float(last_filter_threshold) if last_filter_enabled else None,
        "atr_last_filter_value": float(last_filter_value) if last_filter_enabled else None,
    }


def _build_ll_buy_setup(
    engine,
    xdf: pd.DataFrame,
    all_attempts: List[Dict],
    hh_debug: bool = False,
    gap_info: Optional[Dict] = None,
) -> Optional[Dict]:
    side = "BUY"
    ll_idx = xdf["low"].idxmin()
    ll_row = xdf.loc[ll_idx]

    ll_low = float(ll_row["low"])
    ll_high = float(ll_row["high"])
    picked_time = pd.Timestamp(ll_row["time"])

    pickup_atr = float(ll_row.get("atr", np.nan))
    if not np.isfinite(pickup_atr) or pickup_atr <= 0:
        _record_attempt(all_attempts, side, picked_time,
                        "REJECTED", "pickup_atr_invalid")
        return None

    pickup_open = float(ll_row["open"])
    pickup_close = float(ll_row["close"])
    pickup_oc_gap = _round5(abs(pickup_open - pickup_close))
    pickup_atr15 = _round5(pickup_atr / 15.0)

    if pickup_oc_gap < pickup_atr15:
        _dbg(
            hh_debug,
            f" -> PICKUP_OC_FILTER FAIL | side=BUY | time={picked_time} | "
            f"oc_gap={pickup_oc_gap:.5f} < atr15={pickup_atr15:.5f}",
        )
        _record_attempt(
            all_attempts, side, picked_time, "REJECTED", "pickup_oc_filter_failed"
        )
        return None

    pickup_buffer = pickup_atr / 5.0
    pickup_breakout_level = ll_high + pickup_buffer

    breakout_row = None
    for j in range(ll_idx + 1, len(xdf)):
        r = xdf.iloc[j]
        c = float(r["close"])
        if c >= pickup_breakout_level:
            breakout_row = r
            break

    if breakout_row is None:
        _record_attempt(all_attempts, side, picked_time,
                        "REJECTED", "no_breakout_found")
        return None

    breakout_time = pd.Timestamp(breakout_row["time"])
    breakout_high = float(breakout_row["high"])
    breakout_close = float(breakout_row["close"])

    candidate_atr = float(breakout_row.get("atr", np.nan))
    if not np.isfinite(candidate_atr) or candidate_atr <= 0:
        _record_attempt(all_attempts, side, picked_time,
                        "REJECTED", "candidate_atr_invalid")
        return None

    atr_check = _is_15m_to_1h_positive(
        engine, candidate_atr, breakout_time, verbose=hh_debug
    )
    if not atr_check["valid"]:
        if atr_check.get("atr_last_filter_enabled") and atr_check.get("atr_last_filter_valid") is False:
            _record_attempt(all_attempts, side, picked_time,
                            "REJECTED", "atr_last_roundoff_x2_failed")
        else:
            _record_attempt(all_attempts, side, picked_time,
                            "REJECTED", "atr_compare_failed")
        return None

    gann_cmp = _gann_lookup_price(breakout_high)
    gann_levels = engine._get_gann_from_lookup(gann_cmp)
    if not gann_levels:
        _record_attempt(all_attempts, side, picked_time,
                        "REJECTED", "gann_lookup_failed")
        return None

    levels = StrategyCalculator._extract_levels(gann_levels)
    if gann_levels.get("buy_at") is None:
        _record_attempt(all_attempts, side, picked_time,
                        "REJECTED", "buy_at_missing")
        return None

    base_entry_price = _round5(float(gann_levels["buy_at"]))

    gap_status = str((gap_info or {}).get("status", "")).upper()
    first_candle_time = pd.Timestamp(xdf.iloc[0]["time"])
    pickup_is_first_candle = pd.Timestamp(
        picked_time) == pd.Timestamp(first_candle_time)

    special_gap_pickup_rule = gap_status in (
        "GAP_UP", "GAP_DOWN") and pickup_is_first_candle

    h1_cmp_value = float(atr_check.get("h1_atr_cmp") or 0.0)
    special_atr_multiplier = 3.0 if h1_cmp_value < 50.0 else 1.0

    special_entry_cmp_raw = float(
        breakout_high + (candidate_atr * special_atr_multiplier)
    )
    special_entry_cmp = _round5(special_entry_cmp_raw)
    special_entry_applied = False

    final_entry_price = base_entry_price
    final_levels = levels
    final_gann_levels = gann_levels
    final_gann_cmp = gann_cmp
    final_tp_mode = None
    final_tp_key = None
    final_tp = None
    final_sl = None

    if special_entry_cmp_raw >= base_entry_price:
        special_cmp = _gann_lookup_price(special_entry_cmp_raw)
        special_gann = engine._get_gann_from_lookup(special_cmp)
        if not special_gann:
            _record_attempt(all_attempts, side, picked_time,
                            "REJECTED", "special_gann_lookup_failed")
            return None

        special_levels = StrategyCalculator._extract_levels(special_gann)
        if special_gann.get("buy_at") is None:
            _record_attempt(all_attempts, side, picked_time,
                            "REJECTED", "special_buy_at_missing")
            return None

        special_entry_price = _round5(float(special_gann["buy_at"]))

        if special_levels.get("buy_t1") is None:
            _record_attempt(all_attempts, side, picked_time,
                            "REJECTED", "special_buy_t1_missing")
            return None
        if special_levels.get("sell_super_middle") is None:
            _record_attempt(all_attempts, side, picked_time,
                            "REJECTED", "REJECTED", "special_sell_super_middle_missing")
            return None

        final_entry_price = special_entry_price
        final_levels = special_levels
        final_gann_levels = special_gann
        final_gann_cmp = special_cmp
        final_tp_mode = "SPECIAL_CMP_CROSS"
        final_tp_key = "buy_t1"
        final_tp = float(final_levels["buy_t1"])
        final_sl = _round5(float(final_levels["sell_super_middle"]))
        special_entry_applied = True
    else:
        raw_target_result = _round5(breakout_high + candidate_atr * 5.0)

        if raw_target_result <= final_entry_price:
            final_tp_mode = "TP_EARLY"
            if final_levels.get("buy_t1") is None:
                _record_attempt(all_attempts, side, picked_time,
                                "REJECTED", "buy_t1_missing")
                return None
            final_tp_key = "buy_t1"
            final_tp = float(final_levels["buy_t1"])
        else:
            final_tp_mode = "TP_AFTER"
            nearest = _find_nearest_buy_target(
                final_levels, raw_target_result, final_entry_price
            )
            if not nearest:
                if final_levels.get("buy_t1") is None:
                    _record_attempt(all_attempts, side,
                                    picked_time, "REJECTED", "buy_t1_missing")
                    return None
                final_tp_key = "buy_t1"
                final_tp = float(final_levels["buy_t1"])
            else:
                final_tp_key = nearest["target_key"]
                final_tp = float(nearest["target_price"])

            if final_tp <= final_entry_price:
                if final_levels.get("buy_t1") is None:
                    _record_attempt(all_attempts, side,
                                    picked_time, "REJECTED", "buy_t1_missing")
                    return None
                final_tp_key = "buy_t1"
                final_tp = float(final_levels["buy_t1"])

        if final_levels.get("sell_super_middle") is None:
            _record_attempt(all_attempts, side, picked_time,
                            "REJECTED", "sell_super_middle_missing")
            return None

        final_sl = _round5(
            float(final_levels["sell_super_middle"]) - (candidate_atr / 10.0)
        )

        if (
            gap_status in ("GAP_UP", "GAP_DOWN")
            and pickup_is_first_candle
            and final_tp_mode == "TP_EARLY"
        ):
            _dbg(
                hh_debug,
                " -> GAP_FIRST_PICKUP_EARLY_REJECT | side=BUY | "
                f"gap_status={gap_status} | picked_time={picked_time} | "
                f"special_cross=False | tp_mode={final_tp_mode}",
            )
            _record_attempt(all_attempts, side, picked_time,
                            "REJECTED", "gap_first_pickup_early_reject")
            return None

    if final_tp is None or final_sl is None:
        _record_attempt(all_attempts, side, picked_time,
                        "REJECTED", "tp_or_sl_missing")
        return None

    raw_target_result = _round5(breakout_high + candidate_atr * 5.0)

    sl_pips = StrategyCalculator._calc_sl_pips(
        final_entry_price, final_sl, engine.pair
    )
    lot = StrategyCalculator.calculate_lot_size(
        fund=float(getattr(engine, "current_fund", 0.0)),
        risk_percent=float(getattr(engine, "initial_risk_percent", 0.0)),
        sl_pips=sl_pips,
        pair=engine.pair,
        entry=final_entry_price,
    )

    setup = {
        "side": "BUY",
        "pattern": "LL",
        "entry": _round5(final_entry_price),
        "sl": _round5(final_sl),
        "tp": _round5(final_tp),
        "lot_size": lot,
        "sl_pips": round(float(sl_pips), 1),
        "entry_mode": "BUY_AT",
        "entry_key": "buy_at",
        "target_mode": final_tp_key.upper() if final_tp_key else None,
        "tp_mode": final_tp_mode,
        "trigger_time": breakout_time,
        "picked_candle_time": picked_time,
        "breakout_candle_time": breakout_time,
        "pivot_low": _round5(ll_low),
        "pivot_ref": _round5(ll_low),
        "base_level": _round5(ll_high),
        "breakout_extreme": _round5(breakout_high),
        "breakout_close": _round5(breakout_close),
        "pickup_atr": _round5(pickup_atr),
        "pickup_open": _round5(pickup_open),
        "pickup_close": _round5(pickup_close),
        "pickup_candle_oc_gap": _round5(pickup_oc_gap),
        "pickup_atr15": _round5(pickup_atr15),
        "pickup_oc_filter_valid": pickup_oc_gap >= pickup_atr15,
        "pickup_buffer_atr5": _round5(pickup_buffer),
        "pickup_breakout_level": _round5(pickup_breakout_level),
        "candidate_breakout_atr": _round5(candidate_atr),
        "target_result_price": _round5(raw_target_result),
        "gann_cmp": _round5(final_gann_cmp),
        "gann_lookup_cmp": _round5(final_gann_cmp),
        "gann_raw_lookup_input": final_gann_levels.get("raw_lookup_input"),
        "gann_matched_price": final_gann_levels.get("matched_price"),
        "gann_levels": final_levels,
        "atr_compare_h1_raw": atr_check["h1_atr_raw"],
        "atr_compare_h1_round": atr_check["h1_atr_cmp"],
        "atr_compare_h1_result": atr_check["result_atr"],
        "atr_compare_m15_candidate": atr_check["candidate_atr"],
        "atr_compare_m15_candidate_cmp": atr_check["candidate_atr_cmp"],
        "atr_compare_valid": atr_check["valid"],
        "atr_last_filter_enabled": atr_check.get("atr_last_filter_enabled"),
        "atr_last_filter_valid": atr_check.get("atr_last_filter_valid"),
        "atr_last_filter_threshold": atr_check.get("atr_last_filter_threshold"),
        "atr_last_filter_value": atr_check.get("atr_last_filter_value"),
        "sl_source": "SELL_SUPER_MIDDLE" if special_entry_applied else "SELL_SUPER_MIDDLE_MINUS_ATR10",
        "tp_source": final_tp_key,
        "gap_status": gap_status,
        "pickup_is_first_candle": pickup_is_first_candle,
        "special_gap_pickup_rule": special_gap_pickup_rule,
        "special_entry_cmp_raw": _round5(special_entry_cmp_raw),
        "special_entry_cmp": _round5(special_entry_cmp),
        "special_entry_applied": special_entry_applied,
        "special_entry_atr_multiplier": special_atr_multiplier,
        "special_entry_h1_below_50_active": h1_cmp_value < 50.0,
        "status": "PENDING",
        "setup_valid": True,
    }

    _record_attempt(
        all_attempts,
        side,
        picked_time,
        "VALID",
        "valid",
        extra={
            "trigger_time": breakout_time,
            "entry": setup["entry"],
            "sl": setup["sl"],
            "tp": setup["tp"],
        },
    )
    return setup


def _build_hh_sell_setup(
    engine,
    xdf: pd.DataFrame,
    all_attempts: List[Dict],
    hh_debug: bool = False,
    gap_info: Optional[Dict] = None,
) -> Optional[Dict]:
    side = "SELL"
    hh_idx = xdf["high"].idxmax()
    hh_row = xdf.loc[hh_idx]

    hh_high = float(hh_row["high"])
    hh_low = float(hh_row["low"])
    picked_time = pd.Timestamp(hh_row["time"])

    pickup_atr = float(hh_row.get("atr", np.nan))
    if not np.isfinite(pickup_atr) or pickup_atr <= 0:
        _record_attempt(all_attempts, side, picked_time,
                        "REJECTED", "pickup_atr_invalid")
        return None

    pickup_open = float(hh_row["open"])
    pickup_close = float(hh_row["close"])
    pickup_oc_gap = _round5(abs(pickup_open - pickup_close))
    pickup_atr15 = _round5(pickup_atr / 15.0)

    if pickup_oc_gap < pickup_atr15:
        _dbg(
            hh_debug,
            f" -> PICKUP_OC_FILTER FAIL | side=SELL | time={picked_time} | "
            f"oc_gap={pickup_oc_gap:.5f} < atr15={pickup_atr15:.5f}",
        )
        _record_attempt(
            all_attempts, side, picked_time, "REJECTED", "pickup_oc_filter_failed"
        )
        return None

    pickup_buffer = pickup_atr / 5.0
    pickup_breakout_level = hh_low - pickup_buffer

    breakout_row = None
    for j in range(hh_idx + 1, len(xdf)):
        r = xdf.iloc[j]
        c = float(r["close"])
        if c <= pickup_breakout_level:
            breakout_row = r
            break

    if breakout_row is None:
        _record_attempt(all_attempts, side, picked_time,
                        "REJECTED", "no_breakout_found")
        return None

    breakout_time = pd.Timestamp(breakout_row["time"])
    breakout_low = float(breakout_row["low"])
    breakout_close = float(breakout_row["close"])

    candidate_atr = float(breakout_row.get("atr", np.nan))
    if not np.isfinite(candidate_atr) or candidate_atr <= 0:
        _record_attempt(all_attempts, side, picked_time,
                        "REJECTED", "candidate_atr_invalid")
        return None

    atr_check = _is_15m_to_1h_positive(
        engine, candidate_atr, breakout_time, verbose=hh_debug
    )
    if not atr_check["valid"]:
        if atr_check.get("atr_last_filter_enabled") and atr_check.get("atr_last_filter_valid") is False:
            _record_attempt(all_attempts, side, picked_time,
                            "REJECTED", "atr_last_roundoff_x2_failed")
        else:
            _record_attempt(all_attempts, side, picked_time,
                            "REJECTED", "atr_compare_failed")
        return None

    gann_cmp = _gann_lookup_price(breakout_low)
    gann_levels = engine._get_gann_from_lookup(gann_cmp)
    if not gann_levels:
        _record_attempt(all_attempts, side, picked_time,
                        "REJECTED", "gann_lookup_failed")
        return None

    levels = StrategyCalculator._extract_levels(gann_levels)
    if gann_levels.get("sell_at") is None:
        _record_attempt(all_attempts, side, picked_time,
                        "REJECTED", "sell_at_missing")
        return None

    base_entry_price = _round5(float(gann_levels["sell_at"]))

    gap_status = str((gap_info or {}).get("status", "")).upper()
    first_candle_time = pd.Timestamp(xdf.iloc[0]["time"])
    pickup_is_first_candle = pd.Timestamp(
        picked_time) == pd.Timestamp(first_candle_time)

    special_gap_pickup_rule = gap_status in (
        "GAP_UP", "GAP_DOWN") and pickup_is_first_candle

    h1_cmp_value = float(atr_check.get("h1_atr_cmp") or 0.0)
    special_atr_multiplier = 3.0 if h1_cmp_value < 50.0 else 1.0

    special_entry_cmp_raw = float(
        breakout_low - (candidate_atr * special_atr_multiplier)
    )
    special_entry_cmp = _round5(special_entry_cmp_raw)
    special_entry_applied = False

    final_entry_price = base_entry_price
    final_levels = levels
    final_gann_levels = gann_levels
    final_gann_cmp = gann_cmp
    final_tp_mode = None
    final_tp_key = None
    final_tp = None
    final_sl = None

    if special_entry_cmp_raw <= base_entry_price:
        special_cmp = _gann_lookup_price(special_entry_cmp_raw)
        special_gann = engine._get_gann_from_lookup(special_cmp)
        if not special_gann:
            _record_attempt(all_attempts, side, picked_time,
                            "REJECTED", "special_gann_lookup_failed")
            return None

        special_levels = StrategyCalculator._extract_levels(special_gann)
        if special_gann.get("sell_at") is None:
            _record_attempt(all_attempts, side, picked_time,
                            "REJECTED", "special_sell_at_missing")
            return None

        special_entry_price = _round5(float(special_gann["sell_at"]))

        if special_levels.get("sell_t1") is None:
            _record_attempt(all_attempts, side, picked_time,
                            "REJECTED", "special_sell_t1_missing")
            return None
        if special_levels.get("buy_super_middle") is None:
            _record_attempt(all_attempts, side, picked_time,
                            "REJECTED", "special_buy_super_middle_missing")
            return None

        final_entry_price = special_entry_price
        final_levels = special_levels
        final_gann_levels = special_gann
        final_gann_cmp = special_cmp
        final_tp_mode = "SPECIAL_CMP_CROSS"
        final_tp_key = "sell_t1"
        final_tp = float(final_levels["sell_t1"])
        final_sl = _round5(float(final_levels["buy_super_middle"]))
        special_entry_applied = True
    else:
        raw_target_result = _round5(breakout_low - candidate_atr * 5.0)

        if raw_target_result >= final_entry_price:
            final_tp_mode = "TP_EARLY"
            if final_levels.get("sell_t1") is None:
                _record_attempt(all_attempts, side, picked_time,
                                "REJECTED", "sell_t1_missing")
                return None
            final_tp_key = "sell_t1"
            final_tp = float(final_levels["sell_t1"])
        else:
            final_tp_mode = "TP_AFTER"
            nearest = _find_nearest_sell_target(
                final_levels, raw_target_result, final_entry_price
            )
            if not nearest:
                if final_levels.get("sell_t1") is None:
                    _record_attempt(all_attempts, side, picked_time,
                                    "REJECTED", "sell_t1_missing")
                    return None
                final_tp_key = "sell_t1"
                final_tp = float(final_levels["sell_t1"])
            else:
                final_tp_key = nearest["target_key"]
                final_tp = float(nearest["target_price"])

            if final_tp >= final_entry_price:
                if final_levels.get("sell_t1") is None:
                    _record_attempt(all_attempts, side, picked_time,
                                    "REJECTED", "sell_t1_missing")
                    return None
                final_tp_key = "sell_t1"
                final_tp = float(final_levels["sell_t1"])

        if final_levels.get("buy_super_middle") is None:
            _record_attempt(all_attempts, side, picked_time,
                            "REJECTED", "buy_super_middle_missing")
            return None

        final_sl = _round5(
            float(final_levels["buy_super_middle"]) + (candidate_atr / 10.0)
        )

        if (
            gap_status in ("GAP_UP", "GAP_DOWN")
            and pickup_is_first_candle
            and final_tp_mode == "TP_EARLY"
        ):
            _dbg(
                hh_debug,
                " -> GAP_FIRST_PICKUP_EARLY_REJECT | side=SELL | "
                f"gap_status={gap_status} | picked_time={picked_time} | "
                f"special_cross=False | tp_mode={final_tp_mode}",
            )
            _record_attempt(all_attempts, side, picked_time,
                            "REJECTED", "gap_first_pickup_early_reject")
            return None

    if final_tp is None or final_sl is None:
        _record_attempt(all_attempts, side, picked_time,
                        "REJECTED", "tp_or_sl_missing")
        return None

    raw_target_result = _round5(breakout_low - candidate_atr * 5.0)

    sl_pips = StrategyCalculator._calc_sl_pips(
        final_entry_price, final_sl, engine.pair
    )
    lot = StrategyCalculator.calculate_lot_size(
        fund=float(getattr(engine, "current_fund", 0.0)),
        risk_percent=float(getattr(engine, "initial_risk_percent", 0.0)),
        sl_pips=sl_pips,
        pair=engine.pair,
        entry=final_entry_price,
    )

    setup = {
        "side": "SELL",
        "pattern": "HH",
        "entry": _round5(final_entry_price),
        "sl": _round5(final_sl),
        "tp": _round5(final_tp),
        "lot_size": lot,
        "sl_pips": round(float(sl_pips), 1),
        "entry_mode": "SELL_AT",
        "entry_key": "sell_at",
        "target_mode": final_tp_key.upper() if final_tp_key else None,
        "tp_mode": final_tp_mode,
        "trigger_time": breakout_time,
        "picked_candle_time": picked_time,
        "breakout_candle_time": breakout_time,
        "pivot_high": _round5(hh_high),
        "pivot_ref": _round5(hh_high),
        "base_level": _round5(hh_low),
        "breakout_extreme": _round5(breakout_low),
        "breakout_close": _round5(breakout_close),
        "pickup_atr": _round5(pickup_atr),
        "pickup_open": _round5(pickup_open),
        "pickup_close": _round5(pickup_close),
        "pickup_candle_oc_gap": _round5(pickup_oc_gap),
        "pickup_atr15": _round5(pickup_atr15),
        "pickup_oc_filter_valid": pickup_oc_gap >= pickup_atr15,
        "pickup_buffer_atr5": _round5(pickup_buffer),
        "pickup_breakout_level": _round5(pickup_breakout_level),
        "candidate_breakout_atr": _round5(candidate_atr),
        "target_result_price": _round5(raw_target_result),
        "gann_cmp": _round5(final_gann_cmp),
        "gann_lookup_cmp": _round5(final_gann_cmp),
        "gann_raw_lookup_input": final_gann_levels.get("raw_lookup_input"),
        "gann_matched_price": final_gann_levels.get("matched_price"),
        "gann_levels": final_levels,
        "atr_compare_h1_raw": atr_check["h1_atr_raw"],
        "atr_compare_h1_round": atr_check["h1_atr_cmp"],
        "atr_compare_h1_result": atr_check["result_atr"],
        "atr_compare_m15_candidate": atr_check["candidate_atr"],
        "atr_compare_m15_candidate_cmp": atr_check["candidate_atr_cmp"],
        "atr_compare_valid": atr_check["valid"],
        "atr_last_filter_enabled": atr_check.get("atr_last_filter_enabled"),
        "atr_last_filter_valid": atr_check.get("atr_last_filter_valid"),
        "atr_last_filter_threshold": atr_check.get("atr_last_filter_threshold"),
        "atr_last_filter_value": atr_check.get("atr_last_filter_value"),
        "sl_source": "BUY_SUPER_MIDDLE" if special_entry_applied else "BUY_SUPER_MIDDLE_PLUS_ATR10",
        "tp_source": final_tp_key,
        "gap_status": gap_status,
        "pickup_is_first_candle": pickup_is_first_candle,
        "special_gap_pickup_rule": special_gap_pickup_rule,
        "special_entry_cmp_raw": _round5(special_entry_cmp_raw),
        "special_entry_cmp": _round5(special_entry_cmp),
        "special_entry_applied": special_entry_applied,
        "special_entry_atr_multiplier": special_atr_multiplier,
        "special_entry_h1_below_50_active": h1_cmp_value < 50.0,
        "status": "PENDING",
        "setup_valid": True,
    }

    _record_attempt(
        all_attempts,
        side,
        picked_time,
        "VALID",
        "valid",
        extra={
            "trigger_time": breakout_time,
            "entry": setup["entry"],
            "sl": setup["sl"],
            "tp": setup["tp"],
        },
    )
    return setup


def build_setup_for_day(
    engine,
    day_df,
    fund: float = 0.0,
    risk_percent: float = 0.0,
    verbose: bool = False,
    hh_debug: bool = False,
    gap_info: Optional[Dict] = None,
):
    if day_df is None or day_df.empty:
        return {"chosen_setups": [], "all_setups": []}

    xdf = day_df.copy()
    xdf["time"] = pd.to_datetime(xdf["time"], errors="coerce")
    xdf = (
        xdf.dropna(subset=["time"])
        .sort_values("time")
        .reset_index(drop=True)
    )

    for col in ["open", "high", "low", "close", "atr"]:
        if col in xdf.columns:
            xdf[col] = pd.to_numeric(xdf[col], errors="coerce")

    xdf = (
        xdf.dropna(subset=["open", "high", "low", "close", "atr"])
        .reset_index(drop=True)
    )
    if xdf.empty:
        return {"chosen_setups": [], "all_setups": []}

    all_attempts: List[Dict] = []

    buy_setup = _build_ll_buy_setup(
        engine, xdf, all_attempts=all_attempts, hh_debug=hh_debug, gap_info=gap_info
    )
    sell_setup = _build_hh_sell_setup(
        engine, xdf, all_attempts=all_attempts, hh_debug=hh_debug, gap_info=gap_info
    )

    chosen_setups: List[Dict] = []
    if buy_setup:
        chosen_setups.append(buy_setup)
    if sell_setup:
        chosen_setups.append(sell_setup)

    chosen_setups.sort(key=lambda s: pd.Timestamp(s["trigger_time"]))

    if buy_setup and sell_setup:
        _dbg(
            verbose,
            f" -> Both setups found. BUY trigger={pd.Timestamp(buy_setup['trigger_time'])}, "
            f"SELL trigger={pd.Timestamp(sell_setup['trigger_time'])}, returning BOTH",
        )

    return {
        "chosen_setups": chosen_setups,
        "all_setups": all_attempts,
    }


def invalidate_pending_setup_on_new_pivot(
    setup: Optional[Dict],
    intraday_df: pd.DataFrame,
) -> Dict:
    if not setup:
        return {
            "active_setup": None,
            "cancelled": False,
            "reason": "setup_missing",
        }

    if intraday_df is None or intraday_df.empty:
        return {
            "active_setup": setup,
            "cancelled": False,
            "reason": "intraday_df_missing",
        }

    if str(setup.get("status", "")).upper() != "PENDING":
        return {
            "active_setup": setup,
            "cancelled": False,
            "reason": "setup_not_pending",
        }

    side = str(setup.get("side", "")).upper().strip()
    trigger_time = pd.Timestamp(setup["trigger_time"])
    entry_price = float(setup["entry"])

    xdf = intraday_df.copy()
    xdf["time"] = pd.to_datetime(xdf["time"], errors="coerce")

    if "low" in xdf.columns:
        xdf["low"] = pd.to_numeric(xdf["low"], errors="coerce")
    if "high" in xdf.columns:
        xdf["high"] = pd.to_numeric(xdf["high"], errors="coerce")

    xdf = (
        xdf.dropna(subset=["time"])
        .sort_values("time")
        .reset_index(drop=True)
    )

    after_trigger = xdf.loc[xdf["time"] > trigger_time].copy()

    if after_trigger.empty:
        return {
            "active_setup": setup,
            "cancelled": False,
            "reason": "no_data_after_trigger",
        }

    cancelled_setup = dict(setup)

    if side == "BUY":
        pivot_low = float(setup["pivot_low"])

        fill_chk = after_trigger.dropna(subset=["high"])
        fill_hit = fill_chk.loc[fill_chk["high"] >= entry_price]
        first_fill_time = pd.Timestamp(
            fill_hit.iloc[0]["time"]) if not fill_hit.empty else None

        inv_chk = after_trigger.dropna(subset=["low"])
        inv_hit = inv_chk.loc[inv_chk["low"] < pivot_low]
        first_inv_time = pd.Timestamp(
            inv_hit.iloc[0]["time"]) if not inv_hit.empty else None

        if first_inv_time is None:
            return {
                "active_setup": setup,
                "cancelled": False,
                "reason": "no_new_ll",
            }

        if first_fill_time is not None and first_fill_time <= first_inv_time:
            return {
                "active_setup": setup,
                "cancelled": False,
                "reason": "entry_hit_before_new_ll",
            }

        first_hit = inv_hit.iloc[0]
        cancelled_setup["status"] = "CANCELLED"
        cancelled_setup["setup_valid"] = False
        cancelled_setup["cancel_reason"] = "new_lower_low_found_while_pending"
        cancelled_setup["cancel_time"] = pd.Timestamp(first_hit["time"])
        cancelled_setup["new_pivot_low"] = round(float(first_hit["low"]), 5)

        return {
            "active_setup": cancelled_setup,
            "cancelled": True,
            "reason": "new_lower_low_found_while_pending",
        }

    if side == "SELL":
        pivot_high = float(setup["pivot_high"])

        fill_chk = after_trigger.dropna(subset=["low"])
        fill_hit = fill_chk.loc[fill_chk["low"] <= entry_price]
        first_fill_time = pd.Timestamp(
            fill_hit.iloc[0]["time"]) if not fill_hit.empty else None

        inv_chk = after_trigger.dropna(subset=["high"])
        inv_hit = inv_chk.loc[inv_chk["high"] > pivot_high]
        first_inv_time = pd.Timestamp(
            inv_hit.iloc[0]["time"]) if not inv_hit.empty else None

        if first_inv_time is None:
            return {
                "active_setup": setup,
                "cancelled": False,
                "reason": "no_new_hh",
            }

        if first_fill_time is not None and first_fill_time <= first_inv_time:
            return {
                "active_setup": setup,
                "cancelled": False,
                "reason": "entry_hit_before_new_hh",
            }

        first_hit = inv_hit.iloc[0]
        cancelled_setup["status"] = "CANCELLED"
        cancelled_setup["setup_valid"] = False
        cancelled_setup["cancel_reason"] = "new_higher_high_found_while_pending"
        cancelled_setup["cancel_time"] = pd.Timestamp(first_hit["time"])
        cancelled_setup["new_pivot_high"] = round(float(first_hit["high"]), 5)

        return {
            "active_setup": cancelled_setup,
            "cancelled": True,
            "reason": "new_higher_high_found_while_pending",
        }

    return {
        "active_setup": setup,
        "cancelled": False,
        "reason": "unknown_side",
    }
