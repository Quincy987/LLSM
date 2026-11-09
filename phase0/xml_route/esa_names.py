"""Overview of the named objects in a saved .esa, grouped by name pattern.

Usage: esa_names.py FILE [--show REGEX] [--limit 60]
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--show")
    parser.add_argument("--limit", type=int, default=60)
    args = parser.parse_args()
    anything = re.compile(".")
    shape = Counter()
    shown = 0
    show = re.compile(args.show) if args.show else None
    for offset, chunk in blocks(args.file.read_bytes()):
        if offset == 0:
            continue
        for start, text in names_in(chunk, anything):
            key = re.sub(r"\d+", "#", text)
            shape[key] += 1
            if show and show.search(text) and shown < args.limit:
                guid = chunk[max(0, start - 22):start - 6].hex()
                print(f"block@{offset} +{start} guid={guid} {text!r}")
                shown += 1
    print("distinct name shapes:", len(shape))
    for key, count in shape.most_common(args.limit):
        print(f"{count:7d}  {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
