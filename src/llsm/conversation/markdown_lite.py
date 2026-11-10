"""Markdown-lite: chat answer text -> a list of blocks with inline spans.

Pure (no Tk), so the panel's renderer and the tests share one reading of an
answer. Covers what the model actually writes in the chat pane:

* blocks: paragraph, heading 1-3, bullet / numbered list (nested by
  indentation), fenced code (``` or ~~~, optional language), pipe table
  (header + delimiter row + rows), horizontal rule, blank;
* inline: **bold** / __bold__, *italic* / _italic_, `code`, [text](url).

Edge cases follow VibeModel's chat renderer: pipes without a delimiter row stay
a paragraph, nothing inside a code span is parsed, an unclosed fence runs to
the end, a lone backtick is just a character, and snake_case names
(bedding_preview) are not italic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------- inline


@dataclass(frozen=True)
class Span:
    """A run of text with its styles: any of "bold", "italic", "code"; links carry a url."""

    text: str
    styles: frozenset = frozenset()
    url: str = ""


_LINK = re.compile(r"\[([^\[\]\n]+)\]\(([^()\s]+)\)")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__")
# A single * or _ that opens and closes around non-space text. The underscore
# form must sit on word boundaries so snake_case names stay plain.
_ITALIC = re.compile(r"(?<![*\w])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![*\w])|(?<![_\w])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![_\w])")


def _code_spans(text: str):
    """Yield (start, end, inner) for each backtick code span; unmatched runs stay text."""
    i = 0
    while i < len(text):
        if text[i] != "`":
            i += 1
            continue
        j = i
        while j < len(text) and text[j] == "`":
            j += 1
        run = text[i:j]
        # The closing run must have exactly the same length.
        k = j
        close = -1
        while k < len(text):
            if text[k] == "`":
                m = k
                while m < len(text) and text[m] == "`":
                    m += 1
                if m - k == len(run):
                    close = k
                    break
                k = m
            else:
                k += 1
        if close < 0:
            i = j  # a lone backtick (or run) is a literal; keep scanning after it
            continue
        inner = text[j:close]
        if inner.strip() and inner.startswith(" ") and inner.endswith(" "):
            inner = inner[1:-1]
        yield i, close + len(run), inner
        i = close + len(run)


def _emphasis(text: str, styles: frozenset) -> list[Span]:
    """Links, bold and italic in text that holds no code spans."""
    if not text:
        return []
    best = None
    for kind, pat in (("link", _LINK), ("bold", _BOLD), ("italic", _ITALIC)):
        m = pat.search(text)
        if m and (best is None or m.start() < best[1].start()):
            best = (kind, m)
    if best is None:
        return [Span(text, styles)]
    kind, m = best
    out = _emphasis(text[: m.start()], styles)
    if kind == "link":
        out.append(Span(m.group(1), styles, url=m.group(2)))
    else:
        inner = m.group(1) if m.group(1) is not None else m.group(2)
        out.extend(_emphasis(inner, styles | {kind}))
    out.extend(_emphasis(text[m.end():], styles))
    return out


def parse_inline(text: str) -> list[Span]:
    """One line of text -> spans. Code spans first, so `**x**` stays code."""
    spans: list[Span] = []
    pos = 0
    for start, end, inner in _code_spans(text):
        spans.extend(_emphasis(text[pos:start], frozenset()))
        spans.append(Span(inner, frozenset({"code"})))
        pos = end
    spans.extend(_emphasis(text[pos:], frozenset()))
    return _merge(spans)


def _merge(spans: list[Span]) -> list[Span]:
    out: list[Span] = []
    for s in spans:
        if not s.text:
            continue
        if out and out[-1].styles == s.styles and not out[-1].url and not s.url:
            out[-1] = Span(out[-1].text + s.text, s.styles)
        else:
            out.append(s)
    return out


def plain_text(spans) -> str:
    """Spans -> the text a reader sees (markers gone, a link keeps its text)."""
    return "".join(s.text for s in spans)


# ---------------------------------------------------------------- blocks


@dataclass(frozen=True)
class Paragraph:
    lines: tuple  # tuple[tuple[Span, ...], ...]; line breaks in the source are kept


@dataclass(frozen=True)
class Heading:
    level: int
    spans: tuple


@dataclass(frozen=True)
class ListItem:
    level: int          # 0 = top level
    marker: str         # "•" for bullets, "3." for numbered items
    ordered: bool
    lines: tuple        # tuple[tuple[Span, ...], ...]


@dataclass(frozen=True)
class ListBlock:
    items: tuple


@dataclass(frozen=True)
class CodeBlock:
    lang: str
    lines: tuple        # raw text lines
    closed: bool = True


@dataclass(frozen=True)
class Table:
    header: tuple       # cell texts (inline markers removed)
    align: tuple        # "left" / "right" / "center" / None per column
    rows: tuple         # tuple of tuples of cell texts, padded to the header width


@dataclass(frozen=True)
class Rule:
    pass


@dataclass(frozen=True)
class Blank:
    pass


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*([^`\s]*)")
_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_RULE = re.compile(r"^ {0,3}([-*_])(\s*\1){2,}\s*$")
_BULLET = re.compile(r"^(\s*)([-*+•])\s+(.*)$")
_NUMBERED = re.compile(r"^(\s*)(\d{1,9})([.)])\s+(.*)$")
_DELIM_CELL = re.compile(r"^:?-+:?$")


def _split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, cur, i = [], [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur.append("|")
            i += 2
            continue
        if s[i] == "|":
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(s[i])
        i += 1
    cells.append("".join(cur).strip())
    return cells


def _delimiter_align(line: str):
    """The alignments of a delimiter row (`|:--|--:|`), or None when it is not one."""
    if "|" not in line or "-" not in line:
        return None
    cells = _split_row(line)
    if not cells or not all(_DELIM_CELL.match(c.replace(" ", "")) for c in cells):
        return None
    out = []
    for c in cells:
        c = c.replace(" ", "")
        if c.startswith(":") and c.endswith(":"):
            out.append("center")
        elif c.endswith(":"):
            out.append("right")
        elif c.startswith(":"):
            out.append("left")
        else:
            out.append(None)
    return out


def _cell_text(cell: str) -> str:
    return plain_text(parse_inline(cell)).strip()


def _is_table_start(lines: list[str], i: int) -> bool:
    """A pipe line followed by a delimiter row with as many cells. Pipes alone are text."""
    if i + 1 >= len(lines) or "|" not in lines[i]:
        return False
    align = _delimiter_align(lines[i + 1])
    return align is not None and len(align) == len(_split_row(lines[i]))


def _indent_width(ws: str) -> int:
    return len(ws.replace("\t", "    "))


def _starts_block(lines: list[str], i: int) -> bool:
    line = lines[i]
    return bool(
        not line.strip()
        or _FENCE.match(line)
        or _HEADING.match(line)
        or _RULE.match(line)
        or _BULLET.match(line)
        or _NUMBERED.match(line)
        or _is_table_start(lines, i)
    )


def parse(text: str) -> list:
    """Answer text -> blocks (Paragraph, Heading, ListBlock, CodeBlock, Table, Rule, Blank)."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            while i < len(lines) and not lines[i].strip():
                i += 1
            if blocks:  # leading blank lines carry nothing
                blocks.append(Blank())
            continue
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)
            lang = fence.group(2)
            body = []
            i += 1
            closed = False
            while i < len(lines):
                s = lines[i].strip()
                if s.startswith(marker[0] * len(marker)) and not s.strip(marker[0]):
                    closed = True
                    i += 1
                    break
                body.append(lines[i])
                i += 1
            if not closed:
                while body and not body[-1].strip():
                    body.pop()
            blocks.append(CodeBlock(lang, tuple(body), closed))
            continue
        heading = _HEADING.match(line)
        if heading:
            level = min(len(heading.group(1)), 3)
            blocks.append(Heading(level, tuple(parse_inline(heading.group(2)))))
            i += 1
            continue
        if _RULE.match(line):
            blocks.append(Rule())
            i += 1
            continue
        if _is_table_start(lines, i):
            header = [_cell_text(c) for c in _split_row(line)]
            align = _delimiter_align(lines[i + 1])
            width = len(header)
            rows = []
            i += 2
            while i < len(lines) and lines[i].strip() and "|" in lines[i] and not _FENCE.match(lines[i]):
                cells = [_cell_text(c) for c in _split_row(lines[i])][:width]
                rows.append(tuple(cells + [""] * (width - len(cells))))
                i += 1
            blocks.append(Table(tuple(header), tuple(align), tuple(rows)))
            continue
        if _BULLET.match(line) or _NUMBERED.match(line):
            items: list[ListItem] = []
            indents: list[int] = []  # indentation of each open nesting level
            while i < len(lines):
                cur = lines[i]
                b = _BULLET.match(cur)
                n = _NUMBERED.match(cur) if not b else None
                if b or n:
                    ws = (b or n).group(1)
                    width_ = _indent_width(ws)
                    while indents and width_ < indents[-1]:
                        indents.pop()
                    if not indents or width_ > indents[-1]:
                        indents.append(width_)
                    level = len(indents) - 1
                    if b:
                        items.append(ListItem(level, "•", False, (tuple(parse_inline(b.group(3))),)))
                    else:
                        items.append(ListItem(level, n.group(2) + ".", True, (tuple(parse_inline(n.group(4))),)))
                    i += 1
                    continue
                # A non-blank line that is no new block continues the last item.
                if cur.strip() and items and not _starts_block(lines, i):
                    last = items[-1]
                    items[-1] = ListItem(last.level, last.marker, last.ordered,
                                         last.lines + (tuple(parse_inline(cur.strip())),))
                    i += 1
                    continue
                # A blank line followed by another item keeps the list going.
                if not cur.strip() and i + 1 < len(lines) and (_BULLET.match(lines[i + 1]) or _NUMBERED.match(lines[i + 1])):
                    i += 1
                    continue
                break
            blocks.append(ListBlock(tuple(items)))
            continue
        para = []
        while i < len(lines) and lines[i].strip():
            if para and _starts_block(lines, i):
                break
            para.append(tuple(parse_inline(lines[i].strip())))
            i += 1
        blocks.append(Paragraph(tuple(para)))
    while blocks and isinstance(blocks[-1], Blank):
        blocks.pop()
    return blocks
