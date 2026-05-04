"""CR-2: ModelCatalog falls back to static_models when no backend is provided."""
from __future__ import annotations

from llsm.history.model_catalog import ModelCatalog
from llsm.history.models import ModelRef


def test_catalog_uses_static_provider_when_no_backend():
    """Without a backend, the catalog calls the registered static provider."""
    static_calls = {"openai": 0}

    def static_openai():
        static_calls["openai"] += 1
        return [ModelRef("openai", "gpt-5", display_name="GPT-5")]

    cat = ModelCatalog(providers={"openai": static_openai})
    out = cat.for_backend("openai")
    assert [m.model_id for m in out] == ["gpt-5"]
    assert static_calls["openai"] == 1


def test_catalog_prefers_live_backend_over_static():
    static_calls = {"n": 0}

    def static_openai():
        static_calls["n"] += 1
        return [ModelRef("openai", "static-model")]

    class FakeBackend:
        def available_models(self):
            return [ModelRef("openai", "live-model")]

    cat = ModelCatalog(providers={"openai": static_openai})
    out = cat.for_backend("openai", backend=FakeBackend())
    assert [m.model_id for m in out] == ["live-model"]
    assert static_calls["n"] == 0  # live preferred, never called static
