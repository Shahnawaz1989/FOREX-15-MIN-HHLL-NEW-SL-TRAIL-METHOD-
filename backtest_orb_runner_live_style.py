# backtest_orb_runner_live_style.py
import pandas as pd

from backtest_orb_runner_helpers import (
    get_day_df,
    get_weekly_risk_percent,
    process_setup_candidate,
    detect_day_gap,
    _print_total_setups_summary,
)


def build_intraday_live_style_candidates(engine, day_df, fund, risk_percent, gap_info=None):
    if day_df.empty:
        return [], []

    day_df = day_df.sort_values("time").reset_index(drop=True)

    candidates = []
    all_setups = []
    seen_keys = set()
    all_seen_keys = set()
    active_setups = {"BUY": None, "SELL": None}

    for i in range(1, len(day_df)):
        partial_df = day_df.iloc[: i + 1].copy()

        for side in ("BUY", "SELL"):
            active_setup = active_setups.get(side)
            if active_setup is not None:
                chk = engine._invalidate_pending_setup_on_new_pivot(
                    setup=active_setup,
                    intraday_df=partial_df,
                )
                if chk.get("cancelled"):
                    active_setups[side] = None

        result = engine._build_setup_for_day(
            partial_df,
            fund,
            risk_percent,
            gap_info=gap_info,
        )

        if not result:
            continue

        if isinstance(result, dict):
            partial_candidates = result.get("chosen_setups", []) or []
            partial_all_setups = result.get("all_setups", []) or []
        else:
            partial_candidates = [result] if result else []
            partial_all_setups = [result] if result else []

        for item in partial_all_setups:
            if not isinstance(item, dict):
                continue

            all_key = (
                str(item.get("side", "")).upper().strip(),
                str(item.get("picked_candle_time")),
                str(item.get("trigger_time")),
                str(item.get("status")),
                str(item.get("reason")),
            )
            if all_key not in all_seen_keys:
                all_seen_keys.add(all_key)
                all_setups.append(item)

        for setup in partial_candidates:
            if not isinstance(setup, dict):
                continue
            if setup.get("trigger_time") is None:
                continue

            trigger_time = pd.to_datetime(setup.get("trigger_time"))
            picked_time = pd.to_datetime(setup.get("picked_candle_time"))
            side = str(setup.get("side", "")).upper().strip()
            entry = round(float(setup.get("entry", 0.0)), 5)
            sl = round(float(setup.get("sl", 0.0)), 5) if setup.get(
                "sl") is not None else None
            tp = round(float(setup.get("tp", 0.0)), 5) if setup.get(
                "tp") is not None else None

            key = (side, str(picked_time), str(trigger_time), entry, sl, tp)
            if key in seen_keys:
                active_setups[side] = setup
                continue

            chk_new = engine._invalidate_pending_setup_on_new_pivot(
                setup=setup,
                intraday_df=partial_df,
            )
            if chk_new.get("cancelled"):
                continue

            seen_keys.add(key)
            candidates.append(setup)
            active_setups[side] = setup

    candidates = [
        s for s in candidates
        if isinstance(s, dict) and s.get("trigger_time") is not None
    ]
    candidates.sort(key=lambda s: pd.to_datetime(s.get("trigger_time")))

    all_setups = [s for s in all_setups if isinstance(s, dict)]

    return candidates, all_setups


def process_pair_day_live_style(engine, day, pair, df):
    day_df = get_day_df(engine, df, day)
    if day_df.empty:
        return []

    engine.pair = pair
    engine._debug_last_signature = {}
    engine._debug_seen_messages = set()

    print("\n" + "-" * 40)
    print(f"[{pair}] Processing LIVE-STYLE: {day}")
    print(f"Current Fund: ${engine.current_fund:.2f}")

    gap_info = detect_day_gap(df, day)
    print(f" -> GAP_STATUS={gap_info.get('status')}")

    if gap_info.get("status") in ("GAP_UP", "GAP_DOWN", "NO_GAP"):
        print(
            f" -> {gap_info['status']} | "
            f"prev_last_high={gap_info['prev_high']:.5f}, "
            f"prev_last_low={gap_info['prev_low']:.5f} @ {gap_info['prev_time']} | "
            f"today_first_high={gap_info['today_high']:.5f}, "
            f"today_first_low={gap_info['today_low']:.5f} @ {gap_info['today_time']} | "
            f"gap={gap_info['gap_points']:.5f} ({gap_info['gap_percent']:.4f}%) | "
            f"rule={gap_info.get('rule')}"
        )

    if not engine._validate_day(day_df):
        print(" -> Day invalid, skipping")
        return []

    engine.h1_atr_df = engine.h1_atr_df.copy()
    engine.h1atrdf = engine.h1_atr_df.copy()

    week_num, risk_percent = get_weekly_risk_percent(engine, day)
    raw_fund = engine.current_fund

    print(f" -> Week {week_num}: Risk={risk_percent:.1f}%")
    print(f" -> Sizing Fund: ${raw_fund:,.2f}")

    print("\n" + "-" * 30)
    print(" -> Live-style chronological HH/LL replay")

    candidates, all_setups = build_intraday_live_style_candidates(
        engine=engine,
        day_df=day_df,
        fund=raw_fund,
        risk_percent=risk_percent,
        gap_info=gap_info,
    )

    _print_total_setups_summary(all_setups)

    if not candidates:
        print(" -> No live-style HH/LL candidates for this day")
        return []

    print(f" -> Candidate setups found: {len(candidates)}")

    trades_for_pair_day = []
    last_exit_time_by_side = {"BUY": None, "SELL": None}

    for setup in candidates:
        if getattr(engine, "stop_requested", False):
            break

        side = str(setup.get("side", "")).upper().strip()

        print(
            f" -> Chosen setup: side={setup.get('side')}, "
            f"picked_candle_time={setup.get('picked_candle_time')}, "
            f"trigger_time={setup.get('trigger_time')}, "
            f"breakout_time={setup.get('breakout_candle_time')}, "
            f"entry={setup.get('entry')}, SL={setup.get('sl')}, TP={setup.get('tp')}, "
            f"gap_status={setup.get('gap_status')}, "
            f"pickup_is_first_candle={setup.get('pickup_is_first_candle')}, "
            f"special_gap_pickup_rule={setup.get('special_gap_pickup_rule')}, "
            f"special_entry_cmp_raw={setup.get('special_entry_cmp_raw')}, "
            f"special_entry_cmp={setup.get('special_entry_cmp')}, "
            f"special_entry_applied={setup.get('special_entry_applied')}, "
            f"special_entry_atr_multiplier={setup.get('special_entry_atr_multiplier')}, "
            f"special_entry_h1_below_50_active={setup.get('special_entry_h1_below_50_active')}, "
            f"tp_mode={setup.get('tp_mode')}, "
            f"sl_source={setup.get('sl_source')}, "
            f"tp_source={setup.get('tp_source')}, "
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
            f"gann_lookup_cmp={setup.get('gann_lookup_cmp')}, "
            f"gann_cmp={setup.get('gann_cmp')}"
        )

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
