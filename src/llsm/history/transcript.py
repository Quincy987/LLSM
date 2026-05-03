"""Append-only JSONL transcript writer/reader for one conversation.

Write semantics (per plan):
  * UTF-8, line-buffered append.
  * flush() after each message so a crash loses at most the in-progress line.
  * Reader drops a trailing malformed line silently — matches chat.log's
    discipline (line-buffered, never raises).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from .models import Message


class TranscriptWriter:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._fp = None
        path.parent.mkdir(parents=True, exist_ok=True)

    def _open(self):
        if self._fp is None:
            self._fp = open(self._path, "a", encoding="utf-8", buffering=1)
        return self._fp

    def append(self, msg: Message) -> None:
        try:
            fp = self._open()
            fp.write(json.dumps(msg.to_dict(), ensure_ascii=False) + "\n")
            fp.flush()
        except Exception:
            # Best-effort; never raise into the chat pipeline.
            pass

    def close(self) -> None:
        fp, self._fp = self._fp, None
        if fp is not None:
            try:
                fp.close()
            except Exception:
                pass


def read_transcript(path: Path) -> Iterator[Message]:
    """Yield Messages from a JSONL transcript, skipping malformed lines.

    The last line is the most likely to be malformed (kill mid-write); we
    silently drop ANY parse failure rather than complicate caller code.
    """
    if not path.exists():
        return
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        try:
            yield Message.from_dict(data)
        except Exception:
            continue
