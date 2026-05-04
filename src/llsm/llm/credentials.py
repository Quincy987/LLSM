"""Provider credential storage — pure storage layer.

Storage tiers (in order, per plan A2):
  1. Keyring (Windows Credential Manager via the `keyring` package).
     Service name: "llsm"; key: "<provider>:<profile>:<field>".
  2. Encrypted file fallback at ~/.llsm/credentials.dat — DPAPI-protected
     via win32crypt. Used if keyring is blocked by group policy
     (Amsterdam city laptop edge case).
  3. Env var legacy — ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY,
     GEMINI_API_KEY. Read-only fallback for back-compat.

Profile keying (`<provider>:<profile>:<field>`) reserves the slot for future
named profiles even though v0.3.0 only exposes a single "default" profile.
This avoids a credential migration when multi-profile support lands.

This module is intentionally a leaf. It does NOT import `model_catalog` —
the wizard and the manage-providers dialog are responsible for calling
`ModelCatalog.invalidate(provider)` after they call `set_credential` /
`delete_credential`. Keeping credentials.py free of catalog imports keeps
the layering clean (`llm/` is a leaf, `history/` sits above it).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional


_LLSM_HOME = Path.home() / ".llsm"
_CREDS_FILE = _LLSM_HOME / "credentials.dat"
_KEYRING_SERVICE = "llsm"
_DEFAULT_PROFILE = "default"


# Legacy env vars to probe when no stored credential exists. Read-only — we
# never write env vars. Field-then-provider granular so we can add per-field
# legacy lookups (e.g. base_url) later without touching call sites.
_ENV_LEGACY: dict[tuple[str, str], tuple[str, ...]] = {
    ("api", "api_key"):    ("ANTHROPIC_API_KEY",),
    ("openai", "api_key"): ("OPENAI_API_KEY",),
    ("gemini", "api_key"): ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
}


def _kr_key(provider: str, profile: str, field: str) -> str:
    return f"{provider}:{profile}:{field}"


# ---------- Tier 1: keyring ----------

def _keyring_get(provider: str, profile: str, field: str) -> Optional[str]:
    try:
        import keyring  # type: ignore
    except Exception:
        return None
    try:
        v = keyring.get_password(_KEYRING_SERVICE, _kr_key(provider, profile, field))
        return v if v else None
    except Exception:
        return None


def _keyring_set(provider: str, profile: str, field: str, value: str) -> bool:
    try:
        import keyring  # type: ignore
    except Exception:
        return False
    try:
        keyring.set_password(_KEYRING_SERVICE, _kr_key(provider, profile, field), value)
        return True
    except Exception:
        return False


def _keyring_delete(provider: str, profile: str, field: str) -> bool:
    try:
        import keyring  # type: ignore
        from keyring.errors import PasswordDeleteError  # type: ignore
    except Exception:
        return False
    try:
        keyring.delete_password(_KEYRING_SERVICE, _kr_key(provider, profile, field))
        return True
    except PasswordDeleteError:
        # No such entry — not an error from the caller's POV.
        return True
    except Exception:
        return False


# ---------- Tier 2: DPAPI-encrypted file ----------
#
# File layout: a single dict {"<key>": "<plaintext_value>"} encrypted as a
# whole via CryptProtectData and written to ~/.llsm/credentials.dat. We
# encrypt the JSON blob, not individual entries, so adding/removing a key
# rewrites the file.
#
# DPAPI ties the ciphertext to the current Windows user account. A different
# user on the same machine cannot decrypt it.

def _dpapi_load() -> dict[str, str]:
    if not _CREDS_FILE.exists():
        return {}
    try:
        import win32crypt  # type: ignore
    except Exception:
        return {}
    try:
        cipher = _CREDS_FILE.read_bytes()
        if not cipher:
            return {}
        # DataDescription, Entropy, Reserved, PromptStruct, Flags
        _desc, plain = win32crypt.CryptUnprotectData(cipher, None, None, None, 0)
        data = json.loads(plain.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _dpapi_save(d: dict[str, str]) -> bool:
    try:
        import win32crypt  # type: ignore
    except Exception:
        return False
    try:
        _LLSM_HOME.mkdir(parents=True, exist_ok=True)
        plain = json.dumps(d, ensure_ascii=False).encode("utf-8")
        cipher = win32crypt.CryptProtectData(plain, "llsm-credentials", None, None, None, 0)
        # Atomic-ish write
        tmp = _CREDS_FILE.with_suffix(_CREDS_FILE.suffix + ".tmp")
        tmp.write_bytes(cipher)
        os.replace(tmp, _CREDS_FILE)
        return True
    except Exception:
        return False


def _dpapi_get(provider: str, profile: str, field: str) -> Optional[str]:
    d = _dpapi_load()
    v = d.get(_kr_key(provider, profile, field))
    return v if v else None


def _dpapi_set(provider: str, profile: str, field: str, value: str) -> bool:
    d = _dpapi_load()
    d[_kr_key(provider, profile, field)] = value
    return _dpapi_save(d)


def _dpapi_delete(provider: str, profile: str, field: str) -> bool:
    d = _dpapi_load()
    if d.pop(_kr_key(provider, profile, field), None) is None:
        return True  # already absent
    return _dpapi_save(d)


# ---------- Tier 3: env var legacy ----------

def _env_get(provider: str, field: str) -> Optional[str]:
    for var in _ENV_LEGACY.get((provider, field), ()):
        v = os.environ.get(var)
        if v:
            return v
    return None


# ---------- Public API ----------

def get_credential(
    provider: str, field: str, profile: str = _DEFAULT_PROFILE
) -> Optional[str]:
    """Return the stored credential or None if not configured.

    Probes keyring -> DPAPI file -> env var legacy. Env var fallback only
    applies when profile == default; alternate profiles never inherit env
    vars (which are shared global state).
    """
    v = _keyring_get(provider, profile, field)
    if v:
        return v
    v = _dpapi_get(provider, profile, field)
    if v:
        return v
    if profile == _DEFAULT_PROFILE:
        v = _env_get(provider, field)
        if v:
            return v
    return None


def set_credential(
    provider: str, field: str, value: str, profile: str = _DEFAULT_PROFILE
) -> None:
    """Store the credential. Tries keyring first, falls back to DPAPI file.

    Pure storage — does NOT invalidate the model catalog. Callers (wizard,
    manage-providers dialog) must call `ModelCatalog.invalidate(provider)`
    after this returns.
    """
    if _keyring_set(provider, profile, field, value):
        return
    if _dpapi_set(provider, profile, field, value):
        return
    raise RuntimeError(
        "Could not store credential: keyring is unavailable and DPAPI file "
        "fallback also failed. Check that keyring is installed "
        "(`pip install llsm[wizard]`) or that ~/.llsm is writable."
    )


def delete_credential(
    provider: str, field: str, profile: str = _DEFAULT_PROFILE
) -> None:
    """Remove the credential from both storage tiers (best-effort)."""
    _keyring_delete(provider, profile, field)
    _dpapi_delete(provider, profile, field)


def has_credential(
    provider: str, field: str = "api_key", profile: str = _DEFAULT_PROFILE
) -> bool:
    return get_credential(provider, field, profile) is not None


def list_configured_providers() -> list[str]:
    """Return the set of provider kinds that have at least one stored field.

    Includes env-var-legacy detections (so an existing user with
    ANTHROPIC_API_KEY set keeps seeing the api provider as configured).
    """
    seen: set[str] = set()
    # Probe each provider/field tuple we know how to look up.
    for provider, field in (
        ("api", "api_key"),
        ("claude-code", "subscription"),
        ("openai", "api_key"),
        ("gemini", "api_key"),
        ("openai-compatible", "base_url"),
    ):
        if has_credential(provider, field):
            seen.add(provider)
    return sorted(seen)
