"""Print what read_loads finds in saved models."""
import sys, time
sys.path.insert(0, "src")
from pathlib import Path
from llsm.scia.esa_file import read_loads, EsaFormatError
for f in sys.argv[1:]:
    t = time.time()
    try:
        m = read_loads(Path(f))
    except EsaFormatError as e:
        print("==", Path(f).name, "FORMAT ERROR", e); continue
    print("==", Path(f).name, f"{time.time() - t:.1f}s", m.saved_by)
    print("  nodes", len(m.nodes), "plates", [(p.name, p.node_ids, p.outline) for p in m.plates])
    print("  groups", [(g.name, g.relation, g.load) for g in m.groups])
    print("  cases", [(c.name, c.action) for c in m.cases])
    for l in m.loads:
        print("   ", l)
