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
    is_signal_completed_in_registry_fn,
    is_same_completed_trade_prices_fn,
    load_live_registry_fn,
    save_live_registry_fn,
    live_signal_expiry_server_fn,
    pair: str,
    day,
    setup: dict,
    action: str = "PLACE",
    max_spread_points=25,
    max_slippage_points=15,
):
    trigger_time = fmt_live_ts_fn(setup.get("trigger_time"))
    picked_candle_time = fmt_live_ts_fn(setup.get("picked_candle_time"))
    breakout_candle_time = fmt_live_ts_fn(setup.get("breakout_candle_time"))

    entry = round(float(setup["entry"]), 5)
    sl = round(float(setup["sl"]), 5)
    tp = round(float(setup["tp"]), 5)
    atr = round(float(setup.get("atr", 0.0)), 5)
    lot = round(float(setup["lot_size"]), 2)
    side = str(setup.get("side", "")).upper().strip()

    signal_id = make_signal_id_from_setup_fn(pair, day, setup)

    already_completed_exact = is_signal_completed_in_registry_fn(signal_id)
    already_completed_same_prices = is_same_completed_trade_prices_fn(
        pair=pair,
        day=day,
        side=side,
        entry=entry,
        sl=sl,
        tp=tp,
    )

    if already_completed_exact or already_completed_same_prices:
        print(f" -> Registry says completed, skip payload build: {signal_id}")
        return None

    reg = load_live_registry_fn()
    row = reg.get(signal_id, {})

    row_completed = bool(row.get("completed", False))
    row_status = str(row.get("registry_status", "")).upper().strip()
    row_exit_result = str(row.get("exit_result", "")).strip().lower()

    if (
        row_completed
        or row_status == "COMPLETED"
        or row_exit_result in {"tp", "sl", "sl_lock10", "session_exit"}
    ):
        print(
            f" -> Existing registry row already finalized, skip payload build: {signal_id}"
        )
        return None

    prev_row = reg.get(signal_id, {})
    if (
        bool(prev_row.get("completed", False))
        or str(prev_row.get("registry_status", "")).upper().strip() == "COMPLETED"
    ):
        print(f" -> Refusing to overwrite completed row: {signal_id}")
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

    save_live_registry_fn(reg)
    print(f" -> Registry signal added/updated: {signal_id}")

    return {
        "action": action,
        "signal_id": signal_id,
        "symbol": pair,
        "side": side,
        "expiry_server": live_signal_expiry_server_fn(day).strftime("%Y-%m-%d %H:%M:%S"),
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
    mark_signal_non_completed_in_registry_fn,
    terminal_filled_statuses,
    pair: str,
    day,
    signal_file: str,
    existing: Optional[Dict],
    max_spread_points: int,
    max_slippage_points: int,
    reason: str = "CANCELLEDNEWHHLL",
    pre_cancel_finalize_fn=None,
):
    if existing is None:
        try:
            if os.path.exists(signal_file):
                os.remove(signal_file)
                print(
                    f"  -> Deleted file (no existing payload): {signal_file}")
        except Exception as e:
            print(f"  -> Failed deleting file {signal_file}: {e}")
        return

    existing_status = str(existing.get("status", "")).upper().strip()
    if existing_status in terminal_filled_statuses:
        print(
            f"  -> Existing filled status {existing_status}, skip strict cancel: {signal_file}"
        )
        return

    old_signal_id = str(existing.get("signal_id", "")).strip()

    if pre_cancel_finalize_fn is not None and old_signal_id:
        try:
            finalized = bool(pre_cancel_finalize_fn(old_signal_id))
            if finalized:
                print(
                    f"  -> Existing signal finalized before cancel, skip CANCEL: {old_signal_id}"
                )
                return
        except Exception as e:
            print(
                f"  -> pre_cancel_finalize_fn failed for {old_signal_id}: {e}")

    cancel_payload = build_live_cancel_payload_fn(
        pair=pair,
        day=day,
        existing_signal_id=old_signal_id,
        existing_side=str(existing.get("side", "")).strip().upper(),
        max_spread_points=max_spread_points,
        max_slippage_points=max_slippage_points,
    )
    write_live_signal_file_fn(signal_file, cancel_payload)

    if old_signal_id:
        mark_signal_non_completed_in_registry_fn(old_signal_id, reason)

    print(f"  -> Strict cancel file kept for EA processing: {signal_file}")


def write_fresh_signal_after_strict_delete(
    load_live_registry_fn,
    has_active_registry_signal_for_pair_day_side_fn,
    make_signal_id_from_setup_fn,
    is_signal_completed_in_registry_fn,
    is_same_completed_trade_prices_fn,
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
):
    print(f"\n[FRESH DBG] ENTER pair={pair} file={signal_file}")
    print(f"[FRESH DBG] day={day}")
    print(f"[FRESH DBG] existing_status(raw)={existing_status}")
    print(f"[FRESH DBG] existing={existing}")
    print(f"[FRESH DBG] setup={setup}")

    existing_status = str(existing_status or "").upper().strip()
    setup_side = str(setup.get("side", "")).upper().strip()

    print(f"[FRESH DBG] existing_status(norm)={existing_status}")
    print(f"[FRESH DBG] setup_side={setup_side}")

    reg = load_live_registry_fn()
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

    has_active_same_side = has_active_registry_signal_for_pair_day_side_fn(
        pair, day, setup_side
    )
    print(
        f"[FRESH DBG] has_active_registry_signal_for_pair_day_side={has_active_same_side}"
    )

    if has_active_same_side:
        same_existing_side = (
            str(existing.get("side", "")).upper().strip() if existing else ""
        )
        same_existing_status = (
            str(existing.get("status", "")).upper().strip() if existing else ""
        )

        print(f"[FRESH DBG] same_existing_side={same_existing_side}")
        print(f"[FRESH DBG] same_existing_status={same_existing_status}")
        print(f"[FRESH DBG] ACTIVE_FILE_STATUSES={active_file_statuses}")

        if (
            not existing
            or same_existing_side != setup_side
            or same_existing_status not in active_file_statuses
        ):
            print(
                f"[FRESH DBG] active registry guard blocked fresh write for {pair} {day} side={setup_side}"
            )
            return None

    signal_id = make_signal_id_from_setup_fn(pair, day, setup)
    print(f"[FRESH DBG] signal_id={signal_id}")

    already_completed_exact = is_signal_completed_in_registry_fn(signal_id)
    already_completed_same_prices = is_same_completed_trade_prices_fn(
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

    print(f"[FRESH DBG] TERMINAL_FILLED_STATUSES={terminal_filled_statuses}")
    if existing_status in terminal_filled_statuses:
        print("[FRESH DBG] existing trade already filled/managed -> no overwrite")
        return None

    payload = build_live_place_payload_fn(
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

    if existing is not None and existing_status in active_file_statuses:
        same_payload = is_same_live_payload_fn(existing, payload)
        print(f"[FRESH DBG] existing active file detected")
        print(f"[FRESH DBG] existing_status in ACTIVE_FILE_STATUSES -> True")
        print(f"[FRESH DBG] same_payload={same_payload}")
        print(f"[FRESH DBG] existing payload={existing}")
        print(f"[FRESH DBG] new payload={payload}")

        if same_payload:
            print("[FRESH DBG] chosen setup unchanged (prices/lot/mode) -> no rewrite")
            return payload

        print("[FRESH DBG] chosen setup changed materially -> STRICT DELETE flow")
        cancel_existing_signal_strict_fn(
            pair=pair,
            day=day,
            signal_file=signal_file,
            existing=existing,
            max_spread_points=max_spread_points,
            max_slippage_points=max_slippage_points,
            reason=reason,
        )
        print("[FRESH DBG] strict cancel completed")

        reg_after_cancel = load_live_registry_fn()
        same_side_completed_after_cancel = False

        for _, row in reg_after_cancel.items():
            row_pair = str(row.get("pair", "")).strip()
            row_day = str(row.get("day", "")).strip()
            row_side = str(row.get("side", "")).strip().upper()
            row_completed = bool(row.get("completed", False))
            row_status = str(row.get("registry_status", "")).strip().upper()

            if row_pair != pair:
                continue
            if row_day != str(day):
                continue
            if row_side != setup_side:
                continue

            if row_completed or row_status == "COMPLETED":
                same_side_completed_after_cancel = True
                break

        if same_side_completed_after_cancel:
            print(
                f"[FRESH DBG] completed lock hit after strict cancel -> skip final write for {pair} {day} side={setup_side}"
            )
            return None

    else:
        print("[FRESH DBG] no active existing file branch, proceeding to final write")

    print(f"[FRESH DBG] calling _write_live_signal_file for {signal_file}")
    write_live_signal_file_fn(signal_file, payload)
    print(f"[FRESH DBG] final write done for {signal_file}")

    return payload
