"""List 1D members with begin/end coordinates, and line supports, from a model.

Usage: parse_members.py MODEL.(esa|xml) [--json OUT] [--limit 40]

Two sources:
- an XML with model tables, the supported format (paaltje.xml layout);
- a saved .esa, read-only. Its zlib blocks hold the tables; members and supports
  are recognised by the class GUIDs in their records. That layout is undocumented
  and tied to this SCIA version: good enough for a spike, not for a product
  without a version check.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import struct
import sys
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from esa_records import names_in
from esa_strings import blocks

NODE_GUID = uuid.UUID("39A7F468-A0D4-4DFF-8E5C-5843E1807D13").bytes_le
BEAM_GUID = uuid.UUID("ECB5D684-7357-11D4-9F6C-00104BC3B443").bytes_le
ANYTHING = re.compile(".")
# SCIA logs how many objects each table holds; the reader has to find exactly those.
OBJECT_LOG = re.compile(rb"- \d+ - (\d+) - (EP_DSG_Elements\.EP_Beam\.1|DataAddSupport\.EP_LineSupportLine\.1) -")
# Positions run just past 1 in real files (1.0044 in the sample).
POSITION_SLACK = 0.1
NS = {"s": "http://www.scia.cz"}


@dataclass
class Member:
    name: str
    member_id: int | None  # None from a .esa: not decoded, the name is what counts
    begin_node: int | None
    end_node: int | None
    begin: tuple[float, float, float]
    end: tuple[float, float, float]

    @property
    def length(self) -> float:
        return math.dist(self.begin, self.end)

    @property
    def is_pile_like(self) -> bool:
        """Mostly vertical: a coarse filter for piles, to be replaced by a user choice."""
        return self.length > 0 and abs(self.begin[2] - self.end[2]) / self.length > 0.7


@dataclass
class LineSupport:
    name: str
    member_id: int
    x1: float | None  # relative position; None when the record uses another layout
    x2: float | None
    stiffness_y: float
    stiffness_z: float


def _member_from_record(chunk: bytes, start: int, name: str, span: int = 420) -> Member | None:
    window_end = min(start + span, len(chunk))
    first = chunk.find(NODE_GUID, start, min(start + 200, window_end))
    if first < 0:
        return None
    second = chunk.find(NODE_GUID, first + 16, window_end)
    if second < 0 or second - first > 140:
        return None
    try:
        begin_id = struct.unpack_from("<I", chunk, first + 16)[0]
        end_id = struct.unpack_from("<I", chunk, second + 16)[0]
        begin = struct.unpack_from("<3d", chunk, first + 20)
        end = struct.unpack_from("<3d", chunk, second + 20)
    except struct.error:
        return None
    if not all(math.isfinite(v) and abs(v) < 1e5 for v in (*begin, *end)):
        return None
    return Member(name, None, begin_id, end_id, begin, end)


def _plausible_stiffness(value: float) -> bool:
    """Zero (a free direction) or a real number: other records hold denormal garbage here."""
    return value == 0 or 1e-3 <= value <= 1e13


def _support_from_record(chunk: bytes, start: int, name: str) -> LineSupport | None:
    first = chunk.find(BEAM_GUID, start, min(start + 60, len(chunk)))
    if first < 0:
        return None
    second = chunk.find(BEAM_GUID, first + 16, min(first + 260, len(chunk)))
    if second < 0 or max(first + 277, second + 24) > len(chunk):
        return None
    # The u32 right after the GUID is a count; the member id follows it.
    member_id = struct.unpack_from("<I", chunk, second + 20)[0]
    x1 = struct.unpack_from("<d", chunk, first + 126)[0]
    x2 = struct.unpack_from("<d", chunk, first + 152)[0]
    k_y = struct.unpack_from("<d", chunk, first + 253)[0]
    k_z = struct.unpack_from("<d", chunk, first + 269)[0]
    if not (_plausible_stiffness(k_y) and _plausible_stiffness(k_z)):
        return None
    # Supports written through XML carry 1 here; the ones already in the real model
    # carry 0 and keep their positions elsewhere (not decoded).
    layout = struct.unpack_from("<I", chunk, first + 88)[0]
    relative = layout == 1
    in_range = all(-POSITION_SLACK <= x <= 1 + POSITION_SLACK for x in (x1, x2))
    if layout not in (0, 1) or (relative and not in_range):
        return None
    return LineSupport(name, member_id, x1 if relative else None, x2 if relative else None, k_y, k_z)


def read_esa(path: Path) -> tuple[list[Member], list[LineSupport]]:
    members: dict[str, Member] = {}
    supports: dict[str, LineSupport] = {}
    declared: dict[bytes, int] = {}
    for offset, chunk in blocks(path.read_bytes()):
        if offset == 0:
            continue
        declared.update((m.group(2), int(m.group(1))) for m in OBJECT_LOG.finditer(chunk))
        for start, name in names_in(chunk, ANYTHING):
            member = _member_from_record(chunk, start, name)
            if member and name not in members:
                members[name] = member
                continue
            support = _support_from_record(chunk, start, name)
            if support and name not in supports:
                supports[name] = support
    found = {b"EP_DSG_Elements.EP_Beam.1": len(members), b"DataAddSupport.EP_LineSupportLine.1": len(supports)}
    wrong = {k.decode(): (found[k], declared[k]) for k in declared if found[k] != declared[k]}
    if wrong:
        raise ValueError(f"{path.name}: reader found / SCIA declares: {wrong}")
    return list(members.values()), list(supports.values())


def read_xml(path: Path) -> tuple[list[Member], list[LineSupport]]:
    root = ET.parse(path).getroot()
    nodes: dict[str, tuple[int, tuple[float, float, float]]] = {}
    members: list[Member] = []
    supports: list[LineSupport] = []
    for table in root.iterfind(".//s:table", NS):
        kind = table.get("t", "")
        headers = [h.get("t") for h in table.find("s:h", NS)] if table.find("s:h", NS) is not None else []
        index = {h: f"p{i}" for i, h in enumerate(headers)}
        for obj in table.iterfind("s:obj", NS):
            def cell(label: str):
                return obj.find(f"s:{index[label]}", NS) if label in index else None

            if kind.endswith("EP_StructNode.1"):
                coords = tuple(float(cell(f"Coord {k}").get("v").replace(",", ".")) for k in ("X", "Y", "Z"))
                nodes[obj.get("nm")] = (int(obj.get("id")), coords)
            elif kind.endswith("EP_Beam.1"):
                begin_cell, end_cell = cell("Beg. node"), cell("End node")
                begin = nodes[begin_cell.get("n")]
                end = nodes[end_cell.get("n")]
                members.append(Member(obj.get("nm"), int(obj.get("id")), begin[0], end[0], begin[1], end[1]))
            elif kind.endswith("EP_LineSupportLine.1"):
                ref = obj.find("s:p0/s:row/s:p2", NS)
                supports.append(
                    LineSupport(
                        obj.get("nm"),
                        int(ref.get("v")),
                        float(cell("Position x1").get("v")),
                        float(cell("Position x2").get("v")),
                        float(cell("Stiffness Y").get("v")),
                        float(cell("Stiffness Z").get("v")),
                    )
                )
    return members, supports


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("model", type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()
    members, supports = read_xml(args.model) if args.model.suffix.lower() == ".xml" else read_esa(args.model)
    piles = [m for m in members if m.is_pile_like]
    print(f"members: {len(members)}, pile-like: {len(piles)}, line supports: {len(supports)}")
    print("name | begin_id | end_id | begin xyz | end xyz | length")
    for member in (piles or members)[: args.limit]:
        print(member.name, member.begin_node, member.end_node, member.begin, member.end, round(member.length, 3))
    print("support | member_id | x1 | x2 | kY | kZ")
    for support in supports[: args.limit]:
        print(support.name, support.member_id, support.x1, support.x2, support.stiffness_y, support.stiffness_z)
    if args.json:
        args.json.write_text(
            json.dumps({"members": [asdict(m) for m in members], "supports": [asdict(s) for s in supports]}, indent=1),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
