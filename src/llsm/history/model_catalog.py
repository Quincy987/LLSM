"""Runtime model catalog: what each backend currently offers.

Ephemeral, queried per-launch. Decoupled from ModelUsage so a recent-but-
removed model doesn't ghost the menu, and a newly-added model appears
immediately rather than waiting for a first use.

The catalog accepts a `providers` map (backend_kind -> callable returning
list[ModelRef]) at construction time, so the history layer stays free of
imports from llm/. The panel passes the actual backend modules' static
`available_models()` functions when constructing the catalog.
"""
from __future__ import annotations

from typing import Callable

from .models import ModelRef


ProviderMap = dict[str, Callable[[], list[ModelRef]]]


class ModelCatalog:
    """Wraps backend-side `available_models()` with a per-launch cache.

    `providers` lets the catalog query each backend's model list without
    instantiating it (which for the api backend would require an
    ANTHROPIC_API_KEY just to render a menu). When no provider is registered
    for a backend_kind, the catalog falls back to asking the LIVE backend
    instance passed to `for_backend(..., backend)`.
    """

    def __init__(self, providers: ProviderMap | None = None) -> None:
        self._providers: ProviderMap = dict(providers or {})
        self._cache: dict[str, list[ModelRef]] = {}

    def register_provider(self, backend_kind: str, fn: Callable[[], list[ModelRef]]) -> None:
        self._providers[backend_kind] = fn
        self._cache.pop(backend_kind, None)

    def for_backend(self, backend_kind: str, backend=None) -> list[ModelRef]:
        if backend_kind in self._cache:
            return self._cache[backend_kind]
        models: list[ModelRef] = []
        # Prefer the live backend instance if we have one — supports future
        # backends whose model list depends on instance config.
        if backend is not None and hasattr(backend, "available_models"):
            try:
                models = list(backend.available_models())
            except Exception:
                models = []
        if not models:
            provider = self._providers.get(backend_kind)
            if provider is not None:
                try:
                    models = list(provider())
                except Exception:
                    models = []
        self._cache[backend_kind] = models
        return models

    def contains(self, backend_kind: str, model_id: str) -> bool:
        return any(m.model_id == model_id for m in self.for_backend(backend_kind))

    def invalidate(self, backend_kind: str | None = None) -> None:
        """Drop the cached model list for one provider, or all of them.

        Called from the credential wizard's Save handler and the
        manage-providers Remove action so the next menu cascade re-fetches
        live `/v1/models` (or the static fallback) using the new credential.
        Calling with no kind drops every cache entry — used by tests and as
        the legacy single-arg shape.
        """
        if backend_kind is None:
            self._cache.clear()
            return
        self._cache.pop(backend_kind, None)
