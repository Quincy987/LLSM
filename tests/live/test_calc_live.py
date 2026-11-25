"""The calculation of a chat-built model on the live route. Needs SCIA, a start model and LLSM_LIVE=1.

docs/verify/calc-run.md: beams on a pin and Z rollers, materials with a library grade, pushed into a copy of
LLSM's start model and calculated by SCIA's batch program while LLSM's SCIA stays open. LLSM holds the
supports in the directions no load acts in (stability.py), so the beams cannot twist or swing.

Hand values, 9 kN/m down, 1000 x 600 mm (I = 0.018 m4): single span 12 m M = q L^2 / 8 = 162 kNm,
deflection 5 q L^4 / (384 E I) = 4.11 mm for C30/37 (E 32.8 GPa) and 0.64 mm for S 235 (210 GPa); two spans
2 x 12 m M_B = 162 kNm, largest deflection 0.00542 q L^4 / (E I) = 1.71 mm for C30/37. Moments within 1 %,
deflections within 3 % (SCIA includes shear deformation; measured 4.14 / 0.647 / 1.734 mm in try 20).
Then the load goes to 12 kN/m: the old result is gone, the new two-span moment is 216 kNm.
"""
import os
import time

import pytest

from llsm.scia import start_model, tools
from llsm.scia.esa_xml import running_scia
from llsm.scia.loader import load as load_scia

pytestmark = pytest.mark.skipif(os.environ.get("LLSM_LIVE") != "1", reason="set LLSM_LIVE=1 to run against SCIA")
E = {"C30/37": 32.837e9, "S 235": 210e9}
I = 1.0 * 0.6 ** 3 / 12


@pytest.fixture
def ctx():
    if running_scia():
        pytest.skip("a SCIA is running; never touch someone's session")
    if start_model.find() is None:
        pytest.skip("no LLSM start model on this computer (llsm start-model <template.esa>)")
    load_scia()
    context = tools.LlsmContext()
    yield context
    tools.close_scia(context)


def _calculate(ctx) -> dict:
    out = tools.run_analysis(ctx, confirm=True)
    deadline = time.time() + 900
    while out.get("status") == "running" and time.time() < deadline:
        out = tools.run_analysis(ctx, confirm=True)
    return out


def _my(ctx, beam, case, at_m) -> float:
    forces = tools.get_beam_inner_forces(ctx, beam, case)
    assert forces["ok"], forces
    return abs(forces["values_kN_kNm"]["My (kNm)"][forces["positions_m"].index(at_m)])


def _beam(ctx, tag, y, section, spans, case):
    nodes = [f"{tag}{i}" for i in range(spans + 1)]
    for i, n in enumerate(nodes):
        assert tools.add_node(ctx, n, 12000 * i, y, 0)["ok"]
    for i in range(spans):
        assert tools.add_beam(ctx, f"{tag}-V{i + 1}", nodes[i], nodes[i + 1], section)["ok"]
    assert tools.add_pinned_support(ctx, f"{tag}-S0", nodes[0])["ok"]
    for n in nodes[1:]:
        assert tools.add_roller_support(ctx, f"{tag}-S{n[-1]}", n, "Z")["ok"]
    assert tools.add_load_case(ctx, case, group="LG-Verkeer")["ok"]
    for i in range(spans):
        assert tools.add_line_load(ctx, f"q-{tag}-{i + 1}", beam=f"{tag}-V{i + 1}", load_case=case,
                                   magnitude_kn_per_m=-9)["ok"]


def test_pin_and_roller_beams_with_graded_materials_match_the_hand_values(ctx):
    assert tools.new_model(ctx, name="calc_live")["ok"]
    concrete = tools.add_material(ctx, "C30/37")  # a name that is a grade: renamed inside SCIA (try 20 P2)
    assert concrete["ok"] and concrete["grade"] == "C30/37" and concrete["scia_name"] == "C30/37 (LLSM)", concrete
    steel = tools.add_material(ctx, "Staal ligger", grade="S235")
    assert steel["ok"] and steel["grade"] == "S 235" and steel["kind"] == "Steel", steel
    assert tools.add_material(ctx, "Fout", kind="Steel", grade="C30/37")["ok"] is False  # SCIA would use steel
    assert tools.add_rectangular_section(ctx, "R-beton", material="C30/37", width_mm=1000, height_mm=600)["ok"]
    assert tools.add_rectangular_section(ctx, "R-staal", material="Staal ligger", width_mm=1000, height_mm=600)["ok"]
    assert tools.add_load_group(ctx, "LG-Verkeer", kind="Variable")["ok"]
    _beam(ctx, "E", 0, "R-beton", 1, "BG-enkel")
    _beam(ctx, "T", 30000, "R-beton", 2, "BG-twee")
    _beam(ctx, "S", 60000, "R-staal", 1, "BG-staal")
    case = ctx.builder.get("BG-twee")
    assert str(case.LoadType) == "Static" and str(case.ActionType) == "Variable"

    for attempt in range(3):  # SCIA's licence check sometimes takes over its 3-minute start limit
        try:
            assert tools.launch_scia(ctx, visible=False)["ok"]
            break
        except RuntimeError:
            if attempt == 2:
                raise
    pushed = tools.push_model(ctx)
    assert pushed["ok"] and pushed["start_model"] is True, pushed
    assert pushed["also_held"]["E-S1"] == ["Y"] and pushed["also_held"]["E-S0"] == ["RX"], pushed
    assert "forces do not change" in pushed["stability_note"]

    calc = _calculate(ctx)
    print("run_analysis:", calc)
    assert calc["ok"] is True and calc["calculated"] is True and "material_note" not in calc, calc
    assert _my(ctx, "E-V1", "BG-enkel", 6.0) == pytest.approx(162, rel=0.01)
    assert _my(ctx, "T-V1", "BG-twee", 12.0) == pytest.approx(162, rel=0.01)
    assert _my(ctx, "S-V1", "BG-staal", 6.0) == pytest.approx(162, rel=0.01)
    for beam, case, grade, hand in (("E-V1", "BG-enkel", "C30/37", 5 / 384), ("S-V1", "BG-staal", "S 235", 5 / 384),
                                    ("T-V1", "BG-twee", "C30/37", 0.00542)):
        defl = tools.get_beam_deflection(ctx, beam, case)
        assert defl["ok"] and defl["reliable"], defl
        expected = hand * 9e3 * 12 ** 4 / (E[grade] * I) * 1e3
        assert abs(defl["model_extreme"]["uz_mm"]) == pytest.approx(expected, rel=0.03), (beam, defl)

    for i in (1, 2):
        assert tools.set_line_load_magnitude(ctx, f"q-T-{i}", -12)["ok"]
    assert tools.get_beam_inner_forces(ctx, "T-V1", "BG-twee")["ok"] is False  # the old result is gone
    again = _calculate(ctx)
    assert again["ok"] is True, again
    assert _my(ctx, "T-V1", "BG-twee", 12.0) == pytest.approx(216, rel=0.01)
