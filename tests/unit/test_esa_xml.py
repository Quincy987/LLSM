"""The update XML, and the guards that run before anything is started."""
import stat
from pathlib import Path

import pytest

from llsm.scia import esa_xml
from llsm.scia.esa_xml import ModelLocked, SciaRunning, UpdateSupport, assert_no_scia, assert_unlocked, build_update

GOLDEN = Path(__file__).parent / "data" / "bedding_update.xml"
SUPPORT = UpdateSupport("LLSM:S1:1", "S1", 6, 0.05, 0.35, 2250000.0)


def test_update_xml_matches_the_golden_file():
    assert build_update([SUPPORT]) == GOLDEN.read_text(encoding="utf-8")


def test_names_are_escaped():
    assert 'nm="LLSM:A&amp;B:1"' in build_update([UpdateSupport("LLSM:A&B:1", "A&B", 1, 0.1, 0.2, 1.0)])


def test_unknown_member_id_is_written_as_zero():
    assert '<p2 v="0"/>' in build_update([UpdateSupport("X", "S1", None, 0.1, 0.2, 1.0)])


def test_torsion_spring_is_optional():
    assert '<p10 v="1000.0"/>' in build_update([UpdateSupport("X", "S1", 1, 0.1, 0.2, 1.0, torsion=1000.0)])
    assert "<p10" not in build_update([SUPPORT])


def test_the_def_file_ships_with_the_package():
    assert esa_xml.DEF_FILE.is_file()
    assert "EP_LineSupportLine" in esa_xml.DEF_FILE.read_text(encoding="utf-8")


def test_a_running_scia_stops_the_update(monkeypatch):
    monkeypatch.setattr(esa_xml, "running_scia", lambda: [(1, "sciaengineer.exe")])
    with pytest.raises(SciaRunning, match="sciaengineer"):
        assert_no_scia()


def test_a_read_only_file_is_not_locked(tmp_path):
    path = tmp_path / "m.esa"
    path.write_bytes(b"x")
    path.chmod(stat.S_IREAD)
    try:
        assert_unlocked(path)
    finally:
        path.chmod(stat.S_IWRITE)


def test_a_file_held_open_is_locked(tmp_path):
    path = tmp_path / "m.esa"
    path.write_bytes(b"x")
    assert_unlocked(path)
    with open(path, "rb"):
        with pytest.raises(ModelLocked):
            assert_unlocked(path)
