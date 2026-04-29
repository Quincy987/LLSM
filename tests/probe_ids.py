"""Try DS lookups AFTER run_analysis."""
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
    tools.add_line_load(ctx, "q", "beam_1", "perm", -10.0, "Z")
    tools.launch_scia(ctx, visible=False)
    tools.push_model(ctx)
    tools.run_analysis(ctx)

    sess = ctx.session
    proj = sess.project
    builder = ctx.builder

    def diag():
        from SCIA.OpenAPI import ApiGuid as SciaApiGuid
        out = []
        for nm in ("beam_1", "B1", "M1", "B0001", "Beam_1", "S1"):
            try:
                ds = proj.Model.FindBeamDsId(nm)
                out.append(f"FindBeamDsId({nm!r}) = {ds}")
            except Exception as e:
                out.append(f"FindBeamDsId({nm!r}) EXC: {type(e).__name__}")
        for nm in ("n1", "n2", "N1", "N2"):
            try:
                ds = proj.Model.FindNodeDsId(nm)
                out.append(f"FindNodeDsId({nm!r}) = {ds}")
            except Exception as e:
                out.append(f"FindNodeDsId({nm!r}) EXC: {type(e).__name__}")
        for nm in ("self_weight", "perm"):
            obj = builder.get(nm)
            try:
                api_guid = SciaApiGuid(obj.Id)
                ds = proj.Model.FindLoadCaseDsId(api_guid)
                out.append(f"FindLoadCaseDsId({nm}, adm_guid) = {ds}")
            except Exception as e:
                out.append(f"FindLoadCaseDsId({nm}) EXC: {type(e).__name__}: {e}")
        return out

    diag_lines = sess.submit(diag)
    print("\n=== diagnostics (after run_analysis) ===")
    for line in diag_lines:
        print(f"  {line}")

finally:
    tools.close_scia(ctx)
