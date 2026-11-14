# Vendored: traffic rules of the plaatbruggen toetsmodel

- Repository: https://github.com/Amsterdam/automatisch-toetsmodel-plaatbruggen
- Commit: `e8e44e73bc6c502dc03e385c8f79605a4abe46b2` (2026-07-01)
- Made by: `python scripts/vendor_toetsmodel_rules.py <checkout> --commit e8e44e7`. Nobody edits these
  files by hand: a rule change goes upstream, then this script runs again and the tests show what moved.
- Upstream requires Python 3.14. The copy was imported and run under 3.11.0 (same positions and lanes);
  LLSM's tests run it on 3.12.

## Whole files (equal to the original after one import rewrite; line endings as git checks them out)

`from src.integrations.scia_integration.` → `from llsm.traffic_rules._toetsmodel.`

- `src/integrations/scia_integration/constants/geometry.py` → `constants/geometry.py`
- `src/integrations/scia_integration/constants/loads.py` → `constants/loads.py`
- `src/integrations/scia_integration/constants/vehicles.py` → `constants/vehicles.py`
- `src/integrations/scia_integration/load_system/tandem_sequencer.py` → `load_system/tandem_sequencer.py`

## Single functions

Their modules also import VIKTOR, plotly (through `src.common.constants.parametrization`), `src.common.materials`
or the app's params, so only the functions are copied, each as its exact source lines. `lanes.py` starts with
a generated import of the vendored constants they use.

- `src/integrations/scia_integration/load_system/lane_calculations.py` :: `amount_of_notional_lanes` → `lanes.py`
- `src/integrations/scia_integration/load_system/theoretical_tandem_generators.py` :: `generate_theoretical_lane_positions_bg8000` → `lanes.py`
- `src/integrations/scia_integration/load_system/theoretical_tandem_generators.py` :: `generate_theoretical_lane_positions_bg9000` → `lanes.py`
- `src/integrations/scia_integration/load_system/theoretical_tandem_generators.py` :: `generate_theoretical_lane_positions_bg10000` → `lanes.py`

Not copied: `real_tandem_generators._generate_lanes_*_strategy*` (only used for two road zones, which LLSM
v0.4.0 does not offer) and the `generate_real_lane_positions_*` wrappers (they read the road zone from the app's
params). For one carriageway those wrappers compute the theoretical lanes over the road strip, shifted to its
edge; LLSM does exactly that with the functions above (compared in docs/verify/phase4-traffic-rules.md).
