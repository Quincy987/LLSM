"""LLSM MCP server — exposes the SCIA tool surface to any MCP client.

Two transports:

* **stdio** (default): legacy mode, used by the original `.mcp.json` flow where
  Claude Code spawns one server per chat turn. SCIA is launched lazily on first
  tool call and torn down at process exit. Backwards compatible.

* **HTTP** (--http): persistent server. Started ONCE by the panel for the whole
  session; SCIA + the in-memory ADM model survive across LLM turns so the
  engineer can chat iteratively (e.g. "now move the support 1 m left").

  Lifecycle in HTTP mode is owned by the **panel**, not the LLM:
  - close_scia is NOT registered as an MCP tool (the LLM never sees it).
  - shutdown_server is NOT an MCP tool either.
  - Teardown happens via POST /admin/shutdown (token-gated) or signal.

Usage
-----
    # Standalone debug, stdio:
    python -m llsm.mcp_server

    # Persistent HTTP (called by panel.py):
    python -m llsm.mcp_server --http \
        --port-file C:/path/port.txt \
        --pid-file C:/path/mcp.pid
"""
from __future__ import annotations
import argparse
import atexit
import json
import os
import secrets
import signal
import socket
import sys
import threading
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .scia.loader import load as load_scia
from .scia import tools as scia_tools
from .scia.tools import LlsmContext


# --- Shared instructions string (used by both transports) ---
INSTRUCTIONS_BASE = (
    "LLSM drives SCIA Engineer to build structural models from natural language.\n\n"
    "Strict workflow:\n"
    "  1. new_model(name=...)\n"
    "  2. add_material -> add_rectangular_section -> add_node(s) -> add_beam(s)\n"
    "     -> add_*_support -> add_load_group -> add_load_case -> add_line_load\n"
    "  3. launch_scia -> push_model -> run_analysis\n\n"
    "Units: lengths in mm; line loads in kN/m. Z axis is vertical (up positive); "
    "for gravity-like downward loads use direction='Z' with a NEGATIVE magnitude."
)

INSTRUCTIONS_PERSISTENT = (
    INSTRUCTIONS_BASE
    + "\n\nThis is a PERSISTENT session. SCIA and the in-memory model survive between "
    "turns. Treat each user message as a continuation of the same project. "
    "Do NOT attempt to close SCIA — the panel manages its lifecycle.\n\n"
    "Prefer edit primitives (update_node, delete_object, set_line_load_magnitude, "
    "set_member_cross_section, set_member_endpoints) over rebuilding from scratch.\n\n"
    "Edit semantics:\n"
    "  - delete_object refuses if dependents exist and returns the blocker list. "
    "Pass cascade=True to remove dependents automatically, or delete them first.\n"
    "  - update_node and set_member_endpoints rollback on failure and report the "
    "invalidated beams. Don't retry blindly when you receive a blocker list.\n"
    "  - After ANY edit, just call run_analysis — the mesh is regenerated "
    "automatically. You do NOT need to call push_model again."
)

INSTRUCTIONS_STDIO = INSTRUCTIONS_BASE + "\n\n  4. close_scia at the end (or on error)"


# --- Context and lifecycle (shared) ---

_ctx = LlsmContext()
_teardown_lock = threading.Lock()
_teardown_done = False


def _teardown(reason: str = "exit") -> None:
    """Close SCIA + dispose builder. Idempotent. Safe from any thread."""
    global _teardown_done
    with _teardown_lock:
        if _teardown_done:
            return
        _teardown_done = True
    try:
        _ctx.close()
    except Exception as e:
        print(f"[mcp] teardown error ({reason}): {e}", file=sys.stderr)


atexit.register(lambda: _teardown("atexit"))


# --- Tool registration ---

def _register_common_tools(mcp: FastMCP) -> None:
    """Tools available in BOTH transports."""

    @mcp.tool()
    def new_model(name: str = "LLSM_Model") -> dict:
        """Start a fresh in-memory ADM model. Call this FIRST before adding anything."""
        return scia_tools.new_model(_ctx, name=name)

    @mcp.tool()
    def add_material(name: str, kind: str = "Steel") -> dict:
        """Add a material. kind: Steel, Concrete, Timber, or Aluminium."""
        return scia_tools.add_material(_ctx, name=name, kind=kind)

    @mcp.tool()
    def add_rectangular_section(name: str, material: str, width_mm: float, height_mm: float) -> dict:
        """Add a rectangular cross-section (width x height, mm)."""
        return scia_tools.add_rectangular_section(_ctx, name=name, material=material,
                                                  width_mm=width_mm, height_mm=height_mm)

    @mcp.tool()
    def add_node(name: str, x_mm: float, y_mm: float, z_mm: float) -> dict:
        """Add a structural point (node). X/Y horizontal, Z vertical up."""
        return scia_tools.add_node(_ctx, name=name, x_mm=x_mm, y_mm=y_mm, z_mm=z_mm)

    @mcp.tool()
    def add_beam(name: str, node_a: str, node_b: str, cross_section: str) -> dict:
        """Add a straight 1D beam between two existing nodes."""
        return scia_tools.add_beam(_ctx, name=name, node_a=node_a, node_b=node_b, cross_section=cross_section)

    @mcp.tool()
    def add_fixed_support(name: str, node: str) -> dict:
        """Add a fully-restrained (all 6 DOFs) support on a node."""
        return scia_tools.add_fixed_support(_ctx, name=name, node=node)

    @mcp.tool()
    def add_pinned_support(name: str, node: str) -> dict:
        """Add a pinned support — translations restrained, rotations free."""
        return scia_tools.add_pinned_support(_ctx, name=name, node=node)

    @mcp.tool()
    def add_roller_support(name: str, node: str, direction: str = "Z") -> dict:
        """Add a roller — rigid only in direction (X, Y, or Z), free elsewhere."""
        return scia_tools.add_roller_support(_ctx, name=name, node=node, direction=direction)

    @mcp.tool()
    def add_load_group(name: str, kind: str = "Permanent") -> dict:
        """Add a load group. kind: Permanent, Variable, or Accidental."""
        return scia_tools.add_load_group(_ctx, name=name, kind=kind)

    @mcp.tool()
    def add_load_case(name: str, group: str, kind: str = "Standard") -> dict:
        """Add a load case inside a group. kind: Standard or SelfWeight."""
        return scia_tools.add_load_case(_ctx, name=name, group=group, kind=kind)

    @mcp.tool()
    def add_line_load(name: str, beam: str, load_case: str,
                      magnitude_kn_per_m: float, direction: str = "Z") -> dict:
        """Add a uniform line load on a beam, in kN/m. For gravity-downward, use direction='Z' with NEGATIVE magnitude."""
        return scia_tools.add_line_load(_ctx, name=name, beam=beam, load_case=load_case,
                                        magnitude_kn_per_m=magnitude_kn_per_m, direction=direction)

    @mcp.tool()
    def model_summary() -> dict:
        """Return item count + names of everything currently in the in-memory ADM model."""
        return scia_tools.model_summary(_ctx)

    # --- Edit primitives ---
    @mcp.tool()
    def update_node(name: str, x_mm: float, y_mm: float, z_mm: float) -> dict:
        """Move an existing node to new coordinates. Try-or-rollback: returns invalidated_beams on failure.
        After this, just call run_analysis — the mesh re-generates automatically."""
        return scia_tools.update_node(_ctx, name=name, x_mm=x_mm, y_mm=y_mm, z_mm=z_mm)

    @mcp.tool()
    def delete_object(name: str, cascade: bool = False) -> dict:
        """Delete an ADM object. Refuses if dependents exist (returns blockers).
        Pass cascade=True to delete dependents recursively."""
        return scia_tools.delete_object(_ctx, name=name, cascade=cascade)

    @mcp.tool()
    def set_line_load_magnitude(name: str, magnitude_kn_per_m: float) -> dict:
        """Adjust the magnitude of an existing line load (kN/m)."""
        return scia_tools.set_line_load_magnitude(_ctx, name=name, magnitude_kn_per_m=magnitude_kn_per_m)

    @mcp.tool()
    def set_member_cross_section(name: str, cross_section: str) -> dict:
        """Swap the cross-section of an existing curve member to one already in the model."""
        return scia_tools.set_member_cross_section(_ctx, name=name, cross_section=cross_section)

    @mcp.tool()
    def set_member_endpoints(name: str, node_a: str, node_b: str) -> dict:
        """Re-route a beam between two existing nodes. Try-or-rollback."""
        return scia_tools.set_member_endpoints(_ctx, name=name, node_a=node_a, node_b=node_b)

    # --- SCIA-drive ---
    @mcp.tool()
    def launch_scia(visible: bool = False) -> dict:
        """Launch SCIA Engineer. visible=false (default) runs it minimized without stealing focus."""
        return scia_tools.launch_scia(_ctx, visible=visible)

    @mcp.tool()
    def push_model() -> dict:
        """Open an empty SCIA project and push the current in-memory ADM model.
        After edits, do NOT call this again — the edit tools auto-propagate; just call run_analysis."""
        return scia_tools.push_model(_ctx)

    @mcp.tool()
    def run_analysis() -> dict:
        """Generate FE mesh (only when needed) and run linear analysis."""
        return scia_tools.run_analysis(_ctx)

    # --- Results ---
    @mcp.tool()
    def get_beam_inner_forces(beam: str, load_case: str) -> dict:
        """Read beam internal forces (N, Vy, Vz, Mx, My, Mz) per section for a load case.

        Forces in N, moments in N·m. Returns positions in metres + per-magnitude lists +
        min/max/abs_max summary.
        """
        return scia_tools.get_beam_inner_forces(_ctx, beam=beam, load_case=load_case)

    @mcp.tool()
    def get_beam_deflection(beam: str, load_case: str) -> dict:
        """Read beam deformation (Ux, Uy, Uz, Fix, Fiy, Fiz) per section for a load case.

        Translations in metres (multiply by 1000 for mm), rotations in radians.
        """
        return scia_tools.get_beam_deflection(_ctx, beam=beam, load_case=load_case)


def _register_stdio_only_tools(mcp: FastMCP) -> None:
    """Tools that exist ONLY in the legacy stdio transport.

    close_scia stays here because the original `.mcp.json` flow has the LLM
    own SCIA's lifecycle. In HTTP mode, the panel owns it instead, so this
    tool is deliberately NOT registered.
    """

    @mcp.tool()
    def close_scia() -> dict:
        """Close SCIA and clean up. Call at end of workflow or on fatal error."""
        return scia_tools.close_scia(_ctx)


# --- HTTP transport: port discovery, pid-file, admin route ---

def _find_free_port(start: int = 8765, count: int = 100) -> int:
    """Bind-and-release sweep to find an open localhost port.

    Small TOCTOU window before FastMCP rebinds, but acceptable for single-user
    desktop scope.
    """
    for p in range(start, start + count):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            try:
                s.close()
            except Exception:
                pass
    raise RuntimeError(f"no free port in range {start}-{start + count}")


def _atomic_write(path: Path, payload: str) -> None:
    """Write to <path>.tmp then os.replace — readers see either old or full new."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def _install_signal_handlers() -> None:
    """Install graceful-shutdown handlers for SIGINT and (Windows) SIGBREAK.

    Without these the panel's CTRL_BREAK_EVENT escalation step is a no-op,
    leaving the layered shutdown with a hole in the middle.
    """
    def _sig_handler(signum, _frame):
        print(f"[mcp] signal {signum} received - shutting down", file=sys.stderr)
        _teardown(f"signal-{signum}")
        os._exit(0)

    try:
        signal.signal(signal.SIGINT, _sig_handler)
    except Exception:
        pass
    if hasattr(signal, "SIGBREAK"):
        try:
            signal.signal(signal.SIGBREAK, _sig_handler)  # type: ignore[attr-defined]
        except Exception:
            pass


def _run_http(port_file: Path, pid_file: Path) -> None:
    """Persistent HTTP transport. Owns SCIA across the whole panel session."""
    port = _find_free_port()
    token = secrets.token_urlsafe(32)

    mcp = FastMCP(
        "llsm",
        instructions=INSTRUCTIONS_PERSISTENT,
        host="127.0.0.1",
        port=port,
    )

    _register_common_tools(mcp)
    # NOTE: close_scia + shutdown_server are intentionally NOT registered here.
    # The LLM must not be able to kill the persistent session.

    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response

    @mcp.custom_route("/admin/shutdown", methods=["POST"])
    async def admin_shutdown(request: Request) -> Response:
        provided = request.headers.get("X-LLSM-Shutdown-Token", "")
        if not secrets.compare_digest(provided, token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        # Respond first, then schedule teardown so the response actually flushes.
        def _delayed_exit():
            time.sleep(0.2)
            _teardown("admin-route")
            os._exit(0)

        threading.Thread(target=_delayed_exit, daemon=True).start()
        return JSONResponse({"ok": True})

    @mcp.custom_route("/admin/ping", methods=["GET"])
    async def admin_ping(_request: Request) -> Response:
        return JSONResponse({"ok": True, "transport": "http", "port": port})

    pid_payload = json.dumps({
        "pid": os.getpid(),
        "port": port,
        "shutdown_token": token,
        "cmdline_marker": "llsm.mcp_server",
        "started_at": time.time(),
    })
    _atomic_write(pid_file, pid_payload)
    _atomic_write(port_file, str(port))

    _install_signal_handlers()

    print(f"[mcp] http transport on http://127.0.0.1:{port}/mcp", file=sys.stderr)
    try:
        mcp.run(transport="streamable-http")
    finally:
        for f in (port_file, pid_file):
            try:
                f.unlink(missing_ok=True)
            except Exception:
                pass
        _teardown("http-exit")


def _run_stdio() -> None:
    """Legacy stdio transport — backward-compat with the original .mcp.json flow."""
    mcp = FastMCP("llsm", instructions=INSTRUCTIONS_STDIO)
    _register_common_tools(mcp)
    _register_stdio_only_tools(mcp)
    mcp.run()


def main() -> None:
    ap = argparse.ArgumentParser(prog="llsm.mcp_server")
    ap.add_argument("--http", action="store_true",
                    help="Run as a persistent HTTP MCP server (used by the panel).")
    ap.add_argument("--port-file", type=Path, default=None,
                    help="Path to write the bound port to (HTTP mode).")
    ap.add_argument("--pid-file", type=Path, default=None,
                    help="Path to write the JSON pid-file payload to (HTTP mode).")
    args = ap.parse_args()

    # SCIA DLLs are imported lazily by tools, but loading the resolver early
    # lets us fail fast if the install path is wrong.
    load_scia()

    if args.http:
        if args.port_file is None or args.pid_file is None:
            ap.error("--http requires --port-file and --pid-file")
        _run_http(args.port_file, args.pid_file)
    else:
        _run_stdio()


if __name__ == "__main__":
    main()
