"""Try 8: read the forces back after a real calculation (SCIA's Calculate, as in try 7).

Variant base_static (EG + Verkeer-UDL as a static variable case), which calculates without a message.
Read attempts, each logged: (1) LLSM's path as is, (2) a fresh results API after the calculation,
(3) other locations / coordinate systems, (4) the init string of the results API and what is at that path,
(5) SCIA's result-table request (EPROC_REQUEST/EPROC_OUTPUT), with its answer kept for parsing.
"""
import os
import queue
import threading
import time
import traceback
import uuid

from common import WORK, build_variant, forces, launch, log, run, t
from try07_gui_calculate import com_app, dialog_hand

CASE, BEAM = "Verkeer-UDL", "Veld1"


def gui_calculate(sess) -> dict:
    import System

    def calculate():
        out = System.String("")
        return str(com_app(sess).ExecuteCommandString("CMD.TOOLS.CALCULATION", "", out))

    stop, seen = threading.Event(), []
    threading.Thread(target=dialog_hand, args=(stop, seen), daemon=True).start()
    reply: queue.Queue = queue.Queue()
    t1 = time.time()
    sess._call_q.put((calculate, reply))
    try:
        status, payload = reply.get(timeout=300)
    except queue.Empty:
        status, payload = "timeout", None
    time.sleep(2)
    stop.set()
    sess._clicker.pending = None
    return {"status": status, "returned": str(payload), "seconds": round(time.time() - t1, 1),
            "messages": [d["qt_content"] for d in seen if "qt_content" in d]}


def body(ctx, result):
    build_variant(ctx, "base_static")
    launch(ctx)
    log("push", t.push_model(ctx))
    sess = ctx.session
    log("mesh", sess.submit(lambda: sess.project.CreateMesh(), timeout_s=300))
    result["calc"] = gui_calculate(sess)
    log("calculate", result["calc"])

    reads = result.setdefault("reads", {})

    def r(label, fn):
        try:
            v = sess.submit(fn, timeout_s=240)
        except Exception as e:  # noqa: BLE001
            v = f"EXC {type(e).__name__}: {e} {traceback.format_exc()[-400:]}"
        reads[label] = v
        log(label, "->", str(v)[:600])
        return v

    ctx.rapi = r("1 InitializeResultsAPI", lambda: sess.project.Model.InitializeResultsAPI())
    ctx.last_calc_ok = ctx.rapi is not None
    reads["1 LLSM read"] = forces(ctx, BEAM, CASE)
    log("1 LLSM read", reads["1 LLSM read"])

    import clr
    import System
    from System.Reflection import BindingFlags as BF
    fl = BF.Public | BF.NonPublic | BF.Instance

    def init_string():
        import EnvESA80
        proj = sess.project
        sw = clr.GetClrType(type(proj)).GetField("ServiceWrapper", fl).GetValue(proj)
        env = sw.GetType().GetField("MyEnvEsa", fl).GetValue(sw)
        app = EnvESA80.IEnvESAApp(EnvESA80.IEnvESA(env).ESAApp)
        p = EnvESA80.IEnvESAProject(app.get_ProjectByID(app.ActiveProjectID))
        s = System.String("")
        res = p.GetResultsAPIInitString(s)
        return {"init": str(res), "comm_root": str(p.ProjectCommRootPath)}

    info = r("4 init string", init_string)

    def key(case=CASE, beam=BEAM, location=None, coord=None):
        import Results64Enums as RE
        import SCIA.OpenAPI.Results as R
        from SCIA.OpenAPI import ApiGuid
        k = R.ResultKey()
        k.CaseType = RE.eDsElementType.eDsElementType_LoadCase
        k.CaseId = ApiGuid(ctx.builder.get(case).Id)
        k.EntityType = RE.eDsElementType.eDsElementType_Beam
        k.EntityName = beam
        k.Dimension = RE.eDimension.eDim_1D
        k.ResultType = RE.eResultType.eFemBeamInnerForces
        k.CoordSystem = coord if coord is not None else RE.eCoordSystem.eCoordSys_Local
        if location is not None:
            k.Location = location
        return k

    def summ(res):
        if res is None:
            return None
        n = res.GetSectionCount()
        return {str(res.GetMagnitudeName(m)): [round(res.GetValue(m, i) / 1000, 2) for i in range(n)] for m in range(res.GetMagnitudesCount())}

    import Results64Enums as RE
    for loc in ("eCentres", "eNodesNoAvg", "eNodesAvg"):
        r(f"3 location {loc}", lambda loc=loc: summ(ctx.rapi.LoadResult(key(location=getattr(RE.eResLocation, loc)))) if ctx.rapi else "no api")
    r("3 global coords", lambda: summ(ctx.rapi.LoadResult(key(coord=RE.eCoordSystem.eCoordSys_Global))) if ctx.rapi else "no api")
    r("3 EG", lambda: summ(ctx.rapi.LoadResult(key(case="EG"))) if ctx.rapi else "no api")

    time.sleep(5)
    fresh = r("2 fresh InitializeResultsAPI", lambda: sess.project.Model.InitializeResultsAPI())
    r("2 fresh read", lambda: summ(fresh.LoadResult(key())) if fresh else "no api")

    # 5. SCIA's own result-table request, the way the OpenAPI builds it for combinations
    def table_request():
        asm = clr.GetClrType(type(sess.project.Model)).Assembly
        model = sess.project.Model
        crt = asm.GetType("SCIA.OpenAPI.ResultsCalculation.CalculationRunner")
        sw = clr.GetClrType(type(model)).GetField("ServiceWrapper", fl).GetValue(model)
        runner = crt.GetConstructors(fl)[0].Invoke(System.Array[System.Object]([model, sw]))
        k = key()
        item = crt.GetMethod("GetResultPaletteItem", fl).Invoke(runner, [k])
        xr = asm.GetType("SCIA.OpenAPI.ResultsCalculation.XmlRequest").GetConstructors(fl)[0].Invoke(System.Array[System.Object]([item, k, model]))
        return str(xr.GetType().GetMethod("CreateXmlRequest", fl).Invoke(xr, None))

    xml = r("5 request xml (chars)", table_request)
    if isinstance(xml, str) and xml.startswith("<"):
        (WORK / "try08_request.xml").write_text(xml, encoding="utf-8")
        root = info.get("comm_root") if isinstance(info, dict) else None
        if root:
            name = f"OpenAPIRequest_{uuid.uuid4()}.xml"
            os.makedirs(os.path.join(root, "EPROC_REQUEST"), exist_ok=True)
            with open(os.path.join(root, "EPROC_REQUEST", name), "w", encoding="ascii") as fh:
                fh.write(xml)
            out = os.path.join(root, "EPROC_OUTPUT", name)
            t1 = time.time()
            while time.time() - t1 < 60 and not os.path.exists(out):
                time.sleep(0.2)
            time.sleep(1)
            if os.path.exists(out):
                data = open(out, "rb").read()
                (WORK / "try08_table_answer.bin").write_bytes(data)
                reads["5 table answer bytes"] = len(data)
                log("5 table answer", len(data), "bytes after", round(time.time() - t1, 1), "s")
            else:
                reads["5 table answer bytes"] = None
                log("5 no table answer within 60 s")
        r("5 LLSM read after the request", lambda: summ(ctx.rapi.LoadResult(key())) if ctx.rapi else "no api")
        fresh2 = r("5 fresh API after the request", lambda: sess.project.Model.InitializeResultsAPI())
        r("5 fresh read after the request", lambda: summ(fresh2.LoadResult(key())) if fresh2 else "no api")


if __name__ == "__main__":
    run("try08_readback", body)
