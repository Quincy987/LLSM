"""LLSM MCP server — exposes the SCIA tool surface to any MCP client.

Claude Code auto-detects this via `.mcp.json` in the repo root. On first
tool invocation, SCIA gets launched lazily; it stays alive for subsequent
tool calls in the same conversation. When Claude Code exits, the server
process dies and SCIA is cleaned up via process-kill at shutdown.

Run standalone (for debugging):
    python -m llsm.mcp_server

Run via Claude Code:
    claude             # auto-uses .mcp.json
    > build a 6m portal frame...
"""
from __future__ import annotations
import atexit
import sys

from mcp.server.fastmcp import FastMCP

from .scia.loader import load as load_scia
from .scia import tools as scia_tools
from .scia.tools import LlsmContext


load_scia()

mcp = FastMCP(
    "llsm",
    instructions=(
        "LLSM drives SCIA Engineer to build structural models from natural language.\n\n"
        "Strict workflow:\n"
        "  1. new_model(name=...)\n"
        "  2. add_material -> add_rectangular_section -> add_node(s) -> add_beam(s)\n"
        "     -> add_*_support -> add_load_group -> add_load_case -> add_line_load\n"
        "  3. launch_scia -> push_model -> run_analysis\n"
        "  4. close_scia at the end (or on error)\n\n"
        "Units: lengths in mm; line loads in kN/m. Z axis is vertical (up positive); "
        "for gravity-like downward loads use direction='Z' with a NEGATIVE magnitude."
    ),
)

_ctx = LlsmContext()
atexit.register(lambda: _ctx.close())


# --- Model-building tools ---

@mcp.tool()
def new_model(name: str = "LLSM_Model") -> dict:
    """Start a fresh in-memory ADM model. Call this FIRST before adding anything."""
    return scia_tools.new_model(_ctx, name=name)


@mcp.tool()
def add_material(name: str, kind: str = "Steel") -> dict:
    """Add a material. kind: Steel, Concrete, Timber, or Aluminium. Use grade names like S235, C30/37."""
    return scia_tools.add_material(_ctx, name=name, kind=kind)


@mcp.tool()
def add_rectangular_section(name: str, material: str, width_mm: float, height_mm: float) -> dict:
    """Add a rectangular cross-section (width x height, mm). Use for columns/beams when real IPE/HEA library profiles aren't required."""
    return scia_tools.add_rectangular_section(_ctx, name=name, material=material, width_mm=width_mm, height_mm=height_mm)


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
    """Add a load group. kind: Permanent, Variable, or Accidental. Required before any load cases."""
    return scia_tools.add_load_group(_ctx, name=name, kind=kind)


@mcp.tool()
def add_load_case(name: str, group: str, kind: str = "Standard") -> dict:
    """Add a load case inside a group. kind: Standard (user loads) or SelfWeight (auto gravity of own weight)."""
    return scia_tools.add_load_case(_ctx, name=name, group=group, kind=kind)


@mcp.tool()
def add_line_load(name: str, beam: str, load_case: str, magnitude_kn_per_m: float, direction: str = "Z") -> dict:
    """Add a uniform line load on a beam, in kN/m. For gravity-downward, use direction='Z' with NEGATIVE magnitude."""
    return scia_tools.add_line_load(_ctx, name=name, beam=beam, load_case=load_case,
                                    magnitude_kn_per_m=magnitude_kn_per_m, direction=direction)


@mcp.tool()
def model_summary() -> dict:
    """Return item count + names of everything currently in the in-memory ADM model. Use to verify state before pushing."""
    return scia_tools.model_summary(_ctx)


# --- SCIA-drive tools ---

@mcp.tool()
def launch_scia(visible: bool = False) -> dict:
    """Launch SCIA Engineer. visible=false (default) runs it minimized without stealing focus."""
    return scia_tools.launch_scia(_ctx, visible=visible)


@mcp.tool()
def push_model() -> dict:
    """Open an empty SCIA project and push the current in-memory ADM model. Launches SCIA if not already running."""
    return scia_tools.push_model(_ctx)


@mcp.tool()
def run_analysis() -> dict:
    """Generate FE mesh and run linear analysis. Returns whether RAPI is ready for result queries."""
    return scia_tools.run_analysis(_ctx)


@mcp.tool()
def close_scia() -> dict:
    """Close SCIA and clean up. Call at end of workflow or on fatal error."""
    return scia_tools.close_scia(_ctx)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()


@mcp.tool()
def get_beam_inner_forces(beam: str, load_case: str) -> dict:
    """Read beam internal forces (N, Vy, Vz, Mx, My, Mz) per section for a load case.

    Forces returned in N, moments in N*m. Returns positions in metres + per-magnitude lists +
    min/max/abs_max summary. Common pitfall: if you get LoadResult returned None, check the
    beam name with model_summary first; SCIA may differ from your input.
    """
    return scia_tools.get_beam_inner_forces(_ctx, beam=beam, load_case=load_case)


@mcp.tool()
def get_beam_deflection(beam: str, load_case: str) -> dict:
    """Read beam deformation (Ux, Uy, Uz, Fix, Fiy, Fiz) per section for a load case.

    Translations in metres (multiply by 1000 for mm), rotations in radians.
    """
    return scia_tools.get_beam_deflection(_ctx, beam=beam, load_case=load_case)
