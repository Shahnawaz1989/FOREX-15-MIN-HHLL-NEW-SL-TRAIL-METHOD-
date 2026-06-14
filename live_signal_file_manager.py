import os
from datetime import datetime
from typing import Dict, Optional


ACTIVE_FILE_STATUSES = {
    "NEW",
    "PLACED",
    "ENTRY_HIT",
    "BE_APPLIED",
    "LOCK10_APPLIED",
    "ACTIVE",
}


def is_same_live_payload(existing: Optional[Dict], payload: Dict) -> bool:
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


def build_live_cancel_payload(
    live_signal_expiry_server_fn,
    pair: str,
    day,
    existing_signal_id: str = "",
    existing_side: str = "",
    max_spread_points=25,
    max_slippage_points=15,
):
    signal_id = str(existing_signal_id or "").strip()
    if not signal_id:
        signal_id = f"{pair}_{day}_CANCEL"

    return {
        "action": "CANCEL",
        "signal_id": signal_id,
        "symbol": pair,
        "side": str(existing_side or "").strip().upper(),
        "expiry_server": live_signal_expiry_server_fn(day).strftime("%Y-%m-%d %H:%M:%S"),
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


def build_live_place_payload(
    fmt_live_ts_fn,
    make_signal_id_from_setup_fn,
    live_signal_expiry_server_fn,
    pair: str,
    day,
    setup: dict,
    action: str = "PLACE",
    max_spread_points=25,
    max_slippage_points=15,
    is_signal_completed_in_registry_fn=None,
    is_same_completed_trade_prices_fn=None,
    load_live_registry_fn=None,
    save_live_registry_fn=None,
):
    return {
        "action": action,
        "signal_id": make_signal_id_from_setup_fn(pair, day, setup),
        "symbol": pair,
        "side": str(setup.get("side", "")).upper().strip(),
        "expiry_server": live_signal_expiry_server_fn(day),
        "entry": f"{float(setup.get('entry', 0.0)):.5f}",
        "sl": f"{float(setup.get('sl', 0.0)):.5f}",
        "tp": f"{float(setup.get('tp', 0.0)):.5f}",
        "lot": f"{float(setup.get('lot_size', 0.0)):.2f}",
        "entry_mode": str(setup.get("entry_mode", "STOP")),
        "atr": f"{float(setup.get('atr', 0.0)):.5f}",
        "trigger_time": fmt_live_ts_fn(setup.get("trigger_time")),
        "picked_candle_time": fmt_live_ts_fn(setup.get("picked_candle_time")),
        "breakout_candle_time": fmt_live_ts_fn(setup.get("breakout_candle_time")),
        "status": "NEW",
        "max_spread_points": str(max_spread_points),
        "max_slippage_points": str(max_slippage_points),
    }


def live_payload_to_line(payload: dict) -> str:
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


def read_existing_live_signal(signal_file: str):
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


def write_live_signal_file(
    signal_file: str,
    payload: dict,
    read_existing_live_signal_fn,
    live_payload_to_line_fn,
    is_same_live_payload_fn,
):
    print(f"\n[WRITE DBG] ENTER _write_live_signal_file")
    print(f"[WRITE DBG] signal_file = {signal_file}")
    print(f"[WRITE DBG] abs_path = {os.path.abspath(signal_file)}")
    print(f"[WRITE DBG] cwd = {os.getcwd()}")
    print(f"[WRITE DBG] payload = {payload}")

    new_line = live_payload_to_line_fn(payload)
    print(f"[WRITE DBG] new_line = {new_line}")

    old_line, existing = read_existing_live_signal_fn(signal_file)
    print(f"[WRITE DBG] old_line = {old_line}")
    print(f"[WRITE DBG] existing = {existing}")

    if old_line == new_line:
        print(f"[WRITE DBG] unchanged, skip write: {signal_file}")
        return False

    if existing is not None and is_same_live_payload_fn(existing, payload):
        print(
            f"[WRITE DBG] same payload, normalizing file format: {signal_file}")
    else:
        print(f"[WRITE DBG] file updated: {signal_file}")

    os.makedirs(os.path.dirname(signal_file), exist_ok=True)
    with open(signal_file, "w", encoding="utf-8") as f:
        f.write(new_line)

    with open(signal_file, "r", encoding="utf-8") as f:
        verify_line = f.read().strip()

    print(f"[WRITE DBG] FINAL WRITTEN LINE = {new_line}")
    print(f"[WRITE DBG] verify_after_write = {verify_line}")
    return True


def cancel_existing_signal_strict(
    build_live_cancel_payload_fn,
    write_live_signal_file_fn,
    terminal_filled_statuses,
    pair: str,
    day,
    signal_file: str,
    existing: Optional[Dict],
    max_spread_points: int,
    max_slippage_points: int,
    reason: str = "CANCELLEDNEWHHLL",
    pre_cancel_finalize_fn=None,
    mark_signal_non_completed_in_registry_fn=None,
):
    if not existing:
        return None

    existing_status = str(existing.get("status", "")).upper().strip()
    if existing_status in terminal_filled_statuses:
        print(
            f"  -> Skip cancel, terminal filled status in file: {existing_status}")
        return None

    if callable(pre_cancel_finalize_fn):
        try:
            pre_cancel_finalize_fn(existing.get("signal_id", ""))
        except Exception as e:
            print(f"  -> pre-cancel finalize skipped: {e}")

    payload = build_live_cancel_payload_fn(
        pair=pair,
        day=day,
        existing_signal_id=existing.get("signal_id", ""),
        existing_side=existing.get("side", ""),
        max_spread_points=max_spread_points,
        max_slippage_points=max_slippage_points,
    )
    payload["status"] = reason
    return write_live_signal_file_fn(signal_file, payload)


def write_fresh_signal_after_strict_delete(
    build_live_place_payload_fn,
    is_same_live_payload_fn,
    cancel_existing_signal_strict_fn,
    write_live_signal_file_fn,
    active_file_statuses,
    terminal_filled_statuses,
    pair: str,
    day,
    signal_file: str,
    setup: dict,
    existing: Optional[Dict],
    existing_status: str,
    max_spread_points: int,
    max_slippage_points: int,
    reason: str = "CANCELLEDNEWHHLL",
    load_live_registry_fn=None,
    has_active_registry_signal_for_pair_day_side_fn=None,
    make_signal_id_from_setup_fn=None,
    is_signal_completed_in_registry_fn=None,
    is_same_completed_trade_prices_fn=None,
):
    payload = build_live_place_payload_fn(
        pair=pair,
        day=day,
        setup=setup,
        action="PLACE",
        max_spread_points=max_spread_points,
        max_slippage_points=max_slippage_points,
    )

    if existing and is_same_live_payload_fn(existing, payload):
        print(f"  -> Existing file already matches fresh setup for {pair}")
        return payload

    if existing and existing_status in terminal_filled_statuses:
        print(
            f"  -> Existing terminal filled file status blocks rewrite: {existing_status}")
        return None

    if existing and existing_status in active_file_statuses:
        cancel_existing_signal_strict_fn(
            pair=pair,
            day=day,
            signal_file=signal_file,
            existing=existing,
            max_spread_points=max_spread_points,
            max_slippage_points=max_slippage_points,
            reason=reason,
        )

    return write_live_signal_file_fn(signal_file, payload)
