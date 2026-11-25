"""Provider registry — data-driven specs for every backend kind.

Used by the catalog (lazy `static_models`), the panel factory (lazy
`build_backend`), and the credentials wizard (auth methods, install hints,
help URLs).

Critical invariant (CR-1 in the plan): SDKs are NOT imported at module load.
The spec stores `static_models_path` and `build_backend_path` as dotted-path
strings; `_resolve(path)` does importlib + getattr on first use. That keeps
`import llsm.llm.providers` cheap and SDK-agnostic — a user with only the
default install (claude-code + api) can launch the panel even when `openai`
and `google-genai` aren't installed.

The wizard surfaces `sdk_install_hint` when `_resolve()` raises ImportError,
turning a "module not found" stack trace into "pip install llsm[openai] to
enable" inline next to the provider row.
"""
from __future__ import annotations

import importlib
from concurrent.futures import CancelledError as _StdCancelledError
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


# Re-export so backends and the panel worker import from a single place
# (CR-4). Parallel re-export in `llsm.llm.base`.
CancelledError = _StdCancelledError


class AuthMethod(str, Enum):
    API_KEY = "api_key"
    SUBSCRIPTION_LOGIN = "subscription_login"  # claude login / gcloud auth
    NONE = "none"                              # local server, no auth


@dataclass(frozen=True)
class ProviderSpec:
    kind: str                                       # backend_kind
    display_name: str
    description: str                                # one-line, shown in wizard
    auth_methods: tuple[AuthMethod, ...]            # filtered options for wizard step 2
    requires_base_url: bool                         # true for openai-compatible
    env_var_legacy: tuple[str, ...]                 # names to probe before keyring
    help_url: str                                   # opens "Where to get your key?"
    static_models_path: str                         # dotted path, lazy per CR-1
    build_backend_path: str                         # dotted path, lazy per CR-1
    default_tools_enabled: bool                     # used in Phase B
    sdk_install_hint: str = ""                      # e.g. "pip install llsm[openai]"


PROVIDERS: dict[str, ProviderSpec] = {
    "claude-code": ProviderSpec(
        kind="claude-code",
        display_name="Claude Code (CLI)",
        description="Use the locally-installed `claude` CLI. Requires a Claude.ai login.",
        auth_methods=(AuthMethod.SUBSCRIPTION_LOGIN,),
        requires_base_url=False,
        env_var_legacy=(),
        help_url="https://docs.claude.com/en/docs/claude-code/quickstart",
        static_models_path="llsm.llm.claude_code_backend:available_models",
        build_backend_path="llsm.llm.claude_code_backend:build_backend",
        default_tools_enabled=True,
        sdk_install_hint="",
    ),
    "api": ProviderSpec(
        kind="api",
        display_name="Anthropic API",
        description="Direct Anthropic API. Requires an API key.",
        auth_methods=(AuthMethod.API_KEY,),
        requires_base_url=False,
        env_var_legacy=("ANTHROPIC_API_KEY",),
        help_url="https://console.anthropic.com/settings/keys",
        static_models_path="llsm.llm.anthropic_backend:available_models",
        build_backend_path="llsm.llm.anthropic_backend:build_backend",
        default_tools_enabled=True,
        sdk_install_hint="",
    ),
    "openai": ProviderSpec(
        kind="openai",
        display_name="OpenAI",
        description="OpenAI's API (GPT-5, GPT-4.1, o-series).",
        auth_methods=(AuthMethod.API_KEY,),
        requires_base_url=False,
        env_var_legacy=("OPENAI_API_KEY",),
        help_url="https://platform.openai.com/api-keys",
        static_models_path="llsm.llm.openai_backend:available_models",
        build_backend_path="llsm.llm.openai_backend:build_backend",
        default_tools_enabled=True,
        sdk_install_hint="pip install llsm[openai]",
    ),
    "gemini": ProviderSpec(
        kind="gemini",
        display_name="Google Gemini",
        description="Google's Gemini API (2.5 Pro, 2.5 Flash).",
        auth_methods=(AuthMethod.API_KEY,),
        requires_base_url=False,
        env_var_legacy=("GOOGLE_API_KEY", "GEMINI_API_KEY"),
        help_url="https://aistudio.google.com/apikey",
        static_models_path="llsm.llm.gemini_backend:available_models",
        build_backend_path="llsm.llm.gemini_backend:build_backend",
        default_tools_enabled=True,
        sdk_install_hint="pip install llsm[gemini]",
    ),
    "openai-compatible": ProviderSpec(
        kind="openai-compatible",
        display_name="OpenAI-compatible (Ollama / LM Studio / ...)",
        description="Any server that speaks the OpenAI Chat Completions API. "
                    "Configure base_url; api_key optional for local servers.",
        auth_methods=(AuthMethod.API_KEY, AuthMethod.NONE),
        requires_base_url=True,
        env_var_legacy=(),
        help_url="https://docs.ollama.com/openai",
        static_models_path="llsm.llm.openai_compatible_backend:available_models",
        build_backend_path="llsm.llm.openai_compatible_backend:build_backend",
        # On: picking a local model from the menu must reach the route that
        # was measured (a 35B model built 9 of 9 beam models; 7B models
        # build wrong ones). The Tools menu toggle still switches it off.
        default_tools_enabled=True,
        sdk_install_hint="pip install llsm[openai]",
    ),
}


# Lazy-resolution cache: dotted-path string -> resolved callable.
_resolve_cache: dict[str, Callable] = {}


def _resolve(path: str) -> Callable:
    """Resolve a "module:attr" string to its callable.

    Caches the result so repeat calls don't re-walk the import system.
    Raises ImportError unchanged so the wizard / menu can map it to the
    spec's `sdk_install_hint`.
    """
    cached = _resolve_cache.get(path)
    if cached is not None:
        return cached
    if ":" not in path:
        raise ValueError(f"provider path must be 'module:attr', got {path!r}")
    mod_name, attr = path.split(":", 1)
    module = importlib.import_module(mod_name)  # may raise ImportError if SDK missing
    fn = getattr(module, attr)
    _resolve_cache[path] = fn
    return fn


def static_models(spec: ProviderSpec):
    """Return the spec's static model list, calling its lazy provider.

    On ImportError, returns an empty list — the menu can fall back to
    showing the install hint instead. Other exceptions propagate so genuine
    bugs surface.
    """
    try:
        fn = _resolve(spec.static_models_path)
    except ImportError:
        return []
    return list(fn())


def has_static_models(spec: ProviderSpec) -> bool:
    """True when the provider ships a fixed model list.

    A provider without one (OpenAI-compatible servers: Ollama, LM Studio,
    ...) only knows its models by asking the server, so the menu fetches
    that list in the background instead of waiting for a live chat. A
    missing SDK counts as "static": the menu shows the install hint for it
    and there is nothing to ask.
    """
    try:
        fn = _resolve(spec.static_models_path)
    except ImportError:
        return True
    try:
        return bool(list(fn()))
    except Exception:
        return True


def backend_class_for(spec: ProviderSpec):
    """The concrete `*Backend` class in the module of the spec's factory.

    Convention: each backend module exports a single public class whose
    name ends in "Backend". Raises ImportError when the SDK is missing.
    """
    module = importlib.import_module(spec.build_backend_path.split(":", 1)[0])
    for attr in dir(module):
        if attr.endswith("Backend") and not attr.startswith("_"):
            obj = getattr(module, attr)
            if isinstance(obj, type):
                return obj
    raise RuntimeError(f"no Backend class found in {module.__name__}")


@dataclass(frozen=True)
class ModelProbe:
    """Outcome of asking a provider for its models.

    status: "ok" (models found), "empty" (reachable, no models),
    "unreachable" (no connection or a time-out), "error" (anything else:
    a refused key, a missing SDK, a bad URL).
    """
    status: str
    models: tuple = ()
    error: str = ""
    base_url: str = ""


def _is_connection_error(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    names = {cls.__name__ for cls in type(exc).__mro__}
    return bool(names & {"APIConnectionError", "APITimeoutError",
                         "ConnectError", "ConnectTimeout", "ReadTimeout"})


def probe_models(
    spec: ProviderSpec,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout_s: float = 3.0,
) -> ModelProbe:
    """Build a throwaway backend from credentials and ask it for its models.

    Shared by the wizard's Test connection button and the File menu's
    background fetch. Blocking: call it from a worker thread. Arguments left
    out fall back to the stored credentials (the backend reads them itself).
    A backend with `list_models(timeout_s=...)` gets the short time-out and
    reports failures; the others fall back to `available_models()`.
    """
    try:
        backend_class = backend_class_for(spec)
    except ImportError as exc:
        return ModelProbe("error", error=f"SDK missing: {spec.sdk_install_hint or exc}")
    except Exception as exc:
        return ModelProbe("error", error=f"{type(exc).__name__}: {exc}")

    kwargs: dict = {"verbose": False}
    if AuthMethod.API_KEY in spec.auth_methods and api_key:
        kwargs["api_key"] = api_key
    if spec.requires_base_url and base_url:
        kwargs["base_url"] = base_url
    try:
        backend = backend_class(**kwargs)
    except Exception as exc:
        return ModelProbe("error", error=f"{type(exc).__name__}: {exc}",
                          base_url=base_url or "")
    resolved_url = str(getattr(backend, "base_url", "") or base_url or "")
    try:
        lister = getattr(backend, "list_models", None)
        if callable(lister):
            models = tuple(lister(timeout_s=timeout_s))
        else:
            models = tuple(backend.available_models())
    except Exception as exc:
        status = "unreachable" if _is_connection_error(exc) else "error"
        return ModelProbe(status, error=f"{type(exc).__name__}: {exc}",
                          base_url=resolved_url)
    finally:
        try:
            backend.close()
        except Exception:
            pass
    return ModelProbe("ok" if models else "empty", models=models,
                      base_url=resolved_url)


def build_backend(spec: ProviderSpec, panel):
    """Resolve and call the spec's lazy `build_backend(panel)` factory.

    Panel-coupled (per CR-5): the factory reads transcripts, callbacks, MCP
    handle off the panel object. Headless (CLI) construction is deferred to
    v0.3.2 — see cli.py for the scope-cut error message.
    """
    fn = _resolve(spec.build_backend_path)
    return fn(panel)
