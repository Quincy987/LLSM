"""Compare two saved models, down to the bytes of every project table.

Usage: diff_esa.py BASELINE.esa UPDATED.esa --control SECOND_BASELINE.esa --expect NAME ...

BASELINE and SECOND_BASELINE are two plain re-saves of the same input. The blocks
that differ between those two change by themselves (generated ids). Revision
numbers grow with every edit, so they are masked before comparing. Every other
block that differs from UPDATED is a real change and has to be accounted for: the
result fails on any such block that does not hold an expected name.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from esa_records import names_in
from esa_strings import blocks
from parse_members import read_esa

ANYTHING = re.compile(".")
# Serialised XML fragments and number strings are data, not object names.
NOT_A_NAME = re.compile(r"[<>]|^[-+]?\d+,\d+e[-+]\d+$|^.$")


REVISION_TEXT = re.compile(rb"DS revId: (\d+)")


def load(path: Path) -> list[bytes]:
    return [chunk for offset, chunk in blocks(path.read_bytes()) if offset]


def without_revisions(*models: list[bytes]) -> list[list[bytes]]:
    """Blank the revision stamps, with one window for all models.

    A window per model would blank identical bytes in one file and not in the other.
    Stamps are u32 values around the revision in the log, which is written before the
    save finishes, so a few are slightly above it.
    """
    # The log holds a history of revisions; each model's current one is the highest.
    revisions = [max(int(m.group(1)) for c in model for m in REVISION_TEXT.finditer(c)) for model in models]
    lowest, highest = min(revisions) - 0x4000, max(revisions) + 0x400
    third = sorted({value.to_bytes(4, "little")[2] for value in (lowest, highest)})
    stamp = re.compile(b"(?s)..[" + b"".join(re.escape(bytes([t])) for t in third) + b"]\x00")

    def blank(match: re.Match) -> bytes:
        in_range = lowest <= int.from_bytes(match.group(0), "little") <= highest
        return bytes(4) if in_range else match.group(0)

    return [[stamp.sub(blank, REVISION_TEXT.sub(b"DS revId: 0", c)) for c in model] for model in models]


def names(chunks: list[bytes]) -> Counter:
    found: Counter = Counter()
    for chunk in chunks:
        found.update(t for _, t in names_in(chunk, ANYTHING) if not NOT_A_NAME.search(t))
    return found


def changed_region(a: bytes, b: bytes) -> tuple[int, int, int]:
    """Common prefix length, and how many bytes differ between prefix and suffix on each side."""
    limit = min(len(a), len(b))
    prefix = next((i for i in range(limit) if a[i] != b[i]), limit)
    suffix = next((i for i in range(limit - prefix) if a[-1 - i] != b[-1 - i]), limit - prefix)
    return prefix, len(a) - prefix - suffix, len(b) - prefix - suffix


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("updated", type=Path)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--expect", nargs="*", default=[])
    args = parser.parse_args()

    base, update, control = without_revisions(load(args.baseline), load(args.updated), load(args.control))
    if not len(base) == len(update) == len(control):
        print(f"RESULT: block counts differ ({len(base)}, {len(update)}, {len(control)}); cannot compare")
        return 1
    noise = {i for i, (a, c) in enumerate(zip(base, control)) if a != c}
    differing = [i for i, (a, b) in enumerate(zip(base, update)) if a != b]
    real = [i for i in differing if i not in noise]
    print(f"blocks: {len(base)}; differ from baseline: {len(differing)}; change by themselves: {len(noise)}")
    print(f"not judged, because they differ between the two re-saves: {sorted(noise)}")
    print(f"differ beyond the control: {real}")
    wanted = re.compile("^(" + "|".join(re.escape(n) for n in args.expect) + ")$") if args.expect else None
    unexplained = []
    for i in real:
        prefix, old, new = changed_region(base[i], update[i])
        holds = sorted({t for _, t in names_in(update[i], wanted)}) if wanted else []
        print(f"  block {i}: {len(base[i])} -> {len(update[i])} bytes; region at {prefix}: {old} -> {new} bytes; holds {holds}")
        print(f"    baseline: {base[i][prefix:prefix + 48].hex(' ', 4)}")
        print(f"    updated:  {update[i][prefix:prefix + 48].hex(' ', 4)}")
        if not holds:
            unexplained.append(i)

    name_a, name_b = names(base), names(update)
    removed, added = name_a - name_b, name_b - name_a
    print("names added:", sorted(added.items())[:40])
    print("names removed:", sorted(removed.items())[:40])

    members_a, supports_a = read_esa(args.baseline)
    members_b, supports_b = read_esa(args.updated)
    geometry_a, geometry_b = {m.name: m for m in members_a}, {m.name: m for m in members_b}
    moved = [n for n in geometry_a.keys() & geometry_b.keys() if geometry_a[n] != geometry_b[n]]
    print(f"members: {len(geometry_a)} -> {len(geometry_b)}; changed geometry: {moved}")
    before, after = {s.name: s for s in supports_a}, {s.name: s for s in supports_b}
    new_supports = sorted(after.keys() - before.keys())
    gone = sorted(before.keys() - after.keys())
    altered = [n for n in before.keys() & after.keys() if before[n] != after[n]]
    print(f"line supports: {len(before)} -> {len(after)}; new {new_supports}; gone {gone}; altered {altered}")

    extra_names = (set(added) - set(args.expect)) | set(removed)
    clean = not (moved or gone or altered or unexplained or extra_names) and set(new_supports) == set(args.expect)
    print("unexplained blocks:", unexplained, "; unexpected names:", sorted(extra_names)[:40])
    print("RESULT:", "only the expected objects differ" if clean else "OTHER DIFFERENCES FOUND")
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
