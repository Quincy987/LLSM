"""Background thread that auto-dismisses SCIA's license/confirmation dialogs.

SCIA v24 consistently pops a "SCIA Engineer" dialog with a Dutch "&Ja" button
(and occasionally OK/Yes in other languages). Without this clicker, the
HubServer won't spawn — see docs/06-phase0-results.md.
"""
from __future__ import annotations
import threading
import time
import ctypes
from typing import Iterable

import psutil
import win32gui
import win32process


_ACCEPT_LABELS = {
    "ja", "yes", "ok", "accepteren", "accept", "continue", "doorgaan",
}
_SCIA_NAME_HINTS = ("esa", "scia")


class AutoClicker:
    def __init__(self, poll_interval: float = 0.5, verbose: bool = False) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._interval = poll_interval
        self._verbose = verbose
        self._clicked_hwnds: set[int] = set()

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
                self._sweep()
            except Exception as exc:
                if self._verbose:
                    print(f"[autoclick] err: {exc}")
            time.sleep(self._interval)

    def _sweep(self) -> None:
        scia_pids = _collect_scia_pids()
        if not scia_pids:
            return

        def enum_cb(hwnd: int, _: object) -> bool:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid not in scia_pids or hwnd in self._clicked_hwnds:
                return True
            target = self._find_accept_button(hwnd)
            if target is not None:
                cbtn_hwnd, cbtn_text = target
                title = win32gui.GetWindowText(hwnd)
                if self._verbose:
                    print(f"[autoclick] dialog {title!r} -> click {cbtn_text!r}")
                self._click(cbtn_hwnd, hwnd)
                self._clicked_hwnds.add(hwnd)
            return True

        win32gui.EnumWindows(enum_cb, None)

    @staticmethod
    def _find_accept_button(parent_hwnd: int) -> tuple[int, str] | None:
        found: list[tuple[int, str]] = []

        def child_cb(chwnd: int, _: object) -> bool:
            text = win32gui.GetWindowText(chwnd)
            normalised = text.lower().replace("&", "").strip()
            if normalised in _ACCEPT_LABELS:
                found.append((chwnd, text))
                return False
            return True

        try:
            win32gui.EnumChildWindows(parent_hwnd, child_cb, None)
        except Exception:
            return None
        return found[0] if found else None

    @staticmethod
    def _click(button_hwnd: int, dialog_hwnd: int) -> None:
        # BM_CLICK = 0x00F5
        try:
            win32gui.PostMessage(button_hwnd, 0x00F5, 0, 0)
            return
        except Exception:
            pass
        # Fallback: Enter key
        try:
            win32gui.SetForegroundWindow(dialog_hwnd)
            ctypes.windll.user32.keybd_event(0x0D, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x0D, 0, 2, 0)
        except Exception:
            pass


def _collect_scia_pids() -> set[int]:
    pids: set[int] = set()
    for p in psutil.process_iter(["pid", "name"]):
        name = p.info.get("name")
        if name and any(hint in name.lower() for hint in _SCIA_NAME_HINTS):
            pids.add(p.info["pid"])
    return pids
