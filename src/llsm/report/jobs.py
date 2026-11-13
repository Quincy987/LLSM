"""Report prints as background jobs, for callers that cannot wait 10-15 minutes in one call.

Claude Code (2.1.318) ends an MCP tool call over HTTP after 300 s whatever MCP_TOOL_TIMEOUT
says (take 7, docs/verify/report.md), and a report print takes 5-15 min. So the MCP tool starts
the print here and returns at once; ``wait`` blocks for at most ``MAX_WAIT_S`` per call, well
under that limit, and the chat calls it again until the job is done. One print at a time: SCIA
runs one ESA_XML well, and two would compete for the licence.
"""
from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

MAX_WAIT_S = 240.0
_KEEP_FINISHED = 20


@dataclass
class Job:
    id: str
    model: str
    started: float = field(default_factory=time.time)
    step: str = "starting"
    result: dict | None = None
    done: threading.Event = field(default_factory=threading.Event)

    def status(self) -> dict:
        elapsed = round(time.time() - self.started)
        if self.done.is_set():
            return {**(self.result or {}), "job": self.id, "status": "done", "elapsed_s": elapsed}
        return {
            "ok": False,
            "job": self.id,
            "status": "running",
            "step": self.step,
            "elapsed_s": elapsed,
            "model": self.model,
            "saved_by": None,  # the MCP state block reads this key: the model is a file
            "message": "SCIA is still printing on a copy. Call report_status with this job again; do not start another print.",
        }


_jobs: dict[str, Job] = {}
_lock = threading.Lock()
_ids = itertools.count(1)


def running() -> Job | None:
    with _lock:
        return next((j for j in _jobs.values() if not j.done.is_set()), None)


def start(model: str, work: Callable[[Callable[[str], None]], dict]) -> Job:
    """Run ``work(progress)`` on a thread. Raises RuntimeError when a print is already running."""
    with _lock:
        busy = next((j for j in _jobs.values() if not j.done.is_set()), None)
        if busy is not None:
            raise RuntimeError(f"A report is already being printed (job {busy.id}, {busy.model}); wait for it with report_status.")
        job = Job(f"report-{next(_ids)}", model)
        _jobs[job.id] = job
        finished = [j for j in _jobs.values() if j.done.is_set()]
        for old in sorted(finished, key=lambda j: j.started)[:-_KEEP_FINISHED]:
            _jobs.pop(old.id, None)

    def progress(step: str) -> None:
        job.step = step

    def body() -> None:
        try:
            job.result = work(progress)
        except Exception as exc:  # a crash must end the job, or report_status would wait forever
            job.result = {"ok": False, "model": model, "saved_by": None, "problems": [f"The print failed: {exc}"]}
        finally:
            job.done.set()

    threading.Thread(target=body, name=job.id, daemon=True).start()
    return job


def wait(job_id: str, seconds: float = MAX_WAIT_S) -> dict:
    with _lock:
        job = _jobs.get(job_id)
    if job is None:
        known = ", ".join(sorted(_jobs)) or "none"
        return {"ok": False, "job": job_id, "status": "unknown", "saved_by": None,
                "problems": [f"No report job {job_id!r} (jobs in this server: {known}); a server restart forgets jobs."]}
    job.done.wait(max(0.0, min(seconds, MAX_WAIT_S)))
    return job.status()
