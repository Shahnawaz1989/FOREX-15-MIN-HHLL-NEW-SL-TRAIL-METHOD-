import json
from datetime import datetime
from pathlib import Path


TERMINAL_FILLED_STATUSES = {"FILLED_BUY", "FILLED_SELL", "BE_APPLIED"}

TERMINAL_DEAD_STATUSES = {
    "FAILED",
    "CANCELLEDEOD",
    "CANCELLEDNEWHHLL",
    "EXPIRED",
    "ORDEREXPIRED1930",
}

ACTIVE_FILE_STATUSES = {"NEW", "PLACED"}

COMPLETED_RESULTS = {"tp", "sl", "sl_lock10"}
NON_COMPLETED_RESULTS = {"orderexpired1930", "session_exit"}


def load_json(path: Path):
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[cleanup] failed reading json {path}: {e}")
        return {}


def save_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def is_registry_entry_stale(signal: dict, today_str: str) -> bool:
    registrystatus = str(signal.get("registrystatus", "")).upper().strip()
    completed = bool(signal.get("completed", False))
    exitresult = str(signal.get("exitresult", "")).lower().strip()
    day = str(signal.get("day", "")).strip()[:10]

    if registrystatus in TERMINAL_DEAD_STATUSES:
        return True

    if completed:
        return True

    if exitresult in COMPLETED_RESULTS or exitresult in NON_COMPLETED_RESULTS:
        return True

    if day and day < today_str:
        if registrystatus in {"COMPLETED", "ENTRYHIT"}:
            return True

        if registrystatus not in {"GENERATED", "NEW", "PLACED"}:
            return True

    return False


def prune_live_registry(registry_file: str, today_str: str):
    registry_path = Path(registry_file)
    registry = load_json(registry_path)

    kept = {}
    removed = {}

    for signal_id, signal_data in registry.items():
        if not isinstance(signal_data, dict):
            removed[signal_id] = signal_data
            continue

        if is_registry_entry_stale(signal_data, today_str):
            removed[signal_id] = signal_data
        else:
            kept[signal_id] = signal_data

    save_json(registry_path, kept)

    archive_path = registry_path.parent / f"archive_{today_str}.json"
    archive_data = load_json(archive_path)
    archive_data.update(removed)
    save_json(archive_path, archive_data)

    print(
        f"[cleanup] registry kept={len(kept)} removed={len(removed)} archive={archive_path}")
    return kept, removed


def parse_signal_file_status(file_path: Path):
    try:
        line = file_path.read_text(encoding="utf-8").strip()

        if not line:
            return "EMPTY"

        parts = line.split("|")

        # extended format
        if len(parts) >= 20:
            return parts[19].strip().upper()

        # compact format
        if len(parts) >= 14:
            return parts[13].strip().upper()

        return "UNKNOWN"
    except Exception as e:
        print(f"[cleanup] failed reading signal file {file_path}: {e}")
        return "BROKEN"


def purge_stale_mt5_signal_files(mt5_files_dir: str, today_str: str):
    files_dir = Path(mt5_files_dir)

    if not files_dir.exists():
        print(f"[cleanup] MT5 files dir not found: {files_dir}")
        return [], []

    patterns = [
        "live_signal_*_BUY.txt",
        "live_signal_*_SELL.txt",
    ]

    deleted = []
    kept = []

    for pattern in patterns:
        for file_path in files_dir.glob(pattern):
            status = parse_signal_file_status(file_path)
            should_delete = False

            if status in TERMINAL_DEAD_STATUSES:
                should_delete = True

            if status in TERMINAL_FILLED_STATUSES:
                should_delete = True

            if status in {"EMPTY", "BROKEN", "UNKNOWN"}:
                should_delete = True

            try:
                mtime_date = datetime.fromtimestamp(
                    file_path.stat().st_mtime).strftime("%Y-%m-%d")
                if mtime_date < today_str:
                    should_delete = True
            except Exception:
                should_delete = True

            if should_delete:
                try:
                    file_path.unlink()
                    deleted.append(file_path.name)
                except Exception as e:
                    print(f"[cleanup] failed deleting {file_path}: {e}")
            else:
                kept.append(file_path.name)

    print(f"[cleanup] mt5 files deleted={len(deleted)} kept={len(kept)}")
    if deleted:
        print("[cleanup] deleted files:", deleted)

    return deleted, kept


def run_startup_cleanup(registry_file: str, mt5_files_dir: str):
    today_str = datetime.now().strftime("%Y-%m-%d")
    prune_live_registry(registry_file, today_str)
    purge_stale_mt5_signal_files(mt5_files_dir, today_str)
    print("[cleanup] startup cleanup done")
