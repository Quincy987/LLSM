"""Claude Code subprocess backend.

Spawns the `claude` CLI in --print mode for each user message, using a stable
session ID so the conversation persists across calls.

Two MCP-discovery modes:

* **Static `.mcp.json`** (legacy): claude auto-discovers it via cwd. SCIA gets
  launched fresh per turn — no state survives between turns.

* **Live `mcp_config_json`** (persistent): the panel spawned a long-lived MCP
  server and hands us its URL. We pass `--mcp-config <json> --strict-mcp-config`
  to claude on every turn so all turns hit the SAME server (and the same SCIA
  + in-memory ADM model).

Why this exists: the user is on a Max subscription, so going through the
Claude Code CLI is effectively zero marginal cost vs. paying per-token via the
Anthropic API. Same model (Opus 4.7), same tool surface, no API key needed.
"""
from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # src/llsm/llm/ -> repo
DEFAULT_MODEL = "opus"  # alias for latest Opus (currently 4.7)

# System-prompt snippet that runs ON TOP of the MCP server's instructions when
# the persistent server is in use. Centralised here so this backend and
# mcp_server.py don't drift on persistence/edit semantics.
PERSISTENT_SYSTEM_PROMPT = (
    "You are LLSM's structural assistant. This is a persistent session: SCIA "
    "Engineer and the in-memory ADM model survive between your turns. The "
    "panel manages SCIA's lifecycle — never try to close it. "
    "On the FIRST turn, build the model from scratch with new_model + add_*. "
    "On LATER turns, treat the user's request as an edit to the existing "
    "model: prefer update_node, delete_object, set_line_load_magnitude, "
    "set_member_cross_section, set_member_endpoints over a full rebuild. "
    "After any edit, just call run_analysis — the mesh re-generates "
    "automatically; do NOT call push_model again. "
    "If delete_object returns blockers, delete those first or pass "
    "cascade=True. If update_node or set_member_endpoints rolls back, the "
    "response lists the invalidated beams — adjust accordingly, don't retry "
    "blindly."
)


class ClaudeCodeBackend:
    """Spawns `claude -p` per chat call, persists conversation via session-id."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        session_id: str | None = None,
        verbose: bool = False,
        cwd: Path | None = None,
        timeout_s: int = 600,
        mcp_config_json: str | None = None,
    ) -> None:
        self.model = model
        self.session_id = session_id or str(uuid.uuid4())
        self.verbose = verbose
        self.cwd = str(cwd or REPO_ROOT)
        self.timeout_s = timeout_s
        self.mcp_config_json = mcp_config_json

    def chat(self, message: str) -> str:
        cmd = [
            "claude",
            "-p",
            "--session-id", self.session_id,
            "--model", self.model,
            "--output-format", "json",
            "--permission-mode", "bypassPermissions",
        ]
        if self.mcp_config_json is not None:
            # Strict mode = ignore .mcp.json and any other discovered MCP
            # servers; ONLY use the persistent server we point it at.
            cmd += [
                "--mcp-config", self.mcp_config_json,
                "--strict-mcp-config",
                "--append-system-prompt", PERSISTENT_SYSTEM_PROMPT,
            ]
        cmd.append(message)
        if self.verbose:
            print(f"[claude-code] $ {cmd}")
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=self.cwd,
                timeout=self.timeout_s,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"claude CLI timed out after {self.timeout_s}s")
        except FileNotFoundError:
            raise RuntimeError(
                "`claude` CLI not on PATH. Install Claude Code or use --backend api."
            )

        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()[:1000]
            raise RuntimeError(f"claude exit {proc.returncode}: {stderr}")

        # Parse JSON output: shape is {"type":"result","subtype":"success","result":"..."}
        out = proc.stdout.strip()
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return out  # fall back to raw text

        if isinstance(data, dict):
            if data.get("type") == "result" and "result" in data:
                return data["result"]
            for key in ("text", "message", "output"):
                if key in data:
                    return str(data[key])
        return out

    def close(self) -> None:
        # Subprocess-per-call; nothing persistent to close.
        pass

