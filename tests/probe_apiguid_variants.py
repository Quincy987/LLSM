"""Test which ApiGuid construction path actually works for ResultKey.CaseId."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from llsm.scia.loader import load as load_scia
from llsm.scia import tools
from llsm.scia.tools import LlsmContext

load_scia()

ctx = LlsmContext()
try:
    tools.new_model(ctx, "apiguid_test")
    tools.add_material(ctx, "S235", "Steel")
    tools.add_rectangular_section(ctx, "rect", "S235", 200, 200)
    tools.add_node(ctx, "n1", 0, 0, 0)
    tools.add_node(ctx, "n2", 3000, 0, 0)
    tools.add_beam(ctx, "beam_1", "n1", "n2", "rect")
    tools.add_pinned_support(ctx, "sup1", "n1")
    tools.add_roller_support(ctx, "sup2", "n2", "Z")
    tools.add_load_group(ctx, "lg", "Variable")
    tools.add_load_case(ctx, "lc1", "lg", "Static")
    tools.add_line_load(ctx, "q", "beam_1", "lc1", -10.0, "Z")
    tools.launch_scia(ctx, visible=False)
    tools.push_model(ctx)
    tools.run_analysis(ctx)

    sess = ctx.session
    builder = ctx.builder
    rapi = ctx.rapi
    case_obj = builder.get("lc1")
    case_id = case_obj.Id

    def try_variants():
        from SCIA.OpenAPI import ApiGuid
        import SCIA.OpenAPI.Results as SciaResults
        import Results64Enums as RE

        attempts = []
        def make_rk(case_id_val):
            rk = SciaResults.ResultKey()
            rk.CaseType = RE.eDsElementType.eDsElementType_LoadCase
            rk.CaseId = case_id_val
            rk.EntityType = RE.eDsElementType.eDsElementType_Beam
            rk.EntityName = "beam_1"
            rk.Dimension = RE.eDimension.eDim_1D
            rk.ResultType = RE.eResultType.eFemBeamInnerForces
            rk.CoordSystem = RE.eCoordSystem.eCoordSys_Local
            return rk

        # Variant 1: ApiGuid via op_Implicit static
        try:
            ag = ApiGuid.op_Implicit(case_id)
            r = rapi.LoadResult(make_rk(ag))
            attempts.append(f"v1 op_Implicit(Guid): {'HIT' if r else 'NULL'}")
            if r is not None:
                attempts.append(f"  sections={r.GetSectionCount()} mags={r.GetMagnitudesCount()}")
        except Exception as e:
            attempts.append(f"v1 op_Implicit: EXC {type(e).__name__}: {e}")

        # Variant 2: ApiGuid() + SetFromString
        try:
            ag = ApiGuid()
            ag.SetFromString(str(case_id))
            r = rapi.LoadResult(make_rk(ag))
            attempts.append(f"v2 SetFromString: {'HIT' if r else 'NULL'}")
            if r is not None:
                attempts.append(f"  sections={r.GetSectionCount()} mags={r.GetMagnitudesCount()}")
        except Exception as e:
            attempts.append(f"v2 SetFromString: EXC {type(e).__name__}: {e}")

        # Variant 3: ApiGuid(string)
        try:
            ag = ApiGuid(str(case_id))
            r = rapi.LoadResult(make_rk(ag))
            attempts.append(f"v3 ApiGuid(str): {'HIT' if r else 'NULL'}")
            if r is not None:
                attempts.append(f"  sections={r.GetSectionCount()} mags={r.GetMagnitudesCount()}")
        except Exception as e:
            attempts.append(f"v3 ApiGuid(str): EXC {type(e).__name__}: {e}")

        # Variant 4: ReturnString validation
        try:
            ag1 = ApiGuid(case_id)
            attempts.append(f"v4 ApiGuid(Guid).ReturnString() = {ag1.ReturnString()}")
        except Exception as e:
            attempts.append(f"v4 ReturnString: EXC {e}")

        return attempts

    for line in sess.submit(try_variants):
        print(line)
finally:
    tools.close_scia(ctx)
