"""Try 5: other calculation entry points.

Found in SCIA's own configuration (Configuration/Commands.xml + CommandsEvaluation.xml):
the OpenAPI's RunCalculation sends backend command C35839BF = CMD.TOOLS.HIDDEN_CALCULATION_LINEAR,
which is gated by `<evaluator type="license" tech_module="446" module_name="IS_SCIA_FOR_INTERNAL_USE_ONLY"/>`.
Only the two hidden calculations carry that gate. The GUI's CMD.TOOLS.CALCULATION ("Calculate",
command_name Analysis.Calculation) has no backend id.

This try calls SCIA's COM interface directly (IEnvESAApp.ExecuteCommandClsid / ExecuteCommandString /
ExecuteCommandString_Hide), logs the error code and output string of each, and measures files.
Every SCIA window that appears is photographed.
"""
import sys
import threading
import time

from common import WORK, build, diff, forces, launch, log, log_mark, log_since, run, snap, solver_link_sizes, t, verdict

HIDDEN_LINEAR = "{C35839BF-E775-454D-BC39-723076881E06}"
HIDDEN = "{AD23F9E2-3C96-4C67-8A91-F6A379DB56FD}"
ONLY = sys.argv[1:]  # optional: run only these attempt labels


def com_app(sess):
    import clr
    import EnvESA80
    from System.Reflection import BindingFlags as BF
    fl = BF.Public | BF.NonPublic | BF.Instance
    proj = sess.project
    sw = clr.GetClrType(type(proj)).GetField("ServiceWrapper", fl).GetValue(proj)
    env = sw.GetType().GetField("MyEnvEsa", fl).GetValue(sw)
    return EnvESA80.IEnvESAApp(EnvESA80.IEnvESA(env).ESAApp)


def photographer(stop: threading.Event, shots: list) -> None:
    """Photograph every new visible window of SCIA (dialogs of a calculation)."""
    import ctypes
    import psutil
    import win32gui
    import win32process
    from PIL import ImageGrab
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    seen = set()
    while not stop.is_set():
        found = []

        def top(h, _):
            if win32gui.IsWindowVisible(h):
                try:
                    name = psutil.Process(win32process.GetWindowThreadProcessId(h)[1]).name()
                except psutil.Error:
                    return True
                if name.lower().startswith(("sciaengineer", "esa")):
                    found.append((h, win32gui.GetWindowText(h), win32gui.GetWindowRect(h)))
            return True

        try:
            win32gui.EnumWindows(top, None)
        except Exception:  # noqa: BLE001
            pass
        for h, title, r in found:
            key = (h, r)
            if key in seen or r[0] < -1000 or r[2] - r[0] < 50 or r[2] - r[0] > 1800:
                continue
            seen.add(key)
            path = WORK / f"try05_window_{len(shots):02d}.png"
            try:
                ImageGrab.grab(bbox=r, all_screens=True).save(path)
                shots.append({"title": title, "rect": r, "picture": str(path), "at": time.strftime("%H:%M:%S")})
                log("window", repr(title), r, "->", path.name)
            except Exception as e:  # noqa: BLE001
                log("shot failed", e)
        time.sleep(0.5)


def attempt(ctx, label: str, call, result: dict) -> bool:
    sess = ctx.session
    s0, m0 = snap(), log_mark()
    t1 = time.time()
    try:
        ret = sess.submit(call, timeout_s=240)
    except Exception as e:  # noqa: BLE001
        ret = f"EXC {type(e).__name__}: {e}"
    secs = round(time.time() - t1, 1)
    files: dict = {}
    for _ in range(40):  # up to 40 s for solver output
        time.sleep(1)
        files = diff(s0, snap())
        if any(k.endswith("SolverLinkData.dat") and v > 999 for k, v in files.items()):
            time.sleep(3)
            files = diff(s0, snap())
            break
    entry = {"returned": str(ret), "seconds": secs, "files": files, "solver_link": solver_link_sizes(snap()),
             "logs": log_since(m0, 15)}
    result.setdefault("attempts", {})[label] = entry
    log(label, "->", entry["returned"], f"({secs} s),", len(files), "files changed:", list(files)[:8])
    return any(k.endswith("SolverLinkData.dat") and v > 999 for k, v in files.items())


def body(ctx, result):
    build(ctx)
    launch(ctx, visible=True)
    log("push", t.push_model(ctx))
    sess = ctx.session
    log("mesh", sess.submit(lambda: sess.project.CreateMesh(), timeout_s=300))

    import System

    def clsid(guid, params=""):
        def call():
            app = com_app(sess)
            out = System.String("")
            r = app.ExecuteCommandClsid(System.Guid(guid), params, out)
            return r
        return call

    def by_string(name, params="", hide=False):
        def call():
            app = com_app(sess)
            out = System.String("")
            fn = app.ExecuteCommandString_Hide if hide else app.ExecuteCommandString
            return fn(name, params, out)
        return call

    stop, shots = threading.Event(), []
    threading.Thread(target=photographer, args=(stop, shots), daemon=True).start()
    attempts = [
        ("clsid hidden linear", clsid(HIDDEN_LINEAR)),
        ("clsid hidden", clsid(HIDDEN)),
        ("string CMD.TOOLS.CALCULATION", by_string("CMD.TOOLS.CALCULATION")),
        ("string Analysis.Calculation", by_string("Analysis.Calculation")),
        ("string_hide CMD.TOOLS.CALCULATION", by_string("CMD.TOOLS.CALCULATION", hide=True)),
        ("string calcn", by_string("calcn")),
    ]
    calculated_by = None
    for label, call in attempts:
        if ONLY and label not in ONLY:
            continue
        if attempt(ctx, label, call, result):
            calculated_by = label
            break
        if sess.is_wedged:
            log("session wedged after", label, "- stopping")
            break
    stop.set()
    result["windows"] = shots
    result["calculated_by"] = calculated_by
    if calculated_by and not sess.is_wedged:
        ctx.rapi = sess.submit(lambda: sess.project.Model.InitializeResultsAPI(), timeout_s=120)
        ctx.last_calc_ok = ctx.rapi is not None
        read = forces(ctx, "Veld1", "Verkeer-UDL")
        result["read"] = read
        result["read_veld2"] = forces(ctx, "Veld2", "Verkeer-UDL")
        result["read_eg"] = forces(ctx, "Veld1", "EG")
        result["verdict"] = verdict(result["attempts"][calculated_by]["files"], read)
        log("read", read)
        log("verdict", result["verdict"])


if __name__ == "__main__":
    run("try05_entrypoints", body)
