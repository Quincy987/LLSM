"""Read SCIA's XML results export (the XML IO document behind ``ESA_XML ... -x<file>``).

The export is a flat sequence of tables. A selection table ("Resultaatklasses - X",
"Combinaties - X", "Belastingsgevallen - X") is followed by the result tables computed for it
("Interne 1D-krachten", "1D-vervormingen", "Reacties", ...). Values are SI (N, Nm, m, rad);
:func:`assign_units` names the display unit and factor of each numeric column by its name and
leaves a column it does not know in SI, listed in ``Table.unknown``, never guessed.

Spike origin: phase0/report/route_b.py (docs/verify/report-route.md, route B); the unit factors
are those of the plaatbruggen app's scia_unit_conversion.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

SELECTION = re.compile(r"^(Belastingsgevallen|Combinaties|Resultaatklasses|Load cases|Combinations|Result classes) - (.+)$")
TEXT_COLUMNS = {"Naam", "Name", "Belasting", "Load", "Case", "Fouten, waarschuwingen, opmerkingen", "Knoop", "Node", "Lid", "Member", "Element", "Vezel"}
NOT_SHOWN = {"Fouten, waarschuwingen, opmerkingen"}

# Column name (SCIA's Dutch export, lower case, underscores stripped) -> (unit, factor on SI).
UNITS: list[tuple[re.Pattern, str, float]] = [
    (re.compile(r"^(r[xyz]|n|v[yz]|f[xyz]?)$"), "kN", 1e-3),             # forces, reactions
    (re.compile(r"^(m[xyz]|mt|m)$"), "kNm", 1e-3),                       # moments
    (re.compile(r"^(u[xyz]|utotal|ux|uy|uz|deltaz)$"), "mm", 1e3),       # displacements
    (re.compile(r"^(f[xyz]|phi[xyz]|fi[xyz])$"), "mrad", 1e3),            # rotations (1D deformations)
    (re.compile(r"^(m[xy]d?[+-]?|mxy)$"), "kNm/m", 1e-3),               # 2D moments
    (re.compile(r"^(n[xy]|nxy|v[xy])$"), "kN/m", 1e-3),                  # 2D membrane / shear forces
    (re.compile(r"^(sigma.*|tau.*|s[xyz][+-]?|t[xyz]{2}[+-]?|ttor)$"), "MPa", 1e-6),  # stresses
    (re.compile(r"^(dx|x|y|z|e[xy])$"), "m", 1.0),                       # positions, eccentricities
]


@dataclass(eq=False)
class Table:
    selection_kind: str          # Belastingsgevallen / Combinaties / Resultaatklasses
    selection: str               # e.g. "Alle UGT"
    name: str                    # e.g. "Interne 1D-krachten"
    heading: str                 # SCIA's section heading inside the table, e.g. "Knoopreacties"
    columns: list[str]
    rows: list[list[str]]
    units: dict[str, tuple[str, float]] = field(default_factory=dict)
    unknown: list[str] = field(default_factory=list)

    def column(self, name: str) -> list[str]:
        i = self.columns.index(name)
        return [row[i] for row in self.rows]


def read_text(path: Path) -> str:
    raw = Path(path).read_bytes()
    return raw.decode("utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8", "replace")


def unit_for(column: str) -> tuple[str, float] | None:
    key = column.lower().replace("_", "").replace(" ", "")
    for pattern, unit, factor in UNITS:
        if pattern.match(key):
            return unit, factor
    return None


def is_number(text: str) -> bool:
    try:
        float(text.replace(",", "."))
        return True
    except ValueError:
        return False


def parse_xmlio(path: Path) -> list[Table]:
    """Every result table of the export, each with the selection (result class, case) it belongs to."""
    return parse_xmlio_text(read_text(path))


def parse_xmlio_text(text: str) -> list[Table]:
    tables: list[Table] = []
    kind, selection = "", "(geen selectie)"
    for match in re.finditer(r'(?s)<table [^>]*name="([^"]+)"[^>]*>.*?</table>', text):
        name, body = match.group(1), match.group(0)
        sel = SELECTION.match(name)
        if sel:
            kind, selection = sel.group(1), sel.group(2)
            continue
        chunks = re.split(r'<p\d+ h="', body)[1:]   # one chunk per section (p0, p1, ...) of the table
        for chunk in chunks:
            heading, inner = chunk.split('"', 1)[0], chunk
            labels = dict(re.findall(r'<h(\d+) t="([^"]*)"/>', inner))
            order = sorted(labels, key=int)
            rows = []
            for row in re.finditer(r"(?s)<row [^>]*>(.*?)</row>", inner):
                cells = dict(re.findall(r'<p(\d+) v="([^"]*)"/>', row.group(1)))
                rows.append([cells.get(i, "") for i in order])
            tables.append(Table(kind, selection, name, heading, [labels[i] for i in order], rows))
    for t in tables:
        assign_units(t)
    return tables


def assign_units(t: Table) -> None:
    rotations = "U_total" in t.columns or "u_x" in t.columns   # deformation tables: f_* = rotations
    for col in t.columns:
        if col in TEXT_COLUMNS or col in NOT_SHOWN:
            continue
        values = [r[t.columns.index(col)] for r in t.rows if r[t.columns.index(col)] != ""]
        if not values or not all(is_number(v) for v in values):
            continue
        key = col.lower().replace("_", "")
        if key in ("fx", "fy", "fz"):
            t.units[col] = ("mrad", 1e3) if rotations else ("kN", 1e-3)
            continue
        found = unit_for(col)
        per_metre = "lineaire" in t.heading.lower() or "2D-krachten" in t.name   # line reactions, 2D forces
        if found and found[0] in ("kN", "kNm") and per_metre:
            found = (found[0] + "/m", found[1])   # N/m, Nm/m in the export
        if found:
            t.units[col] = found
        else:
            t.unknown.append(col)
