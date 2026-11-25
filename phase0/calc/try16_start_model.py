"""Try 16: the VIKTOR way, ready for production (plan step 1, docs/verify/calc-run.md).

  python try16_start_model.py make      # v24 copy of the kademuren start model (convert question answered)
  python try16_start_model.py repeat    # push the test beam, batch 3x with SCIA open, then edit + batch
  python try16_start_model.py clash     # names the start model owns: LG1, BG1, a material name
  python try16_start_model.py timing    # beam grids of 2..500 beams x 2/10 cases: push, save, batch

Batch = ONE `ESA_XML LIN <copy> classes.xml -x<out.xml>` while LLSM's own SCIA is open.
Hand values (test beam 2 x 12 m): case 01 |My| at B = q L^2/8 = 162 kNm (9 kN/m), 216 kNm (12 kN/m);
case 02 (span 1 only) = q L^2/16 = 81 kNm.
"""
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from common import WORK, launch, log, run, t
from try10_batch import bypass, convert_hand, env_project

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "report"))
import route_b  # noqa: E402
from llsm.scia import esa_xml  # noqa: E402
from llsm.scia.load_xml import build_case_classes_update  # noqa: E402

KADEMUREN = Path(r"C:\Development\Viktor\viktor-apps\amsterdam-kademuren\app\segment\scia_integration\scia\model.esa")
W = WORK / "try16"
START = W / "start_v24.esa"
STEP = sys.argv[1] if len(sys.argv) > 1 else "repeat"


def answered_hand():
    stop, notes = threading.Event(), []
    threading.Thread(target=convert_hand, args=(stop, notes), daemon=True).start()
    return stop, notes


def open_copy(sess, src: Path, name: str) -> Path:
    """A fresh copy of ``src`` opened as the session's project (the previous one closed unsaved)."""
    from SCIA.OpenAPI import SaveMode
    copy = W / f"{Path(name).stem}-{time.time_ns()}.esa"  # never the file the previous project still holds

    def go():
        if sess.project is not None:
            try:
                sess.project.CloseProject(SaveMode.SaveChangesNo)
            except Exception as e:  # noqa: BLE001
                log("close previous project failed:", e)
        shutil.copyfile(src, copy)
        proj = sess.env.OpenProject(str(copy))
        sess.set_project(proj)
        return proj is not None

    assert bypass(sess, go, 300), "OpenProject failed"
    return copy


def push_into(ctx, src: Path, name: str) -> dict:
    """push_model, but into a copy of ``src`` instead of SCIA's empty file."""
    from SciaTools.Kernel.ModelExchangerExtension.Models.Exchange import AdmChangeStatus
    sess = ctx.session
    t0 = time.time()
    open_copy(sess, src, name)
    opened = time.time() - t0
    model = ctx.builder.model

    def push():
        errors = []
        for elem in model:
            r = sess.project.Model.CreateAdmObject(elem)
            if r.PartialAddResult.Status != AdmChangeStatus.Ok:
                errors.append({"name": str(elem.Name), "errors": " ".join(str(r.PartialAddResult.Errors).split())[:300]})
        sess.project.Model.RefreshModel_ToSCIAEngineer()
        return errors

    errors = bypass(sess, push, 600)
    ctx.project_opened, ctx.mesh_dirty = True, True
    return {"errors": errors, "open_s": round(opened, 1), "push_s": round(time.time() - t0 - opened, 1)}


def save(sess, target: Path) -> dict:
    if target.exists():
        target.unlink()
    stop, notes = answered_hand()
    t0 = time.time()
    try:
        ok = bypass(sess, lambda: bool(env_project(sess)[1].SaveToFile(str(target))), 300)
    finally:
        stop.set()
    return {"ok": ok, "s": round(time.time() - t0, 1), "questions_answered": notes,
            "MB": round(target.stat().st_size / 1e6, 1) if target.exists() else None}


def batch(source: Path, cases: list[str], label: str) -> dict:
    """LIN + one result class per case + -x, in one ESA_XML run, past LLSM's 'no SCIA' guard."""
    folder = W / label
    folder.mkdir(exist_ok=True)
    update = folder / "classes.xml"
    update.write_text(build_case_classes_update(cases), encoding="utf-8")
    shutil.copyfile(esa_xml.RESULT_CLASSES_DEF_FILE, folder / esa_xml.RESULT_CLASSES_DEF_FILE.name)
    out = folder / "results.xml"
    if out.exists():
        out.unlink()
    exe = esa_xml._esa_xml_exe()
    copy = folder / "input.esa"
    shutil.copyfile(source, copy)
    t0 = time.time()
    p = subprocess.run([str(exe), "LIN", str(copy), str(update), f"-x{out}", f"-l{folder / 'run.log'}"],
                       cwd=exe.parent, capture_output=True, text=True, errors="replace", timeout=1800)
    secs = round(time.time() - t0, 1)
    copy.unlink(missing_ok=True)
    res = {"exit": p.returncode, "message": esa_xml.EXIT_MEANING.get(p.returncode, f"exit {p.returncode}"), "s": secs,
           "xml_bytes": out.stat().st_size if out.exists() else None}
    if out.exists():
        tables = route_b.parse_xmlio(out)
        res["tables"] = [(tb.selection, tb.name, tb.heading, len(tb.rows)) for tb in tables]
        res["xml"] = str(out)
    return res


def my_at(xml: Path, case: str, beam: str, dx: float) -> float | None:
    """My (kNm) of ``beam`` at ``dx`` in the class of ``case``."""
    for tb in route_b.parse_xmlio(xml):
        if tb.selection == case and tb.name.startswith("Interne 1D"):
            c = tb.columns
            for row in tb.rows:
                if row[c.index("Naam")] == beam and abs(float(row[c.index("dx")]) - dx) < 1e-6:
                    return round(float(row[c.index("M_y")]) / 1000, 2)
    return None


def test_beam(ctx, names: dict | None = None, q2: float = -9) -> list[str]:
    n = {"mat": "CB_C30/37", "css": "CB_R1000x600", "group": "CB LG", "case": "CB BG"} | (names or {})
    cases = n.get("cases") or [f"{n['case']} 01", f"{n['case']} 02"]
    t.new_model(ctx, name="CB")
    steps = [
        t.add_material(ctx, n["mat"], kind="Concrete"),
        t.add_rectangular_section(ctx, n["css"], material=n["mat"], width_mm=1000, height_mm=600),
        t.add_node(ctx, "CB_A", 0, 0, 0), t.add_node(ctx, "CB_B", 12000, 0, 0), t.add_node(ctx, "CB_C", 24000, 0, 0),
        t.add_beam(ctx, "CB_Veld1", "CB_A", "CB_B", n["css"]), t.add_beam(ctx, "CB_Veld2", "CB_B", "CB_C", n["css"]),
        t.add_pinned_support(ctx, "CB_Sup-A", "CB_A"),
        t.add_roller_support(ctx, "CB_Sup-B", "CB_B", "Z"), t.add_roller_support(ctx, "CB_Sup-C", "CB_C", "Z"),
        t.add_load_group(ctx, n["group"], kind="Variable"),
        t.add_load_case(ctx, cases[0], group=n["group"]), t.add_load_case(ctx, cases[1], group=n["group"]),
        t.add_line_load(ctx, "CB_q1", beam="CB_Veld1", load_case=cases[0], magnitude_kn_per_m=-9),
        t.add_line_load(ctx, "CB_q2", beam="CB_Veld2", load_case=cases[0], magnitude_kn_per_m=q2),
        t.add_line_load(ctx, "CB_q3", beam="CB_Veld1", load_case=cases[1], magnitude_kn_per_m=-9),
    ]
    bad = [s for s in steps if not s.get("ok")]
    assert not bad, bad
    return cases


def make(ctx, result):
    W.mkdir(parents=True, exist_ok=True)
    launch(ctx)
    sess = ctx.session
    open_copy(sess, KADEMUREN, "kademuren_copy.esa")
    result["save"] = save(sess, START)
    log("v24 start model", result["save"])
    # a second save of the v24 file must not ask again
    open_copy(sess, START, "start_reopen.esa")
    result["resave"] = save(sess, W / "start_resaved.esa")
    log("save of the v24 copy", result["resave"])


def repeat(ctx, result):
    assert START.exists(), "run 'make' first"
    cases = test_beam(ctx)
    launch(ctx)
    sess = ctx.session
    result["push"] = push_into(ctx, START, "pushed_copy.esa")
    log("push", result["push"])
    result["save"] = save(sess, W / "pushed.esa")
    log("save", result["save"])
    result["runs"] = []
    for i in range(3):
        b = batch(W / "pushed.esa", cases, f"run{i + 1}")
        b["my_B_case01"] = my_at(Path(b["xml"]), cases[0], "CB_Veld1", 12) if b.get("xml") else None
        b["my_B_case02"] = my_at(Path(b["xml"]), cases[1], "CB_Veld1", 12) if b.get("xml") else None
        b.pop("tables", None)
        result["runs"].append(b)
        log(f"batch {i + 1} with SCIA open:", b)
    # edit after push: the load on span 2 of case 01 from -9 to -12 (through _propagate_edit)
    result["edit"] = t.set_line_load_magnitude(ctx, "CB_q2", -12)
    t.set_line_load_magnitude(ctx, "CB_q1", -12)
    log("edit", result["edit"])
    result["save_after_edit"] = save(sess, W / "edited.esa")
    b = batch(W / "edited.esa", cases, "after_edit")
    b["my_B_case01"] = my_at(Path(b["xml"]), cases[0], "CB_Veld1", 12) if b.get("xml") else None
    b["export_tables_survive"] = sorted({(x[0], x[1]) for x in b.pop("tables", [])})
    result["after_edit"] = b
    log("after edit (12 kN/m both spans, hand 216 kNm):", b)


def clash(ctx, result):
    assert START.exists(), "run 'make' first"
    variants = {"group LG1": {"group": "LG1"}, "case BG1": {"cases": ["BG1", "CB BG 02"]},
                "material C30/37": {"mat": "C30/37"}, "material C20/25": {"mat": "C20/25"}}
    if len(sys.argv) > 2:
        variants = {k: v for k, v in variants.items() if k in sys.argv[2:]}
    launch(ctx)
    sess = ctx.session
    result["variants"] = {}
    for label, names in variants.items():
        try:
            cases = test_beam(ctx, names)
            ctx.session = sess
            p = push_into(ctx, START, "clash_copy.esa")
            target = W / f"clash-{len(result['variants'])}.esa"
            s = save(sess, target)
            b = batch(target, cases, "clash-" + label.replace(" ", "_").replace("/", ""))
            v = {"push_errors": [e["name"] + ": " + e["errors"][-90:] for e in p["errors"]], "save": s["ok"], "batch": b["message"],
                 "tables": b.get("tables"),
                 "my_B_case01": my_at(Path(b["xml"]), cases[0], "CB_Veld1", 12) if b.get("xml") else None}
        except Exception as e:  # noqa: BLE001
            v = {"exception": f"{type(e).__name__}: {e}"}
        result["variants"][label] = v
        log("clash", label, v)


def grid(ctx, beams: int, cases: int) -> list[str]:
    """A plane grid of 6 m beams side by side (no shared nodes), fixed at one end and on a roller at the other
    (pinned + roller leaves a single beam free to twist: SCIA then makes no results, try 17); ``cases`` cases."""
    t.new_model(ctx, name=f"grid{beams}")
    t.add_material(ctx, "G_C30/37", kind="Concrete")
    t.add_rectangular_section(ctx, "G_R300x500", material="G_C30/37", width_mm=300, height_mm=500)
    t.add_load_group(ctx, "G LG", kind="Variable")
    names = [f"G BG {i + 1:02d}" for i in range(cases)]
    for c in names:
        t.add_load_case(ctx, c, group="G LG")
    for i in range(beams):
        y = i * 2000
        t.add_node(ctx, f"G_{i}a", 0, y, 0)
        t.add_node(ctx, f"G_{i}b", 6000, y, 0)
        t.add_beam(ctx, f"G_B{i}", f"G_{i}a", f"G_{i}b", "G_R300x500")
        t.add_fixed_support(ctx, f"G_S{i}a", f"G_{i}a")
        t.add_roller_support(ctx, f"G_S{i}b", f"G_{i}b", "Z")
        for k, c in enumerate(names):
            t.add_line_load(ctx, f"G_q{i}_{k}", beam=f"G_B{i}", load_case=c, magnitude_kn_per_m=-(k + 1))
    return names


def timing(ctx, result):
    assert START.exists(), "run 'make' first"
    sizes = [(2, 2), (2, 10), (50, 2), (50, 10), (200, 2), (200, 10)]
    if len(sys.argv) > 2:
        sizes = [tuple(int(x) for x in s.split("x")) for s in sys.argv[2:]]
    launch(ctx)
    sess = ctx.session
    result["sizes"] = []
    for beams, ncases in sizes:
        t0 = time.time()
        cases = grid(ctx, beams, ncases)
        built = round(time.time() - t0, 1)
        ctx.session = sess
        p = push_into(ctx, START, "grid_copy.esa")
        t1 = time.time()
        bypass(sess, lambda: sess.project.CreateMesh(), 600)
        mesh_s = round(time.time() - t1, 1)
        target = W / f"grid-{beams}x{ncases}.esa"
        s = save(sess, target)
        b = batch(target, cases, "grid")
        rows = 0
        if b.get("xml"):
            rows = sum(len(tb.rows) for tb in route_b.parse_xmlio(Path(b["xml"])) if tb.name.startswith("Interne 1D") and tb.selection in cases)
        e = {"beams": beams, "cases": ncases, "items": t.model_summary(ctx).get("items"), "build_s": built,
             "open_s": p["open_s"], "push_s": p["push_s"], "mesh_s": mesh_s, "push_errors": len(p["errors"]), "save_s": s["s"], "MB": s["MB"],
             "batch_s": b["s"], "batch": b["message"], "force_rows": rows}
        result["sizes"].append(e)
        log("timing", e)
        (W / "timing.json").write_text(json.dumps(result["sizes"], indent=1), encoding="utf-8")


if __name__ == "__main__":
    W.mkdir(parents=True, exist_ok=True)
    run(f"try16_{STEP}", {"make": make, "repeat": repeat, "clash": clash, "timing": timing}[STEP])
