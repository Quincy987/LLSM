"""Preview a moving load, write it to a new file, and prove it landed.

The original model is never written to. ``ok`` comes only from reading the saved file
back and comparing every load, case and group; ESA_XML's exit code is not evidence.

Two routes: a vehicle at an even step along a path (one load group), or ``rule='toetsmodel'``: the LM1
tandem in every notional lane at the positions of the plaatbruggen toetsmodel (one load group per lane
and configuration, all written in one update).
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..scia import esa_xml
from ..scia.esa_file import EsaFormatError, LoadModel, Node, Plate, PointLoad, SurfaceLoad, read_loads
from ..scia.load_xml import (
    RELATION, VARIABLE, LoadCaseRow, PointForceRow, SurfaceForceRow, build_groups_update, metre, newton,
)
from ..scia.output_files import mark_failed, next_output_path
from .path import PathError, RoadPath
from .plan import MovingLoadPlan, build_plan
from .vehicle import VehicleError, make_vehicle

_POSITION_TOLERANCE_MM = 0.1
_RELATIVE_TOLERANCE = 1e-9
_RULES = ("toetsmodel",)


@dataclass
class MovingLoadRequest:
    """Everything that decides what gets written, apart from the model."""

    path_mm: list
    step_mm: float | None = None  # the even-step route; not used with a rule
    vehicle_preset: str | None = None
    axles: list[dict] | None = None
    track_mm: float | None = None
    alpha: float = 1.0
    deck: str = "plate"
    member: str | None = None
    name: str | None = None  # default TS1 (even step) or TS (rule)
    lateral_offset_mm: float = 0.0
    last_step: str = "exact"
    replace: bool = False
    allow_many: bool = False
    deck_outline_mm: list | None = None
    deck_z_mm: float = 0.0
    # contact: "point" or "surface" (0.4 x 0.4 m patches, plates only). Default: surface for the LM1
    # tandem (rule or preset) on a plate, point otherwise.
    contact: str | None = None
    # rule='toetsmodel': lanes and positions from the plaatbruggen toetsmodel.
    rule: str | None = None
    configurations: str = "ABC"
    lanes: str = "theoretical"
    thickness_mm: float | None = None
    deck_edges_mm: list | None = None
    carriageway_mm: list | None = None
    supports_mm: list | None = None


def _base_result(action: str, model_path: Path) -> dict:
    return {
        "ok": False,
        "action": action,
        "model": str(model_path),
        "saved_by": None,  # the MCP state block reads this key: the model is a file
        "vehicle": None,
        "group": None,
        "groups": [],
        "deck": None,
        "contact": None,
        "rule": None,
        "planned_cases": 0,
        "planned_loads": 0,
        "problems": [],
        "warnings": [],
        "notes": [],
        "preview": [],
    }


def _read(model_path: Path, reader: Callable[[Path], LoadModel]) -> tuple[LoadModel | None, str | None]:
    try:
        return reader(model_path), None
    except FileNotFoundError:
        return None, f"Model not found: {model_path}"
    except PermissionError:
        return None, f"{model_path.name} is open elsewhere, probably in SCIA. Close it and try again."
    except (EsaFormatError, OSError) as exc:
        return None, f"The model could not be read reliably: {exc}"


def _contact(request: MovingLoadRequest, lm1: bool) -> str:
    if request.contact is not None:
        return request.contact
    return "surface" if lm1 and request.deck == "plate" else "point"


def _prepare(
    action: str, model_path: Path, request: MovingLoadRequest, reader: Callable[[Path], LoadModel]
) -> tuple[dict, LoadModel | None, list[MovingLoadPlan]]:
    result = _base_result(action, model_path)
    try:
        path = RoadPath.parse(request.path_mm)
    except PathError as exc:
        result["problems"] = [str(exc)]
        return result, None, []
    if request.rule is not None:
        return _prepare_rule(result, model_path, request, path, reader)
    try:
        vehicle = make_vehicle(request.vehicle_preset, request.axles, request.track_mm, request.alpha)
    except VehicleError as exc:
        result["problems"] = [str(exc)]
        return result, None, []
    if request.step_mm is None:
        result["problems"] = ["step_mm is needed: how far the front axle moves per case (or rule='toetsmodel')"]
        return result, None, []
    model, problem = _read(model_path, reader)
    if model is None:
        result["problems"] = [problem]
        return result, None, []
    contact = _contact(request, lm1=request.vehicle_preset is not None)
    plan = build_plan(
        model, vehicle, path, request.step_mm, deck=request.deck, member=request.member, name=request.name or "TS1",
        lateral_offset_mm=request.lateral_offset_mm, last_step=request.last_step, replace=request.replace,
        allow_many=request.allow_many, deck_outline_mm=request.deck_outline_mm, deck_z_mm=request.deck_z_mm,
        contact=contact,
    )
    result.update(
        saved_by=model.saved_by,
        vehicle=vehicle.describe(),
        deck=request.deck if request.deck != "beam" else f"beam {request.member}",
        contact=contact,
        warnings=[*model.warnings],
    )
    _summarise(result, [plan], full_preview=True)
    return result, model, [plan]


def _prepare_rule(result, model_path, request, path, reader):
    if request.rule not in _RULES:
        result["problems"] = [f"rule must be one of {', '.join(_RULES)}, got {request.rule!r}"]
        return result, None, []
    if request.deck != "plate":
        result["problems"] = ["rule='toetsmodel' lays out lanes over a plate deck; deck='beam' is not supported"]
        return result, None, []
    if request.vehicle_preset is not None or request.axles is not None or request.step_mm is not None:
        result["problems"] = [("rule='toetsmodel' sets the vehicle (LM1 tandem per lane) and the positions itself: "
                               "leave out vehicle_preset, axles and step_mm")]
        return result, None, []
    if not (math.isfinite(request.alpha) and request.alpha > 0):
        result["problems"] = [f"alpha must be a positive number, got {request.alpha:g}"]
        return result, None, []
    model, problem = _read(model_path, reader)
    if model is None:
        result["problems"] = [problem]
        return result, None, []
    from ..traffic_rules.presets import RULE_SOURCE, plan_lm1_lanes  # here: only this route needs the vendored rules

    contact = _contact(request, lm1=True)
    rules = plan_lm1_lanes(
        model, path, request.thickness_mm, configurations=request.configurations, lanes=request.lanes,
        alpha=request.alpha, name=request.name or "TS", deck_edges_mm=request.deck_edges_mm,
        carriageway_mm=request.carriageway_mm, supports_mm=request.supports_mm, contact=contact,
        replace=request.replace, allow_many=request.allow_many, deck_outline_mm=request.deck_outline_mm,
        deck_z_mm=request.deck_z_mm,
    )
    frame = rules.frame
    result.update(
        saved_by=model.saved_by,
        vehicle=f"LM1 tandem per notional lane (300 / 200 / 100 kN per axle x alpha {request.alpha:g})",
        deck="plate",
        contact=contact,
        rule={
            "source": RULE_SOURCE,
            "lanes": request.lanes,
            "deck_edges_mm": [round(frame.right_edge_mm, 1), round(frame.left_edge_mm, 1)] if frame else None,
            "deck_width_m": round(frame.width_m, 3) if frame else None,
            "thickness_mm": request.thickness_mm,
            "lanes_laid_out": [
                {"group": p.group, "configuration": r.configuration, "lane": r.lane, "y_m": round(r.y_m, 3),
                 "offset_from_axis_mm": round(r.lateral_mm, 1), "deck_from_to_mm": [round(r.entry_mm, 1), round(r.exit_mm, 1)],
                 "positions": len(r.x_m)}
                for r, p in zip(rules.runs, rules.plans)
            ],
        },
        warnings=[*model.warnings],
        notes=[
            *rules.notes,
            ("Loads as written: the real LM1 load per wheel (150 / 100 / 50 kN in lanes 1 / 2 / 3, times alpha). "
             "The toetsmodel writes 100 kN per wheel in every lane and applies its lane factors in the combinations."),
        ],
    )
    result["problems"] = list(rules.problems)
    _summarise(result, rules.plans, full_preview=False)
    return result, model, rules.plans


def _summarise(result: dict, plans: list[MovingLoadPlan], full_preview: bool) -> None:
    """Counts, problems and the table, over all groups."""
    result["group"] = ", ".join(p.group for p in plans) or None
    result["planned_cases"] = sum(len(p.cases) for p in plans)
    result["planned_loads"] = sum(len(p.loads) + len(p.surfaces) for p in plans)
    for plan in plans:
        result["problems"] += [f"{plan.group}: {m}" if len(plans) > 1 else m for m in plan.problems]
        result["warnings"] += [m for m in plan.warnings if m not in result["warnings"]]
        result["groups"].append({
            "group": plan.group, "cases": len(plan.cases), "loads": len(plan.loads) + len(plan.surfaces),
            "left_out": len(plan.left_out), "dropped": len(plan.dropped),
        })
        rows = plan.preview_table()
        if not full_preview and len(rows) > 3:
            rows = [rows[0], rows[len(rows) // 2], rows[-1]]
        result["preview"] += rows
    left_out = sum(len(p.left_out) for p in plans)
    dropped = sum(len(p.dropped) for p in plans)
    if left_out or dropped:
        result["notes"].append(
            f"{left_out} wheels off the deck are left out, zoals het toetsmodel (a vehicle driving onto or off the deck)"
            + (f"; {dropped} positions had no wheel on the deck and are dropped" if dropped else "") + "."
        )
    partly = sum(p.partly_off for p in plans)
    if partly:
        result["notes"].append(
            f"{partly} wheel patches reach beyond the deck edge: the part beyond does not act, as in the toetsmodel, "
            "so a case's total_kN includes force SCIA will not apply."
        )
    if not full_preview:
        result["notes"].append("preview shows the first, middle and last position of each group.")
    contact = result.get("contact")
    if contact == "point" and any(p.vehicle.name.startswith("LM1") for p in plans) and result.get("deck") == "plate":
        result["warnings"].append(
            "Point loads on a plate give unrealistic local peaks under a wheel; contact='surface' writes the "
            "0.4 x 0.4 m contact areas."
        )


def preview_moving_load(
    model_path: Path, request: MovingLoadRequest, reader: Callable[[Path], LoadModel] = read_loads
) -> dict:
    """Work out what would be written. Touches nothing."""
    result, _, _ = _prepare("preview", Path(model_path).resolve(), request, reader)
    result["ok"] = not result["problems"]
    return result


def update_xml(plan: MovingLoadPlan | list[MovingLoadPlan]) -> str:
    """The ESA_XML update for one plan or several (one group each), in SI."""
    plans = plan if isinstance(plan, list) else [plan]
    groups = [(p.group, [LoadCaseRow(c.name, c.description) for c in p.cases]) for p in plans]
    forces = [
        PointForceRow(
            load.name, load.case, newton(load.value_kn), x=metre(load.x_mm), y=metre(load.y_mm), z=metre(load.z_mm),
            member_name=load.member, member_id=load.member_id, position=load.position,
        )
        for p in plans for load in p.loads
    ]
    surfaces = [
        SurfaceForceRow(
            s.name, s.case, newton(s.pressure_kn_m2), tuple((metre(x), metre(y), metre(s.z_mm)) for x, y in s.corners_mm),
        )
        for p in plans for s in p.surfaces
    ]
    return build_groups_update(groups, forces, relation="exclusive", surfaces=surfaces)


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= _RELATIVE_TOLERANCE * max(abs(a), abs(b), 1.0)


def compare(plan: MovingLoadPlan, after: LoadModel) -> tuple[list[str], int, int]:
    """Mismatches, loads confirmed, cases confirmed."""
    mismatches: list[str] = []
    stored = {load.name: load for load in after.loads}
    patches = {s.name: s for s in after.surfaces}
    members = {m.name: m for m in after.members}
    case_ids: dict[str, set[int]] = {}
    load_ids: dict[str, int | None] = {}
    good: set[str] = set()
    for planned in plan.loads:
        found: PointLoad | None = stored.get(planned.name)
        if found is None:
            mismatches.append(f"{planned.name}: not in the saved file")
            continue
        wrong = []
        if not _same(found.value, newton(planned.value_kn)):
            wrong.append(f"value {found.value:g} N, expected {newton(planned.value_kn):g} N")
        if planned.member is None:
            for axis, value, expected in (("x", found.x, planned.x_mm), ("y", found.y, planned.y_mm)):
                if value is None or abs(value * 1000.0 - expected) > _POSITION_TOLERANCE_MM:
                    wrong.append(f"{axis} {value} m, expected {expected / 1000.0:g} m")
        else:
            beam = members.get(planned.member)
            length_mm = beam.length * 1000.0 if beam else 0.0
            if found.member_id is None or (planned.member_id is not None and found.member_id != planned.member_id):
                wrong.append(f"member id {found.member_id}, expected {planned.member_id}")
            if found.position is None or abs((found.position - planned.position) * length_mm) > _POSITION_TOLERANCE_MM:
                wrong.append(f"position {found.position}, expected {planned.position:.6f}")
        case_ids.setdefault(planned.case, set()).add(found.case_id)
        load_ids[planned.name] = found.load_id
        if wrong:
            mismatches.append(f"{planned.name}: " + ", ".join(wrong))
        else:
            good.add(planned.name)
    for planned_patch in plan.surfaces:
        patch: SurfaceLoad | None = patches.get(planned_patch.name)
        if patch is None:
            mismatches.append(f"{planned_patch.name}: not in the saved file")
            continue
        wrong = []
        if not _same(patch.value, newton(planned_patch.pressure_kn_m2)):
            wrong.append(f"value {patch.value:g} N/m2, expected {newton(planned_patch.pressure_kn_m2):g} N/m2")
        if len(patch.corners) != len(planned_patch.corners_mm):
            wrong.append(f"{len(patch.corners)} corners, expected {len(planned_patch.corners_mm)}")
        else:
            off = max(
                max(abs(c[0] * 1000.0 - p[0]), abs(c[1] * 1000.0 - p[1]))
                for c, p in zip(patch.corners, planned_patch.corners_mm)
            )
            if off > _POSITION_TOLERANCE_MM:
                wrong.append(f"a corner {off:.2f} mm from where it was written")
        case_ids.setdefault(planned_patch.case, set()).add(patch.case_id)
        load_ids[planned_patch.name] = patch.load_id
        if wrong:
            mismatches.append(f"{planned_patch.name}: " + ", ".join(wrong))
        else:
            good.add(planned_patch.name)

    cases = {c.name: c for c in after.cases}
    seen_ids: dict[int, str] = {}
    cases_confirmed = 0
    for case in plan.cases:
        record = cases.get(case.name)
        names = [l.name for l in plan.loads_of(case.name)] + [s.name for s in plan.surfaces_of(case.name)]
        ids = case_ids.get(case.name, set())
        problems = []
        if record is None:
            mismatches.append(f"load case {case.name}: not in the saved file")
            continue
        if record.action != VARIABLE:
            problems.append(f"action type {record.action}, expected variable")
        if len(ids) != 1:
            problems.append(f"its loads point at {len(ids)} different load cases")
        else:
            case_id = next(iter(ids))
            if case_id in seen_ids:
                problems.append(f"its loads point at the same case as those of {seen_ids[case_id]}")
            seen_ids[case_id] = case.name
        listed = record.listed_ids(len(names))
        if listed is None:
            problems.append(f"the case does not list {len(names)} loads")
        else:
            own = {load_ids[n] for n in names if load_ids.get(n) is not None}
            if own and own != set(listed):
                problems.append("the case lists other loads than those written")
        if problems:
            mismatches.append(f"load case {case.name}: " + ", ".join(problems))
        elif all(n in good for n in names):
            cases_confirmed += 1

    group = next((g for g in after.groups if g.name == plan.group), None)
    if group is None:
        mismatches.append(f"load group {plan.group}: not in the saved file")
    else:
        wrong = []
        if group.relation != RELATION["exclusive"]:
            wrong.append(f"relation {group.relation}, expected exclusive")
        if group.load != VARIABLE:
            wrong.append(f"load {group.load}, expected variable")
        listed = group.listed_ids(len(plan.cases))
        if listed is None or set(listed) != set(seen_ids):
            wrong.append("it does not hold exactly the cases written")
        if wrong:
            mismatches.append(f"load group {plan.group}: " + ", ".join(wrong))
    return mismatches, len(good), cases_confirmed


def _comparable(item) -> object:
    """What has to stay the same about someone else's object: its values, not SCIA's ids for it."""
    if isinstance(item, PointLoad):
        return (item.value, item.x, item.y, item.member_id, item.position)
    if isinstance(item, SurfaceLoad):
        return (item.value, item.corners)
    if isinstance(item, Node):
        return item.point
    if isinstance(item, Plate):
        return item.outline
    return item  # members, groups and cases compare by value already (id lists are not compared)


def other_changes(before: LoadModel, after: LoadModel, plan: MovingLoadPlan | list[MovingLoadPlan]) -> list[str]:
    """Anything that differs apart from the planned objects."""
    plans = plan if isinstance(plan, list) else [plan]
    planned = set().union(*(p.names() for p in plans))
    changes = []
    for label, old, new in (
        ("member", before.members, after.members),
        ("2D member", before.plates, after.plates),
        ("node", before.nodes, after.nodes),
        ("load group", before.groups, after.groups),
        ("load case", before.cases, after.cases),
        ("load", before.loads, after.loads),
        ("surface load", before.surfaces, after.surfaces),
    ):
        a = {o.name: o for o in old if o.name not in planned}
        b = {o.name: o for o in new if o.name not in planned}
        changes += [f"{label} {name} is gone" for name in sorted(a.keys() - b.keys())]
        changes += [f"{label} {name} appeared" for name in sorted(b.keys() - a.keys())]
        changes += [
            f"{label} {name} changed" for name in sorted(a.keys() & b.keys()) if _comparable(a[name]) != _comparable(b[name])
        ]
    return changes


def apply_moving_load(
    model_path: Path,
    request: MovingLoadRequest,
    out_dir: Path | None = None,
    reader: Callable[[Path], LoadModel] = read_loads,
    writer: Callable[..., esa_xml.RunResult] = esa_xml.run_update,
) -> dict:
    """Write the plan(s) to a new file in one update and report what is really in it."""
    model_path = Path(model_path).resolve()  # ESA_XML runs from its install folder
    result, before, plans = _prepare("apply", model_path, request, reader)
    result.update(output=None, written=0, expected=0, cases_confirmed=0, mismatches=[], other_changes=[], seconds=None, esa_xml=None)
    if not plans or before is None or result["problems"]:
        return result
    if out_dir is not None and not Path(out_dir).is_dir():
        result["problems"] = [f"Output folder not found: {out_dir}"]
        return result

    output = next_output_path(model_path, Path(out_dir).resolve() if out_dir else None)
    started = time.time()
    try:
        run = writer(model_path, output, update_xml(plans), def_file=esa_xml.LOADS_DEF_FILE)
    except (esa_xml.EsaXmlError, OSError) as exc:
        result["problems"] = [str(exc)]
        return result
    result["seconds"] = round(time.time() - started, 1)
    result["esa_xml"] = run.message
    result["expected"] = sum(len(p.loads) + len(p.surfaces) for p in plans)
    if not run.succeeded or not output.exists():
        result["problems"] = [f"SCIA did not save the model: {run.message}. {run.stderr.strip()}".strip()]
        return result

    try:
        after = reader(output)
    except (EsaFormatError, OSError) as exc:
        result["problems"] = [f"The saved file could not be read back: {exc}"]
        result["output"] = str(mark_failed(output))
        return result

    for plan in plans:
        mismatches, confirmed, cases_confirmed = compare(plan, after)
        result["mismatches"] += mismatches
        result["written"] += confirmed
        result["cases_confirmed"] += cases_confirmed
    result["other_changes"] = other_changes(before, after, plans)
    result["ok"] = not result["mismatches"] and not result["other_changes"]
    if result["ok"]:
        result["output"] = str(output)
        cases = result["planned_cases"]
        where = "one exclusive group" if len(plans) == 1 else f"{len(plans)} exclusive groups (one per lane and configuration)"
        result["warnings"] = [
            *result["warnings"],
            f"The {cases} cases sit in {where}, so a combination takes one position at a time. "
            "Make the combinations and recalculate in SCIA: the model changed, so earlier results no longer apply.",
        ]
    else:
        result["output"] = str(mark_failed(output))
    return result
