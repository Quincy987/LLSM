"""Shared filesystem + time helpers for the history layer."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    """UTC ISO timestamp, second precision. Sortable as a string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_write_json(path: Path, payload) -> None:
    """Write payload as JSON via temp + os.replace.

    A kill mid-write either leaves the original file (if rename hasn't run)
    or the new file fully written (if it has) — never a half-written one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.flush()
        try:
            os.fsync(fp.fileno())
        except OSError:
            pass
    os.replace(tmp, path)
