"""Print a report through ESA_XML. Needs SCIA, a licence, LLSM_LIVE=1 and LLSM_LIVE_REPORT_MODEL=<path>.

The model must be calculated and carry an engineering report of SCIA 24's type, like the plate
of docs/verify/report-route.md (phase0/moving_load/make_plate.py from the kademuur template,
then calculated). Takes several minutes: one XLSX print.
"""
import hashlib
import os
import shutil
from pathlib import Path

import pytest

from llsm.report import ReportRequest, preview_report, write_report
from llsm.scia.esa_xml import running_scia

SOURCE = Path(os.environ.get("LLSM_LIVE_REPORT_MODEL", ""))

pytestmark = pytest.mark.skipif(
    os.environ.get("LLSM_LIVE") != "1" or not SOURCE.is_file(),
    reason="set LLSM_LIVE=1 and LLSM_LIVE_REPORT_MODEL to run against SCIA",
)


@pytest.fixture
def model(tmp_path):
    if running_scia():
        pytest.skip("a SCIA is running; never touch someone's session")
    copy = tmp_path / "model.esa"
    shutil.copy2(SOURCE, copy)
    return copy


def test_xlsx_report_holds_results_and_the_model_is_unchanged(model):
    before = hashlib.sha256(model.read_bytes()).hexdigest()
    request = ReportRequest(formats=["xlsx"])
    preview = preview_report(model, request)
    assert preview["ok"] and preview["report"], preview
    result = write_report(model, request)
    assert result["ok"], result
    chapters = result["chapters"]
    assert chapters["written"] == chapters["expected"] > 0 and chapters["unsupported"] == 0
    assert Path(result["outputs"]["xlsx"]).name == "model_rapport-01.xlsx"
    assert hashlib.sha256(model.read_bytes()).hexdigest() == before
