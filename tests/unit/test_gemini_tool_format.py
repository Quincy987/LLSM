"""Gemini tool-format adapters in tool_loop.py."""
from __future__ import annotations

import pytest


pytest.importorskip("google.genai")


def test_to_gemini_tools_returns_one_tool_with_all_decls():
    from llsm.llm.tool_loop import to_gemini_tools
    from llsm.llm.tools import TOOL_NAMES
    tools = to_gemini_tools()
    assert len(tools) == 1
    decls = tools[0].function_declarations
    names = {d.name if hasattr(d, "name") else d["name"] for d in decls}
    assert names == set(TOOL_NAMES)


def test_extract_gemini_tool_calls_walks_parts():
    from llsm.llm.tool_loop import extract_gemini_tool_calls

    class _FC:
        def __init__(self, name, args): self.name = name; self.args = args
    class _Part:
        def __init__(self, fc): self.function_call = fc
    class _Content:
        def __init__(self, parts): self.parts = parts
    class _Cand:
        def __init__(self, content): self.content = content
    class _Resp:
        def __init__(self, candidates): self.candidates = candidates

    resp = _Resp([_Cand(_Content([
        _Part(_FC("new_model", {"name": "demo"})),
        _Part(_FC("add_material", {"name": "S235", "kind": "Steel"})),
    ]))])
    calls = extract_gemini_tool_calls(resp)
    assert [c.name for c in calls] == ["new_model", "add_material"]
    assert calls[0].arguments == {"name": "demo"}
