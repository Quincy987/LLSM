"""Restore a window of a SCIA this spike started, without taking focus.

Usage: gui_restore.py HWND
"""
import sys

import win32con
import win32gui


def main() -> int:
    hwnd = int(sys.argv[1])
    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
    print(win32gui.GetWindowRect(hwnd), win32gui.GetWindowText(hwnd))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
