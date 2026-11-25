"""The calculation of a chat-built model on the live route. Needs SCIA, a start model and LLSM_LIVE=1.

docs/verify/calc-run.md: the 2 x 12 m beam (9 kN/m on both spans) is pushed into a copy of LLSM's start
model and calculated by SCIA's batch program while LLSM's SCIA stays open. The moment at the middle support
is the hand value q L^2 / 8 = 162 kNm within 1 %. Then the load goes to 12 kN/m: the old result is gone,
and the new calculation gives 216 kNm within 1 %. The variable case is stored with SCIA's Static type.
"""
import os
import time

import pytest

from llsm.scia import start_model, tools
from llsm.scia.esa_xml import running_scia
from llsm.scia.loader import load as load_scia

pytestmark = pytest.mark.skipif(os.environ.get("LLSM_LIVE") != "1", reason="set LLSM_LIVE=1 to run against SCIA")


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


def _my_at_b(ctx) -> float:
    forces = tools.get_beam_inner_forces(ctx, "Veld1", "Verkeer-UDL")
    assert forces["ok"], forces
    i = forces["positions_m"].index(12.0)
    return abs(forces["values_kN_kNm"]["My (kNm)"][i])


def test_two_span_beam_is_calculated_and_follows_an_edit(ctx):
    steps = [
        tools.new_model(ctx, name="calc_live"),
        tools.add_material(ctx, "Beton ligger", kind="Concrete"),
        tools.add_rectangular_section(ctx, "R1000x600 ligger", material="Beton ligger", width_mm=1000, height_mm=600),
        tools.add_node(ctx, "A", 0, 0, 0), tools.add_node(ctx, "B", 12000, 0, 0), tools.add_node(ctx, "C", 24000, 0, 0),
        tools.add_beam(ctx, "Veld1", "A", "B", "R1000x600 ligger"), tools.add_beam(ctx, "Veld2", "B", "C", "R1000x600 ligger"),
        tools.add_pinned_support(ctx, "Sup-A", "A"),
        tools.add_roller_support(ctx, "Sup-B", "B", "Z"), tools.add_roller_support(ctx, "Sup-C", "C", "Z"),
        tools.add_load_group(ctx, "LG-Verkeer", kind="Variable"),
    ]
    assert all(s["ok"] for s in steps), steps
    assert tools.add_material(ctx, "C30/37", kind="Concrete")["reserved"] is True  # a name of the start model
    case = tools.add_load_case(ctx, "Verkeer-UDL", group="LG-Verkeer")
    assert case["ok"] and case["kind"] == "Static" and case["action_type"] == "Variable", case
    for beam in ("Veld1", "Veld2"):
        assert tools.add_line_load(ctx, f"q-{beam}", beam=beam, load_case="Verkeer-UDL", magnitude_kn_per_m=-9)["ok"]

    for attempt in range(3):  # SCIA's licence check sometimes takes over its 3-minute start limit
        try:
            assert tools.launch_scia(ctx, visible=False)["ok"]
            break
        except RuntimeError:
            if attempt == 2:
                raise
    pushed = tools.push_model(ctx)
    assert pushed["ok"] and pushed["start_model"] is True and pushed["calc_estimate_s"] > 0, pushed

    calc = _calculate(ctx)
    print("run_analysis:", calc)
    assert calc["ok"] is True and calc["calculated"] is True, calc
    assert _my_at_b(ctx) == pytest.approx(9 * 12 ** 2 / 8, rel=0.01)
    assert tools.get_beam_deflection(ctx, "Veld1", "Verkeer-UDL")["reliable"] is False

    for beam in ("Veld1", "Veld2"):
        assert tools.set_line_load_magnitude(ctx, f"q-{beam}", -12)["ok"]
    assert tools.get_beam_inner_forces(ctx, "Veld1", "Verkeer-UDL")["ok"] is False  # the old result is gone
    again = _calculate(ctx)
    assert again["ok"] is True, again
    assert _my_at_b(ctx) == pytest.approx(12 * 12 ** 2 / 8, rel=0.01)
