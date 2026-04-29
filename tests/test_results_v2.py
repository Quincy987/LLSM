"""Test variant: include self_weight case + query both."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from llsm.scia.loader import load as load_scia
from llsm.scia import tools
from llsm.scia.tools import LlsmContext


def main():
    load_scia()
    ctx = LlsmContext()
    try:
        tools.new_model(ctx, name="ss_beam")
        tools.add_material(ctx, "S235", "Steel")
        tools.add_rectangular_section(ctx, "rect", "S235", 200, 200)
        tools.add_node(ctx, "n1", 0, 0, 0)
        tools.add_node(ctx, "n2", 3000, 0, 0)
        tools.add_beam(ctx, "beam_1", "n1", "n2", "rect")
        tools.add_pinned_support(ctx, "sup1", "n1")
        tools.add_roller_support(ctx, "sup2", "n2", "Z")
        tools.add_load_group(ctx, "lg", "Permanent")
        # Both kinds:
        tools.add_load_case(ctx, "self_weight", "lg", "SelfWeight")
        tools.add_load_case(ctx, "perm", "lg", "Standard")
        tools.add_line_load(ctx, "q", "beam_1", "perm", -10.0, "Z")
        print(f"summary: {tools.model_summary(ctx)}")

        tools.launch_scia(ctx, visible=False)
        r = tools.push_model(ctx); print(f"push: {r}")
        if not r["ok"]: return
        r = tools.run_analysis(ctx); print(f"calc: {r}")
        if not r["ok"]: return

        for case in ("self_weight", "perm"):
            for kind, fn in (("deflection", tools.get_beam_deflection), ("inner_forces", tools.get_beam_inner_forces)):
                r = fn(ctx, "beam_1", case)
                if r["ok"]:
                    print(f"\nOK [{case}/{kind}]: {r['sections']} sections, magnitudes={list(r['magnitudes'].keys())}")
                    for name, s in r["summary_per_magnitude"].items():
                        print(f"  {name}: min={s['min']:.6g}, max={s['max']:.6g}, abs_max={s['abs_max']:.6g}")
                else:
                    print(f"\nFAIL [{case}/{kind}]: {r['error'][:120]}")
    finally:
        tools.close_scia(ctx)


if __name__ == "__main__":
    main()
