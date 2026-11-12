"""S3 on a file LLSM wrote: calculate a copy with one linear combination of the given cases
and compare the sum of the column forces with the sum of the loads read back from the file.

  python reactions.py MODEL.esa "LLSM-ML TS1" 3
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "xml_route"))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src"))

from _common import WORK, run_esa_xml, work_copy  # noqa: E402
from llsm.scia.esa_file import read_loads  # noqa: E402
from spike import internal_forces, result_class, write  # noqa: E402
from xmlgen import Ref, combinations, project  # noqa: E402


def main(model: Path, group: str, count: int) -> int:
    loads = read_loads(model)
    cases = [f"{group} {k:02d}" for k in range(1, count + 1)]
    total = -sum(l.value for l in loads.loads if any(l.name.startswith(c + " ") for c in cases))
    refs = [Ref(100 + i, c) for i, c in enumerate(cases)]  # ids that are not in this document: resolved by name?
    xml = project("full.xml.def", combinations([("BGT1_kar", 3, [(r, 1.0) for r in refs])]), result_class("Alle BGT kar", Ref(1, "BGT1_kar")))
    copy = work_copy(model, "reactions_in.esa")
    update = write("reactions", xml)
    result = WORK / "reactions_result.xml"
    result.unlink(missing_ok=True)
    run = run_esa_xml(["LIN", copy, update, f"-x{result}"], "reactions", timeout=600)
    print(run.summary())
    rows = [r for r in internal_forces(result) if r.get("Belasting", "").startswith("BGT1_kar")] if result.exists() else []
    columns: dict[str, set[float]] = {}
    for r in rows:
        if r["Naam"].startswith("C"):
            columns.setdefault(r["Naam"], set()).add(round(float(r["N"].replace(",", ".")), 1))
    print({k: sorted(v) for k, v in columns.items()})
    if len(columns) != 4 or any(len(v) != 1 for v in columns.values()):
        print("RESULT: no usable column forces")
        return 1
    reactions = -sum(next(iter(v)) for v in columns.values())
    print(f"loads read back {total / 1000:.1f} kN; column forces {reactions / 1000:.1f} kN; ratio {reactions / total:.4f}")
    print("RESULT:", "PASS" if abs(reactions / total - 1) <= 0.005 else "FAIL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])))
