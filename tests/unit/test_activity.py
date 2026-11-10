"""The per-turn activity log behind the panel's activity block."""
from llsm.conversation.activity import ActivityLog, first_error_line, format_clock, format_duration


def test_durations_and_clock():
    assert format_duration(4.24) == "4,2 s"
    assert format_duration(48.4) == "48 s"
    assert format_duration(65) == "1:05 min"
    assert format_clock(7.9) == "0:07" and format_clock(750) == "12:30"


def test_first_error_line():
    assert first_error_line("\n  SCIA refused\nTraceback ...") == "SCIA refused"
    assert first_error_line("x" * 300).endswith("…") and len(first_error_line("x" * 300)) == 140
    assert first_error_line(None) == ""


def test_steps_start_end_and_summary():
    log = ActivityLog(started=100.0)
    log.start("bedding_preview", "path=a.esa", now=101.0)
    log.start("model_summary", now=102.0)
    log.end("bedding_preview", True, now=104.0)
    log.end("model_summary", False, "kapot\nmeer", now=105.0)
    assert [(s.name, s.ok, s.error) for s in log.steps] == [
        ("bedding_preview", True, ""), ("model_summary", False, "kapot")]
    assert log.steps[0].duration(999) == 3.0
    log.finish(now=148.0)
    assert log.summary() == "2 stappen · 48 s"


def test_one_step_summary_and_unfinished_steps_stop_at_the_end():
    log = ActivityLog(started=0.0)
    log.start("run_analysis", now=1.0)
    log.finish(now=5.0)
    assert log.summary() == "1 stap · 5,0 s"
    assert log.steps[0].ended == 5.0


def test_cap_to_the_last_eight_steps():
    log = ActivityLog(started=0.0)
    for n in range(11):
        log.start(f"t{n}", now=float(n))
    hidden, shown = log.visible()
    assert hidden == 3 and [s.name for s in shown] == [f"t{n}" for n in range(3, 11)]
    assert log.earlier_text(hidden) == "… 3 eerdere stappen"


def test_quiet_after_sixty_seconds_without_progress():
    log = ActivityLog(started=0.0)
    assert not log.quiet(now=59.0)
    assert log.quiet(now=60.0)
    log.start("run_analysis", now=61.0)
    assert not log.quiet(now=100.0) and log.quiet(now=121.0)
    log.finish(now=130.0)
    assert not log.quiet(now=500.0)


def test_end_without_a_start_still_records_a_step():
    log = ActivityLog(started=0.0)
    log.end("onbekend", False, "geen start", now=2.0)
    assert [(s.name, s.ok) for s in log.steps] == [("onbekend", False)]
