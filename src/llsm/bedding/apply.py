"""Preview a bedding, write it to a new file, and prove it landed.

The original model is never written to. ``ok`` comes only from reading the saved file
back and comparing every spring; ESA_XML's exit code is not evidence.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..scia import esa_xml
from ..scia.esa_file import EsaFormatError, EsaModel, LineSupport, read_esa
from .layers import LayerError, parse_layers
from .plan import BeddingPlan, PlannedSegment, build_plan

_RELATIVE_TOLERANCE = 1e-9
_OUTPUT_SUFFIX = re.compile(r"_llsm-\d+$")


@dataclass
class PlanRequest:
    """Everything that decides what gets written, apart from the model and the table."""

    piles: str = "*"
    diameter_m: float | None = None
    variant: str | None = None
    start_level_nap: float | None = None
    datum_offset: float = 0.0
    alpha: float | None = None
    groepsfactor: float | None = None
    replace: bool = False
    allow_existing: bool = False


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= _RELATIVE_TOLERANCE * max(abs(a), abs(b), 1.0)


def _base_result(action: str, model_path: Path, request: PlanRequest) -> dict:
    return {
        "ok": False,
        "action": action,
        "model": str(model_path),
        "saved_by": None,
        "datum_offset": request.datum_offset,
        "start_level_nap": request.start_level_nap,
        "problems": [],
        "warnings": [],
        "piles": [],
        "skipped_piles": [],
        "preview": [],
    }


def _prepare(
    action: str,
    model_path: Path,
    rows: list[dict],
    request: PlanRequest,
    reader: Callable[[Path], EsaModel],
) -> tuple[dict, EsaModel | None, BeddingPlan | None]:
    result = _base_result(action, model_path, request)
    try:
        layers = parse_layers(rows, request.variant)
    except LayerError as exc:
        result["problems"] = exc.problems
        return result, None, None
    try:
        model = reader(model_path)
    except FileNotFoundError:
        result["problems"] = [f"Model not found: {model_path}"]
        return result, None, None
    except PermissionError:
        result["problems"] = [f"{model_path.name} is open elsewhere, probably in SCIA. Close it and try again."]
        return result, None, None
    except EsaFormatError as exc:
        result["problems"] = [f"The model could not be read reliably: {exc}"]
        return result, None, None

    plan = build_plan(
        model,
        layers,
        piles=request.piles,
        diameter_m=request.diameter_m,
        alpha=request.alpha,
        groepsfactor=request.groepsfactor,
        datum_offset=request.datum_offset,
        start_level_nap=request.start_level_nap,
        replace=request.replace,
        allow_existing=request.allow_existing,
    )
    result.update(
        saved_by=model.saved_by,
        problems=plan.problems,
        warnings=plan.warnings,
        piles=plan.piles,
        skipped_piles=plan.skipped_piles,
        preview=plan.preview_table(),
    )
    return result, model, plan


def preview_bedding(
    model_path: Path,
    rows: list[dict],
    request: PlanRequest,
    reader: Callable[[Path], EsaModel] = read_esa,
) -> dict:
    """Work out what would be written. Touches nothing."""
    result, _, _ = _prepare("preview", Path(model_path), rows, request, reader)
    result["ok"] = not result["problems"]
    return result


def next_output_path(model_path: Path, out_dir: Path | None = None) -> Path:
    """``<stem>_llsm-NN.esa`` with the first free number, numbering on from an earlier output."""
    folder = Path(out_dir) if out_dir else model_path.parent
    stem = _OUTPUT_SUFFIX.sub("", model_path.stem)
    number = 1
    while (candidate := folder / f"{stem}_llsm-{number:02d}{model_path.suffix}").exists():
        number += 1
    return candidate


def _compare(planned: list[PlannedSegment], after: dict[str, LineSupport]) -> list[str]:
    mismatches = []
    for item in planned:
        stored = after.get(item.name)
        if stored is None:
            mismatches.append(f"{item.name}: not in the saved file")
            continue
        wrong = []
        if item.pile.member_id is not None and stored.member_id != item.pile.member_id:
            wrong.append(f"member id {stored.member_id}, expected {item.pile.member_id}")
        for label, value, expected in (("x1", stored.x1, item.segment.x1), ("x2", stored.x2, item.segment.x2)):
            if value is None or not _same(value, expected):
                wrong.append(f"{label} {value}, expected {expected}")
        for label, value in (("kY", stored.stiffness_y), ("kZ", stored.stiffness_z)):
            if not _same(value, item.stiffness_n_m2):
                wrong.append(f"{label} {value}, expected {item.stiffness_n_m2}")
        if wrong:
            mismatches.append(f"{item.name}: " + ", ".join(wrong))
    return mismatches


def _other_changes(before: EsaModel, after: EsaModel, planned_names: set[str]) -> list[str]:
    changes = []
    old, new = {s.name: s for s in before.supports}, {s.name: s for s in after.supports}
    for name in sorted(old.keys() - new.keys()):
        changes.append(f"support {name} is gone")
    for name in sorted(new.keys() - old.keys() - planned_names):
        changes.append(f"support {name} appeared")
    for name in sorted((old.keys() & new.keys()) - planned_names):
        if old[name] != new[name]:
            changes.append(f"support {name} changed")
    old_members, new_members = {m.name: m for m in before.members}, {m.name: m for m in after.members}
    for name in sorted(old_members.keys() ^ new_members.keys()):
        changes.append(f"member {name} appeared or disappeared")
    for name in sorted(old_members.keys() & new_members.keys()):
        if old_members[name] != new_members[name]:
            changes.append(f"member {name} changed")
    return changes


def apply_bedding(
    model_path: Path,
    rows: list[dict],
    request: PlanRequest,
    out_dir: Path | None = None,
    reader: Callable[[Path], EsaModel] = read_esa,
    writer: Callable[[Path, Path, str], esa_xml.RunResult] = esa_xml.run_update,
) -> dict:
    """Write the plan to a new file and report what is really in it."""
    model_path = Path(model_path)
    result, before, plan = _prepare("apply", model_path, rows, request, reader)
    result.update(output=None, written=0, expected=0, mismatches=[], other_changes=[], seconds=None, esa_xml=None)
    if plan is None or before is None or plan.problems:
        return result
    if not plan.planned:
        result["problems"] = ["Nothing to write: every selected pile was skipped."]
        return result

    output = next_output_path(model_path, out_dir)
    supports = [
        esa_xml.UpdateSupport(p.name, p.pile.name, p.pile.member_id, p.segment.x1, p.segment.x2, p.stiffness_n_m2)
        for p in plan.planned
    ]
    started = time.time()
    try:
        run = writer(model_path, output, esa_xml.build_update(supports))
    except esa_xml.EsaXmlError as exc:
        result["problems"] = [str(exc)]
        return result
    result["seconds"] = round(time.time() - started, 1)
    result["esa_xml"] = run.message
    result["expected"] = len(plan.planned)
    if not run.succeeded or not output.exists():
        result["problems"] = [f"SCIA did not save the model: {run.message}. {run.stderr.strip()}".strip()]
        return result

    try:
        after = reader(output)
    except EsaFormatError as exc:
        result["problems"] = [f"The saved file could not be read back: {exc}"]
        result["output"] = str(_mark_failed(output))
        return result

    stored = {s.name: s for s in after.supports}
    planned_names = {p.name for p in plan.planned}
    result["mismatches"] = _compare(plan.planned, stored)
    result["other_changes"] = _other_changes(before, after, planned_names)
    result["written"] = len(plan.planned) - len(result["mismatches"])
    result["ok"] = not result["mismatches"] and not result["other_changes"]
    if result["ok"]:
        result["output"] = str(output)
        result["warnings"] = [*result["warnings"], "Recalculate in SCIA: saving drops the mesh and results."]
    else:
        result["output"] = str(_mark_failed(output))
    return result


def _mark_failed(output: Path) -> Path:
    """Keep a bad file for inspection under a name nobody will mistake for a result."""
    failed = output.with_name(f"{output.stem}_FAILED{output.suffix}")
    failed.unlink(missing_ok=True)
    output.rename(failed)
    return failed
