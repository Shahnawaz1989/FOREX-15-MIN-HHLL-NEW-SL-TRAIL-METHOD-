from backtest_engine_1h_orb import BacktestEngine1HORB
from live_data_mt5 import fetch_live_1h
from order_mt5 import init_mt5, shutdown_mt5, place_market_order

PAIRS = [
    "AUDCAD.raw",
    "AUDUSD.raw",
    "EURAUD.raw",
    "EURCAD.raw",
    "EURUSD.raw",
    "EURGBP.raw",
    "GBPAUD.raw",
    "GBPCAD.raw",
    "GBPUSD.raw",
    "NZDCAD.raw",
    "NZDUSD.raw",
]

INITIAL_FUND = 100.0
INITIAL_RISK = 8.0          # ya jo tu backtest me use kar raha hai
DEFAULT_PAIR = PAIRS[0]     # sirf init ke time, baad me override ho jayega


def main():
    init_mt5()

    # yahan 3 arguments de:
    engine = BacktestEngine1HORB(
        initial_fund=INITIAL_FUND,
        initial_risk_percent=INITIAL_RISK,
        pair=DEFAULT_PAIR,
    )

    for pair in PAIRS:
        print("\n" + "=" * 40)
        print(f"Checking live signal for {pair}")

        try:
            df_1h = fetch_live_1h(pair, lookback_days=5)
        except Exception as e:
            print(f"  -> Failed to fetch 1H data for {pair}: {e}")
            continue

        signal = engine.generate_signal_for_latest_day(pair, df_1h)
        if not signal:
            print(f"  -> No signal for {pair}")
            continue

        ticket = place_market_order(
            pair,
            signal["side"],
            signal["lot"],
            signal["sl"],
            signal["tp"],
        )
        print(f"  -> {pair} live ticket:", ticket)

    shutdown_mt5()


if __name__ == "__main__":
    main()
