"""The vendored toetsmodel rules: the same as upstream, importable without VIKTOR, and its own expectations.

The equality test needs the toetsmodel checkout (read-only) at the pinned commit; without it, it skips.
"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from llsm.traffic_rules._toetsmodel.lanes import (
    amount_of_notional_lanes,
    generate_theoretical_lane_positions_bg8000,
    generate_theoretical_lane_positions_bg9000,
    generate_theoretical_lane_positions_bg10000,
)
from llsm.traffic_rules._toetsmodel.load_system.tandem_sequencer import (
    TANDEM_WHEEL_OFFSETS,
    calculate_start_of_lanes,
    tandem_system_sequencer,
)

REPO = Path(__file__).resolve().parents[2]
CHECKOUT = Path(r"C:\Development\ctrl+b\automatisch-toetsmodel-plaatbruggen")
SCRIPT = REPO / "scripts" / "vendor_toetsmodel_rules.py"


def _script():
    spec = importlib.util.spec_from_file_location("vendor_toetsmodel_rules", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # its dataclass looks itself up there
    spec.loader.exec_module(module)
    return module


def _at_pinned_commit() -> bool:
    if not (CHECKOUT / ".git").exists():
        return False
    head = subprocess.run(["git", "-C", str(CHECKOUT), "rev-parse", "HEAD"], capture_output=True, text=True)
    return head.returncode == 0 and head.stdout.startswith(_script().PINNED)


@pytest.mark.skipif(not _at_pinned_commit(), reason="toetsmodel checkout at the pinned commit not on this PC")
def test_copy_equals_upstream():
    vendor = _script()
    assert vendor.differences(vendor.build(CHECKOUT)) == []


def test_copy_imports_without_viktor_or_plotly():
    code = (
        "import sys; sys.modules['viktor'] = None; sys.modules['plotly'] = None; "
        "import llsm.traffic_rules._toetsmodel.lanes, llsm.traffic_rules._toetsmodel.load_system.tandem_sequencer, "
        "llsm.traffic_rules._toetsmodel.constants.vehicles, llsm.traffic_rules.presets"
    )
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert run.returncode == 0, run.stderr


def test_no_hand_edits_marker():
    text = (REPO / "src/llsm/traffic_rules/_toetsmodel/lanes.py").read_text(encoding="utf-8")
    assert "do not edit" in text and text.count("# --- src/integrations/scia_integration/") == 4


# --- the toetsmodel's own test (tests/test_src/test_integrations/test_scia_loads.py) -----------------

def test_amount_of_notional_lanes():
    assert amount_of_notional_lanes(5.0) == (1, 3)
    lanes, width = amount_of_notional_lanes(5.7)
    assert lanes == 2 and abs(width - 2.85) < 0.001
    assert amount_of_notional_lanes(9.0) == (3, 3)
    assert amount_of_notional_lanes(15.0) == (5, 3)


@pytest.mark.parametrize("y_top, width, bg8000, bg9000", [
    (10.0, 9.0, [2.5, 5.5, 8.5], [8.5, 5.5, 2.5]),
    (5.0, 3.0, [3.5], [3.5]),
])
def test_real_lanes_are_theoretical_lanes_on_the_road_strip(y_top, width, bg8000, bg9000):
    """The toetsmodel's expectations for generate_real_lane_positions_bg8000/9000 (one road zone), met by the
    theoretical functions over the road strip shifted to its edge: how LLSM lays out 'real' lanes."""
    bottom = y_top - width
    assert [bottom + y for y in generate_theoretical_lane_positions_bg8000(width)] == pytest.approx(bg8000)
    assert [bottom + y for y in generate_theoretical_lane_positions_bg9000(width)] == pytest.approx(bg9000)


# --- what the copy gives, measured on the toetsmodel itself (docs/verify/phase4-traffic-rules.md) ----

def test_sequencer_on_the_demo_plate():
    """6 m deck, 0.3 m thick, the generators' vehicle length 1.6 m."""
    assert tandem_system_sequencer(6.0, 0.3, length_vehicle=1.6) == [
        -1.33, -0.83, -0.33, 0.17, 0.67, 1.17, 2.17, 3.0, 3.17, 4.17, 4.67, 5.17, 5.67, 5.73,
    ]
    assert calculate_start_of_lanes(0.3) == pytest.approx(0.27)


def test_sequencer_counts():
    assert len(tandem_system_sequencer(20.0, 0.8, length_vehicle=1.6)) == 26
    assert len(tandem_system_sequencer(20.0, 0.8, length_vehicle=1.6, support_x_coords=[0.0, 10.0, 20.0])) == 30


def test_lane_centres():
    assert generate_theoretical_lane_positions_bg8000(4.0) == [1.5]
    assert generate_theoretical_lane_positions_bg9000(4.0) == [2.5]
    assert generate_theoretical_lane_positions_bg10000(4.0) == [2.0]
    assert generate_theoretical_lane_positions_bg8000(10.0) == [1.5, 4.5, 7.5]
    assert generate_theoretical_lane_positions_bg10000(10.0) == [5.0, 2.0, 8.0]


def test_wheel_offsets():
    assert TANDEM_WHEEL_OFFSETS == [(0, 0), (1.2, 0), (0, 2.0), (1.2, 2.0)]
