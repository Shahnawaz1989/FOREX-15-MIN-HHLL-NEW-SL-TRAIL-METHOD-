import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta


def _ensure_mt5_connected():
    info = mt5.terminal_info()
    if info is None:
        raise RuntimeError(
            "MT5 terminal not initialized. Call mt5.initialize() before fetch functions."
        )


def fetch_live_1h(symbol: str, lookback_days: int = 90) -> pd.DataFrame:
    """
    Last N days ka 1H OHLC MT5 se lao.
    Columns: datetime, open, high, low, close
    """
    _ensure_mt5_connected()

    tf = mt5.TIMEFRAME_H1

    # Small forward buffer + enough past lookback
    end = datetime.now() + timedelta(minutes=5)
    start = end - timedelta(days=max(lookback_days, 2))

    rates = mt5.copy_rates_range(symbol, tf, start, end)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No 1H data for {symbol}")

    df = pd.DataFrame(rates)
    df["datetime"] = pd.to_datetime(df["time"], unit="s")
    df = df[["datetime", "open", "high", "low", "close"]].copy()
    df = df.dropna(subset=["datetime"]).sort_values(
        "datetime").reset_index(drop=True)

    print(f"[1H DEBUG] {symbol} rows = {len(df)}")
    print(f"[1H DEBUG] {symbol} min datetime = {df['datetime'].min()}")
    print(f"[1H DEBUG] {symbol} max datetime = {df['datetime'].max()}")
    print(f"[1H DEBUG] {symbol} last 10 rows:")
    print(df[["datetime", "open", "high", "low", "close"]].tail(
        10).to_string(index=False))

    # Check whether previous closed H1 around today's first hour is available
    try:
        latest_ts = df["datetime"].max()
        latest_day_start = latest_ts.normalize()
        prev_closed_h1 = latest_day_start - pd.Timedelta(hours=1)

        prev_row = df.loc[df["datetime"] == prev_closed_h1]
        if not prev_row.empty:
            print(
                f"[1H DEBUG] {symbol} previous closed H1 found for live day start: "
                f"{prev_closed_h1}"
            )
            print(prev_row.to_string(index=False))
        else:
            before_rows = df.loc[df["datetime"] < latest_day_start].tail(5)
            print(
                f"[1H DEBUG] {symbol} previous closed H1 MISSING for live day start: "
                f"expected={prev_closed_h1}"
            )
            if not before_rows.empty:
                print(f"[1H DEBUG] {symbol} nearest rows before day start:")
                print(before_rows.to_string(index=False))
            else:
                print(
                    f"[1H DEBUG] {symbol} no rows exist before {latest_day_start}")
    except Exception as e:
        print(f"[1H DEBUG] {symbol} previous-H1 check failed: {e}")

    return df


def fetch_live_1m(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    """
    Given range ka 1M OHLC MT5 se lao.
    Columns: time, open, high, low, close
    """
    _ensure_mt5_connected()

    tf = mt5.TIMEFRAME_M1
    rates = mt5.copy_rates_range(symbol, tf, start, end)

    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No M1 data for {symbol} from {start} to {end}")

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df[["time", "open", "high", "low", "close"]].copy()
    df = df.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)

    return df


def fetch_live_15m(symbol: str, lookback_days: int = 90) -> pd.DataFrame:
    """
    Latest 15M OHLC MT5 se lao using copy_rates_from_pos.
    Columns: datetime, open, high, low, close
    """
    _ensure_mt5_connected()

    tf = mt5.TIMEFRAME_M15
    bars_needed = max(100, int(lookback_days * 24 * 4))

    rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars_needed)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No 15M data for {symbol}")

    df = pd.DataFrame(rates)
    df["datetime"] = pd.to_datetime(df["time"], unit="s")
    df = df[["datetime", "open", "high", "low", "close"]].copy()
    df = df.dropna(subset=["datetime"]).sort_values(
        "datetime").reset_index(drop=True)

    print(f"[15M DEBUG] {symbol} rows = {len(df)}")
    print(f"[15M DEBUG] {symbol} min datetime = {df['datetime'].min()}")
    print(f"[15M DEBUG] {symbol} max datetime = {df['datetime'].max()}")
    print(df[["datetime", "open", "high", "low", "close"]].tail(
        5).to_string(index=False))

    return df
