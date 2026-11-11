"""Colours and widget styling for the panel: a SCIA-like light theme and a dark theme.

Every colour the panel, the chat renderer and the dialogs paint with comes from
here. Two palettes (LIGHT, DARK) name the surfaces and role hues; `derive()`
turns one into the flat dict the chat tags use; `apply_ttk()` styles the ttk
widgets and sets option defaults for plain Tk widgets; `apply_titlebar()` makes
the Windows title bar follow.

The light palette follows SCIA Engineer 24: grey window chrome (#f0f0f0), a
slightly lighter menu strip, white content areas and SCIA's teal (its logo is
#55c4be) as the single accent, darkened on light backgrounds so text stays
readable (WCAG AA, checked in tests/unit/test_theme.py).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

LIGHT_MODE = "light"
DARK_MODE = "dark"
SYSTEM = "system"
SETTINGS = (LIGHT_MODE, DARK_MODE, SYSTEM)

CHROME_FONT = ("Segoe UI", 9)
CHROME_FONT_BOLD = ("Segoe UI Semibold", 9)
# Chat and input text; the renderer and the panel both read it from here.
BODY_FONT = ("Segoe UI", 10)


@dataclass(frozen=True)
class Palette:
    mode: str
    # Surfaces.
    bg: str              # window chrome (frames, toolbars, sidebar header)
    strip_bg: str        # menu strip
    surface: str         # content areas: chat, input, tree, lists
    fg: str
    muted: str
    separator: str
    # Accent (SCIA teal) and controls.
    accent: str
    accent_fg: str       # text on an accent fill
    accent_hover: str
    button_bg: str
    button_hover: str
    button_active: str
    field_border: str
    # Role hues (same keys as the panel's _THEME_REFERENCE).
    user_accent: str
    llsm_accent: str
    error_accent: str
    system_accent: str
    thinking: str
    running: str
    idle: str
    ok: str
    link: str

    def ref(self) -> dict:
        """Role hues plus surfaces, keyed like the panel's _THEME_REFERENCE."""
        return {
            "bg": self.surface,
            "fg": self.fg,
            "muted": self.muted,
            "insert": self.fg,
            "separator": self.separator,
            "user_accent": self.user_accent,
            "llsm_accent": self.llsm_accent,
            "error_accent": self.error_accent,
            "system_accent": self.system_accent,
            "thinking": self.thinking,
            "running": self.running,
            "idle": self.idle,
        }


LIGHT = Palette(
    mode=LIGHT_MODE,
    bg="#f0f0f0", strip_bg="#f3f3f3", surface="#ffffff",
    fg="#202020", muted="#6e6e6e", separator="#d4d4d4",
    accent="#1a7f79", accent_fg="#ffffff", accent_hover="#17736e",
    button_bg="#fafafa", button_hover="#e6f4f3", button_active="#cfe9e7",
    field_border="#c8c8c8",
    user_accent="#1a7f79", llsm_accent="#1a7f79", error_accent="#c62828",
    system_accent="#808080", thinking="#b8860b", running="#1a7f79", idle="#808080",
    ok="#2e7d32", link="#17736e",
)

DARK = Palette(
    mode=DARK_MODE,
    bg="#2b2b2b", strip_bg="#252526", surface="#1e1e1e",
    fg="#e6e6e6", muted="#9a9a9a", separator="#3c3c3c",
    accent="#4fb3ad", accent_fg="#0f2a29", accent_hover="#5fc1bb",
    button_bg="#333333", button_hover="#3a4a49", button_active="#2f5a57",
    field_border="#3a3a3a",
    user_accent="#4fb3ad", llsm_accent="#4fb3ad", error_accent="#ff6b6b",
    system_accent="#9a9a9a", thinking="#e2b340", running="#4fb3ad", idle="#9a9a9a",
    ok="#7bc47f", link="#7fd3ce",
)

_PALETTES = {LIGHT_MODE: LIGHT, DARK_MODE: DARK}
_current: Palette = LIGHT


# ------------------------------------------------------------------ mode


def _windows_prefers_light() -> bool:
    import winreg

    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
    )
    try:
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
    finally:
        winreg.CloseKey(key)
    return bool(value)


def resolve_mode(setting: str | None, fallback: str = LIGHT_MODE) -> str:
    """'light' / 'dark' as given; 'system' (or anything else) follows Windows,
    `fallback` when the Windows setting can't be read."""
    if setting in (LIGHT_MODE, DARK_MODE):
        return setting
    try:
        return LIGHT_MODE if _windows_prefers_light() else DARK_MODE
    except Exception:
        return fallback


def palette_for(setting: str | None, fallback: str = LIGHT_MODE) -> Palette:
    return _PALETTES[resolve_mode(setting, fallback)]


def set_current(palette: Palette) -> None:
    global _current
    _current = palette


# ------------------------------------------------------------------ colour maths


def parse_hex(value: str) -> tuple[int, int, int] | None:
    v = (value or "").strip()
    if not v.startswith("#"):
        return None
    v = v[1:]
    if len(v) == 3:
        v = "".join(ch * 2 for ch in v)
    if len(v) != 6:
        return None
    try:
        return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
    except ValueError:
        return None


def blend(base_rgb, accent_rgb, alpha: float) -> str:
    """Blend two RGB tuples, `alpha` of the way from base to accent; returns '#rrggbb'."""
    out = []
    for b, a in zip(base_rgb, accent_rgb):
        out.append(max(0, min(255, int(b * (1 - alpha) + a * alpha))))
    return "#%02x%02x%02x" % tuple(out)


def tint(base: str, accent: str, alpha: float = 0.18) -> str:
    """Blend two '#rrggbb' colours. Unparseable input falls back to the light muted grey."""
    b, a = parse_hex(base), parse_hex(accent)
    if b is None or a is None:
        return LIGHT.muted
    return blend(b, a, alpha)


def luminance(color: str) -> float:
    """WCAG relative luminance of a '#rrggbb' colour."""
    def chan(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = parse_hex(color) or (0, 0, 0)
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# ------------------------------------------------------------------ derived colours

DERIVED_KEYS = (
    "bg", "fg", "muted", "separator",
    "user_fg", "llsm_fg", "error_fg",
    "user_bg",
    "code_bg", "table_head_bg", "table_alt_bg", "ok_fg", "link_fg",
    "chrome_bg", "strip_bg", "field_border", "accent", "accent_fg", "accent_hover",
    "thinking_fg", "running_fg", "error_pill_fg",
)


def derive(p: Palette) -> dict:
    """The flat colour dict the chat tags, the pill and the renderer use.

    `bg` is the CONTENT surface (chat, input, table rows and margins), not the
    window chrome; the chrome is `chrome_bg`. Tints are computed against it.
    """
    s, fg = p.surface, p.fg
    return {
        "bg": s,
        "fg": fg,
        "muted": p.muted,
        "separator": p.separator,
        # Body text: only a hint of the role hue, so long answers stay neutral.
        "user_fg": tint(fg, p.user_accent, 0.35),
        "llsm_fg": tint(fg, p.llsm_accent, 0.15),
        "error_fg": p.error_accent,
        # Soft block behind your own messages.
        "user_bg": tint(s, p.user_accent, 0.08),
        "thinking_fg": p.thinking,
        "running_fg": p.running,
        "error_pill_fg": p.error_accent,
        "code_bg": tint(s, fg, 0.06),
        "table_head_bg": tint(s, p.llsm_accent, 0.14),
        "table_alt_bg": tint(s, fg, 0.04),
        "ok_fg": p.ok,
        "link_fg": p.link,
        "chrome_bg": p.bg,
        "strip_bg": p.strip_bg,
        "field_border": p.field_border,
        "accent": p.accent,
        "accent_fg": p.accent_fg,
        "accent_hover": p.accent_hover,
    }


def light_colors() -> dict:
    """Derived colours of the light palette: the defaults for any partial dict."""
    return derive(LIGHT)


# ------------------------------------------------------------------ widgets


def _row_height(root) -> int:
    """Treeview row height in pixels for the chrome font (rowheight doesn't scale itself)."""
    try:
        import tkinter.font as tkfont
        return tkfont.Font(root=root, font=CHROME_FONT).metrics("linespace") + 6
    except Exception:
        return 20


def apply_ttk(style, p: Palette, root=None) -> None:
    """Style ttk widgets with `p` (clam engine) and set option defaults for plain Tk widgets.

    Safe to call again on a theme switch: ttk widgets restyle at once; plain Tk
    widgets created afterwards pick up the new option defaults (existing ones
    are recoloured by their owner).
    """
    try:
        style.theme_use("clam")
    except Exception:
        pass
    sel = dict(selectbackground=p.accent, selectforeground=p.accent_fg)
    style.configure(
        ".", background=p.bg, foreground=p.fg, fieldbackground=p.surface,
        bordercolor=p.field_border, lightcolor=p.bg, darkcolor=p.bg,
        troughcolor=p.bg, focuscolor=p.accent, insertcolor=p.fg,
        font=CHROME_FONT, **sel,
    )
    style.map(".", foreground=[("disabled", p.muted)])
    style.configure("TFrame", background=p.bg)
    style.configure("TLabel", background=p.bg, foreground=p.fg)
    style.configure("Muted.TLabel", background=p.bg, foreground=p.muted)
    style.configure("Link.TLabel", background=p.bg, foreground=p.link)
    style.configure("Header.TLabel", background=p.bg, foreground=p.fg, font=CHROME_FONT_BOLD)
    style.configure(
        "TButton", background=p.button_bg, foreground=p.fg, bordercolor=p.field_border,
        lightcolor=p.button_bg, darkcolor=p.button_bg, relief="flat", padding=(10, 4),
        focusthickness=1, focuscolor=p.accent,
    )
    style.map(
        "TButton",
        background=[("disabled", p.bg), ("pressed", p.button_active), ("active", p.button_hover)],
        lightcolor=[("disabled", p.bg), ("pressed", p.button_active), ("active", p.button_hover)],
        darkcolor=[("disabled", p.bg), ("pressed", p.button_active), ("active", p.button_hover)],
        bordercolor=[("disabled", p.separator), ("active", p.accent)],
        foreground=[("disabled", p.muted)],
    )
    style.configure(
        "Accent.TButton", background=p.accent, foreground=p.accent_fg, bordercolor=p.accent,
        lightcolor=p.accent, darkcolor=p.accent, font=CHROME_FONT_BOLD, padding=(14, 4),
    )
    style.map(
        "Accent.TButton",
        background=[("disabled", p.separator), ("pressed", p.accent_hover), ("active", p.accent_hover)],
        lightcolor=[("disabled", p.separator), ("pressed", p.accent_hover), ("active", p.accent_hover)],
        darkcolor=[("disabled", p.separator), ("pressed", p.accent_hover), ("active", p.accent_hover)],
        bordercolor=[("disabled", p.separator), ("active", p.accent_hover)],
        foreground=[("disabled", p.muted)],
    )
    style.configure("TSeparator", background=p.separator)
    # Grey thumb on the content surface, no border lines: like SCIA's lists.
    thumb, thumb_hover = p.field_border, p.muted
    style.configure(
        "TScrollbar", background=thumb, troughcolor=p.surface, bordercolor=p.surface,
        lightcolor=thumb, darkcolor=thumb, arrowcolor=p.muted, gripcount=0,
        relief="flat",
    )
    style.map(
        "TScrollbar",
        background=[("pressed", thumb_hover), ("active", thumb_hover)],
        lightcolor=[("pressed", thumb_hover), ("active", thumb_hover)],
        darkcolor=[("pressed", thumb_hover), ("active", thumb_hover)],
        bordercolor=[("active", p.surface)],
        arrowcolor=[("active", p.fg)],
    )
    style.configure(
        "Treeview", background=p.surface, fieldbackground=p.surface, foreground=p.fg,
        bordercolor=p.field_border, lightcolor=p.surface, darkcolor=p.surface,
        rowheight=_row_height(root),
    )
    style.map("Treeview", background=[("selected", p.accent)], foreground=[("selected", p.accent_fg)])
    style.configure(
        "Treeview.Heading", background=p.bg, foreground=p.fg, bordercolor=p.field_border,
        lightcolor=p.bg, darkcolor=p.bg, relief="flat", font=CHROME_FONT_BOLD,
    )
    style.map("Treeview.Heading", background=[("active", p.button_hover)])
    style.configure(
        "TEntry", fieldbackground=p.surface, foreground=p.fg, insertcolor=p.fg,
        bordercolor=p.field_border, lightcolor=p.surface, darkcolor=p.surface, padding=3,
    )
    style.map("TEntry", bordercolor=[("focus", p.accent)], lightcolor=[("focus", p.accent)])
    style.configure(
        "TCombobox", fieldbackground=p.surface, foreground=p.fg, background=p.button_bg,
        arrowcolor=p.fg, bordercolor=p.field_border, lightcolor=p.surface, darkcolor=p.surface,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", p.surface)],
        bordercolor=[("focus", p.accent)],
        background=[("active", p.button_hover)],
    )
    for name in ("TCheckbutton", "TRadiobutton"):
        style.configure(name, background=p.bg, foreground=p.fg, indicatorbackground=p.surface,
                        indicatorforeground=p.fg, upperbordercolor=p.field_border,
                        lowerbordercolor=p.field_border)
        style.map(name, background=[("active", p.bg)],
                  indicatorbackground=[("selected", p.accent), ("pressed", p.button_active)],
                  indicatorforeground=[("selected", p.accent_fg)])
    style.configure("TLabelframe", background=p.bg, bordercolor=p.separator,
                    lightcolor=p.bg, darkcolor=p.bg)
    style.configure("TLabelframe.Label", background=p.bg, foreground=p.fg, font=CHROME_FONT_BOLD)
    style.configure("TNotebook", background=p.bg, bordercolor=p.separator,
                    lightcolor=p.bg, darkcolor=p.bg)
    style.configure("TNotebook.Tab", background=p.button_bg, foreground=p.fg,
                    bordercolor=p.separator, lightcolor=p.button_bg, darkcolor=p.button_bg,
                    padding=(10, 3))
    style.map("TNotebook.Tab", background=[("selected", p.surface), ("active", p.button_hover)])

    if root is not None:
        apply_options(root, p)


def apply_options(root, p: Palette) -> None:
    """Option-database defaults for plain Tk widgets created from now on."""
    try:
        root.configure(bg=p.bg)
    except Exception:
        pass
    opts = {
        "*Toplevel.background": p.bg,
        "*Frame.background": p.bg,
        "*Text.background": p.surface,
        "*Text.foreground": p.fg,
        "*Text.insertBackground": p.fg,
        "*Text.selectBackground": p.accent,
        "*Text.selectForeground": p.accent_fg,
        "*Text.inactiveSelectBackground": p.button_active,
        "*Text.highlightBackground": p.field_border,
        "*Text.highlightColor": p.accent,
        "*Label.background": p.bg,
        "*Label.foreground": p.fg,
        "*Button.background": p.button_bg,
        "*Button.foreground": p.fg,
        "*Button.activeBackground": p.button_hover,
        "*Button.activeForeground": p.fg,
        "*Button.highlightBackground": p.bg,
        "*Button.relief": "flat",
        "*Checkbutton.background": p.bg,
        "*Checkbutton.foreground": p.fg,
        "*Checkbutton.selectColor": p.surface,
        "*Checkbutton.activeBackground": p.bg,
        "*Checkbutton.activeForeground": p.fg,
        "*Menu.background": p.surface,
        "*Menu.foreground": p.fg,
        "*Menu.activeBackground": p.accent,
        "*Menu.activeForeground": p.accent_fg,
        "*Menu.disabledForeground": p.muted,
        "*Menu.selectColor": p.fg,
        "*Menu.relief": "flat",
        "*Menubutton.background": p.strip_bg,
        "*Menubutton.foreground": p.fg,
        "*Menubutton.activeBackground": p.button_hover,
        "*Menubutton.activeForeground": p.fg,
        "*Entry.background": p.surface,
        "*Entry.foreground": p.fg,
        "*Entry.insertBackground": p.fg,
        "*Entry.selectBackground": p.accent,
        "*Entry.selectForeground": p.accent_fg,
        "*Listbox.background": p.surface,
        "*Listbox.foreground": p.fg,
        "*Listbox.selectBackground": p.accent,
        "*Listbox.selectForeground": p.accent_fg,
        "*Listbox.highlightBackground": p.field_border,
        "*Listbox.highlightColor": p.accent,
        "*TCombobox*Listbox.background": p.surface,
        "*TCombobox*Listbox.foreground": p.fg,
        "*TCombobox*Listbox.selectBackground": p.accent,
        "*TCombobox*Listbox.selectForeground": p.accent_fg,
    }
    for pattern, value in opts.items():
        try:
            root.option_add(pattern, value)
        except Exception:
            pass


def menu_colors(p: Palette) -> dict:
    """Keyword arguments that recolour an existing tk.Menu."""
    return dict(background=p.surface, foreground=p.fg, activebackground=p.accent,
                activeforeground=p.accent_fg, disabledforeground=p.muted, selectcolor=p.fg)


def menubutton_colors(p: Palette) -> dict:
    """Keyword arguments that recolour an existing menu-strip Menubutton."""
    return dict(background=p.strip_bg, foreground=p.fg, activebackground=p.button_hover,
                activeforeground=p.fg)


# ------------------------------------------------------------------ title bar

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19   # Windows 10 before 20H1


def _frame_hwnd(window) -> int | None:
    try:
        return int(window.wm_frame(), 16)
    except Exception:
        pass
    try:
        import win32gui
        return win32gui.GetParent(window.winfo_id())
    except Exception:
        return None


def apply_titlebar(window, dark: bool | None = None) -> bool:
    """Make the Windows title bar of `window` dark or light. Returns True on success."""
    if sys.platform != "win32":
        return False
    if dark is None:
        dark = _current.mode == DARK_MODE
    hwnd = _frame_hwnd(window)
    if not hwnd:
        return False
    try:
        import ctypes
        value = ctypes.c_int(1 if dark else 0)
        dwm = ctypes.windll.dwmapi
        ok = False
        for attr in (_DWMWA_USE_IMMERSIVE_DARK_MODE, _DWMWA_USE_IMMERSIVE_DARK_MODE_OLD):
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                ok = True
                break
        # Repaint the non-client area so the change shows without a resize.
        SWP = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020   # NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP)
        return ok
    except Exception:
        return False


def install_dialog_titlebars(root) -> None:
    """The main window and every Toplevel (dialogs, simpledialog) get the current
    title bar when they map. Setting it before the window is mapped does nothing,
    so the startup window needs this as much as the dialogs do."""
    def on_map(event):
        w = event.widget
        try:
            if w.winfo_toplevel() is w:
                apply_titlebar(w)
        except Exception:
            pass
    # "Tk" is the root window's class; children don't carry it in their bindtags.
    for cls in ("Toplevel", "Tk"):
        try:
            root.bind_class(cls, "<Map>", on_map, add="+")
        except Exception:
            pass
