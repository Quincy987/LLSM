"""Check 4 of docs/verify/phase4-traffic-rules.md: LLSM's cases against the toetsmodel's own generators.

  <toetsmodel venv python> compare_toetsmodel.py dump OUT.json      run the toetsmodel's code (read-only)
  python compare_toetsmodel.py compare OUT.json                      plan the same decks in LLSM and compare

``dump`` imports the toetsmodel from its checkout and calls its tandem generators
(``tandem_systems_theoretical_lanes_bg8000/9000/10000`` and the ``real`` ones) unchanged. Only what they
read from the app's params is stubbed: the supports (``_extract_support_x_coordinates``) and, for real
lanes, the road strip (``obtain_y_coordinates_road``, one road zone). Their deck: x 0..L, y 0..W.

``compare`` builds each deck as a plate x 0..L, y 0..W with the axis along +x at y = W/2, so LLSM's
global coordinates are the toetsmodel's (m -> mm). Per toetsmodel case, matched by its title: the wheel
patches that lie on the deck must be LLSM's patches (corners within 1 mm); a case with no wheel on the
deck must be one LLSM dropped. Every mismatch is listed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

CHECKOUT = Path(r"C:\Development\ctrl+b\automatisch-toetsmodel-plaatbruggen")
DECKS = [
    {"name": "demo 6 x 4 m, t 0.3 m", "L": 6.0, "W": 4.0, "t": 0.3, "supports": None, "lanes": "theoretical"},
    {"name": "20 x 4 m, t 0.8 m, support at 10 m", "L": 20.0, "W": 4.0, "t": 0.8, "supports": [0.0, 10.0, 20.0],
     "lanes": "theoretical"},
    {"name": "12 x 10 m, t 0.5 m", "L": 12.0, "W": 10.0, "t": 0.5, "supports": None, "lanes": "theoretical"},
    {"name": "12 x 10 m, t 0.5 m, real lanes, road y 2.0-8.5 m", "L": 12.0, "W": 10.0, "t": 0.5, "supports": None,
     "lanes": "real", "road": [2.0, 8.5]},
]
TOLERANCE_MM = 1.0


def dump(out: Path) -> int:
    sys.path.insert(0, str(CHECKOUT))
    from src.integrations.scia_integration.load_system import real_tandem_generators as real  # noqa: PLC0415
    from src.integrations.scia_integration.load_system import scia_load_cases  # noqa: PLC0415
    from src.integrations.scia_integration.load_system import theoretical_tandem_generators as theory  # noqa: PLC0415

    class Params:  # what the generators read, and nothing else
        pass

    decks = []
    for deck in DECKS:
        supports = deck["supports"] or []
        scia_load_cases._extract_support_x_coordinates = lambda _params, s=supports: list(s)
        cases = []
        if deck["lanes"] == "theoretical":
            for generator in (theory.tandem_systems_theoretical_lanes_bg8000, theory.tandem_systems_theoretical_lanes_bg9000,
                              theory.tandem_systems_theoretical_lanes_bg10000):
                cases += generator(Params(), deck["L"], deck["W"], deck["t"], 0.0, 0.0)
        else:
            bottom, top = deck["road"]
            real.obtain_y_coordinates_road = lambda _params, t=top, w=top - bottom: (t, w)
            real.get_number_of_road_zones = lambda _params: 1
            for generator in (real.tandem_systems_real_lanes_bg8000, real.tandem_systems_real_lanes_bg9000,
                              real.tandem_systems_real_lanes_bg10000):
                cases += generator(Params(), deck["L"], deck["t"])
        decks.append({**deck, "cases": [{"title": c["title"], "wheels": c["wheels"], "load": c["load"]} for c in cases]})
    out.write_text(json.dumps({"commit": _head(), "decks": decks}, indent=1), encoding="utf-8")
    print(f"{sum(len(d['cases']) for d in decks)} toetsmodel cases for {len(decks)} decks -> {out}")
    return 0


def _head() -> str:
    import subprocess  # noqa: PLC0415
    return subprocess.run(["git", "-C", str(CHECKOUT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()


def compare(dumped: Path) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from llsm.moving_load.path import RoadPath  # noqa: PLC0415
    from llsm.moving_load.plan import clip, polygon_area  # noqa: PLC0415
    from llsm.scia.esa_file import LoadCase, LoadModel, Plate  # noqa: PLC0415
    from llsm.traffic_rules.presets import plan_lm1_lanes  # noqa: PLC0415

    data = json.loads(dumped.read_text(encoding="utf-8"))
    total_mismatches = 0
    for deck in data["decks"]:
        length, width = deck["L"], deck["W"]
        outline = ((0, 0, 0), (length, 0, 0), (length, width, 0), (0, width, 0))
        model = LoadModel([], [], [Plate("D", (1, 2, 3, 4), outline)], [], [LoadCase("BG1", 0)], [])
        axis = width * 500.0  # mm
        path = RoadPath.parse([[0, axis], [length * 1000.0, axis]])
        extra = {}
        if deck["lanes"] == "real":
            extra = {"lanes": "real", "carriageway_mm": [deck["road"][0] * 1000 - axis, deck["road"][1] * 1000 - axis]}
        supports = [s * 1000.0 for s in deck["supports"]] if deck["supports"] else None
        rules = plan_lm1_lanes(model, path, deck["t"] * 1000.0, configurations="ABC", supports_mm=supports, **extra)
        mismatches = list(rules.problems)
        mine = {}
        dropped = set()
        for plan in rules.plans:
            mismatches += plan.problems
            dropped |= set(plan.dropped)
            for case in plan.cases:
                mine[case.title] = [tuple((x / 1000.0, y / 1000.0) for x, y in s.corners_mm) for s in plan.surfaces_of(case.name)]
        deck_square = [(0.0, 0.0), (length, 0.0), (length, width), (0.0, width)]
        written = set(mine)
        theirs = {c["title"]: c["wheels"] for c in deck["cases"]}
        matched = duplicates = gone = 0
        for case in deck["cases"]:
            title = case["title"]
            on_deck = [w for w in case["wheels"] if polygon_area(clip(deck_square, [tuple(c) for c in w])) > 1e-6]
            if title.startswith("rs 1 - Conf. D"):  # written once, as Conf. C's centre tandem
                twin = title.replace("Conf. D", "Conf. C")
                if theirs.get(twin) != case["wheels"]:
                    mismatches.append(f"{title}: not the same wheels as the toetsmodel's {twin}")
                elif twin in written or twin in dropped:
                    duplicates += 1
                else:
                    mismatches.append(f"{title}: its Conf. C twin is not in LLSM's cases")
                continue
            if not on_deck:
                if title in dropped:
                    gone += 1
                else:
                    mismatches.append(f"{title}: no wheel on the deck, but LLSM did not drop it")
                continue
            if title not in mine:
                mismatches.append(f"{title}: not in LLSM's cases")
                continue
            ours = mine.pop(title)
            if len(ours) != len(on_deck):
                mismatches.append(f"{title}: {len(ours)} patches in LLSM, {len(on_deck)} wheels on the deck in the toetsmodel")
                continue
            unmatched = list(ours)
            for wheel in on_deck:
                hit = next((p for p in unmatched if _same_patch(p, wheel)), None)
                if hit is None:
                    mismatches.append(f"{title}: toetsmodel wheel {wheel} has no LLSM patch within {TOLERANCE_MM} mm")
                else:
                    unmatched.remove(hit)
            if not unmatched:
                matched += 1
        mismatches += [f"{t}: in LLSM only" for t in mine]
        total_mismatches += len(mismatches)
        print(f"{deck['name']}: toetsmodel {len(deck['cases'])} cases; matched {matched}, dropped by both {gone}, "
              f"Conf. D centre written once {duplicates}; mismatches {len(mismatches)}")
        for line in mismatches:
            print(f"  MISMATCH {line}")
    print(f"toetsmodel commit {data['commit']}; RESULT: {'PASS' if total_mismatches == 0 else 'FAIL'} ({total_mismatches} mismatches)")
    return 0 if total_mismatches == 0 else 1


def _same_patch(ours, wheel) -> bool:
    a = sorted((round(x, 6), round(y, 6)) for x, y in ours)
    b = sorted((round(x, 6), round(y, 6)) for x, y in wheel)
    return all(abs(p[0] - q[0]) * 1000 <= TOLERANCE_MM and abs(p[1] - q[1]) * 1000 <= TOLERANCE_MM for p, q in zip(a, b))


if __name__ == "__main__":
    command, target = sys.argv[1], Path(sys.argv[2])
    raise SystemExit(dump(target) if command == "dump" else compare(target))
