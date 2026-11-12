"""The load update XML: shape, units, references. Snapshot of the hand case in tests/unit/data."""
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from llsm.scia.load_xml import LoadCaseRow, PointForceRow, build_load_update, metre, newton, number

NS = {"s": "http://www.scia.cz"}
DATA = Path(__file__).parent / "data"


def hand_case_xml() -> str:
    cases, forces = [], []
    for k, front in enumerate((1.7, 3.3, 4.9), start=1):
        name = f"LLSM-ML TS1 {k:02d}"
        cases.append(LoadCaseRow(name, f"front axle at {front * 1000:.0f} mm"))
        wheel = 0
        for x, value in ((front, -100000.0), (round(front - 1.2, 6), -50000.0)):
            for y in (1.0, 3.0):
                wheel += 1
                forces.append(PointForceRow(f"{name} W{wheel}", name, value, x=x, y=y))
    return build_load_update("LLSM-ML TS1", cases, forces)


def test_units_are_si():
    assert newton(-100) == -100000 and metre(1700) == 1.7


def test_hand_case_snapshot():
    assert hand_case_xml() == (DATA / "moving_load_update.xml").read_text(encoding="utf-8")


def test_references_resolve_inside_the_document():
    root = ET.fromstring(hand_case_xml())
    tables = {t.get("t"): t for t in root.iterfind(".//s:table", NS)}
    cases = {o.get("id"): o.get("nm") for o in tables["DataSetScia.EP_LoadCase.1"].iterfind("s:obj", NS)}
    for force in tables["DataAddLoad.EP_PointForceFree.1"].iterfind("s:obj", NS):
        ref = force.find("s:p1", NS)
        assert cases[ref.get("i")] == ref.get("n") and force.get("nm").startswith(ref.get("n"))
    group = tables["DataSetScia.EP_LoadGroup.1"].find("s:obj", NS)
    assert [group.find(f"s:p{i}", NS).get("v") for i in (1, 2)] == ["1", "1"]  # exclusive, variable


def test_beam_force():
    xml = build_load_update(
        "G", [LoadCaseRow("G 01")],
        [PointForceRow("G 01 A1", "G 01", -1000.0, member_name="L1", member_id=4, position=0.25)],
    )
    beam = ET.fromstring(xml).find(".//s:table[@t='DataAddLoad.EP_PointForceLine.1']/s:obj", NS)
    assert beam.find("s:p2/s:row/s:p3", NS).get("v") == "L1" and beam.find("s:p8", NS).get("v") == "0.25"
    assert beam.find("s:p5", NS).get("v") == "-1000"


@pytest.mark.parametrize("value, text", [(-100000.0, "-100000"), (1.7, "1.7"), (1e-16, "0"), (-1e-16, "0"), (0.1 + 0.2, "0.3")])
def test_numbers_never_use_exponents(value, text):
    assert number(value) == text


def test_cases_are_variable_and_static():
    case = ET.fromstring(hand_case_xml()).find(".//s:table[@t='DataSetScia.EP_LoadCase.1']/s:obj", NS)
    assert case.find("s:p1", NS).get("v") == "1" and case.find("s:p4", NS).get("v") == "0"


def test_force_on_an_unwritten_case_is_refused():
    with pytest.raises(ValueError, match="not written"):
        build_load_update("G", [LoadCaseRow("G 01")], [PointForceRow("x", "G 02", -1.0, x=0.0, y=0.0)])


def test_names_are_escaped():
    xml = build_load_update('G "1"', [LoadCaseRow("G <1>")], [])
    assert 'nm=\'G "1"\'' in xml and "G &lt;1&gt;" in xml
    ET.fromstring(xml)
