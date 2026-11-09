"""SciaSession — manages the SCIA Engineer process lifecycle.

Usage:
    with SciaSession() as sess:
        sess.new_project()
        sess.push_model(adm_model)
        sess.run_analysis()
        result = sess.query_result(...)

SCIA operations run on an internal STA thread (OpenAPI requires this).
All public methods marshal calls onto that thread.
"""
from __future__ import annotations
import json
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable

import psutil

from .autoclick import AutoClicker, PendingDialog, SciaDialogPending
from .esa_xml import SCIA_PROCESSES, running_scia
from . import loader

SESSIONS_FILE = Path.home() / ".llsm" / "scia_sessions.json"
_LAUNCH_LIMIT_S = 420  # RunSCIAEngineer alone can take 3 minutes before it gives up


def our_scia_processes() -> list[psutil.Process]:
    """SCIA processes started from this process. A SCIA someone opened by hand is never one."""
    found = []
    for proc in psutil.Process().children(recursive=True):
        try:
            if proc.name().lower() in SCIA_PROCESSES:
                found.append(proc)
        except psutil.Error:  # it exited while we looked
            pass
    return found


def _load_records(path: Path) -> list[dict]:
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return records if isinstance(records, list) else []


def remember_processes(procs: list[psutil.Process], path: Path | None = None) -> None:
    """Add these processes to the ledger of SCIAs we may stop later."""
    path = path or SESSIONS_FILE
    records = _load_records(path)
    known = {(r.get("pid"), r.get("create_time")) for r in records}
    changed = False
    for proc in procs:
        try:
            record = {"pid": proc.pid, "name": proc.name().lower(), "create_time": proc.create_time()}
        except psutil.Error:
            continue
        if (record["pid"], record["create_time"]) not in known:
            records.append(record)
            changed = True
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(records, indent=1), encoding="utf-8")


def kill_remembered(path: Path | None = None) -> int:
    """Stop only processes in the ledger that are still the same process, then clear it."""
    path = path or SESSIONS_FILE
    killed = 0
    for record in _load_records(path):
        try:
            proc = psutil.Process(record["pid"])
            same = proc.name().lower() == record["name"] and abs(proc.create_time() - record["create_time"]) < 1.0
            if same:
                proc.kill()
                killed += 1
        except (psutil.Error, KeyError, TypeError):
            pass
    path.unlink(missing_ok=True)
    if killed:
        time.sleep(3)
    return killed


class SciaSession:
    """Manages a single SCIA Engineer instance plus the auto-clicker.

    Construct + enter as context manager. All SCIA calls go through submit()
    so they run on the STA thread.
    """

    def __init__(
        self,
        *,
        temp_subfolder: str = "llsm",
        visible: bool = False,  # False = minimized/no-focus; True = normal window
        verbose: bool = True,
        kill_stale: bool = True,
    ) -> None:
        self._paths = loader.load()
        self._our_temp = self._paths.temp_folder / temp_subfolder
        self._our_temp.mkdir(parents=True, exist_ok=True)

        self._visible = visible
        self._verbose = verbose
        self._kill_stale = kill_stale

        self._clicker = AutoClicker(self._owned_pids, verbose=verbose)
        self.foreign_at_start: list[tuple[int, str]] = []
        self._failure: str | None = None
        self._launched_at = 0.0

        self._call_q: queue.Queue[tuple[Callable[[], Any], queue.Queue]] = queue.Queue()
        self._sta_thread: threading.Thread | None = None
        self._started = threading.Event()
        self._shutdown = threading.Event()
        # When a submit() times out the STA thread is still running the wedged
        # call (we can't safely abort native .NET code), so accepting a new
        # submit would just queue behind it and hang forever. Flip this flag
        # and fail fast on subsequent submits until the panel restarts MCP.
        # Typical trigger: SCIA's AdmToAdm service fails with eErrFail and
        # push_model never returns from CreateAdmObject / RefreshModel.
        self._wedged: bool = False
        self._wedge_reason: str = ""

        self._env: Any = None  # SCIA.OpenAPI.Environment instance
        self._proj: Any = None  # current EsaProject

    # --- Lifecycle ---------------------------------------------------------

    def __enter__(self) -> "SciaSession":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _owned_pids(self) -> set[int]:
        """Our SCIA processes right now; every one seen goes into the ledger."""
        procs = our_scia_processes()
        remember_processes(procs)
        return {p.pid for p in procs}

    @property
    def pending_dialog(self) -> PendingDialog | None:
        return self._clicker.pending

    def start(self) -> None:
        """Launch SCIA, or keep waiting for a launch already under way.

        Raises SciaDialogPending while SCIA waits for the user; calling it again
        after they have answered carries on with the same launch.
        """
        if self._sta_thread is None:
            self._launch()
        while not self._started.wait(timeout=1.0):
            if self._failure:
                raise RuntimeError(self._failure)
            if self._clicker.pending is not None:
                raise SciaDialogPending(self._clicker.pending)
            if time.time() - self._launched_at > _LAUNCH_LIMIT_S:
                raise RuntimeError(f"SCIA did not finish starting within {_LAUNCH_LIMIT_S} s")

    def _launch(self) -> None:
        if self._kill_stale:
            n = kill_remembered()
            if n and self._verbose:
                print(f"[scia] stopped {n} processes left by an earlier LLSM session")
        self.foreign_at_start = running_scia()
        self._launched_at = time.time()
        self._clicker.start()

        import clr  # noqa: F401
        import System.Threading as T

        def sta_main():
            try:
                self._sta_loop(T)
            except Exception as e:
                self._log(f"STA thread crashed: {e}")
                detail = f"SCIA did not start: {e}"
                if self.foreign_at_start:
                    detail += " (another SCIA is already running on this computer; close it and try again)"
                self._failure = detail

        thread_start = T.ThreadStart(sta_main)
        t = T.Thread(thread_start)
        t.SetApartmentState(T.ApartmentState.STA)
        t.Start()
        self._sta_thread = t

    def _sta_loop(self, T) -> None:
        from SCIA.OpenAPI import Environment as SciaEnvironment

        mode = (
            SciaEnvironment.GuiMode.ShowWindowShow
            if self._visible
            else SciaEnvironment.GuiMode.ShowWindowShowMinNoActive
        )

        self._env = SciaEnvironment(
            str(self._paths.install_folder),
            str(self._our_temp),
            "1.0.0.0",
        )
        self._log(f"launching SCIA Engineer (visible={self._visible})...")
        self._env.RunSCIAEngineer(mode)
        if self.foreign_at_start and not our_scia_processes():
            raise RuntimeError("no SCIA was started")  # no point waiting for a HubServer

        # Wait for HubServer to spawn — signal that SCIA is fully ready
        self._log("waiting for HubServer...")
        deadline = time.time() + 180
        while time.time() < deadline:
            if self._shutdown.is_set():
                return
            if any("hubserver" in p.name().lower() for p in our_scia_processes()):
                self._log("HubServer ready")
                break
            time.sleep(1)
        else:
            raise RuntimeError("HubServer did not spawn within 3 minutes")

        self._started.set()

        # Dispatch loop — pull callables from queue, run on STA thread
        while not self._shutdown.is_set():
            try:
                fn, reply = self._call_q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                result = fn()
                reply.put(("ok", result))
            except Exception as e:
                reply.put(("err", e))

        # Teardown on STA thread
        try:
            if self._env is not None:
                self._env.Dispose()
        except Exception as e:
            self._log(f"Dispose error: {e}")

    def close(self) -> None:
        self._shutdown.set()
        if self._sta_thread is not None:
            # give STA loop time to drain + dispose env
            for _ in range(20):
                if not self._sta_thread.IsAlive:
                    break
                time.sleep(0.2)
        self._clicker.stop()
        time.sleep(1)
        kill_remembered()

    # --- Call marshalling --------------------------------------------------

    def submit(self, fn: Callable[[], Any], *, timeout_s: float | None = None) -> Any:
        """Run `fn` on the STA thread, block until result.

        If `timeout_s` is set and the call doesn't complete within that window,
        raise TimeoutError and mark the session as wedged. Subsequent submits
        fail fast — the STA thread is still running the original call (we can't
        safely abort native .NET) so queueing new work behind it would just hang.
        """
        if self._wedged:
            raise RuntimeError(
                f"SCIA session wedged ({self._wedge_reason}) — restart MCP to recover"
            )
        if self._shutdown.is_set():
            raise RuntimeError("session is closed")
        if self._clicker.pending is not None:
            raise SciaDialogPending(self._clicker.pending)
        if not self._started.is_set():
            raise RuntimeError("SCIA is still starting; call launch_scia again")
        reply: queue.Queue = queue.Queue()
        self._call_q.put((fn, reply))
        waited = 0.0
        while True:
            try:
                status, payload = reply.get(timeout=1.0)
                break
            except queue.Empty:
                waited += 1.0
            dialog = self._clicker.pending
            if dialog is not None:
                self._wedged = True
                self._wedge_reason = f"a dialog interrupted the call: {dialog.message}"
                raise SciaDialogPending(dialog, mid_call=True)
            if timeout_s is not None and waited >= timeout_s:
                self._wedged = True
                self._wedge_reason = f"call did not return within {timeout_s}s"
                raise TimeoutError(
                    f"SCIA call did not return within {timeout_s}s — likely AdmToAdm hang; "
                    "restart MCP from the panel to recover"
                )
        if status == "err":
            raise payload
        return payload

    @property
    def is_wedged(self) -> bool:
        return self._wedged

    # --- Convenience accessors --------------------------------------------

    @property
    def env(self):
        return self._env

    @property
    def project(self):
        return self._proj

    def set_project(self, proj) -> None:
        self._proj = proj

    @property
    def temp_folder(self) -> Path:
        return self._our_temp

    def _log(self, msg: str) -> None:
        if self._verbose:
            print(f"[scia] {msg}")
