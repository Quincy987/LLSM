"""The calculation of a chat-built model: start model, batch run, results (docs/verify/calc-run.md).

No SCIA here: a captured export of the 2 x 12 m test beam, a synthetic start model, and fakes for the
session and the batch run.
"""
import struct
import threading
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from llsm.scia import calc, esa_xml, start_model
from llsm.scia.autoclick import CONVERT_QUESTION, ScopedAnswer, Window
from llsm.scia.builder import ModelBuilder, ReservedNameError
from llsm.scia.xmlio import parse_xmlio

EXPORT = Path(__file__).parent / "data" / "calc" / "two_span_results.xml"
CASES = ["CB BG 01", "CB BG 02"]


# --- the export ------------------------------------------------------------------------------

def test_the_export_gives_forces_per_case_and_member():
    results = calc.results_per_case(parse_xmlio(EXPORT), CASES)
    assert set(results) == set(CASES)
    veld1 = results["CB BG 01"]["CB_Veld1"]["forces"]
    assert [r["dx_m"] for r in veld1] == sorted(r["dx_m"] for r in veld1)
    at_b = next(r for r in veld1 if r["dx_m"] == 12)
    assert at_b["My"] == pytest.approx(-161193.90625) and at_b["Vz"] == pytest.approx(-67432.828125)
    # hand values: q L^2 / 8 = 162 kNm (both spans), q L^2 / 16 = 81 kNm (span 1 only)
    assert abs(at_b["My"] / 1e3) == pytest.approx(162, rel=0.01)
    span1_only = next(r for r in results["CB BG 02"]["CB_Veld1"]["forces"] if r["dx_m"] == 12)
    assert abs(span1_only["My"] / 1e3) == pytest.approx(81, rel=0.01)


def test_classes_of_the_start_model_are_left_out():
    results = calc.results_per_case(parse_xmlio(EXPORT), ["CB BG 01"])
    assert set(results) == {"CB BG 01"}  # not "Alle BGT", "Alle UGT+BGT"


def test_estimate_grows_with_members_and_cases():
    small, many_cases, many_members = calc.estimate_seconds(2, 2), calc.estimate_seconds(2, 10), calc.estimate_seconds(200, 2)
    assert small < many_cases and small < many_members
    assert calc.estimate_seconds(2, 2) % 5 == 0


def test_lin_with_case_classes_writes_the_update_and_passes_the_switches(tmp_path):
    seen = {}

    def runner(source, folder, how, switches, timeout):
        seen.update(source=source, how=how, switches=switches)
        return esa_xml.RunResult(0, "Succeeded", 1.0, "", "")

    calc.lin_with_case_classes(tmp_path / "m.esa", tmp_path, ["A 01"], ["-xout.xml"], 60, runner)
    assert seen["how"] == "LIN" and seen["switches"][-1] == "-xout.xml"
    assert (tmp_path / "classes.xml").is_file() and (tmp_path / esa_xml.RESULT_CLASSES_DEF_FILE.name).is_file()


def test_calculate_passes_the_allowed_processes_and_reads_the_export(tmp_path):
    seen = {}

    def runner(source, folder, how, switches, timeout, allow_pids):
        seen["allow"] = allow_pids
        Path(switches[-1][2:]).write_bytes(EXPORT.read_bytes())
        return esa_xml.RunResult(0, "Succeeded", 1.0, "", "")

    (tmp_path / "m.esa").write_bytes(b"esa")
    out = calc.calculate(tmp_path / "m.esa", CASES, tmp_path, frozenset({7}), runner=runner)
    assert out["ok"] and seen["allow"] == frozenset({7}) and "CB_Veld2" in out["results"]["CB BG 01"]


def test_a_failed_batch_run_says_so(tmp_path):
    def runner(source, folder, how, switches, timeout, allow_pids):
        return esa_xml.RunResult(5, "Calculation failed", 1.0, "", "line one\nline two")

    (tmp_path / "m.esa").write_bytes(b"esa")
    out = calc.calculate(tmp_path / "m.esa", CASES, tmp_path, runner=runner)
    assert not out["ok"] and "Calculation failed" in out["problems"][0] and "line two" in out["problems"][0]


# --- the guard -------------------------------------------------------------------------------

def test_only_the_allowed_scia_may_run_alongside(monkeypatch):
    monkeypatch.setattr(esa_xml, "running_scia", lambda: [(1, "sciaengineer.exe"), (2, "sciatools.admtoadm.hubserver.exe")])
    with pytest.raises(esa_xml.SciaRunning):
        esa_xml.assert_no_scia()  # strict default: bedding, moving load, report
    esa_xml.assert_no_scia(frozenset({1, 2}))  # the calculation passes its own session
    with pytest.raises(esa_xml.SciaRunning, match="sciaengineer"):
        esa_xml.assert_no_scia(frozenset({2}))


# --- the start model -------------------------------------------------------------------------

def _name(text: str) -> bytes:
    return struct.pack("<H", len(text)) + text.encode("utf-16le")


def _block(table: str | None, names: list[str]) -> bytes:
    body = (_name(f"^$1A {{12345678-1234-1234-1234-123456789ABC}} {table}") if table else b"") + b"\x00".join(_name(n) for n in names)
    return zlib.compress(body + b"\x00" * 32)


def _start_file(tmp_path: Path) -> Path:
    path = tmp_path / "start.esa"
    path.write_bytes(b"\x00" * 64 + b"".join([
        _block("EP_Material.EP_Material.1", ["C30/37", "S235"]),
        _block(None, ["C20/25", "<ARCHIVE>xml</ARCHIVE>"]),  # the material table goes on in the next block
        _block("DataSetScia.EP_LoadGroup.1", ["LG1"]),
        _block("DataSetScia.EP_LoadCase.1", ["BG1", "+D_344_24.0.3015.64"]),
        _block("EP_DSG_Elements.EP_Beam.1", ["B_not_owned"]),
    ]))
    return path


def test_owned_names_come_from_the_listed_tables_and_their_continuation(tmp_path):
    names = start_model.owned_names(_start_file(tmp_path))
    assert {"C30/37", "S235", "C20/25", "LG1", "BG1"} <= names
    assert "B_not_owned" not in names and not any("<" in n or n.startswith("+D_") for n in names)


def test_start_model_is_found_by_env_var_or_default(tmp_path, monkeypatch):
    model = _start_file(tmp_path)
    monkeypatch.setenv(start_model.ENV, str(model))
    assert start_model.find() == model
    monkeypatch.setenv(start_model.ENV, str(tmp_path / "missing.esa"))
    assert start_model.find() is None
    monkeypatch.delenv(start_model.ENV)
    monkeypatch.setattr(start_model, "DEFAULT", model)
    assert start_model.find() == model


def test_the_export_check_looks_for_the_forces_table():
    assert start_model.export_has_results_table(EXPORT.read_text(encoding="utf-8", errors="replace"))
    assert not start_model.export_has_results_table("<project></project>")


def test_a_reserved_name_is_refused_with_the_reason():
    b = ModelBuilder.__new__(ModelBuilder)
    b._objects_by_name, b._group_kinds, b._model = {}, {}, []
    b.reserved = frozenset({"C30/37", "LG1"})
    with pytest.raises(ReservedNameError, match="start model"):
        b._check_free("C30/37")
    b._check_free("Beton C30/37")  # anything else is free


# --- SCIA's questions while saving -------------------------------------------------------------

def test_scoped_answer_presses_only_the_convert_question(monkeypatch):
    from llsm.scia import autoclick
    pressed = []
    monkeypatch.setattr(autoclick.win32gui, "PostMessage", lambda hwnd, msg, a, b: pressed.append(hwnd))
    convert = Window(1, "SCIA Engineer", "#32770", 9, (0, 0, 10, 10), ((11, "&Ja"), (12, "&Nee")),
                     ("De gegevens worden geconverteerd naar een nieuwe versie.\nGegevens converteren ?",))
    other = Window(2, "SCIA Engineer", "#32770", 9, (0, 0, 10, 10), ((21, "Ja"),), ("Wilt u opslaan?",))
    answer = ScopedAnswer(lambda: {9}, CONVERT_QUESTION, windows=lambda pids: [convert, other])
    answer.sweep()
    answer.sweep()  # once per window
    assert pressed == [11] and len(answer.answered) == 1


# --- run_analysis and the result tools ---------------------------------------------------------

class _Session:
    def __init__(self):
        self.calls = []
        self.project = SimpleNamespace(CreateMesh=lambda: self.calls.append("mesh"))
        self.own_pids = frozenset({42})

    def submit(self, fn, timeout_s=None):
        return fn()

    def save_project_as(self, target, timeout_s=None):
        self.calls.append("save")
        Path(target).write_bytes(b"esa")
        return True


class _Builder:
    def __init__(self, members=("CB_Veld1", "CB_Veld2"), cases=tuple(CASES)):
        self.members, self.cases = list(members), list(cases)
        self.items = 15

    def snapshot(self):
        return {"beams": [{"name": m} for m in self.members], "load_cases": [{"name": c} for c in self.cases]}

    def item_count(self):
        return self.items


@pytest.fixture
def tools(monkeypatch):
    from llsm.scia import tools as t
    for job in list(t.calc_jobs.jobs.values()):
        job.done.wait(5)
    t.calc_jobs.jobs.clear()
    return t


def _ctx(t, tmp_path, builder=None):
    ctx = t.LlsmContext()
    ctx.session, ctx.builder = _Session(), builder or _Builder()
    ctx.project_opened, ctx.mesh_dirty = True, True
    ctx.start_copy = tmp_path / "start-copy.esa"
    ctx.pushed_items = ctx.builder.item_count()
    return ctx


def _fake_calculate(monkeypatch, t, gate: threading.Event | None = None, ok: bool = True):
    def fake(saved, cases, folder, allow, progress=lambda s: None, runner=None):
        assert Path(saved).is_file() and allow == frozenset({42})
        if gate is not None:
            gate.wait(5)
        if not ok:
            return {"ok": False, "results": {}, "problems": [], "seconds": 1.0, "esa_xml": "Succeeded"}
        return {"ok": True, "results": calc.results_per_case(parse_xmlio(EXPORT), cases), "problems": [],
                "seconds": 1.0, "esa_xml": "Succeeded"}
    monkeypatch.setattr(t.calc, "calculate", fake)


def test_run_analysis_calculates_and_the_forces_come_from_the_results(tools, tmp_path, monkeypatch):
    _fake_calculate(monkeypatch, tools)
    ctx = _ctx(tools, tmp_path)
    out = tools.run_analysis(ctx)
    assert out["ok"] is True and out["calculated"] is True and out["status"] == "done"
    assert "calculated copy" in out["note"] and ctx.session.calls == ["mesh", "save"]
    forces = tools.get_beam_inner_forces(ctx, "CB_Veld1", "CB BG 01")
    assert forces["ok"] and forces["positions_m"][-1] == 12
    assert forces["values_kN_kNm"]["My (kNm)"][-1] == pytest.approx(-161.19, abs=0.01)
    assert tools.get_beam_inner_forces(ctx, "CB_Veld9", "CB BG 01")["error"].startswith("no results for member 'CB_Veld9'")
    assert "calculated cases" in tools.get_beam_inner_forces(ctx, "CB_Veld1", "nope")["error"]


def test_deflections_are_refused_with_the_reason(tools, tmp_path, monkeypatch):
    _fake_calculate(monkeypatch, tools)
    ctx = _ctx(tools, tmp_path)
    tools.run_analysis(ctx)
    out = tools.get_beam_deflection(ctx, "CB_Veld1", "CB BG 01")
    assert out["ok"] is False and out["reliable"] is False and "stiffness" in out["error"]


def test_no_rows_is_not_a_calculation(tools, tmp_path, monkeypatch):
    _fake_calculate(monkeypatch, tools, ok=False)
    ctx = _ctx(tools, tmp_path)
    out = tools.run_analysis(ctx)
    assert out["ok"] is False and out["calculated"] is False and "twist" in out["error"]
    assert tools.get_beam_inner_forces(ctx, "CB_Veld1", "CB BG 01")["ok"] is False


def test_an_edit_during_the_calculation_throws_its_result_away(tools, tmp_path, monkeypatch):
    gate = threading.Event()
    _fake_calculate(monkeypatch, tools, gate)
    monkeypatch.setattr(tools, "MAX_WAIT_S", 0.05)
    ctx = _ctx(tools, tmp_path)
    started = tools.run_analysis(ctx)
    assert started["status"] == "running" and started["ok"] is None
    ctx.project_opened = False  # the edit's SCIA sync is not under test, only the bookkeeping
    tools._propagate_edit(ctx)
    ctx.project_opened = True
    gate.set()
    tools.calc_jobs.jobs[started["job"]].done.wait(5)
    done = tools.run_analysis(ctx) if tools.calc_jobs.running() else tools._calc_status(started["job"], 0)
    assert done["ok"] is False and done["stale"] is True
    assert ctx.results is None and tools.get_beam_inner_forces(ctx, "CB_Veld1", "CB BG 01")["ok"] is False


def test_a_push_during_the_calculation_also_voids_it(tools, tmp_path, monkeypatch):
    gate = threading.Event()
    _fake_calculate(monkeypatch, tools, gate)
    monkeypatch.setattr(tools, "MAX_WAIT_S", 0.05)
    ctx = _ctx(tools, tmp_path)
    started = tools.run_analysis(ctx)
    ctx.model_changed()  # what push_model does after the push
    gate.set()
    tools.calc_jobs.jobs[started["job"]].done.wait(5)
    assert tools._calc_status(started["job"], 0)["stale"] is True and ctx.results is None


def test_calling_again_while_it_runs_waits_for_the_same_job(tools, tmp_path, monkeypatch):
    gate = threading.Event()
    _fake_calculate(monkeypatch, tools, gate)
    monkeypatch.setattr(tools, "MAX_WAIT_S", 0.05)
    ctx = _ctx(tools, tmp_path)
    first = tools.run_analysis(ctx)
    second = tools.run_analysis(ctx)
    assert second["job"] == first["job"] and second["status"] == "running"
    assert ctx.session.calls.count("save") == 1
    gate.set()
    tools.calc_jobs.jobs[first["job"]].done.wait(5)
    again = tools.run_analysis(ctx)  # finished meanwhile: the same result, not a new calculation
    assert again["calculated"] is True and again["already_calculated"] is True and again["job"] == first["job"]
    assert ctx.session.calls.count("save") == 1


def test_a_long_calculation_asks_first(tools, tmp_path, monkeypatch):
    _fake_calculate(monkeypatch, tools)
    monkeypatch.setattr(tools.calc, "CONFIRM_ABOVE_S", 1)
    ctx = _ctx(tools, tmp_path)
    out = tools.run_analysis(ctx)
    assert out["needs_confirmation"] is True and out["calc_estimate_s"] > 1 and ctx.session.calls == []
    assert tools.run_analysis(ctx, confirm=True)["calculated"] is True


def test_without_a_start_model_nothing_is_calculated(tools, tmp_path):
    ctx = _ctx(tools, tmp_path)
    ctx.start_copy = None
    out = tools.run_analysis(ctx)
    assert out["ok"] is False and out["error"] == tools.NOT_CALCULATED and "start-model" in out["hint"]
    assert tools.get_beam_inner_forces(ctx, "CB_Veld1", "CB BG 01")["error"] == tools.NOT_CALCULATED


def test_objects_added_after_the_push_must_be_pushed_first(tools, tmp_path, monkeypatch):
    _fake_calculate(monkeypatch, tools)
    ctx = _ctx(tools, tmp_path)
    ctx.builder.items += 1
    out = tools.run_analysis(ctx)
    assert out["ok"] is False and "push_model again" in out["error"]


def test_the_result_tools_wait_for_run_analysis(tools, tmp_path):
    ctx = _ctx(tools, tmp_path)
    assert tools.get_beam_inner_forces(ctx, "CB_Veld1", "CB BG 01")["error"].startswith("no analysis results available")


def test_closing_voids_a_running_calculation_and_removes_the_start_copy(tools, tmp_path):
    ctx = _ctx(tools, tmp_path)
    ctx.session = None  # nothing to close in SCIA here
    ctx.builder = None
    ctx.start_copy.write_bytes(b"copy")
    version = ctx.model_version
    assert tools.close_scia(ctx) == {"ok": True}
    assert ctx.model_version > version and not ctx.project_opened and not (tmp_path / "start-copy.esa").exists()


def test_old_start_copies_are_removed_but_not_the_one_in_use(tools, tmp_path):
    current, old = tmp_path / "llsm-start-2.esa", tmp_path / "llsm-start-1.esa"
    other = tmp_path / "keep-me.esa"
    for f in (current, old, other):
        f.write_bytes(b"x")
    tools._remove_old_start_copies(tmp_path, keep=current)
    assert current.exists() and other.exists() and not old.exists()


def test_after_an_edit_run_analysis_calculates_again(tools, tmp_path, monkeypatch):
    _fake_calculate(monkeypatch, tools)
    ctx = _ctx(tools, tmp_path)
    assert tools.run_analysis(ctx)["calculated"] is True
    ctx.project_opened = False
    tools._propagate_edit(ctx)
    ctx.project_opened = True
    again = tools.run_analysis(ctx)
    assert again["calculated"] is True and "already_calculated" not in again
    assert ctx.session.calls.count("save") == 2


def test_a_crashed_calculation_reports_an_error(tools, tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("ESA_XML vanished")
    monkeypatch.setattr(tools.calc, "calculate", boom)
    out = tools.run_analysis(_ctx(tools, tmp_path))
    assert out["ok"] is False and out["calculated"] is False and "ESA_XML vanished" in out["error"]


def test_a_row_without_a_position_is_skipped():
    from llsm.scia.xmlio import Table
    table = Table("Resultaatklasses", "A", "Interne 1D-krachten", "", ["Naam", "dx", "M_y"],
                  [["B1", "", "1"], ["B1", "2", "-5000"]])
    rows = calc.results_per_case([table], ["A"])["A"]["B1"]["forces"]
    assert rows == [{"dx_m": 2.0, "My": -5000.0}]


def test_rows_for_other_members_only_are_not_a_result(tools, tmp_path, monkeypatch):
    _fake_calculate(monkeypatch, tools)
    ctx = _ctx(tools, tmp_path, _Builder(members=("Other",)))
    out = tools.run_analysis(ctx)
    assert out["ok"] is False and out["calculated"] is False and ctx.results is None
