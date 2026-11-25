"""A failed request to a local server says what to do about it.

With SCIA tools on by default for local chats, every error used to read
"doesn't support OpenAI-style tool calls (or another error occurred)",
including a server that simply was not running.
"""
from __future__ import annotations

import pytest

openai = pytest.importorskip("openai")
import httpx  # noqa: E402  (an openai dependency)

from llsm.llm.providers import request_error_kind  # noqa: E402

_REQ = httpx.Request("POST", "http://127.0.0.1:11434/v1/chat/completions")


def _status(cls, code, message):
    return cls(message, response=httpx.Response(code, request=_REQ), body=None)


@pytest.mark.parametrize("exc, kind", [
    (openai.APITimeoutError(request=_REQ), "timeout"),
    (openai.APIConnectionError(request=_REQ), "unreachable"),
    (ConnectionRefusedError(), "unreachable"),
    (_status(openai.AuthenticationError, 401, "bad key"), "auth"),
    (_status(openai.RateLimitError, 429, "slow down"), "rate_limited"),
    (_status(openai.BadRequestError, 400,
             "registry.ollama.ai/library/llama3:latest does not support tools"), "tools_unsupported"),
    (_status(openai.InternalServerError, 500, "tools param requires --jinja flag"), "tools_unsupported"),
    (_status(openai.BadRequestError, 400, "the request exceeds the available context size"), "other"),
    (ValueError("boom"), "other"),
])
def test_request_error_kind(exc, kind):
    assert request_error_kind(exc) == kind


def _backend_raising(monkeypatch, exc, tools_enabled=True):
    from llsm.llm import openai_compatible_backend as oc

    class _Completions:
        def create(self, **kwargs):
            raise exc

    class _Client:
        def __init__(self, **kwargs):
            self.chat = type("C", (), {"completions": _Completions()})()

        def close(self):
            pass

    monkeypatch.setattr(oc, "OpenAI", _Client)
    return oc.OpenAICompatibleBackend(base_url="http://localhost:11434/v1", api_key="x",
                                      tools_enabled=tools_enabled, verbose=False)


@pytest.mark.parametrize("tools_enabled", [True, False])
def test_server_down_is_not_called_a_tool_problem(monkeypatch, tools_enabled):
    b = _backend_raising(monkeypatch, openai.APIConnectionError(request=_REQ), tools_enabled)
    with pytest.raises(RuntimeError) as err:
        b.chat("hoi")
    text = str(err.value)
    assert "ollama (http://localhost:11434/v1) is not reachable. Is the server running?" in text
    assert "tool" not in text.lower()


def test_a_model_without_tools_points_at_the_toggle(monkeypatch):
    exc = _status(openai.BadRequestError, 400, "llama3:latest does not support tools")
    b = _backend_raising(monkeypatch, exc)
    with pytest.raises(RuntimeError) as err:
        b.chat("hoi")
    assert "does not accept tool calls" in str(err.value)
    assert "Enable SCIA tools for this chat" in str(err.value)


def test_an_unrelated_tool_mention_is_not_called_unsupported():
    exc = _status(openai.BadRequestError, 400, "invalid tool_call_id in message 7")
    assert request_error_kind(exc) == "other"


def test_a_404_names_the_url_and_the_model(monkeypatch):
    exc = _status(openai.NotFoundError, 404, "model 'qwen9' not found")
    assert request_error_kind(exc) == "not_found"
    b = _backend_raising(monkeypatch, exc)
    b.model = "qwen9"
    with pytest.raises(RuntimeError) as err:
        b.chat("hoi")
    assert "ends in /v1" in str(err.value) and "'qwen9'" in str(err.value)
