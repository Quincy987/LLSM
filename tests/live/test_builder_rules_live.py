"""Build a beam through the tools, push it to SCIA, calculate. Needs SCIA, a licence and LLSM_LIVE=1.

Checks on the live route what tests/unit/test_builder_rules.py checks without SCIA: a load case takes
its action type from its group (Variable here, Accidental for a Seismic group), a duplicate name is
refused and nothing lands, and the model still pushes and calculates. docs/verify/builder-rules.md.
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
    assert case["ok"] and case["action_type"] == "Variable", case
    quake = tools.add_load_case(ctx, "LC_eq", "LG_eq")
    assert quake["ok"] and quake["action_type"] == "Accidental", quake
    assert tools.add_line_load(ctx, "q1", beam="B1", load_case="LC_q", magnitude_kn_per_m=-5.0)["ok"]
    assert tools.add_line_load(ctx, "q2", beam="B1", load_case="LC_eq", magnitude_kn_per_m=-1.0)["ok"]

    before = tools.model_summary(ctx)
    dup = tools.add_node(ctx, "N1", 3000, 0, 0)
    assert dup["ok"] is False and "already exists" in dup["error"], dup
    after = tools.model_summary(ctx)
    assert after["items"] == before["items"] and after["names"].count("N1") == 1

    assert ctx.builder.get("LC_q").ActionType.ToString() == "Variable"
    assert tools.launch_scia(ctx, visible=False)["ok"]
    pushed = tools.push_model(ctx)
    assert pushed["ok"], pushed
    calc = tools.run_analysis(ctx)
    assert calc["ok"], calc
