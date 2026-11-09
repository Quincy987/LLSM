"""Look inside a .esa for a name, raw and in any zlib-compressed block. Read-only.

Usage: esa_strings.py FILE REGEX [--limit 40]
"""
from __future__ import annotations

import argparse
import re
import zlib
from pathlib import Path

ZLIB_HEADER = re.compile(rb"\x78[\x01\x5e\x9c\xda]")


def blocks(data: bytes):
    """Yield (0, raw file), then (offset, decompressed) for every complete zlib block."""
    yield 0, data
    view = memoryview(data)
    step = 1 << 20
    resume = 0
    for match in ZLIB_HEADER.finditer(data):
        start = match.start()
        if start < resume:
            continue
        decoder = zlib.decompressobj()
        parts = []
        pos = start
        try:
            while not decoder.eof and pos < len(data):
                parts.append(decoder.decompress(view[pos:pos + step]))
                pos += step
        except zlib.error:
            continue
        if not decoder.eof:
            continue
        resume = min(pos, len(data)) - len(decoder.unused_data)
        chunk = b"".join(parts)
        if len(chunk) > 16:
            yield start, chunk


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("regex")
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()
    needle = args.regex.encode()
    wide = re.compile(b"".join(bytes([c]) + b"\x00" for c in needle))
    narrow = re.compile(needle)
    data = args.file.read_bytes()
    print("size", len(data), "header", data[:32].hex())
    count = total = 0
    for offset, chunk in blocks(data):
        total += 1
        for kind, pattern in (("ascii", narrow), ("utf16", wide)):
            for match in pattern.finditer(chunk):
                start = max(0, match.start() - 24)
                print(f"block@{offset} {kind} +{match.start()}: {chunk[start:match.end() + 24]!r}")
                count += 1
                if count >= args.limit:
                    return 0
    print(f"blocks scanned: {total}, hits: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
