import pandas as pd
from backtest_orb_setup_builder import (
    build_high_setup_for_day,
    build_low_setup_for_day,
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
            df = engine._add_atr_column(df)

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
        if "atr" not in df.columns:
            df = engine._add_atr_column(df)
        day_df = df[df["time"].dt.date == day].copy()

    day_df["atr"] = day_df["atr"].ffill()
    return day_df


def build_day_setups(engine, day_df, fund, risk_percent):
    high_setup = build_high_setup_for_day(
        engine=engine,
        day_df=day_df,
        fund=fund,
        risk_percent=risk_percent,
    )
    low_setup = build_low_setup_for_day(
        engine=engine,
        day_df=day_df,
        fund=fund,
        risk_percent=risk_percent,
    )

    candidates = []
    if high_setup:
        candidates.append(high_setup)
    if low_setup:
        candidates.append(low_setup)

    candidates.sort(key=lambda s: s["trigger_time"])
    return candidates


def find_entry_after_trigger(engine, day_df, setup):
    side = setup["side"]
    trigger_time = setup["trigger_time"]
    entry_level = float(setup["entry"])

    search_df = day_df[day_df["time"] >= trigger_time].copy()
    if search_df.empty:
        print(f" -> {side} no candles after trigger_time, skip")
        return None

    for idx, row in search_df.iterrows():
        h = float(row["high"])
        l = float(row["low"])

        if side == "B" and h >= entry_level:
            return idx

        if side == "S" and l <= entry_level:
            return idx

    print(f" -> {side} pending not filled for the day")
    return None


def process_setup_candidate(engine, df, day_df, setup, last_exit_time=None):
    side = setup["side"]
    trigger_time = setup["trigger_time"]

    if last_exit_time is not None and trigger_time <= last_exit_time:
        print(
            f" -> HOLD setup side={side} trigger={trigger_time} "
            f"because trigger <= last exit {last_exit_time}"
        )
        return None

    if engine._is_setup_in_hhll_disable_window(setup):
        print(
            f" -> {engine.pair} {side} setup detected in HH/LL disable window "
            f"(half-process mode in live, skip in backtest)"
        )
        return None

    print(
        f" -> Chosen setup: side={side}, "
        f"picked_candle_time={setup.get('picked_candle_time')}, "
        f"trigger_time={trigger_time}, "
        f"breakout_time={setup.get('breakout_candle_time')}, "
        f"breakout_close={setup.get('breakout_close')}, "
        f"compare_level={setup.get('compare_level')}, "
        f"entry_mode={setup.get('entry_mode')}, "
        f"entry={setup['entry']:.5f}, SL={setup['sl']:.5f}, TP={setup['tp']:.5f}"
    )

    entry_idx = find_entry_after_trigger(engine, day_df, setup)
    if entry_idx is None:
        return None

    entry_level = float(setup["entry"])
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

    candidates = build_day_setups(
        engine=engine,
        day_df=day_df,
        fund=raw_fund,
        risk_percent=risk_percent,
    )

    if not candidates:
        print(" -> No valid Day High/Low setup for this day")
        return []

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
