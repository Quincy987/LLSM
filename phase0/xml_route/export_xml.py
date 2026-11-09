"""Copy a model to the work folder and try to get its model tables out as XML.

Usage: export_xml.py --model PATH [--label NAME] [--doc XMLDOCNAME] [--resave]

A run only counts as a usable export when the output holds node, 1D-member and
line-support tables. Exit 0 with results-only content is the same failure as
exit 8/9: the project has no usable XML IO document.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import WORK, assert_no_scia, read_xml_text, run_esa_xml, work_copy

MODEL_TABLES = {
    "nodes": "EP_DSG_Elements.EP_StructNode.1",
    "members": "EP_DSG_Elements.EP_Beam.1",
    "line_supports": "DataAddSupport.EP_LineSupportLine.1",
}


def table_kinds(xml: str) -> list[str]:
    return sorted(set(re.findall(r'<(?:container|table)\b[^>]*\bt="([^"]+)"', xml)))


def usable(xml: str) -> dict[str, bool]:
    kinds = table_kinds(xml)
    return {key: any(value in kind for kind in kinds) for key, value in MODEL_TABLES.items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="export_base")
    parser.add_argument("--doc")
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--resave", action="store_true")
    parser.add_argument("--keep-copy", action="store_true", help="reuse work/<label>.esa if present")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()

    assert_no_scia()
    copy = WORK / f"{args.label}.esa"
    if not (args.keep_copy and copy.exists()):
        copy = work_copy(args.model, f"{args.label}.esa")
    out = WORK / f"{args.label}.xml"
    out.unlink(missing_ok=True)

    argv: list[str | Path] = ["NONE", copy, f"-x{out}"]
    if args.doc:
        argv.append(f"-m{args.doc}")
    if args.resave:
        argv.append("-r")
    run = run_esa_xml(argv, args.label, timeout=args.timeout)
    print("stdout:", run.stdout[-1500:])
    print("stderr:", run.stderr[-500:])
    print("log:", run.log[-1500:])

    if not out.exists():
        print(f"RESULT: no output file ({run.summary()})")
        return 2
    xml = read_xml_text(out)
    print(f"output: {out} ({len(xml)} chars)")
    print("tables:", table_kinds(xml))
    state = usable(xml)
    print("model tables present:", state)
    verdict = "usable" if all(state.values()) else "NOT usable (no model tables)"
    print(f"RESULT: {verdict}")
    return 0 if all(state.values()) else 3


if __name__ == "__main__":
    raise SystemExit(main())
