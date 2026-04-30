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
    "cascade=True. If update_node fails, the response's dependent_beams "
    "lists every beam that references the moved node — use it to plan "
    "whether to reroute or delete those beams instead of retrying blindly."
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
        session_started: bool = False,
        on_session_rotated=None,  # callback(new_session_id: str) when we rotate
        on_orphan_cleanup=None,   # callback(session_id: str) -> int; kills orphan claudes
    ) -> None:
        self.model = model
        self.session_id = session_id or str(uuid.uuid4())
        # session_started=True when reattaching to a server whose session was
        # already in flight before this panel launched.
        self._session_started = session_started
        self.verbose = verbose
        self.cwd = str(cwd or REPO_ROOT)
        self.timeout_s = timeout_s
        self.mcp_config_json = mcp_config_json
        self._on_session_rotated = on_session_rotated
        self._on_orphan_cleanup = on_orphan_cleanup
        self._current_proc = None  # for Cancel button

    def _build_cmd(self, message: str) -> list[str]:
        cmd = [
            "claude",
            "-p",
            *(["--resume", self.session_id] if self._session_started else ["--session-id", self.session_id]),
            "--model", self.model,
            "--output-format", "json",
            "--permission-mode", "bypassPermissions",
        ]
        if self.mcp_config_json is not None:
            cmd += [
                "--mcp-config", self.mcp_config_json,
                "--strict-mcp-config",
                "--append-system-prompt", PERSISTENT_SYSTEM_PROMPT,
            ]
        cmd.append(message)
        return cmd

    def _run_once(self, message: str):
        cmd = self._build_cmd(message)
        if self.verbose:
            print(f"[claude-code] $ {cmd}")
        try:
            self._current_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd,
                shell=False,
                text=True,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "`claude` CLI not on PATH. Install Claude Code or use --backend api."
            )
        try:
            stdout, stderr = self._current_proc.communicate(timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            self._current_proc.kill()
            self._current_proc.communicate()
            raise RuntimeError(f"claude CLI timed out after {self.timeout_s}s")
        finally:
            rc = self._current_proc.returncode
            self._current_proc = None
        return rc, stdout or "", stderr or ""

    def cancel(self) -> bool:
        """Kill the active claude subprocess (Cancel button hits this).

        Returns True if a process was killed, False if nothing was running.
        Server-side MCP tool calls already in flight will complete; only the
        LLM-side claude process is killed, so its result is dropped.
        """
        proc = self._current_proc
        if proc is None or proc.poll() is not None:
            return False
        try:
            proc.kill()
        except Exception:
            return False
        return True

    @staticmethod
    def _is_session_in_use(stderr: str) -> bool:
        s = stderr.lower()
        return "session id" in s and "already in use" in s

    @staticmethod
    def _is_unknown_session(stderr: str) -> bool:
        s = stderr.lower()
        return (
            ("session" in s and ("not found" in s or "unknown" in s))
            or "no such session" in s
        )

    def _rotate_session_id(self) -> str:
        """Generate a fresh session id, drop _session_started so the next call uses --session-id."""
        new_id = str(uuid.uuid4())
        self.session_id = new_id
        self._session_started = False
        if self._on_session_rotated is not None:
            try:
                self._on_session_rotated(new_id)
            except Exception:
                pass
        return new_id

    def chat(self, message: str) -> str:
        """Send a message and return the LLM's reply.

        On the three known recoverable claude-side errors, retry once:
          - 'Session ... already in use' (orphan claude holds the lock):
            ask the panel to kill orphan claudes carrying our session-id,
            retry. If still blocked, rotate session-id and retry.
          - 'session not found / unknown' (claude GC'd the conversation):
            rotate session-id, retry.
        """
        rc, stdout, stderr = self._run_once(message)

        if rc != 0:
            stderr_strip = stderr.strip()[:2000]
            if self._is_session_in_use(stderr):
                if self._on_orphan_cleanup is not None:
                    try:
                        self._on_orphan_cleanup(self.session_id)
                    except Exception:
                        pass
                rc, stdout, stderr = self._run_once(message)
                if rc != 0 and self._is_session_in_use(stderr):
                    self._rotate_session_id()
                    rc, stdout, stderr = self._run_once(message)
            elif self._is_unknown_session(stderr):
                self._rotate_session_id()
                rc, stdout, stderr = self._run_once(message)

        if rc != 0:
            stderr_strip = (stderr or "").strip()[:1000]
            raise RuntimeError(f"claude exit {rc}: {stderr_strip}")

        out = (stdout or "").strip()
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return out

        if isinstance(data, dict):
            if data.get("type") == "result" and "result" in data:
                self._session_started = True
                return data["result"]
            for key in ("text", "message", "output"):
                if key in data:
                    return str(data[key])
        return out

    def close(self) -> None:
        # Subprocess-per-call; nothing persistent to close.
        pass

