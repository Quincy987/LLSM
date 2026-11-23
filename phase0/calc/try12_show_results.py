"""Try 12: does SCIA's Calculate (try 7) really produce results? Ask SCIA to show them and photograph it.

After the calculation: SCIA's own commands "1D internal forces" (7EEE0CEC...) and "Reactions"
(08CF2495...) through ExecuteCommandClsid, then a PrintWindow picture of the main window
(no focus taken). Hand values for Verkeer-UDL: |My| at B = 162 kNm; reactions 40.5 / 135 / 40.5 kN.
Also SCIA's 'Calculation protocol' command is tried.
"""
import time

from common import WORK, build_variant, launch, log, run, t
from try07_gui_calculate import com_app
from try08_readback import gui_calculate


def full_ids() -> dict:
    import re
    xml = open(r"C:\Program Files\SCIA\Engineer24.0\Configuration\Commands.xml", encoding="utf-8", errors="replace").read()
    out = {}
    for uuid in ("CMD.RESULTS.1D_INTERNAL_FORCES", "CMD.RESULTS.REACTIONS", "CMD.RESULTS.CALCULATION_PROTOCOL"):
        m = re.search(rf'uicommand uuid="{re.escape(uuid)}"[^>]*backend_command_id="([^"]+)"', xml)
        out[uuid] = "{" + m.group(1) + "}" if m else None
    return out


def main_window_picture(name: str) -> str | None:
    from llsm.scia.autoclick import _capture, visible_windows
    from llsm.scia.session import our_scia_processes
    pids = {p.pid for p in our_scia_processes()}
    mains = [w for w in visible_windows(pids) if w.cls.startswith("Qt") and "OwnDC" in w.cls and w.width >= 1000 and w.rect[0] > -1000]
    if not mains:
        return None
    w = max(mains, key=lambda w: w.width * (w.rect[3] - w.rect[1]))
    pic = _capture(w)
    if pic is None:
        return None
    target = WORK / name
    target.write_bytes(pic.read_bytes())
    return str(target)


def body(ctx, result):
    build_variant(ctx, "base_static")
    launch(ctx, visible=True)
    log("push", t.push_model(ctx))
    sess = ctx.session
    log("mesh", sess.submit(lambda: sess.project.CreateMesh(), timeout_s=300))
    result["calc"] = gui_calculate(sess)
    log("calculate", result["calc"])
    ids = full_ids()
    result["command_ids"] = ids
    import System

    for uuid, guid in ids.items():
        if not guid:
            continue

        def call(guid=guid):
            out = System.String("")
            return str(com_app(sess).ExecuteCommandClsid(System.Guid(guid), "", out))

        try:
            r = sess.submit(call, timeout_s=120)
        except Exception as e:  # noqa: BLE001
            r = f"EXC {type(e).__name__}: {e}"
            sess._clicker.pending = None
        time.sleep(4)
        pic = main_window_picture(f"try12_{uuid.split('.')[-1].lower()}.png")
        result.setdefault("shown", {})[uuid] = {"returned": r, "picture": pic}
        log(uuid, r, pic)


if __name__ == "__main__":
    run("try12_show_results", body)
