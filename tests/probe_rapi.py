"""Probe v2: build valid model, push, calc, then reflect RAPI."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from llsm.scia.loader import load as load_scia
from llsm.scia import tools
from llsm.scia.tools import LlsmContext

load_scia()
ctx = LlsmContext()
try:
    tools.new_model(ctx, "probe")
    tools.add_material(ctx, "S235", "Steel")
    tools.add_rectangular_section(ctx, "rect", "S235", 200, 200)
    tools.add_node(ctx, "n1", 0, 0, 0)
    tools.add_node(ctx, "n2", 3000, 0, 0)
    tools.add_beam(ctx, "beam_1", "n1", "n2", "rect")
    tools.add_pinned_support(ctx, "sup1", "n1")
    tools.add_roller_support(ctx, "sup2", "n2", "Z")
    tools.add_load_group(ctx, "lg", "Permanent")
    tools.add_load_case(ctx, "self_weight", "lg", "SelfWeight")
    tools.add_load_case(ctx, "perm", "lg", "Standard")
    tools.add_line_load(ctx, "q", "beam_1", "perm", -10.0, "Z")  # line load on Standard
    tools.launch_scia(ctx, visible=False)
    r = tools.push_model(ctx); print(f"push: {r}")
    if not r["ok"]: sys.exit(1)
    r = tools.run_analysis(ctx); print(f"calc: {r}")
    if not r["ok"]:
        print("RAPI didn't init; can't reflect.")
        sys.exit(1)

    rapi = ctx.rapi
    print(f"\nRAPI type: {type(rapi).__name__}")

    import clr
    import System.Reflection as R
    rapi_type = clr.GetClrType(rapi.GetType())
    flags = R.BindingFlags.Public | R.BindingFlags.Instance
    print("\n=== rapi public methods ===")
    for m in rapi_type.GetMethods(flags):
        if m.DeclaringType and m.DeclaringType.Name in ("Object",): continue
        params = ", ".join(f"{p.ParameterType.Name}" for p in m.GetParameters())
        print(f"  {m.ReturnType.Name} {m.Name}({params})")
    print("\n=== rapi public properties ===")
    for p in rapi_type.GetProperties(flags):
        if p.DeclaringType and p.DeclaringType.Name in ("Object",): continue
        print(f"  {p.PropertyType.Name} {p.Name}")

    # Also peek at proj.Model
    proj = ctx.session.project
    model_type = clr.GetClrType(proj.Model.GetType())
    print("\n=== proj.Model methods ===")
    for m in model_type.GetMethods(flags):
        if m.DeclaringType and m.DeclaringType.Name in ("Object",): continue
        params = ", ".join(f"{p.ParameterType.Name}" for p in m.GetParameters())
        print(f"  {m.ReturnType.Name} {m.Name}({params})")

finally:
    tools.close_scia(ctx)
