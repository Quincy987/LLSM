"""Try 11: open a calculated file and read its results by SCIA's internal ids, skipping the id mapping.

After OpenProject of a file the OpenAPI did not push, the ADM-id -> internal-id mapping is empty, so
LoadResult cannot turn a load-case Guid into SCIA's number. This try builds the native result key
itself: beam (eDsElementType_Beam) id 1 or 2, load case (eDsElementType_LoadCase) id 1 (EG) or 2
(Verkeer-UDL), and calls the native results API (CXEP_SimpleResultsAPI.LoadResult) directly.

Run: python try11_native_read.py <file.esa> [label]
"""
import sys
import threading
import time
import traceback
from pathlib import Path

from common import launch, log, run
from try10_batch import bypass, convert_hand

FILE = Path(sys.argv[1]).resolve()
LABEL = sys.argv[2] if len(sys.argv) > 2 else FILE.stem


def body(ctx, result):
    result["file"] = str(FILE)
    launch(ctx)
    sess = ctx.session
    stop, notes = threading.Event(), []
    threading.Thread(target=convert_hand, args=(stop, notes), daemon=True).start()

    def open_file():
        proj = sess.env.OpenProject(str(FILE))
        sess.set_project(proj)
        return proj is not None

    try:
        log("OpenProject", bypass(sess, open_file, 300))
        time.sleep(3)

        def read():
            import clr
            import System
            import Results64Enums as RE
            import SCIA.OpenAPI.Results as R
            from System.Reflection import BindingFlags as BF
            fl = BF.Public | BF.NonPublic | BF.Instance
            api = sess.project.Model.InitializeResultsAPI()
            if api is None:
                return {"api": None}
            rt = clr.GetClrType(R.ResultsAPI)
            ep = rt.GetField("EP_ResultsApi", fl).GetValue(api)
            out = {"beams": {int(k.Key): str(k.Value) for k in ep.GetBeamNameDict()},
                   "nodes": {int(k.Key): str(k.Value) for k in ep.GetNodeNameDict()}}
            for case_id in (1, 2):
                for beam_id in (1, 2):
                    k = R.ResultKey()
                    k.CaseType = RE.eDsElementType.eDsElementType_LoadCase
                    k.EntityType = RE.eDsElementType.eDsElementType_Beam
                    k.Dimension = RE.eDimension.eDim_1D
                    k.ResultType = RE.eResultType.eFemBeamInnerForces
                    k.CoordSystem = RE.eCoordSystem.eCoordSys_Local
                    k.EntityName = out["beams"].get(beam_id, "")
                    from SCIA.OpenAPI import ApiGuid
                    k.CaseId = ApiGuid(System.Guid.NewGuid())  # any id: the case number is set on the native key below

                    args = System.Array[System.Object]([k])
                    rt.GetMethod("ModifyResultTypeForRequest", fl).Invoke(api, args)
                    k = args[0]
                    cx = rt.GetMethod("ConvertKey", fl).Invoke(api, System.Array[System.Object]([k]))
                    cx.SetEntityID(beam_id)
                    cx.SetCase(int(RE.eDsElementType.eDsElementType_LoadCase), case_id)
                    out.setdefault("keys", {})[f"{beam_id}/{case_id}"] = str(cx.GetStringUniqueKey())
                    raw = ep.LoadResult(cx)
                    label = f"beam {beam_id} / case {case_id}"
                    if raw is None:
                        out[label] = None
                        continue
                    res = R.Result(raw)
                    n = res.GetSectionCount()
                    out[label] = {"x_m": [round(res.GetSectionPosX(i), 3) for i in range(n)],
                                  **{str(res.GetMagnitudeName(m)): [round(res.GetValue(m, i) / 1000, 2) for i in range(n)]
                                     for m in range(res.GetMagnitudesCount())}}
            return out

        try:
            result["read"] = bypass(sess, read)
        except Exception as e:  # noqa: BLE001
            result["read"] = f"EXC {type(e).__name__}: {e} {traceback.format_exc()[-600:]}"
        for k, v in (result["read"].items() if isinstance(result["read"], dict) else [("read", result["read"])]):
            log(k, str(v)[:700])
    finally:
        stop.set()


if __name__ == "__main__":
    run(f"try11_native_read_{LABEL}", body)
