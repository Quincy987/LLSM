"""Try 1: baseline. LLSM's own push + run_analysis; measure files after mesh and after the calculation.

Try 2 rides along: the boolean of SCIA's calculation command (CommandESA), and what SCIA logs while it runs.
"""
import sys
import time

from common import VARIANTS, build_variant, diff, forces, launch, log, log_mark, log_since, run, snap, solver_link_sizes, t, verdict

CALC_CMD = "{C35839BF-E775-454D-BC39-723076881E06}"
VARIANT = next((a for a in sys.argv[1:] if a in VARIANTS), "base")
MESH_CMD = "{F5590706-D252-41B3-BCC3-CDFD2EB87B39}"


def command(sess, guid: str):
    """Call AdmToAdmServiceWrapper.CommandESA(guid, "") by reflection and return its boolean."""
    import clr
    from System.Reflection import BindingFlags as BF
    fl = BF.Public | BF.NonPublic | BF.Instance | BF.Static
    proj = sess.project
    sw = clr.GetClrType(type(proj)).GetField("ServiceWrapper", fl).GetValue(proj)
    m = sw.GetType().GetMethod("CommandESA", fl)
    return bool(m.Invoke(sw, [guid, ""]))


def body(ctx, result):
    result["variant"] = VARIANT
    build_variant(ctx, VARIANT)
    launch(ctx)
    log("push", t.push_model(ctx))
    sess = ctx.session

    s0, m0 = snap(), log_mark()
    t1 = time.time()
    mesh_ok = sess.submit(lambda: command(sess, MESH_CMD), timeout_s=300)
    s1 = snap()
    result["mesh"] = {"command_returned": mesh_ok, "seconds": round(time.time() - t1, 1), "files": len(diff(s0, s1)),
                      "logs": log_since(m0)}
    log("mesh", result["mesh"]["command_returned"], result["mesh"]["seconds"], "s,", result["mesh"]["files"], "files")

    m1 = log_mark()
    t1 = time.time()
    calc_ok = sess.submit(lambda: command(sess, CALC_CMD), timeout_s=300)
    secs = round(time.time() - t1, 1)
    time.sleep(25)
    s2 = snap()
    calc_files = diff(s1, s2)
    result["calc"] = {"command_returned": calc_ok, "seconds": secs, "files_new_or_changed": calc_files,
                      "solver_link_before": solver_link_sizes(s1), "solver_link_after": solver_link_sizes(s2),
                      "logs": log_since(m1)}
    log("calc command returned", calc_ok, "in", secs, "s; files after 25 s:", calc_files)

    # LLSM's own route on top, for the read-back: run_analysis (mesh is clean now, so it only calculates)
    ctx.rapi = sess.submit(lambda: sess.project.Model.InitializeResultsAPI(), timeout_s=120)
    ctx.last_calc_ok = ctx.rapi is not None
    case = VARIANTS[VARIANT][0][-1]
    read = forces(ctx, "Veld1", case)
    log("forces Veld1 / Verkeer-UDL", read)
    result["read"] = read
    result["verdict"] = verdict(calc_files, read)
    log("verdict", result["verdict"])


if __name__ == "__main__":
    run(f"try01_baseline_{VARIANT}", body)
