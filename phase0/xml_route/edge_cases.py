"""What SCIA actually does on awkward XML updates. Results land in work/edge_cases.json.

Each case runs on a fresh copy and is judged on the saved file, not the exit code.
"""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import WORK, run_esa_xml, work_copy
from parse_members import read_esa
from write_update import Segment, apply_update, build_update

PILE = WORK / "pile_base.esa"
REAL = WORK / "export_base.esa"
ONE = (Segment("LLSM:1", 0.10, 0.40, 1.5e6),)


def judge(label: str, base_supports: dict, run, out: Path) -> dict:
    result = {
        "case": label,
        "exit": run.exit_code,
        "meaning": run.meaning,
        "seconds": round(run.seconds, 1),
        "log_tail": run.log[-300:],
        "stderr": run.stderr[-300:],
        "dialogs": run.dialogs,
        "saved": out.exists(),
    }
    if out.exists():
        supports = {s.name: s for s in read_esa(out)[1]}
        result["supports_before"] = len(base_supports)
        result["supports_after"] = len(supports)
        result["new"] = [dataclasses.asdict(supports[n]) for n in sorted(supports.keys() - base_supports.keys())]
        result["altered"] = [
            {"name": n, "before": dataclasses.asdict(base_supports[n]), "after": dataclasses.asdict(supports[n])}
            for n in sorted(supports.keys() & base_supports.keys())
            if supports[n] != base_supports[n]
        ]
    print(json.dumps(result, indent=1), flush=True)
    return result


def main() -> int:
    pile_supports = {s.name: s for s in read_esa(PILE)[1]}
    real_supports = {s.name: s for s in read_esa(REAL)[1]}
    results = []

    cases = [
        ("name_right_id_wrong", PILE, pile_supports, build_update(ONE, "B1", 999)),
        ("name_wrong_id_right", PILE, pile_supports, build_update(ONE, "NOPE", 1)),
        ("name_wrong_id_wrong", PILE, pile_supports, build_update(ONE, "NOPE", 999)),
        ("duplicate_existing_name", PILE, pile_supports, build_update((Segment("Sn:1", 0.10, 0.40, 1.5e6),), "B1", 1)),
        ("decimal_comma_stiffness", PILE, pile_supports, build_update((Segment("LLSM:1", 0.10, 0.40, "1500000,5"),), "B1", 1)),
        ("real_name_S4_id_1", REAL, real_supports, build_update(ONE, "S4", 1)),
    ]
    for label, base, before, xml in cases:
        run, out = apply_update(base, f"edge_{label}", xml, timeout=240)
        results.append(judge(label, before, run, out))

    # The same update applied twice.
    run, out = apply_update(WORK / "upd_b1_out.esa", "edge_applied_twice", build_update(ONE, "B1", 1), timeout=240)
    twice_before = {s.name: s for s in read_esa(WORK / "upd_b1_out.esa")[1]}
    results.append(judge("applied_twice", twice_before, run, out))

    # A file from an older SCIA: what the resave switch says.
    old = work_copy(Path(r"C:\Development\modeloud.esa"), "edge_old_in.esa")
    run = run_esa_xml(["NONE", old, "-r"], "edge_old_resave", timeout=240)
    results.append(
        {"case": "older_file_resave", "exit": run.exit_code, "meaning": run.meaning, "stdout": run.stdout[-400:], "log_tail": run.log[-400:], "dialogs": run.dialogs}
    )
    print(json.dumps(results[-1], indent=1), flush=True)

    (WORK / "edge_cases.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
