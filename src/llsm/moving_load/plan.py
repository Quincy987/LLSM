"""Turn a vehicle, a road path and a step into one load case per position, checked against the model."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..scia.esa_file import LoadModel, Member, Plate
from .path import RoadPath, left_of
from .vehicle import Vehicle

PREFIX = "LLSM-ML "
MAX_CASES = 200
_STEP_TOLERANCE = 0.01  # mm: a step that leaves less than this over still divides the path
_DECK_TOLERANCE = 1.0  # mm: a wheel this close to the deck edge still counts as on it
_AXIS_TOLERANCE = 1.0  # mm: how far the path may run beside a beam deck's axis


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
                "front_axle_mm": round(case.front_chainage_mm, 1),
                "loads": len(loads),
                "total_kN": round(sum(load.value_kn for load in loads), 3),
                "first_load_x_mm": round(first.x_mm, 1) if first else None,
                "first_load_y_mm": round(first.y_mm, 1) if first else None,
            })
        return rows


def front_positions(path_length: float, vehicle_length: float, step: float, last_step: str = "exact") -> tuple[list[float], list[str]]:
    """Chainages of the front axle, from 'whole vehicle on the path' to 'front axle at the end'."""
    problems = []
    if step <= 0:
        return [], [f"step_mm must be positive, got {step:g}"]
    if vehicle_length > path_length + _STEP_TOLERANCE:
        return [], [f"the vehicle ({vehicle_length:g} mm) is longer than the path ({path_length:g} mm)"]
    travel = max(path_length - vehicle_length, 0.0)
    steps = travel / step
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


def _inside(point: tuple[float, float], outline: tuple[tuple[float, float], ...]) -> bool:
    """Point in polygon (plan), or within the deck tolerance of an edge."""
    x, y = point
    inside = False
    for (x1, y1), (x2, y2) in zip(outline, outline[1:] + outline[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside or any(_edge_distance(point, a, b) <= _DECK_TOLERANCE for a, b in zip(outline, outline[1:] + outline[:1]))


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
        return [("given outline", outline, deck_z_mm)], []
    decks, problems = [], []
    for plate in model.plates:
        if not plate.outline:
            problems.append(f"the outline of 2D member {plate.name} could not be read; give deck_outline_mm")
            continue
        if not _horizontal(plate):
            continue  # walls do not carry traffic
        outline = tuple((x * 1000.0, y * 1000.0) for x, y, _ in plate.outline)
        z = sum(p[2] for p in plate.outline) / len(plate.outline) * 1000.0
        decks.append((plate.name, outline, z))
    if not decks and not problems:
        problems.append("the model has no horizontal 2D member to drive on; give deck_outline_mm, or use deck='beam'")
    return decks, problems


def _horizontal(plate: Plate) -> bool:
    zs = [p[2] for p in plate.outline]
    return max(zs) - min(zs) <= 0.001  # m


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

    off_deck: list[str] = []
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
                    plan.problems.append(
                        f"{case_name} axle {number} is {distance:.1f} mm beside beam {beam.name}; "
                        "for a beam deck the path has to run along its axis"
                    )
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
                plan.loads.append(PlannedLoad(f"{case_name} W{number}", case_name, -load, point[0], point[1], on[2]))
    if off_deck:
        shown = "; ".join(off_deck[:8]) + (f"; and {len(off_deck) - 8} more" if len(off_deck) > 8 else "")
        where = f"beam {beam.name}" if beam is not None else "the deck"
        plan.problems.append(f"{len(off_deck)} loads fall off {where}: {shown}")

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
    """SCIA overwrites by name. Refuse what would overwrite, unless it is ours and replace is set."""
    planned = {plan.group, *(c.name for c in plan.cases), *(load.name for load in plan.loads)}
    taken = sorted(planned & model.names())
    if taken and not replace:
        shown = ", ".join(taken[:6]) + (f" and {len(taken) - 6} more" if len(taken) > 6 else "")
        plan.problems.append(
            f"{len(taken)} names already exist in the model ({shown}); SCIA would overwrite them. "
            "Use another name, or replace to overwrite what LLSM wrote earlier."
        )
        return
    if taken and replace:
        foreign = [n for n in taken if not n.startswith(PREFIX)]
        if foreign:
            plan.problems.append(f"replace only overwrites LLSM's own objects; these are not: {', '.join(foreign[:6])}")
            return
        mine = f"{plan.group} "
        left = sorted(n for n in model.names() if n.startswith(mine) and n not in planned)
        if left:
            shown = ", ".join(left[:6]) + (f" and {len(left) - 6} more" if len(left) > 6 else "")
            plan.warnings.append(
                f"{len(left)} objects of an earlier run stay in the model, because this run does not overwrite them: {shown}"
            )
