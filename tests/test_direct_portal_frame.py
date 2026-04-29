"""Direct-tool test: build a steel portal frame without the LLM.

Exercises the tools.py functions in the sequence an LLM would produce.
If this works, the scia layer is solid and we can plug in the LLM next.

Target: 6m x 4m steel portal frame (2 columns + rafter), S235, HEA200 columns,
IPE240 rafter, fixed column bases, wind load on left column.
Actually — simplifying: single beam for first smoke test, then scale up.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from llsm.scia.loader import load as load_scia
from llsm.scia import tools
from llsm.scia.tools import LlsmContext


def build_portal_frame(ctx: LlsmContext) -> None:
    # Library defs
    tools.new_model(ctx, name="portal_frame")
    tools.add_material(ctx, "S235", kind="Steel")
    # Using rectangular pseudo-sections for MVP (real profiles require library lookup).
    # In Phase 2 we'll add proper IPE/HEA picks via the Scia section library API.
    tools.add_rectangular_section(ctx, "HEA200_eq", material="S235", width_mm=200, height_mm=200)
    tools.add_rectangular_section(ctx, "IPE240_eq", material="S235", width_mm=120, height_mm=240)

    # Geometry: 6m wide, 4m tall portal frame in XZ plane
    tools.add_node(ctx, "base_L",  x_mm=0,    y_mm=0, z_mm=0)
    tools.add_node(ctx, "base_R",  x_mm=6000, y_mm=0, z_mm=0)
    tools.add_node(ctx, "knee_L",  x_mm=0,    y_mm=0, z_mm=4000)
    tools.add_node(ctx, "knee_R",  x_mm=6000, y_mm=0, z_mm=4000)

    # Columns
    tools.add_beam(ctx, "col_L", node_a="base_L", node_b="knee_L", cross_section="HEA200_eq")
    tools.add_beam(ctx, "col_R", node_a="base_R", node_b="knee_R", cross_section="HEA200_eq")
    # Rafter (simplified to horizontal for now)
    tools.add_beam(ctx, "rafter", node_a="knee_L", node_b="knee_R", cross_section="IPE240_eq")

    # Supports (fixed bases)
    tools.add_fixed_support(ctx, "sup_L", node="base_L")
    tools.add_fixed_support(ctx, "sup_R", node="base_R")

    # Load case + a simple gravity load on the rafter
    tools.add_load_group(ctx, "lg_perm", kind="Permanent")
    tools.add_load_case(ctx, "perm", group="lg_perm", kind="Standard")
    tools.add_line_load(ctx, "q_rafter", beam="rafter", load_case="perm",
                        magnitude_kn_per_m=-5.0, direction="Z")

    print(tools.model_summary(ctx))


def main() -> None:
    load_scia()
    ctx = LlsmContext()
    try:
        print("=== building model in memory ===")
        build_portal_frame(ctx)

        print("\n=== launching SCIA ===")
        tools.launch_scia(ctx, visible=False)

        print("\n=== pushing model ===")
        result = tools.push_model(ctx)
        print(f"push_model: {result}")
        if not result["ok"]:
            print("ABORT: model push failed")
            return

        print("\n=== running analysis ===")
        result = tools.run_analysis(ctx)
        print(f"run_analysis: {result}")

        if result["ok"]:
            print("\n=== SUCCESS ===")
        else:
            print("\n=== calc returned but RAPI init failed ===")
    finally:
        print("\n=== cleanup ===")
        tools.close_scia(ctx)


if __name__ == "__main__":
    main()
