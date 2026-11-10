"""Watches the dialogs of the SCIA this session started, and only those.

It clicks one thing: SCIA's own Win32 licence question. Everything else is reported,
so the tool that is waiting on SCIA can say what it is waiting for:

* Qt licence notices (modules taken by colleagues, more than one mandatory licence).
  Their text cannot be read and answering them rewrites the user's licence selection,
  so they are shown and reported. ``LLSM_ANSWER_LICENCE_NOTICES=1`` answers them instead.
* any other dialog, e.g. "Onjuist afgesloten projecten" or "Vraag te bewaren".
"""
from __future__ import annotations

import ctypes
import os
import re
import struct
import sys
import threading
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import win32api
import win32con
import win32gui
import win32process
import win32ui

DIALOG_DIR = Path.home() / ".llsm" / "dialogs"

CODE_DIALOG = "SCIA_DIALOG"
CODE_NOTICE = "SCIA_LICENCE_NOTICE"

_BM_CLICK = 0x00F5
_PW_RENDERFULLCONTENT = 2
_TITLE = "SCIA Engineer"
_LICENCE_TEXT = re.compile(r"licen|protect|bescherm|module", re.I)
_YES_LABELS = {"ja", "ok"}
_MAIN_WINDOW_WIDTH = 1000  # the main window is wider; licence notices are about 500 px
_NOTICE_CLASS = re.compile(r"Qt\d+QWindowIcon")  # the main window's class has "OwnDC" in it
_ONLY_STOPS_WORK = {"annuleren", "cancel", "stop", "afbreken", "abort"}  # a progress window, not a question
_MINIMISED_LEFT = -20000  # Windows parks minimised windows at -32000


@dataclass(frozen=True)
class Window:
    hwnd: int
    title: str
    cls: str
    pid: int
    rect: tuple[int, int, int, int]
    buttons: tuple[tuple[int, str], ...] = ()  # (hwnd, label); Win32 dialogs only
    texts: tuple[str, ...] = ()

    @property
    def width(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def minimised(self) -> bool:
        return self.rect[0] < _MINIMISED_LEFT

    @property
    def labels(self) -> list[str]:
        return [label.replace("&", "").strip() for _, label in self.buttons]


@dataclass(frozen=True)
class PendingDialog:
    hwnd: int
    code: str
    title: str
    buttons: tuple[str, ...]
    seen_at: float = field(default_factory=time.time)

    @property
    def message(self) -> str:
        if self.code == CODE_NOTICE:
            return "SCIA asks about licences or modules"
        return f"{self.title}: {', '.join(self.buttons) or 'no buttons'}"


class SciaDialogPending(RuntimeError):
    """SCIA is waiting for an answer from the user."""

    def __init__(self, dialog: PendingDialog, mid_call: bool = False):
        super().__init__(dialog.message)
        self.dialog = dialog
        self.mid_call = mid_call

    def as_result(self) -> dict:
        suggestion = "Answer it in SCIA, then retry."
        if self.mid_call:
            suggestion = "Answer it in SCIA, then restart the MCP server (Tools > Restart MCP server)."
        return {
            "ok": False,
            "error": {"code": self.dialog.code, "message": self.dialog.message, "suggestion": suggestion},
        }


CLICK = "click"
NOTICE = "notice"
REPORT = "report"
MAIN = "main"
IGNORE = "ignore"


def classify(window: Window, main_seen: bool) -> str:
    """What to do about one visible window of our SCIA."""
    if window.cls.startswith("Qt") and window.title == _TITLE:
        if "OwnDC" in window.cls or window.width >= _MAIN_WINDOW_WIDTH:
            return MAIN  # also when minimised, which is how a hidden launch starts
        return NOTICE if _NOTICE_CLASS.fullmatch(window.cls) and not main_seen else IGNORE
    if window.cls != "#32770" or not window.title:
        return IGNORE
    is_licence = window.title == _TITLE and any(_LICENCE_TEXT.search(t) for t in window.texts)
    if is_licence and any(label.lower() in _YES_LABELS for label in window.labels):
        return CLICK
    if not {label.lower() for label in window.labels} - _ONLY_STOPS_WORK:
        return IGNORE  # no buttons, or only a way to cancel: nothing is being asked
    return REPORT


def write_png(path: Path, width: int, height: int, bgra: bytes) -> None:
    """Write top-down 32-bit BGRA pixels as a PNG, without an imaging library."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = bytearray()
    stride = width * 4
    for y in range(height):
        rows.append(0)
        line = bgra[y * stride : (y + 1) * stride]
        rows += bytes(b for i in range(0, stride, 4) for b in (line[i + 2], line[i + 1], line[i]))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b""))


def _capture(window: Window) -> Path | None:
    """Keep a picture of a dialog for the log. Best effort; only that window, never the desktop."""
    raw_dc = window_dc = memory_dc = bitmap = None
    try:
        DIALOG_DIR.mkdir(parents=True, exist_ok=True)
        width, height = window.rect[2] - window.rect[0], window.rect[3] - window.rect[1]
        raw_dc = win32gui.GetWindowDC(window.hwnd)
        window_dc = win32ui.CreateDCFromHandle(raw_dc)
        memory_dc = window_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(window_dc, width, height)
        memory_dc.SelectObject(bitmap)
        ctypes.windll.user32.PrintWindow(window.hwnd, memory_dc.GetSafeHdc(), _PW_RENDERFULLCONTENT)
        target = DIALOG_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{window.hwnd}.png"
        write_png(target, width, height, bitmap.GetBitmapBits(True))
        return target
    except Exception:
        return None
    finally:
        for release in (
            lambda: memory_dc.DeleteDC(),
            lambda: window_dc.DeleteDC(),
            lambda: win32gui.ReleaseDC(window.hwnd, raw_dc),
            lambda: win32gui.DeleteObject(bitmap.GetHandle()),
        ):
            try:
                release()
            except Exception:
                pass


def _describe(hwnd: int, pid: int) -> Window:
    title, cls = win32gui.GetWindowText(hwnd), win32gui.GetClassName(hwnd)
    buttons: list[tuple[int, str]] = []
    texts: list[str] = []
    if cls == "#32770":

        def child(chwnd: int, _: object) -> bool:
            label = win32gui.GetWindowText(chwnd).strip()
            if label:
                kind = win32gui.GetClassName(chwnd)
                if kind == "Button":
                    buttons.append((chwnd, label))
                elif kind == "Static":
                    texts.append(label)
            return True

        try:
            win32gui.EnumChildWindows(hwnd, child, None)
        except win32gui.error:
            pass
    return Window(hwnd, title, cls, pid, win32gui.GetWindowRect(hwnd), tuple(buttons), tuple(texts))


def visible_windows(pids: set[int]) -> list[Window]:
    found: list[Window] = []

    def top(hwnd: int, _: object) -> bool:
        if win32gui.IsWindowVisible(hwnd):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid in pids:
                found.append(_describe(hwnd, pid))
        return True

    win32gui.EnumWindows(top, None)
    return found


class AutoClicker:
    def __init__(
        self,
        owned_pids: Callable[[], set[int]],
        poll_interval: float = 0.5,
        verbose: bool = False,
        windows: Callable[[set[int]], list[Window]] = visible_windows,
    ) -> None:
        self._owned_pids = owned_pids
        self._windows = windows
        self._interval = poll_interval
        self._verbose = verbose
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._handled: set[int] = set()
        self._main_seen = False
        self.pending: PendingDialog | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.sweep()
            except Exception as exc:  # a watcher must never take the session down
                print(f"[autoclick] error: {exc}", file=sys.stderr)
            time.sleep(self._interval)

    def sweep(self) -> None:
        pids = self._owned_pids()
        windows = self._windows(pids) if pids else []
        open_now = {w.hwnd for w in windows}
        if self.pending is not None and self.pending.hwnd not in open_now:
            self.pending = None
        for window in windows:
            if window.hwnd in self._handled:
                continue
            action = classify(window, self._main_seen)
            if action == MAIN:
                self._main_seen = True
            elif action != IGNORE:
                self._handled.add(window.hwnd)
                self._act(action, window)

    def _act(self, action: str, window: Window) -> None:
        if action == CLICK:
            button = next(h for h, label in window.buttons if label.replace("&", "").strip().lower() in _YES_LABELS)
            self._post_click(button)
            self._log(f"licence dialog {window.title!r}: clicked")
        elif action == NOTICE:
            self._on_notice(window)
        else:
            picture = _capture(window)
            self.pending = PendingDialog(window.hwnd, CODE_DIALOG, window.title, tuple(window.labels))
            print(f"[autoclick] dialog {window.title!r} buttons={window.labels} picture={picture}", file=sys.stderr)

    def _on_notice(self, window: Window) -> None:
        if window.minimised:  # otherwise nobody can answer it
            self._show(window.hwnd)
        if os.environ.get("LLSM_ANSWER_LICENCE_NOTICES") == "1":
            self._press_return(window.hwnd)
            self._log("licence notice: sent Return (LLSM_ANSWER_LICENCE_NOTICES=1)")
            return
        self.pending = PendingDialog(window.hwnd, CODE_NOTICE, window.title, ())
        print("[autoclick] licence notice: waiting for the user", file=sys.stderr)

    @staticmethod
    def _post_click(button: int) -> None:
        win32gui.PostMessage(button, _BM_CLICK, 0, 0)

    @staticmethod
    def _show(hwnd: int) -> None:
        win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)

    @staticmethod
    def _press_return(hwnd: int) -> None:
        win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, win32con.VK_RETURN, 1)
        win32api.PostMessage(hwnd, win32con.WM_KEYUP, win32con.VK_RETURN, 3 << 30 | 1)

    def _log(self, message: str) -> None:
        if self._verbose:
            print(f"[autoclick] {message}", file=sys.stderr)
