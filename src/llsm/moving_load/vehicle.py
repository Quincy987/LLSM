"""The vehicle that moves: its axles, and the EN 1991-2 LM1 tandem as a preset."""
from __future__ import annotations

import re
from dataclasses import dataclass

LM1_AXLE_SPACING_MM = 1200.0
LM1_TRACK_MM = 2000.0
# EN 1991-2 table 4.2: axle load Q_ik of the tandem system per notional lane.
# The alpha_Q factors of the Dutch national annex are a parameter, default 1.0: to confirm.
LM1_AXLE_KN = {1: 300.0, 2: 200.0, 3: 100.0}
_PRESET = re.compile(r"^\s*lm1[\s_-]*(?:ts)?[\s_-]*(?:lane)?[\s_-]*([123])\s*$", re.I)


class VehicleError(ValueError):
    """The vehicle cannot be built from what was given."""


@dataclass(frozen=True)
class Axle:
    load_kn: float  # the whole axle, both wheels together, > 0 (it acts downward)
    offset_mm: float  # behind the front axle, >= 0


@dataclass(frozen=True)
class Vehicle:
    name: str
    axles: tuple[Axle, ...]
    track_mm: float | None  # distance between the two wheels of an axle; None: one load on the axis

    @property
    def length_mm(self) -> float:
        return max(a.offset_mm for a in self.axles)

    @property
    def total_kn(self) -> float:
        return sum(a.load_kn for a in self.axles)

    def describe(self) -> str:
        axles = ", ".join(f"{a.load_kn:g} kN at {a.offset_mm:g} mm" for a in self.axles)
        track = f"track {self.track_mm:g} mm" if self.track_mm else "one load per axle"
        return f"{self.name}: {axles}; {track}"


def lm1_tandem(lane: int, alpha: float = 1.0) -> Vehicle:
    """The LM1 tandem system of one notional lane (its UDL does not move and is not included)."""
    if lane not in LM1_AXLE_KN:
        raise VehicleError(f"LM1 has tandem systems for lanes 1, 2 and 3, not {lane}.")
    if alpha <= 0:
        raise VehicleError(f"alpha must be positive, got {alpha:g}.")
    load = LM1_AXLE_KN[lane] * alpha
    return Vehicle(f"LM1 TS{lane}", (Axle(load, 0.0), Axle(load, LM1_AXLE_SPACING_MM)), LM1_TRACK_MM)


def make_vehicle(
    preset: str | None = None,
    axles: list[dict] | None = None,
    track_mm: float | None = None,
    alpha: float = 1.0,
) -> Vehicle:
    """A preset ("LM1-TS1", "LM1 TS2", ...) or an axle list [{load_kn, offset_mm}, ...]."""
    if (preset is None) == (axles is None):
        raise VehicleError("Give either a preset (LM1-TS1, LM1-TS2, LM1-TS3) or an axle list, not both or neither.")
    if preset is not None:
        match = _PRESET.match(preset)
        if not match:
            raise VehicleError(f"Unknown vehicle preset {preset!r}. Known: LM1-TS1, LM1-TS2, LM1-TS3.")
        vehicle = lm1_tandem(int(match.group(1)), alpha)
        if track_mm is not None and track_mm != vehicle.track_mm:
            raise VehicleError(f"LM1 has a fixed track of {LM1_TRACK_MM:g} mm; leave track_mm out for a preset.")
        return vehicle
    problems = []
    parsed = []
    for number, axle in enumerate(axles or [], start=1):
        try:
            load, offset = float(axle["load_kn"]), float(axle["offset_mm"])
        except (KeyError, TypeError, ValueError):
            problems.append(f"axle {number}: needs load_kn and offset_mm as numbers, got {axle!r}")
            continue
        if load <= 0:
            problems.append(f"axle {number}: load_kn must be positive (it acts downward), got {load:g}")
        if offset < 0:
            problems.append(f"axle {number}: offset_mm is measured backward from the front axle, got {offset:g}")
        parsed.append(Axle(load, offset))
    if not parsed and not problems:
        problems.append("the axle list is empty")
    if parsed and min(a.offset_mm for a in parsed) != 0:
        problems.append("one axle must be the front axle, at offset_mm 0")
    offsets = [a.offset_mm for a in parsed]
    if len(set(offsets)) != len(offsets):
        problems.append("two axles at the same offset; add them up into one axle")
    if track_mm is not None and track_mm <= 0:
        problems.append(f"track_mm must be positive, got {track_mm:g}")
    if problems:
        raise VehicleError("; ".join(problems))
    return Vehicle("custom", tuple(sorted(parsed, key=lambda a: a.offset_mm)), track_mm)
