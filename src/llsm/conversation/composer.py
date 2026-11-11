"""The input bar: a rounded box around the text, with a round send button that
becomes a stop button while an answer is running.

Drawn on a Tk Canvas (no images). The Canvas only draws; the panel keeps every
key binding on the text (Enter sends, Shift+Enter newline, auto-grow,
placeholder). The Composer adds just two bindings of its own, both with
add="+": focus in/out for the border colour, and <Configure> on the text so the
box grows with it (auto-grow only changes the text's height option, which a
Text inside a Canvas does not pass on by itself).
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable

from . import theme

RADIUS = 10       # corner radius of the box
PAD_X = 10        # text inset from the box edge, left
PAD_Y = 7         # text inset from the box edge, top and bottom
BUTTON = 28       # send / stop button diameter
GAP = 8           # between the text and the button


def rounded_rect_points(x0: float, y0: float, x1: float, y1: float, r: float) -> list[float]:
    """Polygon points for a rounded rectangle, for create_polygon(..., smooth=True).

    Each straight edge's ends are doubled so the spline stays straight there and
    only bends at the corners. The list is closed (first point == last point).
    The radius is clamped to half the shorter side.
    """
    r = max(0.0, min(float(r), (x1 - x0) / 2, (y1 - y0) / 2))
    pts = [
        x0 + r, y0, x0 + r, y0,
        x1 - r, y0, x1 - r, y0,
        x1, y0,
        x1, y0 + r, x1, y0 + r,
        x1, y1 - r, x1, y1 - r,
        x1, y1,
        x1 - r, y1, x1 - r, y1,
        x0 + r, y1, x0 + r, y1,
        x0, y1,
        x0, y1 - r, x0, y1 - r,
        x0, y0 + r, x0, y0 + r,
        x0, y0,
    ]
    return pts + pts[:2]


def _arrow_points(cx: float, cy: float) -> list[float]:
    """An upward arrow, centred on (cx, cy), about 14 px tall."""
    return [cx, cy - 7, cx + 6, cy - 1, cx + 2, cy - 1, cx + 2, cy + 7,
            cx - 2, cy + 7, cx - 2, cy - 1, cx - 6, cy - 1]


class Composer(tk.Frame):
    """Rounded input box + send/stop button. `colors` is theme.derive(palette)."""

    def __init__(self, parent, *, on_send: Callable[[], None], on_cancel: Callable[[], None],
                 colors: dict, lines: int = 4):
        super().__init__(parent, bd=0, highlightthickness=0, background=colors["chrome_bg"])
        self._on_send = on_send
        self._on_cancel = on_cancel
        self._colors = dict(colors)
        self._busy = False
        self._enabled = True
        self._focused = False
        self._hover = False

        self.canvas = tk.Canvas(self, bd=0, highlightthickness=0, background=colors["chrome_bg"],
                                height=1)
        self.canvas.pack(fill=tk.X, expand=True)
        self.text = tk.Text(self.canvas, height=lines, wrap="word", font=theme.BODY_FONT,
                            relief="flat", bd=0, highlightthickness=0, padx=0, pady=0)
        # No height on the window item: it follows the text's requested height.
        self._win = self.canvas.create_window(PAD_X, PAD_Y, window=self.text, anchor="nw")
        self._box = self.canvas.create_polygon(0, 0, 0, 0, smooth=True, width=1)
        self.canvas.tag_lower(self._box)
        self._button = self.canvas.create_oval(0, 0, 0, 0, width=0, tags=("button",))
        self._arrow = self.canvas.create_polygon(0, 0, 0, 0, width=0, tags=("button",))
        self._stop = self.canvas.create_rectangle(0, 0, 0, 0, width=0, tags=("button",))

        self.canvas.bind("<Configure>", lambda e: self._layout())
        self.text.bind("<Configure>", lambda e: self._layout(), add="+")
        self.text.bind("<FocusIn>", lambda e: self._set_focus(True), add="+")
        self.text.bind("<FocusOut>", lambda e: self._set_focus(False), add="+")
        self.canvas.tag_bind("button", "<Enter>", lambda e: self._set_hover(True))
        self.canvas.tag_bind("button", "<Leave>", lambda e: self._set_hover(False))
        self.canvas.tag_bind("button", "<Button-1>", lambda e: self._click())
        # Clicking the box's padding puts the cursor in the text.
        self.canvas.bind("<Button-1>", lambda e: self.text.focus_set(), add="+")

    # ------------------------------------------------------------ state

    def set_busy(self, busy: bool) -> None:
        """Show the stop button while an answer runs, the send button otherwise."""
        self._busy = bool(busy)
        self._paint()

    def set_enabled(self, enabled: bool) -> None:
        """A greyed send button when there's nothing to send."""
        self._enabled = bool(enabled)
        self._paint()

    def apply_colors(self, colors: dict) -> None:
        self._colors = dict(colors)
        for w in (self, self.canvas):
            w.configure(background=colors["chrome_bg"])
        self._paint()

    @property
    def busy(self) -> bool:
        return self._busy

    # ------------------------------------------------------------ drawing

    def _layout(self) -> None:
        width = self.canvas.winfo_width()
        if width <= 1:
            return
        text_w = max(40, width - PAD_X - GAP - BUTTON - PAD_X)
        self.canvas.itemconfigure(self._win, width=text_w)
        height = self.text.winfo_reqheight() + 2 * PAD_Y
        if int(self.canvas.cget("height")) != height:
            self.canvas.configure(height=height)
        self.canvas.coords(self._box, *rounded_rect_points(0.5, 0.5, width - 1.5, height - 1.5, RADIUS))
        # Button in the bottom-right corner, like most chat apps.
        x1 = width - PAD_X + 2
        y1 = height - (height - BUTTON) / 2 if height < BUTTON + 2 * PAD_Y else height - PAD_Y + 1
        x0, y0 = x1 - BUTTON, y1 - BUTTON
        self.canvas.coords(self._button, x0, y0, x1, y1)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        self.canvas.coords(self._arrow, *_arrow_points(cx, cy))
        self.canvas.coords(self._stop, cx - 5, cy - 5, cx + 5, cy + 5)
        self._paint()

    def _paint(self) -> None:
        c = self._colors
        border = c["accent"] if self._focused else c["field_border"]
        self.canvas.itemconfigure(self._box, fill=c["bg"], outline=border)
        if self._busy:
            # Stop: a ring in the error colour with a square inside.
            self.canvas.itemconfigure(self._button, fill=c["bg"], outline=c["error_fg"], width=2)
            self.canvas.itemconfigure(self._stop, fill=c["error_fg"], state="normal")
            self.canvas.itemconfigure(self._arrow, state="hidden")
        else:
            if not self._enabled:
                fill = c["separator"]
            elif self._hover:
                fill = c["accent_hover"]
            else:
                fill = c["accent"]
            self.canvas.itemconfigure(self._button, fill=fill, outline="", width=0)
            self.canvas.itemconfigure(self._arrow, fill=c["accent_fg"], state="normal")
            self.canvas.itemconfigure(self._stop, state="hidden")
        clickable = self._busy or self._enabled
        self.canvas.configure(cursor="hand2" if (self._hover and clickable) else "")

    def _set_focus(self, focused: bool) -> None:
        self._focused = focused
        self._paint()

    def _set_hover(self, hover: bool) -> None:
        self._hover = hover
        self._paint()

    def _click(self) -> str:
        if self._busy:
            self._on_cancel()
        elif self._enabled:
            self._on_send()
        return "break"
