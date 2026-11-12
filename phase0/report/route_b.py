"""Route B spike: SCIA result export (XML IO document) -> Word report via docxtpl.

  python route_b.py RESULT.xml OUT.docx [--by case|member] [--title TEXT]

1. parse_xmlio: the export is a flat sequence of tables. A selection table
   ("Belastingsgevallen - X", "Combinaties - X", "Resultaatklasses - X") is followed by the
   result tables computed for it ("Reacties", "Interne 1D-krachten", "1D-vervormingen", ...).
2. Units: SCIA's XML export is SI. Columns are converted by name (N -> kN, Nm -> kNm, m -> mm
   for displacements, rad -> mrad); a numeric column with no known unit stays SI and is listed
   as such in the preview, never guessed.
3. Order: --by case  -> chapter per load case/combination/result class, table per result
   type, rows by member (natural sort).  --by member -> chapter per member, then per selection.
4. Rendering: a docxtpl template (built here with python-docx, as the kademuren app's
   prepare_template.py does) with one paragraph loop over sections; each table is a subdoc.
5. Read back: the .docx is reopened and every table cell is compared with the rows written.

Pure Python, no SCIA. Lifted from the apps: the docxtpl render pattern (plaatbruggen
report_functions.py), decimal comma and 2-decimal formatting and "Table Grid" style
(kademuren word_rendering.py), the SI->kN/kNm factors (plaatbruggen scia_unit_conversion.py).
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

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


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    return raw.decode("utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8", "replace")


def unit_for(column: str) -> tuple[str, float] | None:
    key = column.lower().replace("_", "").replace(" ", "")
    # 1D deformations use f_x for rotations; reactions/forces never use f_*: decide by the other columns later
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
    text = read_text(path)
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
        if found and found[0] in ("kN", "kNm") and "lineaire" in t.heading.lower():
            found = (found[0] + "/m", found[1])   # line reactions: N/m, Nm/m in the export
        if found:
            t.units[col] = found
        else:
            t.unknown.append(col)


def natural(text: str) -> list:
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", text)]


def fmt(value: str, factor: float, decimals: int = 2) -> str:
    """Kademuren style: 2 decimals, decimal comma. -0,00 printed as 0,00."""
    number = float(value.replace(",", ".")) * factor
    out = f"{number:.{decimals}f}"
    if float(out) == 0:
        out = f"{0:.{decimals}f}"
    return out.replace(".", ",")


def shown(t: Table) -> list[str]:
    return [c for c in t.columns if c not in NOT_SHOWN]


def display_rows(t: Table) -> tuple[list[str], list[list[str]]]:
    cols = shown(t)
    header = [f"{c} [{t.units[c][0]}]" if c in t.units else (f"{c} [SI]" if c in t.unknown else c) for c in cols]
    member_col = next((i for i, c in enumerate(t.columns) if c in ("Naam", "Name")), 0)
    load_col = next((i for i, c in enumerate(t.columns) if c in ("Belasting", "Load", "Case")), None)
    rows = sorted(t.rows, key=lambda r: (natural(r[member_col]), r[load_col] if load_col is not None else ""))
    out = []
    for r in rows:
        line = []
        for c in cols:
            v = r[t.columns.index(c)]
            line.append(fmt(v, t.units[c][1]) if c in t.units and v != "" else v)
        out.append(line)
    return header, out


def sections(tables: list[Table], by: str) -> list[dict]:
    """Return [{title, subtitle, header, rows}] in report order."""
    out = []
    if by == "case":
        seen: list[tuple[str, str]] = []
        for t in tables:
            if (t.selection_kind, t.selection) not in seen:
                seen.append((t.selection_kind, t.selection))
        for kind, sel in seen:
            for t in [t for t in tables if (t.selection_kind, t.selection) == (kind, sel)]:
                header, rows = display_rows(t)
                out.append({"chapter": f"{kind} - {sel}", "title": f"{t.name} ({t.heading})", "header": header, "rows": rows})
    else:
        members: dict[str, list] = {}
        for t in tables:
            member_col = next((i for i, c in enumerate(t.columns) if c in ("Naam", "Name")), 0)
            for r in t.rows:
                members.setdefault(r[member_col].split("/")[0], []).append((t, r))
        for member in sorted(members, key=natural):
            for t in dict.fromkeys(t for t, _ in members[member]):
                sub = Table(t.selection_kind, t.selection, t.name, t.heading, t.columns,
                            [r for tt, r in members[member] if tt is t], t.units, t.unknown)
                header, rows = display_rows(sub)
                out.append({"chapter": member, "title": f"{t.selection_kind} - {t.selection}: {t.name}", "header": header, "rows": rows})
    return out


def build_template(path: Path) -> None:
    """A minimal docxtpl template: title, then a loop of chapter/table sections, each table a subdoc."""
    from docx import Document

    doc = Document()
    doc.add_heading("{{ title }}", level=0)
    doc.add_paragraph("Model: {{ model }} · export: {{ source }} · eenheden: kN, kNm, mm, mrad (SCIA-export in SI, omgerekend)")
    doc.add_paragraph("{%p for s in sections %}")
    doc.add_paragraph("{%p if s.new_chapter %}")
    doc.add_heading("{{ s.chapter }}", level=1)
    doc.add_paragraph("{%p endif %}")
    doc.add_heading("{{ s.title }}", level=2)
    doc.add_paragraph("{{p s.table }}")
    doc.add_paragraph("{%p endfor %}")
    doc.save(path)


def table_subdoc(tpl, header: list[str], rows: list[list[str]]):
    sub = tpl.new_subdoc()
    table = sub.add_table(rows=1, cols=len(header))
    table.style = "Table Grid"
    for cell, text in zip(table.rows[0].cells, header):
        cell.text = text
        for run in cell.paragraphs[0].runs:
            run.bold = True
    for r in rows:
        cells = table.add_row().cells
        for cell, text in zip(cells, r):
            cell.text = text
    return sub


def render(xml: Path, out: Path, by: str, title: str) -> dict:
    from docxtpl import DocxTemplate

    tables = parse_xmlio(xml)
    secs = sections(tables, by)
    template = out.with_name(out.stem + "_template.docx")
    build_template(template)
    tpl = DocxTemplate(template)
    previous = None
    context_secs = []
    for s in secs:
        context_secs.append({**s, "new_chapter": s["chapter"] != previous, "table": table_subdoc(tpl, s["header"], s["rows"])})
        previous = s["chapter"]
    tpl.render({"title": title, "model": xml.stem, "source": xml.name, "sections": context_secs})
    tpl.save(out)
    return {"tables": len(tables), "sections": len(secs), "rows": sum(len(s["rows"]) for s in secs),
            "unknown_units": sorted({f"{t.name}:{c}" for t in tables for c in t.unknown}),
            "check": read_back(out, secs)}


def read_back(path: Path, secs: list[dict]) -> dict:
    """Reopen the .docx and compare every table cell with what was meant to be written."""
    from docx import Document

    doc = Document(path)
    written = [[[c.text for c in row.cells] for row in t.rows] for t in doc.tables]
    expected = [[s["header"], *s["rows"]] for s in secs]
    mismatches = 0
    for got, want in zip(written, expected):
        if len(got) != len(want):
            mismatches += abs(len(got) - len(want))
        mismatches += sum(1 for g, w in zip(got, want) for a, b in zip(g, w) if a != b)
    return {"tables_written": len(written), "tables_expected": len(expected),
            "cells_expected": sum(len(r) for t in expected for r in t), "mismatches": mismatches,
            # zero tables is a failure, never "ok": an empty export must not make an empty report
            "ok": len(expected) > 0 and len(written) == len(expected) and mismatches == 0}


def preview(xml: Path, by: str) -> None:
    tables = parse_xmlio(xml)
    print(f"{len(tables)} result tables in {xml.name}")
    for t in tables:
        print(f"  [{t.selection_kind} - {t.selection}] {t.name} / {t.heading}: {len(t.rows)} rows; "
              f"units {{{', '.join(f'{c}: {u}' for c, (u, _) in t.units.items())}}}"
              + (f"; NO UNIT (left SI): {t.unknown}" if t.unknown else ""))
    secs = sections(tables, by)
    if secs:
        s = secs[0]
        print(f"first section: {s['chapter']} / {s['title']}")
        print("   ", " | ".join(s["header"]))
        for r in s["rows"][:3]:
            print("   ", " | ".join(r))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("xml", type=Path)
    ap.add_argument("out", type=Path, nargs="?")
    ap.add_argument("--by", choices=("case", "member"), default="case")
    ap.add_argument("--title", default="SCIA-resultaten")
    args = ap.parse_args()
    preview(args.xml, args.by)
    if args.out:
        result = render(args.xml, args.out, args.by, args.title)
        print(json.dumps(result, indent=1, ensure_ascii=False))
        return 0 if result["check"]["ok"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
