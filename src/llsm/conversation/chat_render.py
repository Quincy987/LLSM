"""Draw markdown-lite blocks and the per-turn activity block into the chat Text widget.

Everything is text in the one Tk `Text` widget, styled with tags, so selecting
and copying keeps working across paragraphs, lists and tables alike:

* Tables are laid out with per-table tab stops (left, right or centre per
  column) on cells that are word-wrapped beforehand to the fitted column
  widths. A row's fill runs to the table's right edge (the line's newline is
  tagged, the right margin stops it). Embedded Frame/Label grids would give
  borders, but their text can't be selected, the wheel stops scrolling over
  them and dozens of widgets per table get slow; tabs keep one widget.
  Copying a selection drops the leading tab of each table line, so a copied
  range pastes into Excel as cells too.
* Regions (tables, long code blocks, the activity block) sit between two marks
  and are redrawn in place: a table on resize or unfold, a code block on
  unfold, the activity block every second while a turn runs.
"""
from __future__ import annotations

import time
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass, field
from typing import Callable

from . import table_layout as tl
from .activity import TEXTS as ACT_TEXTS
from .activity import ActivityLog, format_clock, format_duration
from .markdown_lite import (
    Blank, CodeBlock, Heading, ListBlock, Paragraph, Rule, Span, Table, parse,
)

TEXTS = {
    "more_rows": "… nog {n} rijen",
    "more_row": "… nog 1 rij",
    "copy": "Kopiëren",
    "copied": "Gekopieerd",
    "more_lines": "… nog {n} regels",
    "more_line": "… nog 1 regel",
}

# Code blocks over CODE_FOLD_OVER lines show CODE_SHOWN until unfolded.
CODE_FOLD_OVER = 20
CODE_SHOWN = 12

BODY_FONT = ("Segoe UI", 10)
MONO_FONT = ("Consolas", 9)
LIST_INDENT = 18      # px per nesting level
LIST_MARKER = 20      # px from the marker to the item text
TABLE_LEFT = 4        # px from the text edge to the table
PAD = " "        # thin space: padding around inline code, never copied

_BULLETS = ("•", "◦", "▪")


@dataclass
class _Region:
    start: str
    end: str
    draw: Callable[["_Region"], None]
    kind: str
    state: dict = field(default_factory=dict)


class ChatRenderer:
    """Owns the markdown tags of one chat Text widget and the regions drawn into it."""

    def __init__(self, text: tk.Text, *, on_scroll_end: Callable[[], None] | None = None):
        self.t = text
        self._regions: list[_Region] = []
        self._seq = 0
        self._colors: dict = {}
        self._measure_cache: dict = {}
        self._width_seen = 0
        self._refit_after = None
        self._fonts = {
            False: tkfont.Font(root=text, family=BODY_FONT[0], size=BODY_FONT[1]),
            True: tkfont.Font(root=text, family=BODY_FONT[0], size=BODY_FONT[1], weight="bold"),
        }
        self.t.mark_set("md_ins", "end-1c")
        self.t.mark_gravity("md_ins", "right")
        self.t.bind("<Configure>", self._on_configure, add="+")
        self.t.bind("<<Copy>>", self._on_copy)

    # ------------------------------------------------------------ theme

    def configure(self, c: dict, ref: dict) -> None:
        """(Re)configure every markdown tag from the panel's theme colours."""
        self._colors = dict(c)
        tc = self.t.tag_configure
        tint = c.get("system_bg", "#eeeeee")
        link = c.get("link_fg", ref.get("running", "#1e6091"))
        tc("md_bold", font=("Segoe UI", 10, "bold"))
        tc("md_italic", font=("Segoe UI", 10, "italic"))
        tc("md_bolditalic", font=("Segoe UI", 10, "bold italic"))
        tc("md_code", font=("Consolas", 9), background=c.get("code_bg", tint))
        tc("md_pad", font=("Consolas", 9))
        tc("md_link", underline=True, foreground=link)
        tc("md_url", foreground=c.get("muted", "#888888"), font=("Segoe UI", 9))
        tc("md_h1", font=("Segoe UI", 12, "bold"), spacing1=6, spacing3=2)
        tc("md_h2", font=("Segoe UI", 11, "bold"), spacing1=5, spacing3=2)
        tc("md_h3", font=("Segoe UI", 10, "bold"), spacing1=4, spacing3=1)
        tc("md_gap", font=("Segoe UI", 3), spacing1=0, spacing3=0)
        tc("md_bullet", foreground=c.get("muted", "#888888"))
        for level in range(len(_BULLETS)):
            left = 4 + level * LIST_INDENT
            tc(f"md_li{level}", lmargin1=left, lmargin2=left + LIST_MARKER, tabs=(left + LIST_MARKER,))
            tc(f"md_li{level}c", lmargin1=left + LIST_MARKER, lmargin2=left + LIST_MARKER)
        tc("md_codeblock", font=MONO_FONT, background=c.get("code_bg", tint),
           lmargin1=12, lmargin2=12, rmargin=6, spacing1=0, spacing3=0,
           relief="solid", borderwidth=1, wrap="char")
        tc("md_codepad", font=("Consolas", 3), background=c.get("code_bg", tint),
           lmargin1=12, lmargin2=12, rmargin=6, spacing1=0, spacing3=0,
           relief="solid", borderwidth=1)
        tc("md_hr", font=("Segoe UI", 1), background=c.get("separator", "#cccccc"),
           spacing1=0, spacing3=0, lmargin1=4, rmargin=4)
        tc("md_th", font=("Segoe UI", 10, "bold"), background=c.get("table_head_bg", tint))
        tc("md_tr0", background=c.get("bg", "#ffffff"))
        tc("md_tr1", background=c.get("table_alt_bg", tint))
        tc("md_rowtop", spacing1=3)
        tc("md_rowbot", spacing3=3)
        tc("md_tbllinks", font=("Segoe UI", 9), spacing1=3, spacing3=2)
        tc("md_action", foreground=link, underline=True)
        tc("md_act", font=("Segoe UI", 9), foreground=c.get("muted", "#888888"),
           lmargin1=8, lmargin2=26, spacing1=1, spacing3=1)
        tc("md_act_head", font=("Segoe UI Semibold", 9), foreground=c.get("muted", "#888888"))
        tc("md_act_ok", foreground=c.get("ok_fg", "#2e7d32"))
        tc("md_act_fail", foreground=c.get("error_fg", "#c62828"))
        tc("md_act_run", foreground=link)
        tc("md_act_err", foreground=c.get("error_fg", "#c62828"))
        tc("md_act_quiet", foreground=ref.get("thinking", "#d29922"), font=("Segoe UI", 9, "italic"))
        tc("md_act_args", foreground=c.get("separator", "#bbbbbb"))
        self.t.tag_raise("sel")

    # ------------------------------------------------------------ plumbing

    def _ins(self, text: str, tags) -> None:
        self.t.insert("md_ins", text, tuple(tags))

    def _new_tag(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}{self._seq}"

    def _bind_action(self, tag: str, callback: Callable[[], None]) -> None:
        self.t.tag_bind(tag, "<Button-1>", lambda e: (callback(), "break")[1])
        self.t.tag_bind(tag, "<Enter>", lambda e: self.t.configure(cursor="hand2"))
        self.t.tag_bind(tag, "<Leave>", lambda e: self.t.configure(cursor=""))

    def _region(self, kind: str, draw: Callable[[_Region], None], state: dict | None = None) -> _Region:
        """Draw a region at md_ins and remember its marks. The caller adds its final newline."""
        n = self._new_tag("")
        reg = _Region(f"rg{n}s", f"rg{n}e", draw, kind, state or {})
        self.t.mark_set(reg.start, "md_ins")
        self.t.mark_gravity(reg.start, "left")
        draw(reg)
        self.t.mark_set(reg.end, "md_ins")
        self.t.mark_gravity(reg.end, "left")
        self._regions.append(reg)
        return reg

    def _alive(self, reg: _Region) -> bool:
        return reg.start in self.t.mark_names() and reg.end in self.t.mark_names()

    def redraw(self, reg: _Region) -> None:
        """Draw a region again in place, keeping the view pinned to the end if it was."""
        if not self._alive(reg):
            return
        at_end = self.t.yview()[1] >= 0.999
        state = str(self.t.cget("state"))
        self.t.configure(state="normal")
        try:
            self.t.delete(reg.start, reg.end)
            self.t.mark_set("md_ins", reg.start)
            reg.draw(reg)
            self.t.mark_set(reg.end, "md_ins")
        finally:
            self.t.mark_set("md_ins", "end-1c")
            self.t.configure(state=state)
        if at_end:
            self.t.see("end")

    def remove(self, reg: _Region, *, trailing: int = 1) -> None:
        """Delete a region and the `trailing` characters after it (its newline)."""
        if not self._alive(reg):
            return
        state = str(self.t.cget("state"))
        self.t.configure(state="normal")
        try:
            self.t.delete(reg.start, f"{reg.end} + {trailing} chars")
        finally:
            self.t.configure(state=state)
        self.t.mark_unset(reg.start, reg.end)
        if reg in self._regions:
            self._regions.remove(reg)

    def reset(self) -> None:
        """The chat was cleared: forget every region."""
        self._regions = []
        try:
            self.t.mark_set("md_ins", "end-1c")
        except tk.TclError:
            pass

    def measure(self, text: str, bold: bool = False) -> float:
        key = (text, bold)
        if key not in self._measure_cache:
            if len(self._measure_cache) > 20000:
                self._measure_cache.clear()
            self._measure_cache[key] = self._fonts[bold].measure(text)
        return self._measure_cache[key]

    def available_width(self) -> int:
        """Room for a table: the widget's inner width minus padding and margins."""
        w = self.t.winfo_width()
        if w <= 1:
            return tl.ASSUMED_WIDTH
        try:
            inner = int(self.t.cget("padx")) * 2 + int(self.t.cget("borderwidth")) * 2 + int(self.t.cget("highlightthickness")) * 2
        except (tk.TclError, ValueError):
            inner = 24
        return max(120, w - inner - TABLE_LEFT - 8)

    def _on_configure(self, _event=None):
        width = self.t.winfo_width()
        if abs(width - self._width_seen) < 3:
            return
        self._width_seen = width
        if self._refit_after is not None:
            try:
                self.t.after_cancel(self._refit_after)
            except tk.TclError:
                pass
        self._refit_after = self.t.after(150, self.refit)

    def refit(self) -> None:
        """Lay every table out again for the current pane width."""
        self._refit_after = None
        for reg in [r for r in self._regions if r.kind == "table"]:
            if self._alive(reg):
                self.redraw(reg)
            else:
                self._regions.remove(reg)

    def _on_copy(self, _event=None):
        """Copy the selection without the padding characters (md_pad)."""
        try:
            first, last = self.t.index("sel.first"), self.t.index("sel.last")
        except tk.TclError:
            return "break"
        pad = "md_pad" in self.t.tag_names(first)
        out = []
        for key, value, _index in self.t.dump(first, last, text=True, tag=True):
            if key == "tagon" and value == "md_pad":
                pad = True
            elif key == "tagoff" and value == "md_pad":
                pad = False
            elif key == "text" and not pad:
                out.append(value)
        self.t.clipboard_clear()
        self.t.clipboard_append("".join(out))
        return "break"

    # ------------------------------------------------------------ blocks

    def render(self, body: str, base_tag: str) -> None:
        """Append a message body. The widget must be in state normal."""
        self.t.mark_set("md_ins", "end-1c")
        base = (base_tag,)
        blocks = [b for b in parse(body) if not isinstance(b, Blank)]
        if not blocks:
            self._ins("\n", base)
            return
        for n, block in enumerate(blocks):
            if n:
                self._ins("\n", base + ("md_gap",))
            if isinstance(block, Paragraph):
                for line in block.lines:
                    self._spans(line, base)
                    self._ins("\n", base)
            elif isinstance(block, Heading):
                tags = base + (f"md_h{block.level}",)
                self._spans(block.spans, tags)
                self._ins("\n", tags)
            elif isinstance(block, ListBlock):
                self._list(block, base)
            elif isinstance(block, CodeBlock):
                self._code(block, base)
            elif isinstance(block, Table):
                self._table(block, base)
            elif isinstance(block, Rule):
                self._ins("\n", base + ("md_hr",))
        self.t.mark_set("md_ins", "end-1c")

    def _spans(self, spans, tags) -> None:
        for span in spans:
            self._span(span, tags)

    def _span(self, span: Span, tags) -> None:
        styles = span.styles
        if "code" in styles:
            self._ins(PAD, tags + ("md_code", "md_pad"))
            self._ins(span.text, tags + ("md_code",))
            self._ins(PAD, tags + ("md_code", "md_pad"))
            return
        extra = ()
        if "bold" in styles and "italic" in styles:
            extra = ("md_bolditalic",)
        elif "bold" in styles:
            extra = ("md_bold",)
        elif "italic" in styles:
            extra = ("md_italic",)
        if span.url:
            self._ins(span.text, tags + extra + ("md_link",))
            if span.url != span.text:
                self._ins(f" ({span.url})", tags + ("md_url",))
            return
        self._ins(span.text, tags + extra)

    def _list(self, block: ListBlock, base) -> None:
        for item in block.items:
            level = min(item.level, len(_BULLETS) - 1)
            marker = item.marker if item.ordered else _BULLETS[level]
            tags = base + (f"md_li{level}",)
            self._ins(marker, tags + ("md_bullet",))
            self._ins("\t", tags)
            for k, line in enumerate(item.lines):
                if k:
                    tags = base + (f"md_li{level}c",)
                self._spans(line, tags)
                self._ins("\n", tags)

    def _code(self, block: CodeBlock, base) -> None:
        tags = base + ("md_codeblock",)
        lines = list(block.lines) or [""]
        self._ins("\n", base + ("md_codepad",))

        def draw(reg: _Region) -> None:
            shown = lines
            hidden = 0
            if len(lines) > CODE_FOLD_OVER and not reg.state.get("open"):
                shown, hidden = lines[:CODE_SHOWN], len(lines) - CODE_SHOWN
            self._ins("\n".join(shown), tags)
            if hidden:
                label = TEXTS["more_line"] if hidden == 1 else TEXTS["more_lines"].format(n=hidden)
                self._ins("\n", tags)
                self._ins(label, tags + ("md_action", reg.state["tag"]))

        if len(lines) > CODE_FOLD_OVER:
            state = {"tag": self._new_tag("md_unfold"), "open": False}
            reg = self._region("code", draw, state)
            self._bind_action(state["tag"], lambda: self._open(reg))
        else:
            draw(_Region("", "", draw, "code"))
        self._ins("\n", tags)
        self._ins("\n", base + ("md_codepad",))

    def _open(self, reg: _Region) -> None:
        reg.state["open"] = True
        self.redraw(reg)

    # ------------------------------------------------------------ tables

    def _table(self, block: Table, base) -> None:
        header = list(block.header)
        rows = [list(r) for r in block.rows]
        align = tl.column_alignments(header, block.align, rows)
        numeric = [tl.is_numeric_column(r[c] for r in rows) for c in range(len(header))]
        state = {
            "header": header, "rows": rows, "align": align, "numeric": numeric, "open": False,
            "tag": self._new_tag("md_tbl"), "more": self._new_tag("md_more"), "copy": self._new_tag("md_copy"),
            "base": base,
        }
        reg = self._region("table", self._draw_table, state)
        self._bind_action(state["more"], lambda: self._open(reg))
        self._bind_action(state["copy"], lambda: self._copy_table(reg))
        self._ins("\n", base + ("md_tbllinks",))

    def _draw_table(self, reg: _Region) -> None:
        s = reg.state
        header, rows, align, base = s["header"], s["rows"], s["align"], s["base"]
        needs = tl.measure_columns(header, rows, s["numeric"], self.measure)
        avail = self.available_width()
        widths = tl.fit(needs, avail)
        half = tl.CELL_CHROME // 2
        stops, x = [], TABLE_LEFT
        for c, w in enumerate(widths):
            a = align[c]
            if a == "right":
                stops += [x + w - half, "right"]
            elif a == "center":
                stops += [x + w // 2, "center"]
            else:
                stops += [x + half, "left"]
            x += w
        table_w = x
        try:
            pane = self.t.winfo_width() - int(self.t.cget("padx")) * 2 - 4
        except (tk.TclError, ValueError):
            pane = avail
        self.t.tag_configure(s["tag"], tabs=tuple(stops), lmargin1=TABLE_LEFT,
                             lmargin2=TABLE_LEFT, rmargin=max(0, pane - table_w),
                             # Tk fills the margins with the row colour unless told otherwise.
                             lmargincolor=self._colors.get("bg", "#ffffff"),
                             rmargincolor=self._colors.get("bg", "#ffffff"))
        self.t.tag_raise("sel")

        hidden = 0 if s["open"] else tl.hidden_row_count(len(rows))
        shown = rows[: len(rows) - hidden]

        def row(cells, row_tags, bold):
            wrapped = [tl.wrap_cell(cells[c] if c < len(cells) else "", max(8, widths[c] - tl.CELL_CHROME),
                                    self.measure, bold) for c in range(len(widths))]
            height = max(len(w) for w in wrapped)
            for k in range(height):
                tags = base + (s["tag"],) + row_tags
                if k == 0:
                    tags += ("md_rowtop",)
                if k == height - 1:
                    tags += ("md_rowbot",)
                self._ins("\t", tags + ("md_pad",))
                self._ins("\t".join(w[k] if k < len(w) else "" for w in wrapped), tags)
                self._ins("\n", tags)

        row(header, ("md_th",), True)
        for n, cells in enumerate(shown):
            row(cells, ("md_tr1" if n % 2 else "md_tr0",), False)
        links = base + ("md_tbllinks",)
        if hidden:
            label = TEXTS["more_row"] if hidden == 1 else TEXTS["more_rows"].format(n=hidden)
            self._ins(label, links + ("md_action", s["more"]))
            self._ins("    ", links)
        self._ins(s.get("copy_label", TEXTS["copy"]), links + ("md_action", s["copy"]))

    def table_tsv(self, reg: _Region) -> str:
        return tl.to_tsv(reg.state["header"], reg.state["rows"])

    def _copy_table(self, reg: _Region) -> None:
        self.t.clipboard_clear()
        # Tk turns LF into CRLF on the Windows clipboard; CRLF in would become CR CR LF.
        self.t.clipboard_append(self.table_tsv(reg).replace("\r\n", "\n"))
        reg.state["copy_label"] = TEXTS["copied"]
        self.redraw(reg)

        def back():
            reg.state["copy_label"] = TEXTS["copy"]
            self.redraw(reg)
        self.t.after(1500, back)

    def tables(self) -> list[_Region]:
        return [r for r in self._regions if r.kind == "table" and self._alive(r)]

    # ------------------------------------------------------------ activity block

    def start_activity(self) -> "ActivityBlock":
        """Open an activity block at the end of the chat for the turn that just started."""
        state = str(self.t.cget("state"))
        self.t.configure(state="normal")
        try:
            self.t.mark_set("md_ins", "end-1c")
            block = ActivityBlock(self)
            block.region = self._region("activity", block.draw)
            self._ins("\n", ("md_act",))
            self.t.mark_set("md_ins", "end-1c")
        finally:
            self.t.configure(state=state)
        self.t.see("end")
        return block


class ActivityBlock:
    """The live list of tool steps above an answer; collapses when the turn ends."""

    def __init__(self, renderer: ChatRenderer):
        self.r = renderer
        self.log = ActivityLog()
        self.region: _Region | None = None
        self.expanded = False
        self.toggle_tag = renderer._new_tag("md_acttoggle")
        renderer._bind_action(self.toggle_tag, self.toggle)

    def alive(self) -> bool:
        return self.region is not None and self.r._alive(self.region)

    def draw(self, _reg: _Region) -> None:
        ins = self.r._ins
        log = self.log
        now = time.monotonic()
        if log.done:
            arrow = "▾ " if self.expanded else "▸ "
            ins(arrow + log.summary(now), ("md_act", "md_act_head", "md_action", self.toggle_tag))
            if not self.expanded:
                return
            steps, hidden = log.steps, 0
        else:
            running = any(s.ok is None for s in log.steps)
            label = ACT_TEXTS["busy"] if running else ACT_TEXTS["thinking"]
            ins(f"◐ {label}… {format_clock(log.elapsed(now))}", ("md_act", "md_act_head"))
            hidden, steps = log.visible()
            if hidden:
                ins("\n" + log.earlier_text(hidden), ("md_act",))
        for step in steps:
            ins("\n", ("md_act",))
            if step.ok is None and not log.done:
                ins("▶ ", ("md_act", "md_act_run"))
            elif step.ok:
                ins("✓ ", ("md_act", "md_act_ok"))
            else:
                ins("✗ ", ("md_act", "md_act_fail"))
            ins(step.name, ("md_act",))
            if step.args:
                args = step.args if len(step.args) <= 48 else step.args[:47] + "…"
                ins(f" ({args})", ("md_act", "md_act_args"))
            if step.ok is None and not log.done:
                ins(f" · {format_clock(step.duration(now))}", ("md_act",))
            else:
                ins(f" · {format_duration(step.duration(now))}", ("md_act",))
            if step.error:
                ins(" — " + step.error, ("md_act", "md_act_err"))
        if log.quiet(now):
            ins("\n" + ACT_TEXTS["quiet"], ("md_act", "md_act_quiet"))

    def refresh(self) -> None:
        if self.alive():
            self.r.redraw(self.region)

    def tool_start(self, name: str, args: str = "") -> None:
        self.log.start(name, args)
        self.refresh()

    def tool_end(self, name: str, ok: bool, error: str = "") -> None:
        self.log.end(name, ok, error)
        self.refresh()

    def finish(self) -> None:
        """Turn over: one summary line, or nothing at all when no tool ran."""
        self.log.finish()
        if not self.alive():
            return
        if not self.log.steps:
            self.r.remove(self.region)
            self.region = None
            return
        self.refresh()

    def toggle(self) -> None:
        if self.log.done:
            self.expanded = not self.expanded
            self.refresh()
