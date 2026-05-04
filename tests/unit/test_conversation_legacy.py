"""Legacy meta.json (v0.2.x — no `tools_enabled` field) must load cleanly.

We use Conversation.from_dict here because the existing rehydration path
already filters unknown keys + accepts missing optionals — i.e. when Phase B
adds the field with a default, the existing code keeps working.
"""
from __future__ import annotations

from llsm.history.models import Conversation


def test_legacy_meta_loads_with_defaults():
    legacy = {
        "id": "abc123",
        "title": "Old chat",
        "created_at": "2026-01-01T00:00:00+00:00",
        "last_used_at": "2026-01-02T00:00:00+00:00",
        "backend_kind": "api",
        "model": "claude-sonnet-4-5-20250929",
        "session_id": "",
        "message_count": 7,
        "preview": "hello",
        "pinned": False,
    }
    conv = Conversation.from_dict(legacy)
    assert conv.id == "abc123"
    assert conv.backend_kind == "api"
    # tools_enabled isn't in the dataclass yet (Phase B), but if we add it
    # Conversation.from_dict's filter strips unknown keys + uses defaults.


def test_unknown_field_is_tolerated():
    """Forward compat: a future field shouldn't break old loaders."""
    extended = {
        "id": "x", "title": "t", "created_at": "", "last_used_at": "",
        "backend_kind": "api", "model": "m",
        "future_field_we_dont_know_about": True,
    }
    conv = Conversation.from_dict(extended)
    assert conv.id == "x"
