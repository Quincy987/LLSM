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

LLSM_HOME = Path.home() / ".llsm"
PID_FILE = LLSM_HOME / "mcp.pid"
PORT_FILE = LLSM_HOME / "mcp.port"
CMDLINE_MARKER = "llsm.mcp_server"

# How long the panel waits for the child to bind + drop the port-file.
PORT_FILE_TIMEOUT_S = 10.0
PORT_FILE_POLL_INTERVAL_S = 0.1

# Graceful shutdown layers.
ADMIN_SHUTDOWN_WAIT_S = 5.0
CTRL_BREAK_WAIT_S = 3.0


@dataclass
class McpHandle:
    proc: subprocess.Popen
    port: int
    shutdown_token: str
    pid_file: Path
    port_file: Path

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    @property
    def mcp_config_json(self) -> str:
        """JSON string that maps to claude --mcp-config <json>."""
        return json.dumps({
            "mcpServers": {
                "llsm": {"type": "http", "url": self.url}
            }
        })


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

    proc = subprocess.Popen(
        cmd,
        creationflags=creationflags,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for the child to atomically write its port-file.
    deadline = time.monotonic() + PORT_FILE_TIMEOUT_S
    port: int | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            err = (proc.stderr.read() or b"").decode(errors="replace") if proc.stderr else ""
            raise RuntimeError(
                f"MCP server exited early (code={proc.returncode})\n{err}"
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
        raise RuntimeError(
            f"MCP server did not bind a port within {PORT_FILE_TIMEOUT_S}s"
        )

    pid_data = _read_pid_file(PID_FILE) or {}
    token = pid_data.get("shutdown_token", "")
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
    """
    proc = handle.proc

    def _alive() -> bool:
        return proc.poll() is None

    # Layer 1: admin route
    if _alive():
        try:
            import urllib.request
            req = urllib.request.Request(
                f"http://127.0.0.1:{handle.port}/admin/shutdown",
                method="POST",
                headers={"X-LLSM-Shutdown-Token": handle.shutdown_token},
                data=b"",
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                if verbose:
                    print(f"[mcp-supervisor] /admin/shutdown -> {resp.status}", file=sys.stderr)
        except Exception as e:
            if verbose:
                print(f"[mcp-supervisor] admin shutdown failed: {e}", file=sys.stderr)
        try:
            proc.wait(timeout=ADMIN_SHUTDOWN_WAIT_S)
        except subprocess.TimeoutExpired:
            pass

    # Layer 2: CTRL_BREAK_EVENT (Windows) / SIGINT (POSIX)
    if _alive():
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

    # Layer 3: terminate
    if _alive():
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
