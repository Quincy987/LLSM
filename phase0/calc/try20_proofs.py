"""Try 20: the live proofs before the material and stability fix is built (docs/verify/calc-run.md).

Every run is ONE SCIA session with ONE model (a failed push leaves SCIA's hub dirty), through LLSM's
own route: push into a copy of the start model, run_analysis (batch LIN with -x), results per case.
Sections are in the corrected order [height, width]; materials get an ADM Quality from SCIA's library.

  python try20_proofs.py p1      # pin + Z rollers WITH extra restraints set by hand; grades C30/37, S 355, C45/55
  python try20_proofs.py p4      # change an existing support's constraints after push (edit sync), recalculate
  python try20_proofs.py p2 [fixed]   # a material NAMED "C30/37" (name = quality)
  python try20_proofs.py p3 [fixed]   # kind contradicting the grade: Steel + C30/37, Concrete + S 235

Hand values, q = 9 kN/m down, 1000 x 600 mm (I = 0.018 m4), E from EN 1992/1993 (C30/37 32.8 GPa,
C45/55 36.3 GPa, S 355 and S 235 210 GPa):
  single span 12 m: M_mid = q L^2 / 8 = 162 kNm; deflection 5 q L^4 / (384 E I); end rotation q L^3 / (24 E I)
  two spans 2 x 12 m, pinned/rollers: M_B = q L^2 / 8 = 162 kNm; largest deflection 0.00542 q L^4 / (E I)
  two spans, A fixed: M_A = -q L^2 / 14 = -92.57 kNm, M_B = -3 q L^2 / 28 = -138.86 kNm
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from common import WORK, launch, log, run, t

W = WORK / "try20"
W.mkdir(exist_ok=True)
STEP = sys.argv[1] if len(sys.argv) > 1 else "p1"
FIXED = "fixed" in sys.argv[2:]

Q = 9.0
L = 12.0
I = 1.0 * 0.6 ** 3 / 12
E = {"C30/37": 32.837e9, "C45/55": 36.283e9, "S 355": 210e9, "S 235": 210e9, "C20/25": 29.962e9}


def hand_single(grade: str) -> dict:
    ei = E[grade] * I
    return {"M_mid_kNm": Q * L ** 2 / 8, "uz_max_mm": 5 * Q * 1e3 * L ** 4 / (384 * ei) * 1e3,
            "rot_end_mrad": Q * 1e3 * L ** 3 / (24 * ei) * 1e3}


def hand_two(grade: str) -> dict:
    ei = E[grade] * I
    return {"M_B_kNm": Q * L ** 2 / 8, "uz_max_mm": 0.00542 * Q * 1e3 * L ** 4 / ei * 1e3}


def material(ctx, name: str, kind: str, grade: str | None) -> None:
    assert t.add_material(ctx, name, kind=kind)["ok"]
    if grade:
        ctx.builder.get(name).Quality = grade
    log("material", name, kind, "quality", ctx.builder.get(name).Quality)


def hold(ctx, support: str, dof: str, kind: str = "Rigid") -> None:
    """Set one global DOF of an existing support: TranslationX..Z, RotationX..Z."""
    b = ctx.builder
    s = b.get(support)
    c = b._constraint_trans(kind) if dof.startswith("Translation") else b._constraint_rot(kind)
    setattr(s, dof, c)
    log("support", support, dof, "->", kind)


def beam_line(ctx, tag: str, points: list[tuple[float, float]], section: str, case: str,
              first: str = "pinned", extras: tuple[tuple[str, str], ...] = ()) -> list[str]:
    """Members through ``points`` (mm, in plan, z = 0), support ``first`` at the first node and Z rollers at
    the others, -9 kN/m in Z on every member in ``case``. ``extras``: (node index letter, DOF) held by hand."""
    nodes = [f"{tag}_{chr(65 + i)}" for i in range(len(points))]
    for n, (x, y) in zip(nodes, points):
        assert t.add_node(ctx, n, x, y, 0)["ok"]
    members = []
    for i in range(len(points) - 1):
        m = f"{tag}_V{i + 1}"
        assert t.add_beam(ctx, m, nodes[i], nodes[i + 1], section)["ok"]
        members.append(m)
    (t.add_fixed_support if first == "fixed" else t.add_pinned_support)(ctx, f"{tag}_S{nodes[0][-1]}", nodes[0])
    for n in nodes[1:]:
        assert t.add_roller_support(ctx, f"{tag}_S{n[-1]}", n, "Z")["ok"]
    assert t.add_load_case(ctx, case, group="LG-proef")["ok"]
    for m in members:
        assert t.add_line_load(ctx, f"{m}_q", beam=m, load_case=case, magnitude_kn_per_m=-Q)["ok"]
    for letter, dof in extras:
        hold(ctx, f"{tag}_S{letter}", dof)
    return members


def calculate(ctx) -> dict:
    out = t.run_analysis(ctx, confirm=True)
    deadline = time.time() + 900
    while out.get("status") == "running" and time.time() < deadline:
        out = t.run_analysis(ctx, confirm=True)
    log("run_analysis", json.dumps({k: v for k, v in out.items() if k not in ("note", "material_note")})[:400])
    return out


def read(ctx, members: list[str], case: str) -> dict:
    """My at every exported position, and the deformation rows of this case (model extremes), in kNm / mm / mrad."""
    cases = (ctx.results or {}).get("cases", {})
    res = cases.get(case, {})
    out = {"members_with_rows": sorted(res)}
    for m in members:
        e = res.get(m, {"forces": [], "deformations": []})
        out[m] = {
            "My_kNm": {round(r["dx_m"], 2): round(r.get("My", 0) / 1e3, 2) for r in e["forces"]},
            "uz_mm": {round(r["dx_m"], 2): round(r.get("Uz", 0) * 1e3, 3) for r in e["deformations"]},
            "fiy_mrad": {round(r["dx_m"], 2): round(r.get("Fiy", 0) * 1e3, 3) for r in e["deformations"]},
        }
    uz = [abs(r.get("Uz", 0)) for m in res.values() for r in m["deformations"]]
    out["uz_extreme_mm"] = round(max(uz) * 1e3, 3) if uz else None
    return out


def start(ctx, name: str) -> None:
    t.new_model(ctx, name=name)
    assert t.add_load_group(ctx, "LG-proef", kind="Variable")["ok"]


def push(ctx, result: dict) -> None:
    launch(ctx)
    p = t.push_model(ctx)
    result["push"] = {k: p.get(k) for k in ("ok", "errors", "items_pushed", "start_model", "calc_estimate_s")}
    log("push", json.dumps(result["push"])[:400])


def p1(ctx, result):
    start(ctx, "proef_p1")
    for grade, kind in (("C30/37", "Concrete"), ("S 355", "Steel"), ("C45/55", "Concrete")):
        material(ctx, f"Q_{grade.replace(' ', '').replace('/', '-')}", kind, grade)
    for grade in ("C30/37", "S 355", "C45/55"):
        mat = f"Q_{grade.replace(' ', '').replace('/', '-')}"
        assert t.add_rectangular_section(ctx, f"{mat}_R", material=mat, width_mm=1000, height_mm=600)["ok"]
    sec = "Q_C30-37_R"
    cos, sin = 0.8660254037844387, 0.5
    plan = {
        # tag: points, section, case, extras (greedy order: translations of the support nodes, then rotations)
        "E": ([(0, 0), (12000, 0)], sec, "LC-enkel", (("B", "TranslationY"), ("A", "RotationX"))),
        "T": ([(0, 30000), (12000, 30000), (24000, 30000)], sec, "LC-twee", (("B", "TranslationY"), ("A", "RotationX"))),
        "H": ([(0, 60000), (12000 * cos, 60000 + 12000 * sin)], sec, "LC-schuin",
              (("B", "TranslationX"), ("A", "RotationX"))),
        "S": ([(0, 90000), (12000, 90000)], "Q_S355_R", "LC-s355", (("B", "TranslationY"), ("A", "RotationX"))),
        "K": ([(0, 120000), (12000, 120000)], "Q_C45-55_R", "LC-c4555", (("B", "TranslationY"), ("A", "RotationX"))),
    }
    members = {tag: beam_line(ctx, tag, pts, s, case, extras=ex) for tag, (pts, s, case, ex) in plan.items()}
    push(ctx, result)
    result["calc"] = calculate(ctx)
    result["read"] = {tag: read(ctx, members[tag], plan[tag][2]) for tag in plan}
    result["hand"] = {"E": hand_single("C30/37"), "T": hand_two("C30/37"), "H": hand_single("C30/37"),
                      "S": hand_single("S 355"), "K": hand_single("C45/55")}
    log("read", json.dumps(result["read"])[:3000])


def p4(ctx, result):
    start(ctx, "proef_p4")
    material(ctx, "Q_Beton", "Concrete", "C30/37")
    assert t.add_rectangular_section(ctx, "Q_R", material="Q_Beton", width_mm=1000, height_mm=600)["ok"]
    members = beam_line(ctx, "P", [(0, 0), (12000, 0), (24000, 0)], "Q_R", "LC-p4", first="fixed")
    push(ctx, result)
    result["fixed"] = {"calc": calculate(ctx), "read": read(ctx, members, "LC-p4")}
    log("A fixed", json.dumps(result["fixed"]["read"])[:800])
    hold(ctx, "P_SA", "RotationY", "Free")   # A: pin, still held about X and Z (rigid-free about Y)
    result["sync_free"] = t._propagate_edit(ctx)
    result["ry_free"] = {"calc": calculate(ctx), "read": read(ctx, members, "LC-p4")}
    log("A Ry free", result["sync_free"], json.dumps(result["ry_free"]["read"])[:800])
    hold(ctx, "P_SA", "RotationY", "Rigid")
    result["sync_rigid"] = t._propagate_edit(ctx)
    result["ry_rigid"] = {"calc": calculate(ctx), "read": read(ctx, members, "LC-p4")}
    log("A Ry rigid again", result["sync_rigid"], json.dumps(result["ry_rigid"]["read"])[:800])
    result["hand"] = {"fixed": {"M_A_kNm": -Q * L ** 2 / 14, "M_B_kNm": -3 * Q * L ** 2 / 28},
                      "pinned": {"M_A_kNm": 0.0, "M_B_kNm": -Q * L ** 2 / 8}}


def _stable_single(ctx, tag, y, section, case):
    if FIXED:
        return beam_line(ctx, tag, [(0, y), (12000, y)], section, case, first="fixed")
    return beam_line(ctx, tag, [(0, y), (12000, y)], section, case, extras=(("B", "TranslationY"), ("A", "RotationX")))


def p2(ctx, result):
    start(ctx, "proef_p2")
    ctx.builder.reserved = frozenset()  # the test: may a material carry a library name?
    ctx.builder.library = ()  # no "(LLSM)" alias: the ADM material is named "C30/37" itself
    material(ctx, "C30/37", "Concrete", "C30/37")
    assert t.add_rectangular_section(ctx, "Q_R", material="C30/37", width_mm=1000, height_mm=600)["ok"]
    members = _stable_single(ctx, "N", 0, "Q_R", "LC-p2")
    push(ctx, result)
    result["calc"] = calculate(ctx)
    result["read"] = read(ctx, members, "LC-p2")
    result["hand"] = hand_single("C30/37") if not FIXED else {"note": "A fixed: propped cantilever"}
    log("read", json.dumps(result["read"])[:1500])


def p3(ctx, result):
    start(ctx, "proef_p3")
    material(ctx, "Q_StaalC", "Steel", "C30/37")
    material(ctx, "Q_BetonS", "Concrete", "S 235")
    assert t.add_rectangular_section(ctx, "Q_RS", material="Q_StaalC", width_mm=1000, height_mm=600)["ok"]
    assert t.add_rectangular_section(ctx, "Q_RB", material="Q_BetonS", width_mm=1000, height_mm=600)["ok"]
    m1 = _stable_single(ctx, "X", 0, "Q_RS", "LC-staalc")
    m2 = _stable_single(ctx, "Y", 30000, "Q_RB", "LC-betons")
    push(ctx, result)
    result["calc"] = calculate(ctx)
    result["read"] = {"steel_kind_C30": read(ctx, m1, "LC-staalc"), "concrete_kind_S235": read(ctx, m2, "LC-betons")}
    result["hand"] = {"C30/37": hand_single("C30/37"), "S 235": hand_single("S 235")}
    log("read", json.dumps(result["read"])[:2000])


if __name__ == "__main__":
    body = {"p1": p1, "p2": p2, "p3": p3, "p4": p4}[STEP]
    name = f"try20_{STEP}" + ("_fixed" if FIXED else "")
    run(name, body)
    src = WORK / f"{name}.json"
    if src.exists():
        (W / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
