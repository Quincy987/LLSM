"""Headless read-back from a saved .esa: named objects and the numbers next to them.

Usage: esa_records.py FILE NAME_REGEX [--span 900] [--limit 8] [--dump]

The .esa is a set of zlib blocks holding the project tables. Objects carry their
name as a length-prefixed UTF-16 string, followed by their field values. This
script decompresses (read-only) and lists the 8-byte doubles after each name.
"""
from __future__ import annotations

import argparse
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from esa_strings import blocks

NAME = re.compile(rb"([\x01-\x7f])\x00((?:[\x20-\x7e]\x00){1,80})")


def names_in(chunk: bytes, needle: re.Pattern) -> list[tuple[int, str]]:
    found = []
    for match in NAME.finditer(chunk):
        text = match.group(2).decode("utf-16le")
        if match.group(1)[0] == len(text) and needle.search(text):
            found.append((match.start(), text))
    return found


def doubles_after(chunk: bytes, start: int, span: int) -> list[tuple[int, float]]:
    values = []
    for offset in range(start, min(start + span, len(chunk) - 8)):
        value = struct.unpack_from("<d", chunk, offset)[0]
        if value != 0 and 1e-6 <= abs(value) <= 1e13:
            text = f"{value:.12g}"
            if len(text) <= 8:  # only round numbers: arbitrary bytes rarely are
                values.append((offset - start, value))
    return values


def records(path: Path, needle: re.Pattern, span: int):
    data = path.read_bytes()
    for offset, chunk in blocks(data):
        if offset == 0:
            continue
        for start, text in names_in(chunk, needle):
            yield offset, start, text, doubles_after(chunk, start, span), chunk


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("regex")
    parser.add_argument("--span", type=int, default=900)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--dump", action="store_true")
    args = parser.parse_args()
    needle = re.compile(args.regex)
    shown = 0
    for offset, start, text, values, chunk in records(args.file, needle, args.span):
        print(f"block@{offset} +{start} {text!r}: {values}")
        if args.dump:
            print(chunk[start : start + args.span].hex(" ", 8))
        shown += 1
        if shown >= args.limit:
            break
    print("records shown:", shown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
