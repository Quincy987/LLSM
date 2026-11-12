"""``llsm moving-load preview|apply``: works without any LLM credentials."""
from __future__ import annotations

import argparse
from pathlib import Path

from .apply import MovingLoadRequest, apply_moving_load, preview_moving_load

_COLUMNS = (
    ("case", "Case"),
    ("front_axle_along_path_mm", "Front axle along path [mm]"),
    ("loads", "Loads"),
    ("total_kN", "Total [kN]"),
    ("first_load_x_mm", "First load x [mm]"),
    ("first_load_y_mm", "y [mm]"),
)


def _points(text: str) -> list[list[float]]:
    """'500,2000;4900,2000' -> [[500, 2000], [4900, 2000]]"""
    try:
        return [[float(v) for v in pair.split(",")] for pair in text.split(";") if pair.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"points as 'x,y;x,y' in mm: {exc}") from exc


def _axles(text: str) -> list[dict]:
    """'200@0,100@1200' -> axle loads in kN at offsets in mm behind the front axle."""
    try:
        return [{"load_kn": float(a.split("@")[0]), "offset_mm": float(a.split("@")[1])} for a in text.split(",") if a.strip()]
    except (ValueError, IndexError) as exc:
        raise argparse.ArgumentTypeError(f"axles as 'load_kN@offset_mm,...': {exc}") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llsm moving-load", description="Put a vehicle at every step along a road on a saved SCIA model."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def shared(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("model", type=Path, help="Saved SCIA model (.esa).")
        sub.add_argument("--path", type=_points, required=True, help="Road axis in plan, mm: 'x,y;x,y[;...]'.")
        sub.add_argument("--step", type=float, required=True, help="Step of the front axle, mm.")
        vehicle = sub.add_mutually_exclusive_group(required=True)
        vehicle.add_argument("--preset", help="LM1-TS1, LM1-TS2 or LM1-TS3.")
        vehicle.add_argument("--axles", type=_axles, help="'load_kN@offset_mm,...', offset behind the front axle.")
        sub.add_argument("--track", type=float, help="Wheel track in mm (custom axles); without it one load per axle.")
        sub.add_argument("--alpha", type=float, default=1.0, help="Factor on the LM1 axle loads (default 1.0).")
        sub.add_argument("--deck", choices=("plate", "beam"), default="plate")
        sub.add_argument("--member", help="The beam, with --deck beam.")
        sub.add_argument("--name", default="TS1", help="Group name after 'LLSM-ML ' (default TS1).")
        sub.add_argument("--offset", type=float, default=0.0, help="Lateral offset of the vehicle from the path, mm (left +).")
        sub.add_argument("--last-step", choices=("exact", "short"), default="exact", dest="last_step")
        sub.add_argument("--deck-outline", type=_points, dest="deck_outline", help="Deck corners 'x,y;...' in mm, when the model's cannot be read.")
        sub.add_argument("--deck-z", type=float, default=0.0, dest="deck_z", help="Deck level in mm, with --deck-outline.")
        sub.add_argument("--replace", action="store_true", help="Overwrite what this tool wrote earlier under the same name.")
        sub.add_argument("--allow-many", action="store_true", dest="allow_many", help="Allow more than 200 load cases.")

    shared(commands.add_parser("preview", help="Show what would be written. Changes nothing."))
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
    if result["vehicle"]:
        print(f"Vehicle: {result['vehicle']}")
        print(f"Group: {result['group']} (exclusive, variable); deck: {result['deck']}")
        print(f"Load cases: {result['cases']}; loads: {result['loads']}")
    print()
    _print_table(result["preview"])
    for word, key in (("WARNING", "warnings"), ("PROBLEM", "problems")):
        for line in result[key]:
            print(f"{word}: {line}")
    if result["action"] == "apply" and result.get("expected"):
        print()
        print(
            f"Written and confirmed: {result['written']} of {result['expected']} loads, "
            f"{result['cases_confirmed']} of {result['cases']} cases ({result['seconds']} s)"
        )
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
    request = MovingLoadRequest(
        path_mm=args.path, step_mm=args.step, vehicle_preset=args.preset, axles=args.axles, track_mm=args.track,
        alpha=args.alpha, deck=args.deck, member=args.member, name=args.name, lateral_offset_mm=args.offset,
        last_step=args.last_step, replace=args.replace, allow_many=args.allow_many,
        deck_outline_mm=args.deck_outline, deck_z_mm=args.deck_z,
    )
    if args.command == "preview":
        return _report(preview_moving_load(args.model, request))
    return _report(apply_moving_load(args.model, request, out_dir=args.out_dir))
