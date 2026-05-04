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

import json as _json
import threading
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from ..scia.tools import LlsmContext
from .base import CancelledError
from .credentials import get_credential
from .tool_loop import (
    MAX_TOOL_ITERATIONS,
    invoke_tool,
    to_openai_tools,
    extract_openai_tool_calls,
    format_openai_tool_result,
)


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


_SYSTEM_PROMPT_CHAT_ONLY = (
    "You are LLSM, an AI assistant for structural engineers using SCIA Engineer. "
    "In this conversation you are running in chat-only mode — you cannot drive "
    "SCIA directly. Answer the user's questions clearly and concisely."
)

_SYSTEM_PROMPT_WITH_TOOLS = (
    "You are LLSM, an AI assistant that drives SCIA Engineer to build and "
    "analyse structural models from natural-language requests. You have SCIA "
    "tools available. Workflow: new_model -> add_material -> "
    "add_rectangular_section -> add_node(s) -> add_beam(s) -> add_*_support "
    "-> add_load_group -> add_load_case -> add_line_load -> launch_scia -> "
    "push_model -> run_analysis -> close_scia. "
    "Units: lengths in mm; line loads in kN/m; Z is vertical (up positive); "
    "for gravity-like downward loads use direction='Z' with NEGATIVE magnitude."
)


@dataclass
class OpenAIBackend:
    api_key: str | None = None
    model: str = DEFAULT_MODEL
    max_tokens: int = 4096
    verbose: bool = True
    on_tool_event: Any = None
    # Phase B: when True, the tool loop is wired in. When False, behaves
    # exactly like Phase A (single non-streaming-text completion).
    tools_enabled: bool = False

    _client: OpenAI = field(init=False)
    _messages: list[dict] = field(default_factory=list, init=False)
    _cancelled: threading.Event = field(default_factory=threading.Event, init=False)
    _current_stream: Any = field(default=None, init=False)
    _ctx: LlsmContext = field(default_factory=LlsmContext, init=False)

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
        """Tool-aware chat loop (Phase B). With tools_enabled=False, runs
        exactly one non-streaming-but-streamed completion (Phase A behavior)."""
        self._cancelled.clear()
        self._messages.append({"role": "user", "content": user_message})

        if not self.tools_enabled:
            return self._chat_no_tools()

        # Tool-use loop. We don't stream here — OpenAI streaming + tool_calls
        # is messier (needs accumulating function arguments across chunks)
        # and the panel already runs chat() on a worker so blocking is fine.
        # Cancel-mid-call still works because we check _cancelled between
        # the request return and the tool dispatch.
        for _ in range(MAX_TOOL_ITERATIONS):
            if self._cancelled.is_set():
                raise CancelledError()
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT_WITH_TOOLS},
                        *self._messages,
                    ],
                    tools=to_openai_tools(),
                )
            except Exception as exc:
                raise RuntimeError(f"OpenAI request failed: {type(exc).__name__}: {exc}") from exc
            if self._cancelled.is_set():
                raise CancelledError()

            choice = resp.choices[0]
            msg = choice.message
            calls = extract_openai_tool_calls(msg)

            # Append the assistant's message verbatim so the next turn sees
            # the same tool_calls IDs we'll respond to.
            assistant_msg = {
                "role": "assistant",
                "content": msg.content or "",
            }
            if calls:
                assistant_msg["tool_calls"] = [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name, "arguments": _json.dumps(c.arguments)}}
                    for c in calls
                ]
            self._messages.append(assistant_msg)

            if not calls:
                return (msg.content or "").strip()

            for tc in calls:
                if self._cancelled.is_set():
                    raise CancelledError()
                result = invoke_tool(
                    tc.name, tc.arguments, self._ctx,
                    self.on_tool_event, verbose=self.verbose,
                )
                self._messages.append(format_openai_tool_result(tc.id, result))

        return "[ERROR] Hit max tool-use iterations without a final response."

    def _chat_no_tools(self) -> str:
        """Phase A behavior: single streaming completion, no tools."""
        if self._cancelled.is_set():
            raise CancelledError()

        try:
            stream = self._client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT_CHAT_ONLY},
                    *self._messages,
                ],
                stream=True,
            )
        except Exception as exc:
            raise RuntimeError(f"OpenAI request failed: {type(exc).__name__}: {exc}") from exc

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
        """When tools are enabled, surface the live ADM snapshot. Otherwise
        return the empty 8-key shape the sidebar expects."""
        empty = {k: [] for k in (
            "materials", "sections", "nodes", "beams",
            "supports", "load_groups", "load_cases", "loads",
        )}
        if not self.tools_enabled:
            return empty
        builder = getattr(self._ctx, "builder", None)
        if builder is None:
            return empty
        try:
            return builder.snapshot()
        except Exception:
            return empty

    def close(self) -> None:
        try:
            self._ctx.close()
        except Exception:
            pass
        try:
            self._client.close()
        except Exception:
            pass


# ---------- Registry hooks (CR-5) ----------

def build_backend(panel) -> OpenAIBackend:
    """Panel-coupled factory used by `providers._resolve(...)`.

    Reads the active conversation's model and tools_enabled flag, plus the
    panel's tool-event callback.
    """
    cur = panel._conv_session.current()
    model = (cur.model if cur else None) or panel._model or DEFAULT_MODEL
    tools_enabled = bool(getattr(cur, "tools_enabled", False)) if cur else False
    backend = OpenAIBackend(
        model=model,
        verbose=False,
        on_tool_event=panel._on_tool_event_from_worker,
        tools_enabled=tools_enabled,
    )
    if cur is not None:
        try:
            backend.restore_messages(panel._conv_store.read_transcript(cur.id))
        except Exception as exc:
            import sys
            print(f"[panel] openai restore_messages failed: {exc}", file=sys.stderr)
    return backend
