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
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable

import psutil

from .autoclick import AutoClicker
from . import loader

_SCIA_PROC_NAMES = {
    "esastartupscreen.exe", "esa.exe", "sciaengineer.exe",
    "sciatools.admtoadm.hubclient.exe", "sciatools.admtoadm.hubserver.exe",
}


def _kill_stale_scia() -> int:
    killed = 0
    for p in psutil.process_iter(["pid", "name"]):
        if p.info.get("name") and p.info["name"].lower() in _SCIA_PROC_NAMES:
            try:
                psutil.Process(p.info["pid"]).kill()
                killed += 1
            except Exception:
                pass
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

        self._clicker = AutoClicker(verbose=verbose)

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

    def start(self) -> None:
        if self._kill_stale:
            n = _kill_stale_scia()
            if n and self._verbose:
                print(f"[scia] killed {n} stale SCIA processes")
        self._clicker.start()

        import clr  # noqa: F401
        import System.Threading as T

        def sta_main():
            try:
                self._sta_loop(T)
            except Exception as e:
                self._log(f"STA thread crashed: {e}")

        thread_start = T.ThreadStart(sta_main)
        t = T.Thread(thread_start)
        t.SetApartmentState(T.ApartmentState.STA)
        t.Start()
        self._sta_thread = t

        # Wait for SCIA env ready
        if not self._started.wait(timeout=300):
            raise RuntimeError("SCIA startup did not complete in 5 minutes")

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

        # Wait for HubServer to spawn — signal that SCIA is fully ready
        self._log("waiting for HubServer...")
        deadline = time.time() + 180
        while time.time() < deadline:
            names = [p.info["name"].lower() for p in psutil.process_iter(["name"]) if p.info.get("name")]
            if any("hubserver" in n for n in names):
                self._log(f"HubServer ready")
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
        _kill_stale_scia()

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
        reply: queue.Queue = queue.Queue()
        self._call_q.put((fn, reply))
        try:
            if timeout_s is None:
                status, payload = reply.get()
            else:
                status, payload = reply.get(timeout=timeout_s)
        except queue.Empty:
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
