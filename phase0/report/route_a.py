"""Route A spike: can ESA_XML print SCIA's own engineering report from a saved model?

  python route_a.py MODEL.esa LABEL [CALC] [DOC] [only,these,runs]

Runs, each on a fresh copy in phase0/xml_route/work/report/, SCIA closed:
  sd_xlsx_default : -sd -tXLSX -o<out>              (no -d: the "actual" document)
  sd_xlsx_named   : -sd -tXLSX -o<out> -d<DOC>      (by name; default DOC = Report_1)
  sd_pdf_named    : -sd -tPDF  -o<out> -d<DOC>
  sd_rtf_named    : -sd -tRTF  -o<out> -d<DOC>
  sd_xlsx_wrong   : -sd -tXLSX -o<out> -dNO_SUCH_DOC (must fail loudly)
  old_txt_default : -tTXT -o<out>                   (old-style document, no -sd)
Prints exit code, seconds, output size and the last log lines; writes <LABEL>_runs.json.
Document names in a model: esa_docs.py MODEL.esa.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "xml_route"))
from _common import WORK, run_esa_xml, work_copy  # noqa: E402

OUT = WORK / "report"


def one(model: Path, label: str, calc: str, switches: list[str], suffix: str, timeout: float = 900) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    copy = work_copy(model, f"report/{label}_in.esa")
    out = OUT / f"{label}.{suffix}"
    out.unlink(missing_ok=True)
    args = [calc, copy, *[s.replace("{out}", str(out)) for s in switches]]
    run = run_esa_xml(args, f"report_{label}", timeout=timeout)
    size = out.stat().st_size if out.exists() else 0
    tail = [ln for ln in run.log.splitlines() if ln.strip()][-6:]
    print(f"  -> {label}: {run.summary()} | output {size} bytes")
    for ln in tail:
        print("     log:", ln[:200])
    return {"label": label, "args": [str(a) for a in args], "exit": run.exit_code, "meaning": run.meaning,
            "seconds": round(run.seconds, 1), "output": str(out), "bytes": size, "log_tail": tail,
            "dialogs": run.dialogs}


def main() -> int:
    model, prefix = Path(sys.argv[1]), sys.argv[2]
    calc = sys.argv[3] if len(sys.argv) > 3 else "NONE"
    doc = sys.argv[4] if len(sys.argv) > 4 else "Report_1"
    plan = [
        ("sd_xlsx_default", ["-sd", "-tXLSX", "-o{out}"], "xlsx"),
        ("sd_xlsx_named", ["-sd", "-tXLSX", "-o{out}", f"-d{doc}"], "xlsx"),
        ("sd_pdf_named", ["-sd", "-tPDF", "-o{out}", f"-d{doc}"], "pdf"),
        ("sd_rtf_named", ["-sd", "-tRTF", "-o{out}", f"-d{doc}"], "rtf"),
        ("sd_xlsx_wrong", ["-sd", "-tXLSX", "-o{out}", "-dNO_SUCH_DOC"], "xlsx"),
        ("old_txt_default", ["-tTXT", "-o{out}"], "txt"),
    ]
    only = set(sys.argv[5].split(",")) if len(sys.argv) > 5 else None
    results = [one(model, f"{prefix}_{name}", calc, sw, suffix)
               for name, sw, suffix in plan if not only or name in only]
    path = OUT / f"{prefix}_runs.json"
    path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("written", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
