"""Which members are piles, and where each soil layer falls along them."""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from ..scia.esa_file import Member, Point
from .layers import SoilLayer

MIN_VERTICALITY = 0.7


@dataclass(frozen=True)
class Pile:
    name: str
    member_id: int | None
    begin: Point
    end: Point

    @classmethod
    def from_member(cls, member: Member) -> "Pile":
        return cls(member.name, member.member_id, member.begin, member.end)

    @property
    def top_z(self) -> float:
        return max(self.begin[2], self.end[2])

    @property
    def toe_z(self) -> float:
        return min(self.begin[2], self.end[2])


@dataclass(frozen=True)
class Segment:
    """The part of one soil layer on one pile."""

    pile: str
    top_nap: float
    bottom_nap: float
    x1: float
    x2: float
    layer: SoilLayer


def select_piles(members: list[Member], spec: str) -> tuple[list[Pile], list[str]]:
    """Pick piles by a comma-separated list of names and globs.

    An exact name is taken as asked. A glob only picks members steep enough to be piles.
    Returns the piles and the problems (names that match nothing).
    """
    by_name = {m.name: m for m in members}
    chosen: dict[str, Member] = {}
    problems: list[str] = []
    for part in (p.strip() for p in spec.split(",")):
        if not part:
            continue
        if any(ch in part for ch in "*?["):
            matches = [
                m for m in members if fnmatch.fnmatchcase(m.name, part) and m.verticality > MIN_VERTICALITY
            ]
            if not matches:
                problems.append(f"No pile matches {part!r}.")
            chosen.update((m.name, m) for m in matches)
        elif part in by_name:
            chosen[part] = by_name[part]
        else:
            problems.append(f"No member named {part!r} in the model.")
    return [Pile.from_member(m) for m in chosen.values()], problems


def pile_segments(
    pile: Pile,
    layers: list[SoilLayer],
    datum_offset: float = 0.0,
    start_level_nap: float | None = None,
) -> list[Segment]:
    """Cut the layers to this pile, top down.

    A layer is clipped to the part between the toe and the start level; a layer that
    straddles the start level keeps its lower part. Positions are relative along the
    member, and use the true z of the member's two ends, so inclined piles are correct.
    """
    z_begin, z_end = pile.begin[2], pile.end[2]
    if z_begin == z_end:
        raise ValueError(f"Member {pile.name} is horizontal; a layer table cannot be applied to it.")
    top_nap = pile.top_z - datum_offset
    toe_nap = pile.toe_z - datum_offset
    start = top_nap if start_level_nap is None else min(start_level_nap, top_nap)

    segments: list[Segment] = []
    for layer in layers:
        upper, lower = min(layer.top_nap, start), max(layer.bottom_nap, toe_nap)
        if upper <= lower:
            continue
        a, b = ((z + datum_offset - z_begin) / (z_end - z_begin) for z in (upper, lower))
        segments.append(Segment(pile.name, upper, lower, min(a, b), max(a, b), layer))
    return segments
