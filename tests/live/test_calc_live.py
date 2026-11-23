"""The calculation answer on the live route. Needs SCIA, a licence and LLSM_LIVE=1.

docs/verify/calc-run.md: the test beam (2 x 12 m, -9 kN/m variable) is pushed and calculated. Either SCIA
calculated, and the moment at the middle support is the hand value q L^2 / 8 = 162 kNm within 1 %, or it
did not, and run_analysis and both result tools say so with the same plain message. Never ok without
results. The variable case is stored with SCIA's Static load type.
"""
import os

import pytest

from llsm.scia import tools
from llsm.scia.esa_xml import running_scia
from llsm.scia.loader import load as load_scia

pytestmark = pytest.mark.skipif(os.environ.get("LLSM_LIVE") != "1", reason="set LLSM_LIVE=1 to run against SCIA")

HAND_MY_B_KNM = 9 * 12 ** 2 / 8


@pytest.fixture
def ctx():
    if running_scia():
        pytest.skip("a SCIA is running; never touch someone's session")
    load_scia()
    context = tools.LlsmContext()
    yield context
    tools.close_scia(context)


def test_two_span_beam_calculates_or_says_it_did_not(ctx):
    steps = [
        tools.new_model(ctx, name="calc_live"),
        tools.add_material(ctx, "C30/37", kind="Concrete"),
        tools.add_rectangular_section(ctx, "R1000x600", material="C30/37", width_mm=1000, height_mm=600),
        tools.add_node(ctx, "A", 0, 0, 0), tools.add_node(ctx, "B", 12000, 0, 0), tools.add_node(ctx, "C", 24000, 0, 0),
        tools.add_beam(ctx, "Veld1", "A", "B", "R1000x600"), tools.add_beam(ctx, "Veld2", "B", "C", "R1000x600"),
        tools.add_pinned_support(ctx, "Sup-A", "A"),
        tools.add_roller_support(ctx, "Sup-B", "B", "Z"), tools.add_roller_support(ctx, "Sup-C", "C", "Z"),
        tools.add_load_group(ctx, "LG-Verkeer", kind="Variable"),
    ]
    assert all(s["ok"] for s in steps), steps
    case = tools.add_load_case(ctx, "Verkeer-UDL", group="LG-Verkeer")
    assert case["ok"] and case["kind"] == "Static" and case["action_type"] == "Variable", case
    for beam in ("Veld1", "Veld2"):
        assert tools.add_line_load(ctx, f"q-{beam}", beam=beam, load_case="Verkeer-UDL", magnitude_kn_per_m=-9)["ok"]

    assert tools.launch_scia(ctx, visible=False)["ok"]
    assert tools.push_model(ctx)["ok"]
    calc = tools.run_analysis(ctx)
    print("run_analysis:", calc)
    assert calc["ok"] is calc["calculated"], calc
    forces = tools.get_beam_inner_forces(ctx, "Veld1", "Verkeer-UDL")
    if not calc["ok"]:
        assert calc["error"] == tools.NOT_CALCULATED, calc
        assert forces == {"ok": False, "calculated": False, "error": tools.NOT_CALCULATED}, forces
        return
    assert forces["ok"], forces
    my_b_knm = abs(forces["magnitudes"]["My"][-1]) / 1000
    assert abs(my_b_knm - HAND_MY_B_KNM) <= 0.01 * HAND_MY_B_KNM, my_b_knm
