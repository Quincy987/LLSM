"""Backend Protocol — formal seam between the panel and any chat backend.

Both ClaudeCodeBackend and AnthropicBackend implement this. The panel/
conversation layer only depends on this protocol — it never imports a
backend class directly except in the one factory site that constructs them.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..history.models import ModelRef


@runtime_checkable
class Backend(Protocol):
    """Minimum surface a backend must expose for the panel to drive it."""

    model: str

    def chat(self, message: str) -> str: ...
    def cancel(self) -> bool: ...
    def model_snapshot(self) -> dict: ...
    def close(self) -> None: ...

    # New surface introduced for conversation/model awareness.
    def available_models(self) -> list[ModelRef]: ...

    # Optional: claude-code uses this; api implements as no-op.
    # session_id is exposed as an attribute when relevant.
