"""Read the cell values of an .xlsx with the standard library only (no openpyxl on the venv).

Only what SCIA's engineering-report export needs: sheet titles in workbook order, shared and
inline strings, numbers. Formulas, styles and dates are not interpreted.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
_COLUMN = re.compile(r"([A-Z]+)")

Cell = str | float | int | None


def _column_index(ref: str) -> int:
    letters = _COLUMN.match(ref).group(1)
    index = 0
    for char in letters:
        index = index * 26 + ord(char) - 64
    return index - 1


def _text(node: ET.Element) -> str:
    return "".join(t.text or "" for t in node.iter(f"{{{_NS['m']}}}t"))


def _number(text: str) -> float | int:
    value = float(text)
    return int(value) if value.is_integer() and "e" not in text.lower() and "." not in text else value


def read_sheets(path: Path) -> list[tuple[str, list[list[Cell]]]]:
    """(title, rows) per sheet, in workbook order; rows as lists of cell values."""
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = [_text(si) for si in root.findall("m:si", _NS)]
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {r.get("Id"): r.get("Target") for r in rels.findall("rel:Relationship", _NS)}
        sheets = []
        for sheet in workbook.findall("m:sheets/m:sheet", _NS):
            target = targets[sheet.get(f"{{{_NS['r']}}}id")].lstrip("/")
            target = target if target.startswith("xl/") else f"xl/{target}"
            sheets.append((sheet.get("name"), _rows(ET.fromstring(archive.read(target)), shared)))
    return sheets


def _rows(sheet: ET.Element, shared: list[str]) -> list[list[Cell]]:
    rows = []
    for row in sheet.iterfind("m:sheetData/m:row", _NS):
        values: list[Cell] = []
        for cell in row.findall("m:c", _NS):
            ref, kind = cell.get("r"), cell.get("t")
            if ref:
                while len(values) < _column_index(ref):
                    values.append(None)
            value_node = cell.find("m:v", _NS)
            if kind == "s" and value_node is not None:
                values.append(shared[int(value_node.text)])
            elif kind == "inlineStr":
                inline = cell.find("m:is", _NS)
                values.append(_text(inline) if inline is not None else "")
            elif kind in ("str", "e") and value_node is not None:
                values.append(value_node.text)
            elif value_node is not None and value_node.text is not None:
                try:
                    values.append(_number(value_node.text))
                except ValueError:
                    values.append(value_node.text)
            else:
                values.append(None)
        rows.append(values)
    return rows
