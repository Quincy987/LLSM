"""What a running chat turn is doing: one step per tool call. Tk-free.

The panel feeds it from the backends' tool start/end events and draws it as
the activity block above the answer (VibeModel v1.2.7). Nothing here reaches
the stored transcript or the history the model sees.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

# Steps shown while a turn runs; older ones fold into "… N eerdere stappen".
SHOWN_STEPS = 8
# Without a tool event for this long, the block says SCIA may be busy.
QUIET_AFTER_S = 60.0

TEXTS = {
    "busy": "Bezig",
    "thinking": "Aan het denken",
    "quiet": "SCIA kan bezig zijn",
    "earlier": "… {n} eerdere stappen",
    "earlier_one": "… 1 eerdere stap",
    "steps": "{n} stappen",
    "step": "1 stap",
}


def format_duration(seconds: float) -> str:
    """4,2 s / 48 s / 1:05 min — Dutch decimal comma."""
    seconds = max(0.0, seconds)
    if seconds < 10:
        return f"{seconds:.1f}".replace(".", ",") + " s"
    if seconds < 60:
        return f"{int(round(seconds))} s"
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d} min"


def format_clock(seconds: float) -> str:
    """The running counter: 0:07, 12:30."""
    m, s = divmod(int(max(0.0, seconds)), 60)
    return f"{m}:{s:02d}"


def first_error_line(error, limit: int = 140) -> str:
    """The first non-empty line of an error, shortened to `limit` characters."""
    for line in str(error or "").splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return ""


@dataclass
class Step:
    name: str
    args: str = ""
    started: float = 0.0
    ended: float | None = None
    ok: bool | None = None       # None while running
    error: str = ""

    def duration(self, now: float) -> float:
        return (self.ended if self.ended is not None else now) - self.started


@dataclass
class ActivityLog:
    """The steps of one turn and its clock."""

    started: float = field(default_factory=time.monotonic)
    ended: float | None = None
    steps: list = field(default_factory=list)
    last_progress: float = 0.0

    def __post_init__(self):
        self.last_progress = self.started

    def start(self, name: str, args: str = "", now: float | None = None) -> Step:
        now = time.monotonic() if now is None else now
        step = Step(name=name, args=args, started=now)
        self.steps.append(step)
        self.last_progress = now
        return step

    def end(self, name: str, ok: bool, error: str = "", now: float | None = None) -> Step | None:
        """Close the most recent running step with this name (else the most recent running one)."""
        now = time.monotonic() if now is None else now
        self.last_progress = now
        running = [s for s in self.steps if s.ok is None]
        match = next((s for s in reversed(running) if s.name == name), None) or (running[-1] if running else None)
        if match is None:
            match = Step(name=name, started=now)
            self.steps.append(match)
        match.ended = now
        match.ok = bool(ok)
        match.error = "" if ok else first_error_line(error)
        return match

    def finish(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.ended = now
        for s in self.steps:  # a cancelled turn leaves no step spinning
            if s.ok is None:
                s.ended = now

    @property
    def done(self) -> bool:
        return self.ended is not None

    def elapsed(self, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        return (self.ended if self.ended is not None else now) - self.started

    def quiet(self, now: float | None = None) -> bool:
        """True while running with no tool event for QUIET_AFTER_S."""
        now = time.monotonic() if now is None else now
        return not self.done and now - self.last_progress >= QUIET_AFTER_S

    def visible(self, cap: int = SHOWN_STEPS) -> tuple[int, list]:
        """(number of older steps folded away, the last `cap` steps)."""
        hidden = max(0, len(self.steps) - cap)
        return hidden, self.steps[hidden:]

    def summary(self, now: float | None = None) -> str:
        """'7 stappen · 48 s' for the collapsed block."""
        n = len(self.steps)
        count = TEXTS["step"] if n == 1 else TEXTS["steps"].format(n=n)
        return f"{count} · {format_duration(self.elapsed(now))}"

    def earlier_text(self, hidden: int) -> str:
        return TEXTS["earlier_one"] if hidden == 1 else TEXTS["earlier"].format(n=hidden)
