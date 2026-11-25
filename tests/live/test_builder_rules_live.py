"""Build a beam through the tools, push it to SCIA, calculate. Needs SCIA, a licence and LLSM_LIVE=1.

Checks on the live route what tests/unit/test_builder_rules.py checks without SCIA: a load case takes
its action type from its group (Variable with a Short duration here, Accidental for a Seismic group) and
really lands in the model, a duplicate name is refused and nothing lands, and the model still pushes and calculates. docs/verify/builder-rules.md.
"""
import os

import pytest

from llsm.scia import tools
from llsm.scia.esa_xml import running_scia
from llsm.scia.loader import load as load_scia

pytestmark = pytest.mark.skipif(os.environ.get("LLSM_LIVE") != "1", reason="set LLSM_LIVE=1 to run against SCIA")


@pytest.fixture
def ctx():
    if running_scia():
        pytest.skip("a SCIA is running; never touch someone's session")
    load_scia()
    context = tools.LlsmContext()
    yield context
    tools.close_scia(context)


def test_action_type_and_duplicates_on_the_live_route(ctx):
    steps = [
        tools.new_model(ctx, name="builder_rules"),
        tools.add_material(ctx, "S235", kind="Steel"),
        tools.add_rectangular_section(ctx, "R200", material="S235", width_mm=200, height_mm=200),
        tools.add_node(ctx, "N1", 0, 0, 0),
        tools.add_node(ctx, "N2", 6000, 0, 0),
        tools.add_beam(ctx, "B1", node_a="N1", node_b="N2", cross_section="R200"),
        tools.add_pinned_support(ctx, "S1", node="N1"),
        tools.add_roller_support(ctx, "S2", node="N2", direction="Z"),
        tools.add_load_group(ctx, "LG_var", kind="Variable"),
        tools.add_load_group(ctx, "LG_eq", kind="Seismic"),
    ]
    assert all(s["ok"] for s in steps), steps

    case = tools.add_load_case(ctx, "LC_q", "LG_var")
    assert case["ok"] and case["action_type"] == "Variable" and case["duration"] == "Short", case
    quake = tools.add_load_case(ctx, "LC_eq", "LG_eq")
    assert quake["ok"] and quake["action_type"] == "Accidental", quake
    names = tools.model_summary(ctx)["names"]
    assert "LC_q" in names and "LC_eq" in names  # ADM drops a variable case without a duration silently
    assert tools.add_line_load(ctx, "q1", beam="B1", load_case="LC_q", magnitude_kn_per_m=-5.0)["ok"]
    assert tools.add_line_load(ctx, "q2", beam="B1", load_case="LC_eq", magnitude_kn_per_m=-1.0)["ok"]

    before = tools.model_summary(ctx)
    dup = tools.add_node(ctx, "N1", 3000, 0, 0)
    assert dup["ok"] is False and "already exists" in dup["error"], dup
    after = tools.model_summary(ctx)
    assert after["items"] == before["items"] and after["names"].count("N1") == 1

    assert ctx.builder.get("LC_q").ActionType.ToString() == "Variable"
    for attempt in range(3):  # SCIA's licence check sometimes takes over its 3-minute start limit
        try:
            assert tools.launch_scia(ctx, visible=False)["ok"]
            break
        except RuntimeError:
            if attempt == 2:
                raise
    pushed = tools.push_model(ctx)
    assert pushed["ok"], pushed
    calc = tools.run_analysis(ctx, confirm=True)
    while calc.get("status") == "running":
        calc = tools.run_analysis(ctx, confirm=True)
    # ok only with results, never ok without (docs/verify/calc-run.md). Steel S 235 on a pin and a roller:
    # LLSM holds the roller sideways and the pin against twisting, so it calculates (try 20). Without a
    # start model nothing is calculated at all.
    assert calc["ok"] is calc.get("calculated"), calc
    from llsm.scia import start_model
    if start_model.find() is None:
        assert calc["error"] == tools.NOT_CALCULATED, calc
        return
    assert calc["ok"] is True and pushed["also_held"] == {"S2": ["Y"], "S1": ["RX"]}, (calc, pushed)
    forces = tools.get_beam_inner_forces(ctx, "B1", "LC_q")
    i = forces["positions_m"].index(3.0)
    assert forces["values_kN_kNm"]["My (kNm)"][i] == pytest.approx(5 * 6 ** 2 / 8, rel=0.01)  # 22.5 kNm
