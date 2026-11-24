"""Calculate a saved copy of a model with SCIA's batch program, and read the results it exports.

One ESA_XML run does it all: a linear combination and a result class per load case are added
(build_case_classes_update), the copy is calculated (LIN) and the results are exported (-x). The
calculation and the export must be one run: a copy saved after LIN (-tESA) keeps no results
(docs/verify/calc-run.md). The export holds what the start model's XML IO document asks for: per
result class, 1D internal forces and 1D deformations at SCIA's extreme sections (member ends and
extremes), not at every section.
"""
from __future__ import annotations

import functools
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from . import esa_xml
from .load_xml import build_case_classes_update
from .xmlio import Table, parse_xmlio

Runner = Callable[..., esa_xml.RunResult]
FORCES_TABLE = "Interne 1D-krachten"
DEFORMATIONS_TABLE = "1D-vervormingen"
# Export column -> the magnitude names the result tools have always used.
FORCE_COLUMNS = {"N": "N", "V_y": "Vy", "V_z": "Vz", "M_x": "Mx", "M_y": "My", "M_z": "Mz"}
DEFORMATION_COLUMNS = {"u_x": "Ux", "u_y": "Uy", "u_z": "Uz", "f_x": "Fix", "f_y": "Fiy", "f_z": "Fiz"}

# Seconds for mesh + save + batch, with LLSM's own SCIA open: a least-squares fit to beam grids of
# 2 / 50 / 200 members with 2 / 10 load cases, 34.5 s to 150.4 s, within 8 s of each (docs/verify/calc-run.md).
ESTIMATE_BASE_S = 26.4
ESTIMATE_PER_MEMBER_S = 0.41
ESTIMATE_PER_CASE_S = 2.54
ESTIMATE_PER_MEMBER_CASE_S = 0.0093
CONFIRM_ABOVE_S = 120.0


def estimate_seconds(members: int, cases: int) -> int:
    """How long run_analysis takes for a model of this size (mesh, save, batch calculation)."""
    seconds = (ESTIMATE_BASE_S + ESTIMATE_PER_MEMBER_S * members + ESTIMATE_PER_CASE_S * cases
               + ESTIMATE_PER_MEMBER_CASE_S * members * cases)
    return int(round(seconds / 5.0) * 5) or 5


def calc_timeout(model_path: Path) -> float:
    """Seconds allowed for LIN (and an export or a save): 38-44 s on the plate, 107 s on a 50 MB model."""
    return 300 + 10 * Path(model_path).stat().st_size / 1e6


def lin_with_case_classes(source: Path, folder: Path, cases: list[str], out_switches: list[str],
                          timeout: float, runner: Runner = esa_xml.run_on_copy) -> esa_xml.RunResult:
    """LIN on a copy of ``source`` in ``folder``, with a combination and result class per case if any.

    ``out_switches`` say what to keep: ``-tESA -o<file>`` (the report route) or ``-x<file>`` (results).
    """
    switches: list[str] = []
    if cases:
        update = Path(folder) / "classes.xml"
        update.write_text(build_case_classes_update(cases), encoding="utf-8")
        shutil.copyfile(esa_xml.RESULT_CLASSES_DEF_FILE, Path(folder) / esa_xml.RESULT_CLASSES_DEF_FILE.name)
        switches.append(str(update))
    return runner(Path(source), Path(folder), "LIN", switches + list(out_switches), timeout)


def _rows(table: Table, columns: dict[str, str]) -> dict[str, list[dict]]:
    """Rows of one table per member: {"dx_m": ..., <magnitude>: SI value}."""
    out: dict[str, list[dict]] = {}
    names = table.column("Naam")
    for i, row in enumerate(table.rows):
        cells = dict(zip(table.columns, row))
        entry = {"dx_m": float(cells["dx"])}
        for col, name in columns.items():
            if col in cells and cells[col] != "":
                entry[name] = float(cells[col])
        out.setdefault(names[i], []).append(entry)
    return out


def results_per_case(tables: list[Table], cases: list[str]) -> dict[str, dict[str, dict[str, list[dict]]]]:
    """{case: {member: {"forces": [...], "deformations": [...]}}} from the export, rows by position."""
    wanted = set(cases)
    out: dict[str, dict[str, dict[str, list[dict]]]] = {}
    for table in tables:
        if table.selection not in wanted or not table.rows or "Naam" not in table.columns:
            continue
        if table.name.startswith(FORCES_TABLE):
            key, columns = "forces", FORCE_COLUMNS
        elif table.name.startswith(DEFORMATIONS_TABLE):
            key, columns = "deformations", DEFORMATION_COLUMNS
        else:
            continue
        for member, rows in _rows(table, columns).items():
            entry = out.setdefault(table.selection, {}).setdefault(member, {"forces": [], "deformations": []})
            entry[key] = sorted(entry[key] + rows, key=lambda r: r["dx_m"])
    return out


def calculate(saved: Path, cases: list[str], folder: Path, allow_pids: frozenset[int] = frozenset(),
              runner: Runner = esa_xml.run_on_copy, progress: Callable[[str], None] = lambda step: None) -> dict:
    """Calculate a copy of ``saved`` and read every case's results. ``ok`` only when rows came back."""
    started = time.time()
    out = Path(folder) / "results.xml"
    progress("SCIA calculates a copy (LIN) and exports the results")
    run = lin_with_case_classes(saved, folder, cases, [f"-x{out}"], calc_timeout(saved),
                                functools.partial(runner, allow_pids=allow_pids))
    result: dict = {"ok": False, "esa_xml": run.message, "seconds": round(time.time() - started, 1),
                    "results": {}, "problems": []}
    if not run.succeeded or not out.exists():
        tail = [line.strip() for line in run.log.splitlines() if line.strip()][-3:]
        result["problems"].append(f"SCIA's batch calculation failed: {run.message}."
                                  + (f" ESA_XML log: {' | '.join(tail)}" if tail else ""))
        return result
    progress("reading the results")
    result["results"] = results_per_case(parse_xmlio(out), cases)
    result["ok"] = bool(result["results"])
    return result
