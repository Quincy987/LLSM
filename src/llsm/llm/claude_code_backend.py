"""Claude Code subprocess backend.

Spawns the `claude` CLI in --print mode for each user message, using a stable
session ID so the conversation persists across calls. The CLI auto-discovers
our .mcp.json (we set cwd to the repo root), so all 18 LLSM tools are
available without us having to do anything in this file.

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
    ) -> None:
        self.model = model
        self.session_id = session_id or str(uuid.uuid4())
        self.verbose = verbose
        self.cwd = str(cwd or REPO_ROOT)
        self.timeout_s = timeout_s

    def chat(self, message: str) -> str:
        cmd = [
            "claude",
            "-p",
            "--session-id", self.session_id,
            "--model", self.model,
            "--output-format", "json",
            "--permission-mode", "bypassPermissions",
            message,
        ]
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

