"""The result must follow what is in the saved file, never the writer's exit code."""
import struct
from dataclasses import replace
from pathlib import Path

from llsm.moving_load.apply import MovingLoadRequest, apply_moving_load, preview_moving_load, update_xml
from llsm.moving_load.plan import build_plan
from llsm.moving_load.path import RoadPath
from llsm.moving_load.vehicle import make_vehicle
from llsm.scia.esa_file import EsaFormatError, LoadCase, LoadGroup, LoadModel, Plate, PointLoad
from llsm.scia.esa_xml import EsaXmlError, RunResult

DECK = Plate("D1", (1, 2, 3, 4), ((0, 0, 0), (6, 0, 0), (6, 4, 0), (0, 4, 0)))
REQUEST = MovingLoadRequest(
    path_mm=[[500, 2000], [4900, 2000]], step_mm=1600,
    axles=[{"load_kn": 200, "offset_mm": 0}, {"load_kn": 100, "offset_mm": 1200}], track_mm=2000,
)
OK = RunResult(0, "Succeeded", 1.0, "", "")
BASE_CASE = LoadCase("BG1", 0)


def model(groups=(), cases=(BASE_CASE,), loads=()):
    return LoadModel([], [], [DECK], list(groups), list(cases), list(loads), "+D_344_24.0.3015.64")


def listing(ids):
    """The id list SCIA keeps in front of a case's or group's name, and where it ends."""
    block = struct.pack(f"<{len(ids) + 1}I", len(ids), *ids)
    return block, len(block)


def written_model():
    """What a correct write of REQUEST reads back as (ids as SCIA gave them on the fixture)."""
    plan = build_plan(model(), make_vehicle(axles=REQUEST.axles, track_mm=2000), RoadPath.parse(REQUEST.path_mm), 1600)
    loads, cases, load_id = [], [], 0
    for number, case in enumerate(plan.cases):
        case_id = 9 + number
        ids = []
        for planned in plan.loads_of(case.name):
            load_id += 1
            ids.append(load_id)
            loads.append(PointLoad(planned.name, load_id, case_id, planned.value_kn * 1000, x=planned.x_mm / 1000, y=planned.y_mm / 1000))
        cases.append(LoadCase(case.name, 1, *listing(ids)))
    group = LoadGroup("LLSM-ML TS1", 1, 1, *listing([9, 10, 11]))
    return model(groups=(group,), cases=(BASE_CASE, *cases), loads=loads)


class Fake:
    def __init__(self, tmp_path, after, run=OK):
        self.model_path = tmp_path / "m.esa"
        self.model_path.write_bytes(b"original")
        self.before, self.after, self.run = model(), after, run
        self.xml = self.def_file = None

    def reader(self, path):
        if self.after is None and path != self.model_path:
            raise EsaFormatError("unreadable")
        return self.before if path == self.model_path else self.after

    def writer(self, source, output, xml, def_file=None):
        assert source == self.model_path
        self.xml, self.def_file = xml, def_file
        if self.run.succeeded:
            output.write_bytes(b"saved")
        return self.run

    def apply(self, request=REQUEST):
        return apply_moving_load(self.model_path, request, reader=self.reader, writer=self.writer)


def test_preview_writes_nothing_and_reports_the_file(tmp_path):
    fake = Fake(tmp_path, None)
    result = preview_moving_load(fake.model_path, REQUEST, reader=fake.reader)
    assert result["ok"] and result["planned_cases"] == 3 and result["planned_loads"] == 12
    assert result["saved_by"] == "+D_344_24.0.3015.64"  # the MCP state block keys on this
    assert [p.name for p in tmp_path.iterdir()] == ["m.esa"]


def test_good_write_is_ok(tmp_path):
    fake = Fake(tmp_path, written_model())
    result = fake.apply()
    assert result["ok"], result["mismatches"] + result["other_changes"] + result["problems"]
    assert (result["written"], result["expected"], result["cases_confirmed"]) == (12, 12, 3)
    assert Path(result["output"]).name == "m_llsm-01.esa"
    assert fake.model_path.read_bytes() == b"original"
    assert fake.def_file.name == "loads.xml.def"
    assert 'v="-100000"' in fake.xml and 'v="1.7"' in fake.xml  # SI in the XML


def test_wrong_value_is_a_mismatch(tmp_path):
    after = written_model()
    after.loads[0] = replace(after.loads[0], value=-100.0)
    result = Fake(tmp_path, after).apply()
    assert not result["ok"] and "value" in result["mismatches"][0] and result["written"] == 11
    assert result["cases_confirmed"] == 2  # the case of the wrong load does not count
    assert result["output"].endswith("_FAILED.esa")


def test_wrong_position_is_a_mismatch(tmp_path):
    after = written_model()
    after.loads[5] = replace(after.loads[5], x=after.loads[5].x + 0.001)
    result = Fake(tmp_path, after).apply()
    assert not result["ok"] and "x " in result["mismatches"][0]


def test_missing_load_is_a_mismatch(tmp_path):
    after = written_model()
    del after.loads[3]
    result = Fake(tmp_path, after).apply()
    assert not result["ok"] and "not in the saved file" in result["mismatches"][0]


def test_loads_of_two_cases_on_one_case_is_a_mismatch(tmp_path):
    after = written_model()
    after.loads[4:8] = [replace(load, case_id=9) for load in after.loads[4:8]]
    result = Fake(tmp_path, after).apply()
    assert not result["ok"] and any("same case" in m for m in result["mismatches"])


def test_group_that_is_not_exclusive_is_a_mismatch(tmp_path):
    after = written_model()
    after.groups[0] = replace(after.groups[0], relation=0)
    result = Fake(tmp_path, after).apply()
    assert not result["ok"] and "relation" in result["mismatches"][-1]


def test_renumbered_ids_of_someone_elses_load_are_not_a_change(tmp_path):
    other = PointLoad("Q1", 40, 2, -5000.0, x=1.0, y=1.0)
    fake = Fake(tmp_path, written_model())
    fake.before = model(loads=(other,))
    fake.after.loads.append(replace(other, load_id=41, case_id=3))
    assert fake.apply()["ok"]


def test_someone_elses_load_moving_is_reported(tmp_path):
    other = PointLoad("Q1", 40, 2, -5000.0, x=1.0, y=1.0)
    fake = Fake(tmp_path, written_model())
    fake.before = model(loads=(other,))
    fake.after.loads.append(replace(other, x=1.5))
    result = fake.apply()
    assert not result["ok"] and result["other_changes"] == ["load Q1 changed"]


def test_someone_elses_object_changing_is_reported(tmp_path):
    after = written_model()
    after.cases[0] = LoadCase("BG1", 1)
    result = Fake(tmp_path, after).apply()
    assert not result["ok"] and result["other_changes"] == ["load case BG1 changed"]


def test_failed_run_leaves_no_output(tmp_path):
    fake = Fake(tmp_path, written_model(), RunResult(7, "SCIA rejected the XML update", 1.0, "bad", ""))
    result = fake.apply()
    assert not result["ok"] and "did not save" in result["problems"][0]
    assert [p.name for p in tmp_path.iterdir()] == ["m.esa"]


def test_writer_refusal_is_a_problem(tmp_path):
    fake = Fake(tmp_path, written_model())

    def refuse(*args, **kwargs):
        raise EsaXmlError("SCIA is running")

    result = apply_moving_load(fake.model_path, REQUEST, reader=fake.reader, writer=refuse)
    assert result["problems"] == ["SCIA is running"]


def test_unreadable_output_is_kept_as_failed(tmp_path):
    result = Fake(tmp_path, None).apply()
    assert not result["ok"] and "read back" in result["problems"][0] and result["output"].endswith("_FAILED.esa")


def test_bad_vehicle_stops_before_reading(tmp_path):
    result = preview_moving_load(tmp_path / "none.esa", replace(REQUEST, axles=None))
    assert not result["ok"] and "preset" in result["problems"][0]


def test_update_xml_names_every_case_and_load():
    plan = build_plan(model(), make_vehicle(axles=REQUEST.axles, track_mm=2000), RoadPath.parse(REQUEST.path_mm), 1600)
    xml = update_xml(plan)
    assert xml.count("<obj ") == 1 + 3 + 12
    assert 'nm="LLSM-ML TS1 03 W4"' in xml and '<p1 v="1"/>' in xml  # exclusive


# --- the toetsmodel rule: several groups, wheel patches, one update ------------------------------------

RULE = MovingLoadRequest(path_mm=[[0, 2000], [6000, 2000]], rule="toetsmodel", thickness_mm=300)


def rule_written_model(break_value=False):
    """What a correct write of RULE reads back as: every case, patch and group, ids as SCIA numbers them."""
    from llsm.moving_load.apply import _prepare
    from llsm.scia.esa_file import SurfaceLoad
    _, _, plans = _prepare("preview", Path("m.esa"), RULE, lambda _: model())
    groups, cases, patches, case_id, load_id = [], [], [], 8, 0
    for plan in plans:
        ids = []
        for case in plan.cases:
            case_id += 1
            ids.append(case_id)
            own = []
            for patch in plan.surfaces_of(case.name):
                load_id += 1
                own.append(load_id)
                value = patch.pressure_kn_m2 * 1000 * (1.01 if break_value and load_id == 1 else 1)
                patches.append(SurfaceLoad(patch.name, load_id, case_id, value,
                                           tuple((x / 1000, y / 1000, 0.0) for x, y in patch.corners_mm)))
            cases.append(LoadCase(case.name, 1, *listing(own)))
        groups.append(LoadGroup(plan.group, 1, 1, *listing(ids)))
    after = model(groups=groups, cases=(BASE_CASE, *cases))
    after.surfaces = patches
    return after, plans


def test_rule_write_is_one_update_and_confirmed(tmp_path):
    after, plans = rule_written_model()
    fake = Fake(tmp_path, after)
    result = fake.apply(RULE)
    assert result["ok"], result["mismatches"] + result["other_changes"] + result["problems"]
    patches = sum(len(p.surfaces) for p in plans)
    assert (result["written"], result["expected"], result["cases_confirmed"]) == (patches, patches, 42)
    assert fake.xml.count("DataSetScia.EP_LoadGroup.1\" name=") == 1 and "EP_SurfaceForceFree" in fake.xml
    assert "3 exclusive groups" in result["warnings"][-1]


def test_rule_patch_with_a_wrong_value_is_a_mismatch(tmp_path):
    after, _ = rule_written_model(break_value=True)
    result = Fake(tmp_path, after).apply(RULE)
    assert not result["ok"] and "N/m2" in result["mismatches"][0] and result["cases_confirmed"] == 41


def test_someone_elses_surface_load_changing_is_reported(tmp_path):
    from llsm.scia.esa_file import SurfaceLoad
    other = SurfaceLoad("FF1", 50, 2, -31560.0, ((0, 0, 0), (1, 0, 0), (1, 1, 0)))
    after, _ = rule_written_model()
    after.surfaces.append(replace(other, value=-1.0))
    fake = Fake(tmp_path, after)
    fake.before = model()
    fake.before.surfaces = [other]
    result = fake.apply(RULE)
    assert not result["ok"] and result["other_changes"] == ["surface load FF1 changed"]


def test_someone_elses_unread_surface_load_disappearing_is_reported(tmp_path):
    after, _ = rule_written_model()
    fake = Fake(tmp_path, after)
    fake.before = model()
    fake.before.unread_surfaces = ["FF7"]
    result = fake.apply(RULE)
    assert not result["ok"] and result["other_changes"] == ["surface load FF7 is gone"]


def test_rule_refuses_options_it_sets_itself():
    request = replace(RULE, lateral_offset_mm=500.0, last_step="short")
    problems = preview_moving_load("m.esa", request, reader=lambda _: model())["problems"]
    assert "leave out lateral_offset_mm, last_step" in problems[0]


def test_preview_total_counts_only_the_part_on_the_deck():
    """Case 01 of the demo: two front patches with 0.27 of their 0.4 m on the deck (SCIA: 202.5 kN, run 3)."""
    result = preview_moving_load("m.esa", RULE, reader=lambda _: model())
    assert result["preview"][0]["total_kN"] == -202.5
