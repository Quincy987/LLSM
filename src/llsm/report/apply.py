"""Print the engineering report stored in a saved model, on a copy, and prove it holds results.

The model is never written to: ESA_XML prints from a fresh copy (and calculates on one, if
asked). ``ok`` comes only from reading the report's XLSX back (``check.py``); ESA_XML's exit
code and the PDF's size are not evidence (an old-type report prints a 9 MB PDF without a
single result, exit 0: docs/verify/report-route.md).
"""
from __future__ import annotations

import re
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..scia import esa_xml
from ..scia.esa_file import EsaFormatError, LoadModel, ReportIndex, read_loads, read_report_index
from ..scia.load_xml import build_case_classes_update
from ..scia.output_files import mark_failed, next_output_set
from .check import ReportCheck, check_xlsx

FORMATS = ("pdf", "xlsx", "rtf")
_ESA_FORMAT = {"pdf": "PDF", "xlsx": "XLSX", "rtf": "RTF"}
MAX_TABLES_LISTED = 60
NO_TEMPLATE = (
    "What fixes it: in SCIA 24, open the model, rebuild the results items of its engineering report "
    "(or load a report template made in SCIA 24), save, and run this again. "
    "A standard LLSM report template is not available yet."
)

Runner = Callable[[Path, Path, str, list[str], float], esa_xml.RunResult]


@dataclass
class ReportRequest:
    report_name: str | None = None
    formats: list[str] = field(default_factory=lambda: ["pdf"])
    calculate: bool = False
    cases_group: str | None = None


def print_timeout(model_path: Path) -> float:
    """Seconds for one print: 3-7 min on the 36 MB plate, 4-8 min on a 50 MB model (spike)."""
    return 600 + 20 * model_path.stat().st_size / 1e6


def calc_timeout(model_path: Path) -> float:
    """Seconds for LIN and a save: 38-44 s on the plate, 107 s on a 50 MB model (spike)."""
    return 300 + 10 * model_path.stat().st_size / 1e6


def _base_result(action: str, model_path: Path, request: ReportRequest) -> dict:
    return {
        "ok": False,
        "action": action,
        "model": str(model_path),
        "saved_by": None,  # the MCP state block reads this key: the model is a file
        "reports": [],
        "report": None,
        "formats": [],
        "calculate": request.calculate,
        "cases_group": request.cases_group,
        "cases": [],
        "result_sets_stored": 0,
        "load_cases": 0,
        "problems": [],
        "warnings": [],
    }


def _group_cases(model: LoadModel, group: str) -> tuple[list[str], list[str]]:
    """The cases LLSM's moving load wrote into ``group`` ("<group> NN"), and any problems."""
    if group not in {g.name for g in model.groups}:
        names = ", ".join(sorted(g.name for g in model.groups)[:12]) or "none"
        return [], [f"Load group {group!r} not found; groups in the model: {names}"]
    own = re.compile(rf"^{re.escape(group)} \d+$")
    cases = sorted(c.name for c in model.cases if own.match(c.name))
    if not cases:
        return [], [
            f"No load cases named '{group} NN' in the model. cases_group works for the groups LLSM's "
            "moving load writes; for other groups, make the result classes in SCIA."
        ]
    return cases, []


def _prepare(
    action: str,
    model_path: Path,
    request: ReportRequest,
    index_reader: Callable[[Path], ReportIndex],
    load_reader: Callable[[Path], LoadModel],
) -> tuple[dict, ReportIndex | None]:
    result = _base_result(action, model_path, request)
    formats = [f.lower().lstrip(".") for f in request.formats] or ["pdf"]
    unknown = [f for f in formats if f not in FORMATS]
    if unknown:
        result["problems"] = [f"Unknown formats {unknown}; use one or more of {list(FORMATS)}."]
        return result, None
    # The XLSX is what the check reads, so it is always made, and made first.
    result["formats"] = ["xlsx", *dict.fromkeys(f for f in formats if f != "xlsx")]
    if request.cases_group and not request.calculate:
        result["problems"] = ["cases_group needs calculate: the new result classes have no results until a calculation."]
        return result, None
    try:
        index = index_reader(model_path)
    except FileNotFoundError:
        result["problems"] = [f"Model not found: {model_path}"]
        return result, None
    except PermissionError:
        result["problems"] = [f"{model_path.name} is open elsewhere, probably in SCIA. Close it and try again."]
        return result, None
    except OSError as exc:
        result["problems"] = [f"The model could not be read: {exc}"]
        return result, None

    result.update(
        saved_by=index.saved_by,
        reports=[{"name": r.name, "pages": r.pages, "items": r.items} for r in index.reports],
        result_sets_stored=index.result_sets,
        load_cases=index.counts.get("DataSetScia.EP_LoadCase.1", 0),
    )
    problems = result["problems"]
    names = [r.name for r in index.reports]
    if not names:
        problems.append(f"The model has no engineering report. {NO_TEMPLATE}")
    elif request.report_name:
        if request.report_name not in names:
            problems.append(f"No report named {request.report_name!r}; reports in the model: {', '.join(names)}")
        result["report"] = request.report_name
    elif len(names) == 1:
        result["report"] = names[0]
    else:
        problems.append(f"The model has {len(names)} reports ({', '.join(names)}); say which one to print.")

    if request.calculate:
        if index.nonlinear:
            problems.append(
                "calculate runs a linear calculation (LIN) only, and this model has nonlinear "
                f"combinations ({', '.join(index.nonlinear)}). Calculate it in SCIA, save, and print without calculate."
            )
        result["warnings"].append(
            "calculate runs SCIA's linear calculation on a copy; nonlinear supports or elements, if the model has "
            "any, are calculated linearly. The model itself is not calculated or changed."
        )
    elif index.result_sets == 0:
        problems.append(
            "The model has no results stored. Use calculate (linear models), or calculate in SCIA and save first."
        )
    elif index.result_sets < result["load_cases"]:
        # SCIA stores at least one result set per load case; fewer means cases were added after the
        # last calculation (e.g. by LLSM's moving load).
        result["warnings"].append(
            f"The model has {result['load_cases']} load cases but only {index.result_sets} stored result sets: "
            "some cases were probably added after the last calculation, so the report may be out of date. "
            "Use calculate to print fresh results."
        )

    if request.cases_group and not problems:
        try:
            model = load_reader(model_path)
        except (EsaFormatError, OSError) as exc:
            problems.append(f"The load cases could not be read from the model: {exc}")
        else:
            result["cases"], case_problems = _group_cases(model, request.cases_group)
            problems += case_problems
            if result["cases"]:
                result["warnings"].append(
                    f"On the copy only: one combination and one result class per case ({len(result['cases'])}), "
                    "each named after its case, so the report prints a chapter per case. The model keeps none of them."
                )
    return result, index


def preview_report(
    model_path: Path,
    request: ReportRequest | None = None,
    index_reader: Callable[[Path], ReportIndex] = read_report_index,
    load_reader: Callable[[Path], LoadModel] = read_loads,
    out_dir: Path | None = None,
) -> dict:
    """What would be printed, from the file alone. Touches nothing and starts no SCIA."""
    request = request or ReportRequest()
    model_path = Path(model_path).resolve()
    result, _ = _prepare("preview", model_path, request, index_reader, load_reader)
    if out_dir is not None and not Path(out_dir).is_dir():
        result["problems"].append(f"Output folder not found: {out_dir}")
    if result["report"] and not result["problems"]:
        outputs = next_output_set(model_path, "rapport", result["formats"], Path(out_dir).resolve() if out_dir else None)
        result["outputs_planned"] = {fmt: str(path) for fmt, path in outputs.items()}
        size_mb = model_path.stat().st_size / 1e6
        result["minutes_estimated"] = round(len(result["formats"]) * (2.5 + 0.05 * size_mb) + (1 if request.calculate else 0))
        result["warnings"].append(
            "Whether the report's results items print results is only known after printing: "
            "an old-type report (made before SCIA 17) prints its input only."
        )
    result["ok"] = not result["problems"]
    return result


def _table_overview(check: ReportCheck) -> list[dict]:
    return [
        {
            "chapter": t.sheet,
            "result_class": t.result_class,
            "selection": next((v for k, v in t.settings.items() if k.lower().startswith(("selectie", "selection"))), None),
            "extreme": next((v for k, v in t.settings.items() if k.lower().startswith(("extre",))), None),
            "rows": len(t.rows),
        }
        for t in check.tables[:MAX_TABLES_LISTED]
    ]


def write_report(
    model_path: Path,
    request: ReportRequest | None = None,
    out_dir: Path | None = None,
    runner: Runner = esa_xml.run_on_copy,
    index_reader: Callable[[Path], ReportIndex] = read_report_index,
    load_reader: Callable[[Path], LoadModel] = read_loads,
    progress: Callable[[str], None] = lambda step: None,
) -> dict:
    """Print the report next to the model and report what is really in it."""
    request = request or ReportRequest()
    model_path = Path(model_path).resolve()  # ESA_XML runs from its install folder
    result, index = _prepare("write", model_path, request, index_reader, load_reader)
    result.update(outputs={}, chapters=None, tables=[], seconds=None, esa_xml=[])
    if index is None or result["problems"]:
        return result
    if out_dir is not None and not Path(out_dir).is_dir():
        result["problems"] = [f"Output folder not found: {out_dir}"]
        return result
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="llsm-report-", ignore_cleanup_errors=True) as temp:
        folder = Path(temp)
        try:
            if request.calculate:
                progress("calculating a copy (LIN)")
            source = _calculated_copy(model_path, folder, request, result, runner) if request.calculate else model_path
        except (esa_xml.EsaXmlError, OSError) as exc:
            result["problems"] = [str(exc)]
            return result
        if source is None:
            return result

        printed: dict[str, Path] = {}
        check: ReportCheck | None = None
        for fmt in result["formats"]:
            out = folder / f"report.{fmt}"
            switches = ["-sd", f"-t{_ESA_FORMAT[fmt]}", f"-o{out}", f"-d{result['report']}"]
            progress(f"printing {fmt.upper()}" + (" (then the check)" if fmt == "xlsx" else ""))
            try:
                run = runner(source, folder, "NONE", switches, print_timeout(model_path))
            except (esa_xml.EsaXmlError, OSError) as exc:
                result["problems"].append(str(exc))
                break
            result["esa_xml"].append({"step": f"print {fmt}", "message": run.message, "seconds": round(run.seconds, 1)})
            if not run.succeeded:
                result["problems"].append(f"SCIA did not print the {fmt.upper()}: {run.message}. {_log_tail(run.log)}".strip())
                break
            if not out.exists() or out.stat().st_size == 0:
                result["problems"].append(f"SCIA reported success but wrote no {fmt.upper()} (exit 0, nothing written).")
                break
            printed[fmt] = out
            if fmt == "xlsx":
                check = check_xlsx(out)
                result["chapters"] = check.summary()
                result["tables"] = _table_overview(check)
                for name, tables in check.empty_classes.items():
                    result["warnings"].append(
                        f"Result class {name!r} has no combinations in the model: its {len(tables)} tables print "
                        "SCIA's \"Fout E-C04\" and no results. That is the model's class, not a print error."
                    )
                result["warnings"] += [f"SCIA printed: {m}" for m in check.messages[:10]]
                joined = _classes_joined(check, result["cases"])
                if joined:
                    result["warnings"].append(
                        f"SCIA also put the per-case combinations into the model's own result classes {', '.join(joined)} "
                        "(classes that take every combination of a type): in this report those chapters include the "
                        "single cases, so their extremes may differ from the model's. The model itself is unchanged."
                    )
                if not check.ok:
                    result["problems"] += check.problems
                    if check.unsupported or check.expected == 0:
                        result["problems"].append(NO_TEMPLATE)
                    break  # do not spend minutes on a PDF of a report without results

        result["ok"] = check is not None and check.ok and not result["problems"] and set(printed) == set(result["formats"])
        # Names are chosen now, not before the minutes of printing, so a file made meanwhile is not overwritten.
        final = next_output_set(model_path, "rapport", result["formats"], Path(out_dir).resolve() if out_dir else None)
        placed = []
        for fmt, path in printed.items():
            try:
                shutil.move(str(path), final[fmt])
                placed.append(fmt)
            except OSError as exc:
                result["ok"] = False
                result["problems"].append(f"The {fmt.upper()} could not be put next to the model: {exc}")
        for fmt in placed:  # marked only after every move, so a later failure also marks the earlier files
            result["outputs"][fmt] = str(final[fmt] if result["ok"] else mark_failed(final[fmt]))
    result["seconds"] = round(time.time() - started, 1)
    if len(check.tables if check else []) > MAX_TABLES_LISTED:
        result["warnings"].append(f"Only the first {MAX_TABLES_LISTED} results tables are listed here; all are in the report.")
    return result


def _classes_joined(check: ReportCheck, cases: list[str]) -> list[str]:
    """The model's own result classes that list one of the per-case combinations written on the copy."""
    if not cases:
        return []
    ours = tuple(f"{case} - " for case in cases)
    return sorted(name for name, members in check.class_members.items()
                  if name not in cases and any(m.startswith(ours) for m in members))


def _calculated_copy(model_path: Path, folder: Path, request: ReportRequest, result: dict, runner: Runner) -> Path | None:
    """LIN on a copy (with the per-case result classes, if asked), saved in ``folder``."""
    calc = folder / "calculated.esa"
    switches = []
    if result["cases"]:
        update = folder / "classes.xml"
        update.write_text(build_case_classes_update(result["cases"]), encoding="utf-8")
        shutil.copyfile(esa_xml.RESULT_CLASSES_DEF_FILE, folder / esa_xml.RESULT_CLASSES_DEF_FILE.name)
        switches.append(str(update))
    switches += ["-tESA", f"-o{calc}"]
    run = runner(model_path, folder, "LIN", switches, calc_timeout(model_path))
    result["esa_xml"].append({"step": "calculate (LIN) on a copy", "message": run.message, "seconds": round(run.seconds, 1)})
    if not run.succeeded or not calc.exists():
        result["problems"].append(f"The linear calculation on the copy failed: {run.message}. {_log_tail(run.log)}".strip())
        return None
    return calc


def _log_tail(log: str, lines: int = 3) -> str:
    tail = [line.strip() for line in log.splitlines() if line.strip()][-lines:]
    return ("ESA_XML log: " + " | ".join(tail)) if tail else ""
