import pandas as pd


from backtest_orb_runner_helpers import (
    get_day_df,
    get_weekly_risk_percent,
    process_setup_candidate,
)


def build_intraday_live_style_candidates(engine, day_df, fund, risk_percent):
    """
    Backtest-only live-style approximation.

    Idea:
    - Day ko progressive snapshots me replay karo.
    - Har nayi candle par available history tak setup rebuild karo.
    - Same exact setup ko duplicate list me dobara add mat karo.
    - Newer HH/LL setup aaye to usko candidate banne do.
    """
    if day_df.empty:
        return []

    day_df = day_df.sort_values("time").reset_index(drop=True)

    candidates = []
    seen_keys = set()

    # Enough candles hone chahiye warna setup builder meaningless hoga
    for i in range(1, len(day_df)):
        partial_df = day_df.iloc[: i + 1].copy()

        high_setup = engine._build_high_setup_for_day(
            partial_df, fund, risk_percent)
        low_setup = engine._build_low_setup_for_day(
            partial_df, fund, risk_percent)

        for setup in [high_setup, low_setup]:
            if not setup:
                continue

            trigger_time = pd.to_datetime(setup.get("trigger_time"))
            picked_time = pd.to_datetime(setup.get("picked_candle_time"))
            side = str(setup.get("side", "")).upper().strip()
            entry = round(float(setup.get("entry", 0.0)), 5)
            sl = round(float(setup.get("sl", 0.0)), 5)
            tp = round(float(setup.get("tp", 0.0)), 5)

            key = (side, str(picked_time), str(trigger_time), entry, sl, tp)
            if key in seen_keys:
                continue

            seen_keys.add(key)
            candidates.append(setup)

    candidates.sort(key=lambda s: pd.to_datetime(s["trigger_time"]))
    return candidates


def process_pair_day_live_style(engine, day, pair, df):
    day_df = get_day_df(engine, df, day)
    if day_df.empty:
        return []

    engine.pair = pair

    print("\n" + "-" * 40)
    print(f"[{pair}] Processing LIVE-STYLE: {day}")
    print(f"Current Fund: ${engine.current_fund:.2f}")

    if not engine._validate_day(day_df):
        print(" -> Day invalid, skipping")
        return []

    week_num, risk_percent = get_weekly_risk_percent(engine, day)
    raw_fund = engine.current_fund

    print(f" -> Week {week_num}: Risk={risk_percent:.1f}%")
    print(f" -> Sizing Fund: ${raw_fund:,.2f}")

    print("\n" + "-" * 30)
    print(" -> Live-style chronological HH/LL replay")

    candidates = build_intraday_live_style_candidates(
        engine=engine,
        day_df=day_df,
        fund=raw_fund,
        risk_percent=risk_percent,
    )

    if not candidates:
        print(" -> No live-style HH/LL candidates for this day")
        return []

    print(f" -> Candidate setups found: {len(candidates)}")

    trades_for_pair_day = []
    last_exit_time = None

    for setup in candidates:
        if getattr(engine, "stop_requested", False):
            break

        trade = process_setup_candidate(
            engine=engine,
            df=df,
            day_df=day_df,
            setup=setup,
            last_exit_time=last_exit_time,
        )

        if trade is None:
            continue

        trades_for_pair_day.append(trade)
        last_exit_time = trade["exit_time"]

    return trades_for_pair_day
