"""Can the model move without deforming? Which free motions does LLSM hold, and which does it refuse?

A beam on a pin and rollers can still turn about its own axis, and swing sideways about the pin: the
supports hold it only in the plane of the loads. SCIA's solver then sometimes gives no results at all
(docs/verify/calc-run.md, try 19). This module finds those free motions before the model goes to SCIA.

The model is seen as LLSM builds it: members rigidly connected at the nodes they share, no hinges, no
releases and no springs, supports Rigid or Free per global direction. Then each group of connected
members moves as ONE rigid body, and its free motions are the rigid-body motions (v, w) that no support
stops: the velocity at a point p is v + w x p. A held translation d at p stops the motions with
d . (v + w x p) = d . v + w . (p x d) = 0; a held rotation a stops those with a . w = 0. These rows make the
constraint matrix of the group; its null space are the free motions. Pure Python: six columns at most.

A free motion on which no load does work, at any point of any loaded member (each line load and, with a
self-weight case, gravity on every member): LLSM adds as many extra support restraints as there are free
motions, each one stopping one of them. The model then has a unique solution with zero reaction in every
added restraint (the unloaded model can already be put in equilibrium; a rigid motion of the unloaded
mechanism brings every added restraint to zero displacement), so the forces do not change. Candidates are
the free directions of the existing supports: translations first (a bearing held sideways), then rotations
(held against twisting, a fork support).

A free motion a load does work on is a real mechanism (or a beam that tips over): the model is refused
with the motion in plain words, before a 30-second calculation that gives nothing.

When a node of one group lies on a member or a node of another group (within 1 mm), SCIA may connect what
LLSM sees as separate: nothing is added and nothing refused, only a warning.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

DOFS = ("X", "Y", "Z", "RX", "RY", "RZ")
_AXES = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
TOUCH_MM = 1.0
_TOL = 1e-9

Vec = tuple[float, float, float]


@dataclass(frozen=True)
class Support:
    name: str
    node: str
    held: frozenset[str]  # of DOFS


@dataclass(frozen=True)
class Load:
    name: str
    member: str
    direction: Vec  # global, unit or not


@dataclass
class Stability:
    """extras: support -> DOFs LLSM holds besides the requested ones. refusals: why the model cannot be
    calculated. warnings: said, nothing changed. held_motions: the free motions the extras stop."""
    extras: dict[str, list[str]] = field(default_factory=dict)
    refusals: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    held_motions: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.refusals

    def sentence(self) -> str | None:
        """One plain sentence about what LLSM holds extra, or None."""
        if not self.extras:
            return None
        held = "; ".join(f"{s} also in {', '.join(_dof_words(d) for d in dofs)}" for s, dofs in self.extras.items())
        motions = ", ".join(self.held_motions) if self.held_motions else "move freely"
        return (f"LLSM holds {held}: otherwise the model could {motions}. No load acts in those directions, "
                "so the forces do not change.")


def _dof_words(dof: str) -> str:
    return {"X": "X", "Y": "Y", "Z": "Z", "RX": "rotation about X", "RY": "rotation about Y",
            "RZ": "rotation about Z"}[dof]


# --- small linear algebra ---------------------------------------------------

def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm(a) -> float:
    return math.sqrt(_dot(a, a))


def _unit(a: Vec) -> Vec:
    n = _norm(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 0 else a


def _reduce(rows: list[list[float]]) -> list[list[float]]:
    """Row echelon form (Gaussian elimination with partial pivoting); zero rows dropped."""
    m = [list(r) for r in rows]
    out: list[list[float]] = []
    col = 0
    while m and col < 6:
        pivot = max(range(len(m)), key=lambda i: abs(m[i][col]))
        if abs(m[pivot][col]) <= _TOL:
            col += 1
            continue
        p = m.pop(pivot)
        p = [x / p[col] for x in p]
        for r in m:
            f = r[col]
            if f:
                for j in range(6):
                    r[j] -= f * p[j]
        out.append(p)
        col += 1
    return out


def _rank(rows: list[list[float]]) -> int:
    return len(_reduce(rows))


def _null_space(rows: list[list[float]]) -> list[list[float]]:
    """A basis of the vectors m with r . m = 0 for every row r."""
    red = _reduce(rows)
    # back-substitute to reduced row echelon form
    pivots = []
    for i, r in enumerate(red):
        c = next(j for j in range(6) if abs(r[j]) > _TOL)
        pivots.append(c)
    for i in range(len(red) - 1, -1, -1):
        c = pivots[i]
        for k in range(i):
            f = red[k][c]
            if f:
                red[k] = [a - f * b for a, b in zip(red[k], red[i])]
    basis = []
    for free in (j for j in range(6) if j not in pivots):
        v = [0.0] * 6
        v[free] = 1.0
        for i, c in enumerate(pivots):
            v[c] = -red[i][free]
        basis.append(v)
    return basis


# --- the model --------------------------------------------------------------

def _row(dof: str, p: Vec) -> list[float]:
    """The constraint a held DOF at point p puts on a rigid motion (v, w)."""
    if dof.startswith("R"):
        a = _AXES[dof[1]]
        return [0.0, 0.0, 0.0, *a]
    d = _AXES[dof]
    return [*d, *_cross(p, d)]


def _velocity(m: list[float], p: Vec) -> Vec:
    v, w = m[:3], m[3:]
    c = _cross(tuple(w), p)
    return (v[0] + c[0], v[1] + c[1], v[2] + c[2])


def _groups(members: dict[str, tuple[str, str]]) -> list[list[str]]:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in members.values():
        parent[find(a)] = find(b)
    by_root: dict[str, list[str]] = {}
    for name, (a, _) in members.items():
        by_root.setdefault(find(a), []).append(name)
    return list(by_root.values())


def _on_segment(p: Vec, a: Vec, b: Vec, tol: float) -> bool:
    ab = _sub(b, a)
    length = _norm(ab)
    if length == 0:
        return _norm(_sub(p, a)) <= tol
    t = _dot(_sub(p, a), ab) / (length * length)
    if t < -tol / length or t > 1 + tol / length:
        return False
    closest = (a[0] + t * ab[0], a[1] + t * ab[1], a[2] + t * ab[2])
    return _norm(_sub(p, closest)) <= tol


def _touching(nodes: dict[str, Vec], members: dict[str, tuple[str, str]], groups: list[list[str]]) -> list[str]:
    """Places where two groups meet without sharing a node: SCIA may connect them."""
    group_of_node: dict[str, int] = {}
    for g, names in enumerate(groups):
        for m in names:
            for n in members[m]:
                group_of_node[n] = g
    hits = []
    used = sorted(group_of_node)
    for i, a in enumerate(used):
        for b in used[i + 1:]:
            if group_of_node[a] != group_of_node[b] and _norm(_sub(nodes[a], nodes[b])) <= TOUCH_MM:
                hits.append(f"nodes {a} and {b} are at the same place")
    for n in used:
        for m, (a, b) in members.items():
            if group_of_node[n] != group_of_node[a] and _on_segment(nodes[n], nodes[a], nodes[b], TOUCH_MM):
                hits.append(f"node {n} lies on member {m}")
    return hits


def _motion_words(m: list[float], nodes: dict[str, Vec], group_nodes: list[str],
                  members: dict[str, tuple[str, str]], group: list[str], scale: float) -> str:
    """A free motion in plain words, matched against the simple ones (slide, turn about a line)."""
    w = m[3:]
    if _norm(w) <= 1e-7:
        d = _unit(tuple(m[:3]))
        axis = next((k for k, a in _AXES.items() if abs(abs(_dot(a, d)) - 1) < 1e-6), None)
        return f"slide in {axis}" if axis else "slide sideways"
    wu = _unit(tuple(w))
    # a point on the axis: v + w x p parallel to w  ->  p = w x v / |w|^2
    v = tuple(m[:3])
    wn = _dot(w, w)
    p0 = tuple(x / wn for x in _cross(tuple(w), v))
    on_axis = [n for n in group_nodes if _norm(_cross(wu, _sub(tuple(x / scale for x in nodes[n]), p0))) < 1e-6]
    for name in group:
        a, b = members[name]
        if a in on_axis and b in on_axis:
            return f"turn about its own axis ({name})"
    axis = next((k for k, a in _AXES.items() if abs(abs(_dot(a, wu)) - 1) < 1e-6), None)
    if len(on_axis) >= 2 and axis != "Z":
        return f"tip over about the line {on_axis[0]}-{on_axis[-1]}"
    where = f" through node {on_axis[0]}" if on_axis else ""
    if axis == "Z":
        return f"swing about the vertical{where}"
    if axis:
        return f"turn about the {axis} axis{where}"
    return f"turn about an inclined axis{where}"


def check(nodes: dict[str, Vec], members: dict[str, tuple[str, str]], supports: list[Support],
          loads: list[Load], self_weight: bool = False, releases: bool = False) -> Stability:
    """Free motions of every group of connected members; extras for the unloaded ones (see module doc).

    nodes in mm (global); members: name -> (node a, node b); loads in global directions.
    """
    out = Stability()
    if releases:  # a hinge splits a group into bodies that move against each other: not judged here
        out.warnings.append("The model has hinges or releases; LLSM does not check whether it can move freely.")
        return out
    members = {m: ab for m, ab in members.items() if ab[0] in nodes and ab[1] in nodes}
    if not members:
        return out
    groups = _groups(members)
    touching = _touching(nodes, members, groups) if len(groups) > 1 else []
    if touching:
        out.warnings.append("Separate parts of the model touch without a shared node (" + "; ".join(touching[:3])
                            + "): SCIA may connect them, so LLSM does not check or hold free motions. "
                            "Share the node if they are meant to be connected.")
        return out
    used = {n for ab in members.values() for n in ab}
    scale = max((abs(c) for n in used for c in nodes[n]), default=1.0) or 1.0
    pos = {n: tuple(c / scale for c in nodes[n]) for n in used}
    loads_by_member: dict[str, list[Vec]] = {}
    for ld in loads:
        if ld.member in members and _norm(ld.direction) > 0:
            loads_by_member.setdefault(ld.member, []).append(_unit(ld.direction))
    if self_weight:
        for m in members:
            loads_by_member.setdefault(m, []).append((0.0, 0.0, -1.0))

    for group in groups:
        gnodes = sorted({n for m in group for n in members[m]})
        gsupports = [s for s in supports if s.node in gnodes]
        label = group[0] if len(group) == 1 else f"{group[0]} and {len(group) - 1} more"
        if not gsupports:
            out.refusals.append(f"Member {label} has no support: it can move freely. Add a support.")
            continue
        rows = [_row(d, pos[s.node]) for s in gsupports for d in sorted(s.held)]
        free = _null_space(rows) if rows else [[1.0 if i == j else 0.0 for j in range(6)] for i in range(6)]
        if not free:
            continue
        texts = [_motion_words(m, nodes, gnodes, members, group, scale) for m in free]
        words = list(dict.fromkeys(texts))  # each description once
        loaded = [
            (ld_member, text) for m, text in zip(free, texts)
            for ld_member, dirs in loads_by_member.items() if ld_member in group
            for d in dirs for n in members[ld_member] if abs(_dot(d, _velocity(m, pos[n]))) > 1e-7
        ]
        if loaded:
            moved = list(dict.fromkeys(text for _, text in loaded))
            out.refusals.append(
                f"The model ({label}) can {' or '.join(moved)}, and the load on {loaded[0][0]} would move it that "
                "way: it is a mechanism, SCIA cannot calculate it. Add a support, or hold a support in more directions.")
            continue
        # greedy: free directions of the existing supports that stop one more free motion
        candidates = [(s, d) for s in gsupports for d in ("X", "Y", "Z") if d not in s.held]
        candidates += [(s, d) for s in gsupports for d in ("RX", "RY", "RZ") if d not in s.held]
        current = list(rows)
        added: dict[str, list[str]] = {}
        rank = _rank(current)
        for s, d in candidates:
            if rank == 6:
                break
            trial = current + [_row(d, pos[s.node])]
            r = _rank(trial)
            if r > rank:
                current, rank = trial, r
                added.setdefault(s.name, []).append(d)
        if rank < 6:
            out.refusals.append(f"The model ({label}) can {' or '.join(words)}, and its supports cannot stop that. "
                                "Add a support.")
            continue
        for s, dofs in added.items():
            out.extras.setdefault(s, []).extend(dofs)
        out.held_motions.extend(w for w in words if w not in out.held_motions)
    return out
