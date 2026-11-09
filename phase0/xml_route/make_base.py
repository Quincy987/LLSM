"""Build a known pile model from the kademuur template and the sample XML.

Gives the spike a model whose member names and ids are known, next to the real
kademuur copies: `work/pile_base.esa` with member B1. Nothing outside work/ is
written; the template and sample XML are copied first.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import SAMPLE_DEF, SAMPLE_XML, TEMPLATE, WORK, run_esa_xml, work_copy
from parse_members import read_esa


def main() -> int:
    template = work_copy(TEMPLATE, "template.esa")
    shutil.copy2(SAMPLE_XML, WORK / "paaltje.xml")
    shutil.copy2(SAMPLE_DEF, WORK / "viktor.xml.def")
    out = WORK / "pile_base.esa"
    out.unlink(missing_ok=True)
    run = run_esa_xml(
        ["NONE", template, WORK / "paaltje.xml", "-tESA", f"-o{out}"],
        "make_base",
    )
    print("stderr:", run.stderr[-800:])
    print("log:", run.log[-1500:])
    if run.exit_code != 0 or not out.exists():
        print("RESULT: not saved")
        return 1
    members = [m.name for m in read_esa(out)[0]]
    print("RESULT: members", members)
    return 0 if members == ["B1"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
