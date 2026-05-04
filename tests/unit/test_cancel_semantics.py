"""All new backends honor cancel — at minimum, a pre-cancelled chat() raises."""
from __future__ import annotations

import pytest


pytest.importorskip("openai")


class _Stream:
    def __init__(self, chunks): self._chunks = chunks; self.closed = False
    def __iter__(self):
        for c in self._chunks: yield c
    def close(self): self.closed = True


def test_openai_compat_cancel_mid_stream(monkeypatch):
    """openai-compatible: cancel triggered mid-iteration breaks the loop."""
    from llsm.llm import openai_compatible_backend as oc

    backend_holder: dict = {}

    class _Delta:
        def __init__(self, t): self.content = t
    class _Choice:
        def __init__(self, t): self.delta = _Delta(t)
    class _Chunk:
        def __init__(self, t): self.choices = [_Choice(t)]
    class _TriggerChunk:
        @property
        def choices(self):
            backend_holder["b"]._cancelled.set()
            return [_Choice("after")]

    class _FakeStream:
        def __init__(self, chunks): self._chunks = chunks; self.closed = False
        def __iter__(self):
            for c in self._chunks: yield c
        def close(self): self.closed = True

    class FakeOpenAI:
        def __init__(self, **kwargs):
            chunks = [_Chunk("first "), _TriggerChunk(), _Chunk("never")]
            self.chat = type("C", (), {
                "completions": type("X", (), {"create": lambda self2, **kw: _FakeStream(chunks)})(),
            })()
        def close(self): pass

    monkeypatch.setattr(oc, "OpenAI", FakeOpenAI)
    b = oc.OpenAICompatibleBackend(base_url="http://localhost:11434/v1", api_key="x")
    backend_holder["b"] = b

    from llsm.llm.base import CancelledError
    with pytest.raises(CancelledError):
        b.chat("hi")


def test_openai_compat_cancel_returns_false_when_idle(monkeypatch):
    from llsm.llm import openai_compatible_backend as oc

    class FakeOpenAI:
        def __init__(self, **kwargs): pass
        def close(self): pass

    monkeypatch.setattr(oc, "OpenAI", FakeOpenAI)
    b = oc.OpenAICompatibleBackend(base_url="http://x/v1", api_key="k")
    # No chat in flight -> cancel() must return False.
    assert b.cancel() is False
