"""Chat times: transcripts in UTC are shown in local time, and short."""
from __future__ import annotations

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
    assert stamps.local_stamp("garbage")[:10] == stamps.now_stamp()[:10]
    assert stamps.local_stamp("")[:10] == stamps.now_stamp()[:10]


def test_short_stamp_today_and_before():
    assert stamps.short_stamp("2026-11-10 21:41:16", today="2026-11-10") == "21:41"
    assert stamps.short_stamp("2026-11-09 08:05:33", today="2026-11-10") == "2026-11-09 08:05"
    assert stamps.short_stamp(stamps.now_stamp()) == stamps.now_stamp()[11:16]
    assert stamps.short_stamp("") == ""
