"""Chat times: transcripts in UTC are shown in local time, and short."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from llsm.conversation import stamps


def test_utc_transcript_stamp_shows_in_local_time():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    local = now.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    assert stamps.local_stamp(now.isoformat()) == local
    assert stamps.local_stamp(now.isoformat().replace("+00:00", "Z")) == local


def test_legacy_stamp_without_offset_is_kept():
    assert stamps.local_stamp("2026-11-09T08:05:33") == "2026-11-09 08:05:33"


def test_unreadable_stamp_falls_back_to_now():
    # A full local stamp (format check, so a minute or midnight rollover can't flake it).
    for bad in ("garbage", ""):
        assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", stamps.local_stamp(bad))


def test_short_stamp_today_and_before():
    assert stamps.short_stamp("2026-11-10 21:41:16", today="2026-11-10") == "21:41"
    assert stamps.short_stamp("2026-11-09 08:05:33", today="2026-11-10") == "2026-11-09 08:05"
    # Default "today" is the real date, so a long-past stamp keeps its date.
    assert stamps.short_stamp("2000-01-01 10:00:00") == "2000-01-01 10:00"
    assert stamps.short_stamp("") == ""
