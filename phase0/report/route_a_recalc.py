"""Does recalculating an old model make its engineering report print results?

  python route_a_recalc.py

The real kademuur copy (saved in v19.1) prints Report_2 with every results item saying
"Geen ondersteunde taak ... v16 en ouder" and the log says "Old result" (route_a_calc.py real).
Here: LIN on a copy and save (-tESA), then NONE -sd -tXLSX -dReport_2 on the saved result.
Copies only, SCIA must be closed (the runner refuses otherwise).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "xml_route"))
sys.path.insert(0, str(HERE))

from _common import WORK, run_esa_xml, work_copy  # noqa: E402
from route_a_calc import REAL, RESULTS, record  # noqa: E402

OUT = WORK / "report"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    calc = OUT / "real_recalc.esa"
    calc.unlink(missing_ok=True)
    copy = work_copy(REAL, "report/real_recalc_in.esa")
    run = run_esa_xml(["LIN", copy, "-tESA", f"-o{calc}"], "report_real_recalc", timeout=3600)
    record("real LIN + save", run, calc)
    if run.exit_code == 0 and calc.exists():
        out = OUT / "real_recalc_Report_2.xlsx"
        out.unlink(missing_ok=True)
        copy = work_copy(calc, "report/real_recalc_xlsx_in.esa")
        run = run_esa_xml(["NONE", copy, "-sd", "-tXLSX", f"-o{out}", "-dReport_2"], "report_real_recalc_xlsx", timeout=1800)
        record("real recalculated, NONE -sd -tXLSX -dReport_2", run, out)
    (OUT / "recalc_runs.json").write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
