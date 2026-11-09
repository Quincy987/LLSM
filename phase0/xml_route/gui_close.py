"""Close a SCIA this spike started the normal way, so its licences are returned.

Usage: gui_close.py PID [--wait 120]

Sends WM_CLOSE to its main window and reports any dialog it raises. It does not
kill: a killed SCIA keeps its licence seat until the token expires and leaves a
"projects closed incorrectly" prompt for the next start.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import psutil
import win32con
import win32gui

from gui_probe import owned, top_windows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pid", type=int)
    parser.add_argument("--wait", type=float, default=120)
    args = parser.parse_args()

    family = [psutil.Process(pid) for pid in owned(args.pid)]
    windows = top_windows({p.pid for p in family})
    main_window = max(
        (w for w in windows if w[3] == args.pid),
        key=lambda w: (w[4][2] - w[4][0]) * (w[4][3] - w[4][1]),
    )
    win32gui.PostMessage(main_window[0], win32con.WM_CLOSE, 0, 0)
    print("WM_CLOSE sent to", main_window[0], main_window[1])
    deadline = time.time() + args.wait
    while time.time() < deadline:
        if not psutil.pid_exists(args.pid):
            print("process exited")
            # SCIA leaves its startup screen behind; it holds no licence or data.
            for proc in psutil.wait_procs(family, timeout=15)[1]:
                proc.terminate()
                print("stopped leftover", proc.pid)
            return 0
        time.sleep(3)
    print("still running; windows:")
    for window in top_windows(owned(args.pid)):
        print(" ", window)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
