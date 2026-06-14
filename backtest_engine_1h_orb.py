# backtest_engine_1h_orb.py
from strategy_calculator import StrategyCalculator
from gann_fetcher import GannFetcher
from live_fund_manager import get_live_usable_fund
from backtest_orb_runner_live_style import process_pair_day_live_style
from live_data_mt5 import fetch_live_1m
from mt5_atr_bridge import fetch_mt5_h1_m15_atr
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
    build_setup_for_day,
    invalidate_pending_setup_on_new_pivot,
)
from backtest_orb_trade_simulator import (
    resolve_same_candle_exit_with_m1,
    fetch_m1_data_for_window,
    compute_m1_mae_after_entry,
    simulate_trade,
)
from live_registry_manager import (
    ensure_registry_file,
    load_live_registry,
    save_live_registry,
    fmt_live_ts,
    live_signal_expiry_server,
    make_signal_id_from_setup,
    mark_signal_completed_in_registry,
    mark_signal_non_completed_in_registry,
    is_signal_completed_in_registry,
    is_same_completed_trade_prices,
    has_any_completed_trade_for_pair_day,
    has_active_registry_signal_for_pair_day_side,
    get_active_registry_signal_for_pair_day_side,
    is_same_setup_signature,
    is_newer_setup_than_row,
    is_setup_in_hhll_disable_window,
    parse_registry_ts,
    get_signal_expiry_from_row,
    scan_signal_outcome_from_df,
    reconcile_open_registry_signals_with_market_data,
)

from live_signal_file_manager import (
    ACTIVE_FILE_STATUSES,
    is_same_live_payload,
    build_live_cancel_payload,
    build_live_place_payload,
    live_payload_to_line,
    read_existing_live_signal,
    write_live_signal_file,
    cancel_existing_signal_strict,
    write_fresh_signal_after_strict_delete,
)
from live_signal_orchestrator import (
    is_existing_from_old_day,
    choose_live_setup_for_day,
    generate_live_dual_signals_for_latest_day,
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
        self.hhll_disable_start_server = time(22, 00)
        self.hhll_disable_end_server = time(23, 45)

        # 🔹 Local Gann lookup load (JSON)
        self.gann_lookup = self._load_gann_lookup("forex_gann_lookup_1_3.json")

        # 🔹 OLD fixed-lot logic ko effectively disable kar do
        # Saara lot sizing ab StrategyCalculator.calculate_lot_size ke through hoga
        self.max_backtest_lot = None
        self.fixed_lot_mode = False
        self.fixed_lot_value = None

        self.h1_atr_df = pd.DataFrame()

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
        JSON: { "1.23456": { ... } }

        Ab hum teen cheezein rakh rahe hain:
        1) prices -> sorted float list (nearest fallback ke liye)
        2) levels -> unhi prices ke level dicts
        3) exact_map -> "0.8067" jaisi string key -> level dict
        """
        try:
            with open(path, "r") as f:
                data = json.load(f)

            exact_map: Dict[str, Dict] = {}
            items = []

            for k, v in data.items():
                fk = float(k)
                bucket = f"{fk:.4f}"
                exact_map[bucket] = v
                items.append((fk, v))

            items.sort(key=lambda x: x[0])
            prices = [p for p, _ in items]
            levels = [lv for _, lv in items]

            print(f" -> Loaded {len(prices)} Gann lookup keys from {path}")
            return {
                "prices": prices,
                "levels": levels,
                "exact_map": exact_map,
            }
        except Exception as e:
            print(f" -> Gann lookup load failed: {e}")
            return {
                "prices": [],
                "levels": [],
                "exact_map": {},
            }

    def _get_gann_from_lookup(self, price: float) -> Optional[Dict]:
        prices = self.gann_lookup.get("prices", [])
        levels = self.gann_lookup.get("levels", [])
        exact_map = self.gann_lookup.get("exact_map", {})

        if not prices:
            return None

        raw_lookup_input = float(price)

        bucket = f"{raw_lookup_input:.4f}"
        if bucket in exact_map:
            lv = exact_map[bucket]
            matched_price = float(bucket)

            buy_t1 = lv.get("buy_t1") or lv.get("buyT1")
            buy_t2 = lv.get("buy_t2") or lv.get("buyT2")
            sell_t1 = lv.get("sell_t1") or lv.get("sellT1")
            sell_t2 = lv.get("sell_t2") or lv.get("sellT2")

            if buy_t1 is None or buy_t2 is None or sell_t1 is None or sell_t2 is None:
                return None

            return {
                "raw_lookup_input": raw_lookup_input,
                "matched_price": matched_price,
                "buy_at": lv["buy_at"],
                "buy_targets": [buy_t1, buy_t2],
                "sell_at": lv["sell_at"],
                "sell_targets": [sell_t1, sell_t2],
                "buy_t1": buy_t1,
                "buy_t2": buy_t2,
                "sell_t1": sell_t1,
                "sell_t2": sell_t2,
                "buy_sl": lv.get("buy_sl"),
                "sell_sl": lv.get("sell_sl"),
                "middle": lv.get("middle"),
                "buy_super_middle": lv.get("buy_super_middle"),
                "sell_super_middle": lv.get("sell_super_middle"),
            }

        import bisect

        pos = bisect.bisect_left(prices, raw_lookup_input)
        if pos == 0:
            idx = 0
        elif pos == len(prices):
            idx = len(prices) - 1
        else:
            before = prices[pos - 1]
            after = prices[pos]
            idx = pos - 1 if abs(raw_lookup_input -
                                 before) <= abs(raw_lookup_input - after) else pos

        lv = levels[idx]
        matched_price = prices[idx]

        buy_t1 = lv.get("buy_t1") or lv.get("buyT1")
        buy_t2 = lv.get("buy_t2") or lv.get("buyT2")
        sell_t1 = lv.get("sell_t1") or lv.get("sellT1")
        sell_t2 = lv.get("sell_t2") or lv.get("sellT2")

        if buy_t1 is None or buy_t2 is None or sell_t1 is None or sell_t2 is None:
            return None

        return {
            "raw_lookup_input": raw_lookup_input,
            "matched_price": matched_price,
            "buy_at": lv["buy_at"],
            "buy_targets": [buy_t1, buy_t2],
            "sell_at": lv["sell_at"],
            "sell_targets": [sell_t1, sell_t2],
            "buy_t1": buy_t1,
            "buy_t2": buy_t2,
            "sell_t1": sell_t1,
            "sell_t2": sell_t2,
            "buy_sl": lv.get("buy_sl"),
            "sell_sl": lv.get("sell_sl"),
            "middle": lv.get("middle"),
            "buy_super_middle": lv.get("buy_super_middle"),
            "sell_super_middle": lv.get("sell_super_middle"),
        }
    # ------------------ DAY VALIDATION (basic) ------------------

    def _check_atr_buffer_entry(
        self,
        entry_price: float,
        side: str,
        atr: float,
        high: float,
        low: float,
        is_new_orb_shifted: bool = False,
    ):
        """
        SIMPLE ENTRY TOUCH:

        BUY:
            candle high >= entry_price  -> fill
        SELL:
            candle low <= entry_price   -> fill

        ATR ab sirf info/debug ke liye rahega; trigger pe koi effect nahi.
        """
        try:
            entry_price = float(entry_price)
            high = float(high)
            low = float(low)
        except Exception:
            return None

        side = str(side).upper().strip()

        if side in ("BUY", "B"):
            if high >= entry_price:
                return entry_price
            return None

        if side in ("SELL", "S"):
            if low <= entry_price:
                return entry_price
            return None

        return None

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

    def _price_match_tol(self) -> float:
        return 0.00025

    def _find_mt5_pending_order_for_setup(self, pair: str, setup: Dict):
        try:
            import MetaTrader5 as mt5

            orders = mt5.orders_get(symbol=pair)
            if orders is None:
                return None

            side = str(setup.get("side", "")).upper().strip()
            entry = float(setup.get("entry", 0.0))
            sl = float(setup.get("sl", 0.0))
            tp = float(setup.get("tp", 0.0))
            tol = self._price_match_tol()

            buy_types = {
                mt5.ORDER_TYPE_BUY_LIMIT,
                mt5.ORDER_TYPE_BUY_STOP,
                mt5.ORDER_TYPE_BUY_STOP_LIMIT,
            }
            sell_types = {
                mt5.ORDER_TYPE_SELL_LIMIT,
                mt5.ORDER_TYPE_SELL_STOP,
                mt5.ORDER_TYPE_SELL_STOP_LIMIT,
            }
            allowed_types = buy_types if side in {"B", "BUY"} else sell_types

            for o in orders:
                if getattr(o, "symbol", "") != pair:
                    continue
                if getattr(o, "type", None) not in allowed_types:
                    continue

                o_price = float(getattr(o, "price_open", 0.0) or 0.0)
                o_sl = float(getattr(o, "sl", 0.0) or 0.0)
                o_tp = float(getattr(o, "tp", 0.0) or 0.0)

                if (
                    abs(o_price - entry) <= tol
                    and abs(o_sl - sl) <= tol
                    and abs(o_tp - tp) <= tol
                ):
                    return o

            return None

        except Exception as e:
            print(f"  -> _find_mt5_pending_order_for_setup failed: {e}")
            return None

    def _find_mt5_open_position_for_setup(self, pair: str, setup: Dict):
        try:
            import MetaTrader5 as mt5

            positions = mt5.positions_get(symbol=pair)
            if positions is None:
                return None

            side = str(setup.get("side", "")).upper().strip()
            sl = float(setup.get("sl", 0.0))
            tp = float(setup.get("tp", 0.0))
            tol = self._price_match_tol()

            expected_type = mt5.POSITION_TYPE_BUY if side in {
                "B", "BUY"} else mt5.POSITION_TYPE_SELL

            for p in positions:
                if getattr(p, "symbol", "") != pair:
                    continue
                if getattr(p, "type", None) != expected_type:
                    continue

                p_sl = float(getattr(p, "sl", 0.0) or 0.0)
                p_tp = float(getattr(p, "tp", 0.0) or 0.0)

                if abs(p_sl - sl) <= tol and abs(p_tp - tp) <= tol:
                    return p

            return None

        except Exception as e:
            print(f"  -> _find_mt5_open_position_for_setup failed: {e}")
            return None

    def _has_mt5_closed_trade_for_setup(self, pair: str, day, setup: Dict) -> bool:
        try:
            import MetaTrader5 as mt5
            from datetime import datetime, timedelta, time

            side = str(setup.get("side", "")).upper().strip()
            entry = float(setup.get("entry", 0.0) or 0.0)
            sl = float(setup.get("sl", 0.0) or 0.0)
            tp = float(setup.get("tp", 0.0) or 0.0)
            trigger_time_raw = setup.get("trigger_time")
            tol = float(self._price_match_tol())

            print(
                f"  -> [_has_mt5_closed_trade_for_setup] "
                f"pair={pair} side={side} entry={entry} sl={sl} tp={tp} "
                f"trigger_time={trigger_time_raw} tol={tol}"
            )

            if side not in {"B", "BUY", "S", "SELL"}:
                print("     side invalid, returning False")
                return False

            day_date = pd.to_datetime(day).date()
            day_start = datetime.combine(day_date, time(0, 0))
            day_end = day_start + timedelta(days=1)

            trigger_time = pd.to_datetime(
                trigger_time_raw) if trigger_time_raw is not None else day_start
            open_time_min = trigger_time - timedelta(minutes=5)

            print(
                f"     day_date={day_date} day_start={day_start} "
                f"day_end={day_end} open_time_min={open_time_min}"
            )

            deals = mt5.history_deals_get(
                day_start - timedelta(days=1), day_end, group=pair)
            if deals is None:
                print("     no deals returned from history_deals_get, returning False")
                return False

            deals = [d for d in deals if getattr(d, "symbol", "") == pair]
            print(
                f"     total deals for {pair} in window (prev_day->today_end) = {len(deals)}")

            if not deals:
                return False

            expected_open_type = mt5.DEAL_TYPE_BUY if side in {
                "B", "BUY"} else mt5.DEAL_TYPE_SELL
            candidate_positions = []

            for d in deals:
                d_type = getattr(d, "type", None)
                d_entry_flag = getattr(d, "entry", None)
                d_time_val = getattr(d, "time", None)
                d_price = float(getattr(d, "price", 0.0) or 0.0)
                d_sl = float(getattr(d, "sl", 0.0) or 0.0)
                d_tp = float(getattr(d, "tp", 0.0) or 0.0)
                position_id = int(getattr(d, "position_id", 0) or 0)
                deal_ticket = int(getattr(d, "ticket", 0) or 0)

                d_open_time = (
                    pd.to_datetime(d_time_val, unit="s", errors="coerce")
                    if d_time_val is not None
                    else None
                )

                print(
                    "     [deal] "
                    f"ticket={deal_ticket} pos_id={position_id} type={d_type} "
                    f"entry_flag={d_entry_flag} time={d_open_time} "
                    f"price={d_price} sl={d_sl} tp={d_tp}"
                )

                if d_type != expected_open_type:
                    continue

                if d_entry_flag is not None and d_entry_flag != mt5.DEAL_ENTRY_IN:
                    continue

                if d_open_time is None or pd.isna(d_open_time):
                    continue

                if d_open_time.date() != day_date:
                    continue

                if d_open_time < open_time_min:
                    continue

                entry_match = abs(d_price - entry) <= tol
                sl_match = (d_sl == 0.0 or abs(d_sl - sl) <= tol)
                tp_match = (d_tp == 0.0 or abs(d_tp - tp) <= tol)

                print(
                    f"        candidate check: "
                    f"entry_match={entry_match} sl_match={sl_match} tp_match={tp_match}"
                )

                if entry_match and sl_match and tp_match and position_id > 0:
                    print(
                        "        -> CANDIDATE OPEN MATCH: "
                        f"position_id={position_id} ticket={deal_ticket} open_time={d_open_time}"
                    )
                    candidate_positions.append(
                        (position_id, deal_ticket, d_open_time))

            if not candidate_positions:
                print("     no candidate opening positions matched, returning False")
                return False

            for position_id, open_ticket, matched_open_time in candidate_positions:
                print(
                    f"     scanning for close deals on position_id={position_id} "
                    f"(open_ticket={open_ticket})"
                )
                for d in deals:
                    d_position_id = int(getattr(d, "position_id", 0) or 0)
                    d_ticket = int(getattr(d, "ticket", 0) or 0)
                    d_entry_flag = getattr(d, "entry", None)
                    d_time_val = getattr(d, "time", None)

                    if d_position_id != position_id:
                        continue
                    if d_ticket == open_ticket:
                        continue

                    d_close_time = (
                        pd.to_datetime(d_time_val, unit="s", errors="coerce")
                        if d_time_val is not None
                        else None
                    )

                    print(
                        "        [close-scan] "
                        f"ticket={d_ticket} pos_id={d_position_id} "
                        f"entry_flag={d_entry_flag} close_time={d_close_time}"
                    )

                    if d_entry_flag not in {
                        mt5.DEAL_ENTRY_OUT,
                        mt5.DEAL_ENTRY_OUT_BY,
                        mt5.DEAL_ENTRY_INOUT,
                    }:
                        continue
                    if d_close_time is None or pd.isna(d_close_time):
                        continue
                    if d_close_time.date() != day_date:
                        continue
                    if d_close_time < matched_open_time:
                        continue

                    print(
                        "        -> CONFIRMED CLOSED POSITION: "
                        f"position_id={position_id} open={matched_open_time} close={d_close_time}"
                    )
                    return True

            print(
                "     no confirmed closed positions for matched candidates, returning False")
            return False

        except Exception as e:
            print(f"  -> _has_mt5_closed_trade_for_setup failed: {e}")
            return False

    def _build_setup_for_day(
        self,
        day_df: pd.DataFrame,
        fund: float,
        risk_percent: float,
        gap_info: Optional[Dict] = None,
    ):
        return build_setup_for_day(
            engine=self,
            day_df=day_df,
            fund=fund,
            risk_percent=risk_percent,
            gap_info=gap_info,
        )

    def _invalidate_pending_setup_on_new_pivot(
        self,
        setup: Optional[Dict],
        intraday_df: pd.DataFrame,
    ) -> Dict:
        return invalidate_pending_setup_on_new_pivot(
            setup=setup,
            intraday_df=intraday_df,
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

        SIMPLE ENTRY TOUCH MODE:
        - BUY: candle high >= entry_price -> fill
        - SELL: candle low <= entry_price -> fill

        RULE:
        - Har candle pe pehle entry touch check hoga,
          agar fill nahi hua tabhi pending invalidation check hoga.
        """

        entry_price = float(setup["entry"])
        side = str(setup["side"]).upper().strip()

        print("  -> DEBUG: USING NEW _wait_for_entry_in_window V2")
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

        for idx in search_df.index:
            row = day_df.loc[idx]
            row_time = row["time"]
            high = float(row["high"])
            low = float(row["low"])

            # 1) SIMPLE ENTRY TOUCH LOGIC (fill priority)
            if side in ("BUY", "B"):
                if high >= entry_price:
                    print(
                        f"  -> {side} entry HIT at {row_time}, "
                        f"actual_entry={entry_price:.5f}"
                    )
                    return {
                        "entry_idx": idx,
                        "entry_time": row_time,
                        "actual_entry": entry_price,
                    }

            elif side in ("SELL", "S"):
                if low <= entry_price:
                    print(
                        f"  -> {side} entry HIT at {row_time}, "
                        f"actual_entry={entry_price:.5f}"
                    )
                    return {
                        "entry_idx": idx,
                        "entry_time": row_time,
                        "actual_entry": entry_price,
                    }

            # 2) AGAR FILL NAHIN HUA, TABHI PENDING INVALIDATION
            partial_df = day_df.loc[day_df["time"] <= row_time].copy()
            chk = self._invalidate_pending_setup_on_new_pivot(
                setup=setup,
                intraday_df=partial_df,
            )
            if chk.get("cancelled"):
                print(
                    f"  -> {side} pending CANCELLED before fill at {row_time} "
                    f"| reason={chk.get('reason')}"
                )
                return None

        print(f"  -> {side} pending not filled in this session")
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

                bridge_data = fetch_mt5_h1_m15_atr(
                    symbol=pair,
                    day=datetime.strptime(str(day), "%Y-%m-%d"),
                    atr_period=self.atr_period,
                    timeout_sec=30,
                )

                h1atr = bridge_data.get("h1")
                m15atr = bridge_data.get("m15")

                if h1atr is None or h1atr.empty:
                    print(
                        f" -> No MT5 1H ATR data for {pair} on {day}, skipping")
                    continue

                self.h1_atr_df = h1atr.copy()
                self.h1_atr_df["time"] = pd.to_datetime(
                    self.h1_atr_df["time"], errors="coerce")
                self.h1_atr_df["atr"] = pd.to_numeric(
                    self.h1_atr_df["atr"], errors="coerce")
                self.h1_atr_df = (
                    self.h1_atr_df
                    .dropna(subset=["time", "atr"])
                    .sort_values("time")
                    .reset_index(drop=True)
                )
                self.h1atrdf = self.h1_atr_df.copy()

                df = df.copy()
                df["time"] = pd.to_datetime(df["time"], errors="coerce")

                if m15atr is not None and not m15atr.empty:
                    m15 = m15atr.copy()
                    m15["time"] = pd.to_datetime(m15["time"], errors="coerce")
                    m15["atr"] = pd.to_numeric(m15["atr"], errors="coerce")
                    df = df.merge(
                        m15[["time", "atr"]],
                        on="time",
                        how="left",
                        suffixes=("", "_m15_bridge"),
                    )

                trades = process_pair_day_live_style(self, day, pair, df)

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
        return ensure_registry_file()

    def _load_live_registry(self) -> Dict:
        return load_live_registry()

    def _save_live_registry(self, data: Dict):
        return save_live_registry(data)

    def _get_live_fund_for_sizing(self) -> float:
        try:
            from live_fund_manager import get_live_usable_fund

            return float(get_live_usable_fund(
                currentfund=self.current_fund,
                initialfund=self.initial_fund,
                use_live_equity_sizing=getattr(
                    self, "use_live_equity_sizing", False),
                live_source_fund=getattr(self, "live_source_fund", None),
                live_strategy_start_fund=getattr(
                    self, "live_strategy_start_fund", None),
            ))
        except Exception as e:
            print(f"  -> _get_live_fund_for_sizing fallback current fund: {e}")
            return float(self.current_fund)

    def _fmt_live_ts(self, x):
        return fmt_live_ts(x)

    def _live_signal_expiry_server(self, day):
        return live_signal_expiry_server(day)

    def _make_signal_id_from_setup(self, pair: str, day, setup: dict) -> str:
        return make_signal_id_from_setup(pair, day, setup)

    def _mark_signal_completed_in_registry(self, signal_id: str, trade: Dict):
        return mark_signal_completed_in_registry(signal_id, trade)

    def _mark_signal_non_completed_in_registry(self, signal_id: str, status: str):
        return mark_signal_non_completed_in_registry(signal_id, status)

    def _is_signal_completed_in_registry(self, signal_id: str) -> bool:
        return is_signal_completed_in_registry(signal_id)

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
        return is_same_completed_trade_prices(
            pair=pair,
            day=day,
            side=side,
            entry=entry,
            sl=sl,
            tp=tp,
            price_tol=price_tol,
        )

    def _has_any_completed_trade_for_pair_day(self, pair: str, day) -> bool:
        return has_any_completed_trade_for_pair_day(pair, day)

    def _has_active_registry_signal_for_pair_day_side(self, pair: str, day, side: str) -> bool:
        return has_active_registry_signal_for_pair_day_side(pair, day, side)

    def _get_active_registry_signal_for_pair_day_side(self, pair: str, day, side: str):
        return get_active_registry_signal_for_pair_day_side(pair, day, side)

    def _is_same_setup_signature(self, row: Dict, setup: dict, price_tol: float = 0.00005) -> bool:
        return is_same_setup_signature(row, setup, price_tol=price_tol)

    def _is_newer_setup_than_row(self, row: Dict, setup: dict) -> bool:
        return is_newer_setup_than_row(row, setup)

    def _is_setup_in_hhll_disable_window(self, setup: dict) -> bool:
        return is_setup_in_hhll_disable_window(
            setup=setup,
            disable_start_server=self.hhll_disable_start_server,
            disable_end_server=self.hhll_disable_end_server,
        )

    def _parse_registry_ts(self, x):
        return parse_registry_ts(x)

    def _get_signal_expiry_from_row(self, row: Dict):
        return get_signal_expiry_from_row(row)

    def _scan_signal_outcome_from_df(self, df: pd.DataFrame, row: Dict):
        return scan_signal_outcome_from_df(df, row)

    def _finalize_signal_from_market_before_cancel(self, signal_id: str, pair: str, day) -> bool:
        try:
            reg = self._load_live_registry()
            row = reg.get(signal_id)
            if not row:
                return False

            row_completed = bool(row.get("completed", False))
            row_status = str(row.get("registry_status", "")).strip().upper()
            row_exit_result = str(row.get("exit_result", "")).strip().lower()

            # Already finalized, kuch karne ki zarurat nahi
            if (
                row_completed
                or row_status == "COMPLETED"
                or row_exit_result in {"tp", "sl", "sl_lock10", "session_exit"}
            ):
                return True

            # Latest 15m data fetch
            from live_data_mt5 import fetch_live_15m

            df = fetch_live_15m(pair, lookback_days=30)
            if df is None or df.empty:
                return False

            df = df.copy()
            if "time" in df.columns:
                df["time"] = pd.to_datetime(df["time"], errors="coerce")
            elif "datetime" in df.columns:
                df["time"] = pd.to_datetime(df["datetime"], errors="coerce")
            else:
                return False

            df = df.dropna(subset=["time"]).sort_values(
                "time").reset_index(drop=True)
            if df.empty:
                return False

            outcome = self._scan_signal_outcome_from_df(df, row)
            if outcome is None:
                return False

            result = str(outcome.get("result", "")).strip().lower()
            entry_hit = bool(outcome.get("entry_hit", False))
            entry_time = outcome.get("entry_time")
            exit_time = outcome.get("exit_time")

            if entry_hit:
                row["entry_hit"] = True
                row["entry_time"] = self._fmt_live_ts(entry_time)
                row["exit_time"] = self._fmt_live_ts(exit_time)

            if result in {"tp", "sl", "sl_lock10"}:
                row["exit_result"] = result
                row["registry_status"] = "COMPLETED"
                row["completed"] = True
                row["last_updated"] = datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S")
                reg[signal_id] = row
                self._save_live_registry(reg)
                print(
                    f" -> Finalized before cancel: {signal_id} result={result}")
                return True

            if result == "open_or_expired":
                expiry = self._get_signal_expiry_from_row(row)
                now_ts = df.iloc[-1]["time"] if not df.empty else None
                if expiry is not None and now_ts is not None and now_ts > expiry:
                    row["exit_result"] = "session_exit"
                    row["registry_status"] = "COMPLETED"
                    row["completed"] = True
                    row["last_updated"] = datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S")
                    reg[signal_id] = row
                    self._save_live_registry(reg)
                    print(
                        f" -> Finalized before cancel: {signal_id} result=session_exit")
                    return True

            return False

        except Exception as e:
            print(
                f" -> _finalize_signal_from_market_before_cancel failed for {signal_id}: {e}")
            return False

    def _reconcile_open_registry_signals_with_market_data(self, pair: str, df: pd.DataFrame):
        return reconcile_open_registry_signals_with_market_data(
            engine=self,
            pair=pair,
            df=df,
        )

    def _is_same_live_payload(self, existing: Optional[Dict], payload: Dict) -> bool:
        return is_same_live_payload(existing, payload)

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
        return cancel_existing_signal_strict(
            build_live_cancel_payload_fn=lambda **kwargs: self._build_live_cancel_payload(
                **kwargs),
            write_live_signal_file_fn=lambda signal_file, payload: self._write_live_signal_file(
                signal_file, payload
            ),
            terminal_filled_statuses=TERMINAL_FILLED_STATUSES,
            pair=pair,
            day=day,
            signal_file=signal_file,
            existing=existing,
            max_spread_points=max_spread_points,
            max_slippage_points=max_slippage_points,
            reason=reason,
            pre_cancel_finalize_fn=None,
        )

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
        return write_fresh_signal_after_strict_delete(
            build_live_place_payload_fn=lambda **kwargs: self._build_live_place_payload(
                **kwargs),
            is_same_live_payload_fn=self._is_same_live_payload,
            cancel_existing_signal_strict_fn=lambda **kwargs: self._cancel_existing_signal_strict(
                **kwargs
            ),
            write_live_signal_file_fn=lambda signal_file, payload: self._write_live_signal_file(
                signal_file, payload
            ),
            active_file_statuses=ACTIVE_FILE_STATUSES,
            terminal_filled_statuses=TERMINAL_FILLED_STATUSES,
            pair=pair,
            day=day,
            signal_file=signal_file,
            setup=setup,
            existing=existing,
            existing_status=existing_status,
            max_spread_points=max_spread_points,
            max_slippage_points=max_slippage_points,
            reason=reason,
        )

    def _build_live_cancel_payload(
        self,
        pair: str,
        day,
        existing_signal_id: str = "",
        existing_side: str = "",
        max_spread_points=25,
        max_slippage_points=15,
    ):
        return build_live_cancel_payload(
            live_signal_expiry_server_fn=self._live_signal_expiry_server,
            pair=pair,
            day=day,
            existing_signal_id=existing_signal_id,
            existing_side=existing_side,
            max_spread_points=max_spread_points,
            max_slippage_points=max_slippage_points,
        )

    def _build_live_place_payload(
        self,
        pair: str,
        day,
        setup: dict,
        action: str = "PLACE",
        max_spread_points=25,
        max_slippage_points=15,
    ):
        return build_live_place_payload(
            fmt_live_ts_fn=self._fmt_live_ts,
            make_signal_id_from_setup_fn=self._make_signal_id_from_setup,
            live_signal_expiry_server_fn=self._live_signal_expiry_server,
            pair=pair,
            day=day,
            setup=setup,
            action=action,
            max_spread_points=max_spread_points,
            max_slippage_points=max_slippage_points,
        )

    def _live_payload_to_line(self, payload: dict) -> str:
        return live_payload_to_line(payload)

    def _read_existing_live_signal(self, signal_file: str):
        return read_existing_live_signal(signal_file)

    def _write_live_signal_file(self, signal_file: str, payload: dict):
        return write_live_signal_file(
            signal_file=signal_file,
            payload=payload,
            read_existing_live_signal_fn=self._read_existing_live_signal,
            live_payload_to_line_fn=self._live_payload_to_line,
            is_same_live_payload_fn=self._is_same_live_payload,
        )

    def _choose_live_setup_for_day(self, day_df: pd.DataFrame, fund: float, risk_percent: float):
        return choose_live_setup_for_day(
            engine=self,
            day_df=day_df,
            fund=fund,
            risk_percent=risk_percent,
        )

    def generate_live_dual_signals_for_latest_day(
        self,
        pair: str,
        df_15m: pd.DataFrame,
        signal_file: str = None,
        signal_dir: str = None,
        max_spread_points: int = 25,
        max_slippage_points: int = 15,
    ):
        return generate_live_dual_signals_for_latest_day(
            engine=self,
            terminal_filled_statuses=TERMINAL_FILLED_STATUSES,
            pair=pair,
            df_15m=df_15m,
            signal_file=signal_file,
            signal_dir=signal_dir,
            max_spread_points=max_spread_points,
            max_slippage_points=max_slippage_points,
        )

    def export_to_excel(self, output_path: str) -> None:
        folder = "backtests"
        os.makedirs(folder, exist_ok=True)
        full_path = os.path.join(folder, os.path.basename(output_path))

        total_trades = len(self.trades)
        net_pnl = self.current_fund - self.initial_fund

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

                if "entry_mode" not in trades_df.columns:
                    trades_df["entry_mode"] = ""

                if "sl_mode" not in trades_df.columns:
                    trades_df["sl_mode"] = "NORMAL"

                trades_df["Entry From"] = trades_df["entry_mode"].apply(
                    lambda x: "T1" if isinstance(x, str) and x.endswith("_T1")
                    else ("AT" if isinstance(x, str) and x.endswith("_AT") else "")
                )

                if "entry_price" in trades_df.columns:
                    insert_pos = trades_df.columns.get_loc("entry_price") + 1
                    col = trades_df.pop("Entry From")
                    trades_df.insert(insert_pos, "Entry From", col)

                trades_df["SL Mode"] = trades_df["sl_mode"].apply(
                    lambda x: "SL_LOCK10" if x == "LOCK10_TP80" else "NORMAL"
                )

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

                rename_map = {
                    "date": "Date",
                    "open_balance": "Open Balance",
                    "risk_percent": "Risk %",
                    "trade_count": "No Trades",
                    "tp_hits": "TP Hits",
                    "max_lot": "Max Lot",
                    "profit": "Profit",
                    "close_balance": "Final Balance",
                }

                daily_df = daily_df.rename(columns=rename_map)

                ordered_cols = [
                    "Date",
                    "Open Balance",
                    "Risk %",
                    "No Trades",
                    "TP Hits",
                    "Max Lot",
                    "Profit",
                    "Final Balance",
                ]

                for col in ordered_cols:
                    if col not in daily_df.columns:
                        daily_df[col] = 0.0

                daily_df = daily_df[ordered_cols]

                daily_df.to_excel(
                    writer, sheet_name="Day Wise Briefing", index=False)

        print(f"\nBacktest results exported to: {full_path}")
