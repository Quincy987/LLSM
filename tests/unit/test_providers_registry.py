"""Spec invariants for the provider registry."""
from __future__ import annotations

from llsm.llm.providers import PROVIDERS, AuthMethod, ProviderSpec


def test_all_five_providers_registered():
    expected = {"claude-code", "api", "openai", "gemini", "openai-compatible"}
    assert set(PROVIDERS) == expected


def test_specs_have_lazy_paths():
    for kind, spec in PROVIDERS.items():
        assert isinstance(spec, ProviderSpec)
        assert ":" in spec.build_backend_path, f"{kind} build path missing module:attr split"
        assert ":" in spec.static_models_path, f"{kind} static path missing module:attr split"


def test_openai_compatible_defaults_tools_off():
    """Local servers default to tools-off (small models misuse tool calls)."""
    assert PROVIDERS["openai-compatible"].default_tools_enabled is False


def test_openai_and_gemini_have_install_hints():
    for k in ("openai", "gemini", "openai-compatible"):
        assert PROVIDERS[k].sdk_install_hint, f"{k} missing sdk_install_hint"
    # Built-ins (anthropic SDK is in base deps; claude CLI is external).
    assert PROVIDERS["api"].sdk_install_hint == ""
    assert PROVIDERS["claude-code"].sdk_install_hint == ""


def test_openai_compatible_requires_base_url():
    assert PROVIDERS["openai-compatible"].requires_base_url is True
    for other in ("openai", "gemini", "api", "claude-code"):
        assert PROVIDERS[other].requires_base_url is False


def test_auth_methods_match_spec():
    assert AuthMethod.SUBSCRIPTION_LOGIN in PROVIDERS["claude-code"].auth_methods
    assert AuthMethod.API_KEY in PROVIDERS["openai"].auth_methods
    assert AuthMethod.NONE in PROVIDERS["openai-compatible"].auth_methods


def test_cancellederror_reexported():
    """CR-4: providers.CancelledError must be the stdlib one."""
    from concurrent.futures import CancelledError as Std
    from llsm.llm.providers import CancelledError as ProvCE
    from llsm.llm.base import CancelledError as BaseCE
    assert ProvCE is Std
    assert BaseCE is Std
