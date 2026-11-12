"""Moving-load spike: can ESA_XML write load groups, cases and point loads into a saved model?

  python spike.py s1      group + 3 cases + 12 free point loads on the plate fixture -> s1_out.esa
  python spike.py s3      same loads, plus one linear combination of the three cases, calculated
                          (LIN) and exported -> s3_result.xml; column N sum vs load sum
  python spike.py s4      point loads on beam B1 of pile_base by name + relative x -> s4_out.esa
  python spike.py s5      plain re-save of the plate fixture (control for the diff)
  python spike.py s7      write s1's update again on s1_out with one value changed -> s7_out.esa
  python spike.py s8      envelope combination of the three cases -> s8_out.esa

Hand case (docs/10 section 4): axles 200 kN (front) and 100 kN 1200 mm behind, track 2000 mm,
path (500, 2000) -> (4900, 2000) mm, step 1600 mm.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "xml_route"))
sys.path.insert(0, str(HERE))

from _common import WORK, read_xml_text, run_esa_xml, work_copy  # noqa: E402
from xmlgen import (Raw, Ref, beam_point_forces, combinations, container, free_point_forces,  # noqa: E402
                    load_cases, load_groups, obj, project)

PLATE = WORK / "plate_base.esa"
PILE_SOURCE = Path(r"C:\Development\LLSM\.worktrees\t-405\phase0\xml_route\work\pile_base.esa")
FULL_DEF = HERE.parent / "xml_route" / "defs" / "viktor_full.xml.def"

GROUP = "LLSM-ML TS1"
RELATION_EXCLUSIVE = 1  # to be confirmed in SCIA: 0 standard, 1 exclusive, 2 together (expected)
LOAD_VARIABLE = 1  # expected: 0 permanent, 1 variable
ACTION_VARIABLE = 1
LOAD_TYPE_STATIC = 0

FRONT_X = (1.7, 3.3, 4.9)
REAR_OFFSET = 1.2
WHEEL_Y = (1.0, 3.0)
FRONT_WHEEL_N = -100_000.0
REAR_WHEEL_N = -50_000.0


def case_names() -> list[str]:
    return [f"{GROUP} {i:02d}" for i in range(1, 4)]


def wheel_loads(cases: list[Ref]) -> list[tuple[str, Ref, float, float, float, float]]:
    loads = []
    for case, x in zip(cases, FRONT_X):
        wheel = 0
        for axle_x, force in ((x, FRONT_WHEEL_N), (x - REAR_OFFSET, REAR_WHEEL_N)):
            for y in WHEEL_Y:
                wheel += 1
                loads.append((f"{case.name} W{wheel}", case, force, round(axle_x, 6), y, 0.0))
    return loads


def load_containers(group_relation: int = RELATION_EXCLUSIVE) -> tuple[list[str], list[Ref]]:
    group = Ref(1, GROUP)
    cases = [Ref(i + 1, n) for i, n in enumerate(case_names())]
    parts = [
        load_groups([(GROUP, group_relation, LOAD_VARIABLE)]),
        load_cases([(c.name, ACTION_VARIABLE, f"front axle at {x * 1000:.0f} mm", group, LOAD_TYPE_STATIC)
                    for c, x in zip(cases, FRONT_X)]),
        free_point_forces(wheel_loads(cases)),
    ]
    return parts, cases


def result_class(name: str, combo: Ref) -> str:
    """A result class holding one combination, as paaltje.xml's 'Alle BGT kar'."""
    rows = Raw(f"""<pX t="">
             <h>
               <h0 t="Load combi ID"/>
               <h1 t="Nonlinear combi ID"/>
               <h2 t="Case_Type"/>
               <h3 t="Case_Model"/>
               <h4 t="Case_tUS"/>
             </h>
             <row id="0">
               <p0 i="{combo.oid}" n="{combo.name}"/>
               <p2 v="1001"/>
               <p3 v="1"/>
               <p4 v="1"/>
             </row>
           </pX>""")
    return container("{8E5A5442-7217-460C-853D-9D35EC18FAB9}", "DataSetSciaTom.EP_SelectedLC.1",
                     "84DFD127-4444-4378-A414-814FB757AE04", "Result classes", ["Name", "Case"],
                     [obj(1, name, {0: name, 1: rows})])


def write(name: str, xml: str) -> Path:
    path = WORK / f"{name}_update.xml"
    path.write_text(xml, encoding="utf-8")
    shutil.copy2(FULL_DEF, WORK / "full.xml.def")
    return path


def run_save(label: str, source: Path, xml: str) -> int:
    copy = work_copy(source, f"{label}_in.esa")
    update = write(label, xml)
    out = WORK / f"{label}_out.esa"
    out.unlink(missing_ok=True)
    run = run_esa_xml(["NONE", copy, update, "-tESA", f"-o{out}"], label)
    print(run.summary(), "| saved:", out.exists())
    if run.log.strip():
        print("log:", run.log[-1200:])
    return 0 if run.exit_code == 0 and out.exists() else 1


def s1() -> int:
    parts, _ = load_containers()
    return run_save("s1", PLATE, project("full.xml.def", *parts))


def s3() -> int:
    parts, cases = load_containers()
    combo = Ref(1, "BGT1_kar")
    parts += [combinations([("BGT1_kar", 3, [(c, 1.0) for c in cases])]), result_class("Alle BGT kar", combo)]
    copy = work_copy(PLATE, "s3_in.esa")
    update = write("s3", project("full.xml.def", *parts))
    result = WORK / "s3_result.xml"
    result.unlink(missing_ok=True)
    run = run_esa_xml(["LIN", copy, update, f"-x{result}"], "s3", timeout=600)
    print(run.summary(), "| result:", result.exists())
    if not result.exists():
        return 1
    return check_s3(result)


def internal_forces(path: Path) -> list[dict[str, str]]:
    text = read_xml_text(path)
    rows = []
    for table in re.finditer(r'(?s)<table [^>]*name="Interne 1D-krachten".*?</table>', text):
        body = table.group(0)
        labels = dict(re.findall(r'<h(\d+) t="([^"]+)"/>', body))
        for row in re.finditer(r"(?s)<row .*?</row>", body):
            rows.append({labels[i]: v for i, v in re.findall(r'<p(\d+) v="([^"]*)"/>', row.group(0)) if i in labels})
    return rows


def check_s3(result: Path) -> int:
    rows = [r for r in internal_forces(result) if r.get("Belasting", "").startswith("BGT1_kar")]
    by_member: dict[str, set[float]] = {}
    for r in rows:
        by_member.setdefault(r["Naam"], set()).add(round(float(r["N"].replace(",", ".")), 1))
    print(json.dumps({k: sorted(v) for k, v in by_member.items()}, indent=1))
    loads = -sum(load[2] for load in wheel_loads([Ref(i + 1, n) for i, n in enumerate(case_names())]))
    columns = {k: v for k, v in by_member.items() if k.startswith("C")}
    if len(columns) != 4 or any(len(v) != 1 for v in columns.values()):
        print("RESULT: N not constant per column, or not all 4 columns reported")
        return 1
    total = -sum(next(iter(v)) for v in columns.values())
    print(f"sum of loads {loads / 1000:.1f} kN, sum of column N {total / 1000:.1f} kN, ratio {total / loads:.4f}")
    ok = abs(total / loads - 1) <= 0.005
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def s4() -> int:
    pile = work_copy(PILE_SOURCE, "pile_base.esa")
    group = Ref(1, "LLSM-ML S4")
    case = Ref(1, "LLSM-ML S4 01")
    parts = [
        load_groups([(group.name, RELATION_EXCLUSIVE, LOAD_VARIABLE)]),
        load_cases([(case.name, ACTION_VARIABLE, "beam point loads", group, LOAD_TYPE_STATIC)]),
        beam_point_forces([("LLSM-ML S4 01 A1", case, 1, "B1", -10_000.0, 0.25),
                           ("LLSM-ML S4 01 A2", case, 1, "B1", -20_000.0, 0.75)]),
    ]
    return run_save("s4", pile, project("full.xml.def", *parts))


def s5() -> int:
    return run_save("s5", PLATE, project("full.xml.def"))


def s7() -> int:
    """Same names again on s1's output, one wheel changed: overwrite or duplicate?"""
    parts, cases = load_containers()
    loads = wheel_loads(cases)
    name, case, _f, x, y, z = loads[0]
    loads[0] = (name, case, -123_000.0, x, y, z)
    parts[2] = free_point_forces(loads)
    return run_save("s7", WORK / "s1_out.esa", project("full.xml.def", *parts))


def s8() -> int:
    parts, cases = load_containers()
    parts.append(combinations([("LLSM-ML TS1 envelope", 0, [(c, 1.0) for c in cases])]))
    return run_save("s8", PLATE, project("full.xml.def", *parts))




def probe_enums() -> int:
    """Which byte of a group record is Relation and which is Load; where a free load keeps z."""
    group = Ref(1, "LLSM-PROBE G")
    case = Ref(1, "LLSM-PROBE C")
    parts = [
        load_groups([(group.name, 2, 0)]),
        load_cases([(case.name, 0, "probe", group, 1)]),
        free_point_forces([("LLSM-PROBE F", case, -7000.0, 2.25, 1.75, 0.5)]),
    ]
    return run_save("probe", PLATE, project("full.xml.def", *parts))


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "s1"
    raise SystemExit({"s1": s1, "s3": s3, "s4": s4, "s5": s5, "s7": s7, "s8": s8, "probe": probe_enums}[step]())
