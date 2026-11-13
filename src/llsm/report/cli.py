"""``llsm report preview|write``: works without any LLM credentials."""
from __future__ import annotations

import argparse
from pathlib import Path

from .apply import FORMATS, ReportRequest, preview_report, write_report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llsm report", description="Print the engineering report of a saved SCIA model and check it holds results."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def shared(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("model", type=Path, help="Saved SCIA model (.esa).")
        sub.add_argument("--report", dest="report_name", help="Which engineering report, when the model has several.")
        sub.add_argument("--format", dest="formats", action="append", choices=FORMATS,
                         help="pdf (default), xlsx or rtf; repeat for more. The XLSX is always made for the check.")
        sub.add_argument("--calculate", action="store_true", help="Calculate a copy linearly first (LIN).")
        sub.add_argument("--cases-group", dest="cases_group",
                         help="A chapter per load case of this group (LLSM's moving-load groups); needs --calculate.")

    shared(commands.add_parser("preview", help="Show what would be printed. Changes nothing, starts no SCIA."))
    write = commands.add_parser("write", help="Print the report next to the model and check it.")
    shared(write)
    write.add_argument("--out-dir", type=Path, help="Folder for the report (default: next to the model).")
    return parser


def _report(result: dict) -> int:
    print(f"Model: {result['model']} (saved by {result['saved_by'] or 'unknown'})")
    reports = ", ".join(f"{r['name']} ({r['pages']} pages)" for r in result["reports"]) or "none"
    print(f"Engineering reports in the model: {reports}")
    print(f"Results stored for {result['results_stored_for_cases']} of {result['load_cases']} load cases")
    if result["report"]:
        print(f"Report: {result['report']}; formats: {', '.join(result['formats'])}")
    if result["cases"]:
        print(f"A chapter per case: {', '.join(result['cases'])}")
    for fmt, path in result.get("outputs_planned", {}).items():
        print(f"Would write {fmt.upper()}: {path}")
    if result.get("minutes_estimated") is not None:
        print(f"Takes about {result['minutes_estimated']} min")
    chapters = result.get("chapters")
    if chapters:
        print(
            f"Results tables: {chapters['written']} of {chapters['expected']} with results; "
            f"empty {chapters['empty']}; old type {chapters['unsupported']}"
        )
    for table in result.get("tables", []):
        print(f"  {table['chapter']:<34} {table['result_class'] or '':<20} {table['rows']} rows")
    for word, key in (("WARNING", "warnings"), ("PROBLEM", "problems")):
        for line in result[key]:
            print(f"{word}: {line}")
    for fmt, path in result.get("outputs", {}).items():
        print(f"Output {fmt.upper()}: {path}")
    if result.get("seconds") is not None:
        print(f"Took {result['seconds']} s")
    print()
    print("OK" if result["ok"] else "NOT OK")
    return 0 if result["ok"] else 1


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    request = ReportRequest(args.report_name, args.formats or ["pdf"], args.calculate, args.cases_group)
    if args.command == "preview":
        return _report(preview_report(args.model, request))
    return _report(write_report(args.model, request, out_dir=args.out_dir))
