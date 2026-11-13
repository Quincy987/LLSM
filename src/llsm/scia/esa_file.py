"""Read a saved SCIA model without SCIA: members and line supports, and for loads also
nodes, 2D members, load groups, load cases and point forces (:func:`read_loads`).

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


# --- Loads, nodes and plates -------------------------------------------------------------
#
# Decoded on models written through the XML update (docs/verify/phase3-moving-load.md).
# Unlike members, these records are found by the table their block belongs to: every table
# block opens with a header string "^$<n> {<class guid>} <progid>". A top-level record is a name preceded by the
# u32 1; sub-objects inside a record (e.g. a group's "Belastingtype") are not. Blocks
# without a header are not read: a table that spills into one fails the count check
# instead of picking up another table's records. Offsets below are from the end of the
# record's name.

CASE_GUID = uuid.UUID("0908D21F-481F-11D4-AB84-00C06C452330").bytes_le

_NODE_TABLE = "EP_DSG_Elements.EP_StructNode.1"
_PLATE_TABLE = "EP_DSG_Elements.EP_Plane.1"
_GROUP_TABLE = "DataSetScia.EP_LoadGroup.1"
_CASE_TABLE = "DataSetScia.EP_LoadCase.1"
_FREE_FORCE_TABLE = "DataAddLoad.EP_PointForceFree.1"
_BEAM_FORCE_TABLE = "DataAddLoad.EP_PointForceLine.1"
LOAD_TABLES = (_NODE_TABLE, _PLATE_TABLE, _GROUP_TABLE, _CASE_TABLE, _FREE_FORCE_TABLE, _BEAM_FORCE_TABLE)

_TABLE_HEADER = re.compile(r"^\^\$[0-9A-F]+ \{[0-9A-F-]{36}\} ([A-Za-z0-9_.]+)$")
_ANY_OBJECT_LOG = re.compile(rb"- \d+ - (\d+) - ([A-Za-z0-9_.]+) -")
_TOP_LEVEL = b"\x01\x00\x00\x00"
_LIST_END = 22  # a case's or group's id list ends this many bytes before its name


@dataclass(frozen=True)
class Node:
    name: str
    node_id: int
    point: Point  # m


@dataclass(frozen=True)
class Plate:
    """A 2D member. Its outline is the nodes of its geometry table, joined by straight lines."""

    name: str
    node_ids: tuple[int, ...]
    outline: tuple[Point, ...]  # m; empty when a node could not be resolved


@dataclass(frozen=True)
class LoadGroup:
    name: str
    relation: int  # 0 standard, 1 exclusive, 2 together (measured: see the verify doc)
    load: int  # 0 permanent, 1 variable
    _block: bytes = field(default=b"", repr=False, compare=False)  # the block it was read from ...
    _list_end: int = field(default=0, repr=False, compare=False)  # ... and where its id list ends

    def listed_ids(self, count: int) -> tuple[int, ...] | None:
        """The ids of its load cases, if the record lists exactly ``count`` of them."""
        return _listed(self._block, self._list_end, count)


@dataclass(frozen=True)
class LoadCase:
    name: str
    action: int  # 0 permanent, 1 variable
    _block: bytes = field(default=b"", repr=False, compare=False)
    _list_end: int = field(default=0, repr=False, compare=False)

    def listed_ids(self, count: int) -> tuple[int, ...] | None:
        """The ids of its loads, if the record lists exactly ``count`` of them."""
        return _listed(self._block, self._list_end, count)


@dataclass(frozen=True)
class PointLoad:
    """A point force: free (global x, y) or on a beam (member id, relative position)."""

    name: str
    load_id: int | None  # None for beam loads: not found in their record
    case_id: int
    value: float  # N, along the load's direction (Z for everything LLSM writes)
    x: float | None = None  # m, free loads
    y: float | None = None
    member_id: int | None = None  # beam loads
    position: float | None = None  # relative, beam loads


@dataclass
class LoadModel:
    """What :func:`read_loads` found: the geometry a load needs, and the loads themselves."""

    members: list[Member]
    nodes: list[Node]
    plates: list[Plate]
    groups: list[LoadGroup]
    cases: list[LoadCase]
    loads: list[PointLoad]
    saved_by: str | None = None
    warnings: list[str] = field(default_factory=list)

    def names(self) -> set[str]:
        return {o.name for o in (*self.groups, *self.cases, *self.loads)}


def _listed(block: bytes, end: int, count: int) -> tuple[int, ...] | None:
    """The ``count`` u32 ids before ``end``, if the u32 in front of them says ``count``.

    The list is read backwards from its end, so its length cannot be read from it: a caller
    asks for the count it expects, and gets None when the record does not say exactly that.
    """
    start = end - 4 * (count + 1)
    if count < 0 or start < 0:
        return None
    values = struct.unpack_from(f"<{count + 1}I", block, start)
    return tuple(values[1:]) if values[0] == count else None


def _f64(chunk: bytes, offset: int) -> float | None:
    try:
        value = struct.unpack_from("<d", chunk, offset)[0]
    except struct.error:
        return None
    return value if math.isfinite(value) else None


def _u32(chunk: bytes, offset: int) -> int | None:
    try:
        return struct.unpack_from("<I", chunk, offset)[0]
    except struct.error:
        return None


def _node(chunk: bytes, end: int, name: str) -> Node | None:
    guid = chunk.find(NODE_GUID, end, end + 80)
    if guid < 0:
        return None
    node_id = _u32(chunk, guid + 16)
    point = tuple(_f64(chunk, guid + offset) for offset in (40, 48, 56))
    if not node_id or any(v is None or abs(v) > 1e5 for v in point):
        return None
    return Node(name, node_id, point)  # type: ignore[arg-type]


def _plate(chunk: bytes, end: int, record_end: int, name: str) -> Plate:
    """Every node the record refers to, in order. Internal nodes, if any, would be among them."""
    ids: list[int] = []
    at = chunk.find(NODE_GUID, end, record_end)
    while at >= 0:
        node_id = _u32(chunk, at + 16)
        if node_id and node_id not in ids:
            ids.append(node_id)
        at = chunk.find(NODE_GUID, at + 16, record_end)
    return Plate(name, tuple(ids), ())


def _group(chunk: bytes, start: int, end: int, name: str) -> LoadGroup | None:
    if end + 4 > len(chunk) or start < _LIST_END:
        return None
    return LoadGroup(name, chunk[end + 1], chunk[end + 3], chunk, start - _LIST_END)


def _case(chunk: bytes, start: int, end: int, name: str) -> LoadCase | None:
    if end + 4 > len(chunk) or start < _LIST_END:
        return None
    return LoadCase(name, chunk[end + 3], chunk, start - _LIST_END)


def _free_force(chunk: bytes, end: int, name: str) -> PointLoad | None:
    if chunk[end + 89:end + 105] != CASE_GUID:
        return None
    case_id, load_id = _u32(chunk, end + 109), _u32(chunk, end + 169)
    value, x, y = _f64(chunk, end + 450), _f64(chunk, end + 47), _f64(chunk, end + 59)
    if not case_id or not load_id or value is None or x is None or y is None:
        return None
    return PointLoad(name, load_id, case_id, value, x=x, y=y)


def _beam_force(chunk: bytes, end: int, name: str) -> PointLoad | None:
    if chunk[end + 19:end + 35] != BEAM_GUID or chunk[end + 293:end + 309] != CASE_GUID:
        return None
    member_id, case_id = _u32(chunk, end + 284), _u32(chunk, end + 313)
    value, position = _f64(chunk, end + 378), _f64(chunk, end + 173)
    if not member_id or not case_id or value is None or position is None:
        return None
    return PointLoad(name, None, case_id, value, member_id=member_id, position=position)


def _table_of(chunk: bytes) -> str | None:
    found = names_in(chunk[:400])
    if found and found[0][0] == 0:
        header = _TABLE_HEADER.match(found[0][1])
        if header:
            return header.group(1)
    return None


def read_loads(path: Path) -> LoadModel:
    """Read members, nodes, plates, load groups, load cases and point forces.

    Every table is cross-checked against the object count SCIA logs in the file; a
    mismatch raises EsaFormatError. Bedding uses :func:`read_esa`, which does not read
    these tables, so a layout this reader does not know cannot block a bedding.
    The file is read twice (once by read_esa): a few seconds on a 40 MB model.
    """
    base = read_esa(path)
    found: dict[str, list] = {table: [] for table in LOAD_TABLES}
    declared: dict[str, int] = {}
    for chunk in iter_blocks(Path(path).read_bytes()):
        declared.update((m.group(2).decode(), int(m.group(1))) for m in _ANY_OBJECT_LOG.finditer(chunk))
        table = _table_of(chunk)
        if table not in LOAD_TABLES:
            continue
        records = [(s, n) for s, n in names_in(chunk) if s >= 4 and chunk[s - 4:s] == _TOP_LEVEL]
        for index, (start, name) in enumerate(records):
            end = start + 2 + 2 * len(name)
            record_end = records[index + 1][0] if index + 1 < len(records) else len(chunk)
            if table == _NODE_TABLE:
                item = _node(chunk, end, name)
            elif table == _PLATE_TABLE:
                item = _plate(chunk, end, record_end, name)
            elif table == _GROUP_TABLE:
                item = _group(chunk, start, end, name)
            elif table == _CASE_TABLE:
                item = _case(chunk, start, end, name)
            elif table == _FREE_FORCE_TABLE:
                item = _free_force(chunk, end, name)
            else:
                item = _beam_force(chunk, end, name)
            if item is not None:
                found[table].append(item)

    wrong = {t: (len(found[t]), declared.get(t, 0)) for t in LOAD_TABLES if len(found[t]) != declared.get(t, 0)}
    if wrong:
        raise EsaFormatError(f"{Path(path).name}: reader found / SCIA declares {wrong}")
    nodes = {n.node_id: n.point for n in found[_NODE_TABLE]}
    plates = [
        Plate(p.name, p.node_ids, tuple(nodes[i] for i in p.node_ids) if all(i in nodes for i in p.node_ids) else ())
        for p in found[_PLATE_TABLE]
    ]
    return LoadModel(
        base.members, found[_NODE_TABLE], plates, found[_GROUP_TABLE], found[_CASE_TABLE],
        found[_FREE_FORCE_TABLE] + found[_BEAM_FORCE_TABLE], base.saved_by, list(base.warnings),
    )


# --- Engineering reports and stored results (docs/verify/report-route.md) ------------------
#
# Each engineering report keeps its header and item tree as small XML blocks
# (DOCUMENTVIEWDATASTORAGE_*). <Name> is what ESA_XML's -d switch takes. The item tree holds
# ids only: what an item prints, and whether it is of the old post-processor type, is not in
# the file; only printing the report shows it.

_REPORT_MARK = b"DOCUMENTVIEWDATASTORAGE"
_REPORT_INFO = re.compile(r"<DOCUMENTVIEWDATASTORAGE_DOCUMENTINFO\b.*?<Name>([^<]*)</Name>(?:.*?<CountOfPages>(\d+)</CountOfPages>)?", re.S)
_REPORT_ITEMS = re.compile(r"<DOCUMENTVIEWDATASTORAGE_ITEMS_ORDER\b")
_ITEM = re.compile(r"<DocumentItem\b")
# SCIA logs one of these per load case that has results stored (seen: plate 0 -> 4 after LIN).
RESULT_MARKERS = ("EP_Results.IntForcesAddCases.1", "EP_Results.DeforAddCases.1")
NONLINEAR_MARKERS = ("DataSetSciaTom.EP_NonlinearCombi.1",)


@dataclass(frozen=True)
class StoredReport:
    name: str
    pages: int | None  # as SCIA last laid it out
    items: int | None  # entries in its item tree; None when the tree block was not found


@dataclass
class ReportIndex:
    reports: list[StoredReport]
    counts: dict[str, int]  # SCIA's object log: class progid -> count
    saved_by: str | None

    @property
    def cases_with_results(self) -> int:
        return max((self.counts.get(m, 0) for m in RESULT_MARKERS), default=0)

    @property
    def nonlinear(self) -> list[str]:
        return [f"{m} ({self.counts[m]})" for m in NONLINEAR_MARKERS if self.counts.get(m)]


def read_report_index(path: Path) -> ReportIndex:
    """The engineering reports stored in a model, its object counts and version, in one pass."""
    infos: list[tuple[str, int | None]] = []
    trees: list[int] = []
    counts: dict[str, int] = {}
    stamps: set[str] = set()
    for chunk in iter_blocks(Path(path).read_bytes()):
        counts.update((m.group(2).decode(), int(m.group(1))) for m in _ANY_OBJECT_LOG.finditer(chunk))
        stamps |= _stamps_in(chunk)
        if _REPORT_MARK not in chunk[:400]:
            continue
        text = chunk.decode("utf-8", "replace")
        info = _REPORT_INFO.search(text)
        if info:
            infos.append((info.group(1).strip(), int(info.group(2)) if info.group(2) else None))
        elif _REPORT_ITEMS.search(text):
            trees.append(len(_ITEM.findall(text)))
    # The info block and the tree block of one report are written one after the other.
    reports = [
        StoredReport(name, pages, trees[i] if len(trees) == len(infos) else None)
        for i, (name, pages) in enumerate(infos)
    ]
    return ReportIndex(reports, counts, _latest(stamps) if stamps else None)


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
