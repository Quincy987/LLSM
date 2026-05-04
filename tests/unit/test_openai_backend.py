"""Smoke tests for the OpenAI backend.

These tests don't hit the network — they patch openai.OpenAI to return a
fake stream, then assert chat() yields the joined text and cancel() breaks
the iterator within a chunk.
"""
from __future__ import annotations

import pytest


pytest.importorskip("openai")


class _FakeDelta:
    def __init__(self, text): self.content = text


class _FakeChoice:
    def __init__(self, text): self.delta = _FakeDelta(text)


class _FakeChunk:
    def __init__(self, text): self.choices = [_FakeChoice(text)]


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.closed = False

    def __iter__(self):
        for c in self._chunks:
            yield c

    def close(self):
        self.closed = True


class _FakeChatCompletions:
    def __init__(self, chunks): self._chunks = chunks
    def create(self, **kwargs):
        return _FakeStream(self._chunks)


class _FakeChat:
    def __init__(self, chunks): self.completions = _FakeChatCompletions(chunks)


class _FakeOpenAI:
    def __init__(self, *, chunks=None, **kwargs):
        self.chat = _FakeChat(chunks or [_FakeChunk("hello "), _FakeChunk("world")])
    def close(self): pass
    class _ListResp:
        def __init__(self, ids): self.data = [type("M", (), {"id": i})() for i in ids]
    @property
    def models(self):
        return type("M", (), {"list": lambda self_: _FakeOpenAI._ListResp(["gpt-5"])})()


def _patch_openai(monkeypatch, chunks=None):
    """Replace openai.OpenAI with a fake that returns the given chunks."""
    from llsm.llm import openai_backend
    factory = lambda **kwargs: _FakeOpenAI(chunks=chunks, **kwargs)
    monkeypatch.setattr(openai_backend, "OpenAI", factory)


def test_chat_returns_joined_text(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _patch_openai(monkeypatch, chunks=[_FakeChunk("hello "), _FakeChunk("world")])
    from llsm.llm.openai_backend import OpenAIBackend
    b = OpenAIBackend()
    out = b.chat("hi")
    assert out == "hello world"


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from llsm.llm.openai_backend import OpenAIBackend
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        OpenAIBackend()


def test_cancel_aborts_mid_stream(monkeypatch):
    """Real cancel scenario: chat is iterating; cancel is set mid-stream."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    from llsm.llm.openai_backend import OpenAIBackend
    from llsm.llm.base import CancelledError

    # The trigger chunk sets the cancel flag on the backend instance once
    # we hand it back. Need a closure over `b` (constructed below).
    backend_holder = {}

    class _TriggerChunk:
        @property
        def choices(self):
            # Set cancel right before chat() reads the next chunk.
            backend_holder["b"]._cancelled.set()
            return [_FakeChoice("after-cancel")]

    chunks = [_FakeChunk("first "), _TriggerChunk(), _FakeChunk("never-reached")]
    _patch_openai(monkeypatch, chunks=chunks)
    b = OpenAIBackend()
    backend_holder["b"] = b

    with pytest.raises(CancelledError):
        b.chat("anything")
