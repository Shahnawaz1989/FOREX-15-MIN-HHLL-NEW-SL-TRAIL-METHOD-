def get_live_usable_fund(
    currentfund,
    initialfund,
    use_live_equity_sizing=False,
    live_source_fund=None,
    live_strategy_start_fund=None,
):
    """
    Live sizing helper.

    If use_live_equity_sizing == False:
        return currentfund

    Else:
        usable_fund = live_equity - reserved_fund

    reserved_fund logic:
        source_fund = live_source_fund if provided else initialfund
        start_fund = live_strategy_start_fund if provided else initialfund
        reserved_fund = max(0, source_fund - start_fund)
    """
    if not use_live_equity_sizing:
        print(
            f"  -> Live sizing disabled, using currentfund={float(currentfund):.2f}")
        return float(currentfund)

    try:
        import MetaTrader5 as mt5

        info = mt5.account_info()
        if info is None:
            print("  -> live fund manager: account_info() failed, fallback currentfund")
            return float(currentfund)

        live_equity = float(info.equity)

        source_fund = (
            float(live_source_fund)
            if live_source_fund is not None
            else float(initialfund)
        )
        start_fund = (
            float(live_strategy_start_fund)
            if live_strategy_start_fund is not None
            else float(initialfund)
        )

        reserved_fund = max(0.0, source_fund - start_fund)
        usable_fund = max(0.0, live_equity - reserved_fund)

        print(
            f"  -> Live sizing | equity={live_equity:.2f}, "
            f"source={source_fund:.2f}, start={start_fund:.2f}, "
            f"reserved={reserved_fund:.2f}, usable={usable_fund:.2f}"
        )

        return usable_fund

    except Exception as e:
        print(f"  -> live fund manager error, fallback currentfund: {e}")
        return float(currentfund)
