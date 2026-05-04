"""CR-2: ModelCatalog.invalidate(kind) drops one provider's cache."""
from __future__ import annotations

from llsm.history.model_catalog import ModelCatalog
from llsm.history.models import ModelRef


def test_invalidate_kind_drops_only_that_entry():
    calls = {"openai": 0, "api": 0}

    def make_provider(kind):
        def fn():
            calls[kind] += 1
            return [ModelRef(kind, f"{kind}-model")]
        return fn

    cat = ModelCatalog(providers={
        "openai": make_provider("openai"),
        "api": make_provider("api"),
    })
    cat.for_backend("openai")
    cat.for_backend("api")
    assert calls == {"openai": 1, "api": 1}

    # Cached -> no second provider call.
    cat.for_backend("openai")
    assert calls == {"openai": 1, "api": 1}

    # Invalidate openai only; api stays cached.
    cat.invalidate("openai")
    cat.for_backend("openai")
    cat.for_backend("api")
    assert calls == {"openai": 2, "api": 1}


def test_invalidate_all_drops_everything():
    calls = {"openai": 0, "api": 0}
    cat = ModelCatalog(providers={
        "openai": lambda: (calls.__setitem__("openai", calls["openai"] + 1) or
                            [ModelRef("openai", "x")]),
        "api":    lambda: (calls.__setitem__("api", calls["api"] + 1) or
                            [ModelRef("api", "y")]),
    })
    cat.for_backend("openai")
    cat.for_backend("api")
    assert calls == {"openai": 1, "api": 1}

    cat.invalidate()  # no arg -> drop all
    cat.for_backend("openai")
    cat.for_backend("api")
    assert calls == {"openai": 2, "api": 2}
