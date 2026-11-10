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
import re
import subprocess
import threading
import uuid
from pathlib import Path

from ..answer_rules import ANSWER_RULES

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # src/llsm/llm/ -> repo
DEFAULT_MODEL = "opus"  # alias for latest Opus (currently 4.7)


def available_models():
    """Single source of truth for the claude-code model list.

    ModelCatalog imports this when no live backend is around to query
    (e.g. at panel startup before the first chat materialises the backend).
    The instance method `ClaudeCodeBackend.available_models` delegates here.
    """
    from ..history.models import ModelRef
    return [
        ModelRef("claude-code", "opus",   display_name="Opus (latest)"),
        ModelRef("claude-code", "sonnet", display_name="Sonnet (latest)"),
        ModelRef("claude-code", "haiku",  display_name="Haiku (latest)"),
    ]

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
    "whether to reroute or delete those beams instead of retrying blindly. "
    + ANSWER_RULES
)


_MCP_PREFIX = re.compile(r"^mcp__[^_]+(?:_[^_]+)*?__")


def parse_stream_line(line: str):
    """One stdout line of `--output-format stream-json` -> its event dict, or None."""
    line = (line or "").strip()
    if not line.startswith("{"):
        return None
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _result_status(block: dict) -> tuple[bool, str]:
    """(ok, error) of a tool_result block: an MCP error, or a tool's own {"ok": false}."""
    content = block.get("content")
    if isinstance(content, list):
        text = "\n".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
    else:
        text = str(content or "")
    if block.get("is_error"):
        return False, text
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return True, ""
    if isinstance(data, dict) and data.get("ok") is False:
        return False, str(data.get("error") or data.get("message") or "failed")
    return True, ""


def tool_events(event: dict, pending: dict) -> list[tuple]:
    """Tool steps in one stream event, as (phase, name, payload) for the panel.

    An assistant message's tool_use blocks start steps (payload: the input);
    a user message's tool_result blocks end them (payload: {"ok", "error"}).
    `pending` maps tool_use ids to names between the two.
    """
    out = []
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return out
    for block in content:
        if not isinstance(block, dict):
            continue
        if event.get("type") == "assistant" and block.get("type") == "tool_use":
            name = _MCP_PREFIX.sub("", str(block.get("name", "")))
            pending[str(block.get("id", ""))] = name
            out.append(("start", name, block.get("input") or {}))
        elif event.get("type") == "user" and block.get("type") == "tool_result":
            name = pending.pop(str(block.get("tool_use_id", "")), "tool")
            ok, error = _result_status(block)
            out.append(("end", name, {"ok": ok, "error": error}))
    return out


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
        on_tool_event=None,       # callback(phase, name, payload), as in tool_loop.invoke_tool
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
        self.on_tool_event = on_tool_event
        self._current_proc = None  # for Cancel button

    def _build_cmd(self, message: str) -> list[str]:
        cmd = [
            "claude",
            "-p",
            *(["--resume", self.session_id] if self._session_started else ["--session-id", self.session_id]),
            # No built-in tools at all: dontAsk still lets read-only shell commands through.
            # Both flags take a list: keep another option after them, or they eat the prompt.
            "--tools", "",
            "--allowedTools", "mcp__llsm__*",
            "--permission-mode", "dontAsk",
            "--model", self.model,
            # Stream the turn's events so the panel sees each tool call as it
            # happens; the last line is the same result object --output-format
            # json prints. -p needs --verbose for stream-json.
            "--output-format", "stream-json", "--verbose",
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
            shown = ["<mcp-config>" if i and cmd[i - 1] == "--mcp-config" else part for i, part in enumerate(cmd)]
            print(f"[claude-code] $ {shown}")  # the config holds the server's token
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd,
                shell=False,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            raise RuntimeError(
                "`claude` CLI not on PATH. Install Claude Code or use --backend api."
            )
        self._current_proc = proc
        # stderr drains on its own thread so a chatty CLI can't fill the pipe
        # while we read stdout line by line; a timer kills a turn that hangs.
        err: list[str] = []
        drain = threading.Thread(target=lambda: err.append(proc.stderr.read() or ""), daemon=True)
        drain.start()
        timed_out = threading.Event()

        def _expire():
            timed_out.set()
            try:
                proc.kill()
            except Exception:
                pass

        timer = threading.Timer(self.timeout_s, _expire)
        timer.daemon = True
        timer.start()
        lines: list[str] = []
        result_line = None
        pending: dict[str, str] = {}
        try:
            for line in proc.stdout:
                lines.append(line)
                event = parse_stream_line(line)
                if event is None:
                    continue
                if event.get("type") == "result":
                    result_line = line.strip()
                for phase, name, payload in tool_events(event, pending):
                    self._emit(phase, name, payload)
            proc.wait()
        finally:
            timer.cancel()
            drain.join(timeout=5)
            rc = proc.returncode
            self._current_proc = None
        if timed_out.is_set():
            raise RuntimeError(f"claude CLI timed out after {self.timeout_s}s")
        stdout = result_line if result_line is not None else "".join(lines)
        return rc, stdout, "".join(err)

    def _emit(self, phase, name, payload) -> None:
        if self.on_tool_event is None:
            return
        try:
            self.on_tool_event(phase, name, payload)
        except Exception:
            pass  # the panel's queue must never break the turn

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
        """True if claude CLI reports the session id is gone / not recognised.

        We accept several wordings because claude CLI has changed the message
        between versions. Currently observed:
          - "session not found"
          - "unknown session"
          - "No such session"
          - "No conversation found with session ID: ..."   (current claude CLI)

        The substring `'session' in s and 'not found' in s` does NOT match
        the last variant because 'no' and 'found' are separated by
        'conversation' - which is why we add the explicit alternative.
        """
        s = stderr.lower()
        return (
            ("session" in s and ("not found" in s or "unknown" in s))
            or "no such session" in s
            or "no conversation found" in s
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

    def available_models(self):
        return available_models()

    def model_snapshot(self) -> dict:
        """Return an empty-but-conformant snapshot.

        The real ADM model lives in the MCP subprocess we don't have
        bidirectional access to from here. The panel sidebar shows zero
        counts in claude-code mode — accepted degradation per the plan
        (section 4: "Acceptable degradation: in claude-code mode the
        sidebar may show counts only, not per-object detail").

        A future improvement would be to issue an out-of-band MCP
        `model_summary` call and parse the result; that needs a proper
        MCP client which we don't have here yet.
        """
        return {
            "materials":   [],
            "sections":    [],
            "nodes":       [],
            "beams":       [],
            "supports":    [],
            "load_groups": [],
            "load_cases":  [],
            "loads":       [],
        }

    def close(self) -> None:
        # Subprocess-per-call; nothing persistent to close.
        pass


# ---------- Registry hook (CR-5) ----------

def build_backend(panel) -> "ClaudeCodeBackend":
    """Panel-coupled factory used by `providers._resolve(...)`.

    Replaces the inline claude-code branch in `panel._ensure_backend`. Reads
    the live MCP handle, session-rotation / orphan-cleanup callbacks, and the
    active conversation's session_id off the panel.
    """
    cur = panel._conv_session.current()
    model = (cur.model if cur else None) or panel._model or DEFAULT_MODEL
    kwargs: dict = {
        "model": model,
        "verbose": False,
        "on_session_rotated": panel._on_session_rotated,
        "on_orphan_cleanup": panel._on_orphan_cleanup,
        "on_tool_event": panel._on_tool_event_from_worker,
    }
    if panel._mcp_handle is not None:
        kwargs["mcp_config_json"] = panel._mcp_handle.mcp_config_json

    # ConversationStore is authoritative for session_id.
    conv_sid = (cur.session_id if cur else "") or ""
    if conv_sid:
        kwargs["session_id"] = conv_sid
        # Non-empty conv.session_id always came from a prior claude run, so
        # the session ALREADY exists in claude's storage and the first chat
        # must use --resume.
        kwargs["session_started"] = True

    backend = ClaudeCodeBackend(**kwargs)

    # Persist the freshly-minted (or inherited) session_id back to the
    # SPECIFIC conv we read from (panel._ensure_backend can run on the chat
    # worker thread or the Tk thread; the active conv may change between the
    # cur read above and this write).
    if cur is not None:
        try:
            panel._conv_store.update_session_id(cur.id, backend.session_id)
        except Exception as exc:
            import sys as _sys
            print(f"[panel] update_session_id failed: {exc}", file=_sys.stderr)

    return backend

