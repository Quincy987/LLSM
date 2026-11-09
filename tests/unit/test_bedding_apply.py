"""The result must follow what is in the saved file, never the writer's exit code."""
from pathlib import Path

import pytest

from llsm.bedding.apply import PlanRequest, apply_bedding, next_output_path, preview_bedding
from llsm.scia.esa_file import EsaFormatError, EsaModel, LineSupport, Member
from llsm.scia.esa_xml import EsaXmlError, RunResult

ROWS = [{"top": -2, "bottom": -8, "value": 5000, "unit": "kh_kN_m3", "description": "klei"}]
PILE = Member("P1", 7, (0, 0, -1), (0, 0, -21))
REQUEST = PlanRequest(diameter_m=0.45)
GOOD = LineSupport("LLSM:P1:1", 7, 0.05, 0.35, 2.25e6, 2.25e6)
OK = RunResult(0, "Succeeded", 1.0, "", "")


def model(*supports):
    return EsaModel([PILE], list(supports), "+D_344_24.0.3015.64")


class Fake:
    """Reader and writer: the model before, and what the 'saved' file holds."""

    def __init__(self, tmp_path, after, run=OK):
        self.model_path = tmp_path / "m.esa"
        self.model_path.write_bytes(b"original")
        self.before, self.after, self.run = model(), after, run
        self.xml = None

    def reader(self, path):
        return self.before if path == self.model_path else self.after

    def writer(self, source, output, xml):
        assert source == self.model_path
        self.xml = xml
        if self.run.succeeded:
            output.write_bytes(b"saved")
        return self.run

    def apply(self, request=REQUEST):
        return apply_bedding(self.model_path, ROWS, request, reader=self.reader, writer=self.writer)


def test_preview_writes_nothing(tmp_path):
    fake = Fake(tmp_path, model())
    result = preview_bedding(fake.model_path, ROWS, REQUEST, reader=fake.reader)
    assert result["ok"] and result["preview"][0]["segment"] == "LLSM:P1:1"
    assert [p.name for p in tmp_path.iterdir()] == ["m.esa"]


def test_good_write_is_ok(tmp_path):
    fake = Fake(tmp_path, model(GOOD))
    result = fake.apply()
    assert result["ok"] and result["written"] == result["expected"] == 1
    assert Path(result["output"]).name == "m_llsm-01.esa"
    assert fake.model_path.read_bytes() == b"original"


@pytest.mark.parametrize(
    "stored",
    [
        LineSupport("LLSM:P1:1", 99, 0.05, 0.35, 2.25e6, 2.25e6),
        LineSupport("LLSM:P1:1", 7, 0.06, 0.35, 2.25e6, 2.25e6),
        LineSupport("LLSM:P1:1", 7, 0.05, 0.35, 2.25e3, 2.25e6),
        LineSupport("LLSM:P1:1", 7, 0.05, 0.35, 2.25e6, 2.25e3),
        LineSupport("LLSM:P1:1", 7, None, None, 2.25e6, 2.25e6),
    ],
)
def test_any_mismatch_is_not_ok(tmp_path, stored):
    result = Fake(tmp_path, model(stored)).apply()
    assert not result["ok"] and result["mismatches"]
    assert result["written"] == 0


def test_a_missing_support_is_not_ok_although_the_writer_succeeded(tmp_path):
    result = Fake(tmp_path, model()).apply()
    assert not result["ok"] and "not in the saved file" in result["mismatches"][0]


def test_a_bad_file_gets_a_name_nobody_will_mistake(tmp_path):
    result = Fake(tmp_path, model()).apply()
    assert Path(result["output"]).name == "m_llsm-01_FAILED.esa"
    assert not (tmp_path / "m_llsm-01.esa").exists()


def test_another_support_changing_is_not_ok(tmp_path):
    other = LineSupport("Sn1", 99, 0.1, 0.2, 1e6, 1e6)
    fake = Fake(tmp_path, model(GOOD, LineSupport("Sn1", 99, 0.1, 0.2, 2e6, 2e6)))
    fake.before = model(other)
    result = fake.apply()
    assert not result["ok"] and result["other_changes"] == ["support Sn1 changed"]


def test_another_support_disappearing_is_not_ok(tmp_path):
    fake = Fake(tmp_path, model(GOOD))
    fake.before = model(LineSupport("Sn1", 99, 0.1, 0.2, 1e6, 1e6))
    assert fake.apply()["other_changes"] == ["support Sn1 is gone"]


def test_a_moved_member_is_not_ok(tmp_path):
    fake = Fake(tmp_path, model(GOOD))
    fake.after.members = [Member("P1", 7, (0, 0, -1), (0, 0, -30))]
    result = fake.apply()
    assert not result["ok"] and "member P1 changed" in result["other_changes"]


def test_an_intended_overwrite_is_not_another_change(tmp_path):
    fake = Fake(tmp_path, model(GOOD))
    fake.before = model(LineSupport("LLSM:P1:1", 7, 0.05, 0.35, 9e6, 9e6))
    result = fake.apply(PlanRequest(diameter_m=0.45, replace=True))
    assert result["ok"] and not result["other_changes"]


def test_a_failed_save_is_not_ok(tmp_path):
    fake = Fake(tmp_path, model(GOOD), RunResult(7, "SCIA rejected the XML update", 1.0, "", ""))
    result = fake.apply()
    assert not result["ok"] and "rejected" in result["problems"][0] and result["output"] is None


def test_an_unreadable_output_is_not_ok(tmp_path):
    fake = Fake(tmp_path, model(GOOD))

    def reader(path):
        if path == fake.model_path:
            return fake.before
        raise EsaFormatError("counts differ")

    result = apply_bedding(fake.model_path, ROWS, REQUEST, reader=reader, writer=fake.writer)
    assert not result["ok"] and "read back" in result["problems"][0]
    assert result["output"].endswith("_FAILED.esa")


def test_scia_running_is_reported_not_raised(tmp_path):
    fake = Fake(tmp_path, model(GOOD))

    def writer(source, output, xml):
        raise EsaXmlError("SCIA is running")

    result = apply_bedding(fake.model_path, ROWS, REQUEST, reader=fake.reader, writer=writer)
    assert not result["ok"] and result["problems"] == ["SCIA is running"]


def test_problems_stop_before_writing(tmp_path):
    fake = Fake(tmp_path, model(GOOD))
    fake.before = model(LineSupport("Sn1", 7, None, None, 1e6, 1e6))
    result = fake.apply()
    assert not result["ok"] and fake.xml is None and "double" in result["problems"][0]


def test_all_piles_skipped_writes_nothing(tmp_path):
    fake = Fake(tmp_path, model(GOOD))
    fake.before = model(GOOD)
    result = fake.apply()
    assert not result["ok"] and fake.xml is None and "skipped" in result["problems"][0]


def test_bad_layers_are_reported(tmp_path):
    fake = Fake(tmp_path, model(GOOD))
    result = apply_bedding(fake.model_path, [], REQUEST, reader=fake.reader, writer=fake.writer)
    assert not result["ok"] and "empty" in result["problems"][0]


def test_missing_model_is_reported(tmp_path):
    result = preview_bedding(tmp_path / "nope.esa", ROWS, REQUEST)
    assert not result["ok"] and "not found" in result["problems"][0]


def test_output_numbering_continues_from_an_earlier_output(tmp_path):
    (tmp_path / "m_llsm-01.esa").write_bytes(b"x")
    assert next_output_path(tmp_path / "m.esa").name == "m_llsm-02.esa"
    assert next_output_path(tmp_path / "m_llsm-01.esa").name == "m_llsm-02.esa"


def test_output_folder_can_differ(tmp_path):
    other = tmp_path / "out"
    other.mkdir()
    assert next_output_path(tmp_path / "m.esa", other).parent == other
