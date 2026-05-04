"""Backend Protocol — formal seam between the panel and any chat backend.

Both ClaudeCodeBackend and AnthropicBackend implement this. The panel/
conversation layer only depends on this protocol — it never imports a
backend class directly except in the one factory site that constructs them.

Also re-exports `CancelledError` (CR-4 in the plan): every backend and the
panel worker MUST import this single canonical symbol so a backend's cancel
path raises something the panel's catch-all actually catches. Defining a
private one per backend would silently defeat the catch.
"""
from __future__ import annotations

from concurrent.futures import CancelledError as _StdCancelledError
from typing import Protocol, runtime_checkable

from ..history.models import ModelRef


# Re-export under the module's namespace for `from llsm.llm.base import CancelledError`.
CancelledError = _StdCancelledError


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
