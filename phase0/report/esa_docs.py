"""List the engineering reports stored in a .esa (names and item-order XML). Read-only.

  python esa_docs.py MODEL.esa [--dump DIR]

SCIA stores each engineering report's header and item tree as small zlib-compressed XML
blocks (DOCUMENTVIEWDATASTORAGE_*). The <Name> is what ESA_XML's -d switch expects.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "xml_route"))
from esa_strings import blocks  # noqa: E402

MARK = b"DOCUMENTVIEWDATASTORAGE"


def report_blocks(path: Path) -> list[tuple[int, str]]:
    found = []
    for offset, chunk in blocks(path.read_bytes()):
        if offset and MARK in chunk[:400]:
            found.append((offset, chunk.decode("utf-8", "replace")))
    return found


def report_names(path: Path) -> list[str]:
    return [m for _, t in report_blocks(path) for m in re.findall(r"<Name>([^<]+)</Name>", t)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("model", type=Path)
    ap.add_argument("--dump", type=Path)
    args = ap.parse_args()
    found = report_blocks(args.model)
    names = [m for _, t in found for m in re.findall(r"<Name>([^<]+)</Name>", t)]
    print(f"{len(found)} report blocks; document names: {names}")
    for offset, text in found:
        kind = re.search(r"<(DOCUMENTVIEWDATASTORAGE_\w+)", text)
        print(f"@{offset} {kind.group(1) if kind else '?'} {len(text)} chars")
        if args.dump:
            args.dump.mkdir(parents=True, exist_ok=True)
            (args.dump / f"{offset}.xml").write_text(text, encoding="utf-8")
    return 0 if found else 1


if __name__ == "__main__":
    raise SystemExit(main())
