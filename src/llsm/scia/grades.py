"""Material grades: what the engineer says ("C30/37", "S235") matched to a name in SCIA's material library.

SCIA takes a material's stiffness and weight from its library when the ADM material's Quality is a
library name: "C30/37", "C20/25", "S 235" (with the space) gave the real E for concrete and steel
(docs/verify/calc-run.md, try 19). Anything else gets SCIA's default concrete (E about 27 GPa).

The library is the start model's materials table (start_model.material_grades). Matching ignores case
and spaces, so "s235" finds "S 235"; a name with a standard in brackets is found by its first part
("C24" finds "C24 (EN 338)") when that is the only one. An unknown grade is refused with the closest
library names.
"""
from __future__ import annotations

import difflib
import re

KINDS = ("Steel", "Concrete", "Timber", "Aluminium")
_CONCRETE = re.compile(r"^(LC|C)\d+/\d+", re.I)
_STEEL = re.compile(r"^S\s?\d{3}\b", re.I)
_TIMBER = re.compile(r"^(C|D|T)\d{2}\s*\(EN 338\)|^GL\s?\d", re.I)


class GradeError(ValueError):
    """A grade that is not in the library, or a kind that contradicts it."""


def normal(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _base(name: str) -> str:
    return normal(re.sub(r"\s*\([^)]*\)\s*$", "", name))


def match(grade: str, library: list[str] | tuple[str, ...] | frozenset[str]) -> str:
    """The library name for ``grade``. Raises GradeError with the closest names when there is none."""
    wanted = normal(grade)
    names = sorted(set(library))
    exact = [n for n in names if normal(n) == wanted]
    if exact:
        return exact[0]
    by_base = [n for n in names if _base(n) == wanted]
    if len(by_base) == 1:
        return by_base[0]
    if by_base:
        raise GradeError(f"{grade!r} matches several materials in SCIA's library: {', '.join(by_base[:6])}. "
                         "Give the full name.")
    close = difflib.get_close_matches(grade, names, n=5, cutoff=0.5)
    close += [n for n in difflib.get_close_matches(wanted, [normal(x) for x in names], n=5, cutoff=0.6)
              if n not in map(normal, close)]
    suggestion = ", ".join(dict.fromkeys(close)) if close else "C30/37, C20/25, S 235, S 355"
    raise GradeError(f"{grade!r} is not a material grade in SCIA's library. Closest: {suggestion}.")


def kind_of(grade: str) -> str | None:
    """Concrete, Steel or Timber when the grade says so unambiguously; otherwise None."""
    text = grade.strip()
    if _TIMBER.match(text):
        return "Timber"
    if _CONCRETE.match(re.sub(r"\s+", "", text)):
        return "Concrete"
    if _STEEL.match(text):
        return "Steel"
    return None


def looks_like_grade(name: str) -> bool:
    """A material name that is itself a grade ("C30/37", "S235"): then it is used as the grade."""
    return kind_of(name) is not None


def resolve(name: str, kind: str | None, grade: str | None,
            library: list[str] | tuple[str, ...] | frozenset[str] | None) -> tuple[str, str | None, bool]:
    """(kind, quality for SCIA, validated) for a new material.

    ``grade`` falls back to the name when the name is a grade. Without a library (no start model) the
    grade is kept as typed and not validated. A kind that contradicts the grade is refused.
    """
    if kind is not None and kind not in KINDS:
        raise GradeError(f"unknown material kind {kind!r}; use one of {', '.join(KINDS)}")
    if not grade:  # a material named like a grade ("C30/37", "S235") is that grade
        if library:
            try:
                grade = match(name, library)
            except GradeError:
                pass
        elif looks_like_grade(name):
            grade = name
    if not grade:
        if kind is None:
            raise GradeError("say what the material is: a grade (e.g. C30/37, S235) or a kind "
                             f"({', '.join(KINDS)})")
        return kind, None, False
    quality = match(grade, library) if library else grade.strip()
    derived = kind_of(quality)
    if kind is None:
        if derived is None:
            raise GradeError(f"give the kind of {quality!r} ({', '.join(KINDS)}); LLSM cannot tell it from the name")
        kind = derived
    elif derived is not None and derived != kind:
        raise GradeError(f"{quality!r} is a {derived.lower()} grade, not {kind.lower()}: "
                         f"use kind {derived!r} or another grade")
    return kind, quality, bool(library)
