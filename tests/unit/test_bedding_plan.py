"""Where layers fall on piles, and what the plan refuses."""
import pytest

from llsm.bedding.layers import KH, SoilLayer
from llsm.bedding.piles import Pile, pile_segments, select_piles
from llsm.bedding.plan import build_plan
from llsm.scia.esa_file import EsaModel, LineSupport, Member

CLAY = SoilLayer(-2, -8, 5000, KH, "klei")
SAND = SoilLayer(-8, -14, 8000, KH, "zand")
VERTICAL = Pile("P1", 7, (0, 0, -1), (0, 0, -21))  # 20 m, begin at the top


def member(name="P1", member_id=7, begin=(0, 0, -1), end=(0, 0, -21)):
    return Member(name, member_id, begin, end)


def foreign(member_id=7):
    return LineSupport("Sn1", member_id, None, None, 1e6, 1e6)


def ours(n, pile="P1", member_id=7):
    return LineSupport(f"LLSM:{pile}:{n}", member_id, 0.1, 0.2, 1e6, 1e6)


def test_vertical_pile_positions():
    first, second = pile_segments(VERTICAL, [CLAY, SAND])
    assert (first.x1, first.x2) == pytest.approx((0.05, 0.35))
    assert (second.x1, second.x2) == pytest.approx((0.35, 0.65))


def test_pile_defined_bottom_up_keeps_x1_below_x2():
    pile = Pile("P", 1, (0, 0, -21), (0, 0, -1))
    segment = pile_segments(pile, [CLAY])[0]
    assert (segment.x1, segment.x2) == pytest.approx((0.65, 0.95))


def test_inclined_pile_uses_true_z():
    pile = Pile("P", 1, (0.3, 1.31, -1.1), (-3.02, 1.31, -19.5))
    segment = pile_segments(pile, [CLAY])[0]
    assert segment.x1 == pytest.approx(0.9 / 18.4)
    assert segment.x2 == pytest.approx(6.9 / 18.4)


def test_layer_straddling_the_start_level_is_clipped():
    segment = pile_segments(VERTICAL, [CLAY], start_level_nap=-5)[0]
    assert (segment.top_nap, segment.bottom_nap) == (-5, -8)
    assert segment.x1 == pytest.approx(0.2)


def test_layer_above_the_start_level_is_left_out():
    assert [s.layer for s in pile_segments(VERTICAL, [CLAY, SAND], start_level_nap=-9)] == [SAND]


def test_layer_below_the_toe_is_left_out_and_a_straddling_one_clipped():
    short = Pile("P", 1, (0, 0, -1), (0, 0, -10))
    segments = pile_segments(short, [CLAY, SAND, SoilLayer(-14, -20, 1, KH, "")])
    assert len(segments) == 2 and segments[1].bottom_nap == -10 and segments[1].x2 == pytest.approx(1.0)


def test_datum_shift_moves_the_layers():
    # Model z = NAP + 3: the pile top at z -1 is NAP -4, so the clay starts at the pile top.
    segment = pile_segments(VERTICAL, [CLAY], datum_offset=3.0)[0]
    assert segment.top_nap == -4
    assert segment.x1 == pytest.approx(0.0)


def test_start_level_above_the_pile_stops_at_the_top():
    assert pile_segments(VERTICAL, [CLAY], start_level_nap=5)[0].top_nap == -2


def test_horizontal_member_is_refused():
    with pytest.raises(ValueError, match="horizontal"):
        pile_segments(Pile("B", 1, (0, 0, -1), (5, 0, -1)), [CLAY])


def test_split_pile_is_two_members_with_their_own_positions():
    upper = Pile("U", 1, (0, 0, -1), (0, 0, -5))
    lower = Pile("L", 2, (0, 0, -5), (0, 0, -21))
    assert pile_segments(upper, [CLAY])[0].x2 == pytest.approx(1.0)
    assert pile_segments(lower, [CLAY])[0].x1 == pytest.approx(0.0)


def test_globs_pick_only_steep_members_but_a_name_is_taken_as_asked():
    beam = member("S2", 2, (0, 0, 0), (5, 0, 0))
    piles, problems = select_piles([member("S1"), beam], "S*")
    assert [p.name for p in piles] == ["S1"] and not problems
    piles, _ = select_piles([member("S1"), beam], "S2")
    assert [p.name for p in piles] == ["S2"]


def test_unknown_names_are_problems():
    _, problems = select_piles([member()], "P1, NOPE, X*")
    assert len(problems) == 2


def plan(supports=(), **kwargs):
    kwargs.setdefault("diameter_m", 0.45)
    return build_plan(EsaModel([member()], list(supports)), [CLAY, SAND], **kwargs)


def test_plan_names_and_stiffness():
    result = plan()
    assert [p.name for p in result.planned] == ["LLSM:P1:1", "LLSM:P1:2"]
    assert result.planned[0].k_line_kn_m2 == pytest.approx(2250)
    assert result.planned[0].stiffness_n_m2 == pytest.approx(2.25e6)


def test_preview_table_columns():
    row = plan().preview_table()[0]
    assert row == {
        "pile": "P1", "segment": "LLSM:P1:1", "top_nap": -2, "bottom_nap": -8,
        "x1": 0.05, "x2": 0.35, "k_line_kN_m2": 2250.0, "description": "klei",
    }


def test_foreign_supports_refuse_the_pile():
    result = plan([foreign()])
    assert not result.planned and "double the stiffness" in result.problems[0]


def test_foreign_supports_on_another_member_do_not_matter():
    assert not plan([foreign(member_id=99)]).problems


def test_allow_existing_writes_and_warns():
    result = plan([foreign()], allow_existing=True)
    assert result.planned and not result.problems and "adds up" in " ".join(result.warnings)


def test_our_own_supports_skip_the_pile_without_replace():
    result = plan([ours(1)])
    assert result.skipped_piles == ["P1"] and not result.planned and not result.problems


def test_replace_overwrites_our_supports():
    result = plan([ours(1), ours(2)], replace=True)
    assert len(result.planned) == 2 and not result.problems


def test_shrinking_replace_is_refused():
    result = plan([ours(1), ours(2), ours(3)], replace=True)
    assert not result.planned and "leave 1 old" in result.problems[0]


def test_missing_diameter_is_a_problem_not_a_crash():
    result = build_plan(EsaModel([member()], []), [CLAY])
    assert "diameter" in result.problems[0]


def test_every_problem_is_collected():
    model = EsaModel([member("A", 1), member("B", 2)], [foreign(1), foreign(2)])
    result = build_plan(model, [CLAY], piles="A,B,NOPE", diameter_m=0.45)
    assert len(result.problems) == 3


def test_unknown_member_id_warns_and_cannot_count_foreign_supports():
    model = EsaModel([member(member_id=None)], [foreign()])
    result = build_plan(model, [CLAY], diameter_m=0.45)
    assert result.planned and "could not be read" in " ".join(result.warnings)


def test_gap_above_the_toe_is_warned():
    assert "toe is at" in " ".join(plan().warnings)
