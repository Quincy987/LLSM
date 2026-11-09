"""Only the SCIA this session started may be stopped, and no dialog may hang a tool."""
import json
import queue
import threading

import psutil
import pytest

from llsm.scia import autoclick, session as session_module
from llsm.scia.autoclick import CLICK, CODE_DIALOG, CODE_NOTICE, IGNORE, MAIN, NOTICE, REPORT, AutoClicker, PendingDialog, SciaDialogPending, Window, classify, write_png
from llsm.scia.session import SciaSession, kill_remembered, remember_processes


def window(title="SCIA Engineer", cls="#32770", rect=(0, 0, 400, 200), buttons=(), texts=(), hwnd=1):
    return Window(hwnd, title, cls, 7, rect, tuple(buttons), tuple(texts))


QT = "Qt652QWindowIcon"
YES = ((11, "&Ja"),)


def test_win32_licence_question_is_clicked():
    assert classify(window(buttons=YES, texts=("De licentie is in gebruik",)), False) == CLICK


def test_a_scia_engineer_dialog_about_something_else_is_only_reported():
    assert classify(window(buttons=YES, texts=("Project verwijderen?",)), False) == REPORT


@pytest.mark.parametrize("title", ["Onjuist afgesloten projecten", "Vraag te bewaren"])
def test_these_dialogs_are_never_clicked(title):
    assert classify(window(title=title, buttons=YES, texts=("licentie",)), False) == REPORT


def test_qt_licence_notice_is_a_notice_only_before_the_main_window():
    notice = window(cls=QT, rect=(500, 300, 1014, 517))
    assert classify(notice, False) == NOTICE
    assert classify(notice, True) == IGNORE


def test_a_minimised_notice_counts_as_a_notice():
    assert classify(window(cls=QT, rect=(-25600, -25600, -25441, -25573)), False) == NOTICE


def test_the_main_window_is_not_a_notice():
    assert classify(window(cls=QT, rect=(0, 0, 1536, 900)), False) == MAIN


def test_splash_and_startup_screen_are_ignored():
    assert classify(window(title="Splash Window", cls=QT), False) == IGNORE
    assert classify(window(title="SCIA Engineer Startup Screen", cls="HwndWrapper[x]"), False) == IGNORE


class Fixture(AutoClicker):
    clicks: list
    shown: list
    returned: list


def clicker(windows):
    fixture = Fixture(lambda: {7}, windows=lambda pids: list(windows))
    fixture.clicks, fixture.shown, fixture.returned = [], [], []
    fixture._post_click = fixture.clicks.append
    fixture._show = fixture.shown.append
    fixture._press_return = fixture.returned.append
    return fixture


def test_sweep_clicks_the_licence_question_once():
    fixture = clicker([window(buttons=YES, texts=("licentie",))])
    fixture.sweep()
    fixture.sweep()
    assert fixture.clicks == [11] and fixture.pending is None


def test_sweep_reports_other_dialogs_without_clicking(monkeypatch):
    monkeypatch.setattr(autoclick, "_capture", lambda w: None)
    fixture = clicker([window(title="Vraag te bewaren", buttons=YES)])
    fixture.sweep()
    assert not fixture.clicks
    assert fixture.pending.code == CODE_DIALOG and "Vraag te bewaren: Ja" in fixture.pending.message


def test_a_notice_is_shown_and_reported_but_not_answered(monkeypatch):
    monkeypatch.delenv("LLSM_ANSWER_LICENCE_NOTICES", raising=False)
    fixture = clicker([window(cls=QT, rect=(-25600, -25600, -25441, -25573))])
    fixture.sweep()
    assert fixture.pending.code == CODE_NOTICE and fixture.shown == [1] and not fixture.returned


def test_answering_notices_is_opt_in(monkeypatch):
    monkeypatch.setenv("LLSM_ANSWER_LICENCE_NOTICES", "1")
    fixture = clicker([window(cls=QT)])
    fixture.sweep()
    assert fixture.returned == [1] and fixture.pending is None


def test_pending_clears_when_the_window_is_gone(monkeypatch):
    monkeypatch.setattr(autoclick, "_capture", lambda w: None)
    windows = [window(title="Vraag te bewaren")]
    fixture = clicker(windows)
    fixture.sweep()
    assert fixture.pending is not None
    windows.clear()
    fixture.sweep()
    assert fixture.pending is None


def test_dialog_result_has_the_agreed_shape():
    error = SciaDialogPending(PendingDialog(1, CODE_DIALOG, "Vraag", ("Ja", "Nee"))).as_result()
    assert error == {
        "ok": False,
        "error": {"code": CODE_DIALOG, "message": "Vraag: Ja, Nee", "suggestion": "Answer it in SCIA, then retry."},
    }


def test_png_is_written(tmp_path):
    path = tmp_path / "x.png"
    write_png(path, 1, 1, bytes([1, 2, 3, 255]))
    assert path.read_bytes().startswith(b"\x89PNG")


class FakeProcess:
    def __init__(self, pid, name, created):
        self.pid, self._name, self._created, self.killed = pid, name, created, False

    def name(self):
        return self._name

    def create_time(self):
        return self._created

    def kill(self):
        self.killed = True


def test_only_the_same_process_is_stopped(tmp_path, monkeypatch):
    ledger = tmp_path / "sessions.json"
    ours = FakeProcess(1, "sciaengineer.exe", 100.0)
    recycled = FakeProcess(2, "sciaengineer.exe", 100.0)
    remember_processes([ours, recycled], ledger)
    now = {1: ours, 2: FakeProcess(2, "sciaengineer.exe", 900.0), 3: FakeProcess(3, "sciaengineer.exe", 100.0)}

    def lookup(pid):
        return now[pid]

    monkeypatch.setattr(session_module.psutil, "Process", lookup)
    monkeypatch.setattr(session_module.time, "sleep", lambda s: None)
    assert kill_remembered(ledger) == 1
    assert ours.killed and not now[2].killed and not now[3].killed
    assert not ledger.exists()


def test_a_process_that_is_gone_is_no_problem(tmp_path, monkeypatch):
    ledger = tmp_path / "sessions.json"
    ledger.write_text(json.dumps([{"pid": 5, "name": "esa.exe", "create_time": 1.0}]), encoding="utf-8")

    def gone(pid):
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(session_module.psutil, "Process", gone)
    assert kill_remembered(ledger) == 0


def test_remember_keeps_earlier_records(tmp_path):
    ledger = tmp_path / "sessions.json"
    remember_processes([FakeProcess(1, "esa.exe", 1.0)], ledger)
    remember_processes([FakeProcess(1, "esa.exe", 1.0), FakeProcess(2, "esa.exe", 2.0)], ledger)
    assert [r["pid"] for r in json.loads(ledger.read_text(encoding="utf-8"))] == [1, 2]


class Paused:
    """A session whose launch has been started, with a controllable dialog state."""

    def __init__(self, monkeypatch):
        monkeypatch.setattr(session_module.loader, "load", lambda: type("P", (), {"temp_folder": session_module.Path.cwd()})())
        monkeypatch.setattr(session_module.Path, "mkdir", lambda *a, **k: None)
        self.session = SciaSession()
        self.session._sta_thread = object()
        self.session._launched_at = session_module.time.time()


def test_start_returns_on_a_pending_dialog(monkeypatch):
    paused = Paused(monkeypatch)
    paused.session._clicker.pending = PendingDialog(1, CODE_NOTICE, "SCIA Engineer", ())
    with pytest.raises(SciaDialogPending):
        paused.session.start()


def test_start_goes_on_after_the_answer(monkeypatch):
    paused = Paused(monkeypatch)
    paused.session._started.set()
    paused.session.start()


def test_start_reports_a_failed_launch(monkeypatch):
    paused = Paused(monkeypatch)
    paused.session._failure = "SCIA did not start"
    with pytest.raises(RuntimeError, match="did not start"):
        paused.session.start()


def test_submit_refuses_while_a_dialog_waits(monkeypatch):
    paused = Paused(monkeypatch)
    paused.session._started.set()
    paused.session._clicker.pending = PendingDialog(1, CODE_DIALOG, "Vraag", ())
    with pytest.raises(SciaDialogPending):
        paused.session.submit(lambda: 1)


def test_submit_returns_when_a_dialog_appears_during_the_call(monkeypatch):
    paused = Paused(monkeypatch)
    paused.session._started.set()
    dialog = PendingDialog(1, CODE_DIALOG, "Vraag", ())
    threading.Timer(0.2, lambda: setattr(paused.session._clicker, "pending", dialog)).start()
    with pytest.raises(SciaDialogPending) as caught:
        paused.session.submit(lambda: 1)
    assert caught.value.mid_call and paused.session.is_wedged
    assert "restart" in caught.value.as_result()["error"]["suggestion"].lower()


def test_submit_still_returns_results(monkeypatch):
    paused = Paused(monkeypatch)
    paused.session._started.set()

    def worker():
        fn, reply = paused.session._call_q.get(timeout=2)
        reply.put(("ok", fn()))

    threading.Thread(target=worker, daemon=True).start()
    assert paused.session.submit(lambda: 41 + 1) == 42
    assert isinstance(paused.session._call_q, queue.Queue)
