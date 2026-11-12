"""S5: what changed between a plain re-save and an update, when the update adds whole blocks.

phase0's diff_esa.py compares blocks by index, which breaks as soon as an update adds a
table that the model did not have (free point loads). Here blocks are aligned with
difflib on their (revision-masked) content. A second plain re-save gives the noise:
blocks that change by themselves. Every other changed or added block must hold one
of the expected names, or the table header of one of the expected tables.

Usage: diff_aligned.py BASELINE UPDATED --control SECOND_BASELINE --expect-prefix PREFIX [--table PROGID ...]
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "xml_route"))

from diff_esa import load, without_revisions  # noqa: E402
from esa_records import names_in  # noqa: E402

ANYTHING = re.compile(".")


def digest(chunk: bytes) -> str:
    return hashlib.sha1(chunk).hexdigest()


def label(chunk: bytes) -> str:
    head = re.search(rb"\^\$CD \{[0-9A-F-]+\} ([A-Za-z0-9_.]+)", chunk[:400])
    if head:
        return head.group(1).decode()
    found = [t for _, t in names_in(chunk, ANYTHING)][:6]
    return f"names {found}"


def changed_blocks(a: list[bytes], b: list[bytes]) -> list[tuple[str, list[int], list[int]]]:
    matcher = difflib.SequenceMatcher(a=[digest(c) for c in a], b=[digest(c) for c in b], autojunk=False)
    return [(tag, list(range(i1, i2)), list(range(j1, j2))) for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("updated", type=Path)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--expect-prefix", required=True)
    parser.add_argument("--table", nargs="*", default=[])
    args = parser.parse_args()

    base, update, control = without_revisions(load(args.baseline), load(args.updated), load(args.control))
    noise = {digest(base[i]) for _, old, _ in changed_blocks(base, control) for i in old}
    print(f"blocks: baseline {len(base)}, updated {len(update)}, control {len(control)}; noise blocks {len(noise)}")
    unexplained = []
    for tag, old, new in changed_blocks(base, update):
        if all(digest(base[i]) in noise for i in old) and not new:
            continue
        for j in new:
            chunk = update[j]
            ours = sorted({t for _, t in names_in(chunk, ANYTHING) if t.startswith(args.expect_prefix)})
            table = label(chunk)
            explained = bool(ours) or table in args.table
            noisy = bool(old) and all(digest(base[i]) in noise for i in old)
            verdict = "ours" if explained else ("noise (changes by itself)" if noisy else "UNEXPLAINED")
            print(f"  {tag:8s} updated block {j} ({len(chunk)} bytes, {table}): {verdict}; {len(ours)} of our names")
            if verdict == "UNEXPLAINED":
                unexplained.append((j, table))
        for i in old if not new else []:
            print(f"  removed baseline block {i} ({label(base[i])})")
            unexplained.append((i, "removed"))
    print("RESULT:", "only our objects and bookkeeping" if not unexplained else f"OTHER DIFFERENCES: {unexplained}")
    return 0 if not unexplained else 1


if __name__ == "__main__":
    raise SystemExit(main())
