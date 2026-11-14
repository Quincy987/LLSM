"""Route A/B on calculated models: does SCIA's engineering report fill with results headlessly?

  python route_a_calc.py plate     build plate_base (if missing), calculate a copy with the
                                   moving-load spike's 12 wheel loads in one combination, put
                                   that combination in the template's existing result class
                                   "Alle UGT" (name is the key), save -> report/plate_calc.esa
  python route_a_calc.py reports   from the SAVED calculated file, no recalculation (NONE):
                                   Report_1 as XLSX and PDF, plus the XML IO export (-x)
  python route_a_calc.py real      a copy of a real kademuur model (v19.1, has 'Report_2'):
                                   NONE -sd XLSX and PDF, and -x (phase 0: exit 9)
  python route_a_calc.py all

SCIA must be closed (the runner refuses otherwise). Copies only, in phase0/xml_route/work/report/.
Hand check for the plate (docs/verify/phase3-moving-load.md S3): column forces
-232.7 / -217.3 / -217.3 / -232.7 kN, sum 900.0 kN.
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

OUT = WORK / "report"
REAL = Path(r"C:\Development\Viktor\LBG_N1c_c0.1_kademuur (+1,94)_gem eindfase.esa")
RESULTS: list[dict] = []


def record(label: str, run, out: Path | None) -> dict:
    size = out.stat().st_size if out and out.exists() else 0
    tail = [ln for ln in run.log.splitlines() if ln.strip()][-4:]
    row = {"label": label, "exit": run.exit_code, "meaning": run.meaning, "seconds": round(run.seconds, 1),
           "output": str(out) if out else None, "bytes": size, "log_tail": tail}
    print(f"  -> {label}: {run.summary()} | output {size} bytes")
    for ln in tail:
        print("     log:", ln[:200])
    RESULTS.append(row)
    return row


def plate() -> int:
    import make_plate
    from spike import load_containers, result_class, write
    from xmlgen import Ref, combinations, project

    base = WORK / "plate_base.esa"
    if not base.exists():
        if make_plate.main() != 0:
            return 1
    OUT.mkdir(parents=True, exist_ok=True)
    parts, cases = load_containers()
    combo = Ref(1, "BGT1_kar")
    parts += [combinations([("BGT1_kar", 3, [(c, 1.0) for c in cases])]), result_class("Alle UGT", combo)]
    update = write("report_plate", project("full.xml.def", *parts))
    copy = work_copy(base, "report/plate_calc_in.esa")
    out = OUT / "plate_calc.esa"
    out.unlink(missing_ok=True)
    run = run_esa_xml(["LIN", copy, update, "-tESA", f"-o{out}"], "report_plate_calc", timeout=900)
    record("plate_calc LIN + save", run, out)
    return 0 if run.exit_code == 0 and out.exists() else 1


def export_set(model: Path, prefix: str, doc: str) -> None:
    for fmt in ("XLSX", "PDF"):
        out = OUT / f"{prefix}_{doc}.{fmt.lower()}"
        out.unlink(missing_ok=True)
        copy = work_copy(model, f"report/{prefix}_{fmt.lower()}_in.esa")
        run = run_esa_xml(["NONE", copy, "-sd", f"-t{fmt}", f"-o{out}", f"-d{doc}"], f"report_{prefix}_{fmt.lower()}", timeout=1800)
        record(f"{prefix} NONE -sd -t{fmt} -d{doc}", run, out)
    out = OUT / f"{prefix}_xmlio.xml"
    out.unlink(missing_ok=True)
    copy = work_copy(model, f"report/{prefix}_x_in.esa")
    run = run_esa_xml(["NONE", copy, f"-x{out}"], f"report_{prefix}_x", timeout=1800)
    record(f"{prefix} NONE -x (actual XML IO document)", run, out)


def reports() -> int:
    model = OUT / "plate_calc.esa"
    if not model.exists():
        print("plate_calc.esa missing: run 'plate' first")
        return 1
    export_set(model, "plate", "Report_1")
    return 0


def real() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    export_set(REAL, "real", "Report_2")
    return 0


def main() -> int:
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    steps = {"plate": [plate], "reports": [reports], "real": [real], "all": [plate, reports, real]}[step]
    code = 0
    for fn in steps:
        code |= fn()
        if code and fn is plate:
            break
    path = OUT / f"calc_runs_{step}.json"
    path.write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")
    print("written", path)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
