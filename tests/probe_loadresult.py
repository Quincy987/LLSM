"""Probe LoadResult with enum brute-force + Refresh between calc and query."""
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
    rapi = ctx.rapi

    def probe():
        import System
        import clr
        from SCIA.OpenAPI import ApiGuid as SciaApiGuid
        import SCIA.OpenAPI.Results as SciaResults
        import Results64Enums as RE

        # Refresh first - maybe results need to be pulled
        try:
            proj.Model.RefreshModel_FromSCIAEngineer()
            print("  RefreshModel_FromSCIAEngineer OK")
        except Exception as e:
            print(f"  Refresh err: {e}")

        # Enumerate all eDsElementType + eResultType + eDimension values
        et_type = clr.GetClrType(RE.eDsElementType)
        rt_type = clr.GetClrType(RE.eResultType)
        dim_type = clr.GetClrType(RE.eDimension)
        cs_type = clr.GetClrType(RE.eCoordSystem)
        print("\n  eDsElementType values:", list(System.Enum.GetNames(et_type))[:40])
        print("\n  eDimension values:", list(System.Enum.GetNames(dim_type)))
        print("\n  eCoordSystem values:", list(System.Enum.GetNames(cs_type)))
        print("\n  eResultType (beam-related):", [n for n in list(System.Enum.GetNames(rt_type)) if "Beam" in n or "Member" in n or "Fem" in n][:40])

        # Brute-force ResultKey with confirmed inputs
        case_guid = SciaApiGuid(builder.get("perm").Id)
        print("\n  Brute-force LoadResult attempts (all eDsElementType x beam-y eResultType):")
        beam_et_candidates = [n for n in list(System.Enum.GetNames(et_type)) if "Beam" in n or "Member" in n or "1D" in n.lower()]
        print(f"    candidate EntityTypes: {beam_et_candidates}")
        beam_rt_candidates = [n for n in list(System.Enum.GetNames(rt_type)) if "Beam" in n or "Member" in n or "InnerForce" in n or "Deformation" in n][:10]
        print(f"    candidate ResultTypes: {beam_rt_candidates}")

        for et_name in beam_et_candidates:
            for rt_name in beam_rt_candidates:
                rk = SciaResults.ResultKey()
                rk.CaseType = RE.eDsElementType.eDsElementType_LoadCase
                rk.CaseId = case_guid
                rk.EntityType = getattr(RE.eDsElementType, et_name)
                rk.EntityName = "beam_1"
                rk.Dimension = RE.eDimension.eDim_1D
                rk.ResultType = getattr(RE.eResultType, rt_name)
                rk.CoordSystem = RE.eCoordSystem.eCoordSys_Local
                try:
                    r = rapi.LoadResult(rk)
                    if r is not None:
                        try:
                            sc = r.GetSectionCount(); mc = r.GetMagnitudesCount()
                            print(f"    HIT: et={et_name} rt={rt_name} -> sections={sc} magnitudes={mc}")
                        except Exception as e:
                            print(f"    nonNone: et={et_name} rt={rt_name} but probe err: {e}")
                except Exception as e:
                    pass

    sess.submit(probe)

finally:
    tools.close_scia(ctx)

