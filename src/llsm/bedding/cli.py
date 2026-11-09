"""``llsm bedding preview|apply``: works without any LLM credentials."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .apply import PlanRequest, apply_bedding, preview_bedding
from .layers import LayerError, read_layer_rows

_COLUMNS = (
    ("pile", "Pile"),
    ("segment", "Segment"),
    ("top_nap", "NAP top"),
    ("bottom_nap", "NAP bottom"),
    ("x1", "x1"),
    ("x2", "x2"),
    ("k_line_kN_m2", "k [kN/m2]"),
    ("description", "Soil"),
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llsm bedding", description="Put soil beddings on the piles of a saved SCIA model.")
    commands = parser.add_subparsers(dest="command", required=True)

    def shared(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("model", type=Path, help="Saved SCIA model (.esa).")
        sub.add_argument("layers", type=Path, help="Layer table (.csv or .json): top,bottom,value,unit,description.")
        sub.add_argument("--piles", default="*", help="Names and globs, comma-separated. Globs only pick steep members.")
        sub.add_argument("--diameter", type=float, help="Pile diameter in m.")
        sub.add_argument("--variant", help="laag, gemiddeld or hoog, when the table has value_* columns.")
        sub.add_argument("--start-level", type=float, dest="start_level", help="NAP level where the bedding starts.")
        sub.add_argument("--datum", type=float, default=0.0, help="Model z of NAP 0 (default 0).")
        sub.add_argument("--alpha", type=float, help="Ménard alpha, when the table has no alpha column.")
        sub.add_argument("--groepsfactor", type=float, help="Ménard group factor.")
        sub.add_argument("--replace", action="store_true", help="Overwrite supports this tool wrote earlier.")
        sub.add_argument("--allow-existing", action="store_true", help="Add to piles that already carry other line supports.")

    preview = commands.add_parser("preview", help="Show what would be written. Changes nothing.")
    shared(preview)
    apply = commands.add_parser("apply", help="Write a new model file and check it.")
    shared(apply)
    apply.add_argument("--out-dir", type=Path, help="Folder for the new file (default: next to the model).")
    return parser


def _print_table(rows: list[dict]) -> None:
    if not rows:
        return
    table = [[label for _, label in _COLUMNS]] + [[str(row[key]) for key, _ in _COLUMNS] for row in rows]
    widths = [max(len(line[i]) for line in table) for i in range(len(_COLUMNS))]
    for line in table:
        print("  ".join(cell.ljust(width) for cell, width in zip(line, widths)).rstrip())


def _report(result: dict) -> int:
    print(f"Model: {result['model']} (saved by {result['saved_by'] or 'unknown'})")
    print(f"Model z of NAP 0: {result['datum_offset']:g}")
    print(f"Piles: {len(result['piles'])}; segments: {len(result['preview'])}")
    print()
    _print_table(result["preview"])
    for word, key in (("WARNING", "warnings"), ("PROBLEM", "problems")):
        for line in result[key]:
            print(f"{word}: {line}")
    if result["action"] == "apply" and result["expected"]:
        print()
        print(f"Written and confirmed: {result['written']} of {result['expected']} ({result['seconds']} s)")
        for line in result["mismatches"]:
            print(f"MISMATCH: {line}")
        for line in result["other_changes"]:
            print(f"OTHER CHANGE: {line}")
        print(f"Output: {result['output']}")
    print()
    print("OK" if result["ok"] else "NOT OK")
    return 0 if result["ok"] else 1


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        rows = read_layer_rows(args.layers)
    except (OSError, ValueError, IndexError) as exc:
        problems = exc.problems if isinstance(exc, LayerError) else [str(exc)]
        for line in problems:
            print(f"PROBLEM: {line}", file=sys.stderr)
        return 1
    request = PlanRequest(
        piles=args.piles,
        diameter_m=args.diameter,
        variant=args.variant,
        start_level_nap=args.start_level,
        datum_offset=args.datum,
        alpha=args.alpha,
        groepsfactor=args.groepsfactor,
        replace=args.replace,
        allow_existing=args.allow_existing,
    )
    if args.command == "preview":
        return _report(preview_bedding(args.model, rows, request))
    return _report(apply_bedding(args.model, rows, request, out_dir=args.out_dir))
