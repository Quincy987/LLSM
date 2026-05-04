"""B1 regression guard: anthropic_backend uses tool_loop.invoke_tool + the
shared MAX_TOOL_ITERATIONS, not its own private versions.
"""
from __future__ import annotations

import ast
import os


def _read(rel: str) -> str:
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    return open(os.path.join(repo, rel), encoding="utf-8").read()


def test_anthropic_imports_invoke_tool_from_tool_loop():
    src = _read("src/llsm/llm/anthropic_backend.py")
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        # Relative `from .tool_loop import ...` -> module="tool_loop", level=1.
        if isinstance(node, ast.ImportFrom) and node.module == "tool_loop" and node.level >= 1:
            names = {a.name for a in node.names}
            if "invoke_tool" in names and "MAX_TOOL_ITERATIONS" in names:
                found = True
                break
    assert found, "anthropic_backend must `from .tool_loop import MAX_TOOL_ITERATIONS, invoke_tool`"


def test_anthropic_no_longer_defines_invoke_tool_locally():
    src = _read("src/llsm/llm/anthropic_backend.py")
    assert "_invoke_tool" not in src, (
        "anthropic_backend still defines its own _invoke_tool — should be in tool_loop"
    )


def test_anthropic_no_longer_owns_max_iter_constant():
    src = _read("src/llsm/llm/anthropic_backend.py")
    # The constant must be imported, not defined here.
    assert "MAX_TOOL_ITERATIONS = 40" not in src
