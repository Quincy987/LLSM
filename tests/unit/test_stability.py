"""Free motions of a chat-built model: held by LLSM when no load acts on them, refused when one does."""
import math

import pytest

from llsm.scia.stability import Load, Support, check

PIN = frozenset({"X", "Y", "Z"})
ROLL_Z = frozenset({"Z"})
FIXED = frozenset({"X", "Y", "Z", "RX", "RY", "RZ"})
DOWN = (0.0, 0.0, -1.0)


def _line(points, supports, loads=None, self_weight=False):
    nodes = {chr(65 + i): (*p, 0.0) if len(p) == 2 else p for i, p in enumerate(points)}
    names = sorted(nodes)
    members = {f"V{i + 1}": (names[i], names[i + 1]) for i in range(len(names) - 1)}
    sup = [Support(f"S{n}", n, held) for n, held in supports.items()]
    loads = [Load(f"q{m}", m, DOWN) for m in members] if loads is None else loads
    return check(nodes, members, sup, loads, self_weight=self_weight)


def test_single_span_on_pin_and_roller_is_held_sideways_and_against_twist():
    r = _line([(0, 0), (12000, 0)], {"A": PIN, "B": ROLL_Z})
    assert r.ok and r.extras == {"SB": ["Y"], "SA": ["RX"]}
    assert r.held_motions == ["turn about its own axis (V1)", "swing about the vertical through node A"]
    assert "SB also in Y" in r.sentence() and "forces do not change" in r.sentence()


def test_two_spans_get_the_same_two_restraints():
    r = _line([(0, 0), (12000, 0), (24000, 0)], {"A": PIN, "B": ROLL_Z, "C": ROLL_Z})
    assert r.ok and r.extras == {"SB": ["Y"], "SA": ["RX"]}


def test_a_beam_along_y_is_held_in_x():
    r = _line([(0, 0), (0, 12000)], {"A": PIN, "B": ROLL_Z})
    assert r.ok and r.extras == {"SB": ["X"], "SA": ["RY"]}


def test_a_beam_at_30_degrees_in_plan():
    c, s = math.cos(math.pi / 6), math.sin(math.pi / 6)
    r = _line([(0, 0), (12000 * c, 12000 * s)], {"A": PIN, "B": ROLL_Z})
    assert r.ok and r.extras == {"SB": ["X"], "SA": ["RX"]}  # as in the live proof (try 20)


def test_away_from_the_origin_the_same():
    r = _line([(50000, 70000, 3000), (62000, 70000, 3000)], {"A": PIN, "B": ROLL_Z})
    assert r.ok and r.extras == {"SB": ["Y"], "SA": ["RX"]}


def test_a_fixed_end_needs_nothing():
    r = _line([(0, 0), (12000, 0), (24000, 0)], {"A": FIXED, "B": ROLL_Z, "C": ROLL_Z})
    assert r.ok and r.extras == {} and r.sentence() is None


def test_a_cantilever_is_fine():
    assert _line([(0, 0), (5000, 0)], {"A": FIXED}).extras == {}


def test_rollers_only_with_a_horizontal_load_are_a_mechanism():
    r = _line([(0, 0), (12000, 0)], {"A": ROLL_Z, "B": ROLL_Z}, loads=[Load("w", "V1", (1.0, 0.0, 0.0))])
    assert not r.ok and "slide in X" in r.refusals[0] and "w" not in r.extras
    assert r.extras == {}


def test_rollers_only_with_vertical_loads_are_held():
    r = _line([(0, 0), (12000, 0)], {"A": ROLL_Z, "B": ROLL_Z})
    # free: slide in X and Y, swing about the vertical, turn about the axis; no load acts on any of them
    assert r.ok and r.extras == {"SA": ["X", "Y", "RX"], "SB": ["Y"]}


def test_a_beam_that_tips_over_its_only_pin_is_refused():
    r = _line([(0, 0), (12000, 0)], {"A": PIN})
    assert not r.ok and "the load on V1" in r.refusals[0]


def test_a_seesaw_with_a_balanced_load_is_still_refused():
    # pin in the middle: the load does no net work, but it moves the ends up and down
    nodes = {"A": (0.0, 0.0, 0.0), "M": (6000.0, 0.0, 0.0), "B": (12000.0, 0.0, 0.0)}
    members = {"V1": ("A", "M"), "V2": ("M", "B")}
    r = check(nodes, members, [Support("SM", "M", PIN)], [Load("q1", "V1", DOWN), Load("q2", "V2", DOWN)])
    assert not r.ok


def test_self_weight_counts_as_a_load_on_every_member():
    nodes = {"A": (0.0, 0.0, 0.0), "B": (12000.0, 0.0, 0.0)}
    pin = [Support("SA", "A", PIN)]
    assert check(nodes, {"V": ("A", "B")}, pin, [], self_weight=False).ok  # unloaded: held
    out = check(nodes, {"V": ("A", "B")}, pin, [], self_weight=True)  # its own weight tips it over the pin
    assert not out.ok and "the load on V" in out.refusals[0]


def test_a_column_on_a_pin_under_its_own_weight_is_held():
    # linear statics: the weight acts along the column, the tipping moves its top sideways only
    nodes = {"A": (0.0, 0.0, 0.0), "B": (0.0, 0.0, 4000.0)}
    out = check(nodes, {"K": ("A", "B")}, [Support("SA", "A", PIN)], [], self_weight=True)
    assert out.ok and out.extras == {"SA": ["RX", "RY", "RZ"]}


def test_a_portal_on_two_pins_is_held_against_tipping_out_of_plane():
    nodes = {"A": (0.0, 0.0, 0.0), "B": (0.0, 0.0, 4000.0), "C": (8000.0, 0.0, 4000.0), "D": (8000.0, 0.0, 0.0)}
    members = {"K1": ("A", "B"), "L": ("B", "C"), "K2": ("C", "D")}
    r = check(nodes, members, [Support("SA", "A", PIN), Support("SD", "D", PIN)], [Load("q", "L", DOWN)])
    assert r.ok and r.extras == {"SA": ["RX"]} and r.held_motions == ["tip over about the line A-D"]


def test_a_portal_with_a_sideways_load_on_two_pins_tips_over():
    nodes = {"A": (0.0, 0.0, 0.0), "B": (0.0, 0.0, 4000.0), "C": (8000.0, 0.0, 4000.0), "D": (8000.0, 0.0, 0.0)}
    members = {"K1": ("A", "B"), "L": ("B", "C"), "K2": ("C", "D")}
    r = check(nodes, members, [Support("SA", "A", PIN), Support("SD", "D", PIN)], [Load("w", "K1", (0.0, 1.0, 0.0))])
    assert not r.ok and "tip over about the line A-D" in r.refusals[0]


def test_a_member_without_a_support_is_refused():
    nodes = {"A": (0.0, 0.0, 0.0), "B": (12000.0, 0.0, 0.0), "C": (0.0, 9000.0, 0.0), "D": (5000.0, 9000.0, 0.0)}
    members = {"V1": ("A", "B"), "Los": ("C", "D")}
    r = check(nodes, members, [Support("SA", "A", FIXED)], [])
    assert not r.ok and "Los has no support" in r.refusals[0]


def test_two_separate_beams_are_each_held():
    nodes = {"A": (0.0, 0.0, 0.0), "B": (12000.0, 0.0, 0.0), "C": (0.0, 30000.0, 0.0), "D": (12000.0, 30000.0, 0.0)}
    members = {"V1": ("A", "B"), "V2": ("C", "D")}
    sup = [Support("SA", "A", PIN), Support("SB", "B", ROLL_Z), Support("SC", "C", PIN), Support("SD", "D", ROLL_Z)]
    r = check(nodes, members, sup, [Load("q1", "V1", DOWN), Load("q2", "V2", DOWN)])
    assert r.ok and r.extras == {"SB": ["Y"], "SA": ["RX"], "SD": ["Y"], "SC": ["RX"]}


def test_parts_that_touch_without_a_shared_node_are_only_warned_about():
    nodes = {"A": (0.0, 0.0, 0.0), "B": (12000.0, 0.0, 0.0), "C": (6000.0, 0.0, 0.0), "D": (6000.0, 0.0, 4000.0)}
    members = {"V1": ("A", "B"), "K": ("C", "D")}  # K stands on V1's middle, without a node there
    r = check(nodes, members, [Support("SA", "A", PIN), Support("SB", "B", ROLL_Z)], [Load("q", "V1", DOWN)])
    assert r.ok and r.extras == {} and "node C lies on member V1" in r.warnings[0]


def test_hinges_are_not_judged():
    r = check({"A": (0.0, 0.0, 0.0), "B": (1.0, 0.0, 0.0)}, {"V": ("A", "B")}, [], [], releases=True)
    assert r.ok and r.extras == {} and r.warnings


@pytest.mark.parametrize("held,free", [(PIN, 1), (ROLL_Z, 2)])
def test_every_extra_stops_one_free_motion(held, free):
    """Exactly as many restraints as free motions: none of them can carry a force."""
    r = _line([(0, 0), (5000, 0), (9000, 0)], {"A": PIN, "B": held, "C": ROLL_Z})
    assert r.ok and sum(len(v) for v in r.extras.values()) == free
