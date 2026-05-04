"""LLSM panel - Tkinter chat window owned by SCIA Engineer.

Critical ordering: panel.log MUST be opened before any project imports so that
import-time failures (DLL loads in pythonnet, missing pywin32, etc.) land
somewhere visible. Otherwise a detached launch via `Start-Process -WindowStyle
Hidden` would silently fail with no terminal and no log.
"""
from __future__ import annotations

# === panel.log + tee setup (FIRST, no project imports yet) ===
import os
import sys
from datetime import datetime
from pathlib import Path

_LLSM_HOME = Path.home() / ".llsm"
try:
    _LLSM_HOME.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
_PANEL_LOG = _LLSM_HOME / "panel.log"
_CHAT_LOG = _LLSM_HOME / "chat.log"


class _Tee:
    """File-like object that writes to multiple underlying streams."""
    def __init__(self, *streams):
        self._streams = [s for s in streams if s is not None]
    def write(self, data):
        for s in self._streams:
            try:
                s.write(data)
            except Exception:
                pass
        return len(data) if data else 0
    def flush(self):
        for s in self._streams:
            try:
                s.flush()
            except Exception:
                pass
    def isatty(self):
        return False


try:
    _PANEL_LOG_FP = open(_PANEL_LOG, "a", encoding="utf-8", buffering=1)
    _PANEL_LOG_FP.write(f"\n=== panel start {datetime.now().isoformat()} pid={os.getpid()} ===\n")
    # Tee to file + original stdio (so terminal users still see output).
    sys.stdout = _Tee(sys.__stdout__, _PANEL_LOG_FP)
    sys.stderr = _Tee(sys.__stderr__, _PANEL_LOG_FP)
except Exception as _exc:
    # Best effort: if we can't open the log, fall through to plain stdio.
    print(f"[panel] could not open panel.log: {_exc}", file=sys.__stderr__)

# Chat log: user messages + LLM replies + system lines, with timestamps. Plain
# text, structured-ish ("USER:" / "LLSM:" / "SYS:" / "ERR:" prefix per line)
# so a tail of this file is enough to reconstruct the conversation without
# context-switching through panel.log's Tk noise.
try:
    _CHAT_LOG_FP = open(_CHAT_LOG, "a", encoding="utf-8", buffering=1)
    _CHAT_LOG_FP.write(f"\n=== chat session start {datetime.now().isoformat()} pid={os.getpid()} ===\n")
except Exception as _exc:
    _CHAT_LOG_FP = None
    print(f"[panel] could not open chat.log: {_exc}", file=sys.__stderr__)


def _chat_log(role: str, text: str) -> None:
    """Append a chat line to chat.log. Best-effort; never raises."""
    if _CHAT_LOG_FP is None:
        return
    try:
        ts = datetime.now().isoformat(timespec="seconds")
        # Indent multi-line content for readability
        body = text.rstrip().replace("\n", "\n    ")
        _CHAT_LOG_FP.write(f"[{ts}] {role}: {body}\n")
    except Exception:
        pass

# === Now safe to import project modules - failures land in panel.log ===
import argparse
import queue
import signal
import socket
import threading
import time
import tkinter as tk
from tkinter import scrolledtext, ttk

import psutil
import win32api
import win32con
import win32event
import win32gui
import win32process
import winerror

from . import mcp_supervisor
from .conversation.menu import FileMenuBuilder, HistoryDialog
from .conversation.session import ConversationSession
from .conversation.view import ConversationView
from .history import ConversationStore, ModelCatalog, ModelUsage
from .history.migration import migrate_legacy
from .llm.anthropic_backend import AnthropicBackend
from .llm.anthropic_backend import DEFAULT_MODEL as _ANTHROPIC_DEFAULT_MODEL
from .llm.claude_code_backend import ClaudeCodeBackend
from .llm.claude_code_backend import DEFAULT_MODEL as _CLAUDE_CODE_DEFAULT_MODEL
from .scia.loader import load as load_scia


def _default_model_for(backend_kind: str) -> str:
    return _CLAUDE_CODE_DEFAULT_MODEL if backend_kind == "claude-code" else _ANTHROPIC_DEFAULT_MODEL


GWLP_HWNDPARENT = -8
SCIA_PROC_NAMES = {"sciaengineer.exe"}
POLL_INTERVAL_MS = 250
HEALTH_PING_INTERVAL_MS = 30_000
HEALTH_PING_FAST_INTERVAL_MS = 5_000  # ping faster after first failure
# Watchdog tolerance. We previously had 6 (~30 s) but launch_scia takes
# 30-60 s and blocks the FastMCP event loop while SCIA's HubServer comes up,
# tripping a false-positive auto-restart that force-kills SCIA mid-launch.
# Bumped to 18 (~90 s) as a band-aid; the proper fix is to run MCP tool
# bodies on a thread pool so /admin/ping always responds, removing the
# need for a long timeout. Tracked in the todo list.
MCP_HANG_RESTART_AFTER_FAILS = 18  # ~90 s of failures before auto-restart
MCP_RESTART_COOLDOWN_S = 60  # don't auto-restart again within this many seconds
MCP_MAX_RESTARTS_PER_SESSION = 5  # then give up and ask the user
HEARTBEAT_INTERVAL_MS = 10_000
SIGNAL_PIPE_DRAIN_MS = 100
PANEL_WIDTH = 460
SINGLE_INSTANCE_MUTEX = "Global\\LLSMPanel"

# --- Theme reference values (light-theme baked-in fallbacks) ---
# Used when ttk.Style().lookup() returns nothing usable, or as the accent
# hues that _tint() blends the resolved theme bg toward. Per the plan's
# theme rules: glyph + text first, color second; never color alone.
_THEME_REFERENCE = {
    # Surfaces (used as fallbacks when the theme lookup fails).
    "bg":            "#ffffff",
    "fg":            "#202020",
    "muted":         "#777777",
    "insert":        "#202020",
    "separator":     "#cccccc",
    # Role accent hues (these get blended into the theme bg via _tint).
    "user_accent":   "#1e6091",
    "llsm_accent":   "#2d6a4f",
    "error_accent":  "#d62828",
    "system_accent": "#888888",
    # Status pill accents.
    "thinking":      "#d29922",
    "running":       "#1e6091",
    "idle":          "#888888",
}

# Glyph vocabulary (per plan section "Theme & accessibility ground rules").
_GLYPH_USER = "[YOU]"
_GLYPH_LLSM = "[LLSM]"
_GLYPH_ERR  = "[ERROR]"
_GLYPH_SYS  = "[SYS]"
_GLYPH_BAR  = "▎"   # ▎ left accent bar
_GLYPH_SEP  = "─" * 36  # row of ─ for separators
_GLYPH_PILL_IDLE     = "●"  # ●
_GLYPH_PILL_THINKING = "◐"  # ◐
_GLYPH_PILL_RUNNING  = "▶"  # ▶
_GLYPH_PILL_ERROR    = "⚠"  # ⚠

# Sidebar.
SIDEBAR_WIDTH = 200
SIDEBAR_CATEGORIES = (
    ("materials",   "Materials"),
    ("sections",    "Sections"),
    ("nodes",       "Nodes"),
    ("beams",       "Beams"),
    ("supports",    "Supports"),
    ("load_groups", "Load groups"),
    ("load_cases",  "Load cases"),
    ("loads",       "Loads"),
)
# All eight schema keys (used for conformance checks too).
SIDEBAR_SCHEMA_KEYS = tuple(k for k, _ in SIDEBAR_CATEGORIES)

# Input auto-grow bounds.
INPUT_MIN_LINES = 4
INPUT_MAX_LINES = 10

# Pill error auto-clear.
PILL_ERROR_CLEAR_MS = 3000

# Placeholder text for the input.
PLACEHOLDER_TEXT = "Describe your model... (Enter sends, Shift+Enter newline)"


def _find_scia_main_hwnd():
    pids = {p.info["pid"] for p in psutil.process_iter(["pid", "name"])
            if p.info["name"] and p.info["name"].lower() in SCIA_PROC_NAMES}
    if not pids:
        return None
    candidates = []
    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid not in pids:
            return True
        title = win32gui.GetWindowText(hwnd) or ""
        if "SCIA Engineer" not in title:
            return True
        if "Startup Screen" in title or "Splash" in title:
            return True
        rect = win32gui.GetWindowRect(hwnd)
        area = (rect[2] - rect[0]) * (rect[3] - rect[1])
        candidates.append((area, hwnd))
        return True
    win32gui.EnumWindows(cb, None)
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


class LlsmPanel:
    def __init__(self, *, model=None, backend_kind="claude-code", connect_existing=False, always_on_top=False, dev_mode=False):
        self.scia_hwnd = None
        self.outbox = queue.Queue()
        self.busy = False
        self._closing = False
        self._backend = None
        self._model = model
        self._backend_kind = backend_kind
        self._connect_existing = connect_existing
        self._always_on_top = always_on_top
        self._dev_mode = dev_mode
        self._mcp_handle = None  # mcp_supervisor.McpHandle | None
        self._mcp_status = "unknown"  # "up" | "down" | "unknown"
        self._mcp_ping_fails = 0  # consecutive failures since last success
        self._mcp_last_restart = 0.0  # time.monotonic() of last auto-restart
        self._mcp_restart_count = 0  # total auto-restarts this session
        self._mcp_restart_disabled = False  # set True after MAX_RESTARTS
        # Tracking for --dev hot-reload coordination.
        self._mcp_restart_in_progress = False  # set during _restart_mcp_core
        self._mcp_spawn_wall = 0.0  # time.time() (wall clock) of last successful spawn;
                                    # the dev watcher compares file mtimes against this
                                    # to skip events the running MCP already loaded.
        self._dev_watcher = None  # dev_watcher._DevWatcher | None
        # Tools > Restart app: when True, main() spawns a fresh panel
        # process AFTER releasing the single-instance mutex.
        self._restart_requested = False

        # Signal pipe (Item 1: Ctrl+C handling on Windows Tk).
        # socketpair, NOT os.pipe: signal.set_wakeup_fd on Windows requires
        # a socket handle - pipe FDs silently do nothing.
        self._sig_read_sock = None
        self._sig_write_sock = None

        # UX/UI state (per plan: theme + pill + sidebar + placeholder).
        self._theme_colors: dict[str, str] = {}    # populated by _apply_theme
        self._tint_warned = False                  # one-shot _sysline guard
        self._placeholder_active = False
        self._active_tools: list[str] = []         # stack of in-flight tools for pill
        self._pill_error_after = None              # after-id for auto-clear
        self._sidebar_visible = True
        self._sidebar_fetch_inflight = False
        self._sidebar_fetch_dirty = False

        # Set in _send before each chat, read in _dispatch_outbox_event so a
        # mid-flight conversation switch can drop the reply rather than write
        # it into the new active conversation.
        self._inflight_conv_id: str | None = None

        # --- Conversation history (store/session/view + one-shot migration) ---
        self._conv_store = ConversationStore(_LLSM_HOME / "conversations")
        self._model_usage = ModelUsage(_LLSM_HOME / "model_usage.json")
        # Register each backend's static `available_models()` with the catalog
        # so the model list is queryable BEFORE any backend has been
        # instantiated (api backend requires ANTHROPIC_API_KEY just to
        # construct). Keeps the history/ layer free of llm/ imports.
        from .llm.claude_code_backend import available_models as _cc_models
        from .llm.anthropic_backend import available_models as _api_models
        # New providers registered via lazy thunks: importing
        # `openai_backend.available_models` would force `import openai` even
        # when the user hasn't installed it. Wrap in a thunk that imports on
        # call. _resolve in providers.py would also work but threading the
        # spec through here is heavier than the inline lambda.
        def _openai_models():
            try:
                from .llm.openai_backend import available_models
            except ImportError:
                return []
            return available_models()
        def _gemini_models():
            try:
                from .llm.gemini_backend import available_models
            except ImportError:
                return []
            return available_models()
        def _openai_compat_models():
            try:
                from .llm.openai_compatible_backend import available_models
            except ImportError:
                return []
            return available_models()
        self._model_catalog = ModelCatalog(providers={
            "claude-code":       _cc_models,
            "api":               _api_models,
            "openai":            _openai_models,
            "gemini":            _gemini_models,
            "openai-compatible": _openai_compat_models,
        })
        try:
            migrate_legacy(
                store=self._conv_store,
                legacy_session_id_file=mcp_supervisor.LEGACY_SESSION_ID_FILE,
                legacy_chat_log=_CHAT_LOG,
                backend_kind=self._backend_kind,
                default_model=self._model or _default_model_for(self._backend_kind),
            )
        except Exception as exc:
            print(f"[panel] migration failed: {exc}", file=sys.stderr)
        self._conv_session = ConversationSession(
            store=self._conv_store,
            usage=self._model_usage,
            catalog=self._model_catalog,
            cancel_in_flight=self._cancel_inflight_for_switch,
        )
        # ConversationView is a thin Tk adapter constructed AFTER _build_ui
        # since it needs the chat-history widget and the title StringVar.
        # Constructed below in _build_ui.
        self._conv_view: ConversationView | None = None
        # Ensure there's always an active conversation so first-send works
        # even on a clean install where migration found nothing to import.
        try:
            self._conv_session.ensure_active(
                backend_kind=self._backend_kind,
                model=self._model or _default_model_for(self._backend_kind),
            )
        except Exception as exc:
            print(f"[panel] ensure_active failed: {exc}", file=sys.stderr)

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._install_sigint_pipe()
        self._start_mcp_server()
        if self._dev_mode:
            self._start_dev_watcher()
        self._poll_scia()
        self._drain_outbox()
        self._drain_signal_pipe()
        self._schedule_health_ping()
        self._heartbeat_tick()
        # Kick a startup sidebar refresh once Tk is settled. This shows the
        # initial (likely empty) model state so users can confirm the panel
        # is wired to the backend.
        self._safe_after(500, self._kick_startup_sidebar_refresh)

    # -------- Conversation switching helpers --------

    def _cancel_inflight_for_switch(self):
        """Cancel any in-flight chat so the next backend resume starts clean.

        Wired into ConversationSession so `new_conversation()` and `switch_to()`
        can cancel without importing Tk or knowing about backends.
        """
        if self._backend is not None:
            try:
                self._backend.cancel()
            except Exception:
                pass

    def _reply_belongs_to_active(self) -> bool:
        """True if the active conversation is still the one chat was started in.

        ClaudeCodeBackend.cancel() kills the subprocess so its reply never
        arrives, but AnthropicBackend.cancel() is a no-op — the api request
        finishes, the worker thread enqueues the reply, and without this
        guard the Tk dispatch would write that reply into whichever
        conversation is now active.
        """
        if self._inflight_conv_id is None:
            return True
        return self._conv_session.current_id() == self._inflight_conv_id

    def _kick_startup_sidebar_refresh(self):
        # Materialize the backend so we have something to query, then refresh.
        try:
            self._ensure_backend()
        except Exception:
            return
        self._request_sidebar_refresh()

    # -------- Signal handling (Item 1) --------

    def _install_sigint_pipe(self):
        """Wire SIGINT (Ctrl+C in launching terminal) into the Tk thread.

        On Windows, Tk's mainloop() runs a C-level message pump that doesn't
        dispatch Python signal handlers. signal.set_wakeup_fd() writes a byte
        to a fd whenever a signal arrives, and we poll that fd via root.after().

        Critical detail: signal.set_wakeup_fd on Windows requires a SOCKET
        handle, not a pipe FD. socket.socketpair() works on Windows (uses an
        AF_INET loopback pair under the hood) and on POSIX. Using os.pipe()
        here is silently broken on Windows - the wakeup byte goes nowhere
        and Ctrl+C in the launching terminal does NOT trigger _on_close.
        """
        try:
            rsock, wsock = socket.socketpair()
        except Exception as exc:
            print(f"[panel] could not create signal socketpair: {exc}", file=sys.stderr)
            return
        try:
            rsock.setblocking(False)
            wsock.setblocking(False)
            self._sig_read_sock = rsock
            self._sig_write_sock = wsock
            signal.set_wakeup_fd(wsock.fileno())
            # Empty handler: required so Python actually runs *something* on
            # SIGINT and writes to the wakeup fd. The default behaviour is
            # KeyboardInterrupt, which Tk's mainloop swallows on Windows.
            signal.signal(signal.SIGINT, lambda *_: None)
            if hasattr(signal, "SIGBREAK"):
                signal.signal(signal.SIGBREAK, lambda *_: None)
        except Exception as exc:
            print(f"[panel] could not install SIGINT handler: {exc}", file=sys.stderr)
            try: rsock.close()
            except Exception: pass
            try: wsock.close()
            except Exception: pass
            self._sig_read_sock = None
            self._sig_write_sock = None

    def _drain_signal_pipe(self):
        """Tk after-callback: any byte on the wakeup socket means a signal arrived."""
        if self._closing:
            return
        if self._sig_read_sock is not None:
            try:
                data = self._sig_read_sock.recv(1024)
            except (BlockingIOError, OSError):
                data = b""
            if data:
                print("[panel] signal received via wakeup socket -> closing", file=sys.stderr)
                # We ARE the Tk thread; schedule close at idle.
                self.root.after(0, self._on_close)
                return  # don't reschedule; close handler will tear down
        self._safe_after(SIGNAL_PIPE_DRAIN_MS, self._drain_signal_pipe)


    # -------- Diagnostic instrumentation --------

    def _log_callback_exc(self, exc, val, tb):
        """Tk uncaught-exception hook. Writes the full traceback to panel.log.

        Without this override, exceptions raised inside `after()` callbacks
        on Windows can terminate mainloop with no Python-side trace at all -
        the user sees the panel disappear and panel.log shows nothing.
        """
        import traceback as _tb
        msg = "".join(_tb.format_exception(exc, val, tb))
        try:
            print(f"[panel] UNCAUGHT in Tk callback:\n{msg}", file=sys.stderr)
        except Exception:
            pass
        # Don't re-raise; we want mainloop to keep running.

    def _safe_after(self, delay_ms, callback, *args):
        """Wrapper for root.after that catches + logs any exception in the
        scheduled callback, then re-schedules at the same interval.

        Without this, a one-time transient error (e.g. SCIA process gone away
        between our two reads) kills the polling loop permanently because
        the error propagates out and Tk drops the after-handler.
        """
        def _wrap():
            if self._closing:
                return
            try:
                callback(*args)
            except Exception as exc:
                import traceback as _tb
                tb_str = _tb.format_exc()
                try:
                    print(f"[panel] {callback.__name__} raised; rescheduling. {exc!r}\n{tb_str}", file=sys.stderr)
                except Exception:
                    pass
                # Reschedule so the loop survives the error.
                try:
                    self.root.after(delay_ms, _wrap)
                except Exception:
                    pass
        try:
            self.root.after(delay_ms, _wrap)
        except Exception as exc:
            print(f"[panel] after() schedule failed for {callback.__name__}: {exc}", file=sys.stderr)

    def _heartbeat_tick(self):
        """Write a timestamped line to panel.log every 10 s.

        If the panel dies, the gap between the last heartbeat and 'now'
        tells us within ~10 s when it happened, which scopes the search
        in the surrounding stderr noise.
        """
        if self._closing:
            return
        try:
            print(f"[panel] heartbeat {datetime.now().isoformat(timespec='seconds')}", file=sys.stderr)
        except Exception:
            pass
        self._safe_after(HEARTBEAT_INTERVAL_MS, self._heartbeat_tick)

    # -------- MCP supervisor lifecycle --------

    def _start_mcp_server(self):
        """Connect to a running supervisor (Item 7) or spawn a fresh one."""
        if self._backend_kind != "claude-code":
            self._sysline("backend=api: persistent MCP server not started")
            return

        # Item 7: try connect_to_existing first.
        if self._connect_existing:
            try:
                handle = mcp_supervisor.connect_to_existing(verbose=True)
                if handle is not None:
                    self._mcp_handle = handle
                    self._mcp_status = "up"
                    # We don't know when the OTHER panel spawned this MCP, so
                    # use 'now' as a worst-case approximation: the dev watcher
                    # will only restart on files modified AFTER reattach.
                    self._mcp_spawn_wall = time.time()
                    cur_conv = self._conv_session.current()
                    sid = (cur_conv.session_id if cur_conv else "") or ""
                    self._sysline(
                        f"reattached to MCP server at {handle.url}"
                        + (f" (resuming session {sid[:8]}...)" if sid else "")
                    )
                    return
                self._sysline("--connect-to-existing: no live supervisor found, spawning fresh")
            except Exception as exc:
                self._sysline(f"connect-to-existing failed: {exc}; spawning fresh")

        # Spawn fresh.
        try:
            self._mcp_handle = mcp_supervisor.spawn(verbose=True)
            self._mcp_status = "up"
            self._mcp_spawn_wall = time.time()
            self._sysline(f"MCP server up at {self._mcp_handle.url}")
        except Exception as exc:
            self._mcp_status = "down"
            self._sysline(f"MCP server failed to start: {type(exc).__name__}: {exc}")
            self._mcp_handle = None
        self._refresh_status_bar()

    # -------- Health ping (Item 5) --------

    def _schedule_health_ping(self):
        if self._closing:
            return
        # Once we've seen a single failure, ping faster so we either confirm
        # recovery quickly or trip the auto-restart sooner.
        interval = HEALTH_PING_FAST_INTERVAL_MS if self._mcp_ping_fails > 0 else HEALTH_PING_INTERVAL_MS
        self._safe_after(interval, self._health_ping_tick)

    def _health_ping_tick(self):
        if self._closing:
            return
        if self._mcp_handle is not None:
            ok = mcp_supervisor.health_ping(self._mcp_handle)
            new_status = "up" if ok else "down"
            if ok:
                self._mcp_ping_fails = 0
            else:
                self._mcp_ping_fails += 1
            if new_status != self._mcp_status:
                print(f"[panel] mcp health: {self._mcp_status} -> {new_status}", file=sys.stderr)
                self._mcp_status = new_status
                self._refresh_status_bar()
                if not ok:
                    self._sysline("MCP server unreachable; will keep pinging")
            # Auto-restart trigger: enough consecutive failures + cooldown ok
            if (
                not ok
                and self._mcp_ping_fails >= MCP_HANG_RESTART_AFTER_FAILS
                and not self._mcp_restart_disabled
            ):
                if self._mcp_restart_count >= MCP_MAX_RESTARTS_PER_SESSION:
                    self._mcp_restart_disabled = True
                    self._sysline(
                        f"MCP has hung {self._mcp_restart_count} times this session - "
                        "giving up auto-restart. Close and relaunch the panel manually."
                    )
                else:
                    now = time.monotonic()
                    if now - self._mcp_last_restart >= MCP_RESTART_COOLDOWN_S:
                        self._auto_restart_mcp(reason="ping-timeout")
                        return  # _auto_restart_mcp reschedules
        self._schedule_health_ping()

    def _auto_restart_mcp(self, *, reason: str) -> None:
        """Watchdog / user-requested MCP restart. Counts against the per-session
        restart budget. The actual kill+respawn body lives in _restart_mcp_core
        so the dev-mode hot-reload path can reuse it without consuming budget.

        The watchdog path enters here from `_health_ping_tick`, which returns
        early after this call — so we have to (re)schedule the next ping here.
        The dev path (which calls `_restart_mcp_core` directly) does NOT
        reschedule, since the regular ping loop is still ticking.
        """
        self._mcp_last_restart = time.monotonic()
        self._mcp_restart_count += 1
        self._sysline(
            f"MCP server appears hung ({reason}); auto-restart "
            f"#{self._mcp_restart_count}/{MCP_MAX_RESTARTS_PER_SESSION}..."
        )
        self._restart_mcp_core(reason=reason)
        self._schedule_health_ping()

    def _restart_mcp_core(self, *, reason: str) -> None:
        """Kill + respawn the MCP server. Shared body between the watchdog
        (`_auto_restart_mcp`) and the dev hot-reload path.

        Does NOT touch `_mcp_restart_count` or `_mcp_last_restart`; the caller
        decides whether the restart should count against the watchdog's
        per-session budget (it should for hangs and user-clicks; it should not
        for source-code saves under --dev).

        Sets `_mcp_restart_in_progress` for the duration so the dev watcher
        can defer events that arrive mid-restart.
        """
        self._mcp_restart_in_progress = True
        try:
            # Cancel any in-flight chat (strands the user's claude subprocess too,
            # since it's blocked waiting on the hung MCP).
            if self._backend is not None:
                try:
                    self._backend.cancel()
                except Exception as exc:
                    print(f"[panel] backend.cancel during restart failed: {exc}", file=sys.stderr)
            # Force-kill any orphan claudes still holding our session-id lock.
            if self._backend is not None and getattr(self._backend, "session_id", None):
                try:
                    mcp_supervisor.kill_orphan_claudes(self._backend.session_id, verbose=True)
                except Exception as exc:
                    print(f"[panel] kill_orphan_claudes during restart failed: {exc}", file=sys.stderr)
            # Force-kill the hung supervisor (don't try graceful - admin route is wedged).
            if self._mcp_handle is not None:
                try:
                    mcp_supervisor.force_kill(self._mcp_handle, verbose=True)
                except Exception as exc:
                    print(f"[panel] force_kill during restart failed: {exc}", file=sys.stderr)
                self._mcp_handle = None
            # Respawn.
            try:
                self._mcp_handle = mcp_supervisor.spawn(verbose=True)
                self._mcp_status = "up"
                self._mcp_ping_fails = 0
                self._mcp_spawn_wall = time.time()
                # Discard the stale backend so the next chat reconstructs with the
                # new mcp_config_json (port may have changed) and the (possibly
                # rotated) session_id.
                self._backend = None
                self.busy = False
                self.send_btn.config(state="normal", text="Send (Enter)")
                self.cancel_btn.config(state="disabled")
                self._sysline(
                    f"MCP server restarted at {self._mcp_handle.url}; "
                    "conversation continues via session-id. Re-send your last message if needed."
                )
                self._refresh_status_bar()
            except Exception as exc:
                self._sysline(f"MCP restart FAILED: {type(exc).__name__}: {exc}")
                self._mcp_status = "down"
                self._refresh_status_bar()
        finally:
            self._mcp_restart_in_progress = False
        # Note: caller is responsible for (re)scheduling health pings.
        # `_auto_restart_mcp` does so because `_health_ping_tick` returned
        # early. The dev hot-reload path does NOT, because the ping loop
        # has been ticking continuously and would double up.

    # -------- Dev-mode hot-reload (--dev) --------

    def _start_dev_watcher(self):
        """Spin up the file watcher for `--dev` hot-reload.

        No-op (with a SYS warning) when `watchdog` isn't installed or when
        the backend has no MCP layer. The watcher runs in its own thread;
        all callbacks are marshaled back here via `root.after(0, ...)`.
        """
        if self._backend_kind != "claude-code":
            self._sysline("--dev: backend=api has no MCP layer; hot-reload disabled")
            return
        try:
            from .dev_watcher import _DevWatcher
        except ImportError as exc:
            missing = getattr(exc, "name", None) or str(exc)
            self._sysline(
                f"--dev: 'watchdog' package not available ({missing}); "
                "hot-reload disabled. Run: pip install -e .[dev]"
            )
            return
        src_root = Path(__file__).resolve().parent
        try:
            self._dev_watcher = _DevWatcher(
                src_root=src_root,
                tk_call=lambda fn: self.root.after(0, fn),
                on_mcp_change=self._on_dev_mcp_change,
                on_panel_change=self._on_dev_panel_change,
                is_mcp_busy=lambda: self._mcp_restart_in_progress,
                spawn_wall_time=lambda: self._mcp_spawn_wall,
            )
            self._dev_watcher.start()
            self._sysline(f"--dev: watching {src_root} for source changes")
        except Exception as exc:
            self._dev_watcher = None
            self._sysline(f"--dev: failed to start watcher: {type(exc).__name__}: {exc}")

    @staticmethod
    def _format_dev_files_label(files):
        """Format a file-list for a dev-mode SYS line: top-3 names + "(+N more)"."""
        label = ", ".join(files[:3])
        if len(files) > 3:
            label += f" (+{len(files) - 3} more)"
        return label

    def _on_dev_mcp_change(self, files):
        """Tk-thread callback when MCP-side source files were edited.

        Silent-skip when MCP is unavailable (no running supervisor) or the
        watchdog kill switch is on — emitting a SYS line per save would spam
        chat.log on every Ctrl+S during a "MCP can't start" debug session,
        and there's nothing the user can do from the message anyway.
        """
        if self._closing:
            return
        if self._mcp_handle is None or self._mcp_restart_disabled:
            return
        label = self._format_dev_files_label(files)
        self._sysline(f"code change in {label} - restarting MCP server")
        self._restart_mcp_core(reason=f"dev hot-reload: {label}")

    def _on_dev_panel_change(self, files):
        """Tk-thread callback when panel-side source files were edited."""
        if self._closing:
            return
        label = self._format_dev_files_label(files)
        self._sysline(
            f"code change in {label} - restart panel manually to pick up changes"
        )

    def _refresh_status_bar(self):
        scia_part = (
            f"SCIA: running (hwnd 0x{self.scia_hwnd:x})" if self.scia_hwnd
            else "SCIA: not detected"
        )
        mcp_part = f"MCP: {self._mcp_status}"
        if self._mcp_restart_count > 0:
            mcp_part += f" (restarts: {self._mcp_restart_count})"
        self.status_var.set(f"{scia_part}    |    {mcp_part}")

    # -------- Close handler --------

    def _on_close(self):
        if self._closing:
            return
        self._closing = True
        # Stop the dev watcher first so it can't fire mid-shutdown and try
        # to schedule callbacks against a Tk root that's about to be destroyed.
        if self._dev_watcher is not None:
            try:
                self._dev_watcher.stop()
            except Exception as exc:
                print(f"[panel] dev watcher stop failed: {exc}", file=sys.stderr)
            self._dev_watcher = None
        # Cancel any in-flight chat before tearing down MCP.
        if self._backend is not None:
            try:
                self._backend.cancel()
            except Exception:
                pass
        # Tear down MCP supervisor.
        if self._mcp_handle is not None:
            try:
                mcp_supervisor.shutdown(self._mcp_handle, verbose=True)
            except Exception as exc:
                print(f"[panel] MCP shutdown failed: {exc}", file=sys.stderr)
            self._mcp_handle = None
        try:
            self.root.destroy()
        except Exception:
            pass

    # -------- Menu --------

    def _build_menu(self):
        """Tk menu bar. 'Tools > Restart MCP server' lets the user force-restart
        without touching Task Manager when something looks wedged. Bound to
        Ctrl+R as well for muscle memory.
        """
        menubar = tk.Menu(self.root)
        # File menu first (left-most), built lazily so cascades repopulate
        # each time the user opens the menu.
        file_builder = FileMenuBuilder(
            root=self.root,
            session=self._conv_session,
            catalog=self._model_catalog,
            usage=self._model_usage,
            backend_provider=lambda: self._backend,
            on_new=self._on_menu_new_chat,
            on_resume=self._on_menu_resume,
            on_switch_model=self._on_menu_switch_model,
            on_open_history=self._on_menu_open_history,
            open_wizard_cb=self._on_menu_setup_credentials,
        )
        menubar.add_cascade(label="File", menu=file_builder.build(menubar))
        tools = tk.Menu(menubar, tearoff=False, postcommand=self._refresh_tools_menu)
        tools.add_command(label="Set up credentials…",
                          command=self._on_menu_setup_credentials)
        tools.add_command(label="Manage providers…",
                          command=self._on_menu_manage_providers)
        # Per-conversation SCIA tools toggle (Phase B / B4). The variable is
        # synced from the active conversation each time the menu opens via
        # postcommand=_refresh_tools_menu.
        self._tools_enabled_var = tk.BooleanVar(value=True)
        tools.add_checkbutton(
            label="Enable SCIA tools for this chat",
            variable=self._tools_enabled_var,
            command=self._on_toggle_tools_enabled,
        )
        tools.add_separator()
        # Disabled when backend has no MCP layer (api / openai / gemini / etc.).
        state = "normal" if self._backend_kind == "claude-code" else "disabled"
        tools.add_command(
            label="Restart MCP server  Ctrl+R",
            command=self._on_menu_restart_mcp,
            state=state,
        )
        tools.add_separator()
        tools.add_command(label="Open panel.log folder", command=self._on_menu_open_log_folder)
        tools.add_separator()
        tools.add_command(label="Restart app  Ctrl+Shift+R",
                          command=self._on_menu_restart_app)
        # Global keyboard shortcut for restart-app, regardless of focus.
        self.root.bind_all("<Control-Shift-R>",
                           lambda e: (self._on_menu_restart_app(), "break"))
        self.root.bind_all("<Control-Shift-r>",
                           lambda e: (self._on_menu_restart_app(), "break"))
        menubar.add_cascade(label="Tools", menu=tools)
        # View menu: sidebar toggle.
        view = tk.Menu(menubar, tearoff=False)
        self._sidebar_visible_var = tk.BooleanVar(value=True)
        view.add_checkbutton(
            label="Show Model Sidebar",
            onvalue=True, offvalue=False,
            variable=self._sidebar_visible_var,
            command=self._on_toggle_sidebar,
        )
        menubar.add_cascade(label="View", menu=view)
        self.root.config(menu=menubar)
        # Keyboard shortcut for the restart action.
        if self._backend_kind == "claude-code":
            self.root.bind_all("<Control-r>", lambda e: (self._on_menu_restart_mcp(), "break"))

    def _on_menu_restart_mcp(self):
        """User-requested MCP restart. Bypasses the watchdog cooldown but still
        respects the max-restarts kill switch (so stuck-in-a-loop bugs don't
        let the user spam restarts forever)."""
        if self._closing:
            return
        # api backend has no MCP layer - menu item is normally disabled, but
        # guard here too in case it gets clicked via keyboard shortcut.
        if self._backend_kind != "claude-code":
            self._sysline("backend=api has no MCP server")
            return
        if self._mcp_handle is None:
            self._sysline("no MCP server to restart - try relaunching the panel")
            return
        if self._mcp_restart_disabled:
            self._sysline("auto-restart kill switch is on; close + relaunch the panel")
            return
        # User explicitly asked, so reset the cooldown clock so this fires now
        # rather than waiting through MCP_RESTART_COOLDOWN_S.
        self._mcp_last_restart = 0.0
        self._auto_restart_mcp(reason="user-requested")

    def _on_menu_open_log_folder(self):
        """Open ~/.llsm in Explorer so user can grab logs / state files easily."""
        try:
            os.startfile(str(_LLSM_HOME))
        except Exception as exc:
            self._sysline(f"could not open log folder: {exc}")

    def _on_menu_setup_credentials(self):
        """Open the credential wizard. Lazy import keeps panel startup snappy
        and lets a user without `keyring` still launch the panel."""
        if self._closing:
            return
        try:
            from .conversation.credential_wizard import CredentialWizard
        except Exception as exc:
            self._sysline(f"credential wizard unavailable: {exc}")
            return
        wiz = CredentialWizard(
            self.root,
            on_saved=self._on_credentials_saved,
        )
        # Modal — don't block Tk; the dialog grabs focus itself.

    def _on_menu_manage_providers(self):
        if self._closing:
            return
        try:
            from .conversation.credential_wizard import ManageProvidersDialog
        except Exception as exc:
            self._sysline(f"manage-providers dialog unavailable: {exc}")
            return
        ManageProvidersDialog(
            self.root,
            on_changed=self._on_credentials_saved,
        )

    def _refresh_tools_menu(self):
        """Sync the 'Enable SCIA tools' checkmark with the active conv state."""
        if self._closing:
            return
        try:
            cur = self._conv_session.current()
            val = bool(getattr(cur, "tools_enabled", True)) if cur is not None else True
            self._tools_enabled_var.set(val)
        except Exception:
            pass

    def _on_toggle_tools_enabled(self):
        """User toggled SCIA tool-use for the active chat. Persist + rebuild."""
        if self._closing:
            return
        cur = self._conv_session.current()
        if cur is None:
            return
        new_val = bool(self._tools_enabled_var.get())
        try:
            self._conv_store.update_tools_enabled(cur.id, new_val)
        except Exception as exc:
            self._sysline(f"could not update tools_enabled: {exc}")
            return
        # Force a backend rebuild on next chat so the new flag takes effect.
        old = self._backend
        if old is not None:
            try: old.cancel()
            except Exception: pass
            try: old.close()
            except Exception: pass
        self._backend = None
        state = "enabled" if new_val else "disabled"
        self._sysline(f"SCIA tools {state} for this chat")

    def _on_credentials_saved(self, kind: str | None):
        """Wizard / Manage-providers callback: invalidate the catalog cache for
        that provider so the next menu cascade re-fetches with the new key."""
        try:
            self._model_catalog.invalidate(kind)
        except Exception as exc:
            print(f"[panel] catalog invalidate failed: {exc}", file=sys.stderr)
        if kind:
            self._sysline(f"credentials updated for {kind}")

    def _on_menu_restart_app(self):
        """Tools > Restart app: spawn a fresh panel process after this one exits.

        Sets the `_restart_requested` flag and triggers a clean close. The
        actual respawn happens in main()'s finally block AFTER the
        single-instance mutex has been released - otherwise the child would
        bounce off ERROR_ALREADY_EXISTS.
        """
        if self._closing:
            return
        self._sysline("restart requested - closing panel; a fresh instance will start in a moment")
        self._restart_requested = True
        # Schedule the close on Tk idle so this menu callback returns first.
        self.root.after(0, self._on_close)

    # -------- File menu callbacks --------

    def _refresh_status_title(self):
        """Refresh the title bar via the ConversationView.

        Defers all formatting to the view; the panel only resolves whether
        the active model is still in the backend's catalog.
        """
        if self._conv_view is None:
            return
        try:
            available = self._conv_session.is_active_model_available(self._backend)
        except Exception:
            available = True
        try:
            self._conv_view.refresh_status_title(model_available=available)
        except Exception:
            pass

    def _clear_chat_history_widget(self):
        try:
            self.history.config(state="normal")
            self.history.delete("1.0", "end")
            self.history.config(state="disabled")
        except Exception:
            pass

    def _render_replay_message(self, role: str, text: str, ts: str) -> None:
        """ConversationView callback — render one historical message into the chat widget.

        System entries (only present in transcripts imported by an older
        version of the migration that didn't filter them) are skipped on
        replay rather than re-rendered + re-logged to chat.log.
        """
        if role == "user":
            self._append("you", text, "user", ts=ts)
        elif role == "llsm":
            self._append("LLSM", text, "assistant", ts=ts)
        elif role == "error":
            self._append("error", text, "error", ts=ts)

    def _on_menu_new_chat(self, _model_id):
        if self._closing:
            return
        try:
            cur = self._conv_session.current()
            backend_kind = cur.backend_kind if cur else self._backend_kind
            model = (cur.model if cur else None) or self._model or _default_model_for(self._backend_kind)
            self._conv_session.new_conversation(backend_kind=backend_kind, model=model)
        except Exception as exc:
            self._sysline(f"new chat failed: {exc}")
            return
        self._backend = None  # next chat reconstructs with no session_id
        if self._conv_view is not None:
            self._conv_view.replay([])
        self._sysline("started a new chat")
        self._refresh_status_title()

    def _on_menu_resume(self, conv_id):
        if self._closing:
            return
        try:
            conv, msgs = self._conv_session.switch_to(conv_id)
        except Exception as exc:
            self._sysline(f"resume failed: {exc}")
            return
        self._backend = None  # next chat reconstructs with conv's session_id
        if self._conv_view is not None:
            self._conv_view.replay(msgs)
        self._sysline(f"resumed: {conv.title}")
        self._refresh_status_title()

    def _on_menu_switch_model(self, backend_kind, model_id):
        if self._closing:
            return
        # Same-provider switch: just record the model and rebuild backend.
        if backend_kind == self._backend_kind:
            try:
                self._conv_session.update_model(model_id)
            except Exception as exc:
                self._sysline(f"switch_model failed: {exc}")
                return
            self._backend = None  # rebuild on next chat with the new model
            self._sysline(f"switched model to {model_id}")
            self._refresh_status_title()
            return

        # Cross-provider switch: confirm (one-time, suppressible), close the
        # current backend, reset SCIA model state, auto-create a new
        # conversation under the new (kind, model_id), swap to it.
        if not self._confirm_provider_switch(backend_kind, model_id):
            return

        # Close the current backend so its resources (streaming HTTP,
        # subprocess, ADM model) are released cleanly.
        old_backend = self._backend
        if old_backend is not None:
            try:
                old_backend.cancel()
            except Exception:
                pass
            try:
                old_backend.close()
            except Exception:
                pass
        self._backend = None

        # Resolve the new provider's display name + default_tools_enabled.
        display = backend_kind
        default_tools = True
        try:
            from .llm.providers import PROVIDERS
            spec = PROVIDERS.get(backend_kind)
            if spec is not None:
                display = spec.display_name
                default_tools = bool(spec.default_tools_enabled)
        except Exception:
            pass

        # Auto-create a fresh conversation under the new provider, seeded
        # with the provider's default tools_enabled (B4 seeding rule).
        try:
            self._conv_session._cancel()  # explicit pre-cancel for safety
            new_conv = self._conv_store.create(
                backend_kind=backend_kind,
                model=model_id,
                title=f"New chat ({display})",
                tools_enabled=default_tools,
            )
            self._conv_store.set_active(new_conv.id)
            self._model_usage.record(backend_kind, model_id)
        except Exception as exc:
            self._sysline(f"could not start new chat: {exc}")
            self._backend_kind = backend_kind
            return

        # Update panel-level kind so the title bar / Tools menu reflect it.
        self._backend_kind = backend_kind
        # Replay an empty transcript into the chat widget.
        if self._conv_view is not None:
            try:
                self._conv_view.replay([])
            except Exception:
                pass

        # Refresh title + sidebar; toast confirms the switch.
        self._refresh_status_title()
        try:
            self._kick_startup_sidebar_refresh()
        except Exception:
            pass
        self._sysline(f"new conversation started for {display} {model_id}")

    # -------- Cross-provider confirm (one-time, suppressible) --------

    def _ui_settings_path(self):
        return _LLSM_HOME / "ui_settings.json"

    def _ui_settings_get(self, key, default=None):
        try:
            import json as _json
            data = _json.loads(self._ui_settings_path().read_text(encoding="utf-8"))
            return data.get(key, default) if isinstance(data, dict) else default
        except Exception:
            return default

    def _ui_settings_set(self, key, value):
        try:
            import json as _json
            path = self._ui_settings_path()
            try:
                data = _json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    data = {}
            except Exception:
                data = {}
            data[key] = value
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            print(f"[panel] could not write ui_settings: {exc}", file=sys.stderr)

    def _confirm_provider_switch(self, new_kind: str, new_model: str) -> bool:
        """Show a one-time confirm with 'Don't ask again'. Returns True to proceed."""
        if self._ui_settings_get("switch_provider_confirm_suppressed", False):
            return True
        try:
            from .llm.providers import PROVIDERS
            display = PROVIDERS[new_kind].display_name if new_kind in PROVIDERS else new_kind
        except Exception:
            display = new_kind

        dlg = tk.Toplevel(self.root)
        dlg.title("Switch provider")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.geometry("420x180")
        ttk.Label(
            dlg,
            text=f"Switching to {display} starts a new chat.\n"
                 "Your current chat is saved in History.",
            wraplength=380,
            justify="left",
            padding=(16, 16),
        ).pack(side=tk.TOP, anchor="w")

        dont_ask = tk.BooleanVar(value=False)
        ttk.Checkbutton(dlg, text="Don't ask me again",
                        variable=dont_ask).pack(side=tk.TOP, anchor="w", padx=16)

        result = {"ok": False}
        def _ok():
            result["ok"] = True
            if dont_ask.get():
                self._ui_settings_set("switch_provider_confirm_suppressed", True)
            dlg.destroy()
        def _cancel():
            dlg.destroy()

        btnrow = ttk.Frame(dlg, padding=(16, 12))
        btnrow.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(btnrow, text="Cancel", command=_cancel).pack(side=tk.RIGHT)
        ttk.Button(btnrow, text="Switch", command=_ok).pack(side=tk.RIGHT, padx=(0, 6))
        dlg.protocol("WM_DELETE_WINDOW", _cancel)

        self.root.wait_window(dlg)
        return result["ok"]

    def _on_menu_open_history(self):
        if self._closing:
            return
        HistoryDialog(
            self.root,
            session=self._conv_session,
            on_resume=self._on_menu_resume,
            on_state_changed=self._refresh_status_title,
        )

    def _on_rename_active_conv(self):
        from tkinter import simpledialog
        cur = self._conv_session.current()
        if cur is None:
            return
        new_title = simpledialog.askstring(
            "Rename chat", "New title:", initialvalue=cur.title, parent=self.root
        )
        if new_title is None:
            return
        try:
            self._conv_store.rename(cur.id, new_title)
        except Exception as exc:
            self._sysline(f"rename failed: {exc}")
            return
        self._refresh_status_title()

    # -------- UI (Item 2 reflow + Item 4 Enter binding + Item 6 Cancel) --------

    def _build_ui(self):
        self.root = tk.Tk()
        # Route uncaught exceptions in Tk callbacks to panel.log instead of
        # default handling (which on Windows can silently kill mainloop).
        self.root.report_callback_exception = self._log_callback_exc
        self.root.title(f"LLSM - {self._backend_kind}")
        if self._always_on_top:
            self.root.attributes("-topmost", True)
        self._build_menu()
        # Wider default to accommodate the model-state sidebar.
        default_w = PANEL_WIDTH + SIDEBAR_WIDTH
        self.root.geometry(f"{default_w}x800+0+0")
        self.root.minsize(360 + SIDEBAR_WIDTH, 500)

        # Status bar (existing).
        self.status_var = tk.StringVar(value="searching for SCIA...    |    MCP: starting...")
        ttk.Label(self.root, textvariable=self.status_var, anchor="w",
                  padding=(8, 4)).pack(side=tk.TOP, fill=tk.X)
        ttk.Separator(self.root, orient="horizontal").pack(fill=tk.X)

        # Conversation title bar. Shows active conversation title + model.
        # Click to rename.
        self.conv_title_var = tk.StringVar(value="")
        self.conv_title_label = tk.Label(
            self.root, textvariable=self.conv_title_var, anchor="w",
            padx=10, pady=2, font=("Segoe UI", 9), cursor="hand2",
        )
        self.conv_title_label.pack(side=tk.TOP, fill=tk.X)
        self.conv_title_label.bind("<Button-1>", lambda e: self._on_rename_active_conv())

        # Status pill: live indicator of LLSM activity. Glyph + text first,
        # color second. State machine driven via outbox events from the
        # worker thread.
        self.status_pill_var = tk.StringVar(value=f"{_GLYPH_PILL_IDLE} Idle")
        self.status_pill = tk.Label(
            self.root, textvariable=self.status_pill_var, anchor="w",
            padx=10, pady=2, font=("Segoe UI Semibold", 9),
        )
        self.status_pill.pack(side=tk.TOP, fill=tk.X)

        # Bottom: input + buttons (build first so it reserves space; chat
        # area expands to fill the remainder).
        bottom = ttk.Frame(self.root, padding=(4, 4))
        bottom.pack(side=tk.BOTTOM, fill=tk.X)

        self.input_text = tk.Text(bottom, height=INPUT_MIN_LINES, wrap="word",
                                  font=("Segoe UI", 10))
        self.input_text.pack(side=tk.TOP, fill=tk.X, padx=0, pady=(0, 4))

        # Enter sends, Shift+Enter inserts a newline (Tk default).
        self.input_text.bind("<Return>", self._on_return_key)
        self.input_text.bind("<Shift-Return>", lambda e: None)
        self.input_text.bind("<Control-Return>", self._on_return_key)
        # Auto-grow + placeholder bindings.
        self.input_text.bind("<KeyRelease>", self._on_input_keyrelease)
        self.input_text.bind("<FocusIn>", self._on_input_focus_in)
        self.input_text.bind("<FocusOut>", self._on_input_focus_out)

        button_row = ttk.Frame(bottom)
        button_row.pack(side=tk.TOP, fill=tk.X)
        self.cancel_btn = ttk.Button(button_row, text="Cancel", command=self._on_cancel,
                                     state="disabled")
        self.cancel_btn.pack(side=tk.LEFT, padx=(0, 4))
        self.send_btn = ttk.Button(button_row, text="Send (Enter)", command=self._send)
        self.send_btn.pack(side=tk.RIGHT, fill=tk.X, expand=True)

        # Center: chat history (left) + sidebar (right). Pack the sidebar
        # first so it reserves its width; chat fills the remainder.
        self.sidebar_frame = ttk.Frame(self.root, width=SIDEBAR_WIDTH)
        self.sidebar_frame.pack(side=tk.RIGHT, fill=tk.Y)
        self.sidebar_frame.pack_propagate(False)

        ttk.Label(self.sidebar_frame, text="Model", anchor="w",
                  font=("Segoe UI Semibold", 10), padding=(6, 4)).pack(side=tk.TOP, fill=tk.X)
        ttk.Separator(self.sidebar_frame, orient="horizontal").pack(side=tk.TOP, fill=tk.X)
        # Treeview for the snapshot. Open by default so users see structure.
        self.sidebar_tree = ttk.Treeview(self.sidebar_frame, show="tree",
                                          selectmode="none")
        sb = ttk.Scrollbar(self.sidebar_frame, orient="vertical",
                           command=self.sidebar_tree.yview)
        self.sidebar_tree.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.sidebar_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # Pre-create the eight category rows so empty models still look populated.
        self._sidebar_category_iids: dict[str, str] = {}
        for key, label in SIDEBAR_CATEGORIES:
            iid = self.sidebar_tree.insert("", "end", text=f"{label} (0)", open=False)
            self._sidebar_category_iids[key] = iid

        # Chat history.
        self.history = scrolledtext.ScrolledText(
            self.root, wrap="word", state="disabled",
            font=("Segoe UI", 10), padx=10, pady=10,
        )
        self.history.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=4)

        # ConversationView is the only thing that touches BOTH the chat
        # widget and the title StringVar — keeps the panel free of the
        # replay-and-render loop and keeps ConversationSession headless.
        self._conv_view = ConversationView(
            session=self._conv_session,
            clear_history=self._clear_chat_history_widget,
            render_message=self._render_replay_message,
            set_status_title=self.conv_title_var.set,
        )
        self._refresh_status_title()

        # Apply the theme: configure tags AND set tk.Text widget surfaces
        # explicitly (tk.Text does not inherit ttk theme — see plan).
        self._apply_theme()

        # Show placeholder if input starts empty (it does).
        self._render_placeholder_if_empty()

        self._sysline(
            f"Welcome to LLSM. Backend: {self._backend_kind}. "
            f"Press Enter to send, Shift+Enter for newline. "
            f"Standalone mode - this window stays put when SCIA opens/closes."
        )

    def _on_return_key(self, event):
        self._send()
        return "break"  # suppress default newline insertion

    def _on_cancel(self):
        if self._backend is None or not self.busy:
            return
        try:
            killed = self._backend.cancel()
        except Exception as exc:
            killed = False
            self._sysline(f"cancel error: {exc}")
        if killed:
            self._sysline("chat cancelled; SCIA state may be partial - call model_summary to verify")

    # -------- Theme helpers (per plan: theme & accessibility ground rules) --------

    def _resolve_rgb(self, value):
        """Resolve any Tk color spec (#hex / named / SystemFoo) to an 8-bit RGB tuple.

        Returns None on failure. Caller falls back to a baked-in reference value.
        Critical for Windows: ttk.Style().lookup() often returns symbolic names
        like 'SystemButtonFace', which winfo_rgb() resolves uniformly with hex.
        """
        if not value:
            return None
        try:
            r16, g16, b16 = self.root.winfo_rgb(value)
            return (r16 // 256, g16 // 256, b16 // 256)
        except Exception:
            return None

    def _tint(self, base, accent, alpha=0.18):
        """Blend `base` toward `accent` by `alpha` (0..1). Returns "#rrggbb".

        On any TclError or unparseable input, falls back to the accent or to
        a safe gray. Never returns None / "" — Tk tag config rejects those.
        """
        base_rgb = self._resolve_rgb(base) or self._resolve_rgb(_THEME_REFERENCE["bg"])
        accent_rgb = self._resolve_rgb(accent) or self._resolve_rgb(_THEME_REFERENCE["fg"])
        if base_rgb is None or accent_rgb is None:
            if not self._tint_warned:
                self._tint_warned = True
                try:
                    self._sysline(
                        f"theme: could not resolve color (base={base!r}, accent={accent!r}); "
                        "using built-in fallback"
                    )
                except Exception:
                    pass
            return _THEME_REFERENCE.get("muted", "#888888")
        br, bg_, bb = base_rgb
        ar, ag, ab = accent_rgb
        r = int(br * (1 - alpha) + ar * alpha)
        g = int(bg_ * (1 - alpha) + ag * alpha)
        b = int(bb * (1 - alpha) + ab * alpha)
        r = max(0, min(255, r))
        g = max(0, min(255, g))
        b = max(0, min(255, b))
        return f"#{r:02x}{g:02x}{b:02x}"

    def _apply_theme(self):
        """Resolve theme colors and apply them to chat tags AND tk.Text surfaces.

        Two halves: tag colors (per-message badges, separators) and widget
        surfaces (history + input bg/fg/insertbackground). The latter is
        critical because tk.Text does NOT inherit a ttk theme — without
        explicit configuration the surface stays near-white even under a
        dark ttk theme.
        """
        try:
            style = ttk.Style()
            theme_bg = style.lookup(".", "background") or _THEME_REFERENCE["bg"]
            theme_fg = style.lookup(".", "foreground") or _THEME_REFERENCE["fg"]
        except Exception:
            theme_bg = _THEME_REFERENCE["bg"]
            theme_fg = _THEME_REFERENCE["fg"]

        c = {
            "bg":         theme_bg,
            "fg":         theme_fg,
            "muted":      self._tint(theme_bg, theme_fg, 0.45),
            "separator":  self._tint(theme_bg, theme_fg, 0.18),
            "user_fg":    self._tint(theme_fg, _THEME_REFERENCE["user_accent"], 0.65),
            "llsm_fg":    self._tint(theme_fg, _THEME_REFERENCE["llsm_accent"], 0.55),
            "error_fg":   _THEME_REFERENCE["error_accent"],
            "system_fg":  self._tint(theme_fg, _THEME_REFERENCE["system_accent"], 0.6),
            "user_bg":    self._tint(theme_bg, _THEME_REFERENCE["user_accent"], 0.18),
            "llsm_bg":    self._tint(theme_bg, _THEME_REFERENCE["llsm_accent"], 0.16),
            "error_bg":   self._tint(theme_bg, _THEME_REFERENCE["error_accent"], 0.20),
            "system_bg":  self._tint(theme_bg, _THEME_REFERENCE["system_accent"], 0.10),
            "thinking_bg": self._tint(theme_bg, _THEME_REFERENCE["thinking"], 0.22),
            "running_bg":  self._tint(theme_bg, _THEME_REFERENCE["running"], 0.20),
            "idle_bg":     self._tint(theme_bg, _THEME_REFERENCE["idle"], 0.10),
            "error_pill_bg": self._tint(theme_bg, _THEME_REFERENCE["error_accent"], 0.25),
        }
        self._theme_colors = c

        # Configure chat tags. Body tags carry the role's foreground.
        tc = self.history.tag_configure
        tc("user",      foreground=c["user_fg"],   font=("Segoe UI", 10))
        tc("assistant", foreground=c["llsm_fg"],   font=("Segoe UI", 10))
        tc("error",     foreground=c["error_fg"],  font=("Segoe UI", 10))
        tc("system",    foreground=c["system_fg"], font=("Segoe UI", 9, "italic"))
        # Badge tags carry bg + bold + leading accent bar (▎). Backgrounds
        # may render ragged on Windows; the ▎ is the primary affordance.
        tc("badge_user",   foreground=_THEME_REFERENCE["user_accent"],
           background=c["user_bg"],   font=("Segoe UI Semibold", 10), lmargin1=4)
        tc("badge_llsm",   foreground=_THEME_REFERENCE["llsm_accent"],
           background=c["llsm_bg"],   font=("Segoe UI Semibold", 10), lmargin1=4)
        tc("badge_error",  foreground=_THEME_REFERENCE["error_accent"],
           background=c["error_bg"],  font=("Segoe UI Semibold", 10), lmargin1=4)
        tc("badge_sys",    foreground=c["system_fg"],
           background=c["system_bg"], font=("Segoe UI Semibold", 9, "italic"), lmargin1=4)
        tc("timestamp",    foreground=c["muted"], font=("Segoe UI", 8, "italic"))
        tc("separator",    foreground=c["separator"], font=("Segoe UI", 9))
        tc("placeholder",  foreground=c["muted"], font=("Segoe UI", 10, "italic"))

        # Configure tk.Text widget surfaces (history + input).
        for w in (self.history, self.input_text):
            try:
                w.configure(background=c["bg"], foreground=c["fg"],
                            insertbackground=c["fg"])
            except Exception:
                pass

        # Pill default (idle).
        self._set_pill("idle")

    # -------- Status pill (Phase B) --------

    def _set_pill(self, state, detail=""):
        """Update the status pill to one of: idle / thinking / running / error.

        Side effect: explicitly transitioning OUT of "error" cancels any
        pending error auto-clear timer. Otherwise a stale 3-second clear
        scheduled by an earlier failed tool would later overwrite a fresh
        "running:tool" or "thinking" state from the next turn.
        """
        if not hasattr(self, "status_pill"):
            return
        if state != "error" and self._pill_error_after is not None:
            try:
                self.root.after_cancel(self._pill_error_after)
            except Exception:
                pass
            self._pill_error_after = None
        c = self._theme_colors or {}
        if state == "idle":
            self.status_pill_var.set(f"{_GLYPH_PILL_IDLE} Idle")
            bg = c.get("idle_bg", _THEME_REFERENCE["bg"])
            fg = c.get("muted", _THEME_REFERENCE["muted"])
        elif state == "thinking":
            self.status_pill_var.set(f"{_GLYPH_PILL_THINKING} Thinking...")
            bg = c.get("thinking_bg", _THEME_REFERENCE["bg"])
            fg = _THEME_REFERENCE["thinking"]
        elif state == "running":
            label = f"Running: {detail}" if detail else "Running"
            # Truncate to keep pill readable on narrow panels.
            if len(label) > 60:
                label = label[:57] + "..."
            self.status_pill_var.set(f"{_GLYPH_PILL_RUNNING} {label}")
            bg = c.get("running_bg", _THEME_REFERENCE["bg"])
            fg = _THEME_REFERENCE["running"]
        elif state == "error":
            label = f"Error: {detail}" if detail else "Error"
            if len(label) > 60:
                label = label[:57] + "..."
            self.status_pill_var.set(f"{_GLYPH_PILL_ERROR} {label}")
            bg = c.get("error_pill_bg", _THEME_REFERENCE["bg"])
            fg = _THEME_REFERENCE["error_accent"]
        else:
            return
        try:
            self.status_pill.configure(background=bg, foreground=fg)
        except Exception:
            pass

    def _flash_pill_error(self, detail=""):
        """Show the error pill briefly, then return to whatever's most logical."""
        self._set_pill("error", detail)
        # Cancel any prior auto-clear schedule.
        if self._pill_error_after is not None:
            try:
                self.root.after_cancel(self._pill_error_after)
            except Exception:
                pass
        self._pill_error_after = self.root.after(
            PILL_ERROR_CLEAR_MS, self._after_pill_error_clear
        )

    def _after_pill_error_clear(self):
        self._pill_error_after = None
        # Decide: if we're still busy, go back to thinking; else idle.
        if self.busy:
            self._set_pill("thinking")
        else:
            self._set_pill("idle")

    @staticmethod
    def _format_tool_args(args):
        """Best-effort short rendering of a tool's args dict for the pill."""
        if not isinstance(args, dict) or not args:
            return ""
        parts = []
        for i, (k, v) in enumerate(args.items()):
            if i >= 2:
                parts.append("...")
                break
            sv = str(v)
            if len(sv) > 16:
                sv = sv[:13] + "..."
            parts.append(f"{k}={sv}")
        return ", ".join(parts)

    # -------- Input polish (Phase A) --------

    def _on_input_keyrelease(self, event):
        """Auto-grow the input between INPUT_MIN_LINES and INPUT_MAX_LINES."""
        if self._placeholder_active:
            return
        try:
            end_idx = self.input_text.index("end-1c")
            line_count = int(end_idx.split(".")[0])
        except Exception:
            return
        target = max(INPUT_MIN_LINES, min(INPUT_MAX_LINES, line_count))
        try:
            current = int(self.input_text.cget("height"))
        except Exception:
            current = INPUT_MIN_LINES
        if target != current:
            try:
                self.input_text.configure(height=target)
            except Exception:
                pass

    def _render_placeholder_if_empty(self):
        """If input is empty, render the placeholder (grey italic)."""
        try:
            current = self.input_text.get("1.0", "end-1c")
        except Exception:
            return
        if current == "":
            self._placeholder_active = True
            try:
                self.input_text.delete("1.0", "end")
                self.input_text.insert("1.0", PLACEHOLDER_TEXT, "placeholder")
            except Exception:
                self._placeholder_active = False

    def _clear_placeholder(self):
        """Remove the placeholder text if active."""
        if not self._placeholder_active:
            return
        try:
            self.input_text.delete("1.0", "end")
        except Exception:
            pass
        self._placeholder_active = False

    def _on_input_focus_in(self, event):
        if self._placeholder_active:
            self._clear_placeholder()

    def _on_input_focus_out(self, event):
        self._render_placeholder_if_empty()

    # -------- Sidebar toggle + async fetch (Phase C) --------

    def _on_toggle_sidebar(self):
        """View menu checkbutton handler — show/hide the sidebar frame.

        Subtlety: pack_forget+pack appends the slave to the END of the pack
        order. Re-packing without `before=self.history` would put sidebar
        AFTER history, but history has fill=BOTH+expand=True and would
        already have claimed all the cavity — sidebar would render with
        zero size. Using before=self.history inserts sidebar at its
        original position in the pack list so the layout matches the
        initial build_ui order.
        """
        want = bool(self._sidebar_visible_var.get())
        if want == self._sidebar_visible:
            return
        self._sidebar_visible = want
        try:
            if want:
                self.sidebar_frame.pack(side=tk.RIGHT, fill=tk.Y,
                                        before=self.history)
            else:
                self.sidebar_frame.pack_forget()
        except Exception as exc:
            self._sysline(f"sidebar toggle failed: {exc}")

    def _request_sidebar_refresh(self):
        """Tk-thread: kick a daemon thread to fetch a fresh snapshot.

        If a fetch is already inflight, set the dirty flag so we re-kick
        when it completes — this prevents one-turn staleness when __done__
        events arrive faster than fetches complete (per plan section 4).
        """
        if self._closing:
            return
        if self._sidebar_fetch_inflight:
            self._sidebar_fetch_dirty = True
            return
        if self._backend is None:
            # No backend yet; nothing to fetch.
            return
        self._sidebar_fetch_inflight = True
        threading.Thread(
            target=self._sidebar_fetch_thread,
            args=(self._backend,),
            daemon=True,
        ).start()

    def _sidebar_fetch_thread(self, backend):
        """Daemon-thread: call backend.model_snapshot() (may block on MCP I/O).

        Per the Tk-main-thread discipline rule: this MUST run off-thread.
        Result is funneled back through the outbox queue.
        """
        try:
            payload = backend.model_snapshot()
        except Exception as exc:
            payload = exc
        try:
            self.outbox.put(("snapshot_ready", payload))
        except Exception:
            pass

    def _refresh_sidebar_widget(self, snapshot):
        """Tk-thread: repopulate the Treeview from a snapshot dict.

        Tolerates extra/unknown keys per the schema's versioning rules.
        Each item is inserted under its own try/except so one corrupt entry
        cannot drop the rest of the category — count label, children clear,
        and per-item insert are independently isolated.
        """
        if not isinstance(snapshot, dict):
            return
        tree = self.sidebar_tree
        for key, label in SIDEBAR_CATEGORIES:
            cat_iid = self._sidebar_category_iids.get(key)
            if cat_iid is None:
                continue
            items = snapshot.get(key, []) or []
            count = len(items)
            # Update the count label and clear existing children. If this
            # fails we skip the whole category (likely the iid is gone).
            try:
                tree.item(cat_iid, text=f"{label} ({count})")
                for child in tree.get_children(cat_iid):
                    tree.delete(child)
            except Exception:
                continue
            # Insert each item under its own try so a single bad entry
            # (None field, missing key, etc.) doesn't silently drop the rest.
            for it in items:
                try:
                    tree.insert(cat_iid, "end",
                                text=self._format_sidebar_item(key, it))
                except Exception:
                    continue

    @staticmethod
    def _num(item, key):
        """Pull a numeric field from a snapshot item, defaulting to 0.0.

        `dict.get(key, 0)` returns None when the key is present-but-None,
        which then crashes `f"{None:g}"`. The `or 0` collapses both
        missing-key and None-value cases to 0 (and is harmless for genuine
        zero, since 0 == 0). Strings like "1500" survive via float()."""
        v = item.get(key)
        if v is None:
            return 0.0
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _format_sidebar_item(cls, category, item):
        """Render one row inside a category, robust to str/dict/junk items."""
        if isinstance(item, str):
            return item
        if not isinstance(item, dict):
            return str(item)
        name = item.get("name", "?")
        if category == "nodes":
            x = cls._num(item, "x_mm")
            y = cls._num(item, "y_mm")
            z = cls._num(item, "z_mm")
            return f"{name}  ({x:g}, {y:g}, {z:g})"
        if category == "beams":
            n1 = item.get("n1", "?"); n2 = item.get("n2", "?"); sec = item.get("section", "")
            return f"{name}  {n1}-{n2}  {sec}".strip()
        if category == "supports":
            return f"{name}  {item.get('node', '?')}  {item.get('kind', '?')}"
        if category == "load_cases":
            return f"{name}  ({item.get('group', '?')})"
        if category == "loads":
            mag = cls._num(item, "magnitude_kN_per_m")
            d = item.get("direction", "?")
            return f"{name}  {mag:g} kN/m {d}"
        return name

    # -------- Win32 docking (DEAD CODE - kept for reference) --------
    # These methods were used to make the panel a Win32-owned child of SCIA's
    # main HWND. We removed the call sites in q-368 because:
    #   - When SCIA is minimized, all owned windows minimize too (Win32 rule),
    #     so launching SCIA in ShowWindowShowMinNoActive made the panel vanish.
    #   - GetWindowRect on a minimized window returns ~(-32000,-32000,...);
    #     positioning the panel at scia_right placed it off-screen.
    # Kept here so the reasoning isn't lost in git history. Do not call them.

    def _own_to_scia(self, scia_hwnd):
        try:
            our_hwnd = int(self.root.winfo_id())
            top = self.root.wm_frame()
            if top:
                try:
                    our_hwnd = int(top, 16)
                except ValueError:
                    pass
        except Exception:
            our_hwnd = int(self.root.winfo_id())
        try:
            win32api.SetWindowLong(our_hwnd, GWLP_HWNDPARENT, scia_hwnd)
        except Exception as exc:
            self._sysline(f"could not set owner window: {exc}")

    def _position_next_to_scia(self):
        if not self.scia_hwnd or not win32gui.IsWindow(self.scia_hwnd):
            return
        try:
            rect = win32gui.GetWindowRect(self.scia_hwnd)
        except Exception:
            return
        scia_left, scia_top, scia_right, scia_bottom = rect
        height = max(500, scia_bottom - scia_top)
        x = scia_right
        y = scia_top
        cur = f"{PANEL_WIDTH}x{height}+{x}+{y}"
        if self.root.geometry() != cur:
            self.root.geometry(cur)

    def _poll_scia(self):
        """Track whether SCIA is alive for the status bar. No positioning, no owner-window.

        We deliberately do NOT dock to SCIA: the previous Win32 owner-window
        approach (SetWindowLong(GWLP_HWNDPARENT, scia_hwnd)) caused the panel
        to auto-minimize whenever SCIA minimized, plus position math against
        a minimized SCIA's GetWindowRect put the panel off-screen. The panel
        is now a standalone free-floating window that the user places where
        they want.
        """
        if self._closing:
            return
        try:
            hwnd = _find_scia_main_hwnd()
            if hwnd != self.scia_hwnd:
                self.scia_hwnd = hwnd  # may be None
                self._refresh_status_bar()
        except Exception as exc:
            self.status_var.set(f"poll error: {exc}")
        self._safe_after(POLL_INTERVAL_MS, self._poll_scia)

    # -------- Backend wiring (Item 7 callbacks) --------

    def _ensure_backend(self):
        """Build the active conversation's backend via the registry (CR-5).

        All five kinds dispatch through `providers._resolve(spec.build_backend_path)(panel)`.
        The factories live in their respective backend modules
        (`anthropic_backend.build_backend`, `claude_code_backend.build_backend`,
        `openai_backend.build_backend`, etc.) and read everything they need
        — model, transcript, callbacks, MCP handle — off `self`.
        """
        if self._backend is not None:
            return self._backend

        # Honor the active conversation's backend_kind over panel default —
        # a runtime switch may have written a different kind into the active
        # conversation (see _on_menu_switch_model auto-new-chat).
        cur_conv = self._conv_session.current()
        kind = cur_conv.backend_kind if cur_conv else self._backend_kind
        # Keep panel.backend_kind in sync (used by the title / Tools menu state).
        self._backend_kind = kind

        from .llm.providers import PROVIDERS, build_backend as _build_via_registry
        spec = PROVIDERS.get(kind)
        if spec is None:
            raise RuntimeError(f"unknown backend_kind {kind!r}")
        try:
            self._backend = _build_via_registry(spec, self)
        except ImportError as exc:
            hint = spec.sdk_install_hint or "install the missing SDK"
            raise RuntimeError(
                f"{spec.display_name} SDK is not installed. {hint}.\n"
                f"  ({type(exc).__name__}: {exc})"
            ) from exc
        return self._backend

    def _on_session_rotated(self, new_session_id):
        # Pin the rotation to the conversation that was active when this
        # chat started — if the user switched mid-flight, the active conv
        # may now be a different one and we must not write the rotated id
        # to it.
        target = self._inflight_conv_id or self._conv_session.current_id()
        if target is not None:
            try:
                self._conv_store.update_session_id(target, new_session_id)
            except Exception as exc:
                print(f"[panel] update_session_id (rotated) failed: {exc}", file=sys.stderr)
        self.outbox.put((
            "system",
            "prior conversation lost; SCIA state intact (LLM may need model_summary to re-orient)",
        ))

    def _on_orphan_cleanup(self, session_id):
        try:
            n = mcp_supervisor.kill_orphan_claudes(session_id, verbose=True)
            if n > 0:
                self.outbox.put(("system", f"killed {n} orphan claude process(es) holding the session-id lock"))
            return n
        except Exception as exc:
            print(f"[panel] kill_orphan_claudes failed: {exc}", file=sys.stderr)
            return 0

    def _on_tool_event_from_worker(self, phase, tool_name, payload):
        """Backend hook: fired from the worker thread on each tool start/end.

        Per plan: this callback must do nothing but enqueue, and must catch
        and swallow any exception so a panel-side queue failure cannot break
        tool execution.
        """
        try:
            if phase == "start":
                args_summary = self._format_tool_args(payload)
                self.outbox.put(("tool_start", (tool_name, args_summary)))
            elif phase == "end":
                ok = bool(payload)
                self.outbox.put(("tool_end", (tool_name, ok)))
        except Exception:
            # Never propagate into the backend's tool dispatch.
            pass

    # -------- Chat flow --------

    def _send(self):
        if self.busy or self._closing:
            return
        # Don't accidentally submit the placeholder.
        if self._placeholder_active:
            return
        text = self.input_text.get("1.0", "end").strip()
        if not text:
            return
        self.input_text.delete("1.0", "end")
        # Reset auto-grown height.
        try:
            self.input_text.configure(height=INPUT_MIN_LINES)
        except Exception:
            pass
        self._append("you", text, "user")
        _chat_log("USER", text)
        # Ensure an active conv exists before recording — covers the edge
        # where the user deleted the active conv from the History dialog.
        try:
            self._conv_session.ensure_active(
                backend_kind=self._backend_kind,
                model=self._model or _default_model_for(self._backend_kind),
            )
        except Exception as exc:
            print(f"[panel] ensure_active before record failed: {exc}", file=sys.stderr)
        try:
            self._conv_session.record_user(text)
            # Auto-title may have just fired (first user message); refresh.
            self._refresh_status_title()
        except Exception as exc:
            print(f"[panel] record_user failed: {exc}", file=sys.stderr)
        self.busy = True
        self.send_btn.config(state="disabled", text="...")
        self.cancel_btn.config(state="normal")
        # Pill: immediately Thinking; tool_start events will refine this.
        self._set_pill("thinking")
        # Capture the conversation we're chatting in. If the user switches
        # mid-flight (api backend can't be cancelled mid-request), the reply
        # would otherwise land in the new conversation's transcript.
        self._inflight_conv_id = self._conv_session.current_id()
        # Materialise the backend on Tk thread BEFORE spawning the worker.
        # If the user switches conversations after this point, `self._backend`
        # gets reset to None — but the local reference passed to the worker
        # still targets the ORIGINAL conversation's session. Without this,
        # the worker would call _ensure_backend() AFTER the switch and build
        # a backend for the wrong conv, sending the user's message to a
        # freshly-created claude session for the new conv.
        try:
            backend = self._ensure_backend()
        except Exception as exc:
            self.outbox.put(("error", f"{type(exc).__name__}: {exc}"))
            self.outbox.put(("__done__", ""))
            return
        threading.Thread(target=self._chat_thread, args=(text, backend), daemon=True).start()

    def _chat_thread(self, prompt, backend):
        try:
            reply = backend.chat(prompt)
            self.outbox.put(("assistant", reply))
        except Exception as exc:
            self.outbox.put(("error", f"{type(exc).__name__}: {exc}"))
        finally:
            self.outbox.put(("__done__", ""))

    def _drain_outbox(self):
        """Drain queued events from worker threads onto the Tk thread.

        Each event-kind branch is wrapped in its own try/except per the
        chat pipeline isolation rule: an unhandled exception in one branch
        must not freeze the chat. The reschedule lives in a `finally`
        block so the loop survives any error.
        """
        if self._closing:
            return
        try:
            while True:
                try:
                    kind, msg = self.outbox.get_nowait()
                except queue.Empty:
                    break
                # Each handler is isolated. On exception, log via _sysline and
                # continue draining — never let one bad event kill the pipeline.
                try:
                    self._dispatch_outbox_event(kind, msg)
                except Exception as exc:
                    try:
                        import traceback as _tb
                        print(f"[panel] _drain_outbox handler raised on kind={kind!r}: "
                              f"{type(exc).__name__}: {exc}\n{_tb.format_exc()}",
                              file=sys.stderr)
                        # _sysline itself is wrapped (own try/except); even if
                        # this fails, the finally below still reschedules.
                        self._sysline(f"internal: handler for {kind!r} crashed: {exc!r}")
                    except Exception:
                        pass
        finally:
            # MUST live outside the try-loop so a malfunctioning sidebar /
            # snapshot / pill cannot kill chat input.
            self._safe_after(80, self._drain_outbox)

    def _dispatch_outbox_event(self, kind, msg):
        """One outbox event -> one Tk-thread side effect. Wrapped by drain caller."""
        if kind == "__done__":
            self.busy = False
            self._inflight_conv_id = None
            self.send_btn.config(state="normal", text="Send (Enter)")
            self.cancel_btn.config(state="disabled")
            # Authoritative reset for the pill: clear any per-tool tracking
            # so a missed tool_end (cancel, exception) cannot leave us stuck.
            self._active_tools = []
            if self._pill_error_after is None:
                # Don't stomp on a still-flashing error pill.
                self._set_pill("idle")
            # Kick a sidebar refresh on every turn boundary.
            self._request_sidebar_refresh()
        elif kind == "error":
            _chat_log("ERR", msg)
            # If the user switched conversations mid-flight, don't render the
            # stale error into the now-active conv's chat widget — show a
            # discreet sysline instead, and don't record it into the new
            # conv's transcript either.
            if self._reply_belongs_to_active():
                self._append("error", msg, "error")
                try:
                    self._conv_session.record_error(str(msg))
                except Exception:
                    pass
                self._flash_pill_error(msg[:40] if isinstance(msg, str) else "")
            else:
                self._sysline(f"error from prior chat (discarded): {str(msg)[:80]}")
        elif kind == "system":
            self._sysline(msg)
        elif kind == "tool_start":
            # msg = (tool_name, args_summary)
            try:
                tool_name, args_summary = msg
            except (TypeError, ValueError):
                return
            self._active_tools.append(tool_name)
            label = f"{tool_name}({args_summary})" if args_summary else f"{tool_name}()"
            self._set_pill("running", label)
        elif kind == "tool_end":
            # msg = (tool_name, ok)
            try:
                tool_name, ok = msg
            except (TypeError, ValueError):
                return
            if self._active_tools:
                # Pop the most recent matching entry; tools are usually serial.
                try:
                    self._active_tools.remove(tool_name)
                except ValueError:
                    self._active_tools.pop()
            if not self._active_tools and self.busy:
                self._set_pill("thinking")
            if not ok:
                # Brief flash; the chat will get the actual error message
                # via the LLM's tool_result anyway.
                self._flash_pill_error(f"{tool_name} failed")
        elif kind == "snapshot_ready":
            # msg is either the snapshot dict or an Exception.
            try:
                if isinstance(msg, Exception):
                    self._sysline(f"sidebar refresh failed: {type(msg).__name__}: {msg}")
                    # Leave previous Treeview contents in place.
                else:
                    self._refresh_sidebar_widget(msg)
            finally:
                # Clear inflight, then check dirty and re-kick if a __done__
                # arrived during the in-flight fetch (per plan section 4).
                self._sidebar_fetch_inflight = False
                if self._sidebar_fetch_dirty:
                    self._sidebar_fetch_dirty = False
                    self._request_sidebar_refresh()
        else:
            # Default: assistant reply.
            _chat_log("LLSM", msg)
            if self._reply_belongs_to_active():
                self._append("LLSM", msg, "assistant")
                try:
                    self._conv_session.record_assistant(str(msg))
                except Exception:
                    pass
                try:
                    self._refresh_status_title()
                except Exception:
                    pass
            else:
                # Don't render the stale reply into the now-active conv's
                # chat widget; just note that the in-flight chat completed
                # and went nowhere.
                self._sysline("reply discarded: conversation switched mid-flight")

    # -------- Helpers --------

    @staticmethod
    def _now_stamp():
        return datetime.now().isoformat(timespec="seconds").replace("T", " ")

    def _append(self, who, text, tag, ts=None):
        """Render a chat turn as a badge + timestamp + body + separator block.

        `tag` selects the body color (user / assistant / error / system).
        Badge surface uses the matching badge_<role> tag (bold + bg + ▎).
        `ts` (optional) lets transcript replay show the message's ORIGINAL
        timestamp instead of "now"; live messages pass None and get the
        current time.
        """
        # Map body tag -> badge tag + glyph.
        if tag == "user":
            badge_tag, glyph = "badge_user", _GLYPH_USER
        elif tag == "assistant":
            badge_tag, glyph = "badge_llsm", _GLYPH_LLSM
        elif tag == "error":
            badge_tag, glyph = "badge_error", _GLYPH_ERR
        else:
            badge_tag, glyph = "badge_sys", _GLYPH_SYS

        if ts is None:
            ts = self._now_stamp()
        else:
            # Normalise transcript ISO timestamps ("2025-01-01T00:00:00+00:00"
            # or legacy "2025-01-01T00:00:00") to the same "YYYY-MM-DD HH:MM:SS"
            # format _now_stamp produces. Skip TZ math; the date is the
            # informative bit when re-reading old conversations.
            ts = (ts or "").replace("T", " ").split("+", 1)[0].rstrip("Z").strip()
            if not ts:
                ts = self._now_stamp()
        body = (text or "").rstrip()

        self.history.config(state="normal")
        try:
            # Header row: ▎[ROLE]   <timestamp>
            self.history.insert("end", f"{_GLYPH_BAR}{glyph}  ", badge_tag)
            self.history.insert("end", f"{ts}\n", "timestamp")
            # Body.
            self.history.insert("end", body + "\n", tag)
            # Spacer + thin separator + spacer.
            self.history.insert("end", "\n", tag)
            self.history.insert("end", _GLYPH_SEP + "\n\n", "separator")
            self.history.see("end")
        finally:
            self.history.config(state="disabled")

    def _sysline(self, text):
        """One-line status from the panel (not the LLM). Greyed, italic, less prominent."""
        print(f"[panel/sys] {text}", file=sys.stderr)
        _chat_log("SYS", text)
        # Don't crash if the history widget isn't ready yet (early-construction
        # paths can call _sysline before _build_ui finishes).
        if not hasattr(self, "history"):
            return
        self.history.config(state="normal")
        try:
            self.history.insert("end", f"{_GLYPH_BAR}{_GLYPH_SYS}  ", "badge_sys")
            self.history.insert("end", f"{self._now_stamp()}\n", "timestamp")
            self.history.insert("end", (text or "").rstrip() + "\n\n", "system")
            self.history.see("end")
        finally:
            self.history.config(state="disabled")

    def run(self):
        """Enter Tk mainloop with bracketing log lines.

        If the panel dies mysteriously, the LAST heartbeat in panel.log plus the
        'mainloop exited' line below pin death timing and exit cause exactly.
        """
        print(f"[panel] mainloop entering at {datetime.now().isoformat(timespec='seconds')}", file=sys.stderr)
        exit_cause = "clean"
        try:
            self.root.mainloop()
        except SystemExit:
            exit_cause = "SystemExit"
            raise
        except KeyboardInterrupt:
            exit_cause = "KeyboardInterrupt (mainloop)"
        except BaseException as exc:
            import traceback as _tb
            exit_cause = f"exception: {type(exc).__name__}: {exc}"
            print(f"[panel] mainloop crashed:\n{_tb.format_exc()}", file=sys.stderr)
        finally:
            print(f"[panel] mainloop exited at {datetime.now().isoformat(timespec='seconds')} ({exit_cause})", file=sys.stderr)


# === Single-instance enforcement (Item 3) ===

def _acquire_single_instance_mutex():
    """Acquire the named global mutex. Returns the handle, or None if another
    instance already holds it (caller should exit cleanly).
    """
    try:
        h = win32event.CreateMutex(None, True, SINGLE_INSTANCE_MUTEX)
    except Exception as exc:
        print(f"[panel] could not create mutex: {exc}", file=sys.stderr)
        return "ignore"  # fail-open so a broken Win32 doesn't block launch
    err = win32api.GetLastError()
    if err == winerror.ERROR_ALREADY_EXISTS:
        return None
    return h


def main():
    ap = argparse.ArgumentParser(prog="llsm-panel",
        description="LLSM chat panel docked to SCIA Engineer")
    ap.add_argument("--model", default=None,
                    help="Override LLM model (e.g. opus, sonnet, claude-opus-4-7)")
    ap.add_argument("--backend",
                    choices=["claude-code", "api", "openai", "gemini", "openai-compatible"],
                    default="claude-code",
                    help="LLM backend (default: claude-code)")
    ap.add_argument("--connect-to-existing", action="store_true",
                    help="Reattach to a running MCP supervisor instead of spawning fresh")
    ap.add_argument("--always-on-top", action="store_true",
                    help="Keep the panel above other windows (still independent of SCIA's lifecycle)")
    ap.add_argument("--dev", action="store_true",
                    help="Watch src/llsm for source changes; auto-restart MCP on edits "
                         "to scia/** or mcp_server.py (requires `pip install -e .[dev]`)")
    args = ap.parse_args()

    # Item 3: enforce single instance.
    mutex_handle = _acquire_single_instance_mutex()
    if mutex_handle is None:
        print("[panel] another LLSM panel is already running. Exiting.", file=sys.stderr)
        # Best-effort visible feedback for users who launched via shortcut.
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                "Another LLSM panel is already running.\nClose it before launching a new one.",
                "LLSM",
                0x40,  # MB_ICONINFORMATION
            )
        except Exception:
            pass
        return 1

    panel_instance = None
    try:
        load_scia()
        panel_instance = LlsmPanel(
            model=args.model,
            backend_kind=args.backend,
            connect_existing=args.connect_to_existing,
            always_on_top=args.always_on_top,
            dev_mode=args.dev,
        )
        panel_instance.run()
        return 0
    finally:
        # Releasing the mutex on exit lets a relaunch proceed.
        if mutex_handle not in (None, "ignore"):
            try:
                win32event.ReleaseMutex(mutex_handle)
            except Exception:
                pass
        # Tools > Restart app: respawn AFTER the mutex release so the child
        # doesn't bounce off ERROR_ALREADY_EXISTS.
        if panel_instance is not None and getattr(panel_instance, "_restart_requested", False):
            try:
                import subprocess
                creationflags = 0
                if hasattr(subprocess, "DETACHED_PROCESS"):
                    creationflags |= subprocess.DETACHED_PROCESS
                if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
                    creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
                subprocess.Popen(
                    [sys.executable, "-m", "llsm.panel", *sys.argv[1:]],
                    cwd=os.getcwd(),
                    creationflags=creationflags,
                    close_fds=True,
                )
                print("[panel] restart: spawned fresh instance", file=sys.stderr)
            except Exception as exc:
                print(f"[panel] restart spawn failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
