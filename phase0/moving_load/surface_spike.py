"""Surface-load spike (docs/verify/phase4-traffic-rules.md, S9-S11), on copies of the plate fixture.

  python surface_spike.py s9      one rotated 0.4 x 0.4 m free surface load, 937.5 kN/m2, one update -> s9_out.esa
  python surface_spike.py s10     s9's output with one linear combination, calculated (LIN): column sum vs area x q

The patch is a 0.4 x 0.4 m square centred at (3.0, 2.0) m, rotated 30 degrees in plan. Its area is
0.16 m2 whatever the rotation, so the load is 0.16 x 937.5 = 150 kN.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "xml_route"))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src"))

from _common import WORK, run_esa_xml, work_copy  # noqa: E402
from llsm.scia import esa_xml  # noqa: E402
from llsm.scia.load_xml import LoadCaseRow, SurfaceForceRow, build_load_update  # noqa: E402

PLATE = WORK / "plate_base.esa"
GROUP = "LLSM-ML S9"
CASE = "LLSM-ML S9 01"
CENTRE = (3.0, 2.0)
SIDE = 0.4
ANGLE = math.radians(30.0)
PRESSURE = -937_500.0  # N/m2: 150 kN on 0.16 m2


def corners() -> tuple[tuple[float, float, float], ...]:
    c, s = math.cos(ANGLE), math.sin(ANGLE)
    half = SIDE / 2
    out = []
    for dx, dy in ((-half, -half), (half, -half), (half, half), (-half, half)):
        out.append((round(CENTRE[0] + c * dx - s * dy, 9), round(CENTRE[1] + s * dx + c * dy, 9), 0.0))
    return tuple(out)


def s9() -> int:
    xml = build_load_update(GROUP, [LoadCaseRow(CASE, "spike S9")], [], surfaces=[SurfaceForceRow(f"{CASE} W1", CASE, PRESSURE, corners())])
    (WORK / "s9_update.xml").write_text(xml, encoding="utf-8")
    out = WORK / "s9_out.esa"
    out.unlink(missing_ok=True)
    run = esa_xml.run_update(PLATE, out, xml, def_file=esa_xml.LOADS_DEF_FILE)
    print(run.message, f"{run.seconds:.1f} s" if hasattr(run, "seconds") else "", run.stderr.strip())
    print(run.log[-1500:])
    print("output exists:", out.exists(), "corners (m):", corners())
    return 0 if run.succeeded and out.exists() else 1


def s10() -> int:
    from reactions import main as reactions  # noqa: PLC0415
    return reactions(WORK / "s9_out.esa", GROUP, 1)


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "s9"
    raise SystemExit({"s9": s9, "s10": s10}[command]())
