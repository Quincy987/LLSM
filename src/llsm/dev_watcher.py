"""File watcher for `--dev` hot-reload mode.

Watches src/llsm/ for source file changes. MCP-side files (scia/**, mcp_server.py)
trigger an auto-restart of the MCP server. Other files emit a SYS line nudge
suggesting a manual panel restart (debounced per file so a long save burst
doesn't flood chat.log).

Runs the watchdog Observer in its own thread; all Tk interactions are marshaled
onto the Tk thread by the caller-supplied `tk_call` (root.after(0, ...)).
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Callable

# `watchdog` is an optional dependency (`pip install -e .[dev]`). The caller is
# responsible for handling ImportError before instantiating _DevWatcher.
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer


DEBOUNCE_S = 0.5
NUDGE_COOLDOWN_S = 30.0


def _classify(path: Path, src_root: Path) -> str | None:
    """Return 'mcp' | 'panel' | None for a filesystem event path.

    'mcp'   -> MCP-side: triggers MCP auto-restart
    'panel' -> Panel-side: emits a one-line nudge (no automatic action)
    None    -> Ignored entirely (caches, swap files, tests, cli.py, etc.)
    """
    try:
        rel = path.resolve().relative_to(src_root.resolve())
    except (ValueError, OSError):
        return None
    parts = rel.parts
    if not parts:
        return None
    name = parts[-1]
    # Editor swap/backup files and bytecode caches.
    if name.endswith((".pyc", ".pyo", ".swp", ".swx", ".tmp", ".bak")):
        return None
    if name.startswith("~$") or name.startswith("."):
        return None
    if "__pycache__" in parts:
        return None
    # Only watch Python sources.
    if not name.endswith(".py"):
        return None
    # cli.py is a separate entry point not used by the running panel.
    if len(parts) == 1 and name == "cli.py":
        return None
    if parts[0] == "scia":
        return "mcp"
    if len(parts) == 1 and name == "mcp_server.py":
        return "mcp"
    return "panel"


class _Handler(FileSystemEventHandler):
    """Routes filesystem events to the watcher's queue.

    Editors save in different ways: VS Code rewrites in place (modified),
    vim with backup writes a new file then renames (moved), some editors
    delete-then-recreate (created). Subscribe to all three and let the
    debounce in _DevWatcher coalesce duplicates.
    """

    def __init__(self, on_event: Callable[[Path], None]) -> None:
        super().__init__()
        self._on_event = on_event

    def _route(self, raw_path: str) -> None:
        try:
            self._on_event(Path(raw_path))
        except Exception as exc:
            print(f"[dev-watcher] route error: {exc}", file=sys.stderr)

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._route(event.src_path)

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._route(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        # Save-via-rename editors land on dest_path.
        dst = getattr(event, "dest_path", None) or event.src_path
        self._route(dst)


class _DevWatcher:
    """Background filesystem watcher for hot-reload.

    Owns the watchdog Observer and a debounce timer. When the timer fires:
      - MCP-side files newer than the last spawn -> marshal MCP restart on Tk
      - Panel-side files (per-file cooldown)     -> marshal SYS-line nudge on Tk
    """

    def __init__(
        self,
        *,
        src_root: Path,
        tk_call: Callable[[Callable[[], None]], None],
        on_mcp_change: Callable[[list[str]], None],
        on_panel_change: Callable[[list[str]], None],
        is_mcp_busy: Callable[[], bool],
        spawn_wall_time: Callable[[], float],
    ) -> None:
        self._src_root = src_root
        self._tk_call = tk_call
        self._on_mcp_change = on_mcp_change
        self._on_panel_change = on_panel_change
        self._is_mcp_busy = is_mcp_busy
        self._spawn_wall_time = spawn_wall_time

        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._pending_mcp: dict[str, float] = {}    # rel-path -> mtime (wall clock)
        self._pending_panel: dict[str, float] = {}
        self._last_panel_nudge: dict[str, float] = {}  # rel-path -> monotonic ts

        self._observer = Observer()
        self._observer.schedule(_Handler(self._on_raw_event), str(src_root), recursive=True)

    def start(self) -> None:
        self._observer.start()

    def stop(self) -> None:
        try:
            self._observer.stop()
            self._observer.join(timeout=2.0)
        except Exception as exc:
            print(f"[dev-watcher] observer stop failed: {exc}", file=sys.stderr)
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    # --- internals ---

    def _on_raw_event(self, path: Path) -> None:
        layer = _classify(path, self._src_root)
        if layer is None:
            return
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = time.time()
        try:
            rel = str(path.resolve().relative_to(self._src_root.resolve()))
        except (ValueError, OSError):
            rel = str(path)
        rel = rel.replace("\\", "/")
        with self._lock:
            if layer == "mcp":
                self._pending_mcp[rel] = mtime
            else:
                self._pending_panel[rel] = mtime
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(DEBOUNCE_S, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        # Hold the lock for the whole snapshot+decide step so a concurrent
        # _fire (reachable when a new event arrives between scheduling the
        # tk_call and the timer thread waking up) can't race on
        # _last_panel_nudge, and so events arriving during the act of
        # snapshotting can't be silently dropped.
        with self._lock:
            self._timer = None
            # If watchdog is mid-restart, defer: leave pending untouched and
            # re-arm. The next fire will see whether the watchdog is done.
            if self._is_mcp_busy():
                self._timer = threading.Timer(DEBOUNCE_S, self._fire)
                self._timer.daemon = True
                self._timer.start()
                return
            # Snapshot AND clear pending atomically. New events arriving after
            # this point go into a fresh dict and trigger their own debounce.
            mcp_pending = self._pending_mcp
            panel_pending = self._pending_panel
            self._pending_mcp = {}
            self._pending_panel = {}

            # MCP-side: skip files whose mtime is older than the last spawn
            # time (the running MCP already loaded them). Wall-clock vs
            # wall-clock — both from time.time() / os.stat.
            mcp_action_files: list[str] | None = None
            if mcp_pending:
                spawn_wall = self._spawn_wall_time()
                fresh = sorted(rel for rel, mt in mcp_pending.items() if mt > spawn_wall)
                if fresh:
                    mcp_action_files = fresh

            # Panel-side: nudge per-file at most once every NUDGE_COOLDOWN_S
            # so format-on-save doesn't spam chat.log.
            panel_action_files: list[str] | None = None
            if panel_pending:
                now = time.monotonic()
                to_nudge: list[str] = []
                for rel in panel_pending:
                    last = self._last_panel_nudge.get(rel, 0.0)
                    if now - last >= NUDGE_COOLDOWN_S:
                        to_nudge.append(rel)
                        self._last_panel_nudge[rel] = now
                if to_nudge:
                    to_nudge.sort()
                    panel_action_files = to_nudge

        # tk_call dispatches asynchronously (root.after(0, ...)), safe
        # outside the lock.
        if mcp_action_files is not None:
            self._tk_call(lambda files=mcp_action_files: self._on_mcp_change(files))
        if panel_action_files is not None:
            self._tk_call(lambda files=panel_action_files: self._on_panel_change(files))
