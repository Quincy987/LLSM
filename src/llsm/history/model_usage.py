"""Persisted last-used timestamps per (backend_kind, model_id).

Survives even if the backend stops offering a model or its conversations
are deleted. Capped at 50 most-recent entries; tail is dropped on write.
"""
from __future__ import annotations

import json
from pathlib import Path

from ._io import atomic_write_json, now_iso
from .models import ModelRef

_CAP = 50


class ModelUsage:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> list[ModelRef]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        out: list[ModelRef] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                out.append(ModelRef(
                    backend_kind=str(item.get("backend_kind", "")),
                    model_id=str(item.get("model_id", "")),
                    display_name=str(item.get("display_name", "")),
                    last_used_at=str(item.get("last_used_at", "")),
                ))
            except Exception:
                continue
        return [r for r in out if r.backend_kind and r.model_id]

    def _save(self, refs: list[ModelRef]) -> None:
        # Sort newest-first, then cap. Drops the long tail.
        refs.sort(key=lambda r: r.last_used_at, reverse=True)
        capped = refs[:_CAP]
        atomic_write_json(self.path, [
            {
                "backend_kind": r.backend_kind,
                "model_id": r.model_id,
                "display_name": r.display_name,
                "last_used_at": r.last_used_at,
            } for r in capped
        ])

    def record(self, backend_kind: str, model_id: str, display_name: str = "") -> None:
        if not backend_kind or not model_id:
            return
        refs = self._load()
        now = now_iso()
        key = (backend_kind, model_id)
        for r in refs:
            if r.key == key:
                r.last_used_at = now
                if display_name:
                    r.display_name = display_name
                self._save(refs)
                return
        refs.append(ModelRef(
            backend_kind=backend_kind,
            model_id=model_id,
            display_name=display_name or model_id,
            last_used_at=now,
        ))
        self._save(refs)

    def recent(self, backend_kind: str | None = None, limit: int = 10) -> list[ModelRef]:
        refs = self._load()
        if backend_kind is not None:
            refs = [r for r in refs if r.backend_kind == backend_kind]
        refs.sort(key=lambda r: r.last_used_at, reverse=True)
        return refs[:limit]
