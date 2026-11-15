"""Where a plate's thickness sits in the .esa (docs/verify/phase4-traffic-rules.md, "Natural request").

  python thickness_probe.py make     three plates of known thickness on a copy of the kademuur template -> thickness_probe.esa
  python thickness_probe.py read     the f64 at +485 after each plate record's name, in the probe and the demo models

Known inputs: D1 4 corners 0.25 m, D2 5 corners 0.45 m, W1 a vertical wall 0.35 m. The demo models are read
against what they are known to hold: the plate fixture 0.3 m (make_plate.py).
"""
from __future__ import annotations

import re
import shutil
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "xml_route"))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src"))

from _common import TEMPLATE, WORK, run_esa_xml, work_copy  # noqa: E402
from xmlgen import Ref, mesh, nodes, plate, project  # noqa: E402

FULL_DEF = HERE.parent / "xml_route" / "defs" / "viktor_full.xml.def"
POINTS = {
    "A1": (0.0, 0.0, 0.0), "A2": (6.0, 0.0, 0.0), "A3": (6.0, 4.0, 0.0), "A4": (0.0, 4.0, 0.0),
    "B1": (10.0, 0.0, 0.0), "B2": (14.0, 0.0, 0.0), "B3": (15.0, 2.0, 0.0), "B4": (14.0, 4.0, 0.0), "B5": (10.0, 4.0, 0.0),
    "C1": (0.0, 10.0, 0.0), "C2": (5.0, 10.0, 0.0), "C3": (5.0, 10.0, 3.0), "C4": (0.0, 10.0, 3.0),
}
PLATES = {"D1": (("A1", "A2", "A3", "A4"), 0.25), "D2": (("B1", "B2", "B3", "B4", "B5"), 0.45), "W1": (("C1", "C2", "C3", "C4"), 0.35)}


def make() -> int:
    ids = {name: i + 1 for i, name in enumerate(POINTS)}
    ref = {name: Ref(ids[name], name) for name in POINTS}
    # One Member 2D table with three objects: plate() writes one table per plate, each object with id 1.
    tables = [plate(n, "C20/25", t, [ref[c] for c in corners]) for n, (corners, t) in PLATES.items()]
    objects = [re.search(r"(<obj id=\"1\".*?</obj>)", t, re.S).group(1).replace('<obj id="1"', f'<obj id="{i + 1}"', 1)
               for i, t in enumerate(tables)]
    one = re.sub(r"<obj id=\"1\".*?</obj>", lambda _: "\n".join(objects), tables[0], count=1, flags=re.S)
    xml = project("full.xml.def", mesh(0.5), nodes(POINTS), one)
    template = work_copy(TEMPLATE, "thickness_template.esa")
    shutil.copy2(FULL_DEF, WORK / "full.xml.def")
    path = WORK / "thickness_probe.xml"
    path.write_text(xml, encoding="utf-8")
    out = WORK / "thickness_probe.esa"
    out.unlink(missing_ok=True)
    run = run_esa_xml(["NONE", template, path, "-tESA", f"-o{out}"], "thickness_probe")
    print(run.summary(), "saved:", out.exists())
    return 0 if out.exists() else 1


def read() -> int:
    from llsm.scia.esa_file import _PLATE_TABLE, _table_of, iter_tables, names_in  # noqa: PLC0415
    for model in (WORK / "thickness_probe.esa", WORK / "plate_base.esa", WORK / "kademuur_kaal.esa"):
        for chunk in iter_tables(model.read_bytes()):
            if _table_of(chunk) != _PLATE_TABLE:
                continue
            for start, name in names_in(chunk):
                if start >= 4 and chunk[start - 4:start] == b"\x01\x00\x00\x00":
                    end = start + 2 + 2 * len(name)
                    print(f"{model.name} {name}: {struct.unpack_from('<d', chunk, end + 485)[0]:g} m at +485")
    return 0


if __name__ == "__main__":
    raise SystemExit({"make": make, "read": read}[sys.argv[1]]())
