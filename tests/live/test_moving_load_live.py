"""The hand case end to end through ESA_XML. Needs SCIA, a licence, LLSM_LIVE=1 and LLSM_LIVE_DECK=<path>.

The deck is the plate fixture of phase0/moving_load/make_plate.py: plate D1, 6000 x 4000 mm at
z = 0 on four columns, no loads of its own (docs/verify/phase3-moving-load.md).
"""
import hashlib
import os
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from llsm.moving_load import MovingLoadRequest, apply_moving_load, preview_moving_load
from llsm.scia.esa_file import read_loads
from llsm.scia.esa_xml import running_scia

SOURCE = Path(os.environ.get("LLSM_LIVE_DECK", ""))
REQUEST = MovingLoadRequest(
    path_mm=[[500, 2000], [4900, 2000]], step_mm=1600,
    axles=[{"load_kn": 200, "offset_mm": 0}, {"load_kn": 100, "offset_mm": 1200}], track_mm=2000,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("LLSM_LIVE") != "1" or not SOURCE.is_file(),
    reason="set LLSM_LIVE=1 and LLSM_LIVE_DECK to run against SCIA",
)


@pytest.fixture
def deck(tmp_path):
    if running_scia():
        pytest.skip("a SCIA is running; never touch someone's session")
    copy = tmp_path / "deck.esa"
    shutil.copy2(SOURCE, copy)
    return copy


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_hand_case_lands_exactly_as_planned(deck):
    before = digest(deck)
    preview = preview_moving_load(deck, REQUEST)
    assert preview["ok"] and preview["cases"] == 3 and preview["loads"] == 12
    assert sorted(p.name for p in deck.parent.iterdir()) == ["deck.esa"]

    result = apply_moving_load(deck, REQUEST)
    assert result["ok"], result
    assert (result["written"], result["cases_confirmed"]) == (12, 3)
    assert not result["mismatches"] and not result["other_changes"]
    assert digest(deck) == before

    saved = {load.name: load for load in read_loads(Path(result["output"])).loads}
    assert (saved["LLSM-ML TS1 02 W1"].x, saved["LLSM-ML TS1 02 W1"].y, saved["LLSM-ML TS1 02 W1"].value) == (3.3, 1.0, -100000.0)
    assert sum(load.value for load in saved.values()) == pytest.approx(-900000.0)


def test_writing_again_needs_replace_and_then_overwrites(deck):
    first = apply_moving_load(deck, REQUEST)
    assert first["ok"], first
    again = apply_moving_load(Path(first["output"]), REQUEST)
    assert not again["ok"] and "already exist" in again["problems"][0]
    replaced = apply_moving_load(Path(first["output"]), replace(REQUEST, replace=True))
    assert replaced["ok"] and replaced["written"] == 12, replaced
