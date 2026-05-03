"""One-shot importer from the legacy chat.log + mcp.session-id files.

Idempotent. Run at panel start. If conversations/ has no conversations yet
AND a legacy session-id file exists, create one "Imported chat" referencing
that session-id and (best-effort) populate transcript.jsonl from chat.log.
The legacy supervisor session-id file is removed at the end; the new active
pointer lives in conversations/active.json owned by the store.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from .models import Message
from .store import (
    ConversationStore,
    PREVIEW_MAX,
    TITLE_MAX,
    truncate,
)
from .transcript import TranscriptWriter


# chat.log line format (see panel.py:_chat_log):
#   "[2025-... ISO ...] ROLE: body"  with multi-line bodies indented 4 spaces.
# SYS lines are panel-internal status noise ("MCP server up at...", "killed
# orphan claude processes...") — skipped on import so they don't pollute the
# conversation transcript or get re-logged into chat.log on every replay.
_LINE_RE = re.compile(r"^\[(?P<ts>[^\]]+)\]\s+(?P<role>USER|LLSM|ERR):\s?(?P<body>.*)$")
_ROLE_MAP = {"USER": "user", "LLSM": "llsm", "ERR": "error"}


def parse_chat_log(path: Path) -> Iterable[Message]:
    """Yield Messages reconstructed from chat.log. Best-effort.

    chat.log may interleave multiple sessions ("=== chat session start ...");
    we flatten them — the migrated conversation is one chunk.
    """
    msgs: list[Message] = []
    if not path.exists():
        return msgs
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return msgs

    cur_role: str | None = None
    cur_ts: str = ""
    cur_body: list[str] = []

    def emit():
        if cur_role is None:
            return
        body = "\n".join(cur_body).rstrip()
        if body:
            msgs.append(Message(role=cur_role, text=body, ts=cur_ts))

    for raw_line in text.splitlines():
        if not raw_line:
            if cur_role is not None and cur_body:
                cur_body.append("")
            continue
        m = _LINE_RE.match(raw_line)
        if m:
            emit()
            cur_role = _ROLE_MAP.get(m.group("role"), "system")
            cur_ts = m.group("ts").strip()
            cur_body = [m.group("body")]
            continue
        if cur_role is not None and (raw_line.startswith("    ") or raw_line.startswith("\t")):
            cur_body.append(raw_line.lstrip())
            continue
        # Session header / unknown line — ignore.
    emit()
    return msgs


def migrate_legacy(
    *,
    store: ConversationStore,
    legacy_session_id_file: Path,
    legacy_chat_log: Path,
    backend_kind: str,
    default_model: str,
) -> str | None:
    """Return the new conv_id if migration ran, else None. Idempotent."""
    # Already migrated? If any conversation directory exists, bail.
    if store.root.exists():
        existing = [p for p in store.root.iterdir() if p.is_dir()]
        if existing:
            try:
                legacy_session_id_file.unlink(missing_ok=True)
            except Exception:
                pass
            return None

    if not legacy_session_id_file.exists():
        return None

    try:
        sid = legacy_session_id_file.read_text(encoding="utf-8").strip()
    except Exception:
        sid = ""
    if not sid:
        try:
            legacy_session_id_file.unlink(missing_ok=True)
        except Exception:
            pass
        return None

    conv = store.create(
        backend_kind=backend_kind,
        model=default_model,
        title="Imported chat",
        session_id=sid,
    )

    # Bulk-import the transcript by writing JSONL directly rather than
    # calling append_message per line (which would re-write meta + index N
    # times). Then patch meta with derived counts/title/preview in one shot.
    msgs = list(parse_chat_log(legacy_chat_log))
    if msgs:
        writer = TranscriptWriter(store.transcript_path(conv.id))
        try:
            for m in msgs:
                writer.append(m)
        finally:
            writer.close()
        first_user = next((m for m in msgs if m.role == "user"), None)
        last_user = next((m for m in reversed(msgs) if m.role == "user"), None)
        conv.message_count = len(msgs)
        if last_user:
            conv.preview = truncate(last_user.text, PREVIEW_MAX)
        if first_user:
            conv.title = truncate(first_user.text, TITLE_MAX) or "Imported chat"
        store.write_meta(conv)
        # Re-sync the index from the just-written meta. update_session_id
        # with the same sid is an idempotent public path that does this.
        store.update_session_id(conv.id, conv.session_id)

    store.set_active(conv.id)

    try:
        legacy_session_id_file.unlink(missing_ok=True)
    except Exception:
        pass

    return conv.id
