import json
import os
from datetime import datetime, time
from typing import Dict

import pandas as pd

REGISTRY_FILE = r"live_registry/hl_live_registry.json"
REGISTRY_STATUS_COMPLETED = "COMPLETED"


def ensure_registry_file():
    os.makedirs(os.path.dirname(REGISTRY_FILE), exist_ok=True)
    if not os.path.exists(REGISTRY_FILE):
        with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f, indent=2)


def load_live_registry() -> Dict:
    ensure_registry_file()
    try:
        with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"  -> Registry load failed: {e}")
        return {}


def save_live_registry(data: Dict):
    ensure_registry_file()
    with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def fmt_live_ts(x):
    if x is None:
        return ""
    try:
        return pd.to_datetime(x).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(x)


def live_signal_expiry_server(day):
    return datetime.combine(day, time(23, 50))


def make_signal_id_from_setup(pair: str, day, setup: dict) -> str:
    side = str(setup.get("side", "")).strip().upper()
    trigger = fmt_live_ts(setup.get("trigger_time")).replace(
        " ", "_").replace(":", "-")
    entry = round(float(setup.get("entry", 0.0)), 5)
    sl = round(float(setup.get("sl", 0.0)), 5)
    tp = round(float(setup.get("tp", 0.0)), 5)
    return f"{pair}_{day}_{side}_{trigger}_{entry:.5f}_{sl:.5f}_{tp:.5f}"


def mark_signal_completed_in_registry(signal_id: str, trade: Dict):
    reg = load_live_registry()
    if signal_id not in reg:
        reg[signal_id] = {"signal_id": signal_id}

    result = str(trade.get("result", "")).lower()
    reg[signal_id]["entry_hit"] = True
    reg[signal_id]["exit_result"] = result
    reg[signal_id]["entry_time"] = str(trade.get("entry_time", ""))
    reg[signal_id]["exit_time"] = str(trade.get("exit_time", ""))
    reg[signal_id]["registry_status"] = "COMPLETED"
    reg[signal_id]["completed"] = True
    reg[signal_id]["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_live_registry(reg)


def mark_signal_non_completed_in_registry(signal_id: str, status: str):
    reg = load_live_registry()
    if signal_id not in reg:
        reg[signal_id] = {"signal_id": signal_id}

    reg[signal_id]["registry_status"] = str(status).upper()
    reg[signal_id]["completed"] = False
    reg[signal_id]["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_live_registry(reg)


def is_signal_completed_in_registry(signal_id: str) -> bool:
    reg = load_live_registry()
    row = reg.get(signal_id, {})
    return bool(row.get("completed", False))


def is_same_completed_trade_prices(
    pair: str,
    day,
    side: str,
    entry: float,
    sl: float,
    tp: float,
    price_tol: float = 0.00005,
) -> bool:
    reg = load_live_registry()
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


def has_any_completed_trade_for_pair_day(pair: str, day) -> bool:
    reg = load_live_registry()
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


def has_active_registry_signal_for_pair_day_side(pair: str, day, side: str) -> bool:
    reg = load_live_registry()
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


def is_setup_in_hhll_disable_window(setup: dict, disable_start_server, disable_end_server) -> bool:
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
    return disable_start_server <= t < disable_end_server


def parse_registry_ts(x):
    if x is None or str(x).strip() == "":
        return None
    try:
        return pd.to_datetime(x)
    except Exception:
        return None


def get_signal_expiry_from_row(row: Dict):
    day_str = str(row.get("day", "")).strip()
    if not day_str:
        return None
    try:
        day_dt = pd.to_datetime(day_str).date()
        return live_signal_expiry_server(day_dt)
    except Exception:
        return None


def scan_signal_outcome_from_df(df: pd.DataFrame, row: Dict):
    if df is None or df.empty:
        return None

    df = df.copy()
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time").reset_index(drop=True)

    side = str(row.get("side", "")).upper().strip()
    entry = float(row.get("entry", 0.0))
    sl = float(row.get("sl", 0.0))
    tp = float(row.get("tp", 0.0))
    trigger_time = parse_registry_ts(row.get("trigger_time"))
    expiry = get_signal_expiry_from_row(row)

    if trigger_time is None:
        return None

    work_df = df[df["time"] >= trigger_time].copy()
    if expiry is not None:
        work_df = work_df[work_df["time"] <= expiry].copy()

    if work_df.empty:
        return None

    entry_hit = False
    entry_time = None

    for _, candle in work_df.iterrows():
        t = candle["time"]
        high = float(candle["high"])
        low = float(candle["low"])

        if not entry_hit:
            if side == "B":
                if high >= entry:
                    entry_hit = True
                    entry_time = t
                    if low <= sl:
                        return {
                            "entry_hit": True,
                            "result": "sl",
                            "entry_time": entry_time,
                            "exit_time": t,
                        }
                    if high >= tp:
                        return {
                            "entry_hit": True,
                            "result": "tp",
                            "entry_time": entry_time,
                            "exit_time": t,
                        }
            elif side == "S":
                if low <= entry:
                    entry_hit = True
                    entry_time = t
                    if high >= sl:
                        return {
                            "entry_hit": True,
                            "result": "sl",
                            "entry_time": entry_time,
                            "exit_time": t,
                        }
                    if low <= tp:
                        return {
                            "entry_hit": True,
                            "result": "tp",
                            "entry_time": entry_time,
                            "exit_time": t,
                        }
            continue

        if side == "B":
            if low <= sl:
                return {
                    "entry_hit": True,
                    "result": "sl",
                    "entry_time": entry_time,
                    "exit_time": t,
                }
            if high >= tp:
                return {
                    "entry_hit": True,
                    "result": "tp",
                    "entry_time": entry_time,
                    "exit_time": t,
                }

        elif side == "S":
            if high >= sl:
                return {
                    "entry_hit": True,
                    "result": "sl",
                    "entry_time": entry_time,
                    "exit_time": t,
                }
            if low <= tp:
                return {
                    "entry_hit": True,
                    "result": "tp",
                    "entry_time": entry_time,
                    "exit_time": t,
                }

    if entry_hit:
        last_time = work_df.iloc[-1]["time"]
        return {
            "entry_hit": True,
            "result": "open_or_expired",
            "entry_time": entry_time,
            "exit_time": last_time,
        }

    return {
        "entry_hit": False,
        "result": "not_triggered",
        "entry_time": None,
        "exit_time": work_df.iloc[-1]["time"],
    }


def reconcile_open_registry_signals_with_market_data(engine, pair: str, df: pd.DataFrame):
    reg = load_live_registry()
    changed = False

    for signal_id, row in reg.items():
        row_pair = str(row.get("pair", "")).strip()
        row_completed = bool(row.get("completed", False))
        row_status = str(row.get("registry_status", "")).strip().upper()

        if row_pair != pair:
            continue

        if row_completed or row_status == "COMPLETED":
            continue

        outcome = scan_signal_outcome_from_df(df, row)
        if outcome is None:
            continue

        if outcome["entry_hit"]:
            row["entry_hit"] = True
            row["entry_time"] = str(outcome.get("entry_time", ""))
            row["exit_time"] = str(outcome.get("exit_time", ""))

            result = str(outcome.get("result", "")).lower()
            if result in {"tp", "sl"}:
                row["exit_result"] = result
                row["registry_status"] = "COMPLETED"
                row["completed"] = True
            else:
                row["registry_status"] = "ENTRY_HIT"
                row["completed"] = False
        else:
            result = str(outcome.get("result", "")).lower()
            if result == "not_triggered":
                row["registry_status"] = "GENERATED"
                row["completed"] = False

        row["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        reg[signal_id] = row
        changed = True

    if changed:
        save_live_registry(reg)
