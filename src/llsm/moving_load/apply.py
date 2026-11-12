"""Preview a moving load, write it to a new file, and prove it landed.

The original model is never written to. ``ok`` comes only from reading the saved file
back and comparing every load, case and the group; ESA_XML's exit code is not evidence.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..scia import esa_xml
from ..scia.esa_file import EsaFormatError, LoadModel, Node, Plate, PointLoad, read_loads
from ..scia.load_xml import LoadCaseRow, PointForceRow, build_load_update, metre, newton
from ..scia.output_files import mark_failed, next_output_path
from .path import PathError, RoadPath
from .plan import MovingLoadPlan, build_plan
from .vehicle import VehicleError, make_vehicle

_POSITION_TOLERANCE_MM = 0.1
_RELATIVE_TOLERANCE = 1e-9
RELATION_EXCLUSIVE = 1
LOAD_VARIABLE = 1


@dataclass
class MovingLoadRequest:
    """Everything that decides what gets written, apart from the model."""

    path_mm: list
    step_mm: float
    vehicle_preset: str | None = None
    axles: list[dict] | None = None
    track_mm: float | None = None
    alpha: float = 1.0
    deck: str = "plate"
    member: str | None = None
    name: str = "TS1"
    lateral_offset_mm: float = 0.0
    last_step: str = "exact"
    replace: bool = False
    allow_many: bool = False
    deck_outline_mm: list | None = None
    deck_z_mm: float = 0.0


def _base_result(action: str, model_path: Path) -> dict:
    return {
        "ok": False,
        "action": action,
        "model": str(model_path),
        "saved_by": None,  # the MCP state block reads this key: the model is a file
        "vehicle": None,
        "group": None,
        "deck": None,
        "cases": 0,
        "loads": 0,
        "problems": [],
        "warnings": [],
        "preview": [],
    }


def _prepare(
    action: str, model_path: Path, request: MovingLoadRequest, reader: Callable[[Path], LoadModel]
) -> tuple[dict, LoadModel | None, MovingLoadPlan | None]:
    result = _base_result(action, model_path)
    try:
        vehicle = make_vehicle(request.vehicle_preset, request.axles, request.track_mm, request.alpha)
        path = RoadPath.parse(request.path_mm)
    except (VehicleError, PathError) as exc:
        result["problems"] = [str(exc)]
        return result, None, None
    try:
        model = reader(model_path)
    except FileNotFoundError:
        result["problems"] = [f"Model not found: {model_path}"]
        return result, None, None
    except PermissionError:
        result["problems"] = [f"{model_path.name} is open elsewhere, probably in SCIA. Close it and try again."]
        return result, None, None
    except (EsaFormatError, OSError) as exc:
        result["problems"] = [f"The model could not be read reliably: {exc}"]
        return result, None, None

    plan = build_plan(
        model, vehicle, path, request.step_mm, deck=request.deck, member=request.member, name=request.name,
        lateral_offset_mm=request.lateral_offset_mm, last_step=request.last_step, replace=request.replace,
        allow_many=request.allow_many, deck_outline_mm=request.deck_outline_mm, deck_z_mm=request.deck_z_mm,
    )
    result.update(
        saved_by=model.saved_by,
        vehicle=vehicle.describe(),
        group=plan.group,
        deck=request.deck if request.deck != "beam" else f"beam {request.member}",
        cases=len(plan.cases),
        loads=len(plan.loads),
        problems=plan.problems,
        warnings=[*model.warnings, *plan.warnings],
        preview=plan.preview_table(),
    )
    return result, model, plan


def preview_moving_load(
    model_path: Path, request: MovingLoadRequest, reader: Callable[[Path], LoadModel] = read_loads
) -> dict:
    """Work out what would be written. Touches nothing."""
    result, _, _ = _prepare("preview", Path(model_path).resolve(), request, reader)
    result["ok"] = not result["problems"]
    return result


def update_xml(plan: MovingLoadPlan) -> str:
    """The ESA_XML update for a plan, in SI."""
    cases = [LoadCaseRow(c.name, c.description) for c in plan.cases]
    forces = [
        PointForceRow(
            load.name, load.case, newton(load.value_kn), x=metre(load.x_mm), y=metre(load.y_mm), z=metre(load.z_mm),
            member_name=load.member, member_id=load.member_id, position=load.position,
        )
        for load in plan.loads
    ]
    return build_load_update(plan.group, cases, forces, relation="exclusive")


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= _RELATIVE_TOLERANCE * max(abs(a), abs(b), 1.0)


def compare(plan: MovingLoadPlan, after: LoadModel) -> tuple[list[str], int, int]:
    """Mismatches, loads confirmed, cases confirmed."""
    mismatches: list[str] = []
    stored = {load.name: load for load in after.loads}
    members = {m.name: m for m in after.members}
    case_ids: dict[str, set[int]] = {}
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
        if wrong:
            mismatches.append(f"{planned.name}: " + ", ".join(wrong))
        else:
            good.add(planned.name)

    cases = {c.name: c for c in after.cases}
    seen_ids: dict[int, str] = {}
    cases_confirmed = 0
    for case in plan.cases:
        record = cases.get(case.name)
        loads = plan.loads_of(case.name)
        ids = case_ids.get(case.name, set())
        problems = []
        if record is None:
            mismatches.append(f"load case {case.name}: not in the saved file")
            continue
        if record.action != LOAD_VARIABLE:
            problems.append(f"action type {record.action}, expected variable")
        if len(ids) != 1:
            problems.append(f"its loads point at {len(ids)} different load cases")
        else:
            case_id = next(iter(ids))
            if case_id in seen_ids:
                problems.append(f"its loads point at the same case as those of {seen_ids[case_id]}")
            seen_ids[case_id] = case.name
        listed = record.listed_ids(len(loads))
        if listed is None:
            problems.append(f"the case does not list {len(loads)} loads")
        else:
            own = {stored[l.name].load_id for l in loads if l.name in stored and stored[l.name].load_id is not None}
            if own and own != set(listed):
                problems.append("the case lists other loads than those written")
        if problems:
            mismatches.append(f"load case {case.name}: " + ", ".join(problems))
        elif all(load.name in good for load in loads):
            cases_confirmed += 1

    group = next((g for g in after.groups if g.name == plan.group), None)
    if group is None:
        mismatches.append(f"load group {plan.group}: not in the saved file")
    else:
        wrong = []
        if group.relation != RELATION_EXCLUSIVE:
            wrong.append(f"relation {group.relation}, expected exclusive")
        if group.load != LOAD_VARIABLE:
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
    if isinstance(item, Node):
        return item.point
    if isinstance(item, Plate):
        return item.outline
    return item  # members, groups and cases compare by value already (id lists are not compared)


def other_changes(before: LoadModel, after: LoadModel, plan: MovingLoadPlan) -> list[str]:
    """Anything that differs apart from the planned objects."""
    planned = {plan.group, *(c.name for c in plan.cases), *(load.name for load in plan.loads)}
    changes = []
    for label, old, new in (
        ("member", before.members, after.members),
        ("2D member", before.plates, after.plates),
        ("node", before.nodes, after.nodes),
        ("load group", before.groups, after.groups),
        ("load case", before.cases, after.cases),
        ("load", before.loads, after.loads),
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
    """Write the plan to a new file and report what is really in it."""
    model_path = Path(model_path).resolve()  # ESA_XML runs from its install folder
    result, before, plan = _prepare("apply", model_path, request, reader)
    result.update(output=None, written=0, expected=0, cases_confirmed=0, mismatches=[], other_changes=[], seconds=None, esa_xml=None)
    if plan is None or before is None or plan.problems:
        return result
    if out_dir is not None and not Path(out_dir).is_dir():
        result["problems"] = [f"Output folder not found: {out_dir}"]
        return result

    output = next_output_path(model_path, Path(out_dir).resolve() if out_dir else None)
    started = time.time()
    try:
        run = writer(model_path, output, update_xml(plan), def_file=esa_xml.LOADS_DEF_FILE)
    except (esa_xml.EsaXmlError, OSError) as exc:
        result["problems"] = [str(exc)]
        return result
    result["seconds"] = round(time.time() - started, 1)
    result["esa_xml"] = run.message
    result["expected"] = len(plan.loads)
    if not run.succeeded or not output.exists():
        result["problems"] = [f"SCIA did not save the model: {run.message}. {run.stderr.strip()}".strip()]
        return result

    try:
        after = reader(output)
    except (EsaFormatError, OSError) as exc:
        result["problems"] = [f"The saved file could not be read back: {exc}"]
        result["output"] = str(mark_failed(output))
        return result

    mismatches, confirmed, cases_confirmed = compare(plan, after)
    result["mismatches"] = mismatches
    result["written"] = confirmed
    result["cases_confirmed"] = cases_confirmed
    result["other_changes"] = other_changes(before, after, plan)
    result["ok"] = not mismatches and not result["other_changes"]
    if result["ok"]:
        result["output"] = str(output)
        result["warnings"] = [
            *result["warnings"],
            f"The {len(plan.cases)} cases sit in one exclusive group, so a combination takes one position at a time. "
            "Make the combinations and recalculate in SCIA: the model changed, so earlier results no longer apply.",
        ]
    else:
        result["output"] = str(mark_failed(output))
    return result
