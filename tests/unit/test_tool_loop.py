"""Provider-neutral tool dispatch + format adapters."""
from __future__ import annotations

import pytest

from llsm.llm.tool_loop import (
    MAX_TOOL_ITERATIONS,
    invoke_tool,
    to_openai_tools,
    extract_openai_tool_calls,
    format_openai_tool_result,
)


def test_max_iterations_exported():
    """B7 regression guard: the constant lives in tool_loop.py and is non-zero."""
    assert MAX_TOOL_ITERATIONS == 40


def test_max_iterations_imported_by_anthropic_backend():
    """The Anthropic backend imports MAX_TOOL_ITERATIONS from tool_loop, not its own const."""
    import llsm.llm.anthropic_backend as ab
    assert ab.MAX_TOOL_ITERATIONS is MAX_TOOL_ITERATIONS


def test_invoke_tool_unknown_tool_returns_error_dict():
    res = invoke_tool("not_a_real_tool", {}, ctx=object())
    assert res["ok"] is False
    assert "unknown tool" in res["error"]


def test_invoke_tool_fires_callbacks_in_order():
    events: list = []
    def cb(phase, name, payload):
        events.append((phase, name))
    invoke_tool("not_a_real_tool", {}, ctx=object(), on_tool_event=cb)
    assert events == [("start", "not_a_real_tool"), ("end", "not_a_real_tool")]


def test_to_openai_tools_round_trip():
    tools = to_openai_tools()
    assert isinstance(tools, list) and tools
    sample = tools[0]
    assert sample["type"] == "function"
    fn = sample["function"]
    assert {"name", "description", "parameters"} <= set(fn)
    # All TOOL_DEFS surface
    from llsm.llm.tools import TOOL_NAMES
    names = {t["function"]["name"] for t in tools}
    assert names == set(TOOL_NAMES)


class _MockToolCall:
    def __init__(self, id_, name, args_str):
        self.id = id_
        class _F:
            def __init__(s, n, a): s.name = n; s.arguments = a
        self.function = _F(name, args_str)


class _MockMessage:
    def __init__(self, calls):
        self.tool_calls = calls
        self.content = ""


def test_extract_openai_tool_calls_parses_json_args():
    msg = _MockMessage([
        _MockToolCall("c1", "new_model", '{"name": "demo"}'),
        _MockToolCall("c2", "add_node", '{"name":"n1","x_mm":0,"y_mm":0,"z_mm":0}'),
    ])
    calls = extract_openai_tool_calls(msg)
    assert [c.name for c in calls] == ["new_model", "add_node"]
    assert calls[0].arguments == {"name": "demo"}
    assert calls[1].id == "c2"


def test_extract_openai_tool_calls_handles_invalid_json():
    msg = _MockMessage([_MockToolCall("c1", "new_model", "not-json{")])
    calls = extract_openai_tool_calls(msg)
    assert calls[0].arguments == {}


def test_format_openai_tool_result():
    out = format_openai_tool_result("tc-1", {"ok": True, "items": 3})
    assert out["role"] == "tool"
    assert out["tool_call_id"] == "tc-1"
    import json
    assert json.loads(out["content"]) == {"ok": True, "items": 3}
