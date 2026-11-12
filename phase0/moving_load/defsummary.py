"""Print the properties of chosen def containers, compactly (index, name, type, enum values)."""
import re, sys
from pathlib import Path
src = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
for want in sys.argv[2:]:
    for m in re.finditer(r'<def_container[^>]*progid="%s"[^>]*>.*?</def_container>' % re.escape(want), src, re.S):
        block = m.group(0)
        print("=====", want, len(block), "chars")
        depth = 0
        for line in block.splitlines():
            s = line.strip()
            if s.startswith("<def_subtable"): depth += 1; print("   " * depth + "[subtable]"); continue
            if s.startswith("</def_subtable"): depth -= 1; continue
            p = re.match(r'<def_property x="(\d+)" name="([^"]*)" type="(\w+)"', s)
            if p: print("   " * (depth + 1) + f"p{p.group(1)} {p.group(2)!r} {p.group(3)}"); continue
            e = re.match(r'<it_enum v="(-?\d+)" t="([^"]*)"', s)
            if e: print("   " * (depth + 2) + f"{e.group(1)}={e.group(2)}")
            r = re.match(r'<def_ref id="[^"]*" progid="([^"]*)"', s)
            if r: print("   " * (depth + 2) + f"ref -> {r.group(1)}")
