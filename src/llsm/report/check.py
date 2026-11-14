"""Does a printed engineering report hold results? Read from its XLSX export.

SCIA writes one sheet per report table, titled with the chapter number (cut at Excel's 31
characters) and in reverse order. A results table opens with SCIA's settings lines
("Lineaire berekening", "Klasse: Alle UGT", "Extreem: Globaal", "Selectie: Alle"), then a
header, a units row and the data. Seen in docs/verify/report-route.md:

- an old-type results item prints one cell, "Geen ondersteunde taak ... 'v16 en ouder'",
  with exit 0 and a full-size PDF: only this text shows the results are missing;
- an empty table prints one cell, "Lege tabel", also for an empty INPUT table (internal
  edges of 2D members), so it only counts in a results chapter;
- a result class without combinations (the template's "Alle UGT" on a model with only
  characteristic combinations) prints its tables with SCIA's error "Fout E-C04: Niet alle
  items in deze klasse zijn berekend" and no data. That class is empty in the model, not
  badly printed: it is reported apart and does not fail the check. Any other table without
  data does.

A chapter (the first number of a sheet's title) is a results chapter when it holds a results
table or an unsupported item. Exit codes and file sizes are not evidence; only this check is.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .xlsx import Cell, read_sheets

UNSUPPORTED = ("geen ondersteunde taak", "no supported task", "unsupported task", "not supported task")
EMPTY = ("lege tabel", "empty table")
CLASS_KEYS = ("klasse", "class")
ANALYSIS = ("lineaire berekening", "linear calculation", "niet-lineaire berekening", "nonlinear calculation")
LEGEND = ("combinatiesleutel", "combination key", "verklaring van symbolen", "explanation of symbols")
_CHAPTER = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s*(.*)$")
_SETTING = re.compile(r"^\s*([^:]{2,60}):\s*(.*)$")
_MESSAGE = re.compile(r"^\s*(Fout|Error|Waarschuwing|Warning|Opmerking|Note)\s+[EWN]-[A-Z]*\d+\b", re.I)
CLASS_LIST_TITLES = ("resultaatklasse", "result class")


@dataclass
class ResultTable:
    sheet: str
    chapter: tuple[int, ...]
    title: str
    settings: dict[str, str]  # e.g. {"Klasse": "Alle UGT", "Extreem": "Globaal", "Selectie": "Alle"}
    columns: list[str]
    units: list[str]
    rows: list[list[Cell]]
    messages: list[str] = field(default_factory=list)  # SCIA's own errors and warnings in the table

    @property
    def result_class(self) -> str | None:
        return next((v for k, v in self.settings.items() if k.lower() in CLASS_KEYS), None)

    def column(self, name: str) -> list[Cell]:
        """The values of one column, by its header text."""
        if name not in self.columns:
            raise KeyError(f"{name!r} not in {self.columns}")
        index = self.columns.index(name)
        return [row[index] if index < len(row) else None for row in self.rows]


@dataclass
class ReportCheck:
    ok: bool
    tables: list[ResultTable] = field(default_factory=list)
    expected: int = 0  # results items: tables, empty tables and unsupported items in results chapters
    written: int = 0  # results tables with data rows
    empty: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    empty_classes: dict[str, list[str]] = field(default_factory=dict)  # class -> its tables, all without data
    messages: list[str] = field(default_factory=list)  # SCIA's errors/warnings printed in tables WITH data
    class_members: dict[str, list[str]] = field(default_factory=dict)  # result class -> its combinations
    problems: list[str] = field(default_factory=list)
    input_sheets: int = 0

    def summary(self) -> dict:
        return {
            "expected": self.expected,
            "written": self.written,
            "empty": len(self.empty),
            "unsupported": len(self.unsupported),
            "empty_classes": sorted(self.empty_classes),
            "input_tables": self.input_sheets,
        }


def _chapter(title: str) -> tuple[tuple[int, ...], str]:
    match = _CHAPTER.match(title or "")
    if not match:
        return (), title
    return tuple(int(n) for n in match.group(1).split(".")), match.group(2)


def _cells(row: list[Cell]) -> list[Cell]:
    return [c for c in row if c not in (None, "")]


def _has(rows: list[list[Cell]], needles: tuple[str, ...]) -> bool:
    return any(isinstance(c, str) and any(n in c.lower() for n in needles) for row in rows for c in row)


def _is_number(value: Cell) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parse_table(sheet: str, rows: list[list[Cell]]) -> ResultTable | None:
    """A results table, or None when the sheet does not open with SCIA's settings lines."""
    chapter, title = _chapter(sheet)
    lines = [r for r in rows if _cells(r)]
    settings: dict[str, str] = {}
    messages: list[str] = []
    position = 0
    while position < len(lines) and len(_cells(lines[position])) == 1:
        text = str(_cells(lines[position])[0]).strip()
        setting = _SETTING.match(text)
        if _MESSAGE.match(text):
            messages.append(text)
        elif setting:
            settings[setting.group(1).strip()] = setting.group(2).strip()
        elif text.lower() not in ANALYSIS and settings:
            break  # a sub-heading after the settings ("Knoopreacties"): the table starts below it
        position += 1
    if not any(k.lower() in CLASS_KEYS for k in settings):
        return None
    while position < len(lines) and len(_cells(lines[position])) == 1:
        text = str(_cells(lines[position])[0]).strip()
        if _MESSAGE.match(text):
            messages.append(text)
        position += 1  # sub-headings
    columns = [str(c) if c is not None else "" for c in lines[position]] if position < len(lines) else []
    position += 1
    units: list[str] = []
    if position < len(lines) and all(isinstance(c, str) and c.startswith("[") for c in _cells(lines[position])):
        units = [str(c) for c in _cells(lines[position])]
        position += 1
    data = []
    for row in lines[position:]:
        first = str(_cells(row)[0]).lower()
        if any(word in " ".join(str(c).lower() for c in _cells(row)) for word in LEGEND) or first == "naam":
            break  # legend or the combination key below the table
        if any(_is_number(c) for c in row):
            data.append(row)
    return ResultTable(sheet, chapter, title, settings, columns, units, data, messages)


def _class_members(rows: list[list[Cell]]) -> tuple[str, list[str]] | None:
    """(class, its combinations as SCIA lists them) from a "Resultaatklasses - X" sheet.

    Header "Naam | Lijst", then the class in the first column and one combination per row in
    the second ("LLSM-ML TS1 01 - Lineair - BGT"); a class without combinations has only "Naam".
    """
    filled = [r for r in rows if _cells(r)]
    if len(filled) < 2 or str(_cells(filled[0])[0]).lower() not in ("naam", "name"):
        return None
    name = str(_cells(filled[1])[0])
    members = [str(r[1]).strip() for r in filled[1:] if len(r) > 1 and r[1] not in (None, "")]
    return name, members


def check_xlsx(path: Path) -> ReportCheck:
    """Classify every sheet; ok only when every results item holds data and none is unsupported."""
    try:
        sheets = read_sheets(Path(path))
    except Exception as exc:  # anything from a broken zip (BadZipFile) to an odd sheet layout
        return ReportCheck(False, problems=[f"The report's XLSX could not be read: {exc}"])
    sheets = sorted(sheets, key=lambda s: _chapter(s[0])[0])  # SCIA writes them in reverse order

    kinds = []
    for name, rows in sheets:
        if _has(rows, UNSUPPORTED):
            kinds.append((name, rows, "unsupported", None))
        elif (table := parse_table(name, rows)) is not None:
            kinds.append((name, rows, "table", table))
        elif len([r for r in rows if _cells(r)]) == 1 and _has(rows, EMPTY):
            kinds.append((name, rows, "empty", None))
        else:
            kinds.append((name, rows, "other", None))
    results_chapters = {_chapter(name)[0][:1] for name, _, kind, _ in kinds if kind in ("table", "unsupported")}
    classes = {}  # chapter -> (class, its combinations), from the class sheets in results chapters
    for name, rows, kind, _ in kinds:
        number, title = _chapter(name)
        if kind == "other" and number[:1] in results_chapters and title.lower().startswith(CLASS_LIST_TITLES):
            members = _class_members(rows)
            if members:
                classes[number] = members

    def class_of(number: tuple[int, ...]) -> tuple[str, list[str]] | None:
        """The class sheet a results item sits under: its nearest parent chapter that is one."""
        return next((classes[number[:n]] for n in range(len(number) - 1, 0, -1) if number[:n] in classes), None)

    check = ReportCheck(False)
    check.class_members = {name: members for name, members in classes.values()}
    for name, _rows, kind, table in kinds:
        number = _chapter(name)[0]
        in_results = number[:1] in results_chapters
        listing = class_of(number) if in_results else None
        without_combinations = listing is not None and not listing[1]
        if kind == "unsupported":
            check.expected += 1
            check.unsupported.append(name)
        elif kind == "table":
            check.expected += 1
            check.tables.append(table)
            if table.rows:
                check.written += 1
                check.messages += [f"{name}: {m}" for m in table.messages]
            elif without_combinations and listing[0] == table.result_class:
                check.expected -= 1  # nothing to print: the class has no combinations
                check.empty_classes.setdefault(listing[0], []).append(name)
            else:
                check.empty.append(name)
        elif kind == "empty" and in_results and without_combinations:
            check.empty_classes.setdefault(listing[0], []).append(name)
        elif kind == "empty" and in_results:
            check.expected += 1
            check.empty.append(name)
        elif not in_results:
            check.input_sheets += 1

    if check.unsupported:
        check.problems.append(
            f"{len(check.unsupported)} results items are of SCIA's old type and print no results "
            f"(\"Geen ondersteunde taak\"): {', '.join(check.unsupported[:5])}"
            + (" and more" if len(check.unsupported) > 5 else "")
        )
    if check.empty:
        check.problems.append(f"{len(check.empty)} results tables are empty: {', '.join(check.empty[:5])}"
                              + (" and more" if len(check.empty) > 5 else ""))
        said = sorted({m for t in check.tables if t.sheet in check.empty for m in t.messages})
        if said:
            check.problems.append(f"SCIA printed: {'; '.join(said[:3])}. If that says items are not calculated, "
                                  "print again with calculate, or calculate in SCIA and save.")
    if check.expected == 0:
        check.problems.append("The report has no results chapters: it prints input only, or the model has no results.")
    check.ok = not check.problems
    return check
