"""B4: store.create() takes tools_enabled and persists it to meta.json."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from llsm.history.store import ConversationStore


def test_create_with_tools_enabled_false_persists():
    with tempfile.TemporaryDirectory() as td:
        store = ConversationStore(Path(td))
        conv = store.create(
            backend_kind="openai-compatible",
            model="llama3.1:8b",
            tools_enabled=False,
        )
        assert conv.tools_enabled is False

        meta = json.loads(store.meta_path(conv.id).read_text(encoding="utf-8"))
        assert meta["tools_enabled"] is False


def test_create_default_is_true():
    with tempfile.TemporaryDirectory() as td:
        store = ConversationStore(Path(td))
        conv = store.create(backend_kind="api", model="claude-sonnet-4-5-20250929")
        assert conv.tools_enabled is True


def test_update_tools_enabled_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        store = ConversationStore(Path(td))
        conv = store.create(backend_kind="openai", model="gpt-5", tools_enabled=True)
        store.update_tools_enabled(conv.id, False)
        reloaded = store.get(conv.id)
        assert reloaded is not None and reloaded.tools_enabled is False


def test_legacy_meta_without_tools_enabled_loads_with_default():
    """A v0.2.x meta.json predates the field — Conversation.from_dict must
    default tools_enabled to True without crashing."""
    from llsm.history.models import Conversation
    legacy = {
        "id": "legacy", "title": "old", "created_at": "", "last_used_at": "",
        "backend_kind": "api", "model": "claude-sonnet-4-5-20250929",
    }
    conv = Conversation.from_dict(legacy)
    assert conv.tools_enabled is True
