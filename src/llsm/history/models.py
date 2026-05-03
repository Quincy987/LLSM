"""Plain dataclasses for the history layer. No I/O, no Tk."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from ._io import now_iso as _now_iso


@dataclass
class Conversation:
    id: str
    title: str
    created_at: str
    last_used_at: str
    backend_kind: str          # "claude-code" | "api"
    model: str
    session_id: str = ""       # claude-code: claude CLI session id; api: unused
    message_count: int = 0
    preview: str = ""
    pinned: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Conversation":
        # Tolerant of unknown keys (forward-compat) and missing optional keys.
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Message:
    role: str          # "user" | "llsm" | "system" | "error"
    text: str
    ts: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "text": self.text, "ts": self.ts}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Message":
        return cls(
            role=str(d.get("role", "")),
            text=str(d.get("text", "")),
            ts=str(d.get("ts", "")) or _now_iso(),
        )


@dataclass
class ModelRef:
    backend_kind: str
    model_id: str
    display_name: str = ""
    last_used_at: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.backend_kind, self.model_id)
