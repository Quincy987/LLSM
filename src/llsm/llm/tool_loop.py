"""Provider-neutral tool-use plumbing.

Owns:
- `MAX_TOOL_ITERATIONS` (single source of truth — Anthropic + the new
  backends import from here, not from anthropic_backend).
- `invoke_tool(name, args, ctx, on_tool_event)` — dispatches to the
  `llsm.scia.tools` functions, fires the panel's tool-event callbacks,
  wraps unknown tools / exceptions as `{"ok": False, "error": ...}`.
- Provider-format adapters (Phase B):
    `to_openai_tools()`, `to_gemini_tools()`,
    `extract_openai_tool_calls(message)`,
    `extract_gemini_tool_calls(response)`,
    `format_openai_tool_result(...)`, `format_gemini_tool_result(...)`.
  These import their respective SDK types lazily (inside the function)
  so importing tool_loop.py stays SDK-free at module load — same
  discipline as `providers.py` (CR-1).

Anthropic-format adapters are *not* needed: AnthropicBackend uses
TOOL_DEFS directly (those are already in Anthropic's
`{"name", "description", "input_schema"}` shape).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ..scia import tools as scia_tools
from .tools import TOOL_DEFS, TOOL_NAMES


# Single source of truth for how many tool-loop iterations a chat() can
# burn before bailing out. Imported by anthropic_backend, openai_backend,
# gemini_backend, openai_compatible_backend.
MAX_TOOL_ITERATIONS = 40


# Internal neutral representation of one tool call. id may be empty for
# providers that don't track call ids (Gemini doesn't expose one at the
# `function_call` level — we generate a stable name to thread it through).
@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


# ---------- Tool dispatch (was AnthropicBackend._invoke_tool) ----------

def invoke_tool(
    name: str,
    args: dict,
    ctx,
    on_tool_event: Optional[Callable] = None,
    *,
    verbose: bool = False,
) -> dict:
    """Dispatch a tool call to llsm.scia.tools, return a dict result.

    Always returns a dict — unknown tools and exceptions are wrapped as
    `{"ok": False, "error": ...}` so the provider's tool_result block can
    serialize them without dropping the loop.

    `on_tool_event(phase, name, payload)` fires once before
    (phase="start", payload=args) and once after (phase="end",
    payload={"ok": bool, "error": str}) each call. Best-effort: callback exceptions are swallowed so the panel
    queue can never break tool execution.
    """
    def _emit(phase: str, payload):
        if on_tool_event is None:
            return
        try:
            on_tool_event(phase, name, payload)
        except Exception:
            pass

    if name not in TOOL_NAMES:
        _emit("start", args)
        _emit("end", {"ok": False, "error": f"unknown tool {name!r}"})
        return {"ok": False, "error": f"unknown tool {name!r}"}
    fn = getattr(scia_tools, name, None)
    if fn is None:
        _emit("start", args)
        _emit("end", {"ok": False, "error": f"tool {name!r} not implemented"})
        return {"ok": False, "error": f"tool {name!r} not implemented"}
    if verbose:
        print(f"[tool] {name}({args})")
    _emit("start", args)
    try:
        result = fn(ctx, **args)
    except Exception as e:
        _emit("end", {"ok": False, "error": f"{type(e).__name__}: {e}"})
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    ok = bool(result.get("ok", True)) if isinstance(result, dict) else True
    error = str(result.get("error") or "") if isinstance(result, dict) and not ok else ""
    _emit("end", {"ok": ok, "error": error})
    return result


# ---------- OpenAI tool format ----------

def to_openai_tools() -> list[dict]:
    """Convert TOOL_DEFS to OpenAI's tools array format.

    Anthropic shape:
        {"name", "description", "input_schema": {...}}
    OpenAI shape:
        {"type": "function", "function": {"name", "description", "parameters": {...}}}
    """
    out: list[dict] = []
    for d in TOOL_DEFS:
        out.append({
            "type": "function",
            "function": {
                "name": d["name"],
                "description": d.get("description", ""),
                "parameters": d.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return out


def extract_openai_tool_calls(message) -> list[ToolCall]:
    """Pull tool-call blocks out of an OpenAI assistant message.

    `message` is a `chat.completions.Message`-like object with optional
    `tool_calls: list[ToolCall]` where each has `id`, `function.name`,
    `function.arguments` (JSON string).
    """
    raw = getattr(message, "tool_calls", None) or []
    out: list[ToolCall] = []
    for tc in raw:
        try:
            arg_str = tc.function.arguments or "{}"
            args = json.loads(arg_str) if isinstance(arg_str, str) else (arg_str or {})
        except Exception:
            args = {}
        out.append(ToolCall(id=getattr(tc, "id", "") or "", name=tc.function.name, arguments=args))
    return out


def format_openai_tool_result(tool_call_id: str, result: dict) -> dict:
    """OpenAI tool_result message format."""
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": json.dumps(result, default=str),
    }


# ---------- Gemini tool format ----------

def to_gemini_tools():
    """Convert TOOL_DEFS into Gemini's `Tool(function_declarations=[...])`.

    Lazy SDK import (CR-1). Returns a list with one Tool wrapping all our
    function declarations — Gemini accepts either a list of single-decl
    Tools or one Tool with many declarations; we use the latter.
    """
    from google.genai import types  # lazy
    decls = []
    for d in TOOL_DEFS:
        decls.append({
            "name": d["name"],
            "description": d.get("description", ""),
            "parameters": d.get("input_schema", {"type": "object", "properties": {}}),
        })
    return [types.Tool(function_declarations=decls)]


def extract_gemini_tool_calls(response) -> list[ToolCall]:
    """Walk a generate_content response and return ToolCalls.

    Gemini puts function_call entries in
    `response.candidates[0].content.parts[i].function_call`. There's no
    explicit call id — we generate one from index so format_gemini_tool_result
    can correlate, even though the SDK doesn't actually need it.
    """
    out: list[ToolCall] = []
    try:
        candidates = response.candidates or []
    except Exception:
        return []
    for c in candidates:
        content = getattr(c, "content", None)
        if content is None:
            continue
        parts = getattr(content, "parts", None) or []
        for i, p in enumerate(parts):
            fc = getattr(p, "function_call", None)
            if fc is None:
                continue
            args = getattr(fc, "args", None) or {}
            if not isinstance(args, dict):
                # Some SDK versions hand back a Struct-like object.
                try:
                    args = dict(args)
                except Exception:
                    args = {}
            out.append(ToolCall(id=f"gem-{i}", name=fc.name, arguments=args))
    return out


def format_gemini_tool_result(name: str, result: dict):
    """Gemini function-response Part."""
    from google.genai import types  # lazy
    return types.Part.from_function_response(name=name, response=result)


def format_gemini_assistant_with_tool_calls(response):
    """Capture the assistant's `function_call`-bearing content for replay.

    Gemini's chat history needs the assistant's full content (parts) appended
    so the next turn's tool_response is correlated. We just take the first
    candidate's content as-is.
    """
    try:
        return response.candidates[0].content
    except Exception:
        return None


__all__ = [
    "MAX_TOOL_ITERATIONS",
    "ToolCall",
    "invoke_tool",
    "to_openai_tools",
    "extract_openai_tool_calls",
    "format_openai_tool_result",
    "to_gemini_tools",
    "extract_gemini_tool_calls",
    "format_gemini_tool_result",
    "format_gemini_assistant_with_tool_calls",
]
