"""Try 13 (control): does IEnvESAProject.SaveToFile keep stored results?

Open a copy of kademuur.esa (32 result sets), SaveToFile to a new path, count result sets in it.
If they survive, a SaveToFile after SCIA's Calculate that holds no results means no results were made.
Then the same file is calculated with SCIA's Calculate (try 7 route) and saved again, and counted.
"""
import threading
import time
from pathlib import Path

from common import WORK, launch, log, run
from try08_readback import gui_calculate
from try10_batch import bypass, convert_hand, env_project

SRC = WORK / "ctrl" / "kademuur_copy.esa"


def counts(p: Path) -> dict:
    from llsm.scia.esa_file import read_report_index
    i = read_report_index(p)
    return {"result_sets": i.result_sets, "size": p.stat().st_size,
            "EP_Results": {k: v for k, v in i.counts.items() if k.startswith("EP_Results")}}


def body(ctx, result):
    result["source"] = counts(SRC)
    log("source", result["source"]["result_sets"])
    launch(ctx)
    sess = ctx.session
    stop, notes = threading.Event(), []
    threading.Thread(target=convert_hand, args=(stop, notes), daemon=True).start()
    try:
        def open_file():
            proj = sess.env.OpenProject(str(SRC))
            sess.set_project(proj)
            return proj is not None

        log("OpenProject", bypass(sess, open_file, 300))
        time.sleep(3)
        out1 = WORK / "ctrl" / "kademuur_savetofile.esa"
        if out1.exists():
            out1.unlink()
        log("SaveToFile", bypass(sess, lambda: bool(env_project(sess)[1].SaveToFile(str(out1))), 300))
        result["after_savetofile"] = counts(out1) if out1.exists() else None
        log("after SaveToFile", result["after_savetofile"] and result["after_savetofile"]["result_sets"])

        result["calc"] = gui_calculate(sess)
        log("calculate", result["calc"])
        out2 = WORK / "ctrl" / "kademuur_calculated_savetofile.esa"
        if out2.exists():
            out2.unlink()
        log("SaveToFile after Calculate", bypass(sess, lambda: bool(env_project(sess)[1].SaveToFile(str(out2))), 300))
        result["after_calculate"] = counts(out2) if out2.exists() else None
        log("after Calculate + SaveToFile", result["after_calculate"] and result["after_calculate"]["result_sets"])
    finally:
        stop.set()


if __name__ == "__main__":
    run("try13_save_control", body)
