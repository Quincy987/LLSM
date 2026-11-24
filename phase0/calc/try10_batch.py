"""Try 10 (step 2b): the batch route.

Build + push the corrected test model (base_static), save it (SaveToFile, convert question answered),
close SCIA, ESA_XML LIN on a copy (esa_xml.run_on_copy), start SCIA again, open the calculated file
and read back: (1) results API + LoadResult with the ADM ids of the build, (2) SCIA's result-table request.
"""
import queue
import threading
import time
import traceback

from common import WORK, build_variant, launch, log, run, t

CASE, BEAM = "Verkeer-UDL", "Veld1"


def convert_hand(stop, notes):
    import win32gui
    from llsm.scia.autoclick import visible_windows
    from llsm.scia.session import our_scia_processes
    done = set()
    while not stop.is_set():
        pids = {p.pid for p in our_scia_processes()}
        for w in visible_windows(pids) if pids else []:
            if w.hwnd in done or w.cls != "#32770":
                continue
            # only these two questions, both about opening/saving LLSM's own copies
            if any("converteren" in x.lower() for x in w.texts):
                want = "ja"
            elif w.title == "Verouderde doorsnedegegevens":
                want = "ok"
            else:
                continue
            yes = next((h for h, label in w.buttons if label.replace("&", "").strip().lower() == want), None)
            if yes:
                win32gui.PostMessage(yes, 0x00F5, 0, 0)
                done.add(w.hwnd)
                notes.append(f"answered {w.title!r} with {want}")
                log(f"answered {w.title!r} {list(w.texts)[:1]} with {want}")
        time.sleep(0.3)


def bypass(sess, fn, timeout=240):
    reply = queue.Queue()
    sess._call_q.put((fn, reply))
    status, v = reply.get(timeout=timeout)
    sess._clicker.pending = None
    if status == "err":
        raise v
    return v


def env_project(sess):
    import clr
    import EnvESA80
    from System.Reflection import BindingFlags as BF
    fl = BF.Public | BF.NonPublic | BF.Instance
    proj = sess.project
    sw = clr.GetClrType(type(proj)).GetField("ServiceWrapper", fl).GetValue(proj)
    env = sw.GetType().GetField("MyEnvEsa", fl).GetValue(sw)
    app = EnvESA80.IEnvESAApp(EnvESA80.IEnvESA(env).ESAApp)
    return app, EnvESA80.IEnvESAProject(app.get_ProjectByID(app.ActiveProjectID))


def body(ctx, result):
    from llsm.scia import esa_xml
    build_variant(ctx, "base_static")
    ids = {n: str(ctx.builder.get(n).Id) for n in ("EG", "Verkeer-UDL", "Veld1", "Veld2")}
    result["ids"] = ids
    launch(ctx)
    log("push", t.push_model(ctx))
    sess = ctx.session
    saved = WORK / "try10_pushed.esa"
    calc_dir = WORK / "try10_lin"
    calc_dir.mkdir(exist_ok=True)
    calc = calc_dir / "calculated.esa"
    for p in (saved, calc):
        if p.exists():
            p.unlink()
    stop, notes = threading.Event(), []
    threading.Thread(target=convert_hand, args=(stop, notes), daemon=True).start()
    ok = bypass(sess, lambda: bool(env_project(sess)[1].SaveToFile(str(saved))))
    stop.set()
    log("SaveToFile", ok, saved.stat().st_size if saved.exists() else None)
    log("close SCIA", t.close_scia(ctx))
    time.sleep(5)

    time.time()
    run_ = esa_xml.run_on_copy(saved, calc_dir, "LIN", ["-tESA", f"-o{calc}"], 600)
    result["lin"] = {"succeeded": run_.succeeded, "message": run_.message, "seconds": round(run_.seconds, 1),
                     "log": run_.log[-1500:], "output_size": calc.stat().st_size if calc.exists() else None}
    log("ESA_XML LIN", result["lin"]["succeeded"], result["lin"]["message"], result["lin"]["seconds"], "s, output", result["lin"]["output_size"])
    if not calc.exists():
        return

    from llsm.scia.tools import LlsmContext
    ctx2 = LlsmContext()
    launch(ctx2)
    sess2 = ctx2.session
    stop, notes2 = threading.Event(), []
    threading.Thread(target=convert_hand, args=(stop, notes2), daemon=True).start()

    def open_calc():
        proj = sess2.env.OpenProject(str(calc))
        sess2.set_project(proj)
        return str(proj.ProjectID) if proj is not None else None

    try:
        log("OpenProject(calculated)", bypass(sess2, open_calc, 300))
        time.sleep(3)

        def read():
            import Results64Enums as RE
            import SCIA.OpenAPI.Results as R
            import System
            from SCIA.OpenAPI import ApiGuid
            api = sess2.project.Model.InitializeResultsAPI()
            if api is None:
                return "no results API"
            out = {}
            for case in ("Verkeer-UDL", "EG"):
                for beam in ("Veld1", "Veld2"):
                    k = R.ResultKey()
                    k.CaseType = RE.eDsElementType.eDsElementType_LoadCase
                    k.CaseId = ApiGuid(System.Guid(ids[case]))
                    k.EntityType = RE.eDsElementType.eDsElementType_Beam
                    k.EntityName = beam
                    k.Dimension = RE.eDimension.eDim_1D
                    k.ResultType = RE.eResultType.eFemBeamInnerForces
                    k.CoordSystem = RE.eCoordSystem.eCoordSys_Local
                    res = api.LoadResult(k)
                    if res is None:
                        out[f"{beam}/{case}"] = None
                        continue
                    n = res.GetSectionCount()
                    out[f"{beam}/{case}"] = {str(res.GetMagnitudeName(m)): [round(res.GetValue(m, i) / 1000, 2) for i in range(n)]
                                             for m in range(res.GetMagnitudesCount())}
            return out

        try:
            result["read_api"] = bypass(sess2, read)
        except Exception as e:  # noqa: BLE001
            result["read_api"] = f"EXC {type(e).__name__}: {e} {traceback.format_exc()[-400:]}"
        log("results API on the calculated file:", str(result["read_api"])[:900])
    finally:
        stop.set()
        log("close second SCIA", t.close_scia(ctx2))


if __name__ == "__main__":
    run("try10_batch", body)
