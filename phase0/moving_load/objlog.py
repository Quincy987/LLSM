"""Print SCIA's object-count log of a saved .esa (progid -> count), and the reader's members."""
import re, sys
sys.path.insert(0, "src")
from pathlib import Path
from llsm.scia.esa_file import iter_blocks, read_esa
LOG = re.compile(rb"- \d+ - (\d+) - ([A-Za-z0-9_.]+) -")
for f in sys.argv[1:]:
    counts = {}
    for chunk in iter_blocks(Path(f).read_bytes()):
        for m in LOG.finditer(chunk):
            counts[m.group(2).decode()] = int(m.group(1))
    print("==", Path(f).name)
    for k, v in sorted(counts.items()):
        if any(w in k for w in ("Load", "Beam", "Plane", "Node", "Support", "Combi", "Force")):
            print(f"   {v:5d} {k}")
    try:
        m = read_esa(Path(f)); print("   members:", [(x.name, x.begin, x.end) for x in m.members][:6], "supports:", len(m.supports))
    except Exception as e:
        print("   reader:", e)
