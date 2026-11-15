"""The toetsmodel's deck axes on the engineer's own model.

The toetsmodel works on a rectangular deck of its own: x along it from 0 to L, y across it from one
edge, supports at known x, in metres. LLSM puts loads on a saved model with a road axis drawn by the
engineer (a polyline in plan, mm). This module is the one place that maps between the two:

* x of a lane ↔ chainage along the axis, counted from where that lane's centre line enters the deck
  (on a skewed deck every lane enters somewhere else);
* y ↔ lateral offset from the axis (left of the direction of travel positive), counted from the deck's
  right-hand edge, or from the carriageway's right-hand kerb for "real" lanes;
* m ↔ mm.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..moving_load.path import RoadPath, left_of
from ..moving_load.plan import _inside  # one notion of "on the deck"

Point2 = tuple[float, float]
_REACH_MM = 100_000.0  # how far past the axis's ends a lane is followed to find the deck's end
_EDGE_TOLERANCE_MM = 1.0


class FrameError(ValueError):
    """The deck axes cannot be set up from what was given."""


def metres(mm: float) -> float:
    return mm / 1000.0


def millimetres(m: float) -> float:
    return m * 1000.0


@dataclass(frozen=True)
class DeckFrame:
    path: RoadPath
    right_edge_mm: float  # lateral offset of the deck's right-hand edge (left of travel positive)
    left_edge_mm: float
    thickness_mm: float
    carriageway_mm: tuple[float, float] | None = None  # (right kerb, left kerb) offsets, for "real" lanes

    @property
    def width_m(self) -> float:
        return metres(self.left_edge_mm - self.right_edge_mm)

    @property
    def thickness_m(self) -> float:
        return metres(self.thickness_mm)

    def lateral_mm(self, y_m: float, lanes: str = "theoretical") -> float:
        """The toetsmodel's y (m, from the right-hand edge or kerb) as an offset from the axis (mm)."""
        origin = self.carriageway_mm[0] if lanes == "real" and self.carriageway_mm else self.right_edge_mm
        return origin + millimetres(y_m)

    def strip_width_m(self, lanes: str) -> float:
        """Width the lanes are laid out over: the deck, or the carriageway for "real" lanes."""
        if lanes == "real":
            if not self.carriageway_mm:
                raise FrameError("'real' lanes need carriageway_mm: the offsets of the right and left kerb")
            return metres(self.carriageway_mm[1] - self.carriageway_mm[0])
        return self.width_m


def axis_of(outline: tuple[tuple[float, float], ...]) -> RoadPath:
    """The long axis of a four-cornered deck: from the middle of one short (end) edge to the middle of the other.

    Also for a skewed deck: its ends are the shorter pair of opposite edges. A square deck runs along global x.
    The axis starts at the end with the smaller x (then the smaller y).
    """
    if len(outline) != 4:
        raise FrameError(f"the deck has {len(outline)} corners; its axis is only taken from a four-cornered plate: give path_mm")
    edges = [(outline[i], outline[(i + 1) % 4]) for i in range(4)]
    pairs = [(edges[0], edges[2]), (edges[1], edges[3])]

    def mid(edge):
        return ((edge[0][0] + edge[1][0]) / 2, (edge[0][1] + edge[1][1]) / 2)

    def ends_length(pair):
        return math.dist(*pair[0]) + math.dist(*pair[1])

    def along_x(pair):  # how much of the axis between this pair's midpoints runs along x
        (ax, ay), (bx, by) = mid(pair[0]), mid(pair[1])
        return abs(bx - ax) / max(math.dist((ax, ay), (bx, by)), 1e-9)

    lengths = [ends_length(p) for p in pairs]
    if abs(lengths[0] - lengths[1]) <= 1.0:  # square within 1 mm: drive along x
        ends = max(pairs, key=along_x)
    else:
        ends = pairs[0] if lengths[0] < lengths[1] else pairs[1]
    start, end = sorted((mid(ends[0]), mid(ends[1])))
    return RoadPath.parse([start, end])


def lateral_offset(path: RoadPath, point: Point2) -> tuple[float, float]:
    """(chainage, lateral offset) of a point: nearest point on the axis, offset positive to the left."""
    best: tuple[float, float, float] | None = None
    walked = 0.0
    for a, b in zip(path.points, path.points[1:]):
        length = math.dist(a, b)
        dx, dy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        t = max(0.0, min(length, (point[0] - a[0]) * dx + (point[1] - a[1]) * dy))
        foot = (a[0] + t * dx, a[1] + t * dy)
        distance = math.dist(point, foot)
        if best is None or distance < best[0] - 1e-9:
            side = left_of((dx, dy))
            offset = (point[0] - foot[0]) * side[0] + (point[1] - foot[1]) * side[1]
            best = (distance, walked + t, offset)
        walked += length
    assert best is not None
    return best[1], best[2]


def deck_edges(path: RoadPath, outlines: list[tuple[tuple[float, float], ...]]) -> tuple[float, float]:
    """(right, left) lateral offsets of the decks' outline, as seen from the axis."""
    offsets = [lateral_offset(path, corner)[1] for outline in outlines for corner in outline]
    if not offsets:
        raise FrameError("no deck outline to take the deck edges from; give deck_edges_mm")
    return min(offsets), max(offsets)


def _lane_point(path: RoadPath, chainage: float, offset: float) -> Point2:
    centre, direction = path.at(chainage, extend=True)
    side = left_of(direction)
    return centre[0] + offset * side[0], centre[1] + offset * side[1]


def _segments(path: RoadPath, offset: float) -> list[tuple[float, float, Point2, Point2]]:
    """The lane's centre line as straight pieces (chainage from, to, point from, to), carried past the ends."""
    pieces, walked = [], 0.0
    count = len(path.points) - 1
    for index, (a, b) in enumerate(zip(path.points, path.points[1:])):
        length = math.dist(a, b)
        start = walked - _REACH_MM if index == 0 else walked
        end = walked + length + _REACH_MM if index == count - 1 else walked + length
        # both ends on this segment's own line, also when carried past the axis's ends
        direction = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
        side = left_of(direction)

        def on_line(s: float, a=a, walked=walked, direction=direction, side=side) -> Point2:
            along = s - walked
            return (a[0] + along * direction[0] + offset * side[0], a[1] + along * direction[1] + offset * side[1])

        pieces.append((start, end, on_line(start), on_line(end)))
        walked += length
    return pieces


def lane_extent(path: RoadPath, offset: float, outlines: list[tuple[tuple[float, float], ...]]) -> tuple[float, float]:
    """(entry, exit) chainage of the stretch where a lane's centre line runs over a deck.

    From the first point where it enters any deck to the last point where it leaves one; the lane is
    followed past the axis's ends, so the axis need not be drawn exactly from deck end to deck end.
    """
    crossings: list[float] = []
    for start, end, p, q in _segments(path, offset):
        crossings += [start, end]
        for outline in outlines:
            for a, b in zip(outline, [*outline[1:], outline[0]]):
                hit = _cross(p, q, a, b)
                if hit is not None:
                    crossings.append(start + hit * (end - start))
    crossings.sort()
    inside = [
        (lo, hi) for lo, hi in zip(crossings, crossings[1:])
        if hi - lo > 1e-6 and any(_inside(_lane_point(path, (lo + hi) / 2, offset), o) for o in outlines)
    ]
    if not inside:
        raise FrameError(f"the lane {offset:.0f} mm beside the axis does not run over the deck")
    return inside[0][0], inside[-1][1]


def _cross(p: Point2, q: Point2, a: Point2, b: Point2) -> float | None:
    """Where on p-q (0..1) it crosses segment a-b, if it does."""
    rx, ry = q[0] - p[0], q[1] - p[1]
    sx, sy = b[0] - a[0], b[1] - a[1]
    denominator = rx * sy - ry * sx
    if abs(denominator) < 1e-12:
        return None
    t = ((a[0] - p[0]) * sy - (a[1] - p[1]) * sx) / denominator
    u = ((a[0] - p[0]) * ry - (a[1] - p[1]) * rx) / denominator
    return t if 0.0 <= t <= 1.0 and -1e-9 <= u <= 1.0 + 1e-9 else None


def support_chainages(path: RoadPath, offset: float, supports) -> list[float]:
    """Chainages (mm) of the supports along one lane.

    ``supports`` is a list of chainages along the axis (the same for every lane: a square deck), or of
    support lines ``[[x, y], [x, y]]`` in plan, crossed with the lane's centre line (a skewed deck).
    """
    out = []
    for support in supports:
        if isinstance(support, (int, float)):
            if not math.isfinite(support):
                raise FrameError("a support chainage is not a finite number")
            out.append(float(support))
            continue
        try:
            a, b = (float(support[0][0]), float(support[0][1])), (float(support[1][0]), float(support[1][1]))
        except (TypeError, ValueError, IndexError) as exc:
            raise FrameError(f"a support is a chainage in mm or a line [[x, y], [x, y]] in mm, got {support!r}") from exc
        hits = []
        for start, end, p, q in _segments(path, offset):
            t = _line_cross(p, q, a, b)
            if t is not None:
                hits.append(start + t * (end - start))
        if not hits:
            raise FrameError(f"support line {support!r} does not cross the lane {offset:.0f} mm beside the axis")
        out.append(hits[0])
    return sorted(out)


def _line_cross(p: Point2, q: Point2, a: Point2, b: Point2) -> float | None:
    """Where on p-q (0..1) it crosses the infinite line through a and b."""
    rx, ry = q[0] - p[0], q[1] - p[1]
    sx, sy = b[0] - a[0], b[1] - a[1]
    denominator = rx * sy - ry * sx
    if abs(denominator) < 1e-12:
        return None
    t = ((a[0] - p[0]) * sy - (a[1] - p[1]) * sx) / denominator
    return t if 0.0 <= t <= 1.0 else None
