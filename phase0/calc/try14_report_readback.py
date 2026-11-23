"""Try 14 (step 2b, read-back 3): the owner's suggestion, forces from SCIA's own report.

The chat's model has no engineering report, and none can be added from outside SCIA. So the test beam is
pushed into a copy of a model that has one (brugdek.esa, Report_1, items over "all result classes"),
next to the deck, with prefixed names. The file is saved (SaveToFile), SCIA closed, and LLSM's existing
report route runs: LIN on a copy, one result class per case of the group "LLSM-ML CB", print XLSX
(`llsm report write --calculate --cases-group`). Then the 1D internal forces of CB_Veld1 are read from
the XLSX and compared with the hand value |My| at B = 162 kNm.

Run: python try14_report_readback.py [push|report|read]   (default: all three)
"""
import json
import shutil
import sys
import threading
import time
from pathlib import Path

from common import WORK, launch, log, run, t
from try10_batch import bypass, convert_hand, env_project

SRC = Path(r"C:\Users\klerk015\Documents\LLSM demo\brugdek.esa")
BASE = WORK / "try14" / "brugdek_with_beam_in.esa"
PUSHED = WORK / "try14" / "brugdek_with_beam.esa"
OUT = WORK / "try14" / "report"
GROUP = "LLSM-ML CB"
STEPS = sys.argv[1:] or ["push", "report", "read"]


def push(ctx, result):
    BASE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SRC, BASE)
    for p in (PUSHED,):
        if p.exists():
            p.unlink()
    t.new_model(ctx, name="CB")
    steps = [
        t.add_material(ctx, "CB_C30/37", kind="Concrete"),
        t.add_rectangular_section(ctx, "CB_R1000x600", material="CB_C30/37", width_mm=1000, height_mm=600),
        # 20 m beside the deck, so it touches nothing of it
        t.add_node(ctx, "CB_A", 0, 20000, 0), t.add_node(ctx, "CB_B", 12000, 20000, 0), t.add_node(ctx, "CB_C", 24000, 20000, 0),
        t.add_beam(ctx, "CB_Veld1", "CB_A", "CB_B", "CB_R1000x600"), t.add_beam(ctx, "CB_Veld2", "CB_B", "CB_C", "CB_R1000x600"),
        t.add_pinned_support(ctx, "CB_Sup-A", "CB_A"),
        t.add_roller_support(ctx, "CB_Sup-B", "CB_B", "Z"), t.add_roller_support(ctx, "CB_Sup-C", "CB_C", "Z"),
        t.add_load_group(ctx, GROUP, kind="Variable"),
        t.add_load_case(ctx, f"{GROUP} 01", group=GROUP),
        t.add_line_load(ctx, "CB_q1", beam="CB_Veld1", load_case=f"{GROUP} 01", magnitude_kn_per_m=-9),
        t.add_line_load(ctx, "CB_q2", beam="CB_Veld2", load_case=f"{GROUP} 01", magnitude_kn_per_m=-9),
    ]
    result["build"] = steps
    assert all(s["ok"] for s in steps), steps
    launch(ctx)
    sess = ctx.session
    stop, notes = threading.Event(), []
    threading.Thread(target=convert_hand, args=(stop, notes), daemon=True).start()
    try:
        from SciaTools.Kernel.ModelExchangerExtension.Models.Exchange import AdmChangeStatus
        model = ctx.builder.model

        def open_and_push():
            proj = sess.env.OpenProject(str(BASE))
            sess.set_project(proj)
            errors = []
            for elem in model:
                r = proj.Model.CreateAdmObject(elem)
                if r.PartialAddResult.Status != AdmChangeStatus.Ok:
                    errors.append({"name": str(elem.Name), "errors": [str(e) for e in r.PartialAddResult.Errors]})
            proj.Model.RefreshModel_ToSCIAEngineer()
            return errors

        result["push_errors"] = bypass(sess, open_and_push, 300)
        log("pushed into the brugdek copy; errors:", result["push_errors"])
        result["saved"] = bypass(sess, lambda: bool(env_project(sess)[1].SaveToFile(str(PUSHED))), 300)
        log("SaveToFile", result["saved"], PUSHED.stat().st_size if PUSHED.exists() else None)
    finally:
        stop.set()


def report(result):
    from llsm.report.apply import ReportRequest, write_report
    import inspect
    log("write_report signature", inspect.signature(write_report))
    OUT.mkdir(parents=True, exist_ok=True)
    t1 = time.time()
    req = ReportRequest(formats=["xlsx"], calculate=True, cases_group=GROUP)
    res = write_report(PUSHED, req, out_dir=OUT) if "out_dir" in inspect.signature(write_report).parameters else write_report(PUSHED, req)
    result["report"] = {k: res.get(k) for k in ("ok", "problems", "warnings", "outputs", "chapters")}
    log("report", res.get("ok"), round(time.time() - t1), "s", res.get("problems"), res.get("outputs"))


def read(result):
    from llsm.report.xlsx import read_sheets
    xlsx = next(iter(sorted(OUT.glob("*.xlsx"))), None)
    if xlsx is None:
        result["read"] = "no XLSX"
        return
    sheets = read_sheets(xlsx)
    found = []
    for title, rows in sheets:
        flat = [[c for c in r if c not in (None, "")] for r in rows]
        if not any(any("CB_Veld" in str(c) for c in r) for r in flat):
            continue
        head = " ".join(str(r[0]) for r in flat[:6] if r)
        found.append({"sheet": title, "head": head[:200], "rows": [[str(c) for c in r] for r in flat[:40]]})
    result["read"] = {"xlsx": str(xlsx), "sheets_with_the_beam": found}
    for f in found[:6]:
        log("sheet", f["sheet"], "|", f["head"])
        for r in f["rows"][:14]:
            log("   ", " | ".join(r)[:220])


def body(ctx, result):
    if "push" in STEPS:
        push(ctx, result)
        log("close SCIA", t.close_scia(ctx))
        time.sleep(5)
    if "report" in STEPS:
        report(result)
    if "read" in STEPS:
        read(result)


if __name__ == "__main__":
    run("try14_report_readback", body)
