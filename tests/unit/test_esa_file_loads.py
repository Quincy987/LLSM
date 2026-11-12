"""read_loads: nodes, plates, groups, cases and point forces, in a synthetic file in the real layout.

The offsets are the ones decoded on the plate fixture (docs/verify/phase3-moving-load.md).
"""
import struct
import uuid
import zlib

import pytest

from llsm.scia.esa_file import CASE_GUID, EsaFormatError, read_loads

NODE = uuid.UUID("39A7F468-A0D4-4DFF-8E5C-5843E1807D13").bytes_le
BEAM = uuid.UUID("ECB5D684-7357-11D4-9F6C-00104BC3B443").bytes_le
TABLES = {
    "node": ("39A7F468-A0D4-4DFF-8E5C-5843E1807D13", "EP_DSG_Elements.EP_StructNode.1"),
    "plate": ("8708ED31-8E66-11D4-AD94-F6F5DE2BE344", "EP_DSG_Elements.EP_Plane.1"),
    "group": ("F9D4AA72-49D5-11D4-A3CF-000000000000", "DataSetScia.EP_LoadGroup.1"),
    "case": ("0908D21F-481F-11D4-AB84-00C06C452330", "DataSetScia.EP_LoadCase.1"),
    "free": ("E03984FC-B420-4C03-8D2F-72EA2FAB147D", "DataAddLoad.EP_PointForceFree.1"),
    "beam": ("BC16B3C2-F464-11D4-94D3-000000000000", "DataAddLoad.EP_PointForceLine.1"),
}


def name_bytes(name):
    return struct.pack("<H", len(name)) + name.encode("utf-16le")


def top_level(name, body, before=b""):
    """A record as SCIA lays it out: [id list][object guid][2 bytes][u32 1][name][body]."""
    return before + uuid.uuid4().bytes_le + bytes(2) + struct.pack("<I", 1) + name_bytes(name) + body


def table_block(kind, records):
    guid, progid = TABLES[kind]
    return name_bytes(f"^$C3 {{{guid}}} {progid}") + bytes(8) + b"".join(records) + bytes(64)


def node_body(node_id, point):
    body = bytearray(140)
    body[51:67] = NODE
    struct.pack_into("<I", body, 67, node_id)
    struct.pack_into("<3d", body, 91, *point)
    return bytes(body)


def plate_body(node_ids):
    body = bytearray(100 + 52 * len(node_ids))
    for i, node_id in enumerate(node_ids):
        at = 60 + 52 * i
        body[at:at + 16] = NODE
        struct.pack_into("<I", body, at + 16, node_id)
    return bytes(body)


def flags(relation_or_unused=0, kind=0):
    body = bytearray(80)
    body[1], body[3] = relation_or_unused, kind
    return bytes(body)


def id_list(ids):
    return struct.pack(f"<{len(ids) + 1}I", len(ids), *ids)


def free_body(case_id, load_id, value, x, y):
    body = bytearray(870)
    struct.pack_into("<d", body, 47, x)
    struct.pack_into("<d", body, 59, y)
    body[89:105] = CASE_GUID
    struct.pack_into("<I", body, 109, case_id)
    struct.pack_into("<I", body, 169, load_id)
    struct.pack_into("<d", body, 450, value)
    return bytes(body)


def beam_body(case_id, member_id, value, position):
    body = bytearray(600)
    body[19:35] = BEAM
    body[264:280] = BEAM
    struct.pack_into("<d", body, 173, position)
    struct.pack_into("<I", body, 284, member_id)
    body[293:309] = CASE_GUID
    struct.pack_into("<I", body, 313, case_id)
    struct.pack_into("<d", body, 378, value)
    return bytes(body)


def loads_file(tmp_path, blocks, counts, stamp="+D_344_24.0.3015.64"):
    log = "".join(f"- 1 - {n} - {TABLES[k][1]} - x\n" for k, n in counts.items()).encode("ascii")
    tail = log + bytes(32) + stamp.encode("utf-16le") + bytes(32)
    path = tmp_path / "loads.esa"
    path.write_bytes(b"SEN\x00" + bytes(40) + b"".join(zlib.compress(b) + bytes(9) for b in [*blocks, tail]))
    return path


def full_file(tmp_path, **override):
    corners = ((0, 0, 0), (6, 0, 0), (6, 4, 0), (0, 4, 0))
    blocks = [
        table_block("node", [top_level(f"P{i}", node_body(i, p)) for i, p in enumerate(corners, 1)]),
        table_block("plate", [top_level("D1", plate_body([1, 2, 3, 4]))]),
        table_block("group", [top_level("LG1", flags()), top_level("LLSM-ML TS1", flags(1, 1), id_list([9, 10]))]),
        table_block("case", [
            top_level("BG1", flags(1, 0)),
            top_level("C 01", flags(1, 1), id_list([1, 2])),
            top_level("C 02", flags(1, 1), id_list([3])),
        ]),
        table_block("free", [
            top_level("C 01 W1", free_body(9, 1, -100000.0, 1.7, 1.0)),
            top_level("C 01 W2", free_body(9, 2, -100000.0, 1.7, 3.0)),
            top_level("C 02 W1", free_body(10, 3, -50000.0, 0.5, 1.0)),
        ]),
        table_block("beam", [top_level("C 02 A1", beam_body(10, 7, -20000.0, 0.75))]),
    ]
    counts = {"node": 4, "plate": 1, "group": 2, "case": 3, "free": 3, "beam": 1, **override}
    return loads_file(tmp_path, blocks, counts)


def test_every_table_is_read(tmp_path):
    model = read_loads(full_file(tmp_path))
    assert model.plates[0].outline == ((0, 0, 0), (6, 0, 0), (6, 4, 0), (0, 4, 0))
    group = next(g for g in model.groups if g.name == "LLSM-ML TS1")
    assert (group.relation, group.load, group.listed_ids(2)) == (1, 1, (9, 10))
    assert group.listed_ids(3) is None
    cases = {c.name: c for c in model.cases}
    assert cases["BG1"].action == 0 and cases["C 01"].action == 1 and cases["C 01"].listed_ids(2) == (1, 2)
    free = {l.name: l for l in model.loads if l.member_id is None}
    w2 = free["C 01 W2"]
    assert (w2.case_id, w2.load_id, w2.value, w2.x, w2.y) == (9, 2, -100000.0, 1.7, 3.0)
    beam = next(l for l in model.loads if l.member_id is not None)
    assert (beam.member_id, beam.case_id, beam.position, beam.value) == (7, 10, 0.75, -20000.0)
    assert model.names() >= {"LLSM-ML TS1", "C 01", "C 02 A1"}
    assert model.saved_by == "+D_344_24.0.3015.64"


def test_count_mismatch_with_scias_log_raises(tmp_path):
    with pytest.raises(EsaFormatError, match="EP_PointForceFree"):
        read_loads(full_file(tmp_path, free=4))


def test_sub_objects_are_not_records(tmp_path):
    """A group's "Belastingtype" sits in the same block, but is not preceded by the u32 1."""
    sub = struct.pack("<I", 256) + name_bytes("Belastingtype") + bytes(40)
    blocks = [table_block("group", [top_level("LG1", flags() + sub)])]
    assert [g.name for g in read_loads(loads_file(tmp_path, blocks, {"group": 1})).groups] == ["LG1"]


def test_free_load_without_its_case_reference_is_not_read(tmp_path):
    broken = bytearray(free_body(9, 1, -1.0, 0.0, 0.0))
    broken[89:105] = bytes(16)
    blocks = [table_block("free", [top_level("X", bytes(broken))])]
    with pytest.raises(EsaFormatError):
        read_loads(loads_file(tmp_path, blocks, {"free": 1}))


def test_block_without_a_header_is_not_read_as_the_table_before(tmp_path):
    """Supports right after the plate block must not be taken for plates (seen on the fixture)."""
    supports = top_level("Sn1", bytes(200))
    blocks = [table_block("plate", [top_level("D1", plate_body([1]))]), supports + bytes(64)]
    model = read_loads(loads_file(tmp_path, blocks, {"plate": 1}))
    assert [p.name for p in model.plates] == ["D1"] and model.plates[0].outline == ()
