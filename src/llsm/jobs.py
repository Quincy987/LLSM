"""Background jobs for work that outlives one MCP tool call (a report print, a calculation).

Claude Code (2.1.318) ends an MCP tool call over HTTP after 300 s whatever MCP_TOOL_TIMEOUT
says (take 7, docs/verify/report.md). So a tool starts the work here and returns, ``wait``
blocks for at most ``MAX_WAIT_S`` per call, well under that limit, and the chat calls the
polling tool again until the job is done. One job per board at a time: SCIA runs one ESA_XML
well, and two would compete for the licence.
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
    board: "JobBoard"
    started: float = field(default_factory=time.time)
    step: str = "starting"
    result: dict | None = None
    done: threading.Event = field(default_factory=threading.Event)

    def status(self) -> dict:
        elapsed = round(time.time() - self.started)
        if self.done.is_set():
            return {**(self.result or {}), "job": self.id, "status": "done", "elapsed_s": elapsed}
        return {
            "ok": None,  # not known yet; False would show the step as failed in the panel
            "job": self.id,
            "status": "running",
            "step": self.step,
            "elapsed_s": elapsed,
            "model": self.model,
            **self.board.running_fields,
            "message": self.board.running_message,
        }


class JobBoard:
    """One kind of job (reports, calculations): at most one running, the last few kept for status."""

    def __init__(self, prefix: str, busy_message: str, running_message: str, failed_message: str,
                 running_fields: dict | None = None, failed_fields: dict | None = None) -> None:
        self.prefix = prefix
        self.busy_message = busy_message        # formatted with {id} and {model}
        self.running_message = running_message
        self.failed_message = failed_message    # formatted with {exc}
        self.running_fields = running_fields or {}
        self.failed_fields = failed_fields or {}
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._ids = itertools.count(1)

    def running(self) -> Job | None:
        with self._lock:
            return next((j for j in self.jobs.values() if not j.done.is_set()), None)

    def start(self, model: str, work: Callable[[Callable[[str], None]], dict]) -> Job:
        """Run ``work(progress)`` on a thread. Raises RuntimeError when a job of this board already runs."""
        with self._lock:
            busy = next((j for j in self.jobs.values() if not j.done.is_set()), None)
            if busy is not None:
                raise RuntimeError(self.busy_message.format(id=busy.id, model=busy.model))
            job = Job(f"{self.prefix}-{next(self._ids)}", model, self)
            self.jobs[job.id] = job
            finished = [j for j in self.jobs.values() if j.done.is_set()]
            for old in sorted(finished, key=lambda j: j.started)[:-_KEEP_FINISHED]:
                self.jobs.pop(old.id, None)

        def progress(step: str) -> None:
            job.step = step

        def body() -> None:
            try:
                job.result = work(progress)
            except Exception as exc:  # a crash must end the job, or the status call would wait forever
                job.result = {"ok": False, "model": model, **self.failed_fields,
                              "problems": [self.failed_message.format(exc=exc)]}
            finally:
                job.done.set()

        threading.Thread(target=body, name=job.id, daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self.jobs.get(job_id)

    def wait(self, job_id: str, seconds: float = MAX_WAIT_S) -> dict:
        job = self.get(job_id)
        if job is None:
            known = ", ".join(sorted(self.jobs)) or "none"
            return {"ok": False, "job": job_id, "status": "unknown", **self.failed_fields,
                    "problems": [f"No {self.prefix} job {job_id!r} (jobs in this server: {known}); a server restart forgets jobs."]}
        job.done.wait(max(0.0, min(seconds, MAX_WAIT_S)))
        return job.status()
