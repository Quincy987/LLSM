"""List the windows of a SCIA this spike started and capture one of them.

Usage: gui_probe.py PID [--image NAME] [--hwnd HWND]

Captures only that window (PrintWindow), never the desktop, so the engineer's
other applications stay out of the evidence. SCIA's Qt panels do not render
through PrintWindow; the model viewport does.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import psutil
import win32gui
import win32process

from _common import WORK

CAPTURE = r"""
Add-Type -AssemblyName System.Drawing
Add-Type @'
using System; using System.Runtime.InteropServices;
public class W { [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f);
[StructLayout(LayoutKind.Sequential)] public struct R {public int L,T,Ri,B;}
[DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out R r);}
'@
$h=[IntPtr]::new(%d); $r=New-Object W+R; [void][W]::GetWindowRect($h,[ref]$r)
$b=New-Object System.Drawing.Bitmap ($r.Ri-$r.L),($r.B-$r.T)
$g=[System.Drawing.Graphics]::FromImage($b); $d=$g.GetHdc(); [void][W]::PrintWindow($h,$d,2); $g.ReleaseHdc($d)
$b.Save('%s',[System.Drawing.Imaging.ImageFormat]::Png)
"""


def owned(root: int) -> set[int]:
    proc = psutil.Process(root)
    return {root} | {c.pid for c in proc.children(recursive=True)}


def top_windows(pids: set[int]) -> list[tuple[int, str, str, int, tuple]]:
    found = []

    def top(hwnd: int, _: object) -> bool:
        if win32gui.IsWindowVisible(hwnd):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid in pids:
                found.append(
                    (hwnd, win32gui.GetWindowText(hwnd), win32gui.GetClassName(hwnd), pid, win32gui.GetWindowRect(hwnd))
                )
        return True

    win32gui.EnumWindows(top, None)
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pid", type=int)
    parser.add_argument("--image")
    parser.add_argument("--hwnd", type=int, help="capture this window instead of the largest one")
    args = parser.parse_args()
    windows = top_windows(owned(args.pid))
    for hwnd, title, cls, pid, rect in windows:
        print(f"hwnd={hwnd} pid={pid} class={cls} title={title!r} rect={rect}")
    if args.image and windows:
        hwnd = args.hwnd or max(windows, key=lambda w: (w[4][2] - w[4][0]) * (w[4][3] - w[4][1]))[0]
        WORK.mkdir(parents=True, exist_ok=True)
        target = WORK / f"{args.image}.png"
        subprocess.run(["powershell", "-NoProfile", "-Command", CAPTURE % (hwnd, target)], check=True, timeout=60)
        print("image:", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
