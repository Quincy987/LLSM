"""Save a copy through ESA_XML with no change: the baseline for any before/after diff.

Usage: resave.py MODEL.esa LABEL

Saving in this SCIA version upgrades older files and drops mesh and results by
itself, so "before" has to be a plain re-save or the diff blames the update.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import WORK, run_esa_xml, work_copy


def main() -> int:
    model, label = Path(sys.argv[1]), sys.argv[2]
    copy = work_copy(model, f"{label}_in.esa")
    out = WORK / f"{label}_out.esa"
    out.unlink(missing_ok=True)
    run = run_esa_xml(["NONE", copy, "-tESA", f"-o{out}"], label)
    print("log:", run.log[-400:], "stderr:", run.stderr[-400:])
    print("output:", out, out.stat().st_size if out.exists() else None)
    return 0 if run.exit_code == 0 and out.exists() else 1


if __name__ == "__main__":
    raise SystemExit(main())
