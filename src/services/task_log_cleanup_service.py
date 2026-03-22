"""
Task run log cleanup service.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path


def cleanup_task_logs(
    logs_dir: str = "logs",
    *,
    keep_days: int = 7,
    now: datetime | None = None,
) -> list[str]:
    if keep_days < 1:
        print(f"Task log cleanup skipped: invalid keep_days value ({keep_days})")
        return []

    root = Path(logs_dir)
    if not root.exists():
        return []

    current_time = now or datetime.now()
    cutoff = current_time - timedelta(days=keep_days)
    removed_files: list[str] = []

    for path in root.glob("*.log"):
        if not path.is_file():
            continue
        try:
            modified_at = datetime.fromtimestamp(path.stat().st_mtime)
        except OSError as exc:
            print(f"Failed to read task log modification time, skipping: {path} ({exc})")
            continue

        if modified_at >= cutoff:
            continue

        try:
            path.unlink()
            removed_files.append(str(path))
        except OSError as exc:
            print(f"Failed to delete historical task log, skipping: {path} ({exc})")

    if removed_files:
        print(
            f"Task log cleanup complete: deleted {len(removed_files)} log file(s) older than {keep_days} day(s)."
        )

    return removed_files
