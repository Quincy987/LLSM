"""Build the plate-deck fixture for the moving-load spike.

A 6000 x 4000 mm plate, 300 mm thick, C20/25, at z = 0, carried at its four corners by
four 1 m columns (D 400 mm) with fixed feet. The columns are there for S3: the sum of
their axial forces is the sum of the vertical loads, and 1D internal forces are what the
kademuur template's result document exports. Written to work/plate_base.esa from a copy
of the template; nothing else is touched.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "xml_route"))
sys.path.insert(0, str(HERE))

from _common import TEMPLATE, WORK, run_esa_xml, work_copy  # noqa: E402
from xmlgen import Ref, beams, circle_section, fixed_supports, mesh, nodes, plate, project  # noqa: E402

FULL_DEF = HERE.parent / "xml_route" / "defs" / "viktor_full.xml.def"
CORNERS = {"P1": (0.0, 0.0, 0.0), "P2": (6.0, 0.0, 0.0), "P3": (6.0, 4.0, 0.0), "P4": (0.0, 4.0, 0.0)}
FEET = {f"Q{i}": (x, y, -1.0) for i, (x, y, _) in enumerate(CORNERS.values(), start=1)}


def plate_xml() -> str:
    points = {**CORNERS, **FEET}
    ids = {name: i + 1 for i, name in enumerate(points)}
    ref = {name: Ref(ids[name], name) for name in points}
    css = Ref(1, "column_d400")
    columns = [(f"C{i}", ref[f"Q{i}"], ref[f"P{i}"], css) for i in range(1, 5)]
    return project(
        "full.xml.def",
        mesh(0.25),
        circle_section("column_d400", "C20/25", 0.4),
        nodes(points),
        beams(columns),
        plate("D1", "C20/25", 0.3, [ref[n] for n in CORNERS]),
        fixed_supports([(f"Sn{i}", ids[f"Q{i}"], f"Q{i}") for i in range(1, 5)]),
    )


def main() -> int:
    template = work_copy(TEMPLATE, "plate_template.esa")
    shutil.copy2(FULL_DEF, WORK / "full.xml.def")
    xml = WORK / "plate.xml"
    xml.write_text(plate_xml(), encoding="utf-8")
    out = WORK / "plate_base.esa"
    out.unlink(missing_ok=True)
    run = run_esa_xml(["NONE", template, xml, "-tESA", f"-o{out}"], "make_plate")
    print(run.summary())
    print("stderr:", run.stderr[-800:])
    print("log:", run.log[-1500:])
    print("saved:", out.exists(), out.stat().st_size if out.exists() else 0)
    return 0 if run.exit_code == 0 and out.exists() else 1


if __name__ == "__main__":
    raise SystemExit(main())
