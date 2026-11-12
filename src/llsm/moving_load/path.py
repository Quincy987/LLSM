"""The road axis the vehicle drives along: a polyline in plan (global X, Y, mm)."""
from __future__ import annotations

import math
from dataclasses import dataclass

Point2 = tuple[float, float]


class PathError(ValueError):
    """The path cannot be used."""


@dataclass(frozen=True)
class RoadPath:
    points: tuple[Point2, ...]

    @classmethod
    def parse(cls, points) -> "RoadPath":
        try:
            parsed = tuple((float(p[0]), float(p[1])) for p in points)
        except (TypeError, ValueError, IndexError) as exc:
            raise PathError(f"path_mm must be a list of [x, y] points in mm: {exc}") from exc
        if len(parsed) < 2:
            raise PathError("path_mm needs at least two points: where the road starts and where it ends.")
        for a, b in zip(parsed, parsed[1:]):
            if math.dist(a, b) < 1.0:
                raise PathError(f"two path points are less than 1 mm apart: {a} and {b}")
        return cls(parsed)

    @property
    def length(self) -> float:
        return sum(math.dist(a, b) for a, b in zip(self.points, self.points[1:]))

    def at(self, chainage: float) -> tuple[Point2, Point2]:
        """The point at ``chainage`` mm from the start, and the unit direction of travel there."""
        if chainage < -1e-6 or chainage > self.length + 1e-6:
            raise PathError(f"chainage {chainage:g} mm is outside the path (0 to {self.length:g} mm)")
        walked = 0.0
        segments = list(zip(self.points, self.points[1:]))
        for index, (a, b) in enumerate(segments):
            length = math.dist(a, b)
            if chainage <= walked + length or index == len(segments) - 1:
                t = min(max((chainage - walked) / length, 0.0), 1.0)
                direction = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
                return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])), direction
            walked += length
        raise AssertionError("unreachable")


def left_of(direction: Point2) -> Point2:
    """The unit vector to the left of the direction of travel, in plan."""
    return (-direction[1], direction[0])
