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


def test_surface_load_writes_every_column_up_to_the_geometry():
    """With a gap SCIA read the geometry as another column; the value goes in q1 (S9, phase 4 verify)."""
    from llsm.scia.load_xml import SurfaceForceRow
    patch = SurfaceForceRow("C W1", "C", -937500.0, ((1.0, 1.0, 0.0), (1.4, 1.0, 0.0), (1.4, 1.4, 0.0), (1.0, 1.4, 0.0)))
    root = ET.fromstring(build_load_update("G", [LoadCaseRow("C")], [], surfaces=[patch]))
    table = next(t for t in root.iterfind(".//s:table", NS) if t.get("t") == "DataAddLoad.EP_SurfaceForceFree.1")
    headers = [h.get("t") for h in table.find("s:h", NS)]
    assert headers == ["Name", "Load case", "Direction", "Type", "Distribution", "q", "q1", "q2", "q3", "Validity",
                       "Select", "System", "Location", "Table of geometry"]
    obj = table.find("s:obj", NS)
    assert obj.find("s:p6", NS).get("v") == "-937500"
    rows = obj.find("s:p13", NS).findall("s:row", NS)
    assert [(r.find("s:p0", NS).get("v"), r.find("s:p1", NS).get("v"), r.find("s:p3", NS).get("v")) for r in rows] == [
        ("1", "1", "Line"), ("1.4", "1", "Line"), ("1.4", "1.4", "Line"), ("1", "1.4", "Line"),
    ]


def test_several_groups_in_one_update():
    from llsm.scia.load_xml import build_groups_update
    xml = build_groups_update([("G1", [LoadCaseRow("A 01"), LoadCaseRow("A 02")]), ("G2", [LoadCaseRow("B 01")])],
                              [PointForceRow("B 01 W1", "B 01", -1.0, x=0.0, y=0.0)])
    root = ET.fromstring(xml)
    tables = {t.get("t"): t for t in root.iterfind(".//s:table", NS)}
    cases = tables["DataSetScia.EP_LoadCase.1"].findall("s:obj", NS)
    assert [(c.get("id"), c.find("s:p3", NS).get("n"), c.find("s:p3", NS).get("i")) for c in cases] == [
        ("1", "G1", "1"), ("2", "G1", "1"), ("3", "G2", "2"),
    ]
    force = tables["DataAddLoad.EP_PointForceFree.1"].find("s:obj", NS)
    assert force.find("s:p1", NS).get("i") == "3"
    with pytest.raises(ValueError, match="unique"):
        build_groups_update([("G", [LoadCaseRow("A")]), ("G", [LoadCaseRow("B")])], [])
