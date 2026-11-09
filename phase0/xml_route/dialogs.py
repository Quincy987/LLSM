"""Spike-local SCIA dialog watcher.

Only ever looks at windows of processes the spike started. It clicks one thing:
the "Ja" button of a dialog that is positively identified as SCIA's licence
question. Every other dialog is logged with its title, text and buttons and
left alone, because what SCIA asks on a bad input is what the spike has to see.

Not `llsm.scia.autoclick`: that one accepts ja/yes/ok/continue on any SCIA
window and never logs what it skips.

SCIA 24 asks its licence questions in Qt windows, which have no Win32 children
to read: those are logged by title and class but never clicked.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field

import psutil
import win32gui
import win32process

BM_CLICK = 0x00F5
LICENCE_TITLE = re.compile(r"^SCIA Engineer$", re.I)
LICENCE_TEXT = re.compile(r"licen|protect|bescherm", re.I)
YES_LABELS = {"ja"}


@dataclass
class Dialog:
    title: str
    cls: str
    pid: int
    buttons: list[str]
    text: list[str]
    clicked: str | None = None
    seen_at: float = field(default_factory=time.time)

    def describe(self) -> str:
        action = f"CLICKED {self.clicked!r}" if self.clicked else "left alone"
        return (
            f"[{self.cls}] title={self.title!r} buttons={self.buttons} "
            f"text={self.text} -> {action}"
        )


def is_licence_dialog(title: str, text: list[str]) -> bool:
    """True only for SCIA's own licence question, judged on title and body."""
    return bool(LICENCE_TITLE.match(title.strip())) and any(
        LICENCE_TEXT.search(line) for line in text
    )


def _describe_children(hwnd: int) -> tuple[list[tuple[int, str]], list[str]]:
    buttons: list[tuple[int, str]] = []
    text: list[str] = []

    def child(chwnd: int, _: object) -> bool:
        try:
            label = win32gui.GetWindowText(chwnd).strip()
            cls = win32gui.GetClassName(chwnd)
        except win32gui.error:
            return True
        if not label:
            return True
        if cls == "Button":
            buttons.append((chwnd, label))
        elif cls == "Static":
            text.append(label)
        return True

    try:
        win32gui.EnumChildWindows(hwnd, child, None)
    except win32gui.error:
        pass
    return buttons, text


class DialogWatcher:
    """Logs dialogs of `root_pid` and its descendants while the context is open."""

    def __init__(self, root_pid: int, click_licence: bool = True, interval: float = 0.5):
        self.root_pid = root_pid
        self.click_licence = click_licence
        self.interval = interval
        self.dialogs: list[Dialog] = []
        self._owned: set[int] = {root_pid}
        self._seen: set[int] = set()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def __enter__(self) -> "DialogWatcher":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=3)

    def owned_pids(self) -> set[int]:
        try:
            root = psutil.Process(self.root_pid)
            self._owned |= {c.pid for c in root.children(recursive=True)}
        except psutil.Error:
            pass
        return set(self._owned)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._sweep()
            except Exception as exc:  # a watcher must never take the run down
                print(f"[dialogs] sweep error: {exc}")
            time.sleep(self.interval)

    def _sweep(self) -> None:
        owned = self.owned_pids()

        def top(hwnd: int, _: object) -> bool:
            if hwnd in self._seen or not win32gui.IsWindowVisible(hwnd):
                return True
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid not in owned:
                return True
            self._seen.add(hwnd)
            self._handle(hwnd, pid)
            return True

        win32gui.EnumWindows(top, None)

    def _handle(self, hwnd: int, pid: int) -> None:
        title = win32gui.GetWindowText(hwnd)
        buttons, text = _describe_children(hwnd)
        dialog = Dialog(title, win32gui.GetClassName(hwnd), pid, [b for _, b in buttons], text)
        if self.click_licence and is_licence_dialog(title, text):
            for button, label in buttons:
                if label.replace("&", "").strip().lower() in YES_LABELS:
                    win32gui.PostMessage(button, BM_CLICK, 0, 0)
                    dialog.clicked = label
                    break
        self.dialogs.append(dialog)
        print(f"[dialogs] {dialog.describe()}", flush=True)
