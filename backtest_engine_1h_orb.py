from strategy_calculator import StrategyCalculator
from gann_fetcher import GannFetcher
import os
import pandas as pd
import numpy as np
from datetime import datetime, time, timedelta
from typing import List, Dict, Optional
import pytz
import json
import bisect
# Simple ANSI colors for terminal
RESET = "\033[0m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"


class DSTHelper:
    """
    MT5 SERVER (Athens time, Europe/Athens) ↔ IST conversion with real DST.
    CSV: 'datetime' already server time (Athens).
    """

    @staticmethod
    def ist_to_server(ist_dt: datetime) -> datetime:
        ist = pytz.timezone("Asia/Kolkata")
        athens = pytz.timezone("Europe/Athens")

        ist_loc = ist.localize(ist_dt)
        # IST -> UTC -> Athens (handles DST automatically)
        utc = ist_loc.astimezone(pytz.utc)
        server = utc.astimezone(athens)
        return server.replace(tzinfo=None)  # naive datetime

    @staticmethod
    def server_to_ist(server_dt: datetime) -> datetime:
        """
        Opposite direction: server (Athens) -> IST.
        server_dt is naive datetime from CSV in Athens local time.
        """
        athens = pytz.timezone("Europe/Athens")
        ist = pytz.timezone("Asia/Kolkata")

        server_loc = athens.localize(server_dt)
        utc = server_loc.astimezone(pytz.utc)
        ist_dt = utc.astimezone(ist)
        return ist_dt


class BacktestEngine1HORB:
    """
    Single-session ORB on 1H candles:
      - ORB = 00:00 1H candle high/low (server)
      - Day VALID if (H-L of 00:00 candle / ATR14_RMA at that candle) < 1.2
      - Close breakout after ORB
      - Gann dual-side (same StrategyCalculator)
      - Entry window: 7:31–19:30 IST (pending orders only)
      - If pending not filled by 19:30 IST → order_expired_1930 (no trade)
      - If trade filled, TP/SL normal (no 19:30 force exit)
    """

    def __init__(self, initial_fund: float, initial_risk_percent: float, pair: str):
        self.initial_fund = initial_fund
        self.current_fund = initial_fund
        self.initial_risk_percent = initial_risk_percent
        self.base_risk_percent = initial_risk_percent  # weekly ramp ka base
        self.pair = pair

        # Date filter / weekly risk reference
        self.start_date = None
        self.end_date = None

        self.trades: List[Dict] = []
        self.max_drawdown = 0.0
        self.equity_high = initial_fund
        self.total_trades = 0
        self.win_rate = 0.0
        self.stop_requested = False

        # Volatility filter
        self.atr_period = 14
        self.vol_ratio_threshold = 1.20  # < 1.20 = VALID

        # IST-based entry window
        self.entry_start_ist = time(7, 31)
        self.expire_ist = time(19, 30)

        # 🔹 Local Gann lookup load (JSON)
        self.gann_lookup = self._load_gann_lookup("forex_gann_lookup_1_3.json")

    # ------------ EXTRA HELPERS FOR ORB SHIFT LOGIC ------------

    def _compute_bo_ratio(
        self, first_candle: pd.Series, bo_candle: pd.Series
    ) -> float:
        first_hl = first_candle["high"] - first_candle["low"]
        if first_hl <= 0:
            return 0.0

        bo_hl = bo_candle["high"] - bo_candle["low"]
        if bo_hl <= 0:
            return 0.0

        ratio = bo_hl / first_hl
        return ratio

    def _should_shift_orb_to_930(
        self, first_candle: pd.Series, bo_candle: pd.Series
    ) -> bool:
        ratio = self._compute_bo_ratio(first_candle, bo_candle)
        return ratio >= 2.0

    # ----------------------------------------------------------------

    def _load_gann_lookup(self, path: str) -> Dict:
        """
        JSON: { "1.23456": { "buy_at": ..., "buy_t1": ..., "buy_t2": ..., ..., "sell_at": ..., "sell_t1": ... } }
        """
        try:
            with open(path, "r") as f:
                data = json.load(f)

            items = sorted(
                [(float(k), v) for k, v in data.items()],
                key=lambda x: x[0]
            )
            prices = [p for p, _ in items]
            levels = [lv for _, lv in items]
            print(f"  -> Loaded {len(prices)} Gann lookup keys from {path}")
            return {"prices": prices, "levels": levels}
        except Exception as e:
            print(f"  -> Gann lookup load failed: {e}")
            return {"prices": [], "levels": []}

    def _get_gann_from_lookup(self, price: float) -> Optional[Dict]:
        """
        Nearest price lookup in forex_gann_lookup_1_3.json
        Expect JSON structure per price key:
        {
          "buy_at": 1.2345,
          "buy_t1": 1.2350,
          "buy_t2": 1.2360,
          "sell_at": 1.2335,
          "sell_t1": 1.2330,
          "sell_t2": 1.2320
        }
        """
        prices = self.gann_lookup["prices"]
        levels = self.gann_lookup["levels"]
        if not prices:
            return None

        pos = bisect.bisect_left(prices, price)
        if pos == 0:
            idx = 0
        elif pos == len(prices):
            idx = len(prices) - 1
        else:
            before = prices[pos - 1]
            after = prices[pos]
            idx = pos - 1 if abs(price - before) <= abs(price - after) else pos

        lv = levels[idx]
        nearest_price = prices[idx]

        # Safe extraction with fallbacks
        buy_t1 = lv.get("buy_t1") or lv.get("buyT1")
        buy_t2 = lv.get("buy_t2") or lv.get("buyT2")
        sell_t1 = lv.get("sell_t1") or lv.get("sellT1")
        sell_t2 = lv.get("sell_t2") or lv.get("sellT2")

        if buy_t1 is None or buy_t2 is None or sell_t1 is None or sell_t2 is None:
            print(f"  -> Missing T1/T2 keys in JSON for price {nearest_price}")
            return None

        return {
            "input_price": nearest_price,
            "buy_at": lv["buy_at"],
            "buy_targets": [buy_t1, buy_t2],   # sirf T1, T2
            "sell_at": lv["sell_at"],
            "sell_targets": [sell_t1, sell_t2],  # sirf T1, T2
        }

    # ------------------ ATR(14) RMA on 1H ------------------

    def _add_atr_column(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Add ATR(14) RMA/Wilder on 1H candles.
        df: sorted by time (server)
        """
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values

        tr = np.zeros(len(df))
        tr[0] = high[0] - low[0]

        for i in range(1, len(df)):
            hl = high[i] - low[i]
            hc = abs(high[i] - close[i - 1])
            lc = abs(low[i] - close[i - 1])
            tr[i] = max(hl, hc, lc)

        atr = np.zeros(len(df))
        p = self.atr_period

        if len(df) < p:
            df["atr"] = np.nan
            return df

        # First ATR = SMA of first p TRs
        first_atr = np.mean(tr[:p])
        atr[p - 1] = first_atr

        # Wilder RMA
        for i in range(p, len(df)):
            atr[i] = atr[i - 1] + (tr[i] - atr[i - 1]) / p

        # first (p-1) candles ATR undefined
        atr[:p - 1] = np.nan

        df["atr"] = atr
        return df

    # ------------------ DAY VALIDATION (basic) ------------------

    def _validate_day(self, day_df: pd.DataFrame) -> bool:
        """
        Check: 00:00 candle exists and ATR available.
        Naye ORB rules alag se chalenge.
        """
        if day_df.empty:
            return False

        day_date = day_df["time"].dt.date.iloc[0]
        candle_time = datetime.combine(day_date, time(0, 0))

        row = day_df[day_df["time"] == candle_time]
        if row.empty:
            print("  -> No 00:00 candle for this day (skipping)")
            return False

        row = row.iloc[0]
        first_atr = row.get("atr", np.nan)

        if pd.isna(first_atr) or first_atr <= 0:
            print("  -> ATR not available/zero at 00:00 candle (skipping)")
            return False

        return True

    def _decide_orb(self, day_df: pd.DataFrame) -> Dict:
        """
        3 rules sequence:
        1) (00:00 H-L)/ATR > 1.00 -> shift 9:30
        2) |00:00 O-C|/ATR > 1.00 -> shift 9:30
        3) (BO_HL / first_HL) >= 2.00 -> shift 9:30
        Agar tino fail -> ORB 00:00 hi.
        Special: oc<0.30 + Rule3 fail -> first breakout candle as NEW ORB.
        Returns: {"orb": {...}, "rule": 0/1/2/3/4/5, "breakout": breakout,
                  "is_new_orb_shifted": bool}
        """
        day_date = day_df["time"].dt.date.iloc[0]
        is_new_orb_shifted = False

        # 00:00 ORB candle
        orb_0000 = self._get_orb_marking(day_df)
        if not orb_0000:
            return {"orb": None, "rule": 0, "breakout": None, "is_new_orb_shifted": False}

        # 00:00 row
        first_pos = orb_0000["mark_idx"]
        first_row = day_df.iloc[first_pos]
        first_h = first_row["high"]
        first_l = first_row["low"]
        first_o = first_row["open"]
        first_c = first_row["close"]
        first_atr = first_row.get("atr", np.nan)

        if pd.isna(first_atr) or first_atr <= 0:
            print("  -> ATR not available/zero at 00:00, using 00:00 ORB only")
            return {
                "orb": orb_0000,
                "rule": 0,
                "breakout": None,
                "is_new_orb_shifted": False,
            }

        print(f"  ==> DAY ATR14 (00:00) = {first_atr:.5f}")

        # ---------- Rule 1: (H-L)/ATR > 1.00 ----------
        hl_ratio = (first_h - first_l) / first_atr
        print(f"  -> Rule1 (H-L/ATR) = {hl_ratio:.2f}")
        if hl_ratio > 1.0:
            rule_used = 1

        else:
            # ---------- Rule 2: |O-C|/ATR ----------
            oc_ratio = abs(first_o - first_c) / first_atr
            print(f"  -> Rule2 (|O-C|/ATR) = {oc_ratio:.2f}")

            if oc_ratio > 1.0:
                # Direct shift to 9:30
                rule_used = 2

            elif oc_ratio >= 0.30:
                # 0.30–1.0 : normal Rule3 flow on 00:00 ORB
                breakout_0000 = self._detect_breakout(day_df, orb_0000)
                if not breakout_0000:
                    print("  -> No breakout from 00:00 ORB for Rule3 check")
                    return {
                        "orb": orb_0000,
                        "rule": 0,
                        "breakout": None,
                        "is_new_orb_shifted": False,
                    }

                bo_pos = breakout_0000["index"]
                bo_row = day_df.iloc[bo_pos]

                first_hl = first_h - first_l
                bo_hl = bo_row["high"] - bo_row["low"]
                if first_hl <= 0 or bo_hl <= 0:
                    print("  -> Invalid HL values for Rule3")
                    return {
                        "orb": orb_0000,
                        "rule": 0,
                        "breakout": breakout_0000,
                        "is_new_orb_shifted": False,
                    }

                bo_ratio = bo_hl / first_hl
                print(f"  -> Rule3 (BO_HL/First_HL) = {bo_ratio:.2f}")
                if bo_ratio >= 2.0:
                    rule_used = 3
                else:
                    # tino fail -> 00:00 ORB hi
                    return {
                        "orb": orb_0000,
                        "rule": 0,
                        "breakout": breakout_0000,
                        "is_new_orb_shifted": False,
                    }

            else:
                # oc_ratio < 0.30  -> SPECIAL FLOW
                # Pehle 00:00 ORB par Rule3 check karo
                breakout_0000 = self._detect_breakout(day_df, orb_0000)
                if not breakout_0000:
                    print("  -> No breakout from 00:00 ORB for Rule3 check (oc<0.30)")
                    return {
                        "orb": orb_0000,
                        "rule": 0,
                        "breakout": None,
                        "is_new_orb_shifted": False,
                    }

                bo_pos = breakout_0000["index"]
                bo_row = day_df.iloc[bo_pos]

                first_hl = first_h - first_l
                bo_hl = bo_row["high"] - bo_row["low"]
                if first_hl <= 0 or bo_hl <= 0:
                    print("  -> Invalid HL values for Rule3 (oc<0.30)")
                    return {
                        "orb": orb_0000,
                        "rule": 0,
                        "breakout": breakout_0000,
                        "is_new_orb_shifted": False,
                    }

                bo_ratio = bo_hl / first_hl
                print(f"  -> Rule3 (BO_HL/First_HL) = {bo_ratio:.2f}")

                if bo_ratio >= 2.0:
                    # Rule3 positive -> normal 9:30 shift
                    rule_used = 3
                else:
                    # oc<0.30 AND 00:00 Rule3 fail -> first breakout candle as NEW ORB
                    print(
                        CYAN
                        + "  -> Rule2<0.30 and Rule3 fail: using first breakout candle as NEW ORB"
                        + RESET
                    )

                    new_orb_idx = bo_pos
                    new_orb_row = bo_row

                    orb_new = {
                        "high": new_orb_row["high"],
                        "low": new_orb_row["low"],
                        "mark_idx": int(new_orb_idx),
                    }

                    print(
                        CYAN
                        + f"  -> NEW ORB at {new_orb_row['time']} "
                        f"H={new_orb_row['high']:.5f}, L={new_orb_row['low']:.5f}"
                        + RESET
                    )

                    # NEW ORB ka breakout
                    breakout_new = self._detect_breakout(day_df, orb_new)
                    if not breakout_new:
                        print("  -> No breakout from NEW ORB, skipping day")
                        return {
                            "orb": None,
                            "rule": 0,
                            "breakout": None,
                            "is_new_orb_shifted": False,
                        }

                    bo_new_pos = breakout_new["index"]
                    bo_new_row = day_df.iloc[bo_new_pos]

                    # NEW ORB breakout time IST me
                    bo_new_time_server = bo_new_row["time"]
                    bo_new_time_ist = DSTHelper.server_to_ist(
                        bo_new_time_server
                    ).time()
                    if bo_new_time_ist > time(9, 30):
                        print(
                            YELLOW
                            + f"  -> NEW ORB breakout at {bo_new_time_ist} IST (>09:30), skipping this day"
                            + RESET
                        )
                        # Day completely skip
                        return {
                            "orb": None,
                            "rule": 0,
                            "breakout": None,
                            "is_new_orb_shifted": False,
                        }

                    # Ratio NEW ORB par
                    new_orb_hl = orb_new["high"] - orb_new["low"]
                    bo_new_hl = bo_new_row["high"] - bo_new_row["low"]

                    if new_orb_hl <= 0 or bo_new_hl <= 0:
                        return {
                            "orb": orb_new,
                            "rule": 5,
                            "breakout": breakout_new,
                            "is_new_orb_shifted": False,
                        }

                    new_bo_ratio = bo_new_hl / new_orb_hl
                    print(
                        f"  -> Rule3(NEW_ORB) (BO_HL/ORB_HL) = {new_bo_ratio:.2f}"
                    )

                    if new_bo_ratio >= 2.0:
                        # NEW ORB strong -> 9:30 shift flow use karega (rule_used = 5)
                        rule_used = 5
                        is_new_orb_shifted = True
                        # aur aage 9:30 shift block chalega
                        orb_0000 = orb_new  # 9:30 shift ke base ke liye yahi ORB treat kar
                    else:
                        # NEW ORB normal -> direct use, 9:30 shift nahi
                        return {
                            "orb": orb_new,
                            "rule": 5,
                            "breakout": breakout_new,
                            "is_new_orb_shifted": False,
                        }

        # Yahan aate hi rule_used = 1/2/3/5 -> shift to 9:30 IST
        target_930_ist = time(9, 30)
        target_930_server = DSTHelper.ist_to_server(
            datetime.combine(day_date, target_930_ist)
        )
        row_930 = day_df[day_df["time"] == target_930_server]
        if row_930.empty:
            print(YELLOW + "  -> 9:30 IST candle missing, cannot shift ORB" + RESET)
            # fallback 00:00 ORB hi
            return {
                "orb": orb_0000,
                "rule": 0,
                "breakout": None,
                "is_new_orb_shifted": False,
            }

        r = row_930.iloc[0]
        orb_930 = {
            "high": r["high"],
            "low": r["low"],
            "mark_idx": int(day_df.index.get_loc(row_930.index[0])),
        }
        print(
            GREEN
            + f"  -> ORB SHIFTED to 9:30 IST by Rule-{rule_used} "
            f"H={r['high']:.5f}, L={r['low']:.5f}, ATR={r['atr']:.5f}"
            + RESET
        )

        # 9:30 ORB se breakout nikaalte hain
        breakout_930 = self._detect_breakout(day_df, orb_930)

        # Rule-3 style check on 9:30 ORB → possible shift to 15:30 IST
        if breakout_930 is not None:
            bo_pos_930 = breakout_930["index"]
            bo_row_930 = day_df.iloc[bo_pos_930]

            orb930_hl = orb_930["high"] - orb_930["low"]
            bo930_hl = bo_row_930["high"] - bo_row_930["low"]

            if orb930_hl > 0 and bo930_hl > 0:
                bo_ratio_930 = bo930_hl / orb930_hl
                print(f"  -> Rule3(9:30) (BO_HL/ORB_HL) = {bo_ratio_930:.2f}")

                if bo_ratio_930 >= 1.50:
                    # shift ORB to 15:30 IST
                    target_1530_ist = time(15, 30)
                    target_1530_server = DSTHelper.ist_to_server(
                        datetime.combine(day_date, target_1530_ist)
                    )
                    row_1530 = day_df[day_df["time"] == target_1530_server]
                    if row_1530.empty:
                        print(
                            YELLOW
                            + "  -> 15:30 IST candle missing, keeping 9:30 ORB"
                            + RESET
                        )
                    else:
                        r15 = row_1530.iloc[0]
                        orb_1530 = {
                            "high": r15["high"],
                            "low": r15["low"],
                            "mark_idx": int(day_df.index.get_loc(row_1530.index[0])),
                        }
                        print(
                            GREEN
                            + f"  -> ORB SHIFTED to 15:30 IST from 9:30 "
                            f"H={r15['high']:.5f}, L={r15['low']:.5f}, ATR={r15['atr']:.5f}"
                            + RESET
                        )
                        # final ORB & breakout re‑compute from 15:30
                        orb_final = orb_1530
                        breakout_final = self._detect_breakout(
                            day_df, orb_final)
                        return {
                            "orb": orb_final,
                            "rule": 4,
                            "breakout": breakout_final,
                            "is_new_orb_shifted": is_new_orb_shifted,
                        }

        # default: 9:30 ORB hi final
        return {
            "orb": orb_930,
            "rule": rule_used,
            "breakout": breakout_930,
            "is_new_orb_shifted": is_new_orb_shifted,
        }

    # ------------------ MARKET TYPE INFERENCE (3:30 vs 2:30 window) ------------------

    def _infer_market_type(self, day_df: pd.DataFrame) -> str:
        """
        Broker-specific session mapping (IST open 3:30 vs 2:30),
        using hardcoded switch dates:

        11 Mar 2024  -> 2:30
        04 Nov 2024  -> 3:30
        10 Mar 2025  -> 2:30
        03 Nov 2025  -> 3:30
        09 Mar 2026  -> 2:30
        02 Nov 2026  -> 3:30
        """

        if day_df.empty:
            return "3:30_window"

        day_date = day_df["time"].dt.date.iloc[0]

        # Hardcoded broker schedule (inclusive start dates)
        # 2024
        if datetime(2024, 3, 11).date() <= day_date < datetime(2024, 11, 4).date():
            return "2:30_window"
        if datetime(2024, 11, 4).date() <= day_date < datetime(2025, 3, 10).date():
            return "3:30_window"

        # 2025
        if datetime(2025, 3, 10).date() <= day_date < datetime(2025, 11, 3).date():
            return "2:30_window"
        if datetime(2025, 11, 3).date() <= day_date < datetime(2026, 3, 9).date():
            return "3:30_window"

        # 2026
        if datetime(2026, 3, 9).date() <= day_date < datetime(2026, 11, 2).date():
            return "2:30_window"
        if day_date >= datetime(2026, 11, 2).date():
            return "3:30_window"

        # For dates before Mar-2024 fall back to 3:30 (old regime)
        return "3:30_window"

    def _get_entry_expire_time(self, day: datetime.date, market_type: str) -> time:
        """
        Returns expiry time in IST: time(19, 30) or time(20, 30)
        """
        if market_type == "2:30_window":
            return time(20, 30)
        else:  # 3:30_window
            return time(19, 30)

    # ------------------ ORB MARKING (00:00) ------------------

    def _get_orb_marking(self, day_df: pd.DataFrame) -> Optional[Dict]:
        """
        ORB = exact 00:00 1H candle high/low on server time.
        """
        day_date = day_df["time"].dt.date.iloc[0]
        candle_time = datetime.combine(day_date, time(0, 0))

        # Exact match
        row_idx = day_df.index[day_df["time"] == candle_time]
        if row_idx.empty:
            print("  -> ORB 00:00 candle missing")
            return None

        idx = row_idx[0]
        row = day_df.loc[idx]

        print(
            f"  -> ORB (00:00) H={row['high']:.5f}, L={row['low']:.5f} (idx={idx})"
        )

        return {
            "high": row["high"],
            "low": row["low"],
            "mark_idx": int(day_df.index.get_loc(idx)),  # 0-based position
        }

    # ------------------ CLOSE BREAKOUT ------------------

    def _detect_breakout(self, day_df: pd.DataFrame, orb: Dict) -> Optional[Dict]:
        high_level = orb["high"]
        low_level = orb["low"]
        start_pos = orb["mark_idx"] + 1  # 00:00 ke baad wali candle se

        for pos in range(start_pos, len(day_df)):
            row = day_df.iloc[pos]
            close = row["close"]

            if close > high_level:
                print(
                    GREEN + f"  -> BUY BO at {row['time']} (close {close:.5f} > {high_level:.5f})" + RESET)
                return {
                    "side": "B",
                    "bo_time": row["time"],
                    "input_price": row["high"],
                    "index": pos,
                }

            if close < low_level:
                print(
                    YELLOW + f"  -> SELL BO at {row['time']} (close {close:.5f} < {low_level:.5f})" + RESET)
                return {
                    "side": "S",
                    "bo_time": row["time"],
                    "input_price": row["low"],
                    "index": pos,
                }

        print("  -> No close breakout for this day")
        return None

    # ------------------ ATR BUFFER ENTRY HELPER ------------------

    def _check_atr_buffer_entry(
        self,
        entry_price: float,
        side: str,
        atr: float,
        high: float,
        low: float,
        is_new_orb_shifted: bool = False,
    ) -> Optional[float]:
        """
        ATR buffer entry:
        NORMAL DAY:
          - If ATR < 0.00150 -> ATR/7
          - Else             -> ATR/10

        SPECIAL DAY (NEW ORB strong -> 9:30 shift, Rule-5):
          - If ATR < 0.00150 -> ATR/5
          - Else             -> ATR/7

        BUY:  high >= entry + buffer -> fill at (entry + buffer)
        SELL: low  <= entry - buffer -> fill at (entry - buffer)
        """
        if atr is None or atr <= 0:
            return None

        # SPECIAL: NEW ORB strong -> 9:30 shift day
        if is_new_orb_shifted:
            if atr < 0.00150:
                buffer_val = atr / 5.0
            else:
                buffer_val = atr / 7.0
        else:
            # Normal day
            if atr < 0.00150:
                buffer_val = atr / 7.0
            else:
                buffer_val = atr / 10.0

        if side == "B":
            trigger_level = entry_price + buffer_val
            if high >= trigger_level:
                return round(trigger_level, 5)
        else:  # "S"
            trigger_level = entry_price - buffer_val
            if low <= trigger_level:
                return round(trigger_level, 5)

        return None

    # ------------------ ENTRY WINDOW (PENDING) ------------------

    def _wait_for_entry_in_window(
        self,
        day_df: pd.DataFrame,
        setup: Dict,
        bo_time: datetime,
        is_new_orb_shifted: bool = False,
    ) -> Optional[Dict]:
        """
        7:31–19:30 or 7:31–20:30 IST entry window depending on market type.
        Entry sirf ORB breakout ke baad hi allow.
        If price never touches entry in this window → order_expired (no trade).
        """
        entry_price = setup["entry"]
        side = setup["side"]

        day_date = bo_time.date()

        # 1. market type infer karo (3:30_window vs 2:30_window)
        market_type = self._infer_market_type(day_df)

        # 2. day ke hisaab se expiry time (IST)
        expire_ist = self._get_entry_expire_time(day_date, market_type)

        # 3. IST side pe min start = 7:31, but not before breakout candle
        after_bo = day_df[day_df["time"] > bo_time]
        if after_bo.empty:
            return None
        first_after_bo_time = after_bo["time"].iloc[0]

        entry_start_ist_dt = datetime.combine(day_date, self.entry_start_ist)
        entry_start_server_by_time = DSTHelper.ist_to_server(
            entry_start_ist_dt)
        entry_start_server = max(
            entry_start_server_by_time, first_after_bo_time)

        expire_ist_dt = datetime.combine(day_date, expire_ist)
        expire_server = DSTHelper.ist_to_server(expire_ist_dt)

        print(
            f"  -> Entry window (server): {entry_start_server} to {expire_server} | "
            f"Market: {market_type}"
        )

        mask = (day_df["time"] >= entry_start_server) & (
            day_df["time"] < expire_server)
        search_df = day_df.loc[mask]

        if search_df.empty:
            print("  -> No candles in entry window (pending expires)")
            return None

        for idx in search_df.index:
            row = day_df.loc[idx]
            row_time = row["time"]
            high = row["high"]
            low = row["low"]
            atr = row.get("atr", 0.0)

            actual_entry = self._check_atr_buffer_entry(
                entry_price=entry_price,
                side=side,
                atr=atr,
                high=high,
                low=low,
                is_new_orb_shifted=is_new_orb_shifted,
            )

            if actual_entry is not None:
                return {
                    "entry_idx": idx,
                    "entry_time": row_time,
                    "actual_entry": actual_entry,
                }

        return None

    def _wait_for_first_fill_in_window(
        self,
        day_df: pd.DataFrame,
        buy_setup: Dict,
        sell_setup: Dict,
        bo_time: datetime,
        is_new_orb_shifted: bool = False,
    ) -> Optional[Dict]:
        """
        Breakout ke baad buy/sell dono entries ko race mode me watch karo.
        Jo side pehle fill ho wahi actual trade.
        Agar dono same H1 candle me fill ho jayein, M1 se first touch decide karne ki koshish karo.
        """
        buy_result = self._wait_for_entry_in_window(
            day_df, buy_setup, bo_time, is_new_orb_shifted
        )
        sell_result = self._wait_for_entry_in_window(
            day_df, sell_setup, bo_time, is_new_orb_shifted
        )

        if not buy_result and not sell_result:
            return None

        if buy_result and not sell_result:
            return {"setup": buy_setup, "entry_result": buy_result}

        if sell_result and not buy_result:
            return {"setup": sell_setup, "entry_result": sell_result}

        buy_time = buy_result["entry_time"]
        sell_time = sell_result["entry_time"]

        if buy_time < sell_time:
            return {"setup": buy_setup, "entry_result": buy_result}

        if sell_time < buy_time:
            return {"setup": sell_setup, "entry_result": sell_result}

        # Same H1 candle fill on both sides -> try M1 tie-break
        from_time = buy_time
        to_time = buy_time + timedelta(hours=1)

        try:
            from live_data_mt5 import fetch_live_1m
            m1_df = fetch_live_1m(self.pair, from_time, to_time)

            if not m1_df.empty:
                m1_df["time"] = pd.to_datetime(m1_df["time"])
                mask = (m1_df["time"] >= from_time) & (
                    m1_df["time"] <= to_time)
                m1_df = m1_df.loc[mask].sort_values(
                    "time").reset_index(drop=True)

                for _, row in m1_df.iterrows():
                    buy_hit = row["high"] >= buy_setup["entry"]
                    sell_hit = row["low"] <= sell_setup["entry"]

                    if buy_hit and not sell_hit:
                        return {"setup": buy_setup, "entry_result": buy_result}

                    if sell_hit and not buy_hit:
                        return {"setup": sell_setup, "entry_result": sell_result}

                    if buy_hit and sell_hit:
                        # same M1 candle ambiguity -> conservative fallback
                        # current fallback: choose side with smaller distance from candle open
                        open_price = row["open"]
                        buy_dist = abs(buy_setup["entry"] - open_price)
                        sell_dist = abs(open_price - sell_setup["entry"])

                        if buy_dist <= sell_dist:
                            return {"setup": buy_setup, "entry_result": buy_result}
                        else:
                            return {"setup": sell_setup, "entry_result": sell_result}

        except Exception as e:
            print(
                YELLOW
                + f"  -> Same-candle dual-fill M1 tie-break failed ({e}), defaulting to earlier listed side"
                + RESET
            )

        # fallback if same timestamp and M1 unavailable
        return {"setup": buy_setup, "entry_result": buy_result}

    def _resolve_same_candle_exit_with_m1(
        self,
        side: str,
        entry_time: datetime,
        actual_entry: float,
        sl: float,
        tp: float,
    ) -> Optional[str]:
        """
        Same H1 candle me entry + SL/TP ambiguity ko M1 sequence se resolve karo.
        Return:
            "tp", "sl", or None
        Rule:
        - Entry ke baad hi SL/TP valid hoga
        - Entry se pehle ka touch ignore hoga
        """
        from_time = entry_time
        to_time = entry_time + timedelta(hours=1)

        try:
            from live_data_mt5 import fetch_live_1m
            m1_df = fetch_live_1m(self.pair, from_time, to_time)
        except Exception as e:
            print(
                YELLOW
                + f"  -> M1 fetch failed ({e}), keeping H1 result as-is"
                + RESET
            )
            return None

        if m1_df is None or m1_df.empty:
            print(YELLOW + "  -> M1 empty, keeping H1 result as-is" + RESET)
            return None

        m1_df["time"] = pd.to_datetime(m1_df["time"])
        m1_df = m1_df[(m1_df["time"] >= from_time) &
                      (m1_df["time"] <= to_time)].copy()
        m1_df = m1_df.sort_values("time").reset_index(drop=True)

        entry_found = False

        for _, row in m1_df.iterrows():
            o = row["open"]
            h = row["high"]
            l = row["low"]
            t = row["time"]

            if side == "B":
                entry_hit = h >= actual_entry
            else:
                entry_hit = l <= actual_entry

            if not entry_found:
                if not entry_hit:
                    continue

                entry_found = True
                print(CYAN + f"  -> M1 entry confirmed at/after {t}" + RESET)

                # Same M1 candle me entry ke baad immediate ambiguity
                if side == "B":
                    tp_hit = h >= tp
                    sl_hit = l <= sl
                else:
                    tp_hit = l <= tp
                    sl_hit = h >= sl

                if tp_hit and sl_hit:
                    print(
                        YELLOW
                        + "  -> Same M1 candle me entry + TP/SL ambiguity, conservative SL applied"
                        + RESET
                    )
                    return "sl"

                if tp_hit:
                    return "tp"
                if sl_hit:
                    return "sl"

                continue

            # Entry milne ke baad next M1 candles me exit check
            if side == "B":
                tp_hit = h >= tp
                sl_hit = l <= sl
            else:
                tp_hit = l <= tp
                sl_hit = h >= sl

            if tp_hit and sl_hit:
                print(
                    YELLOW
                    + f"  -> Post-entry same M1 ambiguity at {t}, conservative SL applied"
                    + RESET
                )
                return "sl"

            if tp_hit:
                return "tp"

            if sl_hit:
                return "sl"

        return None

        # ------------------ TRADE SIMULATION (MULTI-DAY) ------------------

    # ------------------ TRADE SIMULATION (MULTI-DAY) ------------------

    def _simulate_trade(
        self,
        df: pd.DataFrame,          # FULL DATA, not day_df
        setup: Dict,
        entry_idx,                 # label index in full df
        actual_entry: float,
    ) -> Dict:
        """
        Simulate trade from entry until SL/TP or data end (multi-day).
        No time-based force exit; only TP/SL or data end.
        Special: agar entry aur SL/TP same H1 candle me aayein,
        to pehle 1-min data se confirm karega.
        """
        side = setup["side"]
        sl = setup["sl"]
        tp = setup["tp"]
        lot_size = setup["lot_size"]

        # entry_idx is label index -> use .loc on FULL df
        entry_row = df.loc[entry_idx]
        entry_time = entry_row["time"]
        entry_day = entry_time.date()

        if side == "B":
            risk = actual_entry - sl
        else:
            risk = sl - actual_entry

        tp_adjusted = False

        pos = df.index.get_loc(entry_idx)
        idx = pos

        exit_price = actual_entry
        exit_time = entry_time
        result = "session_exit"

        while idx < len(df):
            row = df.iloc[idx]
            row_time = row["time"]
            high = row["high"]
            low = row["low"]

            # ---- Day change detection & TP adjustment (2R -> 1.5R approx) ----
            if (row_time.date() != entry_day) and (not tp_adjusted):
                if side == "B":
                    orig_tp_dist = tp - actual_entry
                else:
                    orig_tp_dist = actual_entry - tp

                if orig_tp_dist > 0:
                    new_tp_dist = orig_tp_dist * 0.75
                    old_tp = tp
                    if side == "B":
                        tp = actual_entry + new_tp_dist
                    else:
                        tp = actual_entry - new_tp_dist
                    tp_adjusted = True
                    print(
                        CYAN
                        + f"  -> Day changed, TP reduced from {old_tp:.5f} to {tp:.5f} (~1:1.5) at {row_time}"
                        + RESET
                    )

            # ---- Normal TP/SL logic + SAME-CANDLE FLAG ----
            hit_same_candle = False
            hit_type = None  # "tp" or "sl"

            if side == "B":
                if high >= tp:
                    exit_price = tp
                    exit_time = row_time
                    result = "tp"
                    if row_time == entry_time:
                        hit_same_candle = True
                        hit_type = "tp"
                    else:
                        break
                if low <= sl:
                    exit_price = sl
                    exit_time = row_time
                    result = "sl"
                    if row_time == entry_time:
                        hit_same_candle = True
                        hit_type = "sl"
                    else:
                        break
            else:
                if low <= tp:
                    exit_price = tp
                    exit_time = row_time
                    result = "tp"
                    if row_time == entry_time:
                        hit_same_candle = True
                        hit_type = "tp"
                    else:
                        break
                if high >= sl:
                    exit_price = sl
                    exit_time = row_time
                    result = "sl"
                    if row_time == entry_time:
                        hit_same_candle = True
                        hit_type = "sl"
                    else:
                        break

            # Agar same H1 candle me hit hua hai, to M1 sequence se resolve karo
            if hit_same_candle and hit_type is not None:
                print(
                    CYAN
                    + f"  -> Same H1 candle {hit_type.upper()} at {row_time}, checking M1 sequence..."
                    + RESET
                )

                resolved = self._resolve_same_candle_exit_with_m1(
                    side=side,
                    entry_time=entry_time,
                    actual_entry=actual_entry,
                    sl=sl,
                    tp=tp,
                )

                if resolved is None:
                    print(
                        YELLOW
                        + "  -> M1 could not resolve sequence, keeping H1 result as-is"
                        + RESET
                    )
                    break

                if resolved != hit_type:
                    print(
                        YELLOW
                        + f"  -> M1 sequence override: H1 said {hit_type.upper()}, actual is {resolved.upper()}"
                        + RESET
                    )

                if resolved == "tp":
                    exit_price = tp
                    exit_time = row_time
                    result = "tp"
                else:
                    exit_price = sl
                    exit_time = row_time
                    result = "sl"

                break

            idx += 1

        # Agar TP/SL nahi laga aur poore data ka end aa gaya:
        if result == "session_exit":
            last_row = df.iloc[-1]
            exit_price = last_row["close"]
            exit_time = last_row["time"]

        # PNL
        pip_value = StrategyCalculator.get_pip_value_per_lot(
            self.pair, actual_entry
        )

        if self.pair.endswith("JPY"):
            pip_multiplier = 100.0
        else:
            pip_multiplier = 10000.0

        if side == "B":
            pnl_pips = (exit_price - actual_entry) * pip_multiplier
        else:
            pnl_pips = (actual_entry - exit_price) * pip_multiplier

        pnl_amount = pnl_pips * pip_value * lot_size

        self.current_fund += pnl_amount
        self.equity_high = max(self.equity_high, self.current_fund)
        drawdown = self.equity_high - self.current_fund
        self.max_drawdown = max(self.max_drawdown, drawdown)

        # STOP condition: agar fund 0 ya neeche chala gaya
        if self.current_fund <= 0:
            print(
                YELLOW
                + f"  -> Fund depleted (fund={self.current_fund:.2f}), stopping further trades"
                + RESET
            )
            self.stop_requested = True

        trade_record = {
            "date": entry_time.date(),
            "pair": self.pair,
            "side": side,
            "entry_time": entry_time,
            "entry_price": actual_entry,
            "sl": sl,
            "tp": tp,
            "exit_time": exit_time,
            "exit_price": exit_price,
            "result": result,
            "pnl_pips": round(pnl_pips, 1),
            "pnl_amount": round(pnl_amount, 2),
            "fund_after": round(self.current_fund, 2),
        }

        self.trades.append(trade_record)
        self.total_trades += 1

        return trade_record

    def run_single_pair(self, csv_path: str) -> None:
        df = pd.read_csv(csv_path)
        df["time"] = pd.to_datetime(df["datetime"])  # server time
        df = df.sort_values("time").reset_index(drop=True)

        # ATR on full data
        df = self._add_atr_column(df)

        print(f"Loaded {len(df)} 1H candles from {csv_path}")

        grouped = df.groupby(df["time"].dt.date)

        for day, day_df in grouped:
            print("\n" + "=" * 60)
            print(f"[{self.pair}] Processing: {day}")
            print(f"Current Fund: ${self.current_fund:.2f}")

            # Basic day validation (00:00 + ATR)
            if not self._validate_day(day_df):
                print("  -> Day invalid, skipping")
                continue

            # ORB decision (3 rules)
            orb_info = self._decide_orb(day_df)
            orb = orb_info["orb"]
            rule_used = orb_info["rule"]
            breakout = orb_info["breakout"]  # ho sakta hai None
            is_new_orb_shifted = orb_info.get("is_new_orb_shifted", False)

            if not orb:
                print("  -> No ORB available for this day, skipping")
                continue

            if rule_used == 0:
                print(CYAN + "  -> ORB 00:00 retained (no rule triggered)" + RESET)

            # Agar ORB helper me breakout nahi mila, yahan se dhundo
            if breakout is None:
                breakout = self._detect_breakout(day_df, orb)
                if not breakout:
                    print("  -> No breakout after selected ORB candle")
                    continue

            print(f"  -> Gann INPUT price: {breakout['input_price']:.5f}")

            # 🔹 Gann levels from LOCAL JSON LOOKUP
            gann_levels = self._get_gann_from_lookup(breakout["input_price"])
            if not gann_levels:
                print("  -> Local Gann lookup failed, skipping day")
                continue

            fund = self.current_fund

            # Weekly risk ramp based on day
            if self.start_date is not None:
                week_num = ((day - self.start_date).days // 7) + 1
            else:
                week_num = 1

            risk_percent = self.base_risk_percent + (week_num - 1)
            print(f"  -> Week {week_num}: Risk={risk_percent:.1f}%")

            # Breakout sirf Gann trigger ke liye hai, side force nahi karega
            buy_setup = StrategyCalculator.get_buy_bo_primary(
                gann_levels, fund, risk_percent, pair=self.pair
            )
            sell_setup = StrategyCalculator.get_sell_bo_primary(
                gann_levels, fund, risk_percent, pair=self.pair
            )

            buy_setup["side"] = "B"
            sell_setup["side"] = "S"

            primary = buy_setup
            opposite = sell_setup

            print(
                f"  -> BUY  Entry={buy_setup['entry']:.5f}, "
                f"SL={buy_setup['sl']:.5f}, TP={buy_setup['tp']:.5f}, Lot={buy_setup['lot_size']:.2f}"
            )
            print(
                f"  -> SELL Entry={sell_setup['entry']:.5f}, "
                f"SL={sell_setup['sl']:.5f}, TP={sell_setup['tp']:.5f}, Lot={sell_setup['lot_size']:.2f}"
            )

            # Do for both legs, but agar ek side fill ho jaye to dusri skip
            any_filled = False

            for setup in (buy_setup, sell_setup):
                # Agar pehle hi koi trade fill ho chuka hai, dusri side skip
                if any_filled:
                    print(
                        f"  -> {setup['side']} leg skipped because opposite side already filled")
                    continue

                entry_result = self._wait_for_entry_in_window(
                    day_df, setup, breakout["bo_time"], is_new_orb_shifted
                )

                if not entry_result:
                    print(
                        f"  -> {setup['side']} pending not filled till 19:30 IST "
                        f"(order_expired_1930)"
                    )
                    self.trades.append(
                        {
                            "date": day,
                            "pair": self.pair,
                            "side": setup["side"],
                            "entry_time": None,
                            "entry_price": setup["entry"],
                            "sl": setup["sl"],
                            "tp": setup["tp"],
                            "exit_time": None,
                            "exit_price": None,
                            "result": "order_expired_1930",
                            "pnl_pips": 0.0,
                            "pnl_amount": 0.0,
                            "fund_after": round(self.current_fund, 2),
                        }
                    )
                    self.total_trades += 1
                    continue

                # Yahan aate hi is side ka order fill ho gaya
                any_filled = True

                print(
                    f"  -> {setup['side']} Entry filled at {entry_result['entry_time']}, "
                    f"price={entry_result['actual_entry']:.5f}"
                )

                trade = self._simulate_trade(
                    df,  # FULL DATA
                    setup,
                    entry_result["entry_idx"],
                    entry_result["actual_entry"],
                )

                print(
                    f"  -> {setup['side']} Exit {trade['result']} at {trade['exit_time']}, "
                    f"price={trade['exit_price']:.5f}, "
                    f"PNL=${trade['pnl_amount']:.2f}, Fund=${trade['fund_after']:.2f}"
                )

        # Win rate
        wins = sum(1 for t in self.trades if t["result"] == "tp")
        if self.total_trades > 0:
            self.win_rate = wins / self.total_trades * 100.0

        # ------------------ EXCEL EXPORT ------------------

    def run_backtest(self, specs) -> None:
        """
        specs: list of dicts:
        [
          {"pair": "EURAUD.raw", "csv": "EURAUD_H1.csv"},
          {"pair": "GBPAUD.raw", "csv": "GBPAUD_H1.csv"},
          ...
        ]
        Shared fund across all pairs.

        NAYA FLOW:
        - Global min_date..max_date nikalo (START_DATE/END_DATE ke andar)
        - Har day ke liye sab pairs loop, us din ka slice run_single_day se process
        """
        # 1) Sare CSV load + date range collect
        data_by_pair = {}
        all_dates = set()

        for spec in specs:
            pair = spec["pair"]
            csv_path = spec["csv"]

            df = pd.read_csv(csv_path)
            df["time"] = pd.to_datetime(df["datetime"])
            df = df.sort_values("time").reset_index(drop=True)

            # Optional engine-level date filter
            if self.start_date is not None:
                df = df[df["time"].dt.date >= self.start_date]
            if self.end_date is not None:
                df = df[df["time"].dt.date <= self.end_date]

            if df.empty:
                continue

            data_by_pair[pair] = df
            all_dates.update(df["time"].dt.date.unique())

        if not data_by_pair or not all_dates:
            print("No data available for given specs/date range.")
            return

        # 2) Global date range (sorted)
        all_dates = sorted(all_dates)

        print(f"\nTotal unique days in range: {len(all_dates)}")

        # 3) Main loop: per day -> per pair
        for day in all_dates:
            print("\n" + "=" * 60)
            print(f"PROCESSING DAY: {day}")

            for pair, df in data_by_pair.items():
                # Global stop check
                if getattr(self, "stop_requested", False):
                    break

                # Is pair ke liye is day ka slice
                day_mask = df["time"].dt.date == day
                day_df = df.loc[day_mask].copy()
                if day_df.empty:
                    continue

                # Engine state set
                self.pair = pair
                print("\n" + "-" * 40)
                print(f"[{pair}] Processing: {day}")
                print(f"Current Fund: ${self.current_fund:.2f}")

                # ATR ensure (full df pe ek hi baar)
                if "atr" not in day_df.columns:
                    full_df = data_by_pair[pair]
                    if "atr" not in full_df.columns:
                        full_df = self._add_atr_column(full_df)
                        data_by_pair[pair] = full_df
                    day_df = full_df[full_df["time"].dt.date == day]

                # Basic day validation (00:00 + ATR)
                if not self._validate_day(day_df):
                    print("  -> Day invalid, skipping")
                    continue

                # ORB decision (3 rules)
                orb_info = self._decide_orb(day_df)
                orb = orb_info["orb"]
                rule_used = orb_info["rule"]
                breakout = orb_info["breakout"]
                is_new_orb_shifted = orb_info.get("is_new_orb_shifted", False)

                if not orb:
                    print("  -> No ORB available for this day, skipping")
                    continue

                if rule_used == 0:
                    print(CYAN + "  -> ORB 00:00 retained (no rule triggered)" + RESET)

                # Agar ORB helper me breakout nahi mila, yahan se dhundo
                if breakout is None:
                    breakout = self._detect_breakout(day_df, orb)
                    if not breakout:
                        print("  -> No breakout after selected ORB candle")
                        continue

                print(f"  -> Gann INPUT price: {breakout['input_price']:.5f}")

                # Gann levels from LOCAL JSON LOOKUP
                gann_levels = self._get_gann_from_lookup(
                    breakout["input_price"]
                )
                if not gann_levels:
                    print("  -> Local Gann lookup failed, skipping day")
                    continue

                fund = self.current_fund

                # Weekly risk ramp based on day
                if self.start_date is not None:
                    week_num = ((day - self.start_date).days // 7) + 1
                else:
                    week_num = 1

                risk_percent = self.base_risk_percent + (week_num - 1)
                print(f"  -> Week {week_num}: Risk={risk_percent:.1f}%")

                # Breakout sirf Gann trigger hai, side force nahi karega
                if breakout["side"] == "B":
                    buy_setup = StrategyCalculator.get_buy_bo_primary(
                        gann_levels, fund, risk_percent, pair=self.pair
                    )
                    sell_setup = StrategyCalculator.get_buy_bo_opp_sell(
                        gann_levels, fund, risk_percent, pair=self.pair
                    )
                else:  # breakout["side"] == "S"
                    sell_setup = StrategyCalculator.get_sell_bo_primary(
                        gann_levels, fund, risk_percent, pair=self.pair
                    )
                    buy_setup = StrategyCalculator.get_sell_bo_opp_buy(
                        gann_levels, fund, risk_percent, pair=self.pair
                    )

                buy_setup["side"] = "B"
                sell_setup["side"] = "S"

                print(
                    f"  -> BUY  Entry={buy_setup['entry']:.5f}, "
                    f"SL={buy_setup['sl']:.5f}, TP={buy_setup['tp']:.5f}, Lot={buy_setup['lot_size']:.2f}"
                )
                print(
                    f"  -> SELL Entry={sell_setup['entry']:.5f}, "
                    f"SL={sell_setup['sl']:.5f}, TP={sell_setup['tp']:.5f}, Lot={sell_setup['lot_size']:.2f}"
                )

                # Race mode: jo side pehle fill ho wahi trade
                fill = self._wait_for_first_fill_in_window(
                    day_df,
                    buy_setup,
                    sell_setup,
                    breakout["bo_time"],
                    is_new_orb_shifted,
                )

                if not fill:
                    print(
                        "  -> Neither BUY nor SELL entry filled till expiry (order_expired_1930)"
                    )

                    for setup in (buy_setup, sell_setup):
                        self.trades.append(
                            {
                                "date": day,
                                "pair": self.pair,
                                "side": setup["side"],
                                "entry_time": None,
                                "entry_price": setup["entry"],
                                "sl": setup["sl"],
                                "tp": setup["tp"],
                                "exit_time": None,
                                "exit_price": None,
                                "result": "order_expired_1930",
                                "pnl_pips": 0.0,
                                "pnl_amount": 0.0,
                                "fund_after": round(self.current_fund, 2),
                            }
                        )
                        self.total_trades += 1
                    continue

                chosen_setup = fill["setup"]
                entry_result = fill["entry_result"]
                skipped_side = "S" if chosen_setup["side"] == "B" else "B"

                print(
                    f"  -> {chosen_setup['side']} Entry filled FIRST at {entry_result['entry_time']}, "
                    f"price={entry_result['actual_entry']:.5f}"
                )
                print(
                    f"  -> {skipped_side} leg skipped because opposite side already filled first"
                )

                trade = self._simulate_trade(
                    df,
                    chosen_setup,
                    entry_result["entry_idx"],
                    entry_result["actual_entry"],
                )

                print(
                    f"  -> {chosen_setup['side']} Exit {trade['result']} at {trade['exit_time']}, "
                    f"price={trade['exit_price']:.5f}, "
                    f"PNL=${trade['pnl_amount']:.2f}, Fund=${trade['fund_after']:.2f}"
                )

                # Agar fund 0 ya neeche chala gaya, global stop
                if getattr(self, "stop_requested", False):
                    print(
                        YELLOW
                        + "  -> Global stop triggered (fund <= 0), terminating backtest"
                        + RESET
                    )
                    break  # pair loop se bahar

            # outer day loop ke liye stop check
            if getattr(self, "stop_requested", False):
                break

        # Win rate
        wins = sum(1 for t in self.trades if t["result"] == "tp")
        if self.total_trades > 0:
            self.win_rate = wins / self.total_trades * 100.0

        losses = sum(1 for t in self.trades if t["result"] == "sl")
        print(
            f"\nTOTAL TRADES: {self.total_trades}, "
            f"WINS (TP): {wins}, LOSSES (SL): {losses}"
        )

    def generate_signal_for_latest_day(self, pair: str, df_1h: pd.DataFrame):
        """
        Live use ke liye:
        - df_1h: MT5 se aaya latest 1H data (columns: datetime, open, high, low, close)
        - Sirf last day ka ORB+Gann signal nikalta hai
        Return:
            {
              "day": date,
              "side": "B"/"S",
              "entry": float,
              "sl": float,
              "tp": float,
              "lot": float,
            }
            ya None agar koi trade nahi.
        """
        self.pair = pair

        df = df_1h.copy()
        # MT5 se aane wali datetime column ka naam tu already "datetime" use kar raha hai
        df["time"] = pd.to_datetime(df["datetime"])
        df = df.sort_values("time").reset_index(drop=True)

        # ATR full df pe
        df = self._add_atr_column(df)

        # Latest day choose
        day = df["time"].dt.date.max()
        day_df = df[df["time"].dt.date == day]

        print(f"\n[Signal] {pair} latest day = {day}")
        print(f"  -> Rows in day_df: {len(day_df)}")

        # Basic day validation
        if not self._validate_day(day_df):
            print("  -> Day invalid, no signal")
            return None

        # ORB + rules
        orb_info = self._decide_orb(day_df)
        orb = orb_info["orb"]
        breakout = orb_info["breakout"]
        if not orb or not breakout:
            print("  -> No ORB/breakout, no signal")
            return None

        print(f"  -> Gann INPUT price: {breakout['input_price']:.5f}")

        # Gann levels from local lookup
        gann_levels = self._get_gann_from_lookup(breakout["input_price"])
        if not gann_levels:
            print("  -> Gann lookup failed, no signal")
            return None

        fund = self.current_fund
        # Filhaal week ramp simple: base_risk_percent
        risk_percent = self.base_risk_percent

        if breakout["side"] == "B":
            primary = StrategyCalculator.get_buy_bo_primary(
                gann_levels, fund, risk_percent, pair=self.pair
            )
            primary["side"] = "B"
        else:
            primary = StrategyCalculator.get_sell_bo_primary(
                gann_levels, fund, risk_percent, pair=self.pair
            )
            primary["side"] = "S"

        print(
            f"  -> SIGNAL {primary['side']} Entry={primary['entry']:.5f}, "
            f"SL={primary['sl']:.5f}, TP={primary['tp']:.5f}, Lot={primary['lot_size']:.2f}"
        )

        return {
            "day": day,
            "side": primary["side"],
            "entry": primary["entry"],
            "sl": primary["sl"],
            "tp": primary["tp"],
            "lot": primary["lot_size"],
        }

    def export_to_excel(self, output_path: str) -> None:
        folder = "backtests"
        os.makedirs(folder, exist_ok=True)
        full_path = os.path.join(folder, os.path.basename(output_path))

        total_trades = len(self.trades)
        net_pnl = self.current_fund - self.initial_fund

        # Result counts
        wins = sum(1 for t in self.trades if t["result"] == "tp")
        losses = sum(1 for t in self.trades if t["result"] == "sl")
        expired = sum(
            1 for t in self.trades if t["result"] == "order_expired_1930")
        others = total_trades - (wins + losses + expired)

        summary = {
            "Metric": [
                "Initial Fund",
                "Final Fund",
                "Net PNL",
                "Total Records",
                "Win Rate (TP only)",
                "Max Drawdown",
                "Total Trades",
                "Wins (TP)",
                "Losses (SL)",
                "Expired Orders",
                "Other Results",
            ],
            "Value": [
                self.initial_fund,
                self.current_fund,
                net_pnl,
                total_trades,
                f"{self.win_rate:.2f}%" if total_trades > 0 else "N/A",
                self.max_drawdown,
                total_trades,
                wins,
                losses,
                expired,
                others,
            ],
        }

        with pd.ExcelWriter(full_path, engine="openpyxl") as writer:
            if self.trades:
                trades_df = pd.DataFrame(self.trades)
                trades_df.to_excel(writer, sheet_name="Trades", index=False)
            summary_df = pd.DataFrame(summary)
            summary_df.to_excel(writer, sheet_name="Summary", index=False)

        print(f"\nBacktest results exported to: {full_path}")
