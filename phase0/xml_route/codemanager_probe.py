"""What is the value an XML update changes from 100 to 0 in the code manager table?

Usage: codemanager_probe.py BASELINE.esa UPDATED.esa

Read-only, runs no SCIA. Finds the changed bytes in the EP_CodeManager block and prints
the readable strings around them, nearest first.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from esa_records import names_in
from esa_strings import blocks

ANYTHING = re.compile(".")
TABLE = "EP_CodeManager.EP_CodeManager.1"


def code_manager(path: Path) -> bytes:
    for offset, chunk in blocks(path.read_bytes()):
        if offset and chunk[:200].decode("utf-16le", "ignore").find(TABLE) >= 0:
            return chunk
    raise SystemExit(f"{path.name}: no {TABLE} block")


def main() -> int:
    base, updated = code_manager(Path(sys.argv[1])), code_manager(Path(sys.argv[2]))
    # Revision stamps differ too; a changed double ends in the bytes of 100.0.
    hundred = bytes.fromhex("5940")
    spots = [i for i in range(min(len(base), len(updated)) - 1) if base[i:i + 2] == hundred and updated[i:i + 2] == b"\x00\x00"]
    print("blocks:", len(base), len(updated), "value 100 became 0 at:", spots)
    strings = names_in(base, ANYTHING)
    for spot in spots:
        before = [(spot - at, text) for at, text in strings if at < spot][-12:]
        after = [(at - spot, text) for at, text in strings if at > spot][:12]
        print(f"--- at {spot}: 8 bytes before = {base[spot - 6:spot + 2].hex(' ', 2)}")
        print("strings before (distance, text):", list(reversed(before)))
        print("strings after (distance, text):", after)
        print("doubles nearby:", base[spot - 40:spot + 42].hex(" ", 8))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
