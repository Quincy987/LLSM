"""List candidate pile models with their size and the SCIA version that saved them.

Read-only. The version stamp sits in the file's compressed blocks, so no licence
is needed.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from esa_strings import blocks

CANDIDATES = [
    *sorted(Path(r"C:\Development\Viktor").glob("*.esa")),
    *sorted(Path(r"C:\Development\Viktor").glob("*/*.esa")),
    Path(r"C:\Development\modeloud.esa"),
]
STAMP = re.compile(re.escape("+D_".encode("utf-16le")) + rb"(?:[0-9._]\x00){6,30}")


def versions_in(path: Path) -> list[str]:
    """Every version in the file's history, oldest first; the last one saved it."""
    found: set[str] = set()
    for offset, chunk in blocks(path.read_bytes()):
        if offset:
            found |= {m.group(0).decode("utf-16le") for m in STAMP.finditer(chunk)}
    return sorted(found, key=lambda stamp: [int(part) for part in re.findall(r"\d+", stamp)])


def main() -> int:
    for path in CANDIDATES:
        if path.is_file():
            history = versions_in(path)
            print(f"{path.stat().st_size / 1e6:8.1f} MB  saved by {history[-1] if history else 'unknown'}  {path}")
        else:
            print(f"MISSING  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
