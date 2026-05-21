import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta


def _ensure_mt5_connected():
    info = mt5.terminal_info()
    if info is None:
        raise RuntimeError(
            "MT5 terminal not initialized. Call init_mt5() before fetch functions."
        )


def fetch_live_1h(symbol: str, lookback_days: int = 90) -> pd.DataFrame:
    """
    Last N days ka 1H OHLC MT5 se lao.
    Columns: datetime, open, high, low, close
    """
    _ensure_mt5_connected()

    tf = mt5.TIMEFRAME_H1
    end = datetime.now()
    start = end - timedelta(days=lookback_days)

    rates = mt5.copy_rates_range(symbol, tf, start, end)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No 1H data for {symbol}")

    df = pd.DataFrame(rates)
    df["datetime"] = pd.to_datetime(df["time"], unit="s")
    df = df[["datetime", "open", "high", "low", "close"]].copy()
    df = df.sort_values("datetime").reset_index(drop=True)

    print(f"[1H DEBUG] {symbol} max datetime = {df['datetime'].max()}")
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
    df = df.sort_values("time").reset_index(drop=True)

    return df


def fetch_live_15m(symbol: str, lookback_days: int = 90) -> pd.DataFrame:
    """
    Latest 15M OHLC MT5 se lao using copy_rates_from_pos.
    Yeh recent bars uthata hai, isliye Monday/history lag case me zyada useful hai.
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
    df = df.sort_values("datetime").reset_index(drop=True)

    print(f"[15M DEBUG] {symbol} max datetime = {df['datetime'].max()}")
    print(df[["datetime", "open", "high", "low", "close"]].tail(5))

    return df
