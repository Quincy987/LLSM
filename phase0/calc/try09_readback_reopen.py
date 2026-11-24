"""Try 9: after SCIA's Calculate, where are the results and how does the results API reach them?

(a) list the project's temp folder (and its .rapi folder) before and after the calculation
(b) RefreshModel_FromSCIAEngineer, then InitializeResultsAPI
(c) IEnvESAProject.SynchronizeDS, then InitializeResultsAPI
(d) save the calculated project with IEnvESAProject.SaveToFile, open that file again in the same SCIA,
    then InitializeResultsAPI + LoadResult (results stored in the file come back with the project)
"""
import os
import time
import traceback
from pathlib import Path

from common import WORK, build_variant, launch, log, run, t, temp_folder
from try08_readback import gui_calculate

CASE, BEAM = "Verkeer-UDL", "Veld1"


def listing(folder: Path) -> dict:
    out = {}
    for d, _, files in os.walk(folder):
        for f in files:
            p = os.path.join(d, f)
            try:
                out[os.path.relpath(p, folder)] = os.path.getsize(p)
            except OSError:
                pass
    return out


def body(ctx, result):
    build_variant(ctx, "base_static")
    launch(ctx)
    log("push", t.push_model(ctx))
    sess = ctx.session

    import clr
    import System
    from System.Reflection import BindingFlags as BF
    fl = BF.Public | BF.NonPublic | BF.Instance

    def env_project():
        import EnvESA80
        proj = sess.project
        sw = clr.GetClrType(type(proj)).GetField("ServiceWrapper", fl).GetValue(proj)
        env = sw.GetType().GetField("MyEnvEsa", fl).GetValue(sw)
        app = EnvESA80.IEnvESAApp(EnvESA80.IEnvESA(env).ESAApp)
        return app, EnvESA80.IEnvESAProject(app.get_ProjectByID(app.ActiveProjectID))

    def model_dir():
        app, p = sess.submit(env_project)
        s = System.String("")
        init = str(sess.submit(lambda: p.GetResultsAPIInitString(s)))
        import re
        m = re.search(r"<TempDir>([^<]+)</TempDir>", init)
        return Path(m.group(1).replace(".2e", ".")) if m else None, init

    log("mesh", sess.submit(lambda: sess.project.CreateMesh(), timeout_s=300))
    folder, init = model_dir()
    result["model_dir"] = str(folder)
    before = listing(folder) if folder and folder.exists() else {}
    result["calc"] = gui_calculate(sess)
    log("calculate", result["calc"])
    after = listing(folder) if folder and folder.exists() else {}
    result["model_dir_before"] = before
    result["model_dir_after"] = after
    log("model dir", folder, "files before", len(before), "after", len(after))
    log("new/changed in model dir:", {k: v for k, v in after.items() if before.get(k) != v})
    result["temp_root_dirs"] = sorted(p.name for p in temp_folder().iterdir() if p.is_dir())

    def key(rapi_case=CASE):
        import Results64Enums as RE
        import SCIA.OpenAPI.Results as R
        from SCIA.OpenAPI import ApiGuid
        k = R.ResultKey()
        k.CaseType = RE.eDsElementType.eDsElementType_LoadCase
        k.CaseId = ApiGuid(ctx.builder.get(rapi_case).Id)
        k.EntityType = RE.eDsElementType.eDsElementType_Beam
        k.EntityName = BEAM
        k.Dimension = RE.eDimension.eDim_1D
        k.ResultType = RE.eResultType.eFemBeamInnerForces
        k.CoordSystem = RE.eCoordSystem.eCoordSys_Local
        return k

    def read_with(api):
        if api is None:
            return "no results API"
        res = api.LoadResult(key())
        if res is None:
            return "LoadResult None"
        n = res.GetSectionCount()
        return {str(res.GetMagnitudeName(m)): [round(res.GetValue(m, i) / 1000, 2) for i in range(n)] for m in range(res.GetMagnitudesCount())}

    reads = result.setdefault("reads", {})

    def attempt(label, fn, bypass=False):
        try:
            if bypass:  # a dialog is expected and answered by convert_hand: run without submit's dialog guard
                import queue as _q
                reply = _q.Queue()
                sess._call_q.put((fn, reply))
                status, v = reply.get(timeout=240)
                if status == "err":
                    raise v
                sess._clicker.pending = None
            else:
                v = sess.submit(fn, timeout_s=240)
        except Exception as e:  # noqa: BLE001
            v = f"EXC {type(e).__name__}: {e} {traceback.format_exc()[-300:]}"
        reads[label] = v if isinstance(v, (str, dict, list, type(None), bool)) else str(v)
        log(label, "->", str(v)[:700])
        return v

    attempt("b refresh from SCIA, init, read", lambda: (sess.project.Model.RefreshModel_FromSCIAEngineer(), read_with(sess.project.Model.InitializeResultsAPI()))[1])

    def sync_then_read():
        import EnvESA80
        app, p = env_project()
        names = [str(n) for n in System.Enum.GetNames(clr.GetClrType(EnvESA80.TEnvESA_SynchronizeType))]
        dirs = [str(n) for n in System.Enum.GetNames(clr.GetClrType(EnvESA80.TEnvESA_SynchronizeDirection))]
        done = []
        for n in names:
            for d in dirs:
                try:
                    p.SynchronizeDS(getattr(EnvESA80.TEnvESA_SynchronizeType, n), getattr(EnvESA80.TEnvESA_SynchronizeDirection, d))
                    done.append(f"{n}/{d}")
                except Exception as e:  # noqa: BLE001
                    done.append(f"{n}/{d}: {type(e).__name__}")
        return {"synced": done, "read": read_with(sess.project.Model.InitializeResultsAPI())}

    attempt("c SynchronizeDS, init, read", sync_then_read)

    saved = WORK / "try09_calculated.esa"
    if saved.exists():
        saved.unlink()

    def save_to_file():
        app, p = env_project()
        return bool(p.SaveToFile(str(saved)))

    import threading
    stop = threading.Event()

    def convert_hand():
        """Answer SCIA's 'Gegevens converteren?' with Ja (only that question, only in our SCIA)."""
        import win32gui
        from llsm.scia.autoclick import visible_windows
        from llsm.scia.session import our_scia_processes
        done = set()
        while not stop.is_set():
            pids = {p.pid for p in our_scia_processes()}
            for w in visible_windows(pids) if pids else []:
                if w.hwnd in done or w.cls != "#32770":
                    continue
                if any("converteren" in x.lower() for x in w.texts):
                    yes = next((h for h, label in w.buttons if label.replace("&", "").strip().lower() == "ja"), None)
                    if yes:
                        win32gui.PostMessage(yes, 0x00F5, 0, 0)
                        done.add(w.hwnd)
                        reads.setdefault("answered", []).append(list(w.texts))
                        log("answered 'Gegevens converteren?' with Ja")
            time.sleep(0.3)

    threading.Thread(target=convert_hand, daemon=True).start()
    attempt("d SaveToFile", save_to_file, bypass=True)
    reads["d saved size"] = saved.stat().st_size if saved.exists() else None
    log("saved file", saved, reads["d saved size"])
    if not saved.exists():
        return

    def reopen_and_read():
        from SCIA.OpenAPI import SaveMode
        old = sess.project
        try:
            old.CloseProject(SaveMode.SaveChangesNo)
        except Exception as e:  # noqa: BLE001
            log("close old project failed", e)
        proj = sess.env.OpenProject(str(saved))
        sess.set_project(proj)
        time.sleep(3)
        return read_with(proj.Model.InitializeResultsAPI())

    attempt("d reopen saved file, init, read", reopen_and_read, bypass=True)
    stop.set()


if __name__ == "__main__":
    run("try09_readback_reopen", body)
