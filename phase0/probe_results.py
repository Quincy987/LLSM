"""Probe the RAPI surface and figure out the right ResultKey shape to get data.

Strategy:
  1. Build a 3m simply-supported beam, push, mesh, calc.
  2. Use .NET reflection on `rapi` to dump every public method/property — see
     what discovery / enumeration methods exist.
  3. Try several ResultKey combinations (varying CaseId, EntityName, ResultType,
     Dimension, CoordSystem) and report which combos return non-None LoadResult.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from llsm.scia.loader import load as load_scia
from llsm.scia.session import SciaSession
from llsm.scia.builder import ModelBuilder

load_scia()

# Build the simplest possible model
b = ModelBuilder(name="probe_model")
b.add_material("S235", kind="Steel")
b.add_rectangular_section("rect", "S235", 200, 200)
b.add_node("n1", 0, 0, 0)
b.add_node("n2", 3000, 0, 0)
b.add_beam("beam_1", "n1", "n2", "rect")
b.add_pinned_support("sup1", "n1")
b.add_roller_support("sup2", "n2", direction="Z")
b.add_load_group("lg", kind="Permanent")
b.add_load_case("perm", "lg", kind="Standard")
b.add_line_load("q", beam="beam_1", load_case="perm", magnitude_kn_per_m=-10, direction="Z")
b.validate()
print(f"[model] {b.item_count()} items")

import SCIA.OpenAPI.Utils as SciaUtils
import SCIA.OpenAPI.Results as SciaResults
from SCIA.OpenAPI import ApiGuid as SciaApiGuid
from SciaTools.Kernel.ModelExchangerExtension.Models.Exchange import AdmChangeStatus
import Results64Enums as RE
import clr, System
import System.Reflection as R

with SciaSession(visible=False) as sess:
    def push_calc():
        tp = SciaUtils.SciaFileGetter().PrepareBasicEmptyFile(str(sess.temp_folder))
        proj = sess.env.OpenProject(str(tp))
        sess.set_project(proj)
        for elem in b.model:
            r = proj.Model.CreateAdmObject(elem)
            if r.PartialAddResult.Status != AdmChangeStatus.Ok:
                print(f"push err {elem.Name}: {list(r.PartialAddResult.Errors)}")
        proj.Model.RefreshModel_ToSCIAEngineer()
        proj.CreateMesh()
        proj.RunCalculation()
        import time; time.sleep(3)
        return proj.Model.InitializeResultsAPI()

    rapi = sess.submit(push_calc)
    if rapi is None:
        print("RAPI init returned None — abort")
        sys.exit(1)
    print(f"\n[rapi] {type(rapi).__name__}")

    # Reflect all methods on RAPI
    def dump(obj, label):
        t = clr.GetClrType(obj.GetType()) if hasattr(obj,'GetType') else clr.GetClrType(type(obj))
        flags = R.BindingFlags.Public | R.BindingFlags.Instance | R.BindingFlags.DeclaredOnly
        print(f"\n=== {label} ({t.FullName}) ===")
        for m in t.GetMethods(flags):
            params = ", ".join(f"{p.ParameterType.Name}" for p in m.GetParameters())
            print(f"  {m.ReturnType.Name} {m.Name}({params})")
        for p in t.GetProperties(flags):
            print(f"  prop {p.PropertyType.Name} {p.Name}")
    dump(rapi, "rapi")

    # Brute-force: try the standard ResultKey patterns from the notebook
    def get_id(name):
        for o in b.model:
            if o.Name == name:
                return SciaApiGuid(o.Id)
        return None

    def try_key(label, **kw):
        rk = SciaResults.ResultKey()
        rk.CaseType = RE.eDsElementType.eDsElementType_LoadCase
        rk.CaseId = kw["case_id"]
        rk.EntityType = kw.get("entity_type", RE.eDsElementType.eDsElementType_Beam)
        rk.EntityName = kw["entity_name"]
        rk.Dimension = kw.get("dim", RE.eDimension.eDim_1D)
        rk.ResultType = kw["result_type"]
        rk.CoordSystem = kw.get("coord", RE.eCoordSystem.eCoordSys_Local)
        try:
            r = rapi.LoadResult(rk)
            ok = r is not None
            extra = ""
            if ok:
                try:
                    sc = r.GetSectionCount()
                    mc = r.GetMagnitudesCount()
                    extra = f", sections={sc}, magnitudes={mc}"
                except Exception as e:
                    extra = f", (probe err: {e})"
            print(f"  [{label}] {'OK' if ok else 'NULL'}{extra}")
        except Exception as e:
            print(f"  [{label}] EXC: {type(e).__name__}: {e}")

    print("\n=== Trying ResultKey variations on perm load case, beam_1 ===")
    case_id_perm = get_id("perm")
    for rt_name in ["eFemBeamInnerForces", "eFemBeamDeformation",
                    "eFemMemberInnerForces", "eFemMemberDeformation",
                    "eFemDispl", "eFemReactions"]:
        rt = getattr(RE.eResultType, rt_name, None)
        if rt is None:
            print(f"  [{rt_name}] not in enum")
            continue
        try_key(f"{rt_name} entity=beam_1", case_id=case_id_perm, entity_name="beam_1", result_type=rt)

    # Try with empty entity name (means "all")
    rt = RE.eResultType.eFemBeamInnerForces
    try_key("InnerForces entity=''", case_id=case_id_perm, entity_name="", result_type=rt)

    # Dump all eResultType values for reference
    print("\n=== eResultType enum ===")
    rtt = clr.GetClrType(RE.eResultType)
    for n in System.Enum.GetNames(rtt):
        print(f"  {n}")
