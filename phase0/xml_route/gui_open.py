"""Open a work copy in the SCIA GUI and log its windows for a while.

Usage: gui_open.py MODEL.esa --seconds 90

Nothing is clicked. Close it afterwards with gui_close.py: a killed SCIA keeps its
licence seat until the token expires and leaves a recovery prompt behind.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import WORK, assert_no_scia
from gui_probe import owned, top_windows

GUI = Path(r"C:\Program Files\SCIA\Engineer24.0\SciaEngineer.exe")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("model", type=Path)
    parser.add_argument("--seconds", type=float, default=90)
    args = parser.parse_args()

    assert_no_scia()
    proc = subprocess.Popen([str(GUI), str(args.model.resolve())], cwd=GUI.parent)
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "gui.pid").write_text(str(proc.pid), encoding="ascii")
    print(f"started pid {proc.pid}", flush=True)
    seen: set[tuple[str, str]] = set()
    started = time.time()
    while time.time() - started < args.seconds and proc.poll() is None:
        for _, title, cls, pid, _rect in top_windows(owned(proc.pid)):
            if (title, cls) not in seen:
                seen.add((title, cls))
                print(f"[{time.time() - started:6.1f}s] title={title!r} class={cls} pid={pid}", flush=True)
        time.sleep(1)
    print("exit code:", proc.poll())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
