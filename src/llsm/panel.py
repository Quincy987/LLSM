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
from .llm.anthropic_backend import AnthropicBackend
from .llm.claude_code_backend import ClaudeCodeBackend
from .scia.loader import load as load_scia


GWLP_HWNDPARENT = -8
SCIA_PROC_NAMES = {"sciaengineer.exe"}
POLL_INTERVAL_MS = 250
HEALTH_PING_INTERVAL_MS = 30_000
HEALTH_PING_FAST_INTERVAL_MS = 5_000  # ping faster after first failure
MCP_HANG_RESTART_AFTER_FAILS = 6  # ~30 s of failures (5 s fast-ping x 6) before auto-restart
MCP_RESTART_COOLDOWN_S = 60  # don't auto-restart again within this many seconds
MCP_MAX_RESTARTS_PER_SESSION = 5  # then give up and ask the user
HEARTBEAT_INTERVAL_MS = 10_000
SIGNAL_PIPE_DRAIN_MS = 100
PANEL_WIDTH = 460
SINGLE_INSTANCE_MUTEX = "Global\\LLSMPanel"


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
    def __init__(self, *, model=None, backend_kind="claude-code", connect_existing=False, always_on_top=False):
        self.scia_hwnd = None
        self.outbox = queue.Queue()
        self.busy = False
        self._closing = False
        self._backend = None
        self._model = model
        self._backend_kind = backend_kind
        self._connect_existing = connect_existing
        self._always_on_top = always_on_top
        self._mcp_handle = None  # mcp_supervisor.McpHandle | None
        self._mcp_status = "unknown"  # "up" | "down" | "unknown"
        self._mcp_ping_fails = 0  # consecutive failures since last success
        self._mcp_last_restart = 0.0  # time.monotonic() of last auto-restart
        self._mcp_restart_count = 0  # total auto-restarts this session
        self._mcp_restart_disabled = False  # set True after MAX_RESTARTS

        # Signal pipe (Item 1: Ctrl+C handling on Windows Tk).
        # socketpair, NOT os.pipe: signal.set_wakeup_fd on Windows requires
        # a socket handle - pipe FDs silently do nothing.
        self._sig_read_sock = None
        self._sig_write_sock = None

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._install_sigint_pipe()
        self._start_mcp_server()
        self._poll_scia()
        self._drain_outbox()
        self._drain_signal_pipe()
        self._schedule_health_ping()
        self._heartbeat_tick()

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
                    self._sysline(
                        f"reattached to MCP server at {handle.url}"
                        + (f" (resuming session {handle.session_id[:8]}...)" if handle.session_id else "")
                    )
                    return
                self._sysline("--connect-to-existing: no live supervisor found, spawning fresh")
            except Exception as exc:
                self._sysline(f"connect-to-existing failed: {exc}; spawning fresh")

        # Spawn fresh.
        try:
            self._mcp_handle = mcp_supervisor.spawn(verbose=True)
            self._mcp_status = "up"
            # Try to inherit a previously-persisted session_id (so a clean
            # exit + relaunch resumes the conversation).
            persisted = mcp_supervisor.read_session_id()
            if persisted:
                self._mcp_handle.session_id = persisted
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
        """Watchdog action: kill the hung supervisor + stranded claude subprocess,
        respawn fresh. Conversation continues via the persisted session-id.
        """
        self._mcp_last_restart = time.monotonic()
        self._mcp_restart_count += 1
        self._sysline(
            f"MCP server appears hung ({reason}); auto-restart "
            f"#{self._mcp_restart_count}/{MCP_MAX_RESTARTS_PER_SESSION}..."
        )
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
            # Inherit persisted session_id so the conversation continues.
            persisted = mcp_supervisor.read_session_id()
            if persisted:
                self._mcp_handle.session_id = persisted
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
        # Resume polling either way (will catch the new server's health).
        self._schedule_health_ping()

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
        # Cancel any in-flight chat first.
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
        tools = tk.Menu(menubar, tearoff=False)
        # Disabled when backend has no MCP layer (api mode).
        state = "normal" if self._backend_kind == "claude-code" else "disabled"
        tools.add_command(
            label="Restart MCP server  Ctrl+R",
            command=self._on_menu_restart_mcp,
            state=state,
        )
        tools.add_separator()
        tools.add_command(label="Open panel.log folder", command=self._on_menu_open_log_folder)
        menubar.add_cascade(label="Tools", menu=tools)
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
        self.root.geometry(f"{PANEL_WIDTH}x800+0+0")
        self.root.minsize(360, 500)

        self.status_var = tk.StringVar(value="searching for SCIA...    |    MCP: starting...")
        ttk.Label(self.root, textvariable=self.status_var, anchor="w",
                  padding=(8, 4)).pack(side=tk.TOP, fill=tk.X)
        ttk.Separator(self.root, orient="horizontal").pack(fill=tk.X)

        self.history = scrolledtext.ScrolledText(
            self.root, wrap="word", state="disabled",
            font=("Segoe UI", 10), padx=10, pady=10,
        )
        self.history.tag_configure("user", foreground="#1e6091", font=("Segoe UI Semibold", 10))
        self.history.tag_configure("assistant", foreground="#1d3557", font=("Segoe UI", 10))
        self.history.tag_configure("error", foreground="#d62828", font=("Segoe UI", 10))
        self.history.tag_configure("system", foreground="#888", font=("Segoe UI", 9, "italic"))
        self.history.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Item 2: input on top of bottom area, button row spans full width below.
        # This guarantees the buttons are always visible regardless of width.
        bottom = ttk.Frame(self.root, padding=(4, 4))
        bottom.pack(side=tk.BOTTOM, fill=tk.X)

        self.input_text = tk.Text(bottom, height=4, wrap="word", font=("Segoe UI", 10))
        self.input_text.pack(side=tk.TOP, fill=tk.X, padx=0, pady=(0, 4))

        # Item 4: Enter sends, Shift+Enter inserts a newline (Tk default).
        self.input_text.bind("<Return>", self._on_return_key)
        self.input_text.bind("<Shift-Return>", lambda e: None)  # explicit pass-through
        # Keep Ctrl+Enter as an alias for muscle memory.
        self.input_text.bind("<Control-Return>", self._on_return_key)

        button_row = ttk.Frame(bottom)
        button_row.pack(side=tk.TOP, fill=tk.X)
        # Item 6: Cancel button to abort an in-flight chat.
        self.cancel_btn = ttk.Button(button_row, text="Cancel", command=self._on_cancel,
                                     state="disabled")
        self.cancel_btn.pack(side=tk.LEFT, padx=(0, 4))
        self.send_btn = ttk.Button(button_row, text="Send (Enter)", command=self._send)
        self.send_btn.pack(side=tk.RIGHT, fill=tk.X, expand=True)

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
        if self._backend is None:
            kwargs = {"verbose": False}
            if self._model:
                kwargs["model"] = self._model
            if self._backend_kind == "api":
                self._backend = AnthropicBackend(**kwargs)
            else:
                if self._mcp_handle is not None:
                    kwargs["mcp_config_json"] = self._mcp_handle.mcp_config_json
                    if self._mcp_handle.session_id:
                        kwargs["session_id"] = self._mcp_handle.session_id
                        # If we have a session_id at all, it ALREADY exists in
                        # claude's storage - either because we reattached to a
                        # live supervisor or because a previous panel persisted
                        # it on disk. Either way, the FIRST chat must use
                        # --resume <id>, not --session-id <id> (which would
                        # collide and trigger our recovery path on every fresh
                        # launch with a prior conversation on disk).
                        kwargs["session_started"] = True
                # Item 7: callbacks for orphan-cleanup + session rotation.
                kwargs["on_session_rotated"] = self._on_session_rotated
                kwargs["on_orphan_cleanup"] = self._on_orphan_cleanup
                self._backend = ClaudeCodeBackend(**kwargs)
                # Persist whatever id we ended up with so a relaunch can resume.
                try:
                    mcp_supervisor.write_session_id(self._backend.session_id)
                except Exception as exc:
                    print(f"[panel] write_session_id failed: {exc}", file=sys.stderr)
        return self._backend

    def _on_session_rotated(self, new_session_id):
        try:
            mcp_supervisor.write_session_id(new_session_id)
        except Exception as exc:
            print(f"[panel] write_session_id (rotated) failed: {exc}", file=sys.stderr)
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

    # -------- Chat flow --------

    def _send(self):
        if self.busy or self._closing:
            return
        text = self.input_text.get("1.0", "end").strip()
        if not text:
            return
        self.input_text.delete("1.0", "end")
        self._append("you", text, "user")
        self.busy = True
        self.send_btn.config(state="disabled", text="...")
        self.cancel_btn.config(state="normal")
        threading.Thread(target=self._chat_thread, args=(text,), daemon=True).start()

    def _chat_thread(self, prompt):
        try:
            backend = self._ensure_backend()
            reply = backend.chat(prompt)
            self.outbox.put(("assistant", reply))
        except Exception as exc:
            self.outbox.put(("error", f"{type(exc).__name__}: {exc}"))
        finally:
            self.outbox.put(("__done__", ""))

    def _drain_outbox(self):
        if self._closing:
            return
        try:
            while True:
                kind, msg = self.outbox.get_nowait()
                if kind == "__done__":
                    self.busy = False
                    self.send_btn.config(state="normal", text="Send (Enter)")
                    self.cancel_btn.config(state="disabled")
                elif kind == "error":
                    self._append("error", msg, "error")
                elif kind == "system":
                    self._sysline(msg)
                else:
                    self._append("LLSM", msg, "assistant")
        except queue.Empty:
            pass
        self._safe_after(80, self._drain_outbox)

    # -------- Helpers --------

    def _append(self, who, text, tag):
        self.history.config(state="normal")
        self.history.insert("end", f"{who}: ", tag)
        self.history.insert("end", text + "\n\n", tag)
        self.history.see("end")
        self.history.config(state="disabled")

    def _sysline(self, text):
        print(f"[panel/sys] {text}", file=sys.stderr)
        self.history.config(state="normal")
        self.history.insert("end", text + "\n\n", "system")
        self.history.see("end")
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
    ap.add_argument("--backend", choices=["claude-code", "api"], default="claude-code",
                    help="LLM backend (default: claude-code)")
    ap.add_argument("--connect-to-existing", action="store_true",
                    help="Reattach to a running MCP supervisor instead of spawning fresh")
    ap.add_argument("--always-on-top", action="store_true",
                    help="Keep the panel above other windows (still independent of SCIA's lifecycle)")
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

    try:
        load_scia()
        LlsmPanel(
            model=args.model,
            backend_kind=args.backend,
            connect_existing=args.connect_to_existing,
            always_on_top=args.always_on_top,
        ).run()
        return 0
    finally:
        # Releasing the mutex on exit lets a relaunch proceed.
        if mutex_handle not in (None, "ignore"):
            try:
                win32event.ReleaseMutex(mutex_handle)
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
