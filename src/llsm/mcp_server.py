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
import asyncio
import atexit
import functools
import json
import os
import secrets
import signal
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import anyio
import uvicorn
from mcp.server.fastmcp import FastMCP

from . import bedding as bedding_tools
from . import moving_load as moving_load_tools
from . import report as report_tools
from .answer_rules import ANSWER_RULES
from .mcp_guard import GuardMiddleware
from .scia import esa_xml
from .scia.autoclick import SciaDialogPending
from .scia.loader import load as load_scia
from .scia import tools as scia_tools
from .scia.tools import LlsmContext


# Single-worker executor for tool bodies. FastMCP runs sync tools directly on
# its asyncio event loop, which means a long SCIA call (launch_scia takes
# 30-60 s, push_model can hang on AdmToAdm errors) blocks /admin/ping too —
# the panel watchdog then false-trips and force-kills the server mid-launch.
# Offloading to a worker thread keeps the loop responsive. max_workers=1 so
# tool calls serialise around the shared LlsmContext (claude rarely fires
# parallel MCP calls but we don't rely on that).
_TOOL_EXEC = ThreadPoolExecutor(max_workers=1, thread_name_prefix="llsm-tool")


def _state_block(result: dict) -> dict:
    """What the model is working on, attached to every tool result."""
    if "saved_by" in result:  # a bedding or moving-load result: the model is a file
        return {"model": result.get("model"), "saved_by": result.get("saved_by")}
    session = _ctx.session
    if session is None:
        scia = "not launched"
    elif session.pending_dialog is not None:
        scia = "waiting for an answer in SCIA"
    elif _ctx.last_calc_ok:
        scia = "analysed"
    elif _ctx.project_opened:
        scia = "pushed"
    else:
        scia = "launched"
    items = _ctx.builder.item_count() if _ctx.builder is not None else 0
    return {"model": _ctx.model_name, "counts": {"items": items}, "scia": scia}


def _run_tool(fn: Callable, args: tuple, kwargs: dict):
    """Run a tool body: a dialog SCIA waits on becomes a clear result, and every result carries the state."""
    try:
        result = fn(*args, **kwargs)
    except SciaDialogPending as exc:
        result = exc.as_result()
    if not isinstance(result, dict):
        return result
    pending = _ctx.session.pending_dialog if _ctx.session is not None else None
    if pending is not None and not result.get("ok", True) and not isinstance(result.get("error"), dict):
        result = SciaDialogPending(pending).as_result()  # a tool body caught it and flattened it to text
    try:
        result["state"] = _state_block(result)
    except Exception as exc:  # the state is a courtesy; it must not cost the result
        print(f"[mcp] state block failed: {exc}", file=sys.stderr)
    return result


def _async_tool(mcp: FastMCP, fn: Callable):
    """Register `fn` as an MCP tool that runs in the worker executor.

    Preserves the function's signature (via functools.wraps) so FastMCP can
    derive the JSON schema from the original type hints — verified against
    mcp 1.27 FuncMetadata which follows __wrapped__.
    """
    @functools.wraps(fn)
    async def _wrapped(*args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _TOOL_EXEC, lambda: _run_tool(fn, args, kwargs)
        )
    mcp.tool()(_wrapped)
    return _wrapped


# --- Shared instructions string (used by both transports) ---
INSTRUCTIONS_BASE = (
    "LLSM drives SCIA Engineer to build structural models from natural language.\n\n"
    "Strict workflow:\n"
    "  1. new_model(name=...)\n"
    "  2. add_material -> add_rectangular_section -> add_node(s) -> add_beam(s)\n"
    "     -> add_*_support -> add_load_group -> add_load_case -> add_line_load\n"
    "  3. launch_scia -> push_model -> run_analysis\n\n"
    "Units: lengths in mm; line loads in kN/m. Z axis is vertical (up positive); "
    "for gravity-like downward loads use direction='Z' with a NEGATIVE magnitude.\n\n"
    "To put soil beddings on the piles of an existing saved model, use bedding_preview, then bedding_apply.\n\n"
    "For moving traffic positions on an existing saved model: moving_load_preview, then moving_load_apply.\n\n"
    + ANSWER_RULES
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
    "  - update_node failure responses include `dependent_beams`: beams that "
    "reference the moved node. Use that list to decide whether to also reroute "
    "or delete those beams. Don't retry blindly.\n"
    "  - set_member_endpoints rolls back its curve change on failure.\n"
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
    try:
        stopped = esa_xml.stop_all()  # a report print must not outlive the server that started it
        if stopped:
            print(f"[mcp] teardown ({reason}): stopped {stopped} ESA_XML run(s) of this server", file=sys.stderr)
    except Exception as e:
        print(f"[mcp] teardown error stopping ESA_XML ({reason}): {e}", file=sys.stderr)


atexit.register(lambda: _teardown("atexit"))


# --- Tool registration ---

def _register_common_tools(mcp: FastMCP) -> None:
    """Tools available in BOTH transports.

    Every tool is registered via `_async_tool` so its sync body runs on a
    worker thread instead of FastMCP's event loop. Without this, a 60 s
    launch_scia call blocks /admin/ping and the panel's watchdog false-trips.
    """

    def new_model(name: str = "LLSM_Model") -> dict:
        """Start a fresh in-memory ADM model. Call this FIRST before adding anything."""
        return scia_tools.new_model(_ctx, name=name)

    def add_material(name: str, kind: str = "Steel") -> dict:
        """Add a material. kind: Steel, Concrete, Timber, or Aluminium."""
        return scia_tools.add_material(_ctx, name=name, kind=kind)

    def add_rectangular_section(name: str, material: str, width_mm: float, height_mm: float) -> dict:
        """Add a rectangular cross-section (width x height, mm)."""
        return scia_tools.add_rectangular_section(_ctx, name=name, material=material,
                                                  width_mm=width_mm, height_mm=height_mm)

    def add_node(name: str, x_mm: float, y_mm: float, z_mm: float) -> dict:
        """Add a structural point (node). X/Y horizontal, Z vertical up."""
        return scia_tools.add_node(_ctx, name=name, x_mm=x_mm, y_mm=y_mm, z_mm=z_mm)

    def add_beam(name: str, node_a: str, node_b: str, cross_section: str) -> dict:
        """Add a straight 1D beam between two existing nodes."""
        return scia_tools.add_beam(_ctx, name=name, node_a=node_a, node_b=node_b, cross_section=cross_section)

    def add_fixed_support(name: str, node: str) -> dict:
        """Add a fully-restrained (all 6 DOFs) support on a node."""
        return scia_tools.add_fixed_support(_ctx, name=name, node=node)

    def add_pinned_support(name: str, node: str) -> dict:
        """Add a pinned support — translations restrained, rotations free."""
        return scia_tools.add_pinned_support(_ctx, name=name, node=node)

    def add_roller_support(name: str, node: str, direction: str = "Z") -> dict:
        """Add a roller — rigid only in direction (X, Y, or Z), free elsewhere."""
        return scia_tools.add_roller_support(_ctx, name=name, node=node, direction=direction)

    def add_load_group(name: str, kind: str = "Permanent") -> dict:
        """Add a load group. kind: Permanent, Variable, or Accidental."""
        return scia_tools.add_load_group(_ctx, name=name, kind=kind)

    def add_load_case(name: str, group: str, kind: str = "Standard") -> dict:
        """Add a load case inside a group. kind: Standard or SelfWeight."""
        return scia_tools.add_load_case(_ctx, name=name, group=group, kind=kind)

    def add_line_load(name: str, beam: str, load_case: str,
                      magnitude_kn_per_m: float, direction: str = "Z") -> dict:
        """Add a uniform line load on a beam, in kN/m. For gravity-downward, use direction='Z' with NEGATIVE magnitude."""
        return scia_tools.add_line_load(_ctx, name=name, beam=beam, load_case=load_case,
                                        magnitude_kn_per_m=magnitude_kn_per_m, direction=direction)

    def get_node_position(name: str) -> dict:
        """Return (x, y, z) of a node in mm from the in-memory ADM model.

        Use this to verify update_node landed (or to inspect any node's
        coordinates without scrolling around in SCIA).
        """
        return scia_tools.get_node_position(_ctx, name=name)

    def refresh_from_scia() -> dict:
        """Low-level: trigger SCIA's internal engine->store refresh.

        NOTE: the OpenAPI is push-only — this does NOT read SCIA's model back
        into the in-memory ADM (verified against v24). get_node_position /
        model_summary still reflect what was pushed, not an independent re-read.
        A model the API did not build cannot be read through it."""
        return scia_tools.refresh_from_scia(_ctx)

    def model_summary() -> dict:
        """Return item count + names of everything currently in the in-memory ADM model."""
        return scia_tools.model_summary(_ctx)

    # --- Edit primitives ---
    def update_node(name: str, x_mm: float, y_mm: float, z_mm: float) -> dict:
        """Move an existing node to new coordinates. Best-effort rollback if the
        write throws; the new geometry is validated later when SCIA meshes.
        On failure the response includes `dependent_beams`: every beam that
        references this node, so you can plan a re-route or delete.
        After a successful call, just call run_analysis — the mesh re-generates
        automatically."""
        return scia_tools.update_node(_ctx, name=name, x_mm=x_mm, y_mm=y_mm, z_mm=z_mm)

    def delete_object(name: str, cascade: bool = False) -> dict:
        """Delete an ADM object. Refuses if dependents exist (returns blockers).
        Pass cascade=True to delete dependents recursively."""
        return scia_tools.delete_object(_ctx, name=name, cascade=cascade)

    def set_line_load_magnitude(name: str, magnitude_kn_per_m: float) -> dict:
        """Adjust the magnitude of an existing line load (kN/m)."""
        return scia_tools.set_line_load_magnitude(_ctx, name=name, magnitude_kn_per_m=magnitude_kn_per_m)

    def set_member_cross_section(name: str, cross_section: str) -> dict:
        """Swap the cross-section of an existing curve member to one already in the model."""
        return scia_tools.set_member_cross_section(_ctx, name=name, cross_section=cross_section)

    def set_member_endpoints(name: str, node_a: str, node_b: str) -> dict:
        """Re-route a beam between two existing nodes. Try-or-rollback."""
        return scia_tools.set_member_endpoints(_ctx, name=name, node_a=node_a, node_b=node_b)

    # --- SCIA-drive ---
    def launch_scia(visible: bool = False) -> dict:
        """Launch SCIA Engineer. visible=false (default) runs it minimized without stealing focus."""
        return scia_tools.launch_scia(_ctx, visible=visible)

    def push_model() -> dict:
        """Open an empty SCIA project and push the current in-memory ADM model.
        After edits, do NOT call this again — the edit tools auto-propagate; just call run_analysis."""
        return scia_tools.push_model(_ctx)

    def run_analysis() -> dict:
        """Generate FE mesh (only when needed) and run linear analysis."""
        return scia_tools.run_analysis(_ctx)

    # --- Results ---
    def get_beam_inner_forces(beam: str, load_case: str) -> dict:
        """Read beam internal forces (N, Vy, Vz, Mx, My, Mz) per section for a load case.

        Forces in N, moments in N·m. Returns positions in metres + per-magnitude lists +
        min/max/abs_max summary.
        """
        return scia_tools.get_beam_inner_forces(_ctx, beam=beam, load_case=load_case)

    def get_beam_deflection(beam: str, load_case: str) -> dict:
        """Read beam deformation (Ux, Uy, Uz, Fix, Fiy, Fiz) per section for a load case.

        Translations in metres (multiply by 1000 for mm), rotations in radians.
        """
        return scia_tools.get_beam_deflection(_ctx, beam=beam, load_case=load_case)

    # --- Beddings on a saved model ---
    def bedding_preview(
        model_path: str,
        layers: list[dict],
        piles: str = "*",
        diameter_m: float | None = None,
        variant: str | None = None,
        start_level_nap: float | None = None,
        datum_offset: float = 0.0,
        alpha: float | None = None,
        groepsfactor: float | None = None,
        replace: bool = False,
        allow_existing: bool = False,
    ) -> dict:
        """Work out which springs a soil-layer table puts on the piles of a saved .esa. Changes nothing.

        Always call this first and show the engineer the table. Each layer is
        {top, bottom, value, unit, description} with top and bottom in m NAP, listed top down.
        unit is exactly one of: kh_kN_m3 (needs diameter_m), E_menard_MPa (needs diameter_m, alpha,
        groepsfactor) or k_line_kN_m2. piles is a comma-separated list of names and globs.
        datum_offset is the model z of NAP 0. replace and allow_existing mean the same as in bedding_apply."""
        return bedding_tools.preview_bedding(
            Path(model_path),
            layers,
            bedding_tools.PlanRequest(
                piles=piles, diameter_m=diameter_m, variant=variant, start_level_nap=start_level_nap,
                datum_offset=datum_offset, alpha=alpha, groepsfactor=groepsfactor,
                replace=replace, allow_existing=allow_existing,
            ),
        )

    def bedding_apply(
        model_path: str,
        layers: list[dict],
        piles: str = "*",
        diameter_m: float | None = None,
        variant: str | None = None,
        start_level_nap: float | None = None,
        datum_offset: float = 0.0,
        alpha: float | None = None,
        groepsfactor: float | None = None,
        replace: bool = False,
        allow_existing: bool = False,
        out_dir: str | None = None,
    ) -> dict:
        """Write the springs from bedding_preview to a NEW .esa next to the model and read them back.

        The original is never changed. SCIA must be closed. Same units as bedding_preview.
        Trust only `ok`, `mismatches` and `other_changes` in the result; report `output` to the engineer.
        replace overwrites supports this tool wrote earlier; allow_existing adds to piles that
        already carry other line supports, which adds up their stiffness."""
        return bedding_tools.apply_bedding(
            Path(model_path),
            layers,
            bedding_tools.PlanRequest(
                piles=piles, diameter_m=diameter_m, variant=variant, start_level_nap=start_level_nap,
                datum_offset=datum_offset, alpha=alpha, groepsfactor=groepsfactor,
                replace=replace, allow_existing=allow_existing,
            ),
            out_dir=Path(out_dir) if out_dir else None,
        )

    # --- Moving load positions on a saved model ---
    def moving_load_preview(
        model_path: str,
        path_mm: list[list[float]],
        step_mm: float,
        vehicle_preset: str | None = None,
        axles: list[dict] | None = None,
        track_mm: float | None = None,
        alpha: float = 1.0,
        deck: str = "plate",
        member: str | None = None,
        name: str = "TS1",
        lateral_offset_mm: float = 0.0,
        last_step: str = "exact",
        replace: bool = False,
        allow_many: bool = False,
        deck_outline_mm: list[list[float]] | None = None,
        deck_z_mm: float = 0.0,
    ) -> dict:
        """Work out the load cases a vehicle driving along a road puts on a saved .esa. Changes nothing.

        Always call this first and show the engineer the table. One load case per position of the
        front axle, in one exclusive variable load group "LLSM-ML <name>". path_mm is the road axis in
        plan, [[x, y], ...] in mm; the wheels sit square to it, so a skewed deck is just a skewed path.
        step_mm is how far the front axle moves per case. Vehicle: vehicle_preset LM1-TS1/LM1-TS2/LM1-TS3
        (EN 1991-2 tandem, 2 axles 1200 mm apart, track 2000 mm; alpha multiplies the axle load), or
        axles [{load_kn, offset_mm}] with offset behind the front axle (positive kN act downward) and
        optionally track_mm. deck "plate" puts a free point load per wheel on the 2D members; "beam"
        puts one load per axle on member. lateral_offset_mm moves the vehicle left (+) of the path.
        last_step "short" adds a final position at the path end when the step does not fit."""
        return moving_load_tools.preview_moving_load(
            Path(model_path),
            moving_load_tools.MovingLoadRequest(
                path_mm=path_mm, step_mm=step_mm, vehicle_preset=vehicle_preset, axles=axles, track_mm=track_mm,
                alpha=alpha, deck=deck, member=member, name=name, lateral_offset_mm=lateral_offset_mm,
                last_step=last_step, replace=replace, allow_many=allow_many,
                deck_outline_mm=deck_outline_mm, deck_z_mm=deck_z_mm,
            ),
        )

    def moving_load_apply(
        model_path: str,
        path_mm: list[list[float]],
        step_mm: float,
        vehicle_preset: str | None = None,
        axles: list[dict] | None = None,
        track_mm: float | None = None,
        alpha: float = 1.0,
        deck: str = "plate",
        member: str | None = None,
        name: str = "TS1",
        lateral_offset_mm: float = 0.0,
        last_step: str = "exact",
        replace: bool = False,
        allow_many: bool = False,
        deck_outline_mm: list[list[float]] | None = None,
        deck_z_mm: float = 0.0,
        out_dir: str | None = None,
    ) -> dict:
        """Write the load cases from moving_load_preview to a NEW .esa next to the model and read them back.

        The original is never changed. SCIA must be closed. Same arguments as moving_load_preview.
        Trust only `ok`, `mismatches` and `other_changes` in the result; report `output` to the engineer.
        replace overwrites what this tool wrote earlier under the same name, nothing else."""
        return moving_load_tools.apply_moving_load(
            Path(model_path),
            moving_load_tools.MovingLoadRequest(
                path_mm=path_mm, step_mm=step_mm, vehicle_preset=vehicle_preset, axles=axles, track_mm=track_mm,
                alpha=alpha, deck=deck, member=member, name=name, lateral_offset_mm=lateral_offset_mm,
                last_step=last_step, replace=replace, allow_many=allow_many,
                deck_outline_mm=deck_outline_mm, deck_z_mm=deck_z_mm,
            ),
            out_dir=Path(out_dir) if out_dir else None,
        )

    # --- Results report of a saved model ---
    def report_preview(model_path: str, report_name: str | None = None, formats: list[str] | None = None,
                       calculate: bool = False, cases_group: str | None = None) -> dict:
        """What report_write would print from a saved .esa, from the file alone. Starts no SCIA, changes nothing.

        Always call this first. Lists the engineering reports stored in the model, whether results
        are stored, the planned output files and the time it takes (several minutes per format).
        Whether the report's results items really print results is only known after report_write."""
        return report_tools.preview_report(
            Path(model_path), report_tools.ReportRequest(report_name, formats or ["pdf"], calculate, cases_group)
        )

    def report_write(model_path: str, report_name: str | None = None, formats: list[str] | None = None,
                     calculate: bool = False, cases_group: str | None = None, out_dir: str | None = None) -> dict:
        """Start printing the engineering report stored in a saved .esa (SCIA's own report), checked for results.

        Returns at once with a `job`: the print takes 5-15 minutes. Then call report_status(job) until
        its status is "done" (each call waits up to 4 minutes); tell the engineer it is printing meanwhile.
        SCIA must be closed. Prints on a copy: the model is never changed. formats: pdf (default), xlsx,
        rtf; the XLSX is always made, because the check reads it. report_name: needed when the model has
        several reports. calculate: a linear calculation (LIN) of a copy first, for a model without
        (fresh) results. cases_group: a chapter per load case of that group (LLSM's moving-load groups,
        "LLSM-ML <name>"); needs calculate; its result classes exist on the copy only. Problems found
        from the file alone come back at once, without a job."""
        path = Path(model_path)
        request = report_tools.ReportRequest(report_name, formats or ["pdf"], calculate, cases_group)
        preview = report_tools.preview_report(path, request)
        if not preview["ok"]:
            return preview
        try:
            job = report_tools.jobs.start(str(path), lambda progress: report_tools.write_report(
                path, request, out_dir=Path(out_dir) if out_dir else None, progress=progress))
        except RuntimeError as exc:
            return {**preview, "ok": False, "problems": [str(exc)]}
        return {**job.status(), "report": preview["report"], "formats": preview["formats"],
                "minutes_estimated": preview.get("minutes_estimated"), "outputs_planned": preview.get("outputs_planned")}

    def report_status(job: str, wait_s: float = 240) -> dict:
        """Wait (at most 4 minutes) for a print started by report_write, and return where it is.

        status "running": call again. status "done": the full result. Trust only `ok`, `chapters`
        (results tables written / expected / empty / old type, and classes without combinations) and
        `problems`; report `outputs` to the engineer. A report made before SCIA 17 prints no results:
        then `ok` is false and the problems say what to do."""
        return report_tools.jobs.wait(job, wait_s)

    for _fn in (
        bedding_preview, bedding_apply,
        moving_load_preview, moving_load_apply,
        report_preview, report_write, report_status,
        new_model, add_material, add_rectangular_section, add_node, add_beam,
        add_fixed_support, add_pinned_support, add_roller_support,
        add_load_group, add_load_case, add_line_load,
        get_node_position, refresh_from_scia, model_summary,
        update_node, delete_object, set_line_load_magnitude,
        set_member_cross_section, set_member_endpoints,
        launch_scia, push_model, run_analysis,
        get_beam_inner_forces, get_beam_deflection,
    ):
        _async_tool(mcp, _fn)


def _register_stdio_only_tools(mcp: FastMCP) -> None:
    """Tools that exist ONLY in the legacy stdio transport.

    close_scia stays here because the original `.mcp.json` flow has the LLM
    own SCIA's lifecycle. In HTTP mode, the panel owns it instead, so this
    tool is deliberately NOT registered.
    """

    def close_scia() -> dict:
        """Close SCIA and clean up. Call at end of workflow or on fatal error."""
        return scia_tools.close_scia(_ctx)
    _async_tool(mcp, close_scia)


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
    async def admin_shutdown(_request: Request) -> Response:
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
        "token": token,
        "shutdown_token": token,  # older name; drop after one release
        "cmdline_marker": "llsm.mcp_server",
        "started_at": time.time(),
    })
    _atomic_write(pid_file, pid_payload)
    _atomic_write(port_file, str(port))

    _install_signal_handlers()

    print(f"[mcp] http transport on http://127.0.0.1:{port}/mcp", file=sys.stderr)
    try:
        app = mcp.streamable_http_app()
        app.add_middleware(GuardMiddleware, token=token, port=port)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info"))
        anyio.run(server.serve)
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
