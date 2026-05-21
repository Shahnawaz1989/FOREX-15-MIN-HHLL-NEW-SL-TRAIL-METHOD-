from strategy_calculator import StrategyCalculator
from gann_fetcher import GannFetcher
from live_data_mt5 import fetch_live_1m
from live_fund_manager import get_live_usable_fund
import os
import pandas as pd
import numpy as np
from datetime import datetime, time, timedelta
from typing import List, Dict, Optional
import pytz
import json
import bisect
import numpy as np
from backtest_orb_runner_helpers import (
    prepare_backtest_data,
    get_weekly_risk_percent,
    process_pair_day,
)
from backtest_orb_setup_builder import (
    build_high_setup_for_day,
    build_low_setup_for_day,
    select_entry_target_from_gate_and_39,
)
from backtest_orb_trade_simulator import (
    resolve_same_candle_exit_with_m1,
    fetch_m1_data_for_window,
    compute_m1_mae_after_entry,
    simulate_trade,
)

# Simple ANSI colors for terminal
RESET = "\033[0m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RED = "\033[91m"

TERMINAL_FILLED_STATUSES = {"FILLED_BUY", "FILLED_SELL", "BE_APPLIED"}

TERMINAL_DEAD_STATUSES = {
    "FAILED",
    "CANCELLEDEOD",
    "CANCELLEDNEWHHLL",
    "EXPIRED",
    "ORDEREXPIRED1930",
}

ACTIVE_FILE_STATUSES = {"NEW", "PLACED"}

REGISTRY_DIR = "live_registry"
REGISTRY_FILE = os.path.join(REGISTRY_DIR, "hl_live_registry.json")

RESULT_TP = "tp"
RESULT_SL = "sl"
RESULT_SL_LOCK10 = "sl_lock10"
RESULT_SESSION_EXIT = "session_exit"
RESULT_ORDER_EXPIRED = "orderexpired1930"

REGISTRY_STATUS_GENERATED = "GENERATED"
REGISTRY_STATUS_COMPLETED = "COMPLETED"
REGISTRY_STATUS_ENTRY_HIT = "ENTRY_HIT"
REGISTRY_STATUS_CANCELLED_EOD = "CANCELLEDEOD"
REGISTRY_STATUS_CANCELLED_NEW_HH_LL = "CANCELLEDNEWHHLL"
REGISTRY_STATUS_ORDER_EXPIRED = "ORDEREXPIRED1930"

COMPLETED_RESULTS = {RESULT_TP, RESULT_SL, RESULT_SL_LOCK10}
NON_COMPLETED_RESULTS = {RESULT_ORDER_EXPIRED, RESULT_SESSION_EXIT}


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

        # Script-se-controlled risk%
        self.initial_risk_percent = float(initial_risk_percent)
        self.base_risk_percent = float(
            initial_risk_percent)  # weekly ramp ka base

        self.pair = pair

        # ---- Live equity sizing config ----
        self.use_live_equity_sizing = False
        self.live_source_fund = None
        self.live_strategy_start_fund = None

        # Date filter / weekly risk reference
        self.start_date = None
        self.end_date = None

        self.trades: List[Dict] = []
        self.max_drawdown = 0.0
        self.equity_high = initial_fund
        self.total_trades = 0
        self.win_rate = 0.0
        self.stop_requested = False

        # NEW: final list for trades > 2 hours
        self.long_duration_trades = []

        # Volatility filter
        self.atr_period = 14
        self.vol_ratio_threshold = 1.20  # < 1.20 = VALID

        # IST-based entry window
        self.entry_start_ist = time(7, 31)
        self.expire_ist = time(19, 30)

        # SERVER-time HH/LL disable window:
        # Detection allowed, but new order processing blocked in this window.
        self.hhll_disable_start_server = time(11, 15)
        self.hhll_disable_end_server = time(16, 45)

        # 🔹 Local Gann lookup load (JSON)
        self.gann_lookup = self._load_gann_lookup("forex_gann_lookup_1_3.json")

        # 🔹 OLD fixed-lot logic ko effectively disable kar do
        # Saara lot sizing ab StrategyCalculator.calculate_lot_size ke through hoga
        self.max_backtest_lot = None
        self.fixed_lot_mode = False
        self.fixed_lot_value = None

        # summaries
        self.daily_briefings = []
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

    def _debug_atr_row(self, df: pd.DataFrame, ts):
        row = df[df["time"] == pd.Timestamp(ts)]
        if row.empty:
            print(f"ATR DEBUG: no row for {ts}")
            return

        i = row.index[0]
        prev_close = df.loc[i - 1, "close"] if i > 0 else np.nan
        tr = max(
            df.loc[i, "high"] - df.loc[i, "low"],
            abs(df.loc[i, "high"] - prev_close) if pd.notna(prev_close) else 0,
            abs(df.loc[i, "low"] - prev_close) if pd.notna(prev_close) else 0,
        )

        print(
            f"ATR DEBUG | time={df.loc[i, 'time']} | "
            f"high={df.loc[i, 'high']:.5f} low={df.loc[i, 'low']:.5f} "
            f"close={df.loc[i, 'close']:.5f} prev_close={prev_close:.5f} "
            f"TR={tr:.5f} ATR={df.loc[i, 'atr']:.5f}"
        )

    # ------------------ DAY VALIDATION (basic) ------------------

    def _validate_day(self, day_df: pd.DataFrame) -> bool:
        """
        New timed-session strategy validation:
        - Day must have candles
        - ATR column should exist
        - At least some valid ATR values should be present
        """
        if day_df.empty:
            return False

        if "atr" not in day_df.columns:
            print("  -> ATR column missing")
            return False

        valid_atr = day_df["atr"].dropna()
        valid_atr = valid_atr[valid_atr > 0]

        if valid_atr.empty:
            print("  -> ATR not available for this day")
            return False

        return True

    def _select_entry_target_from_gate_and_39(
        self,
        side: str,
        gate_touched: bool,
        r39_touched: bool,
        at: float,
        t1: float,
        t15: float,
    ) -> Dict:
        return select_entry_target_from_gate_and_39(
            side=side,
            gate_touched=gate_touched,
            r39_touched=r39_touched,
            at=at,
            t1=t1,
            t15=t15,
        )

    def _build_low_setup_for_day(
        self,
        day_df: pd.DataFrame,
        fund: float,
        risk_percent: float,
    ) -> Optional[Dict]:
        return build_low_setup_for_day(
            engine=self,
            day_df=day_df,
            fund=fund,
            risk_percent=risk_percent,
        )

    def _build_high_setup_for_day(
        self,
        day_df: pd.DataFrame,
        fund: float,
        risk_percent: float,
    ) -> Optional[Dict]:
        return build_high_setup_for_day(
            engine=self,
            day_df=day_df,
            fund=fund,
            risk_percent=risk_percent,
        )

    def _wait_for_entry_in_window(
        self,
        day_df: pd.DataFrame,
        setup: Dict,
        window_start_server: datetime,
        window_end_server: datetime,
        session_atr: float,
    ) -> Optional[Dict]:
        """
        Generic entry window on SERVER time.

        STRICT ATR BUFFER MODE:
        - Session ORB ka ATR use hoga
        - Buffer formula _check_atr_buffer_entry ke hisaab se apply hoga
        - Buffer hit nahi hua to entry nahi hogi
        - Direct touch fallback DISABLED
        """
        entry_price = float(setup["entry"])
        side = setup["side"]

        print(
            f"  -> Entry window (server): {window_start_server} to {window_end_server}"
        )
        print(f"  -> Session ATR for buffer: {session_atr:.5f}")

        mask = (day_df["time"] >= window_start_server) & (
            day_df["time"] < window_end_server
        )
        search_df = day_df.loc[mask]

        if search_df.empty:
            print("  -> No candles in entry window (pending expires)")
            return None

        if session_atr is None or session_atr <= 0:
            print("  -> Invalid session ATR, buffer entry disabled for this session")
            return None

        for idx in search_df.index:
            row = day_df.loc[idx]
            row_time = row["time"]
            high = float(row["high"])
            low = float(row["low"])

            actual_entry = self._check_atr_buffer_entry(
                entry_price=entry_price,
                side=side,
                atr=session_atr,
                high=high,
                low=low,
                is_new_orb_shifted=False,
            )

            if actual_entry is not None:
                print(
                    f"  -> {side} ATR-buffer entry hit at {row_time}, actual_entry={actual_entry:.5f}"
                )
                return {
                    "entry_idx": idx,
                    "entry_time": row_time,
                    "actual_entry": actual_entry,
                }

        print(f"  -> {side} pending not filled with ATR buffer in this session")
        return None

    def _wait_for_first_fill_in_window_session(
        self,
        day_df: pd.DataFrame,
        buy_setup: Dict,
        sell_setup: Dict,
        window_start_server: datetime,
        window_end_server: datetime,
        session_atr: float,
    ) -> Optional[Dict]:
        """
        BUY/SELL dono ko same session window me race mode me dekho.
        Jo side pehle fill ho wahi final trade.

        Dono sides ke liye same session ORB ATR-based buffer use hoga.
        """
        buy_result = self._wait_for_entry_in_window(
            day_df=day_df,
            setup=buy_setup,
            window_start_server=window_start_server,
            window_end_server=window_end_server,
            session_atr=session_atr,
        )

        sell_result = self._wait_for_entry_in_window(
            day_df=day_df,
            setup=sell_setup,
            window_start_server=window_start_server,
            window_end_server=window_end_server,
            session_atr=session_atr,
        )

        # Dono side expire ho gaye
        if not buy_result and not sell_result:
            return None

        # Sirf BUY fill
        if buy_result and not sell_result:
            return {"setup": buy_setup, "entry_result": buy_result}

        # Sirf SELL fill
        if sell_result and not buy_result:
            return {"setup": sell_setup, "entry_result": sell_result}

        # Dono fill hue -> jo pehle time pe aaya
        buy_time = buy_result["entry_time"]
        sell_time = sell_result["entry_time"]

        if buy_time < sell_time:
            return {"setup": buy_setup, "entry_result": buy_result}

        if sell_time < buy_time:
            return {"setup": sell_setup, "entry_result": sell_result}

        # Same candle pe dono fill -> open ke close side ko choose karo
        same_row = day_df[day_df["time"] == buy_time]
        if same_row.empty:
            return {"setup": buy_setup, "entry_result": buy_result}

        row = same_row.iloc[0]
        open_price = float(row["open"])

        buy_dist = abs(float(buy_setup["entry"]) - open_price)
        sell_dist = abs(open_price - float(sell_setup["entry"]))

        if buy_dist <= sell_dist:
            return {"setup": buy_setup, "entry_result": buy_result}
        else:
            return {"setup": sell_setup, "entry_result": sell_result}

    # ------------------ ENTRY WINDOW (PENDING) ------------------
    def _resolve_same_candle_exit_with_m1(
        self,
        side: str,
        entry_time,
        actual_entry: float,
        sl: float,
        tp: float,
    ):
        return resolve_same_candle_exit_with_m1(
            engine=self,
            side=side,
            entry_time=entry_time,
            actual_entry=actual_entry,
            sl=sl,
            tp=tp,
        )

    def _fetch_m1_data_for_window(self, start_time, end_time):
        return fetch_m1_data_for_window(
            engine=self,
            start_time=start_time,
            end_time=end_time,
        )

    def _compute_m1_mae_after_entry(
        self,
        side: str,
        entry_time,
        exit_time,
        actual_entry: float,
        lot_size: float,
    ):
        return compute_m1_mae_after_entry(
            engine=self,
            side=side,
            entry_time=entry_time,
            exit_time=exit_time,
            actual_entry=actual_entry,
            lot_size=lot_size,
        )

    def _simulate_trade(
        self,
        df: pd.DataFrame,
        setup: Dict,
        entry_idx,
        actual_entry: float,
    ):
        return simulate_trade(
            engine=self,
            df=df,
            setup=setup,
            entry_idx=entry_idx,
            actual_entry=actual_entry,
        )

    def run_backtest(self, specs) -> None:
        data_by_pair, all_dates = prepare_backtest_data(self, specs)

        self.daily_briefings = []
        self.long_duration_trades = []

        if not data_by_pair or not all_dates:
            print("No data available for given specs/date range.")
            return

        print(f"\nTotal unique days in range: {len(all_dates)}")

        for day in all_dates:
            print("\n" + "=" * 60)
            print(f"PROCESSING DAY: {day}")

            day_open_balance = self.current_fund
            day_profit = 0.0
            day_trade_count = 0
            day_tp_hits = 0
            day_risk_percent = None
            day_max_lot = 0.0

            for pair, df in data_by_pair.items():
                if getattr(self, "stop_requested", False):
                    break

                week_num, risk_percent = get_weekly_risk_percent(self, day)
                day_risk_percent = risk_percent

                trades = process_pair_day(self, day, pair, df)

                for trade in trades:
                    day_trade_count += 1
                    day_profit += float(trade["pnl_amount"])
                    day_max_lot = max(day_max_lot, float(
                        trade.get("lot_size", 0.0)))

                    if trade["result"] == "tp":
                        day_tp_hits += 1

                    duration_hours = (
                        pd.to_datetime(trade["exit_time"]) -
                        pd.to_datetime(trade["entry_time"])
                    ).total_seconds() / 3600.0

                    if duration_hours > 2:
                        enriched_trade = dict(trade)
                        enriched_trade["duration_hours"] = round(
                            duration_hours, 2)
                        self.long_duration_trades.append(enriched_trade)

            day_close_balance = self.current_fund
            self.daily_briefings.append({
                "date": day,
                "open_balance": round(day_open_balance, 2),
                "close_balance": round(day_close_balance, 2),
                "profit": round(day_profit, 2),
                "trade_count": day_trade_count,
                "tp_hits": day_tp_hits,
                "risk_percent": day_risk_percent,
                "max_lot": round(day_max_lot, 2),
            })

            print(
                f"DAY SUMMARY | {day} | "
                f"Open=${day_open_balance:.2f} | "
                f"Close=${day_close_balance:.2f} | "
                f"PnL=${day_profit:.2f} | "
                f"Trades={day_trade_count} | "
                f"TP={day_tp_hits} | "
                f"MaxLot={day_max_lot:.2f}"
            )

            if getattr(self, "stop_requested", False):
                print(" -> Stop requested, backtest halted.")
                break

    def _human_amount(self, n: float) -> str:
        n = float(n)
        abs_n = abs(n)

        if abs_n >= 1_000_000_000_000:
            return f"{n / 1_000_000_000_000:.2f} trillion"
        elif abs_n >= 1_000_000_000:
            return f"{n / 1_000_000_000:.2f} billion"
        elif abs_n >= 1_000_000:
            return f"{n / 1_000_000:.2f} million"
        elif abs_n >= 1_000:
            return f"{n / 1_000:.2f} thousand"
        else:
            return f"{n:.2f}"

    def _ensure_registry_file(self):
        os.makedirs(REGISTRY_DIR, exist_ok=True)
        if not os.path.exists(REGISTRY_FILE):
            with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
                json.dump({}, f, indent=2)

    def _load_live_registry(self) -> Dict:
        self._ensure_registry_file()
        try:
            with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print(f"  -> Registry load failed: {e}")
            return {}

    def _save_live_registry(self, data: Dict):
        self._ensure_registry_file()
        with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

    def _fmt_live_ts(self, x):
        if x is None:
            return ""
        try:
            return pd.to_datetime(x).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(x)

    def _live_signal_expiry_server(self, day):
        return datetime.combine(day, time(23, 50))

    def _make_signal_id_from_setup(self, pair: str, day, setup: dict) -> str:
        side = str(setup.get("side", "")).strip().upper()
        trigger = self._fmt_live_ts(setup.get("trigger_time")).replace(
            " ", "_").replace(":", "-")
        entry = round(float(setup.get("entry", 0.0)), 5)
        sl = round(float(setup.get("sl", 0.0)), 5)
        tp = round(float(setup.get("tp", 0.0)), 5)
        return f"{pair}_{day}_{side}_{trigger}_{entry:.5f}_{sl:.5f}_{tp:.5f}"

    def _mark_signal_completed_in_registry(self, signal_id: str, trade: Dict):
        reg = self._load_live_registry()
        if signal_id not in reg:
            reg[signal_id] = {"signal_id": signal_id}

        result = str(trade.get("result", "")).lower()
        reg[signal_id]["entry_hit"] = True
        reg[signal_id]["exit_result"] = result
        reg[signal_id]["entry_time"] = str(trade.get("entry_time", ""))
        reg[signal_id]["exit_time"] = str(trade.get("exit_time", ""))
        reg[signal_id]["registry_status"] = "COMPLETED"
        reg[signal_id]["completed"] = True
        reg[signal_id]["last_updated"] = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S")
        self._save_live_registry(reg)

    def _mark_signal_non_completed_in_registry(self, signal_id: str, status: str):
        reg = self._load_live_registry()
        if signal_id not in reg:
            reg[signal_id] = {"signal_id": signal_id}

        reg[signal_id]["registry_status"] = str(status).upper()
        reg[signal_id]["completed"] = False
        reg[signal_id]["last_updated"] = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S")
        self._save_live_registry(reg)

    def _is_signal_completed_in_registry(self, signal_id: str) -> bool:
        reg = self._load_live_registry()
        row = reg.get(signal_id, {})
        return bool(row.get("completed", False))

    def _is_same_completed_trade_prices(
        self,
        pair: str,
        day,
        side: str,
        entry: float,
        sl: float,
        tp: float,
        price_tol: float = 0.00005,
    ) -> bool:
        reg = self._load_live_registry()
        day_str = str(day)

        def _as_float(x, default=0.0):
            try:
                return float(x)
            except Exception:
                return default

        def _same_price(a, b):
            return abs(_as_float(a) - _as_float(b)) <= price_tol

        for _, row in reg.items():
            row_pair = str(row.get("pair", "")).strip()
            row_day = str(row.get("day", "")).strip()
            row_side = str(row.get("side", "")).strip().upper()
            row_completed = bool(row.get("completed", False))
            row_status = str(row.get("registry_status", "")).strip().upper()

            if row_pair != pair:
                continue
            if row_day != day_str:
                continue
            if row_side != side.upper():
                continue
            if not (row_completed or row_status == "COMPLETED"):
                continue

            row_entry = row.get("entry", 0.0)
            row_sl = row.get("sl", 0.0)
            row_tp = row.get("tp", 0.0)

            if _same_price(row_entry, entry) and _same_price(row_sl, sl) and _same_price(row_tp, tp):
                return True

        return False

    def _has_any_completed_trade_for_pair_day(self, pair: str, day) -> bool:
        reg = self._load_live_registry()
        day_str = str(day)

        for _, row in reg.items():
            row_pair = str(row.get("pair", "")).strip()
            row_day = str(row.get("day", "")).strip()
            row_completed = bool(row.get("completed", False))
            row_status = str(row.get("registry_status", "")).strip().upper()

            if row_pair != pair:
                continue
            if row_day != day_str:
                continue

            if row_completed or row_status == REGISTRY_STATUS_COMPLETED:
                return True

        return False

    def _has_active_registry_signal_for_pair_day_side(self, pair: str, day, side: str) -> bool:
        reg = self._load_live_registry()
        day_str = str(day)
        side = str(side).strip().upper()

        active_statuses = {
            "GENERATED",
            "NEW",
            "PLACED",
            "ENTRY_HIT",
            "BE_APPLIED",
            "LOCK10_APPLIED",
            "ACTIVE",
        }

        for _, row in reg.items():
            row_pair = str(row.get("pair", "")).strip()
            row_day = str(row.get("day", "")).strip()
            row_side = str(row.get("side", "")).strip().upper()
            row_completed = bool(row.get("completed", False))
            row_status = str(row.get("registry_status", "")).strip().upper()

            if row_pair != pair:
                continue
            if row_day != day_str:
                continue
            if row_side != side:
                continue
            if row_completed:
                continue

            if row_status in active_statuses:
                return True

        return False

    def _is_setup_in_hhll_disable_window(self, setup: dict) -> bool:
        if not setup:
            return False

        trigger_time = setup.get("trigger_time")
        if trigger_time is None:
            return False

        try:
            trigger_time = pd.to_datetime(trigger_time)
        except Exception:
            return False

        t = trigger_time.time()
        return self.hhll_disable_start_server <= t < self.hhll_disable_end_server

    def _parse_registry_ts(self, x):
        if x is None or str(x).strip() == "":
            return None
        try:
            return pd.to_datetime(x)
        except Exception:
            return None

    def _get_signal_expiry_from_row(self, row: Dict):
        day_str = str(row.get("day", "")).strip()
        if not day_str:
            return None
        try:
            day_dt = pd.to_datetime(day_str).date()
            return self._live_signal_expiry_server(day_dt)
        except Exception:
            return None

    def _scan_signal_outcome_from_df(self, df: pd.DataFrame, row: Dict):
        if df is None or df.empty:
            return None

        df = df.copy()
        df["time"] = pd.to_datetime(df["time"])
        df = df.sort_values("time").reset_index(drop=True)

        side = str(row.get("side", "")).upper().strip()
        entry = float(row.get("entry", 0.0))
        sl = float(row.get("sl", 0.0))
        tp = float(row.get("tp", 0.0))
        trigger_time = self._parse_registry_ts(row.get("trigger_time"))

        if trigger_time is None:
            return None

        expiry_time = self._get_signal_expiry_from_row(row)
        if expiry_time is None:
            expiry_time = df["time"].max()

        scan_df = df[df["time"] >= trigger_time].copy()
        if scan_df.empty:
            return None

        entry_hit = False
        entry_time = None

        for _, candle in scan_df.iterrows():
            t = candle["time"]
            high = float(candle["high"])
            low = float(candle["low"])

            if not entry_hit:
                if t > expiry_time:
                    return {
                        "kind": "noncompleted",
                        "status": REGISTRY_STATUS_ORDER_EXPIRED,
                    }

                if side == "B" and high >= entry:
                    entry_hit = True
                    entry_time = t
                elif side == "S" and low <= entry:
                    entry_hit = True
                    entry_time = t
                else:
                    continue

            if side == "B":
                tp_hit = high >= tp
                sl_hit = low <= sl
            else:
                tp_hit = low <= tp
                sl_hit = high >= sl

            if tp_hit and sl_hit:
                resolved = self._resolve_same_candle_exit_with_m1(
                    side=side,
                    entry_time=entry_time,
                    actual_entry=entry,
                    sl=sl,
                    tp=tp,
                )

                if resolved is not None:
                    resolved_result = str(resolved.get("result", "")).lower()
                    resolved_exit_time = resolved.get("exit_time")

                    if resolved_result == RESULT_TP:
                        return {
                            "kind": "completed",
                            "trade": {
                                "result": RESULT_TP,
                                "entry_time": entry_time,
                                "exit_time": resolved_exit_time,
                            },
                        }

                    if resolved_result == RESULT_SL:
                        return {
                            "kind": "completed",
                            "trade": {
                                "result": RESULT_SL,
                                "entry_time": entry_time,
                                "exit_time": resolved_exit_time,
                            },
                        }

                    if resolved_result == RESULT_SESSION_EXIT:
                        return {
                            "kind": "noncompleted",
                            "status": REGISTRY_STATUS_ORDER_EXPIRED
                            if t >= expiry_time
                            else REGISTRY_STATUS_ENTRY_HIT,
                        }

            if tp_hit:
                return {
                    "kind": "completed",
                    "trade": {
                        "result": RESULT_TP,
                        "entry_time": entry_time,
                        "exit_time": t,
                    },
                }

            if sl_hit:
                return {
                    "kind": "completed",
                    "trade": {
                        "result": RESULT_SL,
                        "entry_time": entry_time,
                        "exit_time": t,
                    },
                }

        if not entry_hit and df["time"].max() >= expiry_time:
            return {
                "kind": "noncompleted",
                "status": REGISTRY_STATUS_ORDER_EXPIRED,
            }

        if entry_hit:
            reg = self._load_live_registry()
            signal_id = str(row.get("signal_id", "")).strip()
            if signal_id in reg:
                reg[signal_id]["entry_hit"] = True
                reg[signal_id]["entry_time"] = str(entry_time)
                reg[signal_id]["registry_status"] = REGISTRY_STATUS_ENTRY_HIT
                reg[signal_id]["last_updated"] = datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S")
                self._save_live_registry(reg)

        return None

    def _reconcile_open_registry_signals_with_market_data(self, pair: str, df: pd.DataFrame):
        reg = self._load_live_registry()
        if not reg:
            return

        for signal_id, row in list(reg.items()):
            row_pair = str(row.get("pair", "")).strip()
            if row_pair != pair:
                continue

            status = str(row.get("registry_status", "GENERATED")
                         ).upper().strip()
            completed = bool(row.get("completed", False))

            if completed:
                continue

            if status in ("COMPLETED", "FAILED", "ORDEREXPIRED1930", "CANCELLEDNEWHHLL", "CANCELLED_NEW_HH_LL"):
                continue

            outcome = self._scan_signal_outcome_from_df(df=df, row=row)
            if not outcome:
                continue

            if outcome["kind"] == "completed":
                self._mark_signal_completed_in_registry(
                    signal_id, outcome["trade"])
                print(
                    f"  -> Registry completed: {signal_id} -> {outcome['trade']['result']}")
            elif outcome["kind"] == "noncompleted":
                self._mark_signal_non_completed_in_registry(
                    signal_id, outcome["status"])
                print(
                    f"  -> Registry non-completed: {signal_id} -> {outcome['status']}")

    def _is_same_live_payload(self, existing: Optional[Dict], payload: Dict) -> bool:
        if not existing or not payload:
            return False

        def _as_float(v, default=0.0):
            try:
                return float(v)
            except Exception:
                return default

        def _price_same(a, b, tol=0.00005):
            return abs(_as_float(a) - _as_float(b)) <= tol

        def _lot_same(a, b, tol=0.005):
            return abs(_as_float(a) - _as_float(b)) <= tol

        same_symbol = str(existing.get("symbol", "")).strip() == str(
            payload.get("symbol", "")).strip()
        same_side = str(existing.get("side", "")).strip().upper() == str(
            payload.get("side", "")).strip().upper()
        same_expiry = str(existing.get("expiry_server", "")).strip() == str(
            payload.get("expiry_server", "")).strip()

        same_entry = _price_same(existing.get(
            "entry", 0.0), payload.get("entry", 0.0))
        same_sl = _price_same(existing.get("sl", 0.0), payload.get("sl", 0.0))
        same_tp = _price_same(existing.get("tp", 0.0), payload.get("tp", 0.0))
        same_lot = _lot_same(existing.get("lot", 0.0), payload.get("lot", 0.0))
        same_mode = str(existing.get("entry_mode", "")).strip() == str(
            payload.get("entry_mode", "")).strip()

        return (
            same_symbol and
            same_side and
            same_expiry and
            same_entry and
            same_sl and
            same_tp and
            same_lot and
            same_mode
        )

    def _cancel_existing_signal_strict(
        self,
        pair: str,
        day,
        signal_file: str,
        existing: Optional[Dict],
        max_spread_points: int,
        max_slippage_points: int,
        reason: str = "CANCELLEDNEWHHLL",
    ):
        """
        STRICT DELETE: cancel payload + registry mark.
        Filled/managed trades untouched.
        CANCEL file ko delete nahi karte, taaki EA usko read kar sake.
        """
        if existing is None:
            # No payload, just delete file if present (pure stale)
            try:
                if os.path.exists(signal_file):
                    os.remove(signal_file)
                    print(
                        f"  -> Deleted file (no existing payload): {signal_file}")
            except Exception as e:
                print(f"  -> Failed deleting file {signal_file}: {e}")
            return

        existing_status = str(existing.get("status", "")).upper()
        if existing_status in TERMINAL_FILLED_STATUSES:
            print(
                f"  -> Existing filled status {existing_status}, skip strict cancel: {signal_file}"
            )
            return

        old_signal_id = str(existing.get("signal_id", "")).strip()

        # 1) Write CANCEL payload so EA sees proper cancel once
        cancel_payload = self._build_live_cancel_payload(
            pair=pair,
            day=day,
            max_spread_points=max_spread_points,
            max_slippage_points=max_slippage_points,
        )
        self._write_live_signal_file(signal_file, cancel_payload)

        # 2) Registry -> mark as non-completed / cancelled
        if old_signal_id:
            self._mark_signal_non_completed_in_registry(old_signal_id, reason)

        # 3) CANCEL file ko intentionally rakho, delete mat karo
        print(f"  -> Strict cancel file kept for EA processing: {signal_file}")

    def _write_fresh_signal_after_strict_delete(
        self,
        pair: str,
        day,
        signal_file: str,
        setup: dict,
        existing: Optional[Dict],
        existing_status: str,
        max_spread_points: int,
        max_slippage_points: int,
        reason: str = "CANCELLEDNEWHHLL",
    ):
        print(f"\n[FRESH DBG] ENTER pair={pair} file={signal_file}")
        print(f"[FRESH DBG] day={day}")
        print(f"[FRESH DBG] existing_status(raw)={existing_status}")
        print(f"[FRESH DBG] existing={existing}")
        print(f"[FRESH DBG] setup={setup}")

        existing_status = str(existing_status or "").upper()
        setup_side = str(setup.get("side", "")).upper().strip()

        print(f"[FRESH DBG] existing_status(norm)={existing_status}")
        print(f"[FRESH DBG] setup_side={setup_side}")

        # 0) Pair/day/side completed lock (NO NEW HELPER)
        reg = self._load_live_registry()
        day_str = str(day)
        same_side_completed = False

        for _, row in reg.items():
            row_pair = str(row.get("pair", "")).strip()
            row_day = str(row.get("day", "")).strip()
            row_side = str(row.get("side", "")).strip().upper()
            row_completed = bool(row.get("completed", False))
            row_status = str(row.get("registry_status", "")).strip().upper()

            if row_pair != pair:
                continue
            if row_day != day_str:
                continue
            if row_side != setup_side:
                continue

            if row_completed or row_status == "COMPLETED":
                same_side_completed = True
                break

        if same_side_completed:
            print(
                f"[FRESH DBG] pair/day/side completed lock hit -> skip {pair} {day} side={setup_side}"
            )
            return None

        # 0.5) Side-wise active registry guard
        has_active_same_side = self._has_active_registry_signal_for_pair_day_side(
            pair, day, setup_side)
        print(
            f"[FRESH DBG] has_active_registry_signal_for_pair_day_side={has_active_same_side}")

        if has_active_same_side:
            same_existing_side = (
                str(existing.get("side", "")).upper().strip() if existing else ""
            )
            same_existing_status = (
                str(existing.get("status", "")).upper(
                ).strip() if existing else ""
            )

            print(f"[FRESH DBG] same_existing_side={same_existing_side}")
            print(f"[FRESH DBG] same_existing_status={same_existing_status}")
            print(f"[FRESH DBG] ACTIVE_FILE_STATUSES={ACTIVE_FILE_STATUSES}")

            if (
                not existing
                or same_existing_side != setup_side
                or same_existing_status not in ACTIVE_FILE_STATUSES
            ):
                print(
                    f"[FRESH DBG] active registry guard blocked fresh write for {pair} {day} side={setup_side}"
                )
                return None

        # 1) Registry completed filters (exact ID + same prices)
        signal_id = self._make_signal_id_from_setup(pair, day, setup)
        print(f"[FRESH DBG] signal_id={signal_id}")

        already_completed_exact = self._is_signal_completed_in_registry(
            signal_id)
        already_completed_same_prices = self._is_same_completed_trade_prices(
            pair=pair,
            day=day,
            side=setup_side,
            entry=float(setup.get("entry", 0.0)),
            sl=float(setup.get("sl", 0.0)),
            tp=float(setup.get("tp", 0.0)),
        )

        print(f"[FRESH DBG] already_completed_exact={already_completed_exact}")
        print(
            f"[FRESH DBG] already_completed_same_prices={already_completed_same_prices}")

        if already_completed_exact or already_completed_same_prices:
            print(
                f"[FRESH DBG] setup already completed in registry -> skip fresh write: {signal_id}"
            )
            return None

        # 2) Existing file terminal-filled guard
        print(
            f"[FRESH DBG] TERMINAL_FILLED_STATUSES={TERMINAL_FILLED_STATUSES}")
        if existing_status in TERMINAL_FILLED_STATUSES:
            print("[FRESH DBG] existing trade already filled/managed -> no overwrite")
            return None

        # 3) Build PLACE payload (registry inside)
        payload = self._build_live_place_payload(
            pair=pair,
            day=day,
            setup=setup,
            action="PLACE",
            max_spread_points=max_spread_points,
            max_slippage_points=max_slippage_points,
        )

        print(f"[FRESH DBG] payload from _build_live_place_payload={payload}")

        if payload is None:
            print("[FRESH DBG] payload is None -> no live file write")
            return None

        # 4) If existing ACTIVE file + same payload, no rewrite
        if existing is not None and existing_status in ACTIVE_FILE_STATUSES:
            same_payload = self._is_same_live_payload(existing, payload)
            print(f"[FRESH DBG] existing active file detected")
            print(f"[FRESH DBG] existing_status in ACTIVE_FILE_STATUSES -> True")
            print(f"[FRESH DBG] same_payload={same_payload}")
            print(f"[FRESH DBG] existing payload={existing}")
            print(f"[FRESH DBG] new payload={payload}")

            if same_payload:
                print(
                    "[FRESH DBG] chosen setup unchanged (prices/lot/mode) -> no rewrite")
                return payload

            # 5) Material change -> strict cancel + rewrite
            print("[FRESH DBG] chosen setup changed materially -> STRICT DELETE flow")
            self._cancel_existing_signal_strict(
                pair=pair,
                day=day,
                signal_file=signal_file,
                existing=existing,
                max_spread_points=max_spread_points,
                max_slippage_points=max_slippage_points,
                reason=reason,
            )
            print("[FRESH DBG] strict cancel completed")

        else:
            print(
                "[FRESH DBG] no active existing file branch, proceeding to final write")

        # 6) Final write
        print(f"[FRESH DBG] calling _write_live_signal_file for {signal_file}")
        self._write_live_signal_file(signal_file, payload)
        print(f"[FRESH DBG] final write done for {signal_file}")

        return payload

    def _build_live_cancel_payload(self, pair: str, day, max_spread_points=25, max_slippage_points=15):
        return {
            "action": "CANCEL",
            "signal_id": f"{pair}_{day}_CANCEL",
            "symbol": pair,
            "side": "",
            "expiry_server": self._live_signal_expiry_server(day).strftime("%Y-%m-%d %H:%M:%S"),
            "entry": 0.0,
            "sl": 0.0,
            "tp": 0.0,
            "lot": 0.0,
            "entry_mode": "",
            "atr": 0.0,
            "trigger_time": "",
            "picked_candle_time": "",
            "breakout_candle_time": "",
            "status": "NEW",
            "max_spread_points": int(max_spread_points),
            "max_slippage_points": int(max_slippage_points),
        }

    def _build_live_place_payload(
        self,
        pair: str,
        day,
        setup: dict,
        action: str = "PLACE",
        max_spread_points=25,
        max_slippage_points=15,
    ):
        trigger_time = self._fmt_live_ts(setup.get("trigger_time"))
        picked_candle_time = self._fmt_live_ts(setup.get("picked_candle_time"))
        breakout_candle_time = self._fmt_live_ts(
            setup.get("breakout_candle_time"))

        entry = round(float(setup["entry"]), 5)
        sl = round(float(setup["sl"]), 5)
        tp = round(float(setup["tp"]), 5)
        atr = round(float(setup.get("atr", 0.0)), 5)
        lot = round(float(setup["lot_size"]), 2)
        side = str(setup.get("side", "")).upper().strip()

        signal_id = self._make_signal_id_from_setup(pair, day, setup)

        already_completed_exact = self._is_signal_completed_in_registry(
            signal_id)
        already_completed_same_prices = self._is_same_completed_trade_prices(
            pair=pair,
            day=day,
            side=side,
            entry=entry,
            sl=sl,
            tp=tp,
        )

        if already_completed_exact or already_completed_same_prices:
            print(
                f" -> Registry says completed, skip payload build: {signal_id}")
            return None

        reg = self._load_live_registry()
        row = reg.get(signal_id, {})

        row_completed = bool(row.get("completed", False))
        row_status = str(row.get("registry_status", "")).upper().strip()

        if row_completed or row_status == "COMPLETED":
            print(
                f" -> Existing registry row already completed, skip payload build: {signal_id}")
            return None

        reg[signal_id] = {
            "signal_id": signal_id,
            "pair": pair,
            "day": str(day),
            "side": side,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "atr": atr,
            "completed": False,
            "trigger_time": trigger_time,
            "picked_candle_time": picked_candle_time,
            "breakout_candle_time": breakout_candle_time,
            "registry_status": row_status if row_status and row_status != "COMPLETED" else "GENERATED",
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        self._save_live_registry(reg)
        print(f" -> Registry signal added/updated: {signal_id}")

        return {
            "action": action,
            "signal_id": signal_id,
            "symbol": pair,
            "side": side,
            "expiry_server": self._live_signal_expiry_server(day).strftime("%Y-%m-%d %H:%M:%S"),
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "lot": lot,
            "entry_mode": str(setup.get("entry_mode", "")),
            "atr": atr,
            "trigger_time": trigger_time,
            "picked_candle_time": picked_candle_time,
            "breakout_candle_time": breakout_candle_time,
            "status": "NEW",
            "max_spread_points": int(max_spread_points),
            "max_slippage_points": int(max_slippage_points),
        }

    def _live_payload_to_line(self, payload: dict) -> str:
        return "|".join([
            str(payload.get("action", "")),
            str(payload.get("signal_id", "")),
            str(payload.get("symbol", "")),
            str(payload.get("side", "")),
            str(payload.get("expiry_server", "")),
            f"{float(payload.get('entry', 0.0)):.5f}",
            f"{float(payload.get('sl', 0.0)):.5f}",
            f"{float(payload.get('tp', 0.0)):.5f}",
            f"{float(payload.get('lot', 0.0)):.2f}",
            str(payload.get("entry_mode", "")),
            f"{float(payload.get('atr', 0.0)):.5f}",
            str(payload.get("trigger_time", "")),
            str(payload.get("picked_candle_time", "")),
            str(payload.get("breakout_candle_time", "")),
            str(payload.get("status", "NEW")),
            str(int(payload.get("max_spread_points", 25))),
            str(int(payload.get("max_slippage_points", 15))),
        ])

    def _read_existing_live_signal(self, signal_file: str):
        if not os.path.exists(signal_file):
            return None, None

        try:
            with open(signal_file, "r", encoding="utf-8") as f:
                line = f.read().strip()
        except Exception:
            return None, None

        if not line:
            return None, None

        parts = line.split("|")

        # Extended format: 23 fields
        # PLACE|signal_id|day|side|trigger_time|entry|sl|tp|symbol|side|expiry|entry|sl|tp|lot|entry_mode|atr|trigger|picked|breakout|status|spread|slip
        if len(parts) >= 23:
            payload = {
                "action": parts[0],
                "signal_id": parts[1],
                "symbol": parts[8],
                "side": parts[9],
                "expiry_server": parts[10],
                "entry": parts[11],
                "sl": parts[12],
                "tp": parts[13],
                "lot": parts[14],
                "entry_mode": parts[15],
                "atr": parts[16],
                "trigger_time": parts[17],
                "picked_candle_time": parts[18],
                "breakout_candle_time": parts[19],
                "status": parts[20],
                "max_spread_points": parts[21],
                "max_slippage_points": parts[22],
            }
            return line, payload

        # New compact ATR format: 17 fields
        if len(parts) >= 17:
            payload = {
                "action": parts[0],
                "signal_id": parts[1],
                "symbol": parts[2],
                "side": parts[3],
                "expiry_server": parts[4],
                "entry": parts[5],
                "sl": parts[6],
                "tp": parts[7],
                "lot": parts[8],
                "entry_mode": parts[9],
                "atr": parts[10],
                "trigger_time": parts[11],
                "picked_candle_time": parts[12],
                "breakout_candle_time": parts[13],
                "status": parts[14],
                "max_spread_points": parts[15],
                "max_slippage_points": parts[16],
            }
            return line, payload

        # Old compact format: 16 fields
        if len(parts) >= 16:
            payload = {
                "action": parts[0],
                "signal_id": parts[1],
                "symbol": parts[2],
                "side": parts[3],
                "expiry_server": parts[4],
                "entry": parts[5],
                "sl": parts[6],
                "tp": parts[7],
                "lot": parts[8],
                "entry_mode": parts[9],
                "atr": "0.00000",
                "trigger_time": parts[10],
                "picked_candle_time": parts[11],
                "breakout_candle_time": parts[12],
                "status": parts[13],
                "max_spread_points": parts[14],
                "max_slippage_points": parts[15],
            }
            return line, payload

        # Very old format: 14 fields
        if len(parts) >= 14:
            payload = {
                "action": parts[0],
                "signal_id": parts[1],
                "symbol": parts[2],
                "side": parts[3],
                "expiry_server": parts[4],
                "entry": parts[5],
                "sl": parts[6],
                "tp": parts[7],
                "lot": parts[8],
                "entry_mode": parts[9],
                "atr": "0.00000",
                "trigger_time": parts[10],
                "picked_candle_time": "",
                "breakout_candle_time": "",
                "status": parts[11],
                "max_spread_points": parts[12],
                "max_slippage_points": parts[13],
            }
            return line, payload

        return line, None

    def _write_live_signal_file(self, signal_file: str, payload: dict):
        print(f"\n[WRITE DBG] ENTER _write_live_signal_file")
        print(f"[WRITE DBG] signal_file = {signal_file}")
        print(f"[WRITE DBG] abs_path = {os.path.abspath(signal_file)}")
        print(f"[WRITE DBG] cwd = {os.getcwd()}")
        print(f"[WRITE DBG] payload = {payload}")

        new_line = self._live_payload_to_line(payload)
        print(f"[WRITE DBG] new_line = {new_line}")

        old_line, existing = self._read_existing_live_signal(signal_file)
        print(f"[WRITE DBG] old_line = {old_line}")
        print(f"[WRITE DBG] existing = {existing}")

        if old_line == new_line:
            print(f"[WRITE DBG] unchanged, skip write: {signal_file}")
            return False

        if existing is not None and self._is_same_live_payload(existing, payload):
            print(
                f"[WRITE DBG] same payload, normalizing file format: {signal_file}")
        else:
            print(f"[WRITE DBG] file updated: {signal_file}")

        os.makedirs(os.path.dirname(signal_file), exist_ok=True)
        with open(signal_file, "w", encoding="utf-8") as f:
            f.write(new_line)

        # verify immediately from disk
        with open(signal_file, "r", encoding="utf-8") as f:
            verify_line = f.read().strip()

        print(f"[WRITE DBG] FINAL WRITTEN LINE = {new_line}")
        print(f"[WRITE DBG] verify_after_write = {verify_line}")
        return True

    def _choose_live_setup_for_day(self, day_df: pd.DataFrame, fund: float, risk_percent: float):
        high_setup = self._build_high_setup_for_day(day_df, fund, risk_percent)
        low_setup = self._build_low_setup_for_day(day_df, fund, risk_percent)

        candidates = []
        if high_setup:
            candidates.append(high_setup)
        if low_setup:
            candidates.append(low_setup)

        if not candidates:
            return None

        candidates.sort(key=lambda s: s["trigger_time"])
        return candidates[-1]

    def generate_live_dual_signals_for_latest_day(
        self,
        pair: str,
        df_15m: pd.DataFrame,
        signal_file: str = None,
        signal_dir: str = None,
        max_spread_points: int = 25,
        max_slippage_points: int = 15,
    ):
        self.pair = pair

        df = df_15m.copy()
        df["time"] = pd.to_datetime(df["datetime"])
        df = df.sort_values("time").reset_index(drop=True)

        if "atr" not in df.columns:
            df = self._add_atr_column(df)

        # IMPORTANT: refresh registry against latest market data for this pair
        self._reconcile_open_registry_signals_with_market_data(
            pair=pair, df=df)

        day = df["time"].dt.date.max()
        day_df = df[df["time"].dt.date == day].copy()

        print(f"\n[HL Live] {pair} latest day = {day}")
        print(f"  -> Rows in day_df: {len(day_df)}")

        if signal_dir is None:
            if signal_file is not None:
                signal_dir = os.path.dirname(signal_file)
            else:
                raise ValueError("signal_dir or signal_file required")

        buy_file = os.path.join(signal_dir, f"live_signal_{pair}_BUY.txt")
        sell_file = os.path.join(signal_dir, f"live_signal_{pair}_SELL.txt")

        old_buy_line, existing_buy = self._read_existing_live_signal(buy_file)
        old_sell_line, existing_sell = self._read_existing_live_signal(
            sell_file)

        def _is_existing_from_old_day(existing_payload, current_day):
            if existing_payload is None:
                return False

            existing_signal_id = str(
                existing_payload.get("signal_id", "")).strip()
            existing_expiry = str(existing_payload.get(
                "expiry_server", "")).strip()
            day_str = str(current_day)

            return day_str not in existing_signal_id and day_str not in existing_expiry

        # stale old-day BUY file/payload ignore
        if _is_existing_from_old_day(existing_buy, day):
            print(
                f"  -> Existing BUY file belongs to old day, treating as stale: {buy_file}")
            existing_buy = None

        # stale old-day SELL file/payload ignore
        if _is_existing_from_old_day(existing_sell, day):
            print(
                f"  -> Existing SELL file belongs to old day, treating as stale: {sell_file}")
            existing_sell = None

        existing_buy_status = str(existing_buy.get(
            "status", "")).upper() if existing_buy else ""
        existing_sell_status = str(existing_sell.get(
            "status", "")).upper() if existing_sell else ""

        if day_df.empty or not self._validate_day(day_df):
            print("  -> Day invalid")

            if existing_buy_status not in TERMINAL_FILLED_STATUSES:
                self._cancel_existing_signal_strict(
                    pair=pair,
                    day=day,
                    signal_file=buy_file,
                    existing=existing_buy,
                    max_spread_points=max_spread_points,
                    max_slippage_points=max_slippage_points,
                    reason="CANCELLEDEOD",
                )

            if existing_sell_status not in TERMINAL_FILLED_STATUSES:
                self._cancel_existing_signal_strict(
                    pair=pair,
                    day=day,
                    signal_file=sell_file,
                    existing=existing_sell,
                    max_spread_points=max_spread_points,
                    max_slippage_points=max_slippage_points,
                    reason="CANCELLEDEOD",
                )

            return {"buy": None, "sell": None}

        fund = self.current_fund
        risk_percent = self.base_risk_percent

        high_setup = self._build_high_setup_for_day(day_df, fund, risk_percent)
        low_setup = self._build_low_setup_for_day(day_df, fund, risk_percent)

        buy_setup = low_setup
        sell_setup = high_setup

        reg = self._load_live_registry()
        day_str = str(day)

        buy_completed = False
        sell_completed = False

        for _, row in reg.items():
            row_pair = str(row.get("pair", "")).strip()
            row_day = str(row.get("day", "")).strip()
            row_side = str(row.get("side", "")).strip().upper()
            row_completed = bool(row.get("completed", False))
            row_status = str(row.get("registry_status", "")).strip().upper()

            if row_pair != pair or row_day != day_str:
                continue

            if row_side == "B" and (row_completed or row_status == "COMPLETED"):
                buy_completed = True

            if row_side == "S" and (row_completed or row_status == "COMPLETED"):
                sell_completed = True

        if buy_completed:
            print(
                f"  -> {pair} {day} BUY already completed, suppress BUY export")
            buy_setup = None

        if sell_completed:
            print(
                f"  -> {pair} {day} SELL already completed, suppress SELL export")
            sell_setup = None

        buy_payload = None
        sell_payload = None

        if buy_setup:
            print(f"[GEN DBG] {pair} BUY setup = {buy_setup}")
            buy_payload = self._write_fresh_signal_after_strict_delete(
                pair=pair,
                day=day,
                signal_file=buy_file,
                setup=buy_setup,
                existing=existing_buy,
                existing_status=existing_buy_status,
                max_spread_points=max_spread_points,
                max_slippage_points=max_slippage_points,
                reason="CANCELLEDNEWHHLL",
            )
            print(f"[GEN DBG] {pair} BUY payload returned = {buy_payload}")
        else:
            print(f"  -> {pair} BUY: no setup")
            if existing_buy_status not in TERMINAL_FILLED_STATUSES:
                self._cancel_existing_signal_strict(
                    pair=pair,
                    day=day,
                    signal_file=buy_file,
                    existing=existing_buy,
                    max_spread_points=max_spread_points,
                    max_slippage_points=max_slippage_points,
                    reason="CANCELLEDNEWHHLL",
                )

        if sell_setup:
            print(f"[GEN DBG] {pair} SELL setup = {sell_setup}")
            sell_payload = self._write_fresh_signal_after_strict_delete(
                pair=pair,
                day=day,
                signal_file=sell_file,
                setup=sell_setup,
                existing=existing_sell,
                existing_status=existing_sell_status,
                max_spread_points=max_spread_points,
                max_slippage_points=max_slippage_points,
                reason="CANCELLEDNEWHHLL",
            )
            print(f"[GEN DBG] {pair} SELL payload returned = {sell_payload}")
        else:
            print(f"  -> {pair} SELL: no setup")
            if existing_sell_status not in TERMINAL_FILLED_STATUSES:
                self._cancel_existing_signal_strict(
                    pair=pair,
                    day=day,
                    signal_file=sell_file,
                    existing=existing_sell,
                    max_spread_points=max_spread_points,
                    max_slippage_points=max_slippage_points,
                    reason="CANCELLEDNEWHHLL",
                )

        return {"buy": buy_payload, "sell": sell_payload}

    def export_to_excel(self, output_path: str) -> None:
        folder = "backtests"
        os.makedirs(folder, exist_ok=True)
        full_path = os.path.join(folder, os.path.basename(output_path))

        total_trades = len(self.trades)
        net_pnl = self.current_fund - self.initial_fund

        # Result counts
        wins = sum(1 for t in self.trades if t["result"] == "tp")
        sl_lock10 = sum(1 for t in self.trades if t["result"] == "sl_lock10")
        losses = sum(1 for t in self.trades if t["result"] == "sl")
        expired = sum(
            1 for t in self.trades
            if str(t.get("result", "")).lower() == RESULT_ORDER_EXPIRED
        )
        others = total_trades - (wins + sl_lock10 + losses + expired)

        summary = {
            "Metric": [
                "Initial Fund",
                "Final Fund",
                "Final Fund (words)",
                "Net PNL",
                "Total Records",
                "Win Rate (TP only)",
                "Max Drawdown",
                "Total Trades",
                "Wins (TP)",
                "SL Lock10 Hits",
                "Losses (SL)",
                "Expired Orders",
                "Other Results",
            ],
            "Value": [
                self.initial_fund,
                self.current_fund,
                self._human_amount(self.current_fund),
                net_pnl,
                total_trades,
                f"{self.win_rate:.2f}%" if total_trades > 0 else "N/A",
                self.max_drawdown,
                total_trades,
                wins,
                sl_lock10,
                losses,
                expired,
                others,
            ],
        }

        with pd.ExcelWriter(full_path, engine="openpyxl") as writer:
            if self.trades:
                trades_df = pd.DataFrame(self.trades)

                # Ensure columns exist for backward compatibility
                if "entry_mode" not in trades_df.columns:
                    trades_df["entry_mode"] = ""

                if "sl_mode" not in trades_df.columns:
                    trades_df["sl_mode"] = "NORMAL"

                # Entry From column from entry_mode
                trades_df["Entry From"] = trades_df["entry_mode"].apply(
                    lambda x: "T1" if isinstance(x, str) and x.endswith("_T1")
                    else ("AT" if isinstance(x, str) and x.endswith("_AT") else "")
                )

                # Put "Entry From" right after entry_price
                if "entry_price" in trades_df.columns:
                    insert_pos = trades_df.columns.get_loc("entry_price") + 1
                    col = trades_df.pop("Entry From")
                    trades_df.insert(insert_pos, "Entry From", col)

                # Optional: make sl_mode column display-friendly
                trades_df["SL Mode"] = trades_df["sl_mode"].apply(
                    lambda x: "SL_LOCK10" if x == "LOCK10_TP80" else "NORMAL"
                )

                # Optional column ordering for readability
                preferred_order = [
                    "date",
                    "pair",
                    "side",
                    "entry_time",
                    "entry_price",
                    "Entry From",
                    "sl",
                    "tp",
                    "exit_time",
                    "exit_price",
                    "result",
                    "SL Mode",
                    "pnl_pips",
                    "pnl_amount",
                    "fund_after",
                    "max_adverse_pips",
                    "max_adverse_amount",
                    "balance_before_trade",
                    "min_available_balance_during_trade",
                    "entry_mode",
                    "sl_mode",
                ]

                existing_cols = [
                    c for c in preferred_order if c in trades_df.columns]
                remaining_cols = [
                    c for c in trades_df.columns if c not in existing_cols]
                trades_df = trades_df[existing_cols + remaining_cols]

                trades_df.to_excel(writer, sheet_name="Trades", index=False)

            summary_df = pd.DataFrame(summary)
            summary_df.to_excel(writer, sheet_name="Summary", index=False)

            if hasattr(self, "daily_briefings") and self.daily_briefings:
                daily_df = pd.DataFrame(self.daily_briefings)

                expected_cols = [
                    "date",
                    "open_balance",
                    "risk_percent",
                    "no_trades",
                    "tp_hits",
                    "max_lot",
                    "profit",
                    "final_balance",
                ]

                for col in expected_cols:
                    if col not in daily_df.columns:
                        if col == "max_lot":
                            daily_df[col] = 0.0
                        else:
                            daily_df[col] = None

                daily_df = daily_df[expected_cols]

                daily_df.columns = [
                    "Date",
                    "Open Balance",
                    "Risk %",
                    "No Trades",
                    "TP Hits",
                    "Max Lot",
                    "Profit",
                    "Final Balance",
                ]

                daily_df.to_excel(
                    writer, sheet_name="Day Wise Briefing", index=False
                )

        print(f"\nBacktest results exported to: {full_path}")
