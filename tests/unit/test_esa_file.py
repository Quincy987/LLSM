"""Reading a saved model: a synthetic file in the real layout, and the XML hand export."""
import struct
import uuid
import zlib

import pytest

from llsm.scia.esa_file import EsaFormatError, iter_blocks, read_esa, read_xml, saved_by_version

NODE = uuid.UUID("39A7F468-A0D4-4DFF-8E5C-5843E1807D13").bytes_le
BEAM = uuid.UUID("ECB5D684-7357-11D4-9F6C-00104BC3B443").bytes_le


def name_bytes(name):
    return struct.pack("<H", len(name)) + name.encode("utf-16le")


def member_record(name, member_id, begin, end, second_id=None):
    record = bytearray(1100)
    text = name_bytes(name)
    record[: len(text)] = text
    first = 117
    second = first + 80
    for offset, point in ((first, begin), (second, end)):
        record[offset : offset + 16] = NODE
        struct.pack_into("<3d", record, offset + 20, *point)
    marker = first + 460
    record[marker : marker + 4] = b"\x01\x00_\x00"
    struct.pack_into("<I", record, marker + 30, member_id)
    struct.pack_into("<I", record, marker + 236, member_id if second_id is None else second_id)
    return bytes(record)


def support_record(name, member_id, x1, x2, k, layout=1):
    record = bytearray(600)
    text = name_bytes(name)
    record[: len(text)] = text
    first = 33
    second = first + 173
    record[first : first + 16] = BEAM
    record[second : second + 16] = BEAM
    struct.pack_into("<I", record, second + 20, member_id)
    struct.pack_into("<I", record, first + 88, layout)
    struct.pack_into("<d", record, first + 126, x1)
    struct.pack_into("<d", record, first + 152, x2)
    struct.pack_into("<d", record, first + 253, k)
    struct.pack_into("<d", record, first + 269, k)
    return bytes(record)


def esa_file(tmp_path, records, members, supports, stamp="+D_344_24.0.3015.64"):
    log = (
        f"- 1 - {members} - EP_DSG_Elements.EP_Beam.1 - x\n- 1 - {supports} - DataAddSupport.EP_LineSupportLine.1 - x\n"
    ).encode("ascii")
    chunk = b"".join(records) + log + bytes(32) + stamp.encode("utf-16le") + bytes(32)
    path = tmp_path / "m.esa"
    path.write_bytes(b"SEN\x00" + bytes(40) + zlib.compress(chunk))
    return path


def test_reads_members_supports_and_version(tmp_path):
    path = esa_file(
        tmp_path,
        [member_record("S1", 6, (0, 0, -1), (0, 0, -19.5)), support_record("Sn1", 6, 0.1, 0.4, 1.5e6)],
        1,
        1,
    )
    model = read_esa(path)
    assert model.members[0].name == "S1" and model.members[0].member_id == 6
    assert model.members[0].begin == (0, 0, -1) and model.members[0].length == pytest.approx(18.5)
    support = model.supports[0]
    assert (support.member_id, support.x1, support.x2, support.stiffness_y) == (6, 0.1, 0.4, 1.5e6)
    assert model.saved_by == "+D_344_24.0.3015.64" and not model.warnings


def test_position_just_past_the_pile_end_is_kept(tmp_path):
    path = esa_file(tmp_path, [support_record("Sn1", 1, 0.95, 1.0044, 1e6)], 0, 1)
    assert read_esa(path).supports[0].x2 == 1.0044


def test_other_layout_gives_no_positions(tmp_path):
    path = esa_file(tmp_path, [support_record("Sn1", 1, 0.0, 0.43, 1e6, layout=0)], 0, 1)
    assert read_esa(path).supports[0].x1 is None


def test_disagreeing_id_fields_give_no_id(tmp_path):
    path = esa_file(tmp_path, [member_record("S1", 6, (0, 0, 0), (0, 0, -5), second_id=7)], 1, 0)
    assert read_esa(path).members[0].member_id is None


def test_count_mismatch_with_scias_log_raises(tmp_path):
    path = esa_file(tmp_path, [member_record("S1", 6, (0, 0, 0), (0, 0, -5))], 2, 0)
    with pytest.raises(EsaFormatError, match="reader found"):
        read_esa(path)


def test_file_without_a_version_stamp_raises(tmp_path):
    path = tmp_path / "x.esa"
    path.write_bytes(zlib.compress(bytes(64)))
    with pytest.raises(EsaFormatError, match="version stamp"):
        read_esa(path)


def test_untested_version_warns(tmp_path):
    path = esa_file(tmp_path, [], 0, 0, stamp="+D_400_26.0.1000.64")
    assert "26.0" in read_esa(path).warnings[0]


def test_latest_version_in_the_history_counts(tmp_path):
    path = esa_file(tmp_path, [], 0, 0, stamp="+D_261_19.1.3030.32")
    extra = "+D_261_19.1.4034.32".encode("utf-16le")
    data = zlib.decompress(path.read_bytes()[44:]) + extra + bytes(8)
    path.write_bytes(b"SEN\x00" + bytes(40) + zlib.compress(data))
    assert saved_by_version(path) == "+D_261_19.1.4034.32"


def test_every_block_is_found(tmp_path):
    data = b"SEN\x00" + zlib.compress(b"a" * 40) + bytes(9) + zlib.compress(b"b" * 40)
    assert [b[:1] for b in iter_blocks(data)] == [b"a", b"b"]


XML = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://www.scia.cz">
  <container t="EP_DSG_Elements.EP_StructNode.1"><table t="EP_DSG_Elements.EP_StructNode.1">
    <h><h0 t="Name"/><h1 t="Coord X"/><h2 t="Coord Y"/><h3 t="Coord Z"/></h>
    <obj id="1" nm="T"><p0 v="T"/><p1 v="0.0"/><p2 v="0"/><p3 v="0,5"/></obj>
    <obj id="2" nm="E"><p0 v="E"/><p1 v="0.0"/><p2 v="-2.5"/><p3 v="-10"/></obj>
  </table></container>
  <container t="EP_DSG_Elements.EP_Beam.1"><table t="EP_DSG_Elements.EP_Beam.1">
    <h><h0 t="Name"/><h1 t="Beg. node"/><h2 t="End node"/></h>
    <obj id="4" nm="B1"><p0 v="B1"/><p1 i="1" n="T"/><p2 i="2" n="E"/></obj>
  </table></container>
  <container t="DataAddSupport.EP_LineSupportLine.1"><table t="DataAddSupport.EP_LineSupportLine.1">
    <h><h0 t="Reference Table"/><h1 t="Stiffness Y"/><h2 t="Stiffness Z"/><h3 t="Position x1"/><h4 t="Position x2"/></h>
    <obj id="1" nm="Sn:1"><p0 t=""><row id="0"><p2 v="4"/></row></p0>
      <p1 v="2450"/><p2 v="2450"/><p3 v="0.11"/><p4 v="0.14"/></obj>
  </table></container>
</project>
"""


def test_xml_export(tmp_path):
    path = tmp_path / "export.xml"
    path.write_text(XML, encoding="utf-8")
    model = read_xml(path)
    assert (model.members[0].name, model.members[0].member_id) == ("B1", 4)
    assert model.members[0].begin == (0, 0, 0.5) and model.members[0].end == (0, -2.5, -10)
    support = model.supports[0]
    assert (support.name, support.member_id, support.x1, support.x2, support.stiffness_z) == ("Sn:1", 4, 0.11, 0.14, 2450)
