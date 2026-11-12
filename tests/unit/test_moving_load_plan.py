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
    assert "no 2D member to drive on" in plan.problems[0]


def test_a_deck_with_a_gradient_still_counts():
    sloped = Plate("D2", (1, 2, 3, 4), ((0, 0, 0), (6, 0, 0.3), (6, 4, 0.3), (0, 4, 0)))  # 5 %
    plan = build_plan(model(plates=(sloped,)), HAND_VEHICLE, HAND_PATH, 1600)
    assert not plan.problems and plan.loads[0].z_mm == pytest.approx(150)


def test_wheel_above_two_decks_is_refused():
    below = Plate("F1", (5, 6, 7, 8), ((0, 0, -3), (6, 0, -3), (6, 4, -3), (0, 4, -3)))
    plan = build_plan(model(plates=(DECK, below)), HAND_VEHICLE, HAND_PATH, 1600)
    assert "more than one 2D member" in plan.problems[0] and "D1 and F1" in plan.problems[0]


def test_wheel_above_two_decks_is_refused_with_a_given_outline_too():
    below = Plate("F1", (5, 6, 7, 8), ((0, 0, -3), (6, 0, -3), (6, 4, -3), (0, 4, -3)))
    plan = build_plan(model(plates=(DECK, below)), HAND_VEHICLE, HAND_PATH, 1600,
                      deck_outline_mm=[[0, 0], [6000, 0], [6000, 4000], [0, 4000]])
    assert "more than one 2D member" in plan.problems[0] and "cannot yet tie" in plan.problems[0]


def test_beam_deck_without_a_member_is_refused():
    assert "needs member" in build_plan(model(), HAND_VEHICLE, HAND_PATH, 1600, deck="beam").problems[0]


@pytest.mark.parametrize("kwargs", [{"lateral_offset_mm": float("nan")}, {"deck_z_mm": float("inf")}])
def test_non_finite_offsets_are_refused(kwargs):
    assert "finite" in build_plan(model(), HAND_VEHICLE, HAND_PATH, 1600, **kwargs).problems[0]


@pytest.mark.parametrize("step", [float("nan"), float("inf"), 0, -5])
def test_bad_steps_are_refused(step):
    positions, problems = front_positions(4400, 1200, step)
    assert not positions and "positive number" in problems[0]


def test_decks_that_share_an_edge_do_not_overlap():
    left = Plate("A", (1, 2, 3, 4), ((0, 0, 0), (3, 0, 0), (3, 4, 0), (0, 4, 0)))
    right = Plate("B", (2, 5, 6, 3), ((3, 0, 0), (6, 0, 0), (6, 4, 0), (3, 4, 0)))
    plan = build_plan(model(plates=(left, right)), HAND_VEHICLE, HAND_PATH, 1600)
    assert not plan.problems and len(plan.loads) == 12


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


def test_replace_overwrites_an_earlier_run_of_the_same_shape():
    earlier = build_plan(model(), HAND_VEHICLE, HAND_PATH, 1600)
    taken = model(groups=(LoadGroup(earlier.group, 1, 1),), cases=[LoadCase(c.name, 1) for c in earlier.cases],
                  loads=[PointLoad(l.name, i, 9, -1.0, x=0.0, y=0.0) for i, l in enumerate(earlier.loads, 1)])
    assert "already exist" in build_plan(taken, HAND_VEHICLE, HAND_PATH, 1600).problems[0]
    assert not build_plan(taken, HAND_VEHICLE, HAND_PATH, 1600, replace=True).problems


def test_replace_refuses_what_an_earlier_longer_run_left_behind():
    """Cases 04 and 05 would stay in the exclusive group: the update cannot delete them."""
    earlier = [LoadCase(f"LLSM-ML TS1 0{i}", 1) for i in range(1, 6)]
    plan = build_plan(model(cases=earlier, groups=(LoadGroup("LLSM-ML TS1", 1, 1),)), HAND_VEHICLE, HAND_PATH, 1600, replace=True)
    assert "LLSM-ML TS1 04" in plan.problems[0] and "Delete them" in plan.problems[0]


def test_replace_refuses_wheels_of_an_earlier_run_left_in_a_reused_case():
    left = PointLoad("LLSM-ML TS1 01 W5", 1, 9, -1.0, x=0.0, y=0.0)
    plan = build_plan(model(loads=(left,)), HAND_VEHICLE, HAND_PATH, 1600, replace=True)
    assert "LLSM-ML TS1 01 W5" in plan.problems[0]


def test_another_run_is_not_a_leftover():
    other = [LoadCase("LLSM-ML TS1 B 01", 1), PointLoad("LLSM-ML TS1 B 01 W1", 1, 9, -1.0, x=0.0, y=0.0)]
    plan = build_plan(model(cases=other[:1], loads=other[1:]), HAND_VEHICLE, HAND_PATH, 1600, replace=True)
    assert not plan.problems


def test_unreadable_plate_with_a_given_outline_is_a_warning():
    plan = build_plan(model(plates=(Plate("X", (1, 2), ()),)), HAND_VEHICLE, HAND_PATH, 1600,
                      deck_outline_mm=[[0, 0], [6000, 0], [6000, 4000], [0, 4000]])
    assert not plan.problems and "was not checked" in plan.warnings[0]


def test_other_names_are_left_alone():
    plan = build_plan(model(cases=(LoadCase("TS1 01", 1), LoadCase("LLSM-ML TS2 01", 1))), HAND_VEHICLE, HAND_PATH, 1600)
    assert not plan.problems


def test_empty_name_is_refused():
    assert build_plan(model(), HAND_VEHICLE, HAND_PATH, 1600, name=" ").problems


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


def test_beam_deck_path_beside_the_axis_is_refused_once():
    beam = Member("L1", 3, (0, 0, 0), (10, 0, 0))
    plan = build_plan(model(plates=(), members=(beam,)), HAND_VEHICLE, RoadPath.parse([[0, 50], [10000, 50]]), 4400, deck="beam", member="L1")
    assert len(plan.problems) == 1 and "6 axles are beside beam L1" in plan.problems[0]  # 3 positions x 2 axles


def test_step_in_metres_is_caught_before_building_positions():
    positions, problems = front_positions(4400, 1200, 0.0016)
    assert not positions and "is the step in mm" in problems[0]


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
        {"axles": [{"load_kn": float("nan"), "offset_mm": 0}]},
        {"axles": [{"load_kn": 10, "offset_mm": 0}], "track_mm": float("nan")},
        {"preset": "LM1-TS1", "alpha": float("nan")},
        {"axles": [{"load_kn": 10, "offset_mm": 0}], "alpha": 0.8},
    ],
)
def test_bad_vehicles_are_refused(kwargs):
    with pytest.raises(VehicleError):
        make_vehicle(**kwargs)


@pytest.mark.parametrize("points", [[[0, 0]], [[0, 0], [0.5, 0]], "abc", [[0, 0], [float("nan"), 0]]])
def test_bad_paths_are_refused(points):
    with pytest.raises(PathError):
        RoadPath.parse(points)


def test_polyline_chainage_turns_the_corner():
    path = RoadPath.parse([[0, 0], [1000, 0], [1000, 1000]])
    point, direction = path.at(1500)
    assert point == pytest.approx((1000, 500)) and direction == pytest.approx((0, 1))
    assert path.length == pytest.approx(2000)


def test_random_plans_hold_their_invariants():
    """Every case carries the whole vehicle, names are unique, the last position is at the path end."""
    import random
    import xml.etree.ElementTree as ET

    from llsm.moving_load.apply import update_xml

    rng = random.Random(7)
    big = model(plates=(Plate("D", (1, 2, 3, 4), ((-100, -100, 0), (100, -100, 0), (100, 100, 0), (-100, 100, 0))),))
    checked = 0
    for _ in range(300):
        points = [(rng.uniform(-50000, 50000), rng.uniform(-50000, 50000))]
        for _ in range(rng.randint(1, 4)):
            points.append((points[-1][0] + rng.uniform(-20000, 20000), points[-1][1] + rng.uniform(-20000, 20000)))
        axles = [{"load_kn": rng.uniform(1, 300), "offset_mm": 0}] + [
            {"load_kn": rng.uniform(1, 300), "offset_mm": rng.uniform(100, 5000)} for _ in range(rng.randint(0, 3))
        ]
        vehicle = make_vehicle(axles=axles, track_mm=rng.choice([None, rng.uniform(500, 3000)]))
        path = RoadPath.parse(points)
        plan = build_plan(big, vehicle, path, path.length / rng.randint(1, 30), last_step=rng.choice(["exact", "short"]),
                          lateral_offset_mm=rng.uniform(-2000, 2000), allow_many=True)
        if plan.problems:
            continue
        checked += 1
        names = [l.name for l in plan.loads] + [c.name for c in plan.cases]
        assert len(names) == len(set(names))
        for case in plan.cases:
            assert -sum(l.value_kn for l in plan.loads_of(case.name)) == pytest.approx(vehicle.total_kn)
        assert plan.cases[-1].front_chainage_mm == pytest.approx(path.length, abs=0.02)
        ET.fromstring(update_xml(plan))
    assert checked > 100
