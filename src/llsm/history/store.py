"""ConversationStore — file I/O for conversation metadata + index + active pointer.

Layout under ~/.llsm/conversations/:
    index.json              derived cache (atomic write, self-healing)
    active.json             {"active_conversation_id": "..."}
    <id>/meta.json          source of truth (full Conversation dataclass)
    <id>/transcript.jsonl   append-only messages

meta.json is authoritative; index.json is a derived cache, written via
temp-file + os.replace so a kill mid-write never leaves a corrupt list.
On load, if index.json is missing or fails to parse, the store rebuilds it
by scanning <id>/meta.json files. Mutations PATCH the index in place
(O(1) disk reads) instead of re-scanning every meta.json — only the parse
failure path pays the rebuild cost.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Iterable

from ._io import atomic_write_json, now_iso
from .models import Conversation, Message
from .transcript import TranscriptWriter, read_transcript


# Public so migration / tests / future tools can use the same caps.
DEFAULT_TITLE = "New chat"
TITLE_MAX = 60
PREVIEW_MAX = 120

_INDEX_NAME = "index.json"
_ACTIVE_NAME = "active.json"
_META_NAME = "meta.json"
_TRANSCRIPT_NAME = "transcript.jsonl"


def truncate(s: str, n: int) -> str:
    """Public so migration / dialogs can apply the same titling rules."""
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _sort_for_display(convs: list[Conversation]) -> list[Conversation]:
    # Stable sort: recency first, then pinned-ness so pins float to the top
    # while preserving recency order within each group.
    convs.sort(key=lambda c: c.last_used_at, reverse=True)
    convs.sort(key=lambda c: not c.pinned)
    return convs


class ConversationStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    # --- Paths ---

    def _conv_dir(self, conv_id: str) -> Path:
        return self.root / conv_id

    def meta_path(self, conv_id: str) -> Path:
        return self._conv_dir(conv_id) / _META_NAME

    def transcript_path(self, conv_id: str) -> Path:
        return self._conv_dir(conv_id) / _TRANSCRIPT_NAME

    @property
    def _index_path(self) -> Path:
        return self.root / _INDEX_NAME

    @property
    def _active_path(self) -> Path:
        return self.root / _ACTIVE_NAME

    # --- Index (derived cache, self-healing) ---

    def list_conversations(self) -> list[Conversation]:
        """Return all conversations sorted (pinned-first, then recency-desc).

        Tries the cached index first. On miss/parse-failure, rebuilds from
        per-conversation meta.json files and writes a fresh index.
        """
        cached = self._load_index()
        if cached is not None:
            return cached
        rebuilt = self._rebuild_index()
        try:
            self._write_index(rebuilt)
        except Exception:
            pass
        return rebuilt

    def _load_index(self) -> list[Conversation] | None:
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except Exception:
            return None
        if not isinstance(data, list):
            return None
        out: list[Conversation] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                out.append(Conversation.from_dict(item))
            except Exception:
                continue
        return _sort_for_display(out)

    def _rebuild_index(self) -> list[Conversation]:
        out: list[Conversation] = []
        if not self.root.exists():
            return out
        for child in self.root.iterdir():
            if not child.is_dir():
                continue
            meta = child / _META_NAME
            if not meta.exists():
                continue
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                out.append(Conversation.from_dict(data))
            except Exception:
                continue
        return _sort_for_display(out)

    def _write_index(self, convs: Iterable[Conversation]) -> None:
        atomic_write_json(self._index_path, [c.to_dict() for c in convs])

    def _index_upsert(self, conv: Conversation) -> None:
        """Insert-or-replace `conv` in index.json. O(1) disk reads.

        On parse failure of the cached index, falls back to full rebuild —
        slower, but only the once-per-corruption recovery path pays for it.
        """
        cached = self._load_index()
        if cached is None:
            self._write_index(self._rebuild_index())
            return
        replaced = False
        for i, c in enumerate(cached):
            if c.id == conv.id:
                cached[i] = conv
                replaced = True
                break
        if not replaced:
            cached.append(conv)
        self._write_index(_sort_for_display(cached))

    def _index_remove(self, conv_id: str) -> None:
        cached = self._load_index()
        if cached is None:
            self._write_index(self._rebuild_index())
            return
        cached = [c for c in cached if c.id != conv_id]
        self._write_index(cached)

    # --- CRUD ---

    def get(self, conv_id: str) -> Conversation | None:
        try:
            data = json.loads(self.meta_path(conv_id).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except Exception:
            return None
        try:
            return Conversation.from_dict(data)
        except Exception:
            return None

    def write_meta(self, conv: Conversation) -> None:
        """Public: migration uses this for bulk imports."""
        atomic_write_json(self.meta_path(conv.id), conv.to_dict())

    def create(
        self,
        *,
        backend_kind: str,
        model: str,
        title: str = DEFAULT_TITLE,
        session_id: str = "",
        conv_id: str | None = None,
    ) -> Conversation:
        cid = conv_id or uuid.uuid4().hex
        now = now_iso()
        conv = Conversation(
            id=cid,
            title=truncate(title, TITLE_MAX) or DEFAULT_TITLE,
            created_at=now,
            last_used_at=now,
            backend_kind=backend_kind,
            model=model,
            session_id=session_id,
            message_count=0,
            preview="",
        )
        self._conv_dir(cid).mkdir(parents=True, exist_ok=True)
        self.write_meta(conv)
        self._index_upsert(conv)
        return conv

    def rename(self, conv_id: str, title: str) -> Conversation | None:
        conv = self.get(conv_id)
        if conv is None:
            return None
        conv.title = truncate(title, TITLE_MAX) or DEFAULT_TITLE
        self.write_meta(conv)
        self._index_upsert(conv)
        return conv

    def set_pinned(self, conv_id: str, pinned: bool) -> Conversation | None:
        conv = self.get(conv_id)
        if conv is None:
            return None
        conv.pinned = bool(pinned)
        self.write_meta(conv)
        self._index_upsert(conv)
        return conv

    def update_session_id(self, conv_id: str, new_session_id: str) -> Conversation | None:
        conv = self.get(conv_id)
        if conv is None:
            return None
        conv.session_id = new_session_id or ""
        self.write_meta(conv)
        self._index_upsert(conv)
        return conv

    def update_model(self, conv_id: str, model: str) -> Conversation | None:
        conv = self.get(conv_id)
        if conv is None:
            return None
        conv.model = model
        self.write_meta(conv)
        self._index_upsert(conv)
        return conv

    def delete(self, conv_id: str) -> bool:
        d = self._conv_dir(conv_id)
        if not d.exists():
            return False
        for p in (self.transcript_path(conv_id), self.meta_path(conv_id)):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        try:
            d.rmdir()
        except OSError:
            for stray in list(d.iterdir()):
                try:
                    stray.unlink()
                except Exception:
                    pass
            try:
                d.rmdir()
            except Exception:
                pass
        if self.get_active_id() == conv_id:
            self.set_active(None)
        self._index_remove(conv_id)
        return True

    # --- Transcript + message append ---

    def append_message(self, conv_id: str, msg: Message) -> Conversation | None:
        conv = self.get(conv_id)
        if conv is None:
            return None
        writer = TranscriptWriter(self.transcript_path(conv_id))
        try:
            writer.append(msg)
        finally:
            writer.close()
        conv.last_used_at = msg.ts or now_iso()
        conv.message_count += 1
        if msg.role in ("user", "llsm") and msg.text:
            conv.preview = truncate(msg.text, PREVIEW_MAX)
        # Auto-title from the first user message.
        # Recognise both the bare default and provider-tagged variants
        # (e.g. "New chat (OpenAI)") so cross-provider auto-new-chats also
        # get titled from their first user turn.
        is_default_title = (
            conv.title == DEFAULT_TITLE
            or (conv.title.startswith(DEFAULT_TITLE + " (") and conv.title.endswith(")"))
        )
        if is_default_title and msg.role == "user" and msg.text:
            conv.title = truncate(msg.text, TITLE_MAX)
        self.write_meta(conv)
        self._index_upsert(conv)
        return conv

    def read_transcript(self, conv_id: str) -> list[Message]:
        return list(read_transcript(self.transcript_path(conv_id)))

    def touch(self, conv_id: str) -> Conversation | None:
        conv = self.get(conv_id)
        if conv is None:
            return None
        conv.last_used_at = now_iso()
        self.write_meta(conv)
        self._index_upsert(conv)
        return conv

    # --- Active pointer ---

    def get_active_id(self) -> str | None:
        try:
            data = json.loads(self._active_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        cid = data.get("active_conversation_id")
        return cid if isinstance(cid, str) and cid else None

    def get_active(self) -> Conversation | None:
        cid = self.get_active_id()
        if cid is None:
            return None
        return self.get(cid)

    def set_active(self, conv_id: str | None) -> None:
        if conv_id is None:
            try:
                self._active_path.unlink(missing_ok=True)
            except Exception:
                pass
            return
        atomic_write_json(self._active_path, {"active_conversation_id": conv_id})
