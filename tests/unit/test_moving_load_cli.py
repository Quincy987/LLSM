"""The CLI report prints every result shape without a missing key, and says OK only for ok."""
from .test_moving_load_apply import REQUEST, Fake, written_model

from llsm.moving_load import cli
from llsm.moving_load.apply import preview_moving_load


def test_preview_report(tmp_path, capsys):
    fake = Fake(tmp_path, None)
    assert cli._report(preview_moving_load(fake.model_path, REQUEST, reader=fake.reader)) == 0
    out = capsys.readouterr().out
    assert "Planned: 3 load cases, 12 loads" in out and "LLSM-ML TS1 03" in out and out.rstrip().endswith("OK")


def test_apply_report(tmp_path, capsys):
    assert cli._report(Fake(tmp_path, written_model()).apply()) == 0
    assert "Written and confirmed: 12 of 12 loads, 3 of 3 cases" in capsys.readouterr().out


def test_failed_apply_report(tmp_path, capsys):
    after = written_model()
    del after.loads[0]
    assert cli._report(Fake(tmp_path, after).apply()) == 1
    out = capsys.readouterr().out
    assert "MISMATCH" in out and out.rstrip().endswith("NOT OK")


def test_argument_parsing():
    args = cli._parser().parse_args(["preview", "m.esa", "--path", "0,0;10,0", "--step", "5", "--axles", "200@0,100@1200"])
    assert args.path == [[0, 0], [10, 0]] and args.axles[1] == {"load_kn": 100, "offset_mm": 1200}
