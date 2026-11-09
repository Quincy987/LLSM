"""The same XML update, but through an OpenAPI session (question d).

Own STA thread, not SciaSession: that class closes every SCIA on the machine and
starts the broad auto-clicker. Only processes this script started are stopped.

Usage: openapi_update.py [--answer-licence]

Startup dialogs, all logged:
- SCIA's licence notices are small Qt windows titled "SCIA Engineer", unreadable
  through Win32. By default they are left alone and the session runs into its
  timeout. --answer-licence sends Return to them, which rewrites the licence
  selection of this Windows user, so it is opt-in;
- "Onjuist afgesloten projecten" gets "Doorgaan", which keeps the recovery files;
- anything else is left alone.

Result of the two runs so far: HubServer did not start. Exit code 0 only when the
new supports are really in the saved file. Finding SCIA through this process's
children has not been run yet; the outcome lists any SCIA still running afterwards.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(Path(__file__).resolve().parent), str(ROOT / "src")]

import psutil
import win32api
import win32con
import win32gui
import win32process

from _common import SAMPLE_DEF, SCIA_PROCESSES, WORK, assert_no_scia, running_scia, work_copy
from dialogs import BM_CLICK
from parse_members import read_esa
from write_update import DEFAULT_SEGMENTS, build_update

LIMIT_S = 420
log_lines: list[str] = []


def log(message: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {message}"
    log_lines.append(line)
    print(line, flush=True)


def our_processes() -> list[psutil.Process]:
    """SCIA processes started by this script. A SCIA someone opens meanwhile is not one."""
    found = []
    for proc in psutil.Process().children(recursive=True):
        try:
            if proc.name().lower() in SCIA_PROCESSES:
                found.append(proc)
        except psutil.Error:  # it exited while we looked
            pass
    return found


class Startup(threading.Thread):
    """Answers the startup notices of the SCIA this script launched."""

    def __init__(self, answer_licence: bool):
        super().__init__(daemon=True)
        self.answer_licence = answer_licence
        self.halt = threading.Event()
        self.handled: set[int] = set()
        self.main_seen = False

    def run(self) -> None:
        while not self.halt.is_set():
            try:
                self.sweep()
            except Exception as exc:
                log(f"watcher error: {exc}")
            time.sleep(1)

    def sweep(self) -> None:
        pids = {p.pid for p in our_processes()}

        def top(hwnd: int, _: object) -> bool:
            if hwnd in self.handled or not win32gui.IsWindowVisible(hwnd):
                return True
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid not in pids:
                return True
            title, cls = win32gui.GetWindowText(hwnd), win32gui.GetClassName(hwnd)
            left, up, right, down = win32gui.GetWindowRect(hwnd)
            width = right - left
            if cls.startswith("Qt") and title == "SCIA Engineer" and width > 1000:
                self.main_seen = True
            minimised = left < -20000
            if cls.startswith("Qt") and title == "SCIA Engineer" and (minimised or 300 < width < 800) and not self.main_seen:
                self.handled.add(hwnd)
                if not self.answer_licence:
                    log(f"licence notice {width}x{down - up}: left alone")
                    return True
                if minimised:
                    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
                    time.sleep(1)
                win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, win32con.VK_RETURN, 1)
                win32api.PostMessage(hwnd, win32con.WM_KEYUP, win32con.VK_RETURN, 3 << 30 | 1)
                log(f"licence notice {width}x{down - up}: sent Return")
            elif cls == "#32770" and title:
                self.handled.add(hwnd)
                buttons: dict[str, int] = {}

                def child(c: int, _2: object) -> bool:
                    if win32gui.GetClassName(c) == "Button":
                        buttons[win32gui.GetWindowText(c).replace("&", "")] = c
                    return True

                win32gui.EnumChildWindows(hwnd, child, None)
                if title == "Onjuist afgesloten projecten" and "Doorgaan" in buttons:
                    win32gui.PostMessage(buttons["Doorgaan"], BM_CLICK, 0, 0)
                    log(f"dialog {title!r}: clicked Doorgaan")
                else:
                    log(f"dialog {title!r} buttons={list(buttons)}: left alone")
            return True

        win32gui.EnumWindows(top, None)


outcome: dict = {"steps": []}


def session(model: Path, update: Path) -> None:
    from llsm.scia import loader

    paths = loader.load()
    from SCIA.OpenAPI import Environment as Env, SaveMode

    temp = paths.temp_folder / "llsm_xml_spike"
    temp.mkdir(parents=True, exist_ok=True)
    env = Env(str(paths.install_folder), str(temp), "1.0.0.0")
    env.RunSCIAEngineer(Env.GuiMode.ShowWindowShowMinNoActive)
    outcome["steps"].append("RunSCIAEngineer returned")
    deadline = time.time() + 240
    while time.time() < deadline:
        if any("hubserver" in (p.info.get("name") or "").lower() for p in psutil.process_iter(["name"])):
            break
        time.sleep(1)
    else:
        raise RuntimeError("HubServer did not start")
    outcome["steps"].append("HubServer running")
    project = env.OpenProject(str(model))
    outcome["steps"].append(f"OpenProject: id {project.ProjectID}")
    env.UpdateProject(Env.TypeOfExtProject.TypeXML, str(update), project.ProjectID)
    outcome["steps"].append("UpdateProject returned")
    saved = project.CloseProject(SaveMode.SaveChangesYes)
    outcome["steps"].append(f"CloseProject(SaveChangesYes): {saved}")
    env.Dispose()
    outcome["steps"].append("Dispose returned")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--answer-licence", action="store_true")
    args = parser.parse_args()

    assert_no_scia()
    model = work_copy(WORK / "pile_base.esa", "openapi_in.esa")
    shutil.copy2(SAMPLE_DEF, WORK / "viktor.xml.def")
    update = WORK / "openapi_update.xml"
    update.write_text(build_update(DEFAULT_SEGMENTS, "B1", 1), encoding="utf-8")
    before = {s.name for s in read_esa(model)[1]}

    import clr

    clr.AddReference("System")
    import System.Threading as T

    errors: list[str] = []

    def body() -> None:
        try:
            session(model, update)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")

    startup = Startup(args.answer_licence)
    startup.start()
    thread = T.Thread(T.ThreadStart(body))
    thread.SetApartmentState(T.ApartmentState.STA)
    thread.Start()
    finished = thread.Join(LIMIT_S * 1000)
    startup.halt.set()
    outcome["finished_in_time"] = bool(finished)
    outcome["errors"] = errors

    time.sleep(15)
    left = our_processes()
    outcome["left_running"] = [p.pid for p in left]
    for proc in left:
        try:
            proc.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(left, timeout=15)  # the model file stays locked until they are gone
    outcome["scia_still_running"] = running_scia()

    after = {s.name: s for s in read_esa(model)[1]}
    outcome["new_supports"] = sorted(set(after) - before)
    outcome["values"] = {n: [after[n].x1, after[n].x2, after[n].stiffness_y] for n in outcome["new_supports"]}
    outcome["log"] = log_lines
    (WORK / "openapi_update.json").write_text(json.dumps(outcome, indent=1), encoding="utf-8")
    print(json.dumps(outcome, indent=1))
    sys.stdout.flush()
    # A hard exit: a wedged STA thread would otherwise keep this process alive.
    os._exit(0 if outcome["new_supports"] and not errors else 1)


if __name__ == "__main__":
    raise SystemExit(main())
