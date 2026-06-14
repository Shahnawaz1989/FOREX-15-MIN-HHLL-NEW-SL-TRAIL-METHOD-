# backtest_orb_runner_helpers.py
import pandas as pd
from typing import Dict
from backtest_orb_setup_builder import build_setup_for_day


def detect_day_gap(df: pd.DataFrame, day) -> Dict:
    if df is None or df.empty:
        return {"status": "NO_DATA"}

    xdf = df.copy()
    xdf["time"] = pd.to_datetime(xdf["time"], errors="coerce")
    xdf = xdf.dropna(subset=["time"]).sort_values(
        "time").reset_index(drop=True)

    for col in ["open", "high", "low", "close"]:
        if col in xdf.columns:
            xdf[col] = pd.to_numeric(xdf[col], errors="coerce")

    xdf = xdf.dropna(subset=["high", "low"]).reset_index(drop=True)
    if xdf.empty:
        return {"status": "NO_DATA"}

    xdf["date_only"] = xdf["time"].dt.date
    target_day = pd.Timestamp(day).date()

    all_days = sorted(xdf["date_only"].dropna().unique().tolist())
    if target_day not in all_days:
        return {"status": "NO_TODAY"}

    pos = all_days.index(target_day)
    if pos == 0:
        return {"status": "NO_PREV"}

    prev_day = all_days[pos - 1]

    prev_df = xdf.loc[xdf["date_only"] == prev_day].copy()
    today_df = xdf.loc[xdf["date_only"] == target_day].copy()

    if prev_df.empty:
        return {"status": "NO_PREV"}
    if today_df.empty:
        return {"status": "NO_TODAY"}

    prev_last = prev_df.sort_values("time").iloc[-1]
    today_first = today_df.sort_values("time").iloc[0]

    prev_time = pd.Timestamp(prev_last["time"])
    today_time = pd.Timestamp(today_first["time"])

    prev_high = float(prev_last["high"])
    prev_low = float(prev_last["low"])
    today_high = float(today_first["high"])
    today_low = float(today_first["low"])

    if today_high < prev_low:
        gap_points = round(prev_low - today_high, 5)
        gap_percent = round((gap_points / prev_low) * 100.0,
                            4) if prev_low != 0 else 0.0
        return {
            "status": "GAP_DOWN",
            "prev_time": prev_time,
            "today_time": today_time,
            "prev_high": round(prev_high, 5),
            "prev_low": round(prev_low, 5),
            "today_high": round(today_high, 5),
            "today_low": round(today_low, 5),
            "gap_points": gap_points,
            "gap_percent": gap_percent,
            "rule": "today_first_high < prev_last_low",
        }

    if today_low > prev_high:
        gap_points = round(today_low - prev_high, 5)
        gap_percent = round((gap_points / prev_high) *
                            100.0, 4) if prev_high != 0 else 0.0
        return {
            "status": "GAP_UP",
            "prev_time": prev_time,
            "today_time": today_time,
            "prev_high": round(prev_high, 5),
            "prev_low": round(prev_low, 5),
            "today_high": round(today_high, 5),
            "today_low": round(today_low, 5),
            "gap_points": gap_points,
            "gap_percent": gap_percent,
            "rule": "today_first_low > prev_last_high",
        }

    overlap_low = max(prev_low, today_low)
    overlap_high = min(prev_high, today_high)
    overlap_exists = overlap_low <= overlap_high

    return {
        "status": "NO_GAP",
        "prev_time": prev_time,
        "today_time": today_time,
        "prev_high": round(prev_high, 5),
        "prev_low": round(prev_low, 5),
        "today_high": round(today_high, 5),
        "today_low": round(today_low, 5),
        "gap_points": 0.0,
        "gap_percent": 0.0,
        "rule": "ranges_touch_or_overlap",
        "overlap_exists": overlap_exists,
    }


def _safe_round(v, digits=5):
    try:
        if v is None:
            return None
        return round(float(v), digits)
    except Exception:
        return v


def _setup_signature(setup):
    if not setup:
        return ("NONE",)

    return (
        str(setup.get("side")),
        str(setup.get("pattern")),
        str(pd.Timestamp(setup.get("picked_candle_time"))) if setup.get(
            "picked_candle_time") is not None else None,
        str(pd.Timestamp(setup.get("trigger_time"))) if setup.get(
            "trigger_time") is not None else None,
        str(pd.Timestamp(setup.get("breakout_candle_time"))) if setup.get(
            "breakout_candle_time") is not None else None,
        str(setup.get("entry_mode")),
        _safe_round(setup.get("entry")),
        _safe_round(setup.get("sl")),
        _safe_round(setup.get("tp")),
    )


def _emit_once(engine, category, message):
    if not hasattr(engine, "_debug_seen_messages"):
        engine._debug_seen_messages = set()

    key = (str(category), str(message))
    if key in engine._debug_seen_messages:
        return

    engine._debug_seen_messages.add(key)
    print(message)


def _emit_on_change(engine, category, signature, message):
    if not hasattr(engine, "_debug_last_signature"):
        engine._debug_last_signature = {}

    prev = engine._debug_last_signature.get(category)
    if prev == signature:
        return

    engine._debug_last_signature[category] = signature
    print(message)


def _fmt_filter_status(setup):
    def yn(v):
        if v is True:
            return "PASS"
        if v is False:
            return "FAIL"
        return "ERROR"

    side = str(setup.get("side", "")).upper().strip()
    gap_status = str(setup.get("gap_status", "NO_GAP")).upper().strip()
    pickup_is_first = bool(setup.get("pickup_is_first_candle", False))
    special_entry_applied = bool(setup.get("special_entry_applied", False))

    last_filter_enabled = bool(setup.get("atr_last_filter_enabled", False))
    last_filter_valid = setup.get("atr_last_filter_valid", None)
    last_filter_value = setup.get("atr_last_filter_value", None)
    last_filter_threshold = setup.get("atr_last_filter_threshold", None)

    if last_filter_enabled:
        atr_last_filter_text = (
            f"{yn(last_filter_valid)} "
            f"(m15x2={last_filter_value}, h1={last_filter_threshold})"
        )
    else:
        atr_last_filter_text = "NA (h1_atr > 100)"

    return {
        "OC_BODY": yn(setup.get("pickup_oc_filter_valid")),
        "ATR_COMPARE": yn(setup.get("atr_compare_valid")),
        "ATR_LAST_ROUNDOFF_X2": atr_last_filter_text,
        "GAP_RULE": "PASS" if gap_status in ("GAP_UP", "GAP_DOWN") else f"NA ({gap_status})",
        "FIRST_PICKUP_RULE": "PASS" if pickup_is_first else "NA (not first candle)",
        "SPECIAL_ENTRY_CROSS": "PASS" if special_entry_applied else "NA (not crossed)",
        "BREAKOUT_OC": "NA (not enabled)",
        "PENDING_INVALIDATION": "NA (before pending phase)" if side in ("BUY", "SELL") else "ERROR",
    }


def _print_filter_summary(setup):
    filters = _fmt_filter_status(setup)
    print(" -> FILTERS SUMMARY")
    for k, v in filters.items():
        print(f"    {k}: {v}")


def _print_total_setups_summary(all_setups):
    if not all_setups:
        print(" -> TOTAL SETUPS SUMMARY")
        print("    TOTAL=0 | BUY=0 | SELL=0")
        return

    total = len(all_setups)
    buy_count = sum(1 for x in all_setups if str(
        x.get("side", "")).upper() == "BUY")
    sell_count = sum(1 for x in all_setups if str(
        x.get("side", "")).upper() == "SELL")

    print(" -> TOTAL SETUPS SUMMARY")
    print(f"    TOTAL={total} | BUY={buy_count} | SELL={sell_count}")

    for i, s in enumerate(all_setups, 1):
        side = str(s.get("side", "UNKNOWN")).upper()
        pickup = s.get("picked_candle_time")
        trigger = s.get("trigger_time")
        status = str(s.get("status", "UNKNOWN")).upper()
        reason = str(s.get("reason", ""))

        print(
            f"    {i}. side={side} | picked={pickup} | trigger={trigger} | "
            f"status={status} | reason={reason}"
        )


def prepare_backtest_data(engine, specs):
    data_by_pair = {}
    all_dates = set()

    for spec in specs:
        pair = spec["pair"]
        csv_path = spec["csv"]

        df = pd.read_csv(csv_path)
        df["time"] = pd.to_datetime(df["datetime"])
        df = df.sort_values("time").reset_index(drop=True)

        if "atr" not in df.columns:
            df = df.copy()

        full_df = df.copy()

        filtered_df = df.copy()
        if engine.start_date is not None:
            filtered_df = filtered_df[filtered_df["time"].dt.date >=
                                      engine.start_date]
        if engine.end_date is not None:
            filtered_df = filtered_df[filtered_df["time"].dt.date <=
                                      engine.end_date]

        if filtered_df.empty:
            continue

        data_by_pair[pair] = full_df
        all_dates.update(filtered_df["time"].dt.date.unique())

    return data_by_pair, sorted(all_dates)


def get_weekly_risk_percent(engine, day):
    if engine.start_date is not None:
        week_num = ((day - engine.start_date).days // 7) + 1
    else:
        week_num = 1

    raw_risk_percent = engine.base_risk_percent + (week_num - 1) * 0.5
    risk_percent = min(raw_risk_percent, 5.0)
    return week_num, risk_percent


def get_day_df(engine, df, day):
    day_df = df[df["time"].dt.date == day].copy()
    if day_df.empty:
        return day_df

    if "atr" not in day_df.columns:
        raise RuntimeError(
            f"M15 ATR column missing in dataframe for {engine.pair} on {day}. "
            f"Strategy currently requires candidate 15M ATR."
        )

    day_df["atr"] = pd.to_numeric(day_df["atr"], errors="coerce").ffill()
    return day_df


def build_day_setups(engine, day_df, fund, risk_percent):
    result = build_setup_for_day(
        engine=engine,
        day_df=day_df,
        fund=fund,
        risk_percent=risk_percent,
        verbose=False,
        hh_debug=False,
    )

    if result is None:
        return [], []

    if isinstance(result, dict):
        candidates = result.get("chosen_setups", []) or []
        all_setups = result.get("all_setups", []) or []
        return candidates, all_setups

    return ([result] if result else []), ([result] if result else [])


def find_entry_after_trigger(engine, day_df, setup):
    side = str(setup["side"]).upper().strip()
    trigger_time = setup["trigger_time"]
    entry_level = float(setup["entry"])

    search_df = day_df[day_df["time"] >= trigger_time].copy()
    if search_df.empty:
        _emit_once(engine, "no_candles_after_trigger",
                   f" -> {side} no candles after trigger_time, skip")
        return None

    for idx, row in search_df.iterrows():
        h = float(row["high"])
        l = float(row["low"])

        if side == "BUY" and h >= entry_level:
            return idx

        if side == "SELL" and l <= entry_level:
            return idx

    msg = f" -> {side} pending not filled for the day"
    sig = (
        side,
        str(pd.Timestamp(trigger_time)),
        _safe_round(entry_level),
    )
    _emit_on_change(engine, "pending_not_filled", sig, msg)
    return None


def process_setup_candidate(engine, df, day_df, setup, last_exit_time=None):
    side = str(setup["side"]).upper().strip()
    trigger_time = pd.to_datetime(setup["trigger_time"])

    if last_exit_time is not None and trigger_time <= last_exit_time:
        _emit_once(
            engine,
            "hold_setup",
            f" -> HOLD same-side setup side={side} trigger={trigger_time} because trigger <= last {side} exit {last_exit_time}",
        )
        return None

    if engine._is_setup_in_hhll_disable_window(setup):
        _emit_once(
            engine,
            "disable_window",
            f" -> {engine.pair} {side} setup detected in HH/LL disable window "
            f"(half-process mode in live, skip in backtest)",
        )
        return None

    entry_txt = f"{float(setup['entry']):.5f}" if setup.get(
        "entry") is not None else "None"
    sl_txt = f"{float(setup['sl']):.5f}" if setup.get(
        "sl") is not None else "None"
    tp_txt = f"{float(setup['tp']):.5f}" if setup.get(
        "tp") is not None else "None"

    setup_sig = _setup_signature(setup)
    setup_msg = (
        f" -> Chosen setup: side={side}, "
        f"picked_candle_time={setup.get('picked_candle_time')}, "
        f"trigger_time={trigger_time}, "
        f"breakout_time={setup.get('breakout_candle_time')}, "
        f"breakout_close={setup.get('breakout_close')}, "
        f"entry_mode={setup.get('entry_mode')}, "
        f"entry={entry_txt}, SL={sl_txt}, TP={tp_txt}, "
        f"pickup_atr={setup.get('pickup_atr')}, "
        f"pickup_buffer_atr5={setup.get('pickup_buffer_atr5')}, "
        f"pickup_breakout_level={setup.get('pickup_breakout_level')}, "
        f"target_result_price={setup.get('target_result_price')}, "
        f"tp_mode={setup.get('tp_mode')}, "
        f"m15_atr={setup.get('candidate_breakout_atr')}, "
        f"m15_cmp={setup.get('atr_compare_m15_candidate_cmp')}, "
        f"h1_raw={setup.get('atr_compare_h1_raw')}, "
        f"h1_cmp={setup.get('atr_compare_h1_round')}, "
        f"h1_result_cmp={setup.get('atr_compare_h1_result')}, "
        f"atr_valid={setup.get('atr_compare_valid')}, "
        f"atr_last_filter_enabled={setup.get('atr_last_filter_enabled')}, "
        f"atr_last_filter_valid={setup.get('atr_last_filter_valid')}, "
        f"atr_last_filter_value={setup.get('atr_last_filter_value')}, "
        f"atr_last_filter_threshold={setup.get('atr_last_filter_threshold')}, "
        f"sl_source={setup.get('sl_source')}, "
        f"tp_source={setup.get('tp_source')}, "
        f"special_entry_atr_multiplier={setup.get('special_entry_atr_multiplier')}, "
        f"special_entry_h1_below_50_active={setup.get('special_entry_h1_below_50_active')}, "
        f"gann_cmp={setup.get('gann_cmp')}"
    )
    _emit_on_change(engine, "chosen_setup", setup_sig, setup_msg)

    _print_filter_summary(setup)

    if setup.get("entry") is None or setup.get("sl") is None or setup.get("tp") is None:
        incomplete_sig = (
            setup_sig,
            "incomplete",
            _safe_round(setup.get("entry")),
            _safe_round(setup.get("sl")),
            _safe_round(setup.get("tp")),
        )
        incomplete_msg = (
            f" -> DEBUG ONLY: incomplete setup, skip simulation "
            f"entry={setup.get('entry')}, sl={setup.get('sl')}, tp={setup.get('tp')}"
        )
        _emit_on_change(engine, "incomplete_setup",
                        incomplete_sig, incomplete_msg)
        return None

    session_atr = setup.get("pickup_atr")
    try:
        session_atr = float(session_atr) if session_atr is not None else None
    except Exception:
        session_atr = None

    # FORCE pending window till 23:50 server time
    window_end_server = pd.Timestamp.combine(
        pd.Timestamp(trigger_time).date(),
        pd.Timestamp("23:50:00").time(),
    )

    print(
        f"  -> DEBUG: window_end_server from process_setup_candidate = {window_end_server}"
    )
    print(
        f"  -> DEBUG: last candle available in day_df = {pd.Timestamp(day_df['time'].max())}"
    )

    entry_result = engine._wait_for_entry_in_window(
        day_df=day_df,
        setup=setup,
        window_start_server=trigger_time,
        window_end_server=window_end_server,
        session_atr=session_atr,
    )
    if entry_result is None:
        return None

    entry_idx = entry_result["entry_idx"]
    entry_level = float(entry_result["actual_entry"])
    sl = float(setup["sl"])
    tp = float(setup["tp"])
    lot = float(setup["lot_size"])

    print(
        f" -> {side} Entry filled at {df.loc[entry_idx, 'time']}, "
        f"price={entry_level:.5f}"
    )

    sim_setup = {
        "side": side,
        "sl": sl,
        "tp": tp,
        "lot_size": lot,
        "entry_mode": setup.get("entry_mode", ""),
        "tp_mode": str(setup.get("tp_mode", "")).strip().upper(),
        "target_result_price": setup.get("target_result_price"),
    }

    trade = engine._simulate_trade(
        df=df,
        setup=sim_setup,
        entry_idx=entry_idx,
        actual_entry=entry_level,
    )

    print(
        f" -> {side} Exit {trade['result']} at {trade['exit_time']}, "
        f"price={trade['exit_price']:.5f}, "
        f"PNL=${trade['pnl_amount']:.2f}, Fund=${trade['fund_after']:.2f}"
    )

    return trade


def process_pair_day(engine, day, pair, df):
    day_df = get_day_df(engine, df, day)
    if day_df.empty:
        return []

    engine.pair = pair
    engine._debug_last_signature = {}
    engine._debug_seen_messages = set()

    print("\n" + "-" * 40)
    print(f"[{pair}] Processing: {day}")
    print(f"Current Fund: ${engine.current_fund:.2f}")

    if not engine._validate_day(day_df):
        print(" -> Day invalid, skipping")
        return []

    week_num, risk_percent = get_weekly_risk_percent(engine, day)
    raw_fund = engine.current_fund

    print(f" -> Week {week_num}: Risk={risk_percent:.1f}%")
    print(f" -> Sizing Fund: ${raw_fund:,.2f}")

    print("\n" + "-" * 30)
    print(" -> New Day High/Low pattern processing")

    candidates, all_setups = build_day_setups(
        engine=engine,
        day_df=day_df,
        fund=raw_fund,
        risk_percent=risk_percent,
    )

    _print_total_setups_summary(all_setups)

    if not candidates:
        print(" -> No valid Day High/Low setup for this day")
        return []

    print(f" -> Candidate setups found: {len(candidates)}")

    trades_for_pair_day = []
    last_exit_time_by_side = {"BUY": None, "SELL": None}

    for setup in candidates:
        if getattr(engine, "stop_requested", False):
            break

        side = str(setup.get("side", "")).upper().strip()

        trade = process_setup_candidate(
            engine=engine,
            df=df,
            day_df=day_df,
            setup=setup,
            last_exit_time=last_exit_time_by_side.get(side),
        )

        if trade is None:
            continue

        trades_for_pair_day.append(trade)
        last_exit_time_by_side[side] = trade["exit_time"]

    return trades_for_pair_day
