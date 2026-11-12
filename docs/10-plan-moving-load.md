# Plan v2 — traffic loads on an existing SCIA model, with the toetsmodel's rules

Date: 2026-11-13. Status: **plan, nothing built, nothing agreed with Geert yet** (§6 first).
v1 of this plan (2026-11-12) shipped as v0.3.11, verified in `docs/verify/phase3-moving-load.md`;
its text is in git history (`74d03d0`). Prior attempts: v1 itself (`src/llsm/moving_load/`); no
earlier traffic-rule work in LLSM (`git log -i --grep=traffic` / `--grep=lane` empty).

## 0. Why v2, and the rule that follows
- Geert (mail 2026-11-12): "dit hebben we al helemaal geautomatiseerd in ons automatisch toetsmodel
  plaatbruggen … De broncode in python zouden we eventueel ook om kunnen bouwen of kunnen gebruiken in
  andere applicaties."
- Tom: user-friendly, and as a replacement for SCIA's 2D traffic-load module (Traffic Loads 2D).
- Sebastiaan: the components are known and tested; give LLSM the instructions of those components.

**Rule:** the AI never calculates traffic rules. It picks a tested component (the toetsmodel's) and fills
in the input. Reuse only the **rules** — which vehicles, where the lanes lie, which positions — not the
assessment (unity checks, IDEA, report). Difference that shapes the adapter: the toetsmodel builds its
own rectangular model from parameters; LLSM puts loads into the engineer's own saved model.

Honest limit for Tom: SCIA's module searches governing positions on influence surfaces; this is
position stepping, as the toetsmodel does. It replaces the copying and the licence, not the search.

## 1. What v0.3.11 already does (stays as is)
Custom axle list or LM1 tandem (lane 1/2/3, α default 1.0); path of two points or a polyline in any
direction; one case per position in one exclusive group `LLSM-ML <name>`; wheels as **free point
loads** on a plate (or per axle on a beam deck); new file version; read back and compared. Tools
`moving_load_preview` / `moving_load_apply`, CLI `llsm moving-load`. All of this remains the
"custom vehicle" route in v2.

## 2. What the toetsmodel has (read-only, `Amsterdam/automatisch-toetsmodel-plaatbruggen`, EUPL-1.2, commit `e8e44e7` 2026-07-01, CHANGELOG v0.0.34)
Import check (static, transitive, `TYPE_CHECKING` imports excluded): 13 of 110 `src` files import
`viktor`. The load modules do not import it directly, but several reach it through
`src/geometry/load_zone_geometry.py` or `scia_enums.py`.

**A — pure, usable as is** (no viktor, no params object):
- `src/data_models/vehicle_models.py`: pydantic models (Tandem, Service, Accidental, Amsterdam
  accidental, Tram), only `pydantic`. Not used by the production code (only exported).
- `scia_integration/constants/vehicles.py`, `geometry.py`, `loads.py`: plain numbers; **these** are what
  the production code uses.
- `load_system/tandem_sequencer.py` → `tandem_system_sequencer(length, thickness, length_vehicle,
  spacing=1.0, support_x_coords)`: the position rule, 1D, metres.
- `load_system/lane_calculations.py` → `amount_of_notional_lanes(width)`.
- `load_system/theoretical_tandem_generators.py` → `generate_theoretical_lane_positions_bg8000/9000/10000(
  width, lane_width, zone3_width, zone2_width)` (lane centre y). The `tandem_systems_theoretical_lanes_*`
  wrappers are not pure: they read supports from params via a lazy import that reaches viktor.
- `real_tandem_generators._generate_lanes_bg8000/9000/10000_strategy*(y_top, width, n, lane_width)`:
  the "real lanes" rule, pure helpers (private names).
- `load_combination_generator/` (pydantic only) and `results/scia_integration_strips_to_idea.py`
  (pandas only): see docs/12 and docs/11.

**B — rule wanted, code tied to `BridgeParametrization` and/or viktor:**
- `real_tandem_generators.generate_real_lane_positions_*` (lanes from the "Auto" road zone),
  `udl_generators.create_*_udl_traffic_loads` (lane strips per span), `scia_point_loads.add_service_vehicle_loads`,
  `add_accidental_vehicle_loads`, `add_tram_loads`, `dispersal_function` (+ `get_dispersion_at_coord`),
  `scia_load_cases._extract_support_x_coordinates`. They read `params.bridge_segments_array`,
  `load_zones_data_array`, tram tracks, `spreiding`, and write through `SciaModelBuilder`.

**C — not reused:** model building, results, IDEA interface, report, `app/`.

**Packaging:** top-level packages are named `src` and `app`, version comes from `viktor.__version__`,
`requires-python >= 3.14` (LLSM: ≥ 3.11). It cannot be a pip dependency of LLSM as it stands.

**The rules, as the running code has them** (metres, kN):
- *Tandem (LM1 TS):* 4 wheels 0.4 × 0.4, 1.2 apart along, 2.0 across. Each case holds a unit load of
  100 kN per wheel (625 kN/m²); lane factor 3 / 2 / 1 and ψ · α_trend · α_q (NEN 8701 / EN 1991-2) are
  applied in the combination, not in the case.
- *UDL:* unit 2.5 kN/m², factor 3.6 on lane 1 (9.0 kN/m²), 1.0 on other lanes and the rest area; one
  case per lane (or rest area) × span × configuration.
- *Lanes:* width < 5.4 → 1 lane of 3.0; 5.4–6.0 → 2 lanes of width/2; else ⌊w/3⌋ lanes of 3.0, rest area.
  Configurations: BG8000 counted from one edge, BG9000 from the other, BG10000 lane 1 in the centre.
  "Theoretical" lanes over the deck width; "real" lanes inside the "Auto" road zone(s).
- *Positions:* start 0.9 · t from the deck end (45° spread through the deck), step 1.0; within
  ±1.0 of every support the step is 0.5; every mid-span and the end are always included.
- *Service vehicle:* 2 axles of 25 kN, 3.0 apart, wheel track 1.75, wheel 0.25, outer wheel centre
  0.5 from each deck edge (both edges).
- *Accidental, standard:* axles 80 + 40 kN, 3.0 apart, track 1.3, wheel 0.2, 0.5 from the edge,
  driving forward and reversed.
- *Accidental, Amsterdam:* one 240 kN axle, wheels 2.0 apart, wheel 0.4, 0.5 from the edge, also rotated.
- *Tram (CAF Urbos 100):* 6 × 97 kN, axle spacings 1.8 / 8.187 / 1.85 / 8.187 / 1.8, gauge 1.435,
  Φ = 1.40 − L/500 ≥ 1.0 applied in the case, on given track centre lines.
- *Dispersal (optional, `spreiding`):* per wheel, half the deck-zone dispersion + the load-zone
  (pavement) dispersion, max 0.5 per side, the smallest over the four corners; along the road only for
  axle loads; clipped to the deck; total force kept (pressure scaled down).

## 3. How LLSM would use it — form and adapter

### 3.1 Form: a pinned, vendored copy of the A files (now); a shared package (later, if Geert agrees)
- Copy the A files only into `src/llsm/traffic_rules/_toetsmodel/`, unchanged except one mechanical
  import rewrite (`from src.` → `from llsm.traffic_rules._toetsmodel.`), done by
  `scripts/vendor_toetsmodel_rules.py <commit>`. The same script, run in a test, re-fetches the files
  at the pinned commit, applies the rewrite and requires byte equality. Nobody edits the copy;
  changes go upstream.
- `UPSTREAM.md` in that folder: repo URL, commit, date, file list, and the EUPL-1.2 licence text.
  **Licence note:** EUPL-1.2 is copyleft. The copied files keep their licence, and LLSM as
  distributed with them must be under EUPL-1.2 or a licence on the EUPL's compatibility list. LLSM has
  no LICENSE file yet: choosing EUPL-1.2 for LLSM is the simplest fit. To confirm with Geert (and, if
  needed, legal) before the first copy is committed.
- Run the toetsmodel's own tests for those files (`tests/test_vehicle_models.py`, the sequencer
  and lane parts of `test_scia_load_cases.py`) inside LLSM under Python 3.11.
- B rules are **not** re-implemented in LLSM. Note what that means: of the wheel layouts, only the
  tandem's is pure (`TANDEM_WHEEL_OFFSETS`); where the service, accidental, Amsterdam and tram wheels
  sit, and the UDL strips, live inside the B functions. v2 uses them once Geert's side splits each
  into a pure function (numbers in, deck coordinates out) with the params wrapper on top; LLSM would
  offer that refactor as a PR. Until then those vehicles wait (§4).
- Considered and not chosen for production: running the B functions unchanged in the toetsmodel's
  own environment (checkout + Python 3.14 venv + `viktor==14.29.1`) with a rectangular params stub
  and a recording `SciaModelBuilder` that captures each `create_surface_load` call. No re-implementation,
  but every engineer's PC would need that checkout and venv, and a params stub outside VIKTOR is
  fragile. Used **only for verification** (§5 check 2).
- Later: one small viktor-free package (vehicles, lanes, sequencer, dispersal, combination rules,
  Python ≥ 3.11, versioned) that both apps import; LLSM then pins a version instead of a commit.

### 3.2 Adapter: deck axes ↔ the engineer's model (`src/llsm/traffic_rules/frame.py`)
Toetsmodel frame: x along a rectangular deck from 0 to L, y across between two straight edges,
supports at known x, deck thickness t. LLSM builds that frame from the engineer's model:
- **Axis** = v1's road path (polyline, mm). Toetsmodel x ↦ chainage s along the path;
  y ↦ lateral offset from the axis. Point = `path.point_at(s) + offset · left_of(direction)`, as v1
  already places wheels. Each axle sits at its own chainage, so a kink in the path is handled as in v1.
- **Deck edges and carriageway** = two lateral offsets (left/right edge) and, for "real" lanes, the
  road strip between kerbs (stands in for the toetsmodel's "Auto" zone). Offered from the plate outline
  that v0.3.11 already reads; the engineer confirms or types them. Constant width only.
- **Supports** = chainages. Per lane, where the support line crosses that lane's centre line, so on a
  skewed deck the fine steps follow the skew lane by lane. Read from the model where the reader knows
  the supports; otherwise asked. Skew is LLSM's addition: the toetsmodel deck is rectangular.
- **t** = plate thickness from the model (shown, can be overridden); pavement thickness asked only
  when dispersal is on.
- Units: the rules work in m and kN; the adapter converts to LLSM's mm and kN in one place.
- Wheel patches are rotated with the local path direction (4 corners), not axis-aligned.

### 3.3 Contact areas: surface loads, not point loads
The toetsmodel writes every wheel as a free surface load (rectangle, N/m²); v0.3.11 writes free
point loads. v2 default on plates: wheel patches as **free surface loads**
(`DataAddLoad.EP_SurfaceForceFree.1`, already declared in `phase0/xml_route/defs/viktor_full.xml.def`;
to be trimmed into `src/llsm/scia/resources/loads.xml.def`, which now has only the point-force tables). Point loads stay as `contact="point"` for quick
global checks and are always used on beam decks (per axle on the member). Point loads give
unrealistic local plate peaks under the wheel; the preview says so when chosen on a plate.
Dispersal: v2.1, only once `dispersal_function`'s rule exists as a pure function upstream (§3.1).

### 3.4 Loads per case, names, groups
- Per case LLSM writes the **real** load (what the engineer would type: e.g. 150 kN per wheel for
  LM1 lane 1 at α = 1), and shows the factor that produced it (lane factor, α, Φ) in the preview.
  The toetsmodel's unit-load convention (100 kN, factors in the combination) only becomes an option
  if docs/12 decides to reuse its combination generator.
- Case description follows the toetsmodel title pattern (`rs 1 - Conf. A - x = 3.5 m`, `Span 2`), so
  its combination generator can parse LLSM's cases unchanged. Case names stay `LLSM-ML …`.
- One exclusive group per vehicle × lane × configuration. Combining lanes and vehicles is the
  combination step (docs/12), not this tool.

## 4. What v2 adds for Tom, and what stays out
**v2.0 — needs only the pure (A) files:**
- LM1 tandem, lanes 1–3, layout and loads from the vendored constants. The custom axle list stays.
- Notional lanes: theoretical over the carriageway, configurations A/B/C (BG8000/9000/10000); "real"
  lanes from the road strip through the pure `_generate_lanes_*` helpers (private names: ask Geert
  to make them public, or call the public wrapper once it is pure).
- Positions: the toetsmodel sequencer (1.0 step, 0.5 near supports, mid-spans, start at 0.9 · t)
  along each lane; v1's even step stays as an option.

**v2.1 — after the B rules are pure functions upstream (§3.1, §6):**
- Service vehicle (both edges), accidental standard (both edges, forward and reversed), accidental
  Amsterdam (normal and rotated), tram CAF Urbos 100 on track lines the engineer gives, with Φ.
- UDL: lane strips and rest area per span as free surface loads, one case each, as the toetsmodel.
- Dispersal.
- Until then Tom can enter these vehicles as a custom axle list (v1), with the values from the
  constants shown as a hint, clearly labelled "eigen invoer", not "volgens toetsmodel".

**Both:**
- One-line request, e.g. "LM1 met UDL en servicevoertuig op D1, rijbaan tussen de schampkanten,
  steunpunten uit het model" → preview: cases per vehicle/lane/configuration, total kN per case,
  rule source (`toetsmodel e8e44e7, tandem_sequencer.py`), and a top-view picture of deck, lanes and
  the first/mid/last position per vehicle, so Tom checks lanes without reading 300 rows.
- Case count: a 20 m single-span deck with 3 lanes and 3 configurations is ≈ 230 tandem cases. The
  200-cases-per-apply cap stays; one apply per vehicle family and configuration, shown up front.

**Out:** the assessment (UC, IDEA, report); generating combinations (docs/12); ψ / α_trend / α_q /
signage factors; LM2, LM3 (fatigue), LM4 crowd, braking and centrifugal forces; curved decks and
varying width; load distribution on beam grids; reading results / influence lines.

## 5. Verification (`docs/verify/phase4-traffic-rules.md`)
**Spike first (gate), on copies:** S9 one rotated free surface load in one update (exit 0, reopens,
value as N/m², corners as m); S10 reaction sum = patch area × pressure (±0.5 %); S11 read-back of
surface loads from the `.esa` (new record layout, counts checked against SCIA's object log).
Fail → v2 ships with point loads only and says so in every preview.

1. **Phase 3 hand case, again**: `plate_base.esa` (6000 × 4000 mm, columns C1–C4), axles 200 + 100 kN,
   path (500, 2000) → (4900, 2000), step 1600 → 3 cases. As point loads: unchanged result (regression).
   As 0.4 × 0.4 patches: each case −300 kN; the column forces add up to the loads and split per side
   as the hand calculation (within 0.5 %, as phase 3). Plus the LM1 tandem off-centre (wheel line
   at y = 1000 mm): column split by the lever rule, by hand.
2. **Position by position against the toetsmodel**: one rectangular deck (a bridge Geert supplies, or
   the params fixture from their own tests). A script run in the toetsmodel's own venv, read-only,
   dumps its cases (name, title, wheel corners, load) to JSON; LLSM plans the same deck with the
   same inputs. Compare per family: case count, x per case, lane centre y, wheel corners (±1 mm),
   load per wheel after the unit-convention mapping (§3.4), title. Pass = zero mismatches; every
   mismatch is listed, never averaged away. Skew is not compared (the toetsmodel has none); it is
   covered by check 1 on a 30° path.
3. Vendored-copy equality test and the toetsmodel's own tests for the copied files, under Python 3.11.
4. Read-back of every written load (position ± 0.1 mm, value exact, case and group right), as v1.

## 6. Agree with Geert first
- **Reuse at all**: EUPL allows it, but it is his team's component; ask before, not after.
- **Ownership**: the toetsmodel team owns the rules; LLSM is a consumer and never changes them;
  fixes go upstream (issue or PR).
- **Form**: pinned vendored copy now (§3.1); is he open to pure functions for the B rules, and later
  a small shared package? Who maintains that package?
- **Versioning**: what LLSM pins (commit, `dev-v0.0.x` tag or CHANGELOG version), and how a rule
  change reaches LLSM (CHANGELOG line, mail).
- **Licence**: LLSM under EUPL-1.2, fine by him?
- **Three code questions** (may be my misreading, so asked, not claimed):
  1. `vehicle_models.py` defaults differ from `constants/vehicles.py`, which the code uses (service
     axle spacing 2.5 vs 3.0 m and track 1.25 vs 1.75 m; accidental spacing 1.2 vs 3.0 m and track
     0.8 vs 1.3 m; Amsterdam track 1.5 vs 2.0 m). Which is the rule?
  2. Tandem: 100 kN per wheel × lane factor 3 = 300 kN per wheel, where EN 1991-2 gives 150 kN per wheel
     (300 kN per axle) for lane 1. Where does the ½ come in?
  3. Tram with `spreiding` off: all four corners are one point, area 0, so no load is created. Intended?
- **Test deck** for check 2, and may LLSM cite "volgens toetsmodel plaatbruggen <versie>" in previews?

Mail draft (Geert):
```
Hoi Geert,

Dank voor je tip over het toetsmodel. Ik wil de verkeersregels daaruit gebruiken in LLSM in plaats van zelf regels te verzinnen: welke voertuigen, waar de rijstroken liggen en op welke posities. De toetsing zelf niet; LLSM zet alleen de belastingen in het eigen SCIA-model van de constructeur.

Voordat ik iets bouw wil ik dit met je afspreken:
1. Mag dat, en blijven jullie eigenaar van de regels? LLSM past ze nooit zelf aan; wijzigingen gaan via jullie repo.
2. Vorm: ik neem een vaste kopie over van de losse bestanden zonder VIKTOR (voertuigen, rijstroken, posities), met de commit erbij. Voor servicevoertuig, bijzonder voertuig, UDL en spreiding zou ik de regel graag als losse functie hebben, los van de params. Mag ik daar een PR voor maken? Later eventueel één klein pakket dat beide apps gebruiken.
3. Versies: hoe hoor ik het als een regel verandert? Is een regel in de CHANGELOG genoeg?
4. Licentie: jullie code is EUPL-1.2. Ik zou LLSM dan ook onder EUPL-1.2 zetten. Akkoord?

Drie dingen die ik in de code zag, misschien lees ik het verkeerd:
- vehicle_models.py wijkt af van constants/vehicles.py, bijv. asafstand servicevoertuig 2,5 vs 3,0 m. Welke geldt?
- De tandem krijgt 100 kN per wiel met rijstrookfactor 3. Volgens EN 1991-2 is dat 150 kN per wiel op rijstrook 1. Waar zit de factor ½?
- De tram zonder spreiding lijkt geen belasting te krijgen (oppervlak 0). Klopt dat?

Om te controleren leg ik voor één brug de posities uit LLSM één voor één naast die van het toetsmodel. Heb je een brug die ik daarvoor mag gebruiken?

Groet, Quincy
```

## 7. Risks and scope
- **Rules drift** between the copy and upstream → pinned commit, equality test, CHANGELOG watch (§6).
- **A rule changed in the adapter by accident** (axes, units, which end is x = 0) → check 2 is the
  release gate; no "same as the toetsmodel" claim without zero mismatches.
- **Surface loads are a new write and read path** → spike S9–S11 is the gate; point-load fallback.
- **Case counts** blow up with lanes × configurations → cap per apply, counts shown before writing.
- **Licence** unresolved → no vendored file is committed until §6 is answered.
- Scope: one new package `src/llsm/traffic_rules/` (vendored folder, frame adapter, presets, UDL
  planner), changes in `moving_load/plan.py` (surface patches, groups per lane) and `apply.py`
  (surface-load read-back), one def table, `esa_file.py` reader for surface loads, MCP preview/apply
  arguments, one vendoring script, tests, one verify doc. No change to the bedding route.
