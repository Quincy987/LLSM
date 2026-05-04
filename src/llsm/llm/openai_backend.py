"""OpenAI chat backend (chat-only in Phase A; tool-use lands in Phase B).

The SDK is imported at module top-level INSIDE this module — `providers.py`
only loads this module when the openai spec is actually resolved (CR-1), so
a default-install user without `pip install llsm[openai]` never triggers the
import.

Streaming mode is used internally so cancel can close the in-flight HTTP
response cheaply (CR-3). `chat()` still returns a single concatenated string
to keep the panel's worker-thread + cancel logic unchanged.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from .base import CancelledError
from .credentials import get_credential


DEFAULT_MODEL = "gpt-5"


# Curated static fallback. Live `available_models()` is preferred when
# credentials exist; this list keeps the menu populated when they don't.
def available_models():
    """Single source of truth for the openai backend's static model list.

    ModelCatalog imports this when no live backend is around to query.
    """
    from ..history.models import ModelRef
    return [
        ModelRef("openai", "gpt-5",      display_name="GPT-5"),
        ModelRef("openai", "gpt-4.1",    display_name="GPT-4.1"),
        ModelRef("openai", "o4-mini",    display_name="o4-mini"),
    ]


_PHASE_A_SYSTEM_PROMPT = (
    "You are LLSM, an AI assistant for structural engineers using SCIA Engineer. "
    "In this conversation you are running in chat-only mode — you cannot drive "
    "SCIA directly. Answer the user's questions clearly and concisely. "
    "If they ask you to build or analyse a model, point them to the Anthropic "
    "API or Claude Code backend in the Switch Model menu (those have SCIA "
    "tool-use enabled)."
)


@dataclass
class OpenAIBackend:
    api_key: str | None = None
    model: str = DEFAULT_MODEL
    max_tokens: int = 4096
    verbose: bool = True
    on_tool_event: Any = None  # kept for Phase B parity; unused here

    _client: OpenAI = field(init=False)
    _messages: list[dict] = field(default_factory=list, init=False)
    _cancelled: threading.Event = field(default_factory=threading.Event, init=False)
    _current_stream: Any = field(default=None, init=False)

    def __post_init__(self) -> None:
        key = self.api_key or get_credential("openai", "api_key")
        if not key:
            raise RuntimeError(
                "OPENAI_API_KEY not set. Use Tools -> Set up credentials, or set "
                "the OPENAI_API_KEY env var."
            )
        self._client = OpenAI(api_key=key)

    # --- Public entrypoint -------------------------------------------------

    def chat(self, user_message: str) -> str:
        """Single-call chat (no tool loop in Phase A)."""
        self._cancelled.clear()
        self._messages.append({"role": "user", "content": user_message})

        if self._cancelled.is_set():
            raise CancelledError()

        # OpenAI's streaming context manager closes the underlying HTTPX
        # stream on __exit__, so cancel() can break the iteration safely.
        try:
            stream = self._client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[
                    {"role": "system", "content": _PHASE_A_SYSTEM_PROMPT},
                    *self._messages,
                ],
                stream=True,
            )
        except Exception as exc:
            # Re-raise as a plain RuntimeError so the panel surfaces a clean
            # message instead of leaking the openai SDK's exception hierarchy.
            raise RuntimeError(f"OpenAI request failed: {type(exc).__name__}: {exc}") from exc

        self._current_stream = stream
        parts: list[str] = []
        try:
            for chunk in stream:
                if self._cancelled.is_set():
                    raise CancelledError()
                # delta layout: choices[0].delta.content (str | None)
                try:
                    delta = chunk.choices[0].delta
                    text = getattr(delta, "content", None)
                except Exception:
                    text = None
                if text:
                    parts.append(text)
        finally:
            self._current_stream = None
            try:
                stream.close()
            except Exception:
                pass

        full = "".join(parts).strip()
        self._messages.append({"role": "assistant", "content": full})
        return full

    def cancel(self) -> bool:
        """Set the cancel flag and close any active streaming response.

        Returns True iff a chat was actively running (mid-stream). The
        streaming HTTP response is closed so the worker thread breaks out
        of its iterator within ~1 chunk.
        """
        was_running = self._current_stream is not None
        self._cancelled.set()
        s = self._current_stream
        if s is not None:
            try:
                s.close()
            except Exception:
                pass
        return was_running

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    def available_models(self):
        """Live `/v1/models` filtered to chat-capable, with static fallback."""
        try:
            resp = self._client.models.list()
            ids: list[str] = []
            for m in resp.data:
                mid = getattr(m, "id", "")
                if not mid:
                    continue
                # Filter to chat-capable families. OpenAI's /v1/models also
                # returns embedding / audio / image models that we can't chat with.
                low = mid.lower()
                if not any(low.startswith(p) for p in ("gpt-", "o1", "o3", "o4", "chatgpt-")):
                    continue
                ids.append(mid)
            if not ids:
                return available_models()
            ids.sort(reverse=True)  # newest-ish first by lex
            from ..history.models import ModelRef
            return [ModelRef("openai", mid, display_name=mid) for mid in ids]
        except Exception:
            return available_models()

    def restore_messages(self, transcript) -> None:
        """Replay user/llsm text only (tool blocks stripped — Phase A scope)."""
        msgs = []
        for m in transcript:
            role = "user" if m.role == "user" else "assistant" if m.role == "llsm" else None
            if role is None or not m.text:
                continue
            msgs.append({"role": role, "content": m.text})
        self._messages = msgs

    def model_snapshot(self) -> dict:
        """No SCIA in Phase A — return the empty 8-key shape the sidebar expects."""
        return {k: [] for k in (
            "materials", "sections", "nodes", "beams",
            "supports", "load_groups", "load_cases", "loads",
        )}


# ---------- Registry hooks (CR-5) ----------

def build_backend(panel) -> OpenAIBackend:
    """Panel-coupled factory used by `providers._resolve(...)`.

    Reads the active conversation's model and the panel's tool-event callback
    (which is unused in Phase A but threaded through for Phase B parity).
    """
    cur = panel._conv_session.current()
    model = (cur.model if cur else None) or panel._model or DEFAULT_MODEL
    backend = OpenAIBackend(
        model=model,
        verbose=False,
        on_tool_event=panel._on_tool_event_from_worker,
    )
    if cur is not None:
        try:
            backend.restore_messages(panel._conv_store.read_transcript(cur.id))
        except Exception as exc:
            import sys
            print(f"[panel] openai restore_messages failed: {exc}", file=sys.stderr)
    return backend
