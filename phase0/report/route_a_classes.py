"""Does the engineering report show each result class's own results? (plan 13, gate 3)

  python route_a_classes.py

In route_a_calc.py the plate had one combination, and all three result classes of the
template (Alle UGT / Alle BGT / Alle UGT+BGT) listed it, so they printed the same values.
Here: the moving-load spike's 3 cases in two combinations with different coefficients,
  UGT1 (Lineair - UGT, 1.5 x each case)   and   BGT1 (Lineair - BGT, 1.0 x each case),
and the three result classes written by name: Alle UGT = [UGT1], Alle BGT = [BGT1],
Alle UGT+BGT = [UGT1, BGT1]. LIN + save, then NONE -sd -tXLSX -dReport_1 from the saved file.
Expected: column C1 N = -232.68 kN in Alle BGT, -349.02 kN (1.5x) in Alle UGT, and the
UGT+BGT class's extreme = the UGT value. Copies only, SCIA must be closed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "xml_route"))
sys.path.insert(0, str(HERE.parent / "moving_load"))
sys.path.insert(0, str(HERE))

from _common import WORK, run_esa_xml, work_copy  # noqa: E402
from route_a_calc import RESULTS, record  # noqa: E402

OUT = WORK / "report"
LIN_ULS, LIN_SLS = 2, 3  # combination types; 3 printed as "Lineair - BGT" in route_a_calc


def result_classes(classes: list[tuple[str, list]]) -> str:
    from xmlgen import Raw, container, obj

    objs = []
    for i, (name, combos) in enumerate(classes):
        rows = "\n".join(
            f'             <row id="{j}"><p0 i="{c.oid}" n="{c.name}"/><p2 v="1001"/><p3 v="1"/><p4 v="1"/></row>'
            for j, c in enumerate(combos)
        )
        table = Raw(f"""<pX t="">
             <h>
               <h0 t="Load combi ID"/>
               <h1 t="Nonlinear combi ID"/>
               <h2 t="Case_Type"/>
               <h3 t="Case_Model"/>
               <h4 t="Case_tUS"/>
             </h>
{rows}
           </pX>""")
        objs.append(obj(i + 1, name, {0: name, 1: table}))
    return container("{8E5A5442-7217-460C-853D-9D35EC18FAB9}", "DataSetSciaTom.EP_SelectedLC.1",
                     "84DFD127-4444-4378-A414-814FB757AE04", "Result classes", ["Name", "Case"], objs)


def main() -> int:
    from spike import load_containers, write
    from xmlgen import Ref, combinations, project

    base = WORK / "plate_base.esa"
    if not base.exists():
        print("plate_base.esa missing: run phase0/moving_load/make_plate.py first")
        return 1
    parts, cases = load_containers()
    uls, sls = Ref(1, "UGT1"), Ref(2, "BGT1")
    parts += [
        combinations([("UGT1", LIN_ULS, [(c, 1.5) for c in cases]), ("BGT1", LIN_SLS, [(c, 1.0) for c in cases])]),
        result_classes([("Alle UGT", [uls]), ("Alle BGT", [sls]), ("Alle UGT+BGT", [uls, sls])]),
    ]
    update = write("report_classes", project("full.xml.def", *parts))
    copy = work_copy(base, "report/classes_calc_in.esa")
    calc = OUT / "classes_calc.esa"
    calc.unlink(missing_ok=True)
    run = run_esa_xml(["LIN", copy, update, "-tESA", f"-o{calc}"], "report_classes_calc", timeout=900)
    record("classes LIN + save", run, calc)
    if run.exit_code == 0 and calc.exists():
        out = OUT / "classes_Report_1.xlsx"
        out.unlink(missing_ok=True)
        copy = work_copy(calc, "report/classes_xlsx_in.esa")
        run = run_esa_xml(["NONE", copy, "-sd", "-tXLSX", f"-o{out}", "-dReport_1"], "report_classes_xlsx", timeout=1800)
        record("classes NONE -sd -tXLSX -dReport_1", run, out)
    (OUT / "classes_runs.json").write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
