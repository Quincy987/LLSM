"""Positions, wheels, names and refusals of a moving-load plan. No SCIA needed."""
import math

import pytest

from llsm.moving_load.path import PathError, RoadPath
from llsm.moving_load.plan import build_plan, dividing_steps, front_positions
from llsm.moving_load.vehicle import VehicleError, make_vehicle
from llsm.scia.esa_file import LoadCase, LoadGroup, LoadModel, Member, Plate, PointLoad

DECK = Plate("D1", (1, 2, 3, 4), ((0, 0, 0), (6, 0, 0), (6, 4, 0), (0, 4, 0)))
HAND_VEHICLE = make_vehicle(axles=[{"load_kn": 200, "offset_mm": 0}, {"load_kn": 100, "offset_mm": 1200}], track_mm=2000)
HAND_PATH = RoadPath.parse([[500, 2000], [4900, 2000]])


def model(*, plates=(DECK,), members=(), groups=(), cases=(), loads=()):
    return LoadModel(list(members), [], list(plates), list(groups), list(cases), list(loads), "+D_344_24.0.3015.64")


def test_hand_case_matches_the_plan_document():
    """docs/10 section 4: three positions, four wheels each, -300 kN per case."""
    plan = build_plan(model(), HAND_VEHICLE, HAND_PATH, 1600)
    assert not plan.problems
    assert [c.name for c in plan.cases] == ["LLSM-ML TS1 01", "LLSM-ML TS1 02", "LLSM-ML TS1 03"]
    assert plan.group == "LLSM-ML TS1"
    for case, front, rear in zip(plan.cases, (1700, 3300, 4900), (500, 2100, 3700)):
        loads = plan.loads_of(case.name)
        assert [(l.name[-2:], l.x_mm, l.y_mm, l.value_kn) for l in loads] == [
            ("W1", front, 1000, -100), ("W2", front, 3000, -100), ("W3", rear, 1000, -50), ("W4", rear, 3000, -50),
        ]
    assert [row["total_kN"] for row in plan.preview_table()] == [-300, -300, -300]


def test_skewed_path_puts_the_wheels_square_to_it():
    angle = math.radians(30)
    start = (1000.0, 1000.0)
    end = (start[0] + 3000 * math.cos(angle), start[1] + 3000 * math.sin(angle))
    vehicle = make_vehicle(axles=[{"load_kn": 100, "offset_mm": 0}], track_mm=2000)
    plan = build_plan(model(), vehicle, RoadPath.parse([start, end]), 3000)
    first = plan.loads_of(plan.cases[0].name)
    centre = (start[0], start[1])  # one axle: the first position has the front axle at the path start
    offset = (-math.sin(angle) * 1000, math.cos(angle) * 1000)
    assert first[0].x_mm == pytest.approx(centre[0] - offset[0]) and first[0].y_mm == pytest.approx(centre[1] - offset[1])
    assert first[1].x_mm == pytest.approx(centre[0] + offset[0]) and first[1].y_mm == pytest.approx(centre[1] + offset[1])


def test_lateral_offset_moves_the_vehicle_left():
    plan = build_plan(model(), HAND_VEHICLE, HAND_PATH, 1600, lateral_offset_mm=500)
    assert [l.y_mm for l in plan.loads_of(plan.cases[0].name)] == [1500, 3500, 1500, 3500]


def test_step_that_does_not_fit_is_refused_with_steps_that_do():
    plan = build_plan(model(), HAND_VEHICLE, HAND_PATH, 1500)
    assert not plan.cases and "does not fit" in plan.problems[0] and "1600" in plan.problems[0]


def test_short_last_step_ends_at_the_path_end():
    positions, problems = front_positions(4400, 1200, 1500, "short")
    assert not problems and positions == [1200, 2700, 4200, 4400]


def test_rounding_does_not_lose_the_last_position():
    positions, problems = front_positions(1000.0000001, 0, 100)
    assert not problems and len(positions) == 11 and positions[-1] <= 1000.0000001


def test_dividing_steps_divide():
    for step in dividing_steps(4630.95, 500):
        assert abs(4630.95 / step - round(4630.95 / step)) * step < 0.01


def test_vehicle_longer_than_the_path_is_refused():
    plan = build_plan(model(), HAND_VEHICLE, RoadPath.parse([[0, 0], [1000, 0]]), 100)
    assert "longer than the path" in plan.problems[0]


def test_wheels_off_the_deck_are_listed():
    plan = build_plan(model(), HAND_VEHICLE, RoadPath.parse([[500, 3500], [4900, 3500]]), 1600)
    assert "fall off the deck" in plan.problems[0] and "W2" in plan.problems[0]


def test_wheel_on_the_edge_counts_as_on_the_deck():
    plan = build_plan(model(), HAND_VEHICLE, RoadPath.parse([[500, 3000], [4900, 3000]]), 1600)
    assert not plan.problems


def test_walls_do_not_count_as_deck():
    wall = Plate("W", (5, 6, 7, 8), ((0, 0, 0), (6, 0, 0), (6, 0, 3), (0, 0, 3)))
    plan = build_plan(model(plates=(wall,)), HAND_VEHICLE, HAND_PATH, 1600)
    assert "no horizontal 2D member" in plan.problems[0]


def test_given_outline_replaces_the_models():
    plan = build_plan(model(plates=()), HAND_VEHICLE, HAND_PATH, 1600, deck_outline_mm=[[0, 0], [6000, 0], [6000, 4000], [0, 4000]], deck_z_mm=-1100)
    assert not plan.problems and plan.loads[0].z_mm == -1100


def test_unreadable_outline_asks_for_one():
    plan = build_plan(model(plates=(Plate("D1", (1, 2), ()),)), HAND_VEHICLE, HAND_PATH, 1600)
    assert "deck_outline_mm" in plan.problems[0]


def test_existing_names_are_refused():
    taken = LoadCase("LLSM-ML TS1 02", 1)
    plan = build_plan(model(cases=(taken,)), HAND_VEHICLE, HAND_PATH, 1600)
    assert "already exist" in plan.problems[0]


def test_replace_overwrites_only_our_own_and_warns_about_leftovers():
    earlier = [LoadCase(f"LLSM-ML TS1 0{i}", 1) for i in range(1, 6)]
    plan = build_plan(model(cases=earlier, groups=(LoadGroup("LLSM-ML TS1", 1, 1),)), HAND_VEHICLE, HAND_PATH, 1600, replace=True)
    assert not plan.problems and "LLSM-ML TS1 04" in plan.warnings[0]


def test_replace_never_touches_someone_elses_object():
    plan = build_plan(model(cases=(LoadCase("TS1", 1),)), HAND_VEHICLE, HAND_PATH, 1600, name="", replace=True)
    assert plan.problems  # an empty name is refused before anything else


def test_more_than_200_cases_needs_allow_many():
    plan = build_plan(model(), make_vehicle("LM1-TS1"), RoadPath.parse([[0, 2000], [6000, 2000]]), 20)
    assert "allow_many" in plan.problems[0]
    plan = build_plan(model(), make_vehicle("LM1-TS1"), RoadPath.parse([[0, 2000], [6000, 2000]]), 20, allow_many=True)
    assert not plan.problems and len(plan.cases) == 241 and plan.cases[0].name.endswith(" 001")


def test_beam_deck_one_load_per_axle_at_its_relative_position():
    beam = Member("L1", 3, (0, 0, 0), (10, 0, 0))
    plan = build_plan(model(plates=(), members=(beam,)), HAND_VEHICLE, RoadPath.parse([[0, 0], [10000, 0]]), 4400, deck="beam", member="L1")
    assert not plan.problems
    first = plan.loads_of(plan.cases[0].name)
    assert [(l.name[-2:], l.member, l.member_id, round(l.position, 4), l.value_kn) for l in first] == [
        ("A1", "L1", 3, 0.12, -200), ("A2", "L1", 3, 0.0, -100),
    ]
    assert "one load per axle" in plan.warnings[0]


def test_beam_deck_path_beside_the_axis_is_refused():
    beam = Member("L1", 3, (0, 0, 0), (10, 0, 0))
    plan = build_plan(model(plates=(), members=(beam,)), HAND_VEHICLE, RoadPath.parse([[0, 50], [10000, 50]]), 4400, deck="beam", member="L1")
    assert "beside beam L1" in plan.problems[0]


def test_unknown_beam_names_the_candidates():
    plan = build_plan(model(plates=(), members=(Member("L1", 3, (0, 0, 0), (10, 0, 0)),)), HAND_VEHICLE, HAND_PATH, 1600, deck="beam", member="X")
    assert "L1" in plan.problems[0]


@pytest.mark.parametrize("preset, axle", [("LM1-TS1", 300), ("lm1 ts2", 200), ("LM1 TS lane 3", 100)])
def test_lm1_presets(preset, axle):
    vehicle = make_vehicle(preset)
    assert [(a.load_kn, a.offset_mm) for a in vehicle.axles] == [(axle, 0), (axle, 1200)] and vehicle.track_mm == 2000


def test_lm1_alpha_scales_the_axles():
    assert make_vehicle("LM1-TS1", alpha=0.8).axles[0].load_kn == pytest.approx(240)


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"preset": "LM1-TS1", "axles": [{"load_kn": 1, "offset_mm": 0}]},
        {"preset": "LM2"},
        {"axles": [{"load_kn": -10, "offset_mm": 0}]},
        {"axles": [{"load_kn": 10, "offset_mm": 500}]},
        {"axles": [{"load_kn": 10, "offset_mm": 0}, {"load_kn": 10, "offset_mm": 0}]},
        {"axles": [{"load": 10}]},
    ],
)
def test_bad_vehicles_are_refused(kwargs):
    with pytest.raises(VehicleError):
        make_vehicle(**kwargs)


@pytest.mark.parametrize("points", [[[0, 0]], [[0, 0], [0.5, 0]], "abc"])
def test_bad_paths_are_refused(points):
    with pytest.raises(PathError):
        RoadPath.parse(points)


def test_polyline_chainage_turns_the_corner():
    path = RoadPath.parse([[0, 0], [1000, 0], [1000, 1000]])
    point, direction = path.at(1500)
    assert point == pytest.approx((1000, 500)) and direction == pytest.approx((0, 1))
    assert path.length == pytest.approx(2000)
