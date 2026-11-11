"""The input bar's rounded box: a closed outline inside its bounding box (no Tk needed)."""
from __future__ import annotations

import pytest

from llsm.conversation.composer import rounded_rect_points


def _pairs(pts):
    return list(zip(pts[0::2], pts[1::2]))


@pytest.mark.parametrize("box, r", [((0, 0, 400, 80), 10), ((5, 5, 45, 25), 10), ((0, 0, 30, 12), 50)])
def test_outline_is_closed_and_stays_inside(box, r):
    x0, y0, x1, y1 = box
    pts = _pairs(rounded_rect_points(x0, y0, x1, y1, r))
    assert pts[0] == pts[-1]
    assert all(x0 <= x <= x1 and y0 <= y <= y1 for x, y in pts)


def test_radius_is_clamped_to_half_the_shorter_side():
    pts = _pairs(rounded_rect_points(0, 0, 30, 12, 50))
    # With r clamped to 6 the top edge starts 6 px in from the left.
    assert pts[0] == (6.0, 0)


def test_straight_edges_are_doubled_so_only_the_corners_bend():
    pts = _pairs(rounded_rect_points(0, 0, 100, 40, 10))
    assert pts[0] == pts[1] and pts[2] == pts[3]
