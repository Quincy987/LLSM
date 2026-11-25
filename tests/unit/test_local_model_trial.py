"""The trial script scores what was built, not what the model said."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "local_model_trial.py"
_spec = importlib.util.spec_from_file_location("local_model_trial", _PATH)
trial = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trial)


def _beam(kinds, magnitude=-10.0, z2=0.0):
    nodes = [{"name": "A", "x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0},
             {"name": "B", "x_mm": 6000.0, "y_mm": 0.0, "z_mm": z2}]
    return {
        "nodes": nodes,
        "beams": [{"name": "L", "n1": "A", "n2": "B", "section": "S"}],
        "supports": [{"name": f"S{i}", "node": "AB"[i], "kind": k} for i, k in enumerate(kinds)],
        "loads": [{"name": "q", "case": "LC", "kind": "line", "target": "L",
                   "magnitude_kN_per_m": magnitude, "direction": "Z"}],
    }


_SECTION = [{"tool": "add_rectangular_section", "ok": True,
             "args": {"name": "S", "material": "C", "width_mm": 300, "height_mm": 500}}]


def test_simply_supported_beam_passes():
    _, exp = trial.PROMPTS["ligger"]
    assert all(trial.score(_beam(["pinned", "roller"]), exp, _SECTION).values())


def test_beam_clamped_at_both_ends_is_not_on_two_supports():
    _, exp = trial.PROMPTS["ligger"]
    checks = trial.score(_beam(["fixed", "fixed"]), exp, _SECTION)
    assert checks["support_kind"] is False


def test_upward_load_and_wrong_section_fail():
    _, exp = trial.PROMPTS["ligger"]
    checks = trial.score(_beam(["pinned", "roller"], magnitude=10.0), exp, [])
    assert checks["load"] is False and checks["section"] is False


def test_missing_results_are_not_counted_as_model_errors():
    events = [{"tool": "get_beam_inner_forces", "ok": False,
               "error": "no analysis results available - call run_analysis first"},
              {"tool": "add_beam", "ok": False, "error": "KeyError: 'N9'"}]
    assert trial._model_errors(events) == 1


def test_a_run_only_passes_with_a_correct_model_and_a_real_answer():
    good = {"nodes": True, "load": True}
    assert trial.judge(good, "Klaar.", None) == {"built": True, "answered": True, "passed": True}
    assert trial.judge(good, "   ", None)["passed"] is False
    assert trial.judge(good, "[ERROR] The model stopped", None)["passed"] is False
    assert trial.judge(good, "Klaar.", "RuntimeError: x")["passed"] is False
    assert trial.judge({"nodes": False}, "Klaar.", None) == \
        {"built": False, "answered": True, "passed": False}
    assert trial.judge({}, "Klaar.", None)["built"] is False


def test_continuous_beam_spans_must_be_in_place_mirrored_or_not():
    _, exp = trial.PROMPTS["doorgaand"]
    def nodes(xs):
        return [{"name": f"N{i}", "x_mm": float(x), "y_mm": 0.0, "z_mm": 0.0} for i, x in enumerate(xs)]
    assert trial._stations(nodes([0, 5000, 12000])) == (0, 5000, 12000)
    for xs, ok in (([0, 5000, 12000], True), ([0, 7000, 12000], True), ([0, 6000, 12000], False)):
        snap = {"nodes": nodes(xs), "beams": [], "supports": [], "loads": []}
        assert trial.score(snap, exp, [])["stations"] is ok
