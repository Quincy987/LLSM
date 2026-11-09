"""Send one key to one window of a SCIA this spike started, without taking focus.

Usage: gui_answer.py HWND

Used for the licence question ("modules not available, continue without?").
It is a Qt window, so the Win32 button lookup in dialogs.py cannot see it.
"""
import sys

import win32api
import win32con
import win32gui


def main() -> int:
    hwnd = int(sys.argv[1])
    win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, win32con.VK_RETURN, 1)
    win32api.PostMessage(hwnd, win32con.WM_KEYUP, win32con.VK_RETURN, 3 << 30 | 1)
    print("sent Return to", hwnd, repr(win32gui.GetWindowText(hwnd)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
