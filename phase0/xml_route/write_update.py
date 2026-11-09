"""Add two flexible line supports to one existing member through an XML update.

Usage: write_update.py --base MODEL.esa --member B1 --member-id 1 --label NAME

Stiffness is written in N/m2 (measured by numeric_check.py); these are 1.5 and
3.0 MN/m2. The input model is never changed: ESA_XML saves to a separate file.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import quoteattr

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import SAMPLE_DEF, WORK, Run, run_esa_xml, work_copy
from parse_members import read_esa

HEADERS = [
    "Reference Table", "Constraint", "Type", "X", "Stiffness X", "Y", "Stiffness Y", "Z", "Stiffness Z",
    "Rx", "Stiffness Rx", "Ry", "Stiffness Ry", "Rz", "Stiffness Rz", "Function X", "Function Y",
    "Function Z", "Function Rx", "Function Ry", "Function Rz", "System", "Coord. definition", "Extent",
    "Position x1", "Position x2", "Origin",
]
BEAM_CLSID = "{ECB5D684-7357-11D4-9F6C-00104BC3B443}"
BEAM_PROGID = "EP_DSG_Elements.EP_Beam.1"


@dataclass(frozen=True)
class Segment:
    name: str
    x1: float
    x2: float
    stiffness: float | str
    torsion: float = 0  # rotational spring about the member axis; 0 leaves it free


DEFAULT_SEGMENTS = (
    Segment("LLSM:1", 0.10, 0.40, 1.5e6),
    Segment("LLSM:2", 0.40, 0.70, 3.0e6),
)


def line_support_obj(index: int, seg: Segment, member: str, member_id: int) -> str:
    rotation_x = f'<p9 v="2"/>\n           <p10 v="{seg.torsion}"/>' if seg.torsion else '<p9 v="0"/>'
    return f"""         <obj id="{index}" nm={quoteattr(seg.name)}>
           <p0 t="">
             <h>
               <h0 t="Member Type"/>
               <h1 t="Member Type Name"/>
               <h2 t="Member Id"/>
               <h3 t="Member Name"/>
             </h>
             <row id="0">
               <p0 v="{BEAM_CLSID}"/>
               <p1 v="{BEAM_PROGID}"/>
               <p2 v="{member_id}"/>
               <p3 v={quoteattr(member)}/>
             </row>
           </p0>
           <p1 v="3"/>
           <p2 v="0"/>
           <p3 v="0"/>
           <p5 v="2"/>
           <p6 v="{seg.stiffness}"/>
           <p7 v="2"/>
           <p8 v="{seg.stiffness}"/>
           {rotation_x}
           <p11 v="0"/>
           <p13 v="0"/>
           <p24 v="{seg.x1}"/>
           <p25 v="{seg.x2}"/>
         </obj>
"""


def build_update(segments: tuple[Segment, ...], member: str, member_id: int, first_id: int = 9001) -> str:
    head = "\n".join(f'            <h{i} t="{text}"/>' for i, text in enumerate(HEADERS))
    objs = "\n".join(
        line_support_obj(first_id + i, seg, member, member_id) for i, seg in enumerate(segments)
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<project xmlns="http://www.scia.cz">
   <def uri="viktor.xml.def"/>
   <container id="{{61FC64DE-5B75-4074-BF61-DA1AAA0A194C}}" t="DataAddSupport.EP_LineSupportLine.1">
      <table id="F1872059-F8EE-4009-8094-18C23FDC874D" t="DataAddSupport.EP_LineSupportLine.1" name="Line supports on beam">
         <h>
{head}
         </h>
{objs}
      </table>
   </container>
</project>
"""


def apply_update(base: Path, label: str, xml_text: str, timeout: float = 300) -> tuple[Run, Path]:
    copy = work_copy(base, f"{label}_in.esa")
    shutil.copy2(SAMPLE_DEF, WORK / "viktor.xml.def")
    update = WORK / f"{label}_update.xml"
    update.write_text(xml_text, encoding="utf-8")
    out = WORK / f"{label}_out.esa"
    out.unlink(missing_ok=True)
    run = run_esa_xml(["NONE", copy, update, "-tESA", f"-o{out}"], label, timeout=timeout)
    return run, out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--member", default="B1")
    parser.add_argument("--member-id", type=int, default=1)
    parser.add_argument("--label", default="update")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()

    # SCIA saves a support on a member that does not exist without complaint.
    if args.member not in {m.name for m in read_esa(args.base)[0]}:
        print(f"RESULT: no member named {args.member!r} in {args.base.name}; nothing written")
        return 1
    xml = build_update(DEFAULT_SEGMENTS, args.member, args.member_id)
    run, out = apply_update(args.base, args.label, xml, args.timeout)
    print("stderr:", run.stderr[-800:])
    print("log:", run.log[-1500:])
    print("dialogs:", run.dialogs)
    if run.exit_code != 0 or not out.exists():
        print("RESULT: not saved")
        return 1
    # Exit 0 is not evidence: SCIA also saves supports that point at nothing.
    stored = {s.name: s for s in read_esa(out)[1]}
    missing = [
        seg.name
        for seg in DEFAULT_SEGMENTS
        if seg.name not in stored
        or (stored[seg.name].x1, stored[seg.name].x2, stored[seg.name].stiffness_y, stored[seg.name].stiffness_z)
        != (seg.x1, seg.x2, seg.stiffness, seg.stiffness)
    ]
    print("output:", out, out.stat().st_size)
    print("RESULT:", f"not stored as written: {missing}" if missing else "stored as written")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
