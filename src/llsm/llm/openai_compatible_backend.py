"""OpenAI-compatible backend (Ollama, LM Studio, vLLM, OpenRouter, ...).

One backend covers any server that speaks the OpenAI Chat Completions API.
The user configures `base_url` (required) and an optional `api_key` (some
local servers accept anything; cloud-hosted services like OpenRouter / Groq
require a real key).

Phase A scope: chat-only. Phase B will wire SCIA tools, defaulting to OFF
for this backend because small local models often misuse tool calls
(`ProviderSpec.default_tools_enabled = False`).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from openai import OpenAI

from .base import CancelledError
from .credentials import get_credential


# No DEFAULT_MODEL — there is nothing universal here. Caller must set one.
_FALLBACK_MODEL = "local-model"


def available_models():
    """Static fallback for the catalog when no live backend exists.

    Empty list — there's nothing universal across all OpenAI-compatible
    servers. The menu falls back to "(no models — set up credentials first)".
    """
    return []


_PHASE_A_SYSTEM_PROMPT = (
    "You are LLSM, an AI assistant for structural engineers using SCIA Engineer. "
    "In this conversation you are running in chat-only mode — you cannot drive "
    "SCIA directly. Answer the user's questions clearly and concisely."
)


def _host_label(base_url: str) -> str:
    """Short tag like '[ollama]' or '[localhost:1234]' for ModelRef display."""
    try:
        host = urlparse(base_url).hostname or "local"
    except Exception:
        host = "local"
    if host in ("localhost", "127.0.0.1"):
        # Try to disambiguate Ollama (default port 11434) vs LM Studio (1234)
        # vs other local servers via port.
        try:
            port = urlparse(base_url).port
        except Exception:
            port = None
        if port == 11434:
            return "ollama"
        if port == 1234:
            return "lmstudio"
        return f"local:{port}" if port else "local"
    return host


@dataclass
class OpenAICompatibleBackend:
    base_url: str = ""
    api_key: str | None = None
    model: str = _FALLBACK_MODEL
    max_tokens: int = 4096
    verbose: bool = True
    on_tool_event: Any = None

    _client: OpenAI = field(init=False)
    _messages: list[dict] = field(default_factory=list, init=False)
    _cancelled: threading.Event = field(default_factory=threading.Event, init=False)
    _current_stream: Any = field(default=None, init=False)
    _host_label: str = field(default="", init=False)

    def __post_init__(self) -> None:
        base_url = self.base_url or get_credential("openai-compatible", "base_url") or ""
        if not base_url:
            raise RuntimeError(
                "OpenAI-compatible backend needs a base_url. "
                "Use Tools -> Set up credentials -> OpenAI-compatible."
            )
        # api_key is optional; many local servers accept any value.
        api_key = self.api_key or get_credential("openai-compatible", "api_key") or "not-needed"
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self.base_url = base_url
        self._host_label = _host_label(base_url)

    def chat(self, user_message: str) -> str:
        self._cancelled.clear()
        self._messages.append({"role": "user", "content": user_message})

        if self._cancelled.is_set():
            raise CancelledError()

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
            raise RuntimeError(
                f"{self._host_label} request failed: {type(exc).__name__}: {exc}"
            ) from exc

        self._current_stream = stream
        parts: list[str] = []
        try:
            for chunk in stream:
                if self._cancelled.is_set():
                    raise CancelledError()
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
        """Live `/v1/models` — the only source for this backend."""
        try:
            resp = self._client.models.list()
            from ..history.models import ModelRef
            out: list[ModelRef] = []
            label = self._host_label
            for m in resp.data:
                mid = getattr(m, "id", "")
                if not mid:
                    continue
                out.append(ModelRef(
                    "openai-compatible",
                    mid,
                    display_name=f"[{label}] {mid}",
                ))
            return out
        except Exception:
            return []

    def restore_messages(self, transcript) -> None:
        msgs = []
        for m in transcript:
            role = "user" if m.role == "user" else "assistant" if m.role == "llsm" else None
            if role is None or not m.text:
                continue
            msgs.append({"role": role, "content": m.text})
        self._messages = msgs

    def model_snapshot(self) -> dict:
        return {k: [] for k in (
            "materials", "sections", "nodes", "beams",
            "supports", "load_groups", "load_cases", "loads",
        )}


def build_backend(panel) -> OpenAICompatibleBackend:
    cur = panel._conv_session.current()
    model = (cur.model if cur else None) or panel._model or _FALLBACK_MODEL
    backend = OpenAICompatibleBackend(
        model=model,
        verbose=False,
        on_tool_event=panel._on_tool_event_from_worker,
    )
    if cur is not None:
        try:
            backend.restore_messages(panel._conv_store.read_transcript(cur.id))
        except Exception as exc:
            import sys
            print(f"[panel] openai-compatible restore_messages failed: {exc}", file=sys.stderr)
    return backend
