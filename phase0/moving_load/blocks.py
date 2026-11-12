"""List the table header and top-level record names of each block in a saved .esa."""
import re, sys
sys.path.insert(0, "src")
from pathlib import Path
from llsm.scia.esa_file import iter_blocks, names_in
HDR = re.compile(rb"\^\$[0-9A-F]+ \{([0-9A-F-]+)\} ([A-Za-z0-9_.]+)")
keys = [k.encode() for k in sys.argv[2:]] or [b""]
for bi, chunk in enumerate(iter_blocks(Path(sys.argv[1]).read_bytes())):
    m = HDR.search(chunk[:400])
    top = [n for s, n in names_in(chunk) if s >= 4 and chunk[s - 4:s] == b"\x01\x00\x00\x00"]
    head = m.group(2).decode() if m else "-"
    if any(k in head.encode() for k in keys) or (not m and top and any(k == b"" for k in keys)):
        print(bi, len(chunk), m.start() if m else None, head, len(top), top[:6])
