from backtest_engine_1h_orb import BacktestEngine1HORB
from live_data_mt5 import fetch_live_1h
from order_mt5 import init_mt5, shutdown_mt5
import os

PAIRS = [
    "AUDCAD.ecn",
    "AUDUSD.ecn",
    "EURAUD.ecn",
    "EURCAD.ecn",
    "EURUSD.ecn",
    "EURGBP.ecn",
    "GBPAUD.ecn",
    "GBPCAD.ecn",
    "GBPUSD.ecn",
    "NZDCAD.ecn",
    "NZDUSD.ecn",
]

INITIAL_FUND = 30.0
INITIAL_RISK = 8.0
DEFAULT_PAIR = PAIRS[0]

SIGNAL_DIR = r"C:\Users\Uzair Khan\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5\Files"


def get_signal_file(pair: str) -> str:
    safe_pair = pair.replace("/", "_")
    return os.path.join(SIGNAL_DIR, f"live_signal_{safe_pair}.txt")


def read_existing_status(signal_file: str):
    if not os.path.exists(signal_file):
        return None, None

    try:
        with open(signal_file, "r", encoding="utf-8") as f:
            line = f.read().strip()

        if not line:
            return None, None

        parts = line.split("|")
        if len(parts) < 15:
            return None, None

        signal_id = parts[0]
        status = parts[14]
        return signal_id, status
    except Exception:
        return None, None


def write_dual_signal_for_ea(signal: dict, pair: str):
    signal_file = get_signal_file(pair)
    signal_id = f"{pair}_{signal['day']}"

    existing_signal_id, existing_status = read_existing_status(signal_file)
    if existing_signal_id == signal_id and existing_status == "NEW":
        print(
            f"  -> Existing NEW signal already present for {pair}, skipping overwrite")
        return

    expiry_server = signal["expiry_server"]

    line = (
        f"{signal_id}|"
        f"{pair}|"
        f"{signal['breakout_side']}|"
        f"{expiry_server}|"
        f"{round(float(signal['buy']['entry']), 5)}|"
        f"{round(float(signal['buy']['sl']), 5)}|"
        f"{round(float(signal['buy']['tp']), 5)}|"
        f"{round(float(signal['buy']['lot']), 2)}|"
        f"{round(float(signal['sell']['entry']), 5)}|"
        f"{round(float(signal['sell']['sl']), 5)}|"
        f"{round(float(signal['sell']['tp']), 5)}|"
        f"{round(float(signal['sell']['lot']), 2)}|"
        f"25|15|NEW"
    )

    os.makedirs(os.path.dirname(signal_file), exist_ok=True)

    with open(signal_file, "w", encoding="utf-8") as f:
        f.write(line)

    print(f"  -> Dual signal written for EA: {signal_file}")
    print(f"  -> FILE CONTENT WRITTEN: {line}")


def main():
    init_mt5()

    engine = BacktestEngine1HORB(
        initial_fund=INITIAL_FUND,
        initial_risk_percent=INITIAL_RISK,
        pair=DEFAULT_PAIR,
    )

    for pair in PAIRS:
        print("\n" + "=" * 40)
        print(f"Checking live dual signal for {pair}")

        try:
            df_1h = fetch_live_1h(pair, lookback_days=5)
        except Exception as e:
            print(f"  -> Failed to fetch 1H data for {pair}: {e}")
            continue

        signal = engine.generate_live_dual_signal_for_latest_day(pair, df_1h)
        if not signal:
            print(f"  -> No dual signal for {pair}")
            continue

        write_dual_signal_for_ea(signal, pair)
        print(f"  -> {pair} dual signal processing done")

    shutdown_mt5()


if __name__ == "__main__":
    main()
