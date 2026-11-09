"""Print the UTF-16 strings of ESA_XML.exe that describe its command line.

Reads the binary only; nothing is executed and no licence is used.
"""
import re
import sys
from pathlib import Path

EXE = Path(r"C:\Program Files\SCIA\Engineer24.0\ESA_XML.exe")
PATTERN = rb"(?:[\x20-\x7e\t\r\n]\x00){6,}"


def main() -> int:
    needle = re.compile(sys.argv[1], re.I) if len(sys.argv) > 1 else None
    seen = []
    for match in re.finditer(PATTERN, EXE.read_bytes()):
        text = match.group(0).decode("utf-16le")
        if text not in seen and (needle is None or needle.search(text)):
            seen.append(text)
    for text in seen:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
