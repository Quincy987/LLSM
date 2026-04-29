import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from llsm.scia.loader import load as load_scia
from llsm.scia import tools
from llsm.scia.tools import LlsmContext

load_scia()

# Probe: try LoadCaseType=Static with LoadGroupType=Variable (matches the working C# example)
import ModelExchanger.AnalysisDataModel as adm
print("\nLoadCaseType enum values:")
import clr, System
for n in System.Enum.GetNames(clr.GetClrType(adm.Enums.LoadCaseType)):
    print(f"  {n}")
print("\nLoadGroupType enum values:")
for n in System.Enum.GetNames(clr.GetClrType(adm.Enums.LoadGroupType)):
    print(f"  {n}")
print("\nActionType enum values:")
for n in System.Enum.GetNames(clr.GetClrType(adm.Enums.ActionType)):
    print(f"  {n}")

ctx = LlsmContext()
try:
    tools.new_model(ctx, "static_test")
    tools.add_material(ctx, "S235", "Steel")
    tools.add_rectangular_section(ctx, "rect", "S235", 200, 200)
    tools.add_node(ctx, "n1", 0, 0, 0)
    tools.add_node(ctx, "n2", 3000, 0, 0)
    tools.add_beam(ctx, "beam_1", "n1", "n2", "rect")
    tools.add_pinned_support(ctx, "sup1", "n1")
    tools.add_roller_support(ctx, "sup2", "n2", "Z")
    # Use Variable group and Static case kind, matching the working C# example
    tools.add_load_group(ctx, "lg", "Variable")
    tools.add_load_case(ctx, "lc1", "lg", "Static")
    tools.add_line_load(ctx, "q", "beam_1", "lc1", -10.0, "Z")

    tools.launch_scia(ctx, visible=False)
    r = tools.push_model(ctx); print(f"push: {r}")
    if not r["ok"]: sys.exit(1)
    r = tools.run_analysis(ctx); print(f"calc: {r}")
    if not r["ok"]: sys.exit(1)

    r = tools.get_beam_inner_forces(ctx, "beam_1", "lc1")
    if r["ok"]:
        print(f"\n*** SUCCESS *** sections={r['sections']} magnitudes={list(r['magnitudes'].keys())}")
        for n, s in r["summary_per_magnitude"].items():
            print(f"  {n}: min={s['min']:.6g} max={s['max']:.6g} abs_max={s['abs_max']:.6g}")
    else:
        print(f"FAIL: {r['error']}")

    r = tools.get_beam_deflection(ctx, "beam_1", "lc1")
    if r["ok"]:
        print(f"\n*** DEFLECTION SUCCESS *** sections={r['sections']} magnitudes={list(r['magnitudes'].keys())}")
        for n, s in r["summary_per_magnitude"].items():
            print(f"  {n}: min={s['min']:.6g} max={s['max']:.6g} abs_max={s['abs_max']:.6g}")
    else:
        print(f"deflection FAIL: {r['error']}")
finally:
    tools.close_scia(ctx)
