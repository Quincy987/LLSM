"""Shared harness for the calculation spike (docs/verify/calc-run.md).

Every try builds the same test model, measures with the same yardstick and closes SCIA.

Test model: continuous concrete beam 2 x 12 m, 1000 x 600 mm, pinned at A, Z rollers at B and C,
EG (self weight) and Verkeer-UDL (-9 kN/m on both spans). Hand value for Verkeer-UDL:
|My| at B = 9 * 12^2 / 8 = 162 kNm, reaction sum 216 kN.

Yardstick: a try counts as calculated only when (a) files appear after the calculation command
(solver output next to the mesh, SolverLinkData.dat well above 999 bytes) and (b) LoadResult for
Veld1 / Verkeer-UDL returns sections. The "ok" of run_analysis proves nothing.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = HERE / "work"
WORK.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("LLSM_ANSWER_LICENCE_NOTICES", "1")

from llsm.scia import tools as t  # noqa: E402
from llsm.scia.loader import load as load_scia  # noqa: E402
from llsm.scia.tools import LlsmContext  # noqa: E402

HAND_MY_B_KNM = 162.0
HAND_REACTIONS_KN = 216.0
T0 = time.time()
LOG: list[str] = []


def log(*parts) -> None:
    line = f"[{time.time() - T0:6.1f}s] " + " ".join(str(p) for p in parts)
    print(line, flush=True)
    LOG.append(line)


VARIANTS = {
    # name: (cases to add, group of the UDL case, fixed support at A, load type SCIA stores for Verkeer-UDL)
    # The builder now writes a variable Standard case as Static (case_load_type). To reproduce what the tries
    # measured before that fix, build_variant sets the stored load type of Verkeer-UDL itself.
    "base": (("EG", "Verkeer-UDL"), "LG-Verkeer", False, "Standard"),
    "eg_only": (("EG",), None, False, None),
    "udl_only": (("Verkeer-UDL",), "LG-Verkeer", False, "Standard"),
    "udl_permanent": (("Verkeer-UDL",), "LG-Permanent", False, "Standard"),
    "udl_static": (("Verkeer-UDL",), "LG-Verkeer", False, "Static"),
    "base_static": (("EG", "Verkeer-UDL"), "LG-Verkeer", False, "Static"),
    "fixed_a": (("EG", "Verkeer-UDL"), "LG-Verkeer", True, "Static"),
}


def build_variant(ctx: LlsmContext, variant: str) -> None:
    """The test model with only some load cases, another support at A, or another stored load type."""
    cases, udl_group, fixed, udl_type = VARIANTS[variant]
    t.new_model(ctx, name=f"Brugligger_{variant}")
    t.add_material(ctx, "C30/37", kind="Concrete")
    t.add_rectangular_section(ctx, "R1000x600", material="C30/37", width_mm=1000, height_mm=600)
    for n, x in (("A", 0), ("B", 12000), ("C", 24000)):
        t.add_node(ctx, n, x_mm=x, y_mm=0, z_mm=0)
    t.add_beam(ctx, "Veld1", node_a="A", node_b="B", cross_section="R1000x600")
    t.add_beam(ctx, "Veld2", node_a="B", node_b="C", cross_section="R1000x600")
    (t.add_fixed_support if fixed else t.add_pinned_support)(ctx, "Sup-A", node="A")
    t.add_roller_support(ctx, "Sup-B", node="B", direction="Z")
    t.add_roller_support(ctx, "Sup-C", node="C", direction="Z")
    t.add_load_group(ctx, "LG-Permanent", kind="Permanent")
    if udl_group == "LG-Verkeer":
        t.add_load_group(ctx, "LG-Verkeer", kind="Variable")
    if "EG" in cases:
        t.add_load_case(ctx, "EG", group="LG-Permanent", kind="SelfWeight")
    if "Verkeer-UDL" in cases:
        t.add_load_case(ctx, "Verkeer-UDL", group=udl_group)
        case = ctx.builder.get("Verkeer-UDL")
        case.LoadType = getattr(ctx.builder._adm.Enums.LoadCaseType, udl_type)  # before the push: what SCIA gets
        t.add_line_load(ctx, "q-verkeer-1", beam="Veld1", load_case="Verkeer-UDL", magnitude_kn_per_m=-9, direction="Z")
        t.add_line_load(ctx, "q-verkeer-2", beam="Veld2", load_case="Verkeer-UDL", magnitude_kn_per_m=-9, direction="Z")
    log("variant", variant, "| Verkeer-UDL stored as", udl_type, "|", t.model_summary(ctx).get("items"), "items")


def build(ctx: LlsmContext) -> None:
    """The test model as LLSM wrote it before the load-type fix (Verkeer-UDL variable, type Standard)."""
    build_variant(ctx, "base")


LICENCE_EVENTS: list[dict] = []


def watch_licence() -> None:
    """Record every licence dialog and notice SCIA shows at start-up (text, or a picture of a Qt notice)."""
    from llsm.scia import autoclick as ac
    original_act, original_notice = ac.AutoClicker._act, ac.AutoClicker._on_notice

    def act(self, action, window):
        LICENCE_EVENTS.append({"action": action, "title": window.title, "texts": list(window.texts), "buttons": window.labels})
        return original_act(self, action, window)

    def notice(self, window):
        try:
            pic = ac._capture(window)
        except Exception as e:  # noqa: BLE001
            pic = f"capture failed: {e}"
        LICENCE_EVENTS.append({"action": "notice picture", "picture": str(pic)})
        return original_notice(self, window)

    ac.AutoClicker._act, ac.AutoClicker._on_notice = act, notice


def licence_selection() -> dict:
    """SCIA's own licence selection on this machine (which modules it will claim)."""
    p = Path(os.environ.get("LOCALAPPDATA", "")) / "SCIA" / "Protection" / "UserSelection.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"error": str(e)}
    return {"selected": [x["name"] for x in data.get("licences", []) if x.get("selected")],
            "not_selected": [x["name"] for x in data.get("licences", []) if not x.get("selected")]}


def launch(ctx: LlsmContext, visible: bool = False) -> None:
    watch_licence()
    for attempt in range(6):
        try:
            r = t.launch_scia(ctx, visible=visible)
        except RuntimeError as e:  # a licence check over 3 minutes: HubServer too late; try once more
            log("launch failed:", e)
            if attempt >= 2:
                raise
            time.sleep(10)
            continue
        log("launch", json.dumps(r)[:200])
        if r.get("ok"):
            return
        time.sleep(5)
    raise RuntimeError("SCIA did not start")


def temp_folder() -> Path:
    from llsm.scia.loader import paths
    return paths().temp_folder


def snap() -> dict[str, tuple[int, int]]:
    """Every file under SCIA's temp folder (size, mtime), logs and LLSM's own folder left out."""
    root = temp_folder()
    out: dict[str, tuple[int, int]] = {}
    for d, _, files in os.walk(root):
        for f in files:
            if f.lower().endswith((".log", ".logtmp")):
                continue
            p = os.path.join(d, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out[os.path.relpath(p, root)] = (st.st_size, int(st.st_mtime_ns // 1_000_000))
    return out


def diff(a: dict, b: dict) -> dict[str, int]:
    return {k: v[0] for k, v in b.items() if a.get(k) != v}


def solver_link_sizes(s: dict) -> dict[str, int]:
    return {k: v[0] for k, v in s.items() if k.endswith("SolverLinkData.dat")}


def log_mark() -> dict[str, int]:
    root = temp_folder()
    marks = {}
    for name in ("SciaEngineer.log", "Solver.log", "EsaProcessor.log", "ER.log"):
        p = root / name
        marks[name] = p.stat().st_size if p.exists() else 0
    for p in (root / "llsm").glob("openapi*.log"):
        marks["llsm/" + p.name] = p.stat().st_size
    return marks


def log_since(marks: dict[str, int], max_lines: int = 40) -> dict[str, list[str]]:
    """New log lines since `marks`, SCIA's QML/Qt noise dropped."""
    root = temp_folder()
    out = {}
    names = set(marks) | {"llsm/" + p.name for p in (root / "llsm").glob("openapi*.log")}
    for name in sorted(names):
        p = root / name
        if not p.exists():
            continue
        with open(p, "rb") as fh:
            fh.seek(marks.get(name, 0))
            raw = fh.read()
        text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "replace")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        lines = [ln for ln in lines if not any(n in ln for n in (".qml", "QSortFilterProxyModel", "[UI] [warning]"))]
        if lines:
            out[name] = lines[-max_lines:]
    return out


def forces(ctx: LlsmContext, beam: str, case: str) -> dict:
    """Inner forces through LLSM's own read path, with My at both ends in kNm."""
    r = t.get_beam_inner_forces(ctx, beam, case)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error")}
    mags = r["magnitudes"]
    my = mags.get("My") or next((v for k, v in mags.items() if k.lower() == "my"), None)
    vz = mags.get("Vz") or next((v for k, v in mags.items() if k.lower() == "vz"), None)
    out = {"ok": True, "sections": r["sections"], "magnitudes": list(mags),
           "positions_m": [round(x, 3) for x in r["positions_m"]]}
    if my:
        out["My_kNm_start_end"] = [round(my[0] / 1000, 2), round(my[-1] / 1000, 2)]
        out["My_kNm_min_max"] = [round(min(my) / 1000, 2), round(max(my) / 1000, 2)]
    if vz:
        out["Vz_kN_start_end"] = [round(vz[0] / 1000, 2), round(vz[-1] / 1000, 2)]
    return out


def verdict(files_after_calc: dict, read: dict) -> dict:
    calc_files = {k: v for k, v in files_after_calc.items() if not k.startswith(("ADMsync", "RemotingEsaProcessor", "EP_Storage"))}
    solver = {k: v for k, v in files_after_calc.items() if k.endswith("SolverLinkData.dat")}
    a = bool(calc_files) and (any(v > 999 for v in solver.values()) or len(calc_files) > 2)
    b = bool(read.get("ok"))
    out = {"files_after_calc": len(calc_files), "solver_link_after_calc": solver, "a_files": a, "b_read": b,
           "calculated": a and b}
    if read.get("My_kNm_start_end"):
        my_b = abs(read["My_kNm_start_end"][1])
        out["My_B_kNm"] = my_b
        out["hand_check_1pct"] = abs(my_b - HAND_MY_B_KNM) <= 0.01 * HAND_MY_B_KNM
    return out


def save_result(name: str, data: dict) -> None:
    data = {**data, "licence_events": LICENCE_EVENTS, "licence_selection": licence_selection(), "log": LOG}
    (WORK / f"{name}.json").write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    log("result written to", WORK / f"{name}.json")


def run(name: str, body) -> None:
    """Build context, run `body(ctx, result)`, always close SCIA, write the result."""
    load_scia()
    ctx = LlsmContext()
    result: dict = {"try": name, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        body(ctx, result)
    except BaseException as e:  # noqa: BLE001 - a spike records every failure
        result["exception"] = f"{type(e).__name__}: {e}"
        result["traceback"] = traceback.format_exc()[-1500:]
        log("FAILED", result["exception"])
    finally:
        try:
            log("close", t.close_scia(ctx))
        except Exception as e:  # noqa: BLE001
            log("close failed", e)
        save_result(name, result)
