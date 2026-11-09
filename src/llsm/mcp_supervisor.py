"""Supervisor for the persistent MCP server child process.

Owned by the panel. Handles:
  - Stale-cleanup of an MCP server left over from a crashed previous panel
  - Spawning the new server with --http in a separate Windows process group
  - Polling for the port-file (atomic-write means partial reads can't happen)
  - Building the --mcp-config JSON the LLM backend will hand to claude
  - Layered graceful shutdown: admin route -> CTRL_BREAK_EVENT -> terminate

Stale-cleanup checks BOTH that the PID is alive AND that the process's command
line still references llsm.mcp_server, so we never kill an unrelated process
that happened to recycle the PID.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

from .mcp_guard import TOKEN_HEADER

LLSM_HOME = Path.home() / ".llsm"
PID_FILE = LLSM_HOME / "mcp.pid"
PORT_FILE = LLSM_HOME / "mcp.port"
# Legacy session-id file. Owned and migrated by llsm.history; this module no
# longer reads or writes it. Kept here only as a path constant so the
# migration code can locate it.
LEGACY_SESSION_ID_FILE = LLSM_HOME / "mcp.session-id"
LOG_FILE = LLSM_HOME / "mcp.log"
CMDLINE_MARKER = "llsm.mcp_server"

# How long the panel waits for the child to bind + drop the port-file.
# Bumped from 10s -> 30s: cold-start of pythonnet + SCIA DLL discovery
# regularly takes 12-20s on this machine, especially right after a force_kill
# when residual SCIA processes are still being torn down. The previous 10s
# turned a successful restart into a fatal "did not bind a port" error.
PORT_FILE_TIMEOUT_S = 30.0
PORT_FILE_POLL_INTERVAL_S = 0.1

# Graceful shutdown layers.
ADMIN_SHUTDOWN_WAIT_S = 5.0
CTRL_BREAK_WAIT_S = 3.0


@dataclass
class McpHandle:
    proc: subprocess.Popen | None  # None when reattached to a pre-existing supervisor
    port: int
    shutdown_token: str
    pid_file: Path
    port_file: Path
    reattached: bool = False  # True if connect_to_existing() returned this handle

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    @property
    def mcp_config_json(self) -> str:
        """JSON string that maps to claude --mcp-config <json>.

        Compact separators avoid embedded spaces, which keeps the value safe
        even if some future caller routes it through a path with shell parsing.
        """
        return json.dumps(
            {"mcpServers": {"llsm": {"type": "http", "url": self.url, "headers": {TOKEN_HEADER: self.shutdown_token}}}},
            separators=(",", ":"),
        )


def _tail_log(path: Path, max_bytes: int = 4000) -> str:
    """Best-effort tail of the child stdio log, for surfacing in error messages."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return ""
    except Exception:
        return ""
    offset = max(0, size - max_bytes)
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            return f.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def _read_pid_file(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except Exception:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "pid" not in data:
        return None
    return data


def _process_matches(pid: int, marker: str) -> bool:
    """True iff a process with that PID exists and its cmdline references marker."""
    try:
        p = psutil.Process(pid)
        cmdline = " ".join(p.cmdline())
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    return marker in cmdline


def _kill_tree(pid: int) -> None:
    """Kill the process and all of its descendants (in case SCIA was spawned)."""
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    procs = parent.children(recursive=True) + [parent]
    for p in procs:
        try:
            p.kill()
        except Exception:
            pass
    psutil.wait_procs(procs, timeout=3)


def cleanup_stale_server(verbose: bool = False) -> bool:
    """If a previous MCP server is still running (panel crashed), kill it cleanly.

    Returns True if a stale process was found and killed.
    """
    data = _read_pid_file(PID_FILE)
    if data is None:
        return False
    pid = data.get("pid")
    if not isinstance(pid, int):
        try:
            PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        return False
    if not _process_matches(pid, CMDLINE_MARKER):
        # PID is dead or recycled — just clean the stale file
        for f in (PID_FILE, PORT_FILE):
            try:
                f.unlink(missing_ok=True)
            except Exception:
                pass
        return False
    if verbose:
        print(f"[mcp-supervisor] killing stale MCP server pid={pid}", file=sys.stderr)
    _kill_tree(pid)
    for f in (PID_FILE, PORT_FILE):
        try:
            f.unlink(missing_ok=True)
        except Exception:
            pass
    return True


def spawn(verbose: bool = False) -> McpHandle:
    """Spawn a new persistent MCP server child process.

    Creates LLSM_HOME if needed, cleans up stale state from a crashed previous
    run, then launches `python -m llsm.mcp_server --http`. Blocks until the
    port-file appears (atomic write means a successful read returns the
    final value; the only failure mode is timeout if the child never started).
    """
    LLSM_HOME.mkdir(parents=True, exist_ok=True)
    cleanup_stale_server(verbose=verbose)

    # Defense in depth: cleanup_stale_server only removes PORT_FILE/PID_FILE
    # when it positively identifies a stale child. If the prior process died
    # leaving PORT_FILE alone (or PID_FILE was manually deleted), our polling
    # loop below would read the STALE port before the new child rewrites it.
    # Clear them unconditionally now so the only port-file we ever see is the
    # one our new child writes.
    for f in (PORT_FILE, PID_FILE):
        try:
            f.unlink(missing_ok=True)
        except Exception:
            pass

    creationflags = 0
    if os.name == "nt":
        # CREATE_NEW_PROCESS_GROUP lets us send CTRL_BREAK_EVENT later.
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    cmd = [
        sys.executable,
        "-m", "llsm.mcp_server",
        "--http",
        "--port-file", str(PORT_FILE),
        "--pid-file", str(PID_FILE),
    ]
    if verbose:
        print(f"[mcp-supervisor] spawn: {cmd}", file=sys.stderr)

    # Route child stdio to a log file rather than PIPE. With PIPE, nobody
    # drains the buffer, so once it fills (~64KB on Windows) the child blocks
    # on print/log writes — fatal during a long session where uvicorn logs
    # every MCP request.
    log_fp = open(LOG_FILE, "ab")
    try:
        proc = subprocess.Popen(
            cmd,
            creationflags=creationflags,
            stdout=log_fp,
            stderr=log_fp,
        )
    finally:
        # Popen dups the fd; we don't need our copy any more.
        log_fp.close()

    # Wait for the child to atomically write its port-file.
    deadline = time.monotonic() + PORT_FILE_TIMEOUT_S
    port: int | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            tail = _tail_log(LOG_FILE, max_bytes=4000)
            raise RuntimeError(
                f"MCP server exited early (code={proc.returncode}); "
                f"log tail (see {LOG_FILE}):\n{tail}"
            )
        try:
            raw = PORT_FILE.read_text(encoding="utf-8").strip()
            if raw:
                port = int(raw)
                break
        except FileNotFoundError:
            pass
        except ValueError:
            pass
        time.sleep(PORT_FILE_POLL_INTERVAL_S)

    if port is None:
        # Port-file never appeared; abort and clean up.
        _kill_tree(proc.pid)
        tail = _tail_log(LOG_FILE, max_bytes=4000)
        raise RuntimeError(
            f"MCP server did not bind a port within {PORT_FILE_TIMEOUT_S}s; "
            f"log tail (see {LOG_FILE}):\n{tail}"
        )

    pid_data = _read_pid_file(PID_FILE) or {}
    token = pid_data.get("token") or pid_data.get("shutdown_token", "")
    if not token:
        _kill_tree(proc.pid)
        raise RuntimeError("MCP server pid-file missing shutdown token")

    return McpHandle(
        proc=proc,
        port=port,
        shutdown_token=token,
        pid_file=PID_FILE,
        port_file=PORT_FILE,
    )


def shutdown(handle: McpHandle, verbose: bool = False) -> None:
    """Layered graceful shutdown of the MCP server child.

    1. POST /admin/shutdown with the token; expect 200 -> clean exit
    2. If still alive after 5 s: send CTRL_BREAK_EVENT to the process group
    3. If still alive after another 3 s: terminate

    Total worst case ~8 s.

    If the handle is reattached (proc=None - we don't own the subprocess),
    only the admin route is used; signal/terminate require process ownership.
    """
    proc = handle.proc

    def _alive() -> bool:
        if proc is None:
            data = _read_pid_file(handle.pid_file)
            if data is None:
                return False
            pid = data.get("pid")
            if not isinstance(pid, int):
                return False
            return _process_matches(pid, CMDLINE_MARKER)
        return proc.poll() is None

    # Layer 1: admin route
    if _alive():
        try:
            import urllib.request
            req = urllib.request.Request(
                f"http://127.0.0.1:{handle.port}/admin/shutdown",
                method="POST",
                headers={TOKEN_HEADER: handle.shutdown_token},
                data=b"",
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                if verbose:
                    print(f"[mcp-supervisor] /admin/shutdown -> {resp.status}", file=sys.stderr)
        except Exception as e:
            if verbose:
                print(f"[mcp-supervisor] admin shutdown failed: {e}", file=sys.stderr)
        if proc is not None:
            try:
                proc.wait(timeout=ADMIN_SHUTDOWN_WAIT_S)
            except subprocess.TimeoutExpired:
                pass
        else:
            deadline = time.monotonic() + ADMIN_SHUTDOWN_WAIT_S
            while time.monotonic() < deadline and _alive():
                time.sleep(0.1)

    # Layer 2: CTRL_BREAK_EVENT (Windows) / SIGINT (POSIX) - only if we own the process
    if _alive() and proc is not None:
        if os.name == "nt":
            try:
                import signal as _signal
                proc.send_signal(_signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            except Exception as e:
                if verbose:
                    print(f"[mcp-supervisor] CTRL_BREAK_EVENT failed: {e}", file=sys.stderr)
        else:
            try:
                proc.send_signal(2)  # SIGINT
            except Exception:
                pass
        try:
            proc.wait(timeout=CTRL_BREAK_WAIT_S)
        except subprocess.TimeoutExpired:
            pass

    # Layer 3: terminate (only if we own the process)
    if _alive() and proc is not None:
        if verbose:
            print("[mcp-supervisor] terminating MCP server", file=sys.stderr)
        try:
            _kill_tree(proc.pid)
        except Exception:
            pass

    # Pid + port files are cleaned up by the child; if that didn't run,
    # remove them ourselves so the next panel doesn't false-positive on stale.
    for f in (handle.pid_file, handle.port_file):
        try:
            f.unlink(missing_ok=True)
        except Exception:
            pass


def kill_orphan_claudes(session_id: str, verbose: bool = False) -> int:
    """Kill any live claude processes whose command line carries session_id.

    Used to recover from the Ctrl+C-mid-chat case: the panel died but its claude
    subprocess outlived it and is still holding the session-id lock. Bounded
    scope - only kills claude processes carrying OUR session-id.
    """
    if not session_id:
        return 0
    killed = 0
    needle = session_id.lower()
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if "claude" not in name:
                continue
            cmdline = " ".join(proc.cmdline()).lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if needle in cmdline:
            if verbose:
                print(f"[mcp-supervisor] killing orphan claude pid={proc.pid} (carries session-id)", file=sys.stderr)
            try:
                proc.kill()
                killed += 1
            except Exception:
                pass
    if killed:
        time.sleep(0.5)
    return killed


def connect_to_existing(verbose: bool = False) -> McpHandle | None:
    """Try to attach to a supervisor that's already running.

    Returns an McpHandle with proc=None (we don't own the subprocess) and
    reattached=True if a healthy server is found. Returns None if no usable
    server exists - caller should then spawn() a fresh one.
    """
    pid_data = _read_pid_file(PID_FILE)
    if pid_data is None:
        return None
    pid = pid_data.get("pid")
    if not isinstance(pid, int):
        return None
    if not _process_matches(pid, CMDLINE_MARKER):
        return None
    try:
        port_raw = PORT_FILE.read_text(encoding="utf-8").strip()
        port = int(port_raw)
    except Exception:
        return None
    token = pid_data.get("token") or pid_data.get("shutdown_token", "")
    if not token:
        return None
    if verbose:
        print(f"[mcp-supervisor] reattached to running MCP server pid={pid} port={port}", file=sys.stderr)
    return McpHandle(
        proc=None,
        port=port,
        shutdown_token=token,
        pid_file=PID_FILE,
        port_file=PORT_FILE,
        reattached=True,
    )


def health_ping(handle: McpHandle, timeout_s: float = 1.5) -> bool:
    """Cheap alive-check via GET /admin/ping. Returns True iff status==200."""
    try:
        import urllib.request
        request = urllib.request.Request(
            f"http://127.0.0.1:{handle.port}/admin/ping",
            headers={TOKEN_HEADER: handle.shutdown_token},
        )
        with urllib.request.urlopen(request, timeout=timeout_s) as resp:
            return resp.status == 200
    except Exception:
        return False


def force_kill(handle: McpHandle, verbose: bool = False) -> None:
    """Hard-kill a hung supervisor: skip the graceful shutdown layers.

    Called by the panel-side watchdog when /admin/ping has been failing for
    too long - at that point the admin route can't respond and CTRL_BREAK
    won't help (process is wedged inside a SCIA call). Just terminate the
    tree and clean up state files so the next spawn() starts clean.
    """
    proc = handle.proc
    pid_to_kill = None
    if proc is not None:
        pid_to_kill = proc.pid
    else:
        # Reattached path: find the pid via the pid-file.
        data = _read_pid_file(handle.pid_file)
        if data is not None:
            cand = data.get("pid")
            if isinstance(cand, int):
                pid_to_kill = cand
    if pid_to_kill is not None:
        if verbose:
            print(f"[mcp-supervisor] force-killing hung MCP server pid={pid_to_kill}", file=sys.stderr)
        try:
            _kill_tree(pid_to_kill)
        except Exception:
            pass
    # Always clear state files so the next spawn doesn't false-positive on stale.
    for f in (handle.pid_file, handle.port_file):
        try:
            f.unlink(missing_ok=True)
        except Exception:
            pass
