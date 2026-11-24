"""run_analysis says ok only when SCIA returned results (docs/verify/calc-run.md).

SCIA's calculation command and its results API both report success when nothing was calculated,
so the check is a LoadResult for the first beam and load case. A fake session stands in for SCIA.
"""
from types import SimpleNamespace

import pytest

from llsm.scia import tools


class _Session:
    def __init__(self, results_api, *, wedge=False):
        self.calls = []
        self.results_api = results_api
        self.wedge = wedge
        model = SimpleNamespace(InitializeResultsAPI=lambda: self._init())
        self.project = SimpleNamespace(CreateMesh=lambda: self.calls.append("mesh"),
                                       RunCalculation=lambda: self.calls.append("calc"), Model=model)

    def _init(self):
        self.calls.append("init")
        return self.results_api

    def submit(self, fn, timeout_s=None):
        if self.wedge:
            raise TimeoutError("SCIA call did not return")
        return fn()


class _Builder:
    def __init__(self, beams=("Veld1",), cases=("Verkeer-UDL",)):
        self._snap = {"beams": [{"name": b} for b in beams], "load_cases": [{"name": c} for c in cases]}

    def snapshot(self):
        return self._snap


def _ctx(session, builder=None, mesh_dirty=True):
    ctx = tools.LlsmContext()
    ctx.session, ctx.builder = session, builder or _Builder()
    ctx.project_opened, ctx.mesh_dirty = True, mesh_dirty
    return ctx


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    monkeypatch.setattr(tools, "RESULTS_WAIT_S", 0.0)
    monkeypatch.setattr(tools.time, "sleep", lambda s: None)
    monkeypatch.setattr(tools, "_result_key", lambda ctx, beam, case, kind: (beam, case, kind))


class _Api:
    def __init__(self, result):
        self.result, self.keys, self.disposed = result, [], 0

    def LoadResult(self, key):
        self.keys.append(key)
        return self.result

    def Dispose(self):
        self.disposed += 1


def test_calculated_when_scia_returns_forces():
    api = _Api(object())
    ctx = _ctx(_Session(api))
    out = tools.run_analysis(ctx)
    assert out["ok"] is True and out["calculated"] is True
    assert out["checked"] == {"beam": "Veld1", "load_case": "Verkeer-UDL"}
    assert api.keys == [("Veld1", "Verkeer-UDL", "inner_forces")]
    assert ctx.last_calc_ok is True and ctx.rapi is api and ctx.mesh_dirty is False


def test_not_calculated_when_the_results_api_has_nothing():
    # what SCIA 24 does on this licence: the command and the results API both "succeed"
    ctx = _ctx(_Session(_Api(None)))
    out = tools.run_analysis(ctx)
    assert out["ok"] is False and out["calculated"] is False
    assert out["error"] == tools.NOT_CALCULATED
    assert ctx.last_calc_ok is False and ctx.rapi is None
    assert ctx.mesh_dirty is False  # the mesh itself was made


def test_not_calculated_when_the_results_api_is_missing():
    ctx = _ctx(_Session(None))
    out = tools.run_analysis(ctx)
    assert out["ok"] is False and out["error"] == tools.NOT_CALCULATED


def test_result_tools_repeat_the_plain_message():
    ctx = _ctx(_Session(_Api(None)))
    tools.run_analysis(ctx)
    for read in (tools.get_beam_inner_forces, tools.get_beam_deflection):
        out = read(ctx, "Veld1", "Verkeer-UDL")
        assert out == {"ok": False, "calculated": False, "error": tools.NOT_CALCULATED}


def test_mesh_only_when_dirty():
    session = _Session(_Api(object()))
    tools.run_analysis(_ctx(session, mesh_dirty=False))
    assert "mesh" not in session.calls and "calc" in session.calls


def test_a_model_without_beams_or_cases_is_not_checked_as_calculated():
    out = tools.run_analysis(_ctx(_Session(_Api(object())), _Builder(cases=())))
    assert out["ok"] is False and out["calculated"] is False and "no beam or no load case" in out["error"]


def test_a_hanging_scia_is_reported_as_wedged():
    out = tools.run_analysis(_ctx(_Session(_Api(object()), wedge=True)))
    assert out["ok"] is False and out["wedged"] is True


def test_result_tools_before_run_analysis_ask_for_it():
    ctx = _ctx(_Session(_Api(object())))
    assert tools.get_beam_inner_forces(ctx, "Veld1", "Verkeer-UDL")["error"].startswith("no analysis results available")


def test_a_results_api_that_saw_nothing_is_freed():
    api = _Api(None)
    tools.run_analysis(_ctx(_Session(api)))
    assert api.disposed == 1


def test_an_edit_makes_earlier_results_unreadable():
    api = _Api(object())
    ctx = _ctx(_Session(api))
    assert tools.run_analysis(ctx)["ok"]
    ctx.project_opened = False  # keep the edit from syncing to a SCIA; only the bookkeeping is tested
    tools._propagate_edit(ctx)
    assert ctx.last_calc_ok is False and ctx.rapi is None and api.disposed == 1
    assert tools.get_beam_inner_forces(ctx, "Veld1", "Verkeer-UDL")["error"].startswith("no analysis results available")


def test_without_a_pushed_project_nothing_runs():
    ctx = tools.LlsmContext()
    assert tools.run_analysis(ctx) == {"ok": False, "error": "no project open — call push_model first"}
    assert tools.get_beam_inner_forces(ctx, "B", "LC")["error"].startswith("no analysis results available")
