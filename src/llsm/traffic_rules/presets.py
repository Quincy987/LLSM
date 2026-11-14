"""LM1 tandems per notional lane, placed by the plaatbruggen toetsmodel's rules.

Which lanes, where they lie, and at which positions along the deck come from the vendored toetsmodel
code (``_toetsmodel``); this module only feeds it the deck's numbers and turns its answers into LLSM
plans, one exclusive load group per lane and configuration. The AI does not calculate a rule.

What LLSM writes differs from the toetsmodel in one way, on purpose: each case holds the real load
(EN 1991-2 LM1: 150 / 100 / 50 kN per wheel in lanes 1 / 2 / 3, times alpha), where the toetsmodel
writes 100 kN per wheel in every lane and applies its lane factors in the load combinations.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..moving_load.path import RoadPath
from ..moving_load.plan import MovingLoadPlan, _deck_outlines, build_plan
from ..moving_load.vehicle import LM1_AXLE_SPACING_MM, LM1_TRACK_MM, Vehicle, lm1_tandem
from ..scia.esa_file import LoadModel
from ._toetsmodel import lanes as upstream_lanes
from ._toetsmodel.constants import geometry as upstream_geometry
from ._toetsmodel.constants import loads as upstream_loads
from ._toetsmodel.load_system.tandem_sequencer import tandem_system_sequencer
from .frame import DeckFrame, FrameError, deck_edges, lane_extent, metres, millimetres, support_chainages

RULE_SOURCE = "toetsmodel plaatbruggen e8e44e7, tandem_sequencer.py and lane rules (lanes.py)"
CONFIGURATIONS = {"A": "BG8000, lanes counted from the right-hand edge", "B": "BG9000, from the left-hand edge",
                  "C": "BG10000, lane 1 in the middle"}

# The toetsmodel's tandem, from its own constants (m). Its sequencer's x is the rear edge of the rear
# wheel patch: patches run x .. x + side and x + spacing .. x + spacing + side.
WHEEL_SIDE_M = upstream_loads.TANDEM_CONTACT_AREA_SIDE
AXLE_SPACING_M = upstream_geometry.TANDEM_WHEEL_SPACING_LONGITUDINAL
TRACK_M = upstream_geometry.TANDEM_WHEEL_SPACING_TRANSVERSE
VEHICLE_LENGTH_M = upstream_geometry.TANDEM_VEHICLE_LENGTH  # what its generators pass to the sequencer
FRONT_AXLE_FROM_X_M = AXLE_SPACING_M + WHEEL_SIDE_M / 2  # x -> front axle centre


class RuleError(ValueError):
    """The rule cannot be applied to this deck as given."""


def _assert_same_tandem() -> None:
    """LLSM's LM1 tandem and the toetsmodel's must be one vehicle; if upstream changes it, stop."""
    lateral_first_wheel = upstream_geometry.TANDEM_START_Y_OFFSET - WHEEL_SIDE_M / 2
    if not (math.isclose(millimetres(AXLE_SPACING_M), LM1_AXLE_SPACING_MM)
            and math.isclose(millimetres(TRACK_M), LM1_TRACK_MM)
            and math.isclose(lateral_first_wheel, TRACK_M / 2)
            and upstream_geometry.TANDEM_WHEEL_SIZE == WHEEL_SIDE_M
            and math.isclose(VEHICLE_LENGTH_M, AXLE_SPACING_M + WHEEL_SIDE_M)):
        raise RuleError("the toetsmodel's tandem no longer matches LLSM's LM1 tandem; the adapter needs a look")


@dataclass(frozen=True)
class LaneRun:
    """One lane in one configuration: what becomes one load group."""

    configuration: str  # A, B, C, or D (the toetsmodel's second half of BG10000)
    lane: int  # 1, 2, 3: the "rs" of the toetsmodel's titles
    y_m: float  # lane centre in the toetsmodel's frame
    lateral_mm: float  # lane centre beside the axis
    entry_mm: float
    exit_mm: float
    supports_mm: tuple[float, ...]  # along the lane, from its entry
    x_m: tuple[float, ...]  # the sequencer's positions

    @property
    def suffix(self) -> str:
        return f"L{self.lane}{self.configuration}"

    @property
    def fronts_mm(self) -> list[float]:
        return [self.entry_mm + millimetres(x + FRONT_AXLE_FROM_X_M) for x in self.x_m]

    @property
    def titles(self) -> list[str]:
        return [f"rs {self.lane} - Conf. {self.configuration} - x = {x:g} m" for x in self.x_m]


@dataclass
class RulePlan:
    plans: list[MovingLoadPlan] = field(default_factory=list)
    runs: list[LaneRun] = field(default_factory=list)
    frame: DeckFrame | None = None
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _lane_centres(configuration: str, width_m: float) -> list[tuple[str, int, float]]:
    """(configuration, lane number, y) as the toetsmodel's theoretical generators assign them."""
    if configuration == "A":
        ys = upstream_lanes.generate_theoretical_lane_positions_bg8000(width_m)
        return [("A", i + 1, y) for i, y in enumerate(ys[:3])]
    if configuration == "B":
        ys = upstream_lanes.generate_theoretical_lane_positions_bg9000(width_m)
        return [("B", i + 1, y) for i, y in enumerate(ys[:3])]
    ys = upstream_lanes.generate_theoretical_lane_positions_bg10000(width_m)
    if len(ys) == 1:
        return [("C", 1, ys[0])]
    if len(ys) == 2:
        return [("C", 1, ys[0]), ("C", 2, ys[1])]
    # Conf. C: 300 kN centre, 200 kN at ys[1], 100 kN at ys[2]; Conf. D swaps the outer two. The centre
    # tandem of Conf. D is the same as Conf. C's and is written once.
    return [("C", 1, ys[0]), ("C", 2, ys[1]), ("C", 3, ys[2]), ("D", 3, ys[1]), ("D", 2, ys[2])]


def plan_lm1_lanes(
    model: LoadModel,
    path: RoadPath,
    thickness_mm: float | None,
    configurations: str = "ABC",
    lanes: str = "theoretical",
    alpha: float = 1.0,
    name: str = "TS",
    deck_edges_mm: list[float] | None = None,
    carriageway_mm: list[float] | None = None,
    supports_mm: list | None = None,
    contact: str = "surface",
    replace: bool = False,
    allow_many: bool = False,
    deck_outline_mm=None,
    deck_z_mm: float = 0.0,
) -> RulePlan:
    """One plan per lane and configuration, positions and lanes by the toetsmodel's rules."""
    result = RulePlan()
    try:
        _assert_same_tandem()
        frame, outlines = _frame(model, path, thickness_mm, lanes, deck_edges_mm, carriageway_mm, deck_outline_mm, deck_z_mm)
    except (RuleError, FrameError) as exc:
        result.problems.append(str(exc))
        return result
    result.frame = frame
    if not (math.isfinite(alpha) and alpha > 0):
        result.problems.append(f"alpha must be a positive number, got {alpha:g}")
        return result
    text = "".join(map(str, configurations)) if isinstance(configurations, (list, tuple)) else str(configurations)
    wanted = "".join(dict.fromkeys(text.upper().replace(",", "").replace(" ", "")))
    if not wanted or any(c not in CONFIGURATIONS for c in wanted):
        result.problems.append(f"configurations is one or more of A, B, C, got {configurations!r}")
        return result
    width = frame.strip_width_m(lanes)
    for configuration in wanted:
        for conf, lane, y in _lane_centres(configuration, width):
            lateral = frame.lateral_mm(y, lanes)
            try:
                entry, exit_ = lane_extent(path, lateral, outlines)
                supports = (
                    tuple(s - entry for s in support_chainages(path, lateral, supports_mm)) if supports_mm else ()
                )
            except FrameError as exc:
                result.problems.append(f"lane {lane} of Conf. {conf}: {exc}")
                continue
            length = metres(exit_ - entry)
            xs = tandem_system_sequencer(
                length, frame.thickness_m, length_vehicle=VEHICLE_LENGTH_M,
                support_x_coords=[metres(s) for s in supports] or None,
            )
            result.runs.append(LaneRun(conf, lane, y, lateral, entry, exit_, supports, tuple(xs)))
    if not result.runs and not result.problems:
        # e.g. a strip narrower than one 3 m lane: no lane in A or B (BG10000 always has its centre lane)
        result.problems.append(f"the toetsmodel lays out no lane on a {width:g} m wide strip in Conf. {', '.join(wanted)}")
    if "C" in wanted and any(r.configuration == "D" for r in result.runs):
        result.notes.append("Conf. D's centre tandem (rs 1) is the same as Conf. C's: written once, in L1C.")
    if not supports_mm:
        result.notes.append("Supports: the deck's ends per lane (give supports_mm, every support including the ends, "
                            "for intermediate supports).")

    for run in result.runs:
        vehicle: Vehicle = lm1_tandem(run.lane, alpha)
        plan = build_plan(
            model, vehicle, path, step_mm=0.0, name=f"{name} {run.suffix}", lateral_offset_mm=run.lateral_mm,
            replace=replace, allow_many=allow_many, deck_outline_mm=deck_outline_mm, deck_z_mm=deck_z_mm,
            fronts_mm=run.fronts_mm, titles=run.titles, off_deck="leave_out", contact=contact,
            wheel_mm=millimetres(WHEEL_SIDE_M),
        )
        result.plans.append(plan)
    return result


def _frame(model, path, thickness_mm, lanes, deck_edges_mm, carriageway_mm, deck_outline_mm, deck_z_mm):
    if lanes not in ("theoretical", "real"):
        raise RuleError(f"lanes must be 'theoretical' or 'real', got {lanes!r}")
    if thickness_mm is None:
        raise RuleError(
            "thickness_mm is needed: the toetsmodel starts the tandem 0.9 x the deck thickness from the deck end, "
            "and LLSM does not read a plate's thickness from the model yet"
        )
    if not (math.isfinite(thickness_mm) and thickness_mm > 0):
        raise RuleError(f"thickness_mm must be a positive number, got {thickness_mm:g}")
    decks, problems = _deck_outlines(model, deck_outline_mm, deck_z_mm)
    if not decks:
        raise RuleError("; ".join(problems) or "no deck found")
    outlines = [d[1] for d in decks]
    if deck_edges_mm is not None:
        right, left = _pair(deck_edges_mm, "deck_edges_mm")
    else:
        right, left = deck_edges(path, outlines)
    if left - right <= 0:
        raise RuleError(f"deck_edges_mm must be [right, left] with left > right, got [{right:g}, {left:g}]")
    carriageway = None
    if carriageway_mm is not None:
        carriageway = _pair(carriageway_mm, "carriageway_mm")
        if carriageway[1] <= carriageway[0]:
            raise RuleError("carriageway_mm must be [right kerb, left kerb] with left > right")
        if carriageway[0] < right - 1.0 or carriageway[1] > left + 1.0:
            raise RuleError(f"the carriageway {list(carriageway)} mm runs past the deck edges [{right:.0f}, {left:.0f}] mm")
    elif lanes == "real":
        raise RuleError("'real' lanes need carriageway_mm: the offsets of the right and left kerb from the axis, in mm")
    return DeckFrame(path, right, left, float(thickness_mm), carriageway), outlines


def _pair(values, label: str) -> tuple[float, float]:
    try:
        a, b = float(values[0]), float(values[1])
    except (TypeError, ValueError, IndexError) as exc:
        raise RuleError(f"{label} must be two numbers in mm: [right, left] offsets from the axis") from exc
    if not (math.isfinite(a) and math.isfinite(b)):
        raise RuleError(f"{label} must be finite numbers")
    return a, b
