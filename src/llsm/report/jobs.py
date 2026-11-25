"""Report prints as background jobs, for callers that cannot wait 10-15 minutes in one call.

A report print takes 5-15 min; the MCP tool starts it here and returns at once, and the chat
calls report_status until it is done. The job machinery is shared with the calculation of a
chat-built model (``llsm.jobs``).
"""
from __future__ import annotations

from collections.abc import Callable

from ..jobs import MAX_WAIT_S, Job, JobBoard

__all__ = ["MAX_WAIT_S", "Job", "board", "running", "start", "wait"]

board = JobBoard(
    "report",
    busy_message="A report is already being printed (job {id}, {model}); wait for it with report_status.",
    running_message="SCIA is still printing on a copy. Call report_status with this job again; do not start another print.",
    failed_message="The print failed: {exc}",
    running_fields={"saved_by": None},  # the MCP state block reads this key: the model is a file
    failed_fields={"saved_by": None},
)
_jobs = board.jobs  # the tests drain this


def running() -> Job | None:
    return board.running()


def start(model: str, work: Callable[[Callable[[str], None]], dict]) -> Job:
    """Run ``work(progress)`` on a thread. Raises RuntimeError when a print is already running."""
    return board.start(model, work)


def wait(job_id: str, seconds: float = MAX_WAIT_S) -> dict:
    return board.wait(job_id, seconds)
