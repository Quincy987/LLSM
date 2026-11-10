"""Layout decisions for a markdown table in the narrow chat pane, on plain cell text.

Which columns are numeric (right-aligned), how wide each column gets, how a
cell wraps, how many rows show before "… nog N rijen", and the tab-separated
text the Kopiëren link puts on the clipboard. Tk-free: text widths come from a
`measure(text, bold) -> px` function the panel builds from its fonts.

A port of VibeModel's TableLayout (src/VibeModel/Markdown/TableLayout.cs), with
the units a structural engineer meets in SCIA added.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

Measure = Callable[[str, bool], float]

# Rows shown before the rest folds behind "… nog N rijen".
SHOWN_ROWS = 30

# Cell padding (6 + 6) plus a little slack.
CELL_CHROME = 14

# Before the pane reports its width, tables are laid out for a narrow pane.
ASSUMED_WIDTH = 520

_UNITS = sorted({
    "mm", "cm", "dm", "m", "km",
    "mm²", "mm2", "cm²", "cm2", "m²", "m2",
    "mm³", "mm3", "cm³", "m³", "m3",
    "mm⁴", "mm4", "cm⁴", "cm4", "m⁴", "m4",
    "g", "kg", "t", "N", "kN", "MN",
    "kN/m", "kN/m²", "kN/m2", "kN/m³", "kN/m3", "kNm", "kN·m", "kNm/m", "kN/m/m",
    "N/mm", "N/mm²", "N/mm2", "Pa", "kPa", "MPa", "GPa",
    "kg/m", "kg/m³", "kg/m3", "m/s", "mm/s", "Hz", "°", "°C", "%", "‰", "rad",
    "s", "min", "h",
}, key=len, reverse=True)

# "3000", "-1,5", "12 345,6 mm", "2,5 m²", "45°", "≈ 300 kg"; "1:50" and "3 wanden" are not.
_NUMBER_WITH_UNIT = re.compile(
    r"^[\-+−±~≈]?\s*\d[\d  ' .,]*(?:\s*(?:" + "|".join(re.escape(u) for u in _UNITS) + r"))?$"
)

_EMPTY_MARKERS = {"", "-", "–", "—", "n.v.t.", "n/a"}


def is_numeric_cell(cell: str) -> bool:
    return bool(_NUMBER_WITH_UNIT.match((cell or "").strip()))


def is_numeric_column(cells: Iterable[str]) -> bool:
    """True when every non-empty cell is a number with an optional unit (and at least one is)."""
    any_number = False
    for raw in cells:
        cell = (raw or "").strip()
        if cell.lower() in _EMPTY_MARKERS:
            continue
        if not is_numeric_cell(cell):
            return False
        any_number = True
    return any_number


def column_alignments(header: Sequence[str], align: Sequence, rows: Sequence[Sequence[str]]) -> list[str]:
    """Per column "left" / "right" / "center": an explicit `:--`/`--:` wins, else numbers go right."""
    out = []
    for c in range(len(header)):
        explicit = align[c] if c < len(align) else None
        if explicit:
            out.append(explicit)
        else:
            out.append("right" if is_numeric_column(r[c] if c < len(r) else "" for r in rows) else "left")
    return out


@dataclass
class ColumnNeed:
    """What a column needs (px, padding included)."""

    min: float      # its longest unbreakable piece: a word, or a whole numeric cell
    max: float      # its longest cell on one line
    numeric: bool = False


def measure_columns(header: Sequence[str], rows: Sequence[Sequence[str]], numeric: Sequence[bool],
                    measure: Measure) -> list[ColumnNeed]:
    """Measure each column: header (bold) and cells, words and whole lines."""
    needs = []
    for c in range(len(header)):
        cells = [(r[c] if c < len(r) else "").strip() for r in rows]
        head = (header[c] or "").strip()
        is_num = c < len(numeric) and numeric[c]
        pieces = [measure(t, False) for t in cells] if is_num else [measure(w, False) for t in cells for w in t.split(" ")]
        pieces += [measure(w, True) for w in head.split(" ")]
        min_px = max(pieces, default=0)
        max_px = max([measure(t, False) for t in cells] + [measure(head, True)], default=0)
        needs.append(ColumnNeed(
            min=math.ceil(min_px + CELL_CHROME),
            max=math.ceil(max(min_px, max_px) + CELL_CHROME),
            numeric=is_num,
        ))
    return needs


def fit(needs: Sequence[ColumnNeed], available: float) -> list[int]:
    """Column widths (px) for `available` — the auto table layout browsers use.

    Everything on one line when it fits (the table then stays narrower than the
    pane); otherwise every column keeps its longest word and the spare room goes
    to the columns that want more, in proportion; only when even the words do
    not fit do columns shrink below them — text columns first (a broken word
    reads better than a broken number): numeric columns keep their width while
    they take at most half the room.
    """
    n = len(needs)
    sum_min = sum(x.min for x in needs)
    sum_max = sum(x.max for x in needs)
    widths = []
    if available < sum_min:
        numeric_min = sum(x.min for x in needs if x.numeric)
        text_min = sum_min - numeric_min
        keep_numbers = numeric_min > 0 and text_min > 0 and numeric_min <= available / 2
        for x in needs:
            if keep_numbers:
                w = x.min if x.numeric else x.min * (available - numeric_min) / text_min
            else:
                w = x.min * available / sum_min if sum_min > 0 else available / n
            widths.append(math.floor(w))
        return widths
    for x in needs:
        if available >= sum_max:
            w = x.max
        else:
            w = x.min + ((available - sum_min) * (x.max - x.min) / (sum_max - sum_min) if sum_max > sum_min else 0)
        widths.append(math.floor(w))
    return widths


def wrap_cell(text: str, width: float, measure: Measure, bold: bool = False) -> list[str]:
    """Greedy word wrap of one cell into lines no wider than `width` px.

    A word wider than the column is broken between characters, so a cell never
    runs into its neighbour.
    """
    text = (text or "").strip()
    if not text:
        return [""]
    if measure(text, bold) <= width:
        return [text]
    lines: list[str] = []
    cur = ""
    for word in text.split(" "):
        trial = f"{cur} {word}" if cur else word
        if measure(trial, bold) <= width:
            cur = trial
            continue
        if cur:
            lines.append(cur)
            cur = ""
        while measure(word, bold) > width and len(word) > 1:
            k = len(word) - 1
            while k > 1 and measure(word[:k], bold) > width:
                k -= 1
            lines.append(word[:k])
            word = word[k:]
        cur = word
    if cur:
        lines.append(cur)
    return lines


def hidden_row_count(body_rows: int) -> int:
    """Rows beyond SHOWN_ROWS fold away; 0 when everything shows."""
    return body_rows - SHOWN_ROWS if body_rows > SHOWN_ROWS else 0


def _tsv_cell(cell: str) -> str:
    return re.sub(r"[\t\r\n]+", " ", cell or "").strip()


def to_tsv(header: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    """Tab-separated text (header first), one row per line — pastes into Excel as cells."""
    out = []
    if header:
        out.append("\t".join(_tsv_cell(c) for c in header))
    for row in rows:
        out.append("\t".join(_tsv_cell(c) for c in row))
    return "".join(line + "\r\n" for line in out)
