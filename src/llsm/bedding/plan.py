"""Turn a model and a layer table into a checked list of springs to write."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..scia.esa_file import EsaModel, LineSupport
from .layers import SoilLayer, line_stiffness
from .piles import Pile, Segment, pile_segments, select_piles

PREFIX = "LLSM:"


@dataclass(frozen=True)
class PlannedSegment:
    name: str
    pile: Pile
    segment: Segment
    k_line_kn_m2: float

    @property
    def stiffness_n_m2(self) -> float:
        return self.k_line_kn_m2 * 1000.0  # the XML takes N/m2 (measured in Phase 0)


@dataclass
class BeddingPlan:
    planned: list[PlannedSegment] = field(default_factory=list)
    skipped_piles: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def piles(self) -> list[str]:
        return list(dict.fromkeys(p.pile.name for p in self.planned))

    def preview_table(self) -> list[dict]:
        return [
            {
                "pile": p.pile.name,
                "segment": p.name,
                "top_nap": round(p.segment.top_nap, 3),
                "bottom_nap": round(p.segment.bottom_nap, 3),
                "x1": round(p.segment.x1, 5),
                "x2": round(p.segment.x2, 5),
                "k_line_kN_m2": round(p.k_line_kn_m2, 1),
                "description": p.segment.layer.description,
            }
            for p in self.planned
        ]


def segment_name(pile: str, number: int) -> str:
    return f"{PREFIX}{pile}:{number}"


def pile_of_support(name: str) -> str | None:
    """The pile an LLSM support belongs to, from its name; None for anyone else's support."""
    if not name.startswith(PREFIX):
        return None
    pile, _, number = name[len(PREFIX):].rpartition(":")
    return pile if pile and number.isdigit() else None


def _existing_for(pile: Pile, supports: list[LineSupport]) -> tuple[int, int]:
    """How many of our supports, and how many others', this pile carries."""
    ours = sum(1 for s in supports if pile_of_support(s.name) == pile.name)
    theirs = sum(
        1 for s in supports if pile_of_support(s.name) is None and pile.member_id is not None and s.member_id == pile.member_id
    )
    return ours, theirs


def build_plan(
    model: EsaModel,
    layers: list[SoilLayer],
    piles: str = "*",
    diameter_m: float | None = None,
    alpha: float | None = None,
    groepsfactor: float | None = None,
    datum_offset: float = 0.0,
    start_level_nap: float | None = None,
    replace: bool = False,
    allow_existing: bool = False,
) -> BeddingPlan:
    """Check everything and collect every problem; the plan is only usable without problems."""
    plan = BeddingPlan(warnings=list(model.warnings))

    selected, selection_problems = select_piles(model.members, piles)
    plan.problems.extend(selection_problems)

    stiffness: dict[SoilLayer, float] = {}
    for layer in layers:
        try:
            stiffness[layer] = line_stiffness(layer, diameter_m, alpha, groepsfactor)
        except ValueError as exc:
            message = f"Layer {layer.top_nap}..{layer.bottom_nap}: {exc}."
            plan.problems.append(message)
    if len(stiffness) != len(layers):
        return plan

    for pile in selected:
        if pile.member_id is None:
            plan.warnings.append(
                f"{pile.name}: its member id could not be read, so its existing supports cannot be counted "
                "and the read-back cannot confirm the member."
            )
        ours, theirs = _existing_for(pile, model.supports)
        if theirs and not allow_existing:
            plan.problems.append(
                f"{pile.name} already carries {theirs} line support(s) that are not from this tool. Adding more "
                "would double the stiffness. Remove them in SCIA first, or pass allow-existing if that is intended."
            )
            continue
        if theirs:
            plan.warnings.append(f"{pile.name}: keeping {theirs} existing line support(s); stiffness adds up.")

        try:
            segments = pile_segments(pile, layers, datum_offset, start_level_nap)
        except ValueError as exc:
            plan.problems.append(str(exc))
            continue
        if not segments:
            plan.problems.append(f"{pile.name}: no layer in the table overlaps this pile.")
            continue

        if ours and not replace:
            plan.skipped_piles.append(pile.name)
            plan.warnings.append(f"{pile.name}: already has {ours} support(s) from this tool; skipped (use replace).")
            continue
        if ours > len(segments):
            plan.problems.append(
                f"{pile.name}: replacing {ours} supports with {len(segments)} would leave {ours - len(segments)} old "
                "ones in place (an update can only overwrite by name). Start from a file without them."
            )
            continue

        covered_to = segments[-1].bottom_nap
        toe = pile.toe_z - datum_offset
        if covered_to > toe + 1e-6:
            plan.warnings.append(f"{pile.name}: no layer below NAP {covered_to:g}; toe is at NAP {toe:g}.")
        for number, segment in enumerate(segments, start=1):
            plan.planned.append(
                PlannedSegment(segment_name(pile.name, number), pile, segment, stiffness[segment.layer])
            )

    if not plan.planned and not plan.problems and not plan.skipped_piles:
        plan.problems.append("Nothing to do: no pile was selected.")
    return plan
