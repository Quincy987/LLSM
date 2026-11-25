"""A turn with no tool calls and no text is not an answer.

Seen live (2026-11-25, Qwen3.5-35B-A3B on llama-server, thinking on): the
model wrote its next tool call inside its reasoning block, the server
answered finish_reason="stop" with empty content and no tool_calls, and the
chat ended with an empty reply halfway through the model.
"""
from __future__ import annotations

import json

import pytest

from llsm.llm.tool_loop import (
    EMPTY_TURN_NUDGE,
    MAX_EMPTY_TURN_NUDGES,
    resolve_turn_without_tools,
)


# ---------- the rule itself ----------

def test_visible_text_is_the_answer_unchanged():
    out = resolve_turn_without_tools("  Klaar.  ", finish_reason="stop",
                                     nudges_used=0, tool_calls_done=3)
    assert out.answer == "Klaar." and out.nudge is None


def test_empty_turn_is_nudged_until_the_cap():
    for used in range(MAX_EMPTY_TURN_NUDGES):
        out = resolve_turn_without_tools("", finish_reason="stop",
                                         nudges_used=used, tool_calls_done=4)
        assert out.answer is None and out.nudge == EMPTY_TURN_NUDGE


def test_empty_turn_past_the_cap_says_it_stopped():
    out = resolve_turn_without_tools(None, finish_reason="stop",
                                     nudges_used=MAX_EMPTY_TURN_NUDGES,
                                     tool_calls_done=4)
    assert out.nudge is None
    assert out.answer.startswith("[ERROR] The model stopped without an answer after 4 tool calls")


@pytest.mark.parametrize("reason", ["length", "MAX_TOKENS"])
def test_empty_turn_cut_off_by_the_token_limit_says_so(reason):
    out = resolve_turn_without_tools("", finish_reason=reason,
                                     nudges_used=0, tool_calls_done=1)
    assert out.nudge is None
    assert "cut off by its token limit" in out.answer
    assert "after 1 tool call)" in out.answer


@pytest.mark.parametrize("reason", ["content_filter", "SAFETY", "FinishReason.SAFETY"])
def test_empty_turn_blocked_by_a_filter_is_reported_not_nudged(reason):
    out = resolve_turn_without_tools("", finish_reason=reason,
                                     nudges_used=0, tool_calls_done=2)
    assert out.nudge is None
    assert out.answer.startswith("[ERROR] The provider's content filter blocked")


def test_stall_hint_is_only_added_where_given():
    plain = resolve_turn_without_tools("", finish_reason="stop", nudges_used=2,
                                       tool_calls_done=3)
    hinted = resolve_turn_without_tools("", finish_reason="stop", nudges_used=2,
                                        tool_calls_done=3, stall_hint="Try X.")
    assert "thinking" not in plain.answer and plain.answer.endswith("ask it to continue.")
    assert hinted.answer.endswith("ask it to continue. Try X.")


# ---------- the OpenAI-compatible loop ----------

class _Fn:
    def __init__(self, name, args):
        self.name = name
        self.arguments = json.dumps(args)


class _ToolCall:
    def __init__(self, i, name, args):
        self.id = f"call-{i}"
        self.function = _Fn(name, args)


class _Msg:
    def __init__(self, content="", calls=()):
        self.content = content
        self.tool_calls = list(calls) or None


class _Choice:
    def __init__(self, msg, finish):
        self.message = msg
        self.finish_reason = finish


class _Resp:
    def __init__(self, msg, finish):
        self.choices = [_Choice(msg, finish)]


def _scripted_client(turns, seen):
    """A fake OpenAI client that plays `turns` (msg, finish_reason) in order."""
    queue = list(turns)

    class _Completions:
        def create(self, **kwargs):
            seen.append([dict(m) for m in kwargs["messages"]])
            msg, finish = queue.pop(0)
            return _Resp(msg, finish)

    class _Client:
        def __init__(self, **kwargs):
            self.chat = type("C", (), {"completions": _Completions()})()

        def close(self):
            pass

    return _Client


def _backend(monkeypatch, turns, seen, module="openai_compatible_backend"):
    pytest.importorskip("openai")
    import importlib
    mod = importlib.import_module(f"llsm.llm.{module}")
    monkeypatch.setattr(mod, "OpenAI", _scripted_client(turns, seen))
    # Tool dispatch is not under test: record and succeed.
    calls = []
    monkeypatch.setattr(mod, "invoke_tool",
                        lambda name, args, ctx, cb, verbose=False:
                        calls.append(name) or {"ok": True})
    if module == "openai_compatible_backend":
        b = mod.OpenAICompatibleBackend(base_url="http://127.0.0.1:1/v1", api_key="x",
                                        tools_enabled=True, verbose=False)
    else:
        b = mod.OpenAIBackend(api_key="x", tools_enabled=True, verbose=False)
    return b, calls


@pytest.mark.parametrize("module", ["openai_compatible_backend", "openai_backend"])
def test_empty_turn_then_tool_call_continues(monkeypatch, module):
    seen: list = []
    turns = [
        (_Msg(calls=[_ToolCall(1, "new_model", {"name": "m"})]), "tool_calls"),
        (_Msg(""), "stop"),                       # the stall
        (_Msg(calls=[_ToolCall(2, "add_material", {"name": "C"})]), "tool_calls"),
        (_Msg("Model klaar."), "stop"),
    ]
    b, calls = _backend(monkeypatch, turns, seen, module)
    assert b.chat("maak een ligger") == "Model klaar."
    assert calls == ["new_model", "add_material"]
    # The request after the stall carried the nudge as the last message.
    assert seen[2][-1] == {"role": "user", "content": EMPTY_TURN_NUDGE}


@pytest.mark.parametrize("module", ["openai_compatible_backend", "openai_backend"])
def test_empty_turns_past_the_cap_give_a_clear_message(monkeypatch, module):
    seen: list = []
    turns = [(_Msg(calls=[_ToolCall(1, "new_model", {"name": "m"})]), "tool_calls")]
    turns += [(_Msg(""), "stop")] * (MAX_EMPTY_TURN_NUDGES + 1)
    b, calls = _backend(monkeypatch, turns, seen, module)
    reply = b.chat("maak een ligger")
    assert reply.startswith("[ERROR] The model stopped without an answer after 1 tool call")
    assert len(seen) == 1 + MAX_EMPTY_TURN_NUDGES + 1


def test_cut_off_turn_is_reported_not_nudged(monkeypatch):
    seen: list = []
    turns = [(_Msg(""), "length")]
    b, _ = _backend(monkeypatch, turns, seen)
    assert "cut off by its token limit" in b.chat("hoi")
    assert len(seen) == 1


def test_normal_final_answer_is_unchanged(monkeypatch):
    seen: list = []
    turns = [(_Msg("  Hallo!  "), "stop")]
    b, _ = _backend(monkeypatch, turns, seen)
    assert b.chat("hoi") == "Hallo!"
    assert len(seen) == 1


# ---------- Gemini: no nudge, but never a silent "" ----------

def test_gemini_empty_turn_gives_a_message_without_nudging(monkeypatch):
    pytest.importorskip("google.genai")
    from llsm.llm import gemini_backend as gb

    class _Cand:
        finish_reason = type("R", (), {"name": "STOP"})()
        content = None

    class _Resp:
        candidates = [_Cand()]
        text = None

    requests = []

    class _Models:
        def generate_content(self, **kwargs):
            requests.append(kwargs)
            return _Resp()

    class _Client:
        def __init__(self, **kwargs):
            self.models = _Models()

    monkeypatch.setattr(gb.genai, "Client", _Client)
    monkeypatch.setattr(gb, "to_gemini_tools", lambda: [])
    b = gb.GeminiBackend(api_key="x", tools_enabled=True, verbose=False)
    reply = b.chat("hoi")
    assert reply.startswith("[ERROR] The model stopped without an answer after 0 tool calls")
    assert len(requests) == 1
