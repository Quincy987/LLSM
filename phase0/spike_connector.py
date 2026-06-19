"""Live spike: can the third-party connector SDK read a live SCIA model back?

Tests SciaTools.ThirdParty.SDK.ADM (dll_calatrava) — the connector that exposes
GetAnalysisModelFromSciaEngineer(). We try the "LaunchedManually" path:
build a RegisterCommand + config, get the wrapper, then try to read the model.

Loads STRICTLY from dll_calatrava (a different ADM copy than OpenAPI_dll) in a
fresh process, on an STA thread. Captures logs + any exception. Hard timeout
from the caller (Bash tool).

Run:  .venv/Scripts/python.exe phase0/spike_connector.py
"""
from __future__ import annotations
import sys, winreg, traceback
from pathlib import Path

with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\SCIA\ESA\24.0\Admin\Dir") as k:
    SEN = Path(winreg.QueryValueEx(k, "SYSTEM")[0])
CAL = SEN / "dll_calatrava"

# Resolve SCIA temp folder
with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\SCIA\ESA\24.0\Admin\Dir") as k:
    SCIA_TEMP = Path(winreg.QueryValueEx(k, "Temp")[0])

sys.path.insert(0, str(CAL))
import clr  # noqa
# Load the connector SDK + its ADM model copy from dll_calatrava ONLY.
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


def run() -> dict:
    out: dict = {}
    logs: list[str] = []
    from SciaTools.ThirdParty.SDK.Commands import RegisterCommand
    from SciaTools.ThirdParty.SDK.ADM import ThirdPartySdkAnalysisDataModelWrapper
    from SciaTools.ThirdParty.SDK.ADM.Models import ThirdPartySdkAdmConfiguration

    app_id = System.Guid.NewGuid()
    work = Path(SCIA_TEMP) / "llsm_connector_spike"
    work.mkdir(parents=True, exist_ok=True)

    reg = RegisterCommand(app_id, "LLSM_Spike", str(work), sys.executable)
    cfg = ThirdPartySdkAdmConfiguration(app_id)
    out["app_id"] = str(app_id)
    out["work"] = str(work)

    wrapper = None
    try:
        wrapper = ThirdPartySdkAnalysisDataModelWrapper.LaunchedManually(
            reg, str(SCIA_TEMP), cfg)
        out["LaunchedManually"] = f"ok: {type(wrapper).__name__}"
    except Exception as e:
        out["LaunchedManually_error"] = f"{type(e).__name__}: {e}"
        return out

    # Try to enable logging (best-effort)
    try:
        from SciaTools.ThirdParty.SDK.ADM.Events import OnLogEventHandler
        def _log(msg):
            logs.append(str(msg))
        wrapper.EnableLogging(OnLogEventHandler(_log))
    except Exception as e:
        out["enable_logging_error"] = f"{type(e).__name__}: {e}"

    # The crux: read the live model back.
    try:
        model = wrapper.GetAnalysisModelFromSciaEngineer()
        if model is None:
            out["GetAnalysisModel"] = "returned None"
        else:
            counts: dict = {}
            for o in model:
                t = type(o).__name__
                counts[t] = counts.get(t, 0) + 1
            out["GetAnalysisModel_counts"] = counts
    except Exception as e:
        out["GetAnalysisModel_error"] = f"{type(e).__name__}: {e}"

    out["logs_tail"] = logs[-15:]
    try:
        wrapper.Dispose()
    except Exception:
        pass
    return out


def main() -> None:
    holder: dict = {}

    def sta():
        try:
            holder["result"] = run()
        except Exception as e:
            holder["result"] = {"fatal": f"{type(e).__name__}: {e}",
                                 "tb": traceback.format_exc()}

    th = T.Thread(T.ThreadStart(sta))
    th.SetApartmentState(T.ApartmentState.STA)
    th.Start()
    th.Join()

    print("\n=== CONNECTOR SPIKE RESULT ===")
    for kk, vv in holder.get("result", {}).items():
        print(f"{kk}: {vv}")


if __name__ == "__main__":
    main()
