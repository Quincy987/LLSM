"""Thin Tk adapter for ConversationSession.

The panel owns the chat history widget and status-bar StringVar; this
module knows how to:
  - replay a transcript iterator into the widget
  - render the active-conversation title + model into the status bar,
    including an `(unavailable)` badge when the model has left the catalog.

Kept tiny on purpose so the headless ConversationSession stays the
testable seam. No business logic here — just rendering.
"""
from __future__ import annotations

from typing import Callable, Iterable

from ..history.models import Message


class ConversationView:
    def __init__(
        self,
        *,
        session,
        clear_history: Callable[[], None],
        render_message: Callable[[str, str, str], None],  # (role, text, ts_iso)
        set_status_title: Callable[[str], None],
    ) -> None:
        self.session = session
        self._clear = clear_history
        self._render = render_message
        self._set_status_title = set_status_title

    def replay(self, msgs: Iterable[Message]) -> None:
        self._clear()
        for m in msgs:
            self._render(m.role, m.text, m.ts)

    def refresh_status_title(self, *, model_available: bool = True) -> None:
        cur = self.session.current()
        if cur is None:
            self._set_status_title("")
            return
        suffix = "" if model_available else "  (unavailable)"
        title = f"{cur.title}  ·  {cur.model}{suffix}"
        self._set_status_title(title)
