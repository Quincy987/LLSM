"""Anthropic chat backend with tool-use loop.

Runs the classic agentic loop:
  1. send user message + tool defs
  2. if stop_reason == tool_use: execute each tool call by dispatching to
     llsm.scia.tools functions, append tool_result blocks, loop
  3. else: return to user

Keeps conversation history in memory across turns within one session.
"""
from __future__ import annotations
import os
import json
from dataclasses import dataclass, field
from typing import Any

from anthropic import Anthropic

from ..scia.tools import LlsmContext
from .tools import TOOL_DEFS
from .tool_loop import MAX_TOOL_ITERATIONS, invoke_tool


DEFAULT_MODEL = "claude-sonnet-4-5-20250929"


def available_models():
    """Single source of truth for the api backend's model list.

    Module-level so ModelCatalog can call it without instantiating the
    backend (which requires an ANTHROPIC_API_KEY). The instance method
    `AnthropicBackend.available_models` delegates here.
    """
    from ..history.models import ModelRef
    return [
        ModelRef("api", "claude-opus-4-5",            display_name="Opus 4.5"),
        ModelRef("api", DEFAULT_MODEL,                display_name="Sonnet 4.5"),
        ModelRef("api", "claude-haiku-4-5-20251001",  display_name="Haiku 4.5"),
    ]

SYSTEM_PROMPT = """You are LLSM, an AI assistant that drives SCIA Engineer (a structural engineering FEM program) to create and analyse structural models from natural-language requests by structural engineers.

You have tools to build models in memory (materials, cross-sections, nodes, beams, supports, load cases, loads) and to drive SCIA (launch, push the model, run analysis).

### Workflow

1. ALWAYS start with `new_model(name=...)` to initialize a fresh model.
2. Build defs in dependency order: material -> cross-section -> nodes -> beams -> supports -> load group -> load case -> loads.
3. ONLY after the model is complete: `launch_scia`, then `push_model`, then `run_analysis`.
4. ALWAYS call `close_scia` at the end — even on errors — so SCIA processes are cleaned up.
5. If a tool call fails, read the error and decide whether to retry with different args or report it. Never silently ignore errors.

### Units & conventions

- Lengths and coordinates in mm; line loads in kN/m. Mention the final units in your reply to the user.
- Global axes: X and Y horizontal, Z vertical (up positive). Gravity-like (downward) uniform loads use direction='Z' with a NEGATIVE magnitude.

### Naming

Give every object a concrete, descriptive short name ('col_left', 'rafter', 'base_L', 'perm_dead') — never 'obj1', 'tmp', 'x'.

### Response style

After the workflow, summarize what you built in 2-4 concise bullet lines — what's in the model and whether analysis succeeded.
"""


@dataclass
class AnthropicBackend:
    api_key: str | None = None
    model: str = DEFAULT_MODEL
    max_tokens: int = 4096
    verbose: bool = True
    # Optional callback fired around each tool invocation. Signature:
    #   on_tool_event(phase: "start"|"end", name: str, payload: Any)
    # phase=="start": payload is the args dict.
    # phase=="end":   payload is True/False (ok flag).
    # Runs synchronously on whatever thread the tool loop runs on.
    on_tool_event: Any = None

    _client: Anthropic = field(init=False)
    _messages: list[dict] = field(default_factory=list, init=False)
    _ctx: LlsmContext = field(default_factory=LlsmContext, init=False)

    def __post_init__(self) -> None:
        key = self.api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Set it as an env var or pass api_key= explicitly."
            )
        self._client = Anthropic(api_key=key)

    # --- Public entrypoint -------------------------------------------------

    def chat(self, user_message: str) -> str:
        """Run one user-turn through the agentic tool loop."""
        self._messages.append({"role": "user", "content": user_message})
        for iteration in range(MAX_TOOL_ITERATIONS):
            response = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                tools=TOOL_DEFS,
                messages=self._messages,
            )
            assistant_blocks = response.content
            self._messages.append({"role": "assistant", "content": assistant_blocks})

            if response.stop_reason != "tool_use":
                return self._extract_text(assistant_blocks)

            # Execute each tool_use block via the shared dispatcher.
            tool_results = []
            for block in assistant_blocks:
                if block.type != "tool_use":
                    continue
                result = invoke_tool(
                    block.name, block.input,
                    self._ctx, self.on_tool_event, verbose=self.verbose,
                )
                if self.verbose:
                    print(f"  -> {result}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, default=str),
                })
            self._messages.append({"role": "user", "content": tool_results})

        return "[ERROR] Hit max tool-use iterations without a final response."

    def close(self) -> None:
        self._ctx.close()

    def cancel(self) -> bool:
        """Hook for the panel's Cancel button — no-op for the API backend.

        The API backend runs synchronously inside chat(); there is no
        separate process to kill. Returning False matches ClaudeCodeBackend's
        contract for "nothing was running".
        """
        return False

    def available_models(self):
        return available_models()

    def restore_messages(self, transcript) -> None:
        """Replay a transcript into the in-memory message list.

        Best-effort fidelity: the JSONL transcript only carries user/llsm role
        + text, so any tool_use blocks from the original turns are lost. The
        model can re-orient from the conversational context. For a fully
        accurate replay we'd need to persist tool_use/tool_result blocks too.
        """
        msgs = []
        for m in transcript:
            role = "user" if m.role == "user" else "assistant" if m.role == "llsm" else None
            if role is None or not m.text:
                continue
            msgs.append({"role": role, "content": m.text})
        self._messages = msgs

    def model_snapshot(self) -> dict:
        """Return a structured snapshot of the in-memory ADM model.

        Used by the panel's sidebar. Schema is the locked contract in the
        plan (eight top-level keys). When no model has been built yet,
        returns the eight keys with empty lists (sidebar still renders
        zero-count rows consistently).
        """
        empty = {k: [] for k in (
            "materials", "sections", "nodes", "beams",
            "supports", "load_groups", "load_cases", "loads",
        )}
        builder = getattr(self._ctx, "builder", None)
        if builder is None:
            return empty
        try:
            return builder.snapshot()
        except Exception as e:
            # Don't leak builder internals to the panel; panel will _sysline.
            raise RuntimeError(f"snapshot failed: {type(e).__name__}: {e}") from e

    # --- Internals ---------------------------------------------------------

    @staticmethod
    def _extract_text(blocks: list) -> str:
        parts = []
        for b in blocks:
            if getattr(b, "type", None) == "text":
                parts.append(b.text)
        return "\n".join(parts).strip()


# ---------- Registry hook (CR-5) ----------

def build_backend(panel) -> "AnthropicBackend":
    """Panel-coupled factory used by `providers._resolve(...)`.

    Replaces the inline `if self._backend_kind == "api"` branch in
    `panel._ensure_backend`. Reads the active conversation's model and
    transcript off the panel and constructs the backend with the panel's
    tool-event callback wired in.
    """
    cur = panel._conv_session.current()
    model = (cur.model if cur else None) or panel._model or DEFAULT_MODEL
    backend = AnthropicBackend(
        model=model,
        verbose=False,
        on_tool_event=panel._on_tool_event_from_worker,
    )
    if cur is not None:
        try:
            backend.restore_messages(panel._conv_store.read_transcript(cur.id))
        except Exception as exc:
            import sys as _sys
            print(f"[panel] api restore_messages failed: {exc}", file=_sys.stderr)
    return backend
