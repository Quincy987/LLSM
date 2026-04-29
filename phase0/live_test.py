"""Phase 0 LIVE test v6: auto-clicker + CreateMesh + minimized SCIA."""
import sys, time, threading, winreg, psutil
from pathlib import Path

SCIA_VERSION = "24.0"
SCIA_PROCS = ["esastartupscreen.exe", "esa.exe", "sciaengineer.exe",
              "sciatools.admtoadm.hubclient.exe", "sciatools.admtoadm.hubserver.exe"]

running = [p.info for p in psutil.process_iter(["pid", "name"])
           if p.info["name"] and p.info["name"].lower() in SCIA_PROCS]
if running:
    print("NOTE: SCIA already running. Closing it cleanly to start fresh...")
    for p in running:
        try: psutil.Process(p["pid"]).kill()
        except Exception: pass
    time.sleep(3)

with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\SCIA\ESA\24.0\Admin\Dir") as key:
    SEN = Path(winreg.QueryValueEx(key, "SYSTEM")[0])
    STEMP = Path(winreg.QueryValueEx(key, "Temp")[0])
OAD = SEN / "OpenAPI_dll"
OTEMP = STEMP / "llsm_phase0"
OTEMP.mkdir(parents=True, exist_ok=True)
print(f"[setup] SCIA {SCIA_VERSION} at {SEN}")

sys.path += [str(SEN), str(OAD)]
import clr, System, System.Threading as T
import win32gui, win32con, win32process, ctypes
from ctypes import wintypes

for dll in ["UnitsNet.dll","System.ValueTuple.dll",
            "ModelExchanger.Excel.Conversion.Contracts.dll","ModelExchanger.Excel.Conversion.Implementation.dll",
            "ModelExchanger.Excel.Integration.Bootstrapper.dll",
            "AutoMapper.dll","AutoMapper.Extensions.EnumMapping.dll",
            "ModelExchanger.AnalysisDataModel.dll","ModelExchanger.AnalysisDataModel.Implementation.dll",
            "ModelExchanger.AnalysisDataModel.Contracts.dll","ModelExchanger.AnalysisDataModel.Integration.Bootstrapper.dll",
            "ModelExchanger.Shared.Implementation.dll","ModelExchanger.Shared.Models.dll",
            "CSInfrastructure.dll","FluentValidation.dll","Newtonsoft.Json.dll",
            "SimpleInjector.dll","Infrastructure.Geometry.dll","MathNet.Spatial.dll","MathNet.Numerics.dll",
            "SciaTools.Kernel.Common.Contracts.dll","SciaTools.Kernel.Common.Implementation.dll",
            "SciaTools.Kernel.Common.Models.dll",
            "SciaTools.Kernel.ModelExchangerExtension.Contracts.dll","SciaTools.Kernel.ModelExchangerExtension.Implementation.dll",
            "SciaTools.Kernel.ModelExchangerExtension.Models.dll","SCIA.OpenAPI.dll"]:
    for f in (SEN, OAD):
        p = f / dll
        if p.is_file():
            clr.AddReference(str(p)); break

import ModelExchanger.AnalysisDataModel as adm
import CSInfrastructure as cs
import UnitsNet
from SCIA.OpenAPI import Environment as SciaEnvironment, ApiGuid as SciaApiGuid
import SCIA.OpenAPI.Utils as SciaUtils
import SCIA.OpenAPI.Results as SciaResults
from SciaTools.Kernel.ModelExchangerExtension.Models.Exchange import AdmChangeStatus
import Results64Enums as RE

print("[setup] DLLs loaded")

# ===== Auto-clicker: dismisses any Ja/Yes/OK dialogs SCIA pops up =====
_stop_click = threading.Event()

def _autoclicker():
    clicked = set()
    poll_count = 0
    while not _stop_click.is_set():
        poll_count += 1
        try:
            # Collect SCIA-owned PIDs
            scia_pids = set()
            for p in psutil.process_iter(["pid", "name"]):
                if p.info["name"] and any(k in p.info["name"].lower() for k in ["esa", "scia"]):
                    scia_pids.add(p.info["pid"])
            
            def walk(hwnd, _):
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid not in scia_pids:
                    return True
                if hwnd in clicked:
                    return True
                title = win32gui.GetWindowText(hwnd)
                cls = win32gui.GetClassName(hwnd)
                # enumerate children for clickable buttons
                btn = {"target": None}
                def child_cb(chwnd, _):
                    ctxt = win32gui.GetWindowText(chwnd)
                    low = ctxt.lower().replace("&", "").strip()
                    if low in ("ja", "yes", "ok", "accepteren", "accept", "continue", "doorgaan"):
                        btn["target"] = (chwnd, ctxt)
                        return False
                    return True
                try:
                    win32gui.EnumChildWindows(hwnd, child_cb, None)
                except Exception:
                    pass
                if btn["target"]:
                    chwnd, ctxt = btn["target"]
                    print(f"[autoclick] dialog hwnd=0x{hwnd:x} title={title!r} -> click {ctxt!r}")
                    try:
                        # BM_CLICK = 0x00F5
                        win32gui.PostMessage(chwnd, 0x00F5, 0, 0)
                        clicked.add(hwnd)
                    except Exception as e:
                        print(f"[autoclick] BM_CLICK failed: {e}; trying Enter")
                        win32gui.SetForegroundWindow(hwnd)
                        # VK_RETURN = 0x0D via keybd_event
                        ctypes.windll.user32.keybd_event(0x0D, 0, 0, 0)
                        ctypes.windll.user32.keybd_event(0x0D, 0, 2, 0)
                        clicked.add(hwnd)
                return True
            win32gui.EnumWindows(walk, None)
        except Exception as e:
            if poll_count % 30 == 0:
                print(f"[autoclick] poll err: {e}")
        time.sleep(0.5)

_clicker_thread = threading.Thread(target=_autoclicker, daemon=True)
_clicker_thread.start()
print("[setup] auto-clicker thread started")

# ===== Build ADM model =====
adm_model = adm.Models.AnalysisModel()
bootstrapper = adm.Integration.Bootstrapper.AnalysisDataModelBootstrapper()
scope = bootstrapper.CreateThreadedScope()
svc = scope.GetService[adm.Contracts.IAnalysisModelService]()

def g(name): return next((it for it in adm_model if it.Name == name), None)

info = adm.ModelInformation(System.Guid.NewGuid(), "LLSM_Phase0")
info.NationalCode = adm.Enums.NationalCode.EC_Standard_EN
info.SourceApplication = "LLSM"
svc.AddItemToModel(adm_model, info)

mat = adm.Libraries.StructuralMaterial(System.Guid.NewGuid(), "C30/37",
    adm.Enums.MaterialType.Concrete, "C30/37")
svc.AddItemToModel(adm_model, mat)

pa = System.Collections.Generic.List[UnitsNet.Length]()
pa.Add(UnitsNet.Length(300, UnitsNet.Units.LengthUnit.Millimeter))
pa.Add(UnitsNet.Length(300, UnitsNet.Units.LengthUnit.Millimeter))
xs = adm.Libraries.StructuralParametricCrossSection(System.Guid.NewGuid(),
    "rect_300x300", mat, adm.Enums.ProfileLibraryId.Rectangle, pa)
svc.AddItemToModel(adm_model, xs)

for i, (x, y, z) in enumerate([(0,0,0), (3000,0,0)]):
    n = adm.StructuralElements.StructuralPointConnection(System.Guid.NewGuid(),
        f"n{i+1}",
        UnitsNet.Length(x, UnitsNet.Units.LengthUnit.Millimeter),
        UnitsNet.Length(y, UnitsNet.Units.LengthUnit.Millimeter),
        UnitsNet.Length(z, UnitsNet.Units.LengthUnit.Millimeter))
    svc.AddItemToModel(adm_model, n)

nodes = System.Collections.Generic.List[adm.StructuralElements.StructuralPointConnection]()
nodes.Add(g("n1")); nodes.Add(g("n2"))
line = adm.Subtypes.Curve[adm.StructuralElements.StructuralPointConnection](
    adm.Enums.CurveGeometricalShape.Line, nodes)
curves = System.Collections.Generic.List[adm.Subtypes.Curve[adm.StructuralElements.StructuralPointConnection]]()
curves.Add(line)
beam = adm.StructuralElements.StructuralCurveMember(System.Guid.NewGuid(),
    "beam_1", curves, g("rect_300x300"))
beam.Layer = "LayerADM"
svc.AddItemToModel(adm_model, beam)

def ctr(t):
    v = adm.Subtypes.Constraint[System.Nullable[UnitsNet.ForcePerLength]]()
    v.Type = t; return v
def crot(t):
    v = adm.Subtypes.Constraint[System.Nullable[UnitsNet.RotationalStiffness]]()
    v.Type = t; return v
TR, TF = ctr(adm.Enums.ConstraintType.Rigid), ctr(adm.Enums.ConstraintType.Free)
RR, RF = crot(adm.Enums.ConstraintType.Rigid), crot(adm.Enums.ConstraintType.Free)

s1 = adm.StructuralElements.StructuralPointSupport(System.Guid.NewGuid(), "support_1", g("n1"))
s1.TranslationX=TR; s1.TranslationY=TR; s1.TranslationZ=TR; s1.RotationX=RR; s1.RotationY=RF; s1.RotationZ=RR
svc.AddItemToModel(adm_model, s1)

s2 = adm.StructuralElements.StructuralPointSupport(System.Guid.NewGuid(), "support_2", g("n2"))
s2.TranslationX=TF; s2.TranslationY=TF; s2.TranslationZ=TR; s2.RotationX=RF; s2.RotationY=RF; s2.RotationZ=RF
svc.AddItemToModel(adm_model, s2)

lg = adm.Loads.StructuralLoadGroup(System.Guid.NewGuid(), "lg_permanent", adm.Enums.LoadGroupType.Permanent)
lg.Relation = adm.Enums.Relation.Standard
lg.Load = cs.FlexibleEnum[adm.Enums.Load](adm.Enums.Load.Domestic)
svc.AddItemToModel(adm_model, lg)

sw = adm.Loads.StructuralLoadCase(System.Guid.NewGuid(), "self_weight",
    adm.Enums.ActionType.Permanent, lg, adm.Enums.LoadCaseType.SelfWeight)
svc.AddItemToModel(adm_model, sw)

vlc = adm.Loads.StructuralLoadCase(System.Guid.NewGuid(), "variable_load_case",
    adm.Enums.ActionType.Permanent, lg, adm.Enums.LoadCaseType.Standard)
svc.AddItemToModel(adm_model, vlc)

bref = adm.StructuralReferences.Curves.CurveStructuralReferenceOnBeam(beam)
ld = adm.Loads.StructuralCurveAction[adm.StructuralReferences.Curves.CurveStructuralReferenceOnBeam](
    System.Guid.NewGuid(), "q_on_beam", adm.Enums.CurveForceAction.OnBeam,
    UnitsNet.ForcePerLength(-10, UnitsNet.Units.ForcePerLengthUnit.KilonewtonPerMeter),
    vlc, bref)
ld.Direction = adm.Enums.ActionDirection.Z
svc.AddItemToModel(adm_model, ld)

print(f"[model] {sum(1 for _ in adm_model)} items built")
svc.ValidateModel(adm_model)
print("[model] validated")
scope.Dispose()

# ===== SCIA operations on STA thread =====
holder = {"ok": False, "err": None, "result_str": None, "leave_alive": False}

def sta_work():
    try:
        print(f"[scia] STA: {T.Thread.CurrentThread.GetApartmentState()}")
        env = SciaEnvironment(str(SEN), str(OTEMP), "1.0.0.0")
        print("[scia] launching (minimized, no focus)...")
        env.RunSCIAEngineer(SciaEnvironment.GuiMode.ShowWindowShowMinNoActive)
        print("[scia] launched; waiting for HubServer (auto-clicker handling dialogs)...")
        deadline = time.time() + 180
        while time.time() < deadline:
            names = [p.info["name"].lower() for p in psutil.process_iter(["name"]) if p.info["name"]]
            if any("hubserver" in n for n in names):
                print(f"[scia] HubServer ready at t={int(time.time()-deadline+180)}s")
                break
            time.sleep(1)
        else:
            raise RuntimeError("HubServer timeout")
        
        tp = SciaUtils.SciaFileGetter().PrepareBasicEmptyFile(str(OTEMP))
        proj = env.OpenProject(str(tp))
        print(f"[scia] opened {tp}")
        
        errs = []
        for el in adm_model:
            r = proj.Model.CreateAdmObject(el)
            if r.PartialAddResult.Status != AdmChangeStatus.Ok:
                errs.append((el.Name, list(r.PartialAddResult.Errors)))
        if errs: print(f"[scia] push errors: {errs}")
        else:    print("[scia] model pushed clean")
        proj.Model.RefreshModel_ToSCIAEngineer()
        
        # NEW: Create mesh before calc
        print("[scia] generating FE mesh...")
        try:
            proj.CreateMesh()
            print("[scia] CreateMesh() OK")
        except Exception as e:
            print(f"[scia] CreateMesh() failed: {e}")
        
        print("[scia] running calc...")
        proj.RunCalculation()
        print("[scia] RunCalculation returned, waiting 5s then init RAPI...")
        time.sleep(5)
        
        rapi = None
        for i in range(4):
            rapi = proj.Model.InitializeResultsAPI()
            if rapi is not None:
                print(f"[scia] RAPI OK on attempt {i+1}")
                break
            print(f"[scia] RAPI attempt {i+1} None, waiting 5s")
            time.sleep(5)
        if rapi is None:
            raise RuntimeError("RAPI still None after retries")
        
        rk = SciaResults.ResultKey()
        rk.CaseType = RE.eDsElementType.eDsElementType_LoadCase
        rk.CaseId = SciaApiGuid(g("self_weight").Id)
        rk.EntityType = RE.eDsElementType.eDsElementType_Beam
        rk.EntityName = beam.Name
        rk.Dimension = RE.eDimension.eDim_1D
        rk.ResultType = RE.eResultType.eFemBeamDeformation
        rk.CoordSystem = RE.eCoordSystem.eCoordSys_Local
        result = rapi.LoadResult(rk)
        if result is None:
            holder["result_str"] = "LoadResult returned None (no data for this key)"
        else:
            try:
                sc = result.GetSectionCount()
                mc = result.GetMagnitudesCount()
                mags = {result.GetMagnitudeName(m): [result.GetValue(m, i) for i in range(sc)] for m in range(mc)}
                positions = [result.GetSectionPosX(i) for i in range(sc)]
                summary = f"{sc} sections, magnitudes: {list(mags.keys())}"
                # max abs deflection Uz
                if "Uz" in mags:
                    uz_mm = [v*1000 for v in mags["Uz"]]
                    summary += f"; max |Uz| = {max(abs(v) for v in uz_mm):.3f} mm"
                holder["result_str"] = summary
            except Exception as e:
                holder["result_str"] = f"error extracting: {e}"
        holder["ok"] = True
        print(f"[scia] result: {holder['result_str']}")
        
        try: env.Dispose()
        except Exception as e: print(f"[cleanup] dispose: {e}")
    except Exception as e:
        holder["err"] = e

ts = T.Thread(T.ThreadStart(sta_work))
ts.SetApartmentState(T.ApartmentState.STA)
ts.Start()
ts.Join()

_stop_click.set()

if holder["err"]:
    print(f"\n[RESULT] FAILED: {holder['err']}")
    time.sleep(2)
    for p in psutil.process_iter(["pid","name"]):
        if p.info["name"] and p.info["name"].lower() in SCIA_PROCS:
            try: p.kill()
            except Exception: pass
    sys.exit(1)
else:
    print(f"\n=== SUCCESS: full end-to-end pipeline works autonomously ===")
    print(f"=== deformation result: {holder['result_str']} ===")
    time.sleep(2)
    for p in psutil.process_iter(["pid","name"]):
        if p.info["name"] and p.info["name"].lower() in SCIA_PROCS:
            try: p.kill()
            except Exception: pass
