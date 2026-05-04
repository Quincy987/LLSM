"""Shared pytest fixtures for the unit tests."""
from __future__ import annotations

import os
import sys

# Make `import llsm.*` work without an editable install.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


import pytest


@pytest.fixture
def fake_keyring(monkeypatch):
    """In-memory replacement for the keyring package.

    Lets credential roundtrip tests run without touching Windows Credential
    Manager (and without requiring `keyring` to be installed at all).
    """
    store: dict[tuple[str, str], str] = {}

    class _FakeKeyring:
        def get_password(self, service, name):
            return store.get((service, name))

        def set_password(self, service, name, value):
            store[(service, name)] = value

        def delete_password(self, service, name):
            try:
                del store[(service, name)]
            except KeyError:
                from keyring.errors import PasswordDeleteError  # type: ignore
                raise PasswordDeleteError(name)

    fake = _FakeKeyring()

    # Stub the entire keyring module so `import keyring` returns our fake.
    monkeypatch.setitem(sys.modules, "keyring", fake)
    # Real PasswordDeleteError lives in keyring.errors; stub that too.
    class _PasswordDeleteError(Exception):
        pass
    fake_errors = type(sys)("keyring.errors")  # type: ignore
    fake_errors.PasswordDeleteError = _PasswordDeleteError
    monkeypatch.setitem(sys.modules, "keyring.errors", fake_errors)
    return store
