"""Turn a vehicle, a road path and a step into one load case per position, checked against the model."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..scia.esa_file import LoadModel, Member, Plate
from .path import RoadPath, left_of
from .vehicle import Vehicle

PREFIX = "LLSM-ML "
MAX_CASES = 200
_STEP_TOLERANCE = 0.01  # mm: a step that leaves less than this over still divides the path
_DECK_TOLERANCE = 1.0  # mm: a wheel this close to the deck edge still counts as on it
_AXIS_TOLERANCE = 1.0  # mm: how far the path may run beside a beam deck's axis
_MIN_PLAN_FRACTION = 0.5  # a 2D member steeper than 60 degrees is a wall, not a deck
_MAX_POSITIONS = 100_000  # far past MAX_CASES: a step typed in m instead of mm, not a real request


@dataclass(frozen=True)
class PlannedCase:
    name: str
    front_chainage_mm: float

    @property
    def description(self) -> str:
        return f"LLSM moving load: front axle at {self.front_chainage_mm:.0f} mm along the path"


@dataclass(frozen=True)
class PlannedLoad:
    name: str
    case: str
    value_kn: float  # negative: downward
    x_mm: float
    y_mm: float
    z_mm: float = 0.0
    member: str | None = None  # beam deck: on this member ...
    member_id: int | None = None
    position: float | None = None  # ... at this relative position from its start


@dataclass
class MovingLoadPlan:
    group: str
    vehicle: Vehicle
    deck: str
    cases: list[PlannedCase] = field(default_factory=list)
    loads: list[PlannedLoad] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def loads_of(self, case: str) -> list[PlannedLoad]:
        return [load for load in self.loads if load.case == case]

    def preview_table(self) -> list[dict]:
        rows = []
        for case in self.cases:
            loads = self.loads_of(case.name)
            first = loads[0] if loads else None
            rows.append({
                "case": case.name,
                "front_axle_along_path_mm": round(case.front_chainage_mm, 1),
                "loads": len(loads),
                "total_kN": round(sum(load.value_kn for load in loads), 3),
                "first_load_x_mm": round(first.x_mm, 1) if first else None,
                "first_load_y_mm": round(first.y_mm, 1) if first else None,
            })
        return rows


def front_positions(path_length: float, vehicle_length: float, step: float, last_step: str = "exact") -> tuple[list[float], list[str]]:
    """Chainages of the front axle, from 'whole vehicle on the path' to 'front axle at the end'."""
    problems = []
    if not math.isfinite(step) or step <= 0:
        return [], [f"step_mm must be a positive number, got {step:g}"]
    if vehicle_length > path_length + _STEP_TOLERANCE:
        return [], [f"the vehicle ({vehicle_length:g} mm) is longer than the path ({path_length:g} mm)"]
    travel = max(path_length - vehicle_length, 0.0)
    steps = travel / step
    if steps > _MAX_POSITIONS:
        return [], [f"a step of {step:g} mm gives {steps:.0f} positions; is the step in mm?"]
    whole = math.floor(steps + 1e-9)
    left_over = travel - whole * step
    if step - left_over <= _STEP_TOLERANCE:  # one step short of a whole number of steps, by rounding
        whole, left_over = whole + 1, 0.0
    if left_over > _STEP_TOLERANCE:
        if last_step != "short":
            problems.append(
                f"a step of {step:g} mm does not fit the {travel:g} mm the front axle travels "
                f"(path {path_length:g} mm minus vehicle {vehicle_length:g} mm); "
                f"steps that fit: {', '.join(f'{s:g}' for s in dividing_steps(travel, step))} mm, "
                "or last_step='short' for a shorter last step at the end of the path"
            )
            return [], problems
        return [vehicle_length + k * step for k in range(whole + 1)] + [path_length], problems
    positions = [vehicle_length + k * step for k in range(whole + 1)]
    positions[-1] = min(positions[-1], path_length)  # rounding must not push the last axle off the end
    return positions, problems


def dividing_steps(travel: float, step: float) -> list[float]:
    """The three steps nearest to ``step`` that divide ``travel`` exactly, smallest first.

    Rounded to 0.0001 mm, which still divides within the tolerance for up to MAX_CASES steps.
    """
    if travel <= 0:
        return []
    count = max(1, round(travel / step))
    return sorted({round(travel / n, 4) for n in (count + 1, count, max(1, count - 1))})


def _crosses(point: tuple[float, float], outline: tuple[tuple[float, float], ...]) -> bool:
    """Ray casting: is the point inside the polygon (plan)?"""
    x, y = point
    inside = False
    for (x1, y1), (x2, y2) in zip(outline, outline[1:] + outline[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _near_edge(point: tuple[float, float], outline: tuple[tuple[float, float], ...]) -> bool:
    return any(_edge_distance(point, a, b) <= _DECK_TOLERANCE for a, b in zip(outline, outline[1:] + outline[:1]))


def _inside(point: tuple[float, float], outline: tuple[tuple[float, float], ...]) -> bool:
    """On the deck: inside it, or within the deck tolerance of an edge."""
    return _crosses(point, outline) or _near_edge(point, outline)


def _strictly_inside(point: tuple[float, float], outline: tuple[tuple[float, float], ...]) -> bool:
    """Inside and clear of every edge: two decks that only share an edge do not overlap."""
    return _crosses(point, outline) and not _near_edge(point, outline)


def _edge_distance(p, a, b) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return math.dist(p, (a[0] + t * dx, a[1] + t * dy))


def _deck_outlines(model: LoadModel, deck_outline_mm, deck_z_mm: float) -> tuple[list[tuple[str, tuple, float]], list[str]]:
    """(name, outline in mm, z in mm) per plate to put wheels on."""
    if deck_outline_mm is not None:
        try:
            outline = tuple((float(p[0]), float(p[1])) for p in deck_outline_mm)
        except (TypeError, ValueError, IndexError) as exc:
            return [], [f"deck_outline_mm must be a list of [x, y] points in mm: {exc}"]
        if len(outline) < 3:
            return [], ["deck_outline_mm needs at least three corners"]
        if not all(math.isfinite(v) for point in outline for v in point):
            return [], ["deck_outline_mm has a corner that is not a finite number"]
        return [("given outline", outline, deck_z_mm)], []
    decks, problems = [], []
    for plate in model.plates:
        if not plate.outline:
            problems.append(f"the outline of 2D member {plate.name} could not be read; give deck_outline_mm")
            continue
        if _plan_fraction(plate) < _MIN_PLAN_FRACTION:
            continue  # walls do not carry traffic
        outline = tuple((x * 1000.0, y * 1000.0) for x, y, _ in plate.outline)
        z = sum(p[2] for p in plate.outline) / len(plate.outline) * 1000.0
        decks.append((plate.name, outline, z))
    if not decks and not problems:
        problems.append("the model has no 2D member to drive on (walls do not count); give deck_outline_mm, or use deck='beam'")
    return decks, problems


def _plan_fraction(plate: Plate) -> float:
    """Its area seen from above over its true area: 1 for a flat deck, 0 for a wall."""
    pts = plate.outline
    normal = [0.0, 0.0, 0.0]
    for (x1, y1, z1), (x2, y2, z2) in zip(pts, pts[1:] + pts[:1]):  # Newell's method
        normal[0] += (y1 - y2) * (z1 + z2)
        normal[1] += (z1 - z2) * (x1 + x2)
        normal[2] += (x1 - x2) * (y1 + y2)
    size = math.sqrt(sum(n * n for n in normal))
    return abs(normal[2]) / size if size else 0.0


def _case_names(group: str, count: int) -> list[str]:
    width = max(2, len(str(count)))
    return [f"{group} {k:0{width}d}" for k in range(1, count + 1)]


def build_plan(
    model: LoadModel,
    vehicle: Vehicle,
    path: RoadPath,
    step_mm: float,
    deck: str = "plate",
    member: str | None = None,
    name: str = "TS1",
    lateral_offset_mm: float = 0.0,
    last_step: str = "exact",
    replace: bool = False,
    allow_many: bool = False,
    deck_outline_mm=None,
    deck_z_mm: float = 0.0,
) -> MovingLoadPlan:
    group = f"{PREFIX}{name}".strip()
    plan = MovingLoadPlan(group, vehicle, deck)
    if not name.strip() or any(c in name for c in "\"<>&"):
        plan.problems.append(f"name {name!r} cannot be used in a SCIA name")
        return plan
    if deck not in ("plate", "beam"):
        plan.problems.append(f"deck must be 'plate' or 'beam', got {deck!r}")
        return plan
    if last_step not in ("exact", "short"):
        plan.problems.append(f"last_step must be 'exact' or 'short', got {last_step!r}")
        return plan
    if not (math.isfinite(lateral_offset_mm) and math.isfinite(deck_z_mm)):
        plan.problems.append("lateral_offset_mm and deck_z_mm must be finite numbers")
        return plan
    if deck == "beam" and not member:
        plan.problems.append("deck='beam' needs member: the name of the beam the vehicle drives on")
        return plan

    fronts, problems = front_positions(path.length, vehicle.length_mm, step_mm, last_step)
    plan.problems += problems
    if plan.problems:
        return plan
    if len(fronts) > MAX_CASES and not allow_many:
        plan.problems.append(
            f"{len(fronts)} positions means {len(fronts)} load cases; more than {MAX_CASES} needs allow_many "
            "(or a larger step)"
        )
        return plan

    beam: Member | None = None
    decks: list = []
    model_decks: list = []
    if deck == "beam":
        found = [m for m in model.members if m.name == member]
        if not found:
            names = ", ".join(sorted(m.name for m in model.members)[:20]) or "none"
            plan.problems.append(f"member {member!r} not found; members in the model: {names}")
            return plan
        beam = found[0]
        if vehicle.track_mm:
            plan.warnings.append("On a beam deck both wheels of an axle land on the beam: one load per axle.")
    else:
        if member is not None:
            plan.warnings.append("member is only used with deck='beam'; ignored.")
        decks, problems = _deck_outlines(model, deck_outline_mm, deck_z_mm)
        plan.problems += problems
        if not decks:
            return plan
        # Where SCIA puts a free load does not depend on the outline given here: check overlap
        # against the model's own decks either way.
        model_decks = decks
        if deck_outline_mm is not None:
            model_decks, unread = _deck_outlines(model, None, 0.0)
            if any("could not be read" in u for u in unread):
                plan.warnings.append(
                    "Some 2D members' outlines could not be read, so whether a wheel also lies above one of them "
                    "was not checked."
                )

    off_deck: list[str] = []
    beside: list[str] = []
    stacked: list[str] = []
    for case_name, front in zip(_case_names(group, len(fronts)), fronts):
        plan.cases.append(PlannedCase(case_name, front))
        number = 0
        for axle in vehicle.axles:
            centre, direction = path.at(front - axle.offset_mm)
            side = left_of(direction)
            base = (centre[0] + side[0] * lateral_offset_mm, centre[1] + side[1] * lateral_offset_mm)
            if beam is not None:
                number += 1
                loaded = _on_beam(beam, base)
                if loaded is None:
                    off_deck.append(f"{case_name} axle {number}")
                    continue
                position, distance = loaded
                if distance > _AXIS_TOLERANCE:
                    beside.append(f"{case_name} axle {number} ({distance:.1f} mm)")
                    continue
                plan.loads.append(PlannedLoad(
                    f"{case_name} A{number}", case_name, -axle.load_kn, base[0], base[1],
                    _beam_z(beam, position), beam.name, beam.member_id, position,
                ))
                continue
            wheels = [(-0.5, axle.load_kn / 2), (0.5, axle.load_kn / 2)] if vehicle.track_mm else [(0.0, axle.load_kn)]
            for across, load in wheels:
                number += 1
                width = across * (vehicle.track_mm or 0.0)
                point = (base[0] + side[0] * width, base[1] + side[1] * width)
                on = next((d for d in decks if _inside(point, d[1])), None)
                if on is None:
                    off_deck.append(f"{case_name} W{number} at ({point[0]:.0f}, {point[1]:.0f}) mm")
                    continue
                under = [d[0] for d in model_decks if _strictly_inside(point, d[1])]
                if len(under) > 1:
                    stacked.append(f"{case_name} W{number} above {' and '.join(under)}")
                plan.loads.append(PlannedLoad(f"{case_name} W{number}", case_name, -load, point[0], point[1], on[2]))
    if off_deck:
        where = f"beam {beam.name}" if beam is not None else "the deck"
        plan.problems.append(f"{len(off_deck)} loads fall off {where}: {_listing(off_deck)}")
    if beside:
        plan.problems.append(
            f"{len(beside)} axles are beside beam {beam.name}; for a beam deck the path has to run along its axis: "
            f"{_listing(beside)}"
        )
    if stacked:
        plan.problems.append(
            f"{len(stacked)} wheels lie above more than one 2D member: {_listing(stacked)}. LLSM writes free loads "
            "valid on all 2D members, so SCIA may load each of them, and LLSM cannot yet tie a load to one member."
        )

    _check_names(plan, model, replace)
    return plan


def _on_beam(beam: Member, point: tuple[float, float]) -> tuple[float, float] | None:
    """Relative position along the beam (plan projection) and the distance beside it, in mm."""
    bx, by = beam.begin[0] * 1000.0, beam.begin[1] * 1000.0
    ex, ey = beam.end[0] * 1000.0, beam.end[1] * 1000.0
    dx, dy = ex - bx, ey - by
    length2 = dx * dx + dy * dy
    if length2 < 1.0:
        return None  # a vertical member carries no road
    t = ((point[0] - bx) * dx + (point[1] - by) * dy) / length2
    slack = _DECK_TOLERANCE / math.sqrt(length2)
    if t < -slack or t > 1 + slack:
        return None
    t = min(max(t, 0.0), 1.0)
    return t, math.dist(point, (bx + t * dx, by + t * dy))


def _beam_z(beam: Member, position: float) -> float:
    return (beam.begin[2] + position * (beam.end[2] - beam.begin[2])) * 1000.0


def _check_names(plan: MovingLoadPlan, model: LoadModel, replace: bool) -> None:
    """SCIA overwrites by name. Refuse what would overwrite, unless replace is set.

    Every name LLSM writes starts with "LLSM-ML <name>", so replace can only hit objects of
    that name; that is the whole guard for someone else's objects. What an earlier, longer
    run left behind would not be overwritten and would stay in the group or in a reused case,
    and the update cannot delete it: that is refused too.
    """
    planned = {plan.group, *(c.name for c in plan.cases), *(load.name for load in plan.loads)}
    taken = sorted(planned & model.names())
    if taken and not replace:
        plan.problems.append(
            f"{len(taken)} names already exist in the model ({_shown(taken)}); SCIA would overwrite them. "
            "Use another name, or replace to overwrite what LLSM wrote earlier under this name."
        )
        return
    # Only names of this run's own shape: "<group> 07" and "<group> 07 W3" / "A2". A run called
    # "TS1 B" is another run, not a leftover of "TS1".
    own = re.compile(rf"^{re.escape(plan.group)} \d+( [WA]\d+)?$")
    left = sorted(n for n in model.names() if own.match(n) and n not in planned)
    if replace and left:
        plan.problems.append(
            f"an earlier run under this name left {len(left)} objects this run does not overwrite ({_shown(left)}); "
            "they would stay in the group or in its cases. Delete them in SCIA first, or use another name."
        )


def _shown(names: list[str], limit: int = 6) -> str:
    return ", ".join(names[:limit]) + (f" and {len(names) - limit} more" if len(names) > limit else "")


def _listing(items: list[str], limit: int = 8) -> str:
    return "; ".join(items[:limit]) + (f"; and {len(items) - limit} more" if len(items) > limit else "")
