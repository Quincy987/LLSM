"""Boot a normal SciaEngineer.exe GUI, then try to attach the third-party
connector to it and read the live model. Single process, no OpenAPI_dll loaded
(avoids ADM type clash) — only dll_calatrava is loaded for the connector SDK.

Leaves SCIA running on exit so it can be reused / retested.
"""
from __future__ import annotations
import sys, winreg, time, subprocess, threading, traceback
from pathlib import Path

with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\SCIA\ESA\24.0\Admin\Dir") as k:
    SEN = Path(winreg.QueryValueEx(k, "SYSTEM")[0])
    SCIA_TEMP = Path(winreg.QueryValueEx(k, "Temp")[0])
CAL = SEN / "dll_calatrava"
EXE = SEN / "SciaEngineer.exe"

# --- in-process autoclicker for the license dialog ---
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from llsm.scia.autoclick import AutoClicker
ac = AutoClicker(verbose=True); ac.start()

import psutil
def scia_running() -> bool:
    return any((p.info.get("name") or "").lower() == "sciaengineer.exe"
               for p in psutil.process_iter(["name"]))

print(f"[attach] launching {EXE.name} ...")
proc = subprocess.Popen([str(EXE)], cwd=str(SEN))

# wait for the process to appear + settle (GUI load). No hub signal here, so
# use a generous settle window; the connector retries internally too.
deadline = time.time() + 150
while time.time() < deadline and not scia_running():
    time.sleep(2)
print(f"[attach] SciaEngineer.exe present={scia_running()}; settling 60s for full load")
time.sleep(60)

# --- load ONLY the connector SDK from dll_calatrava ---
sys.path.insert(0, str(CAL))
import clr  # noqa
for dll in ("UnitsNet.dll", "CSInfrastructure.dll", "Newtonsoft.Json.dll",
            "ModelExchanger.AnalysisDataModel.dll",
            "ModelExchanger.AnalysisDataModel.Implementation.dll",
            "ModelExchanger.AnalysisDataModel.Contracts.dll",
            "ModelExchanger.AnalysisDataModel.Integration.Bootstrapper.dll",
            "SciaTools.ThirdParty.SDK.dll", "SciaTools.ThirdParty.SDK.ADM.dll"):
    p = CAL / dll
    if p.is_file():
        clr.AddReference(str(p))

import System
import System.Threading as T


def attempt() -> dict:
    out: dict = {}
    from SciaTools.ThirdParty.SDK.Commands import RegisterCommand
    from SciaTools.ThirdParty.SDK.ADM import ThirdPartySdkAnalysisDataModelWrapper
    from SciaTools.ThirdParty.SDK.ADM.Models import ThirdPartySdkAdmConfiguration
    app_id = System.Guid.NewGuid()
    work = Path(SCIA_TEMP) / "llsm_connector_spike"
    work.mkdir(parents=True, exist_ok=True)
    reg = RegisterCommand(app_id, "LLSM_Spike", str(work), sys.executable)
    cfg = ThirdPartySdkAdmConfiguration(app_id)
    # retry attach a few times while SCIA finishes loading
    wrapper = None
    last = None
    for i in range(6):
        try:
            wrapper = ThirdPartySdkAnalysisDataModelWrapper.LaunchedManually(
                reg, str(SCIA_TEMP), cfg)
            out["attach"] = f"OK after {i+1} tries: {type(wrapper).__name__}"
            break
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(10)
    if wrapper is None:
        out["attach_error"] = last
        return out
    try:
        model = wrapper.GetAnalysisModelFromSciaEngineer()
        if model is None:
            out["GetAnalysisModel"] = "None"
        else:
            counts = {}
            for o in model:
                t = type(o).__name__; counts[t] = counts.get(t, 0) + 1
            out["GetAnalysisModel_counts"] = counts
    except Exception as e:
        out["GetAnalysisModel_error"] = f"{type(e).__name__}: {e}"
    try: wrapper.Dispose()
    except Exception: pass
    return out


holder: dict = {}
def sta():
    try: holder["r"] = attempt()
    except Exception as e: holder["r"] = {"fatal": f"{type(e).__name__}: {e}", "tb": traceback.format_exc()}
th = T.Thread(T.ThreadStart(sta)); th.SetApartmentState(T.ApartmentState.STA); th.Start(); th.Join()

print("\n=== ATTACH-TO-RUNNING SPIKE RESULT ===")
print(f"scia_running_at_end: {scia_running()}")
for kk, vv in holder.get("r", {}).items():
    print(f"{kk}: {vv}")
print("[attach] leaving SCIA running.")
