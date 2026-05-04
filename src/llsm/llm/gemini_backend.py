"""Google Gemini chat backend (chat-only in Phase A; tool-use in Phase B).

Uses the new `google-genai` SDK (`from google import genai`). Streaming mode
is used internally so cancel can break the response iteration cheaply (CR-3).
`chat()` returns a single concatenated string.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from google import genai

from .base import CancelledError
from .credentials import get_credential


DEFAULT_MODEL = "gemini-2.5-pro"


def available_models():
    """Single source of truth for the gemini backend's static model list."""
    from ..history.models import ModelRef
    return [
        ModelRef("gemini", "gemini-2.5-pro",   display_name="Gemini 2.5 Pro"),
        ModelRef("gemini", "gemini-2.5-flash", display_name="Gemini 2.5 Flash"),
    ]


_PHASE_A_SYSTEM_INSTRUCTION = (
    "You are LLSM, an AI assistant for structural engineers using SCIA Engineer. "
    "In this conversation you are running in chat-only mode — you cannot drive "
    "SCIA directly. Answer the user's questions clearly and concisely. "
    "If they ask you to build or analyse a model, point them to the Anthropic "
    "API or Claude Code backend in the Switch Model menu."
)


@dataclass
class GeminiBackend:
    api_key: str | None = None
    model: str = DEFAULT_MODEL
    max_tokens: int = 4096
    verbose: bool = True
    on_tool_event: Any = None

    _client: Any = field(init=False)
    # Gemini messages are a list of {"role": "user"|"model", "parts": [...]} dicts.
    _messages: list[dict] = field(default_factory=list, init=False)
    _cancelled: threading.Event = field(default_factory=threading.Event, init=False)
    _stream_active: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        key = self.api_key or get_credential("gemini", "api_key")
        if not key:
            raise RuntimeError(
                "GOOGLE_API_KEY not set. Use Tools -> Set up credentials, or set "
                "GOOGLE_API_KEY / GEMINI_API_KEY env vars."
            )
        self._client = genai.Client(api_key=key)

    def chat(self, user_message: str) -> str:
        self._cancelled.clear()
        self._messages.append({"role": "user", "parts": [{"text": user_message}]})

        if self._cancelled.is_set():
            raise CancelledError()

        try:
            # Use the streaming form so cancel can break the iteration.
            stream = self._client.models.generate_content_stream(
                model=self.model,
                contents=self._messages,
                config={"system_instruction": _PHASE_A_SYSTEM_INSTRUCTION},
            )
        except Exception as exc:
            raise RuntimeError(f"Gemini request failed: {type(exc).__name__}: {exc}") from exc

        self._stream_active = True
        parts: list[str] = []
        try:
            for chunk in stream:
                if self._cancelled.is_set():
                    raise CancelledError()
                t = getattr(chunk, "text", None)
                if t:
                    parts.append(t)
        finally:
            self._stream_active = False

        full = "".join(parts).strip()
        self._messages.append({"role": "model", "parts": [{"text": full}]})
        return full

    def cancel(self) -> bool:
        was_running = self._stream_active
        self._cancelled.set()
        # Gemini's stream doesn't expose a close() handle; the cancelled flag
        # check on the next chunk breaks the loop. Mid-call abort is best-
        # effort, similar to Anthropic's between-iteration semantics.
        return was_running

    def close(self) -> None:
        # The genai Client doesn't have an explicit close; nothing persistent to clean up.
        pass

    def available_models(self):
        try:
            ids: list[str] = []
            for m in self._client.models.list():
                mid = getattr(m, "name", "")
                if not mid:
                    continue
                # `models/gemini-2.5-pro` -> `gemini-2.5-pro`
                short = mid.split("/", 1)[-1].lower()
                if not short.startswith("gemini-"):
                    continue
                ids.append(short)
            if not ids:
                return available_models()
            ids.sort(reverse=True)
            from ..history.models import ModelRef
            return [ModelRef("gemini", mid, display_name=mid) for mid in ids]
        except Exception:
            return available_models()

    def restore_messages(self, transcript) -> None:
        """Replay user/llsm text only (Gemini's role names are 'user'/'model')."""
        msgs = []
        for m in transcript:
            role = "user" if m.role == "user" else "model" if m.role == "llsm" else None
            if role is None or not m.text:
                continue
            msgs.append({"role": role, "parts": [{"text": m.text}]})
        self._messages = msgs

    def model_snapshot(self) -> dict:
        return {k: [] for k in (
            "materials", "sections", "nodes", "beams",
            "supports", "load_groups", "load_cases", "loads",
        )}


def build_backend(panel) -> GeminiBackend:
    cur = panel._conv_session.current()
    model = (cur.model if cur else None) or panel._model or DEFAULT_MODEL
    backend = GeminiBackend(
        model=model,
        verbose=False,
        on_tool_event=panel._on_tool_event_from_worker,
    )
    if cur is not None:
        try:
            backend.restore_messages(panel._conv_store.read_transcript(cur.id))
        except Exception as exc:
            import sys
            print(f"[panel] gemini restore_messages failed: {exc}", file=sys.stderr)
    return backend
