"""Read 1D members and line supports from a saved SCIA model, without SCIA.

Two sources:

* a saved ``.esa``. Its project tables sit in zlib blocks; members and supports are
  recognised by the class GUIDs in their records. The layout is undocumented, so every
  read is checked against the object counts SCIA logs in the same file, and a mismatch
  raises instead of returning a partial model. Tested on files saved by 19.1 and 24.0.
* an XML with model tables (a hand export from SCIA), the supported format.
"""
from __future__ import annotations

import math
import re
import struct
import uuid
import xml.etree.ElementTree as ET
import zlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

NODE_GUID = uuid.UUID("39A7F468-A0D4-4DFF-8E5C-5843E1807D13").bytes_le
BEAM_GUID = uuid.UUID("ECB5D684-7357-11D4-9F6C-00104BC3B443").bytes_le

TESTED_VERSIONS = ("19.1", "24.0")

_BEAM_PROGID = b"EP_DSG_Elements.EP_Beam.1"
_SUPPORT_PROGID = b"DataAddSupport.EP_LineSupportLine.1"
_ZLIB_HEADER = re.compile(rb"\x78[\x01\x5e\x9c\xda]")
_NAME = re.compile(rb"([\x01-\x7f])\x00((?:[\x20-\x7e]\x00){1,80})")
_OBJECT_LOG = re.compile(rb"- \d+ - (\d+) - (" + re.escape(_BEAM_PROGID) + b"|" + re.escape(_SUPPORT_PROGID) + rb") -")
_VERSION_STAMP = re.compile(re.escape("+D_".encode("utf-16le")) + rb"(?:[0-9._]\x00){6,30}")
_MEMBER_ID_MARKER = b"\x01\x00_\x00"  # the string "_" that follows the member's geometry
_MEMBER_ID_OFFSETS = (30, 236)  # from that marker; both hold the id
_POSITION_SLACK = 0.1  # positions run just past 1 in real files (1.0044 in a sample)
_XML_NS = {"s": "http://www.scia.cz"}

Point = tuple[float, float, float]


class EsaFormatError(RuntimeError):
    """The file does not read as expected; nothing from it can be trusted."""


@dataclass(frozen=True)
class Member:
    name: str
    member_id: int | None  # None when the record does not give it unambiguously
    begin: Point
    end: Point

    @property
    def length(self) -> float:
        return math.dist(self.begin, self.end)

    @property
    def verticality(self) -> float:
        """|dz| / L: 1 for a vertical member, 0 for a horizontal one."""
        return abs(self.begin[2] - self.end[2]) / self.length if self.length else 0.0


@dataclass(frozen=True)
class LineSupport:
    name: str
    member_id: int
    x1: float | None  # relative position; None when the record uses a layout we cannot read
    x2: float | None
    stiffness_y: float  # N/m2
    stiffness_z: float  # N/m2


@dataclass
class EsaModel:
    members: list[Member]
    supports: list[LineSupport]
    saved_by: str | None = None  # e.g. "+D_344_24.0.3015.64"
    warnings: list[str] = field(default_factory=list)
    source: str = "esa"  # "xml" for a hand export, whose values a .esa read cannot be compared with


def iter_blocks(data: bytes) -> Iterator[bytes]:
    """Yield the decompressed content of every complete zlib block in the file."""
    view = memoryview(data)
    step = 1 << 20
    resume = 0
    for match in _ZLIB_HEADER.finditer(data):
        start = match.start()
        if start < resume:
            continue
        decoder = zlib.decompressobj()
        parts = []
        pos = start
        try:
            while not decoder.eof and pos < len(data):
                parts.append(decoder.decompress(view[pos:pos + step]))
                pos += step
        except zlib.error:
            continue
        if not decoder.eof:
            continue
        resume = min(pos, len(data)) - len(decoder.unused_data)
        chunk = b"".join(parts)
        if len(chunk) > 16:
            yield chunk


def names_in(chunk: bytes) -> list[tuple[int, str]]:
    """Length-prefixed UTF-16 strings in a block, as (offset, text)."""
    found = []
    for match in _NAME.finditer(chunk):
        text = match.group(2).decode("utf-16le")
        if match.group(1)[0] == len(text):
            found.append((match.start(), text))
    return found


def _member_id(chunk: bytes, first: int) -> int | None:
    """The member's id, from two places in its record that must agree; None if they do not."""
    marker = chunk.find(_MEMBER_ID_MARKER, first, first + 800)
    if marker < 0:
        return None
    try:
        values = {struct.unpack_from("<I", chunk, marker + offset)[0] for offset in _MEMBER_ID_OFFSETS}
    except struct.error:
        return None
    value = values.pop()
    return value if not values and value > 0 else None


def _member_from_record(chunk: bytes, start: int, name: str, span: int = 420) -> Member | None:
    window_end = min(start + span, len(chunk))
    first = chunk.find(NODE_GUID, start, min(start + 200, window_end))
    if first < 0:
        return None
    second = chunk.find(NODE_GUID, first + 16, window_end)
    if second < 0 or second - first > 140:
        return None
    try:
        begin = struct.unpack_from("<3d", chunk, first + 20)
        end = struct.unpack_from("<3d", chunk, second + 20)
    except struct.error:
        return None
    if not all(math.isfinite(v) and abs(v) < 1e5 for v in (*begin, *end)):
        return None
    return Member(name, _member_id(chunk, first), begin, end)


def _plausible_stiffness(value: float) -> bool:
    """Zero (a free direction) or a real number: other records hold garbage here."""
    return value == 0 or 1e-3 <= value <= 1e13


def _support_from_record(chunk: bytes, start: int, name: str) -> LineSupport | None:
    first = chunk.find(BEAM_GUID, start, min(start + 60, len(chunk)))
    if first < 0:
        return None
    second = chunk.find(BEAM_GUID, first + 16, min(first + 260, len(chunk)))
    if second < 0 or max(first + 277, second + 24) > len(chunk):
        return None
    member_id = struct.unpack_from("<I", chunk, second + 20)[0]
    x1 = struct.unpack_from("<d", chunk, first + 126)[0]
    x2 = struct.unpack_from("<d", chunk, first + 152)[0]
    k_y = struct.unpack_from("<d", chunk, first + 253)[0]
    k_z = struct.unpack_from("<d", chunk, first + 269)[0]
    if not (_plausible_stiffness(k_y) and _plausible_stiffness(k_z)):
        return None
    # Supports written through XML carry 1 here; others carry 0 and keep their
    # positions elsewhere (not decoded).
    layout = struct.unpack_from("<I", chunk, first + 88)[0]
    relative = layout == 1
    in_range = all(-_POSITION_SLACK <= x <= 1 + _POSITION_SLACK for x in (x1, x2))
    if layout not in (0, 1) or (relative and not in_range):
        return None
    return LineSupport(name, member_id, x1 if relative else None, x2 if relative else None, k_y, k_z)


def _stamps_in(chunk: bytes) -> set[str]:
    return {m.group(0).decode("utf-16le") for m in _VERSION_STAMP.finditer(chunk)}


def _latest(stamps: set[str]) -> str:
    """The newest of a file's version stamps; older saves stay in its history."""
    return max(stamps, key=lambda stamp: [int(part) for part in re.findall(r"\d+", stamp)])


def read_esa(path: Path) -> EsaModel:
    """Read members and line supports from a saved model. Raises EsaFormatError on any doubt."""
    members: dict[str, Member] = {}
    supports: dict[str, LineSupport] = {}
    declared: dict[bytes, int] = {}
    stamps: set[str] = set()
    for chunk in iter_blocks(Path(path).read_bytes()):
        declared.update((m.group(2), int(m.group(1))) for m in _OBJECT_LOG.finditer(chunk))
        stamps |= _stamps_in(chunk)
        for start, name in names_in(chunk):
            member = _member_from_record(chunk, start, name)
            if member and name not in members:
                members[name] = member
                continue
            support = _support_from_record(chunk, start, name)
            if support and name not in supports:
                supports[name] = support

    found = {_BEAM_PROGID: len(members), _SUPPORT_PROGID: len(supports)}
    wrong = {k.decode(): (found[k], declared[k]) for k in declared if found[k] != declared[k]}
    if wrong:
        raise EsaFormatError(f"{Path(path).name}: reader found / SCIA declares {wrong}")
    if not stamps:
        raise EsaFormatError(f"{Path(path).name}: no SCIA version stamp found; is this a saved model?")

    saved_by = _latest(stamps)
    model = EsaModel(list(members.values()), list(supports.values()), saved_by)
    for progid, count in found.items():
        if count and progid not in declared:
            model.warnings.append(f"SCIA logs no object count for {progid.decode()}; the read could not be cross-checked.")
    version = saved_by.split("_")[2]
    if not version.startswith(TESTED_VERSIONS):
        model.warnings.append(f"Saved by SCIA {version}; this reader was tested on 19.1 and 24.0 only.")
    return model


def saved_by_version(path: Path) -> str | None:
    """The version stamp of the SCIA that last saved the file, or None."""
    stamps: set[str] = set()
    for chunk in iter_blocks(Path(path).read_bytes()):
        stamps |= _stamps_in(chunk)
    return _latest(stamps) if stamps else None


def read_xml(path: Path) -> EsaModel:
    """Read members and line supports from an XML with model tables (a hand export)."""
    root = ET.parse(path).getroot()
    nodes: dict[str, Point] = {}
    members: list[Member] = []
    supports: list[LineSupport] = []
    for table in root.iterfind(".//s:table", _XML_NS):
        kind = table.get("t", "")
        header = table.find("s:h", _XML_NS)
        index = {h.get("t"): f"p{i}" for i, h in enumerate(header)} if header is not None else {}
        for obj in table.iterfind("s:obj", _XML_NS):
            def cell(label: str):
                return obj.find(f"s:{index[label]}", _XML_NS) if label in index else None

            if kind.endswith("EP_StructNode.1"):
                nodes[obj.get("nm")] = tuple(
                    float(cell(f"Coord {axis}").get("v").replace(",", ".")) for axis in "XYZ"
                )
            elif kind.endswith("EP_Beam.1"):
                begin, end = nodes[cell("Beg. node").get("n")], nodes[cell("End node").get("n")]
                members.append(Member(obj.get("nm"), int(obj.get("id")), begin, end))
            elif kind.endswith("EP_LineSupportLine.1"):
                supports.append(
                    LineSupport(
                        obj.get("nm"),
                        int(obj.find("s:p0/s:row/s:p2", _XML_NS).get("v")),
                        float(cell("Position x1").get("v")),
                        float(cell("Position x2").get("v")),
                        float(cell("Stiffness Y").get("v")),
                        float(cell("Stiffness Z").get("v")),
                    )
                )
    return EsaModel(members, supports, source="xml")
