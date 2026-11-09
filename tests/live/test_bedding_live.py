"""End to end on a copy of a real model. Needs SCIA, a licence, LLSM_LIVE=1 and LLSM_LIVE_MODEL=<path>.

The model needs a pile S4 (member id 6) with existing line supports.
"""
import hashlib
import os
import shutil
from pathlib import Path

import pytest

from llsm.bedding import PlanRequest, apply_bedding, preview_bedding
from llsm.scia.esa_file import read_esa
from llsm.scia.esa_xml import running_scia

SOURCE = Path(os.environ.get("LLSM_LIVE_MODEL", ""))  # kept out of the repo: it names a real project
ROWS = [
    {"top": -2, "bottom": -8, "value": 5000, "unit": "kh_kN_m3", "description": "klei"},
    {"top": -8, "bottom": -14, "value": 8000, "unit": "kh_kN_m3", "description": "zand, kleiig"},
    {"top": -14, "bottom": -20, "value": 12000, "unit": "kh_kN_m3", "description": "zand"},
]
REQUEST = PlanRequest(piles="S4", diameter_m=0.45, start_level_nap=-1.1, allow_existing=True)

pytestmark = pytest.mark.skipif(
    os.environ.get("LLSM_LIVE") != "1" or not SOURCE.is_file(),
    reason="set LLSM_LIVE=1 and LLSM_LIVE_MODEL to run against SCIA",
)


@pytest.fixture
def model(tmp_path):
    if running_scia():
        pytest.skip("a SCIA is running; never touch someone's session")
    copy = tmp_path / "model.esa"
    shutil.copy2(SOURCE, copy)
    return copy


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_apply_lands_exactly_what_was_planned(model):
    before = digest(model)
    preview = preview_bedding(model, ROWS, REQUEST)
    assert preview["ok"] and len(preview["preview"]) == 3
    assert sorted(p.name for p in model.parent.iterdir()) == ["model.esa"]

    result = apply_bedding(model, ROWS, REQUEST)
    assert result["ok"], result
    assert result["written"] == 3 and not result["mismatches"] and not result["other_changes"]
    assert digest(model) == before

    saved = {s.name: s for s in read_esa(Path(result["output"])).supports}
    assert saved["LLSM:S4:1"].member_id == 6
    assert saved["LLSM:S4:1"].stiffness_y == pytest.approx(2.25e6)


def test_foreign_supports_are_refused_without_the_flag(model):
    result = apply_bedding(model, ROWS, PlanRequest(piles="S4", diameter_m=0.45))
    assert not result["ok"] and "double the stiffness" in result["problems"][0]
