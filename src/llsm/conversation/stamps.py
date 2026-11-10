"""Times shown in the chat: local, short ("21:41" today, "2026-11-09 21:41" before)."""
from __future__ import annotations

from datetime import datetime


def now_stamp() -> str:
    """Local time now as 'YYYY-MM-DD HH:MM:SS'."""
    return datetime.now().isoformat(timespec="seconds").replace("T", " ")


def local_stamp(iso: str | None) -> str:
    """A transcript timestamp as local 'YYYY-MM-DD HH:MM:SS' (like now_stamp).

    Transcripts store UTC with an offset ("2026-11-10T20:41:16+00:00"); shown
    as-is they'd read an hour or two early next to live messages. Stamps
    without an offset (legacy transcripts) are taken as local already.
    Empty or unreadable input gives the current time.
    """
    try:
        dt = datetime.fromisoformat((iso or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return now_stamp()
    if dt.tzinfo is not None:
        try:
            dt = dt.astimezone()   # to local time
        except (OSError, OverflowError, ValueError):
            pass                   # out of the platform's range: show it unconverted
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def short_stamp(stamp: str, today: str | None = None) -> str:
    """'YYYY-MM-DD HH:MM:SS' -> 'HH:MM' on `today` (default: now), else 'YYYY-MM-DD HH:MM'."""
    date, _, clock = (stamp or "").partition(" ")
    clock = clock[:5]
    if not clock:
        return stamp
    if today is None:
        today = now_stamp()[:10]
    return clock if date == today else f"{date} {clock}"
