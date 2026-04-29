"""Direct test: build a simply-supported beam, run analysis, read results."""
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
        # 3m steel beam, simply supported, 10 kN/m gravity
        tools.new_model(ctx, name="ss_beam_test")
        tools.add_material(ctx, "S235", "Steel")
        tools.add_rectangular_section(ctx, "rect", "S235", 200, 200)
        tools.add_node(ctx, "n1", 0, 0, 0)
        tools.add_node(ctx, "n2", 3000, 0, 0)
        tools.add_beam(ctx, "beam_1", "n1", "n2", "rect")
        tools.add_pinned_support(ctx, "sup1", "n1")
        tools.add_roller_support(ctx, "sup2", "n2", "Z")
        tools.add_load_group(ctx, "lg", "Permanent")
        tools.add_load_case(ctx, "perm", "lg", "Standard")
        tools.add_line_load(ctx, "q", "beam_1", "perm", -10.0, "Z")
        print(f"summary: {tools.model_summary(ctx)}")

        print("\n--- launching SCIA ---")
        tools.launch_scia(ctx, visible=False)

        print("\n--- pushing model ---")
        r = tools.push_model(ctx)
        print(r)
        if not r["ok"]:
            print("ABORT: push failed")
            return

        print("\n--- running analysis ---")
        r = tools.run_analysis(ctx)
        print(r)
        if not r["ok"]:
            print("ABORT: analysis didn't initialize RAPI")
            return

        print("\n--- get_beam_deflection beam_1, perm ---")
        r = tools.get_beam_deflection(ctx, "beam_1", "perm")
        if r["ok"]:
            print(f"OK: {r['sections']} sections, magnitudes={list(r['magnitudes'].keys())}")
            for name, summary in r["summary_per_magnitude"].items():
                print(f"  {name}: min={summary['min']:.6g}, max={summary['max']:.6g}, abs_max={summary['abs_max']:.6g}")
        else:
            print(f"FAIL: {r}")

        print("\n--- get_beam_inner_forces beam_1, perm ---")
        r = tools.get_beam_inner_forces(ctx, "beam_1", "perm")
        if r["ok"]:
            print(f"OK: {r['sections']} sections, magnitudes={list(r['magnitudes'].keys())}")
            for name, summary in r["summary_per_magnitude"].items():
                print(f"  {name}: min={summary['min']:.6g}, max={summary['max']:.6g}")
        else:
            print(f"FAIL: {r}")

    finally:
        tools.close_scia(ctx)


if __name__ == "__main__":
    main()
