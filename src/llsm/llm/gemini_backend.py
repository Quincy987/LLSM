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

from ..scia.tools import LlsmContext
from .base import CancelledError
from .credentials import get_credential
from .tool_loop import (
    MAX_TOOL_ITERATIONS,
    invoke_tool,
    to_gemini_tools,
    extract_gemini_tool_calls,
    format_gemini_tool_result,
    format_gemini_assistant_with_tool_calls,
)


DEFAULT_MODEL = "gemini-2.5-pro"


def available_models():
    """Single source of truth for the gemini backend's static model list."""
    from ..history.models import ModelRef
    return [
        ModelRef("gemini", "gemini-2.5-pro",   display_name="Gemini 2.5 Pro"),
        ModelRef("gemini", "gemini-2.5-flash", display_name="Gemini 2.5 Flash"),
    ]


_SYSTEM_INSTRUCTION_CHAT_ONLY = (
    "You are LLSM, an AI assistant for structural engineers using SCIA Engineer. "
    "In this conversation you are running in chat-only mode — you cannot drive "
    "SCIA directly. Answer the user's questions clearly and concisely."
)

_SYSTEM_INSTRUCTION_WITH_TOOLS = (
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
class GeminiBackend:
    api_key: str | None = None
    model: str = DEFAULT_MODEL
    max_tokens: int = 4096
    verbose: bool = True
    on_tool_event: Any = None
    tools_enabled: bool = False

    _client: Any = field(init=False)
    _messages: list[dict] = field(default_factory=list, init=False)
    _cancelled: threading.Event = field(default_factory=threading.Event, init=False)
    _stream_active: bool = field(default=False, init=False)
    _ctx: LlsmContext = field(default_factory=LlsmContext, init=False)

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

        if not self.tools_enabled:
            return self._chat_no_tools()
        return self._chat_with_tools()

    def _chat_no_tools(self) -> str:
        if self._cancelled.is_set():
            raise CancelledError()
        try:
            stream = self._client.models.generate_content_stream(
                model=self.model,
                contents=self._messages,
                config={"system_instruction": _SYSTEM_INSTRUCTION_CHAT_ONLY},
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

    def _chat_with_tools(self) -> str:
        for _ in range(MAX_TOOL_ITERATIONS):
            if self._cancelled.is_set():
                raise CancelledError()
            try:
                resp = self._client.models.generate_content(
                    model=self.model,
                    contents=self._messages,
                    config={
                        "system_instruction": _SYSTEM_INSTRUCTION_WITH_TOOLS,
                        "tools": to_gemini_tools(),
                    },
                )
            except Exception as exc:
                raise RuntimeError(f"Gemini request failed: {type(exc).__name__}: {exc}") from exc
            if self._cancelled.is_set():
                raise CancelledError()

            calls = extract_gemini_tool_calls(resp)

            # Append the assistant's full content as-is so the next turn's
            # function_response correlates by name.
            assistant_content = format_gemini_assistant_with_tool_calls(resp)
            if assistant_content is not None:
                self._messages.append(assistant_content)

            if not calls:
                # Pull plain text out of the response.
                try:
                    return (resp.text or "").strip()
                except Exception:
                    return ""

            for tc in calls:
                if self._cancelled.is_set():
                    raise CancelledError()
                result = invoke_tool(
                    tc.name, tc.arguments, self._ctx,
                    self.on_tool_event, verbose=self.verbose,
                )
                # Gemini wants tool results in a "user" role with function_response parts.
                self._messages.append({
                    "role": "user",
                    "parts": [format_gemini_tool_result(tc.name, result)],
                })

        return "[ERROR] Hit max tool-use iterations without a final response."

    def cancel(self) -> bool:
        was_running = self._stream_active
        self._cancelled.set()
        # Gemini's stream doesn't expose a close() handle; the cancelled flag
        # check on the next chunk breaks the loop. Mid-call abort is best-
        # effort, similar to Anthropic's between-iteration semantics.
        return was_running

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

    def close(self) -> None:
        try:
            self._ctx.close()
        except Exception:
            pass

    def model_snapshot(self) -> dict:
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


def build_backend(panel) -> GeminiBackend:
    cur = panel._conv_session.current()
    model = (cur.model if cur else None) or panel._model or DEFAULT_MODEL
    tools_enabled = bool(getattr(cur, "tools_enabled", False)) if cur else False
    backend = GeminiBackend(
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
            print(f"[panel] gemini restore_messages failed: {exc}", file=sys.stderr)
    return backend
