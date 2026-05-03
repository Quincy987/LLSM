"""Headless conversation session — pure logic, no Tk.

Owns "active conversation" state and the pure-data side of switching:
cancel-in-flight (delegated to a callable), persist current state, swap
the store's active pointer, and return a transcript iterator that the Tk
view consumes for replay.

Anything that touches a Tk widget belongs in conversation.view, not here.
"""
from __future__ import annotations

from typing import Callable, Iterable

from ..history import ConversationStore, ModelUsage, ModelCatalog
from ..history.models import Conversation, Message


class ConversationSession:
    def __init__(
        self,
        *,
        store: ConversationStore,
        usage: ModelUsage,
        catalog: ModelCatalog,
        cancel_in_flight: Callable[[], None] = lambda: None,
    ) -> None:
        self.store = store
        self.usage = usage
        self.catalog = catalog
        self._cancel = cancel_in_flight

    # --- Active conversation queries ---

    def current(self) -> Conversation | None:
        return self.store.get_active()

    def current_id(self) -> str | None:
        return self.store.get_active_id()

    # --- Mutations ---

    def ensure_active(self, *, backend_kind: str, model: str) -> Conversation:
        """Return the active conversation, creating one if none exists.

        Used at panel start when migration has nothing to import.
        """
        cur = self.store.get_active()
        if cur is not None:
            return cur
        conv = self.store.create(backend_kind=backend_kind, model=model)
        self.store.set_active(conv.id)
        self.usage.record(backend_kind, model)
        return conv

    def new_conversation(self, *, backend_kind: str, model: str) -> Conversation:
        """Cancel anything in flight, create a fresh conversation, set active."""
        try:
            self._cancel()
        except Exception:
            pass
        conv = self.store.create(backend_kind=backend_kind, model=model)
        self.store.set_active(conv.id)
        self.usage.record(backend_kind, model)
        return conv

    def switch_to(self, conv_id: str) -> tuple[Conversation, Iterable[Message]]:
        """Cancel anything in flight, set active to conv_id, return (conv, transcript_iter).

        Caller (the Tk view) is responsible for replaying the transcript into
        the chat widget and rebuilding the backend with the conversation's
        backend_kind/model/session_id.
        """
        try:
            self._cancel()
        except Exception:
            pass
        conv = self.store.get(conv_id)
        if conv is None:
            raise KeyError(f"conversation {conv_id!r} not found")
        self.store.set_active(conv.id)
        self.store.touch(conv.id)
        self.usage.record(conv.backend_kind, conv.model)
        msgs = self.store.read_transcript(conv.id)
        return conv, msgs

    # --- Append message + book-keeping ---

    def _record(self, role: str, text: str) -> None:
        cid = self.store.get_active_id()
        if cid is None:
            return
        self.store.append_message(cid, Message(role=role, text=text))

    def record_user(self, text: str) -> None:      self._record("user", text)
    def record_assistant(self, text: str) -> None: self._record("llsm", text)
    def record_error(self, text: str) -> None:     self._record("error", text)

    def update_session_id(self, new_session_id: str) -> None:
        cid = self.store.get_active_id()
        if cid is None:
            return
        self.store.update_session_id(cid, new_session_id)

    def update_model(self, new_model: str) -> None:
        cid = self.store.get_active_id()
        if cid is None:
            return
        cur = self.store.get(cid)
        if cur is None:
            return
        self.store.update_model(cid, new_model)
        self.usage.record(cur.backend_kind, new_model)

    # --- Catalog awareness ---

    def is_active_model_available(self, backend) -> bool:
        cur = self.store.get_active()
        if cur is None:
            return True
        # Always prime the catalog with the live backend BEFORE checking,
        # so a defaults-populated cache from an earlier `contains()` call
        # can't shadow the real backend's list.
        models = self.catalog.for_backend(cur.backend_kind, backend)
        return any(m.model_id == cur.model for m in models)

    def title(self) -> str:
        cur = self.store.get_active()
        return cur.title if cur is not None else ""
