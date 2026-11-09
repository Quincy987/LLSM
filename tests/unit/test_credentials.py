"""Roundtrip set/get/delete via the fake keyring."""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _no_api_keys_from_the_machine(monkeypatch):
    """A key in the developer's environment must not answer for the keyring."""
    for name in list(os.environ):
        if name.endswith("_API_KEY"):
            monkeypatch.delenv(name)


def test_set_get_roundtrip(fake_keyring):
    from llsm.llm import credentials
    credentials.set_credential("openai", "api_key", "sk-test-123")
    assert credentials.get_credential("openai", "api_key") == "sk-test-123"
    assert credentials.has_credential("openai", "api_key")


def test_delete_clears(fake_keyring):
    from llsm.llm import credentials
    credentials.set_credential("openai", "api_key", "sk-zzz")
    credentials.delete_credential("openai", "api_key")
    assert credentials.get_credential("openai", "api_key") is None
    assert not credentials.has_credential("openai", "api_key")


def test_env_legacy_fallback(monkeypatch, fake_keyring):
    from llsm.llm import credentials
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    # No keyring entry; expect env fallback.
    assert credentials.get_credential("openai", "api_key") == "sk-from-env"


def test_keyring_takes_precedence_over_env(monkeypatch, fake_keyring):
    from llsm.llm import credentials
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    credentials.set_credential("openai", "api_key", "sk-keyring")
    assert credentials.get_credential("openai", "api_key") == "sk-keyring"


def test_profile_keyed_storage_schema(fake_keyring):
    from llsm.llm import credentials
    credentials.set_credential("openai-compatible", "base_url",
                                "http://localhost:11434/v1")
    # Implementation detail: stored under "openai-compatible:default:base_url".
    assert ("llsm", "openai-compatible:default:base_url") in fake_keyring
