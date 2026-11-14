"""LM1 per lane by the toetsmodel's rules, mapped onto a model: lanes, positions, patches, loads."""
import pytest

from llsm.moving_load.apply import MovingLoadRequest, preview_moving_load, update_xml
from llsm.moving_load.path import RoadPath
from llsm.moving_load.plan import clip, polygon_area, wheel_patch
from llsm.scia.esa_file import LoadCase, LoadModel, Plate
from llsm.traffic_rules._toetsmodel.constants.geometry import TANDEM_START_Y_OFFSET
from llsm.traffic_rules._toetsmodel.load_system.tandem_sequencer import TANDEM_WHEEL_OFFSETS
from llsm.traffic_rules.frame import deck_edges, lane_extent, lateral_offset, support_chainages
from llsm.traffic_rules.presets import plan_lm1_lanes


def deck(length=6.0, width=4.0, name="D1"):
    return Plate(name, (1, 2, 3, 4), ((0, 0, 0), (length, 0, 0), (length, width, 0), (0, width, 0)))


def model(*plates):
    return LoadModel([], [], list(plates) or [deck()], [], [LoadCase("BG1", 0)], [], "+D_344_24.0.3015.64")


DEMO_PATH = RoadPath.parse([[0, 2000], [6000, 2000]])


def toetsmodel_wheels(x, y_center, size=0.4):
    """theoretical_tandem_generators._create_tandem_wheels, as in the toetsmodel (m)."""
    start_y = y_center - TANDEM_START_Y_OFFSET
    return [
        [[x + dx + size, start_y + dy], [x + dx + size, start_y + dy + size], [x + dx, start_y + dy + size], [x + dx, start_y + dy]]
        for dx, dy in TANDEM_WHEEL_OFFSETS
    ]


def test_demo_plate_three_configurations():
    rules = plan_lm1_lanes(model(), DEMO_PATH, 300)
    assert rules.problems == []
    assert [p.group for p in rules.plans] == ["LLSM-ML TS L1A", "LLSM-ML TS L1B", "LLSM-ML TS L1C"]
    assert [r.y_m for r in rules.runs] == [1.5, 2.5, 2.0]
    assert all(len(p.cases) == 14 for p in rules.plans)
    first = rules.plans[0].cases[0]
    assert first.title == "rs 1 - Conf. A - x = -1.33 m" and first.front_chainage_mm == pytest.approx(70.0)


def test_patches_are_where_the_toetsmodel_puts_them():
    """Deck frame = global frame here (axis along x at y = 2 m, right-hand edge at y = 0)."""
    rules = plan_lm1_lanes(model(), DEMO_PATH, 300, configurations="A")
    plan, run = rules.plans[0], rules.runs[0]
    for case, x in zip(plan.cases, run.x_m):
        expected = toetsmodel_wheels(x, run.y_m)
        on_deck = [w for w in expected if polygon_area(clip([(0, 0), (6, 0), (6, 4), (0, 4)], [tuple(c) for c in w])) > 1e-6]
        written = plan.surfaces_of(case.name)
        assert len(written) == len(on_deck)
        for patch in written:
            mine = sorted((round(px / 1000, 6), round(py / 1000, 6)) for px, py in patch.corners_mm)
            assert any(mine == sorted((round(a, 6), round(b, 6)) for a, b in w) for w in on_deck), (case.name, patch.name)


def test_loads_are_the_real_lm1_loads():
    wide = deck(12.0, 10.0)
    rules = plan_lm1_lanes(model(wide), RoadPath.parse([[0, 5000], [12000, 5000]]), 500, configurations="A", alpha=0.8)
    pressures = {p.group: {round(s.pressure_kn_m2, 6) for s in p.surfaces} for p in rules.plans}
    assert pressures == {"LLSM-ML TS L1A": {-750.0}, "LLSM-ML TS L2A": {-500.0}, "LLSM-ML TS L3A": {-250.0}}


def test_bg10000_three_lanes_gives_c_and_d_with_the_centre_once():
    rules = plan_lm1_lanes(model(deck(12.0, 10.0)), RoadPath.parse([[0, 5000], [12000, 5000]]), 500, configurations="C")
    assert [p.group for p in rules.plans] == [
        "LLSM-ML TS L1C", "LLSM-ML TS L2C", "LLSM-ML TS L3C", "LLSM-ML TS L3D", "LLSM-ML TS L2D",
    ]
    assert [r.y_m for r in rules.runs] == [5.0, 2.0, 8.0, 2.0, 8.0]
    assert any("written once" in n for n in rules.notes)


def test_supports_give_fine_steps_per_lane():
    plain = plan_lm1_lanes(model(deck(20.0, 4.0)), RoadPath.parse([[0, 2000], [20000, 2000]]), 800, configurations="A")
    supported = plan_lm1_lanes(model(deck(20.0, 4.0)), RoadPath.parse([[0, 2000], [20000, 2000]]), 800,
                               configurations="A", supports_mm=[0, 10000, 20000])
    assert len(plain.runs[0].x_m) == 26 and len(supported.runs[0].x_m) == 30


def test_skewed_deck_lanes_enter_where_the_deck_starts():
    """A deck whose ends are skewed: each lane's x = 0 is where its centre line crosses the end."""
    skew = Plate("D1", (1, 2, 3, 4), ((0, 0, 0), (10, 0, 0), (12, 4, 0), (2, 4, 0)))
    rules = plan_lm1_lanes(model(skew), RoadPath.parse([[0, 2000], [12000, 2000]]), 400, configurations="AB")
    entries = {r.suffix: round(r.entry_mm) for r in rules.runs}
    assert entries == {"L1A": 750, "L1B": 1250}  # lane at y 1.5 m and 2.5 m: end line x = y / 2


def test_support_lines_cross_each_lane():
    path = RoadPath.parse([[0, 2000], [12000, 2000]])
    assert support_chainages(path, -500, [[[5000, 0], [7000, 4000]]]) == [pytest.approx(5750)]
    assert support_chainages(path, 500, [3000.0]) == [3000.0]


def test_frame_helpers():
    path = RoadPath.parse([[0, 2000], [6000, 2000]])
    assert lateral_offset(path, (3000, 2500)) == (pytest.approx(3000), pytest.approx(500))
    assert deck_edges(path, [((0, 0), (6000, 0), (6000, 4000), (0, 4000))]) == (-2000, 2000)
    assert lane_extent(path, -500, [((0, 0), (6000, 0), (6000, 4000), (0, 4000))]) == (pytest.approx(0), pytest.approx(6000))


def test_real_lanes_over_the_carriageway():
    rules = plan_lm1_lanes(model(deck(12.0, 10.0)), RoadPath.parse([[0, 5000], [12000, 5000]]), 500,
                           configurations="A", lanes="real", carriageway_mm=[-3000, 3500])
    assert [round(r.lateral_mm) for r in rules.runs] == [-1500, 1500]  # 6.5 m road: 2 lanes from its right kerb


@pytest.mark.parametrize("kwargs, message", [
    ({"thickness_mm": None}, "thickness_mm is needed"),
    ({"lanes": "real"}, "carriageway_mm"),
    ({"configurations": "X"}, "configurations"),
    ({"carriageway_mm": [-3000, 3000]}, "past the deck edges"),
])
def test_refusals(kwargs, message):
    args = {"thickness_mm": 300, **kwargs}
    rules = plan_lm1_lanes(model(), DEMO_PATH, args.pop("thickness_mm"), **args)
    assert any(message in p for p in rules.problems), rules.problems


def test_preview_through_the_tool():
    request = MovingLoadRequest(path_mm=[[0, 2000], [6000, 2000]], rule="toetsmodel", thickness_mm=300)
    result = preview_moving_load("m.esa", request, reader=lambda _: model())
    assert result["ok"], result["problems"]
    assert result["planned_cases"] == 42 and result["contact"] == "surface" and len(result["groups"]) == 3
    assert result["rule"]["deck_edges_mm"] == [-2000.0, 2000.0]
    assert any("zoals het toetsmodel" in n for n in result["notes"])
    assert len(result["preview"]) == 9  # first, middle, last per group


def test_rule_refuses_a_vehicle_or_step():
    request = MovingLoadRequest(path_mm=[[0, 2000], [6000, 2000]], rule="toetsmodel", thickness_mm=300, step_mm=1000)
    assert "leave out" in preview_moving_load("m.esa", request, reader=lambda _: model())["problems"][0]


def test_one_update_for_all_groups():
    rules = plan_lm1_lanes(model(), DEMO_PATH, 300)
    xml = update_xml(rules.plans)
    assert xml.count('t="DataSetScia.EP_LoadGroup.1" name=') == 1 and xml.count("<obj id=") == 3 + 42 + sum(
        len(p.surfaces) for p in rules.plans
    )
    assert '<p3 i="3" n="LLSM-ML TS L1C"/>' in xml


def test_wheel_patch_turns_with_the_path():
    corners = wheel_patch((0.0, 0.0), (0.0, 1.0), 400.0)  # driving along +y
    assert corners[0] == pytest.approx((200.0, -200.0)) and polygon_area(corners) == pytest.approx(160000.0)


def test_clip_area():
    assert polygon_area(clip([(0, 0), (10, 0), (10, 10), (0, 10)], [(8, 8), (12, 8), (12, 12), (8, 12)])) == pytest.approx(4.0)


def test_narrow_deck_follows_the_toetsmodel():
    """2.5 m wide: no 3 m lane for BG8000/9000, BG10000 still has its centre tandem (as the toetsmodel)."""
    rules = plan_lm1_lanes(model(deck(6.0, 2.5)), RoadPath.parse([[0, 1250], [6000, 1250]]), 300)
    assert rules.problems == [] and [r.suffix for r in rules.runs] == ["L1C"]
    none = plan_lm1_lanes(model(deck(6.0, 2.5)), RoadPath.parse([[0, 1250], [6000, 1250]]), 300, configurations="AB")
    assert "no lane" in none.problems[0]


def test_even_step_route_refuses_a_patch_whose_wheel_is_off_the_deck():
    """Refusing (the engineer's own path), a wheel is off the deck by its centre, patch or point."""
    from llsm.moving_load.plan import build_plan
    from llsm.moving_load.vehicle import lm1_tandem
    path = RoadPath.parse([[1000, 900], [5000, 900]])  # right-hand wheels at y = -100 mm, beyond the edge
    plan = build_plan(model(), lm1_tandem(1), path, 1400, contact="surface")
    assert any("fall off the deck" in p for p in plan.problems)


def test_configurations_as_a_list():
    rules = plan_lm1_lanes(model(), DEMO_PATH, 300, configurations=["A", "C"])
    assert [r.suffix for r in rules.runs] == ["L1A", "L1C"]
