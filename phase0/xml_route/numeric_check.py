"""What unit does the XML stiffness have? Let SCIA calculate and compare with a hand value.

The known pile B1 (D = 0.1 m, C20/25, direction (0, -2.5, -10)) under its own weight.
Its line supports are overwritten by name with one uniform stiffness literal, through
the XML update. SCIA's result tables are in SI (forces in N, so displacements in m).

  q = 2500 kg/m3 * 9.81 * pi/4 * 0.1^2 = 192.6 N/m
  across the pile: q_t = 192.6 * 2.5 / 10.308 = 46.7 N/m

The supports cover only part of the pile, so the hand value treats it as a rigid bar
on those springs: translation plus rotation, read at the two ends. The pile's bending
is ignored, hence the wide tolerance. It only has to tell N/m2 from kN/m2, a factor
1000. Doubling the literal must halve the movement.
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import WORK, read_xml_text, run_esa_xml, work_copy
from parse_members import read_esa
from write_update import Segment, build_update

BASE = WORK / "pile_base.esa"
LENGTH = math.hypot(2.5, 10)
Q_TRANSVERSE = 2500 * 9.81 * math.pi / 4 * 0.1**2 * 2.5 / LENGTH
AXIS = (0.0, -2.5 / LENGTH, -10 / LENGTH)
TOLERANCE = 0.5
# The sample pile has no restraint about its own axis. Left free, the solver returned
# an empty table with exit 0 for some stiffness values.
TORSION = 1000.0


def rigid_bar_ends(k: float, spans: list[tuple[float, float]]) -> tuple[float, float]:
    """Movement across the pile at both ends for a rigid bar on spring segments [x1, x2] (relative)."""
    stiffness = sum(k * (b - a) * LENGTH for a, b in spans)
    centre = sum(k * (b - a) * LENGTH * (a + b) / 2 * LENGTH for a, b in spans) / stiffness
    inertia = sum(
        k * (((b * LENGTH - centre) ** 3 - (a * LENGTH - centre) ** 3) / 3) for a, b in spans
    )
    force = Q_TRANSVERSE * LENGTH
    moment = force * (LENGTH / 2 - centre)
    return force / stiffness - moment / inertia * centre, force / stiffness + moment / inertia * (LENGTH - centre)


def deformation_rows(path: Path) -> list[dict[str, float]]:
    text = read_xml_text(path)
    rows = []
    for table in re.finditer(r'(?s)<table [^>]*name="1D-vervormingen".*?</table>', text):
        body = table.group(0)
        labels = dict(re.findall(r'<h(\d+) t="([^"]+)"/>', body))
        for row in re.finditer(r"(?s)<row .*?</row>", body):
            values = {labels[i]: v for i, v in re.findall(r'<p(\d+) v="([^"]*)"/>', row.group(0)) if i in labels}
            rows.append({k: float(values[k].replace(",", ".")) for k in ("dx", "u_x", "u_y", "u_z")})
    return rows


def transverse(row: dict[str, float]) -> float:
    u = (row["u_x"], row["u_y"], row["u_z"])
    along = sum(a * b for a, b in zip(u, AXIS))
    return math.dist(u, tuple(along * a for a in AXIS))


def calculate(k: float, spans: list[tuple[str, float, float]]) -> list[dict[str, float]] | None:
    segments = tuple(Segment(name, x1, x2, k, TORSION) for name, x1, x2 in spans)
    label = f"unit_{int(k)}"
    copy = work_copy(BASE, f"{label}_in.esa")
    update = WORK / f"{label}_update.xml"
    update.write_text(build_update(segments, "B1", 1), encoding="utf-8")
    result = WORK / f"{label}_result.xml"
    result.unlink(missing_ok=True)
    run = run_esa_xml(["LIN", copy, update, f"-x{result}"], label, timeout=280)
    rows = deformation_rows(result) if result.exists() else []
    print(f"k={k:g}: {run.summary()}, {len(rows)} result rows")
    return rows or None


def main() -> int:
    supports = [s for s in read_esa(BASE)[1] if s.x1 is not None]
    spans = [(s.name, s.x1, s.x2) for s in supports]
    print(f"{len(spans)} supports, covering {min(s[1] for s in spans):.3f} to {max(s[2] for s in spans):.3f}")
    first = calculate(1500.0, spans)
    second = calculate(3000.0, spans)
    if first is None or second is None:
        print("RESULT: no calculation to compare")
        return 1
    on_pile = [(a, min(b, 1.0)) for _, a, b in spans]
    hand = rigid_bar_ends(1500.0, on_pile)
    hand_kn = rigid_bar_ends(1500.0e3, on_pile)
    print(f"hand value at the ends, literal read as N/m2: {hand[0] * 1000:.1f} mm / {hand[1] * 1000:.1f} mm")
    print(f"same, literal read as kN/m2: {hand_kn[0] * 1000:.4f} mm / {hand_kn[1] * 1000:.4f} mm")
    ok = True
    # The same extremes come back once per result class.
    for row in {row["dx"]: row for row in first}.values():
        predicted = hand[0] + (hand[1] - hand[0]) * row["dx"] / LENGTH
        ratio = transverse(row) / abs(predicted)
        print(f"dx={row['dx']:.3f} m: SCIA {transverse(row) * 1000:.1f} mm, hand {predicted * 1000:.1f} mm, SCIA/hand {ratio:.2f}")
        ok = ok and abs(ratio - 1) <= TOLERANCE
    # SCIA reports extremes, at positions that differ per run, so compare the largest movement.
    halved = max(map(transverse, first)) / max(map(transverse, second))
    print(f"largest movement, k=1500 over k=3000: {halved:.2f}")
    ok = ok and abs(halved - 2) <= 0.2
    print("RESULT:", "literal is N/m2" if ok else "does not match the hand value")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
