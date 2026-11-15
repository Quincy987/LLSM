# Phase 4 verification — the toetsmodel's traffic rules on a saved SCIA model

Date: 2026-11-14/15. SCIA Engineer 24.0.3015.64, ESA_XML, on copies only. Plan: `docs/10-plan-moving-load.md`
(v2, §3–§5). Rules: plaatbruggen toetsmodel at `e8e44e7` (2026-07-01), vendored in
`src/llsm/traffic_rules/_toetsmodel/` (`UPSTREAM.md`). Scripts: `phase0/moving_load/surface_spike.py`,
`reactions.py`, `compare_toetsmodel.py`, `verify_app_rule.py` (models and logs in the gitignored
`phase0/xml_route/work/`).

## Decision

**GO.** LM1 per notional lane at the toetsmodel's positions, with the wheels as 0.4 × 0.4 m free surface loads,
written into a saved model in one update, read back load by load, and calculated: the column forces equal the
loads that lie on the deck. LLSM's cases are the toetsmodel's own, case by case, on four decks.

## Spike: free surface loads (gate)

| # | Criterion | Result | Evidence |
|---|---|---|---|
| S9 | One rotated 0.4 × 0.4 m free surface load in one update | **PASS, after two fixes** | `surface_spike.py s9` on `plate_base.esa`: patch centred at (3.0, 2.0) m, turned 30°, 937.5 kN/m². First try refused: `Unknown element 'row'`. SCIA maps a row's cells to the table's columns by position: with columns left out (q1–q3, Location), the geometry landed on another column. Writing every column up to "Table of geometry" in the def's order: exit 0. Second: the value in `q` was not stored (no −937500.0 in any block); in `q1` it is. LLSM writes both. Corners stored as m (2.9268, 1.7268 …). |
| S10 | Column forces = area × pressure | **PASS** | LIN on the S9 output: 4 × 37.50 kN = **150.0 kN** = 0.16 m² × 937.5 kN/m². |
| S11 | Read-back from the `.esa` | **PASS** | Probes with 3, 4 and 5 corners (`probe_surface*.esa`): corner count at +47 after the name; corner k at first + 69 k (x, y, z at +0, +12, +24), first = 91 + 24 n; the load case GUID 2 bytes after the last corner; case id +20, load id +80, value +413 after it. Values, corners, case ids and the cases' load lists all read back; counts agree with SCIA's object log. On the real kademuur model the 9 free surface loads it already had (FF1 … FF13, e.g. −31.56 kN/m² on the wall) now read too, without a format error. |

## A table longer than one piece

The first apply of the rule (42 cases) was written by SCIA but refused on read-back: the reader found 21 of 43
load cases. SCIA stores its data in pieces of exactly 512 000 bytes; the case table ran on into the next piece,
a block without a table header, which the reader skipped on purpose (phase 3: supports after the plate block must
not be read as plates). On the plate fixture, the demo models and this output every full piece is followed by its
continuation (219, 319 and 217 full pieces). The reader now joins a full piece with what follows it before reading
a table; other headerless blocks are still not read. Unit test: a case record cut in two at the piece boundary.
The member and support reader (bedding) joins them the same way: on five models (the plate, the kademuur demo with
and without beddings, this output, a real model with 319 line supports) it reads exactly what it read before.

## The rule, against the toetsmodel's own code (check 4)

`compare_toetsmodel.py dump` runs the toetsmodel's tandem generators unchanged in its own venv
(`tandem_systems_theoretical_lanes_bg8000/9000/10000` and the `real` ones), stubbing only what they read from the
app's params (the supports; for real lanes the road strip). `compare` plans the same decks in LLSM (plate x 0..L,
y 0..W, axis along +x at W/2, so the coordinates are the same) and matches every case by its title.

| Deck | Toetsmodel cases | Same in LLSM (corners ± 1 mm) | Mismatches |
|---|---|---|---|
| 6 × 4 m, t 0.3 m | 42 | 42 | 0 |
| 20 × 4 m, t 0.8 m, support at 10 m | 90 | 90 | 0 |
| 12 × 10 m, t 0.5 m (3 lanes; Conf. C and D) | 228 | 209, plus 19 Conf. D centre tandems written once | 0 |
| 12 × 10 m, real lanes on a road y 2.0–8.5 m | 95 | 95 | 0 |

**RESULT: PASS, 0 mismatches over 455 cases.** Matched means: the wheels of the toetsmodel's case that lie (partly)
on the deck are exactly LLSM's patches; a wheel wholly off the deck is the one LLSM left out. Conf. D's centre
tandem ("rs 1 - Conf. D") has the same wheels as Conf. C's, checked per position, and is written once.

What this run settled about the rule:
- The sequencer is called with the vehicle length 1.6 m (`TANDEM_VEHICLE_LENGTH`), as the generators do, not
  1.2 m as first measured for this plan: the 6 m demo plate gets **14** positions per lane, not 13.
- Its x is the rear edge of the rear wheel patch: LLSM's front axle sits at x + 1.4 m. The vehicle enters with its
  front patch's leading edge 0.9 t onto the deck and leaves with its rear patch's trailing edge 0.9 t from the end.
- "Real" lanes for one road zone are the theoretical lanes laid over the road strip, shifted to its edge; the
  toetsmodel's own tests for `generate_real_lane_positions_bg8000/9000` are met that way (unit test).
- Loads differ on purpose: the toetsmodel writes 100 kN per wheel in every lane and applies 3 / 2 / 1 in its
  combinations; LLSM writes 150 / 100 / 50 kN per wheel (EN 1991-2, × α). Question 2 to Geert stands.

## Through the product, on copies of the plate deck (6000 × 4000 × 300 mm, columns C1–C4)

| # | Run | Result |
|---|---|---|
| 1 | Phase 3 hand case, point loads (CLI, custom axles 200 + 100 kN, step 1600) | 12 of 12, 3 of 3, 25.0 s. LIN: C1/C4 232.68 kN, C2/C3 217.32 kN, **900.0 = 900.0 kN**: as phase 3. |
| 2 | Same, `--contact surface` | 12 of 12 patches, 3 of 3, 22.5 s. LIN: the same four column forces, 900.0 = 900.0 kN; per side 465.36 / 434.64 kN against 465 / 435 kN by hand (0.08 %). |
| 3 | `--rule toetsmodel --thickness 300` (CLI) | 3 groups `LLSM-ML TS L1A/B/C` (lane centre 1.5 / 2.5 / 2.0 m from the right-hand edge), 14 positions each, 42 cases, **138 of 138 patches and 42 of 42 cases confirmed**, 25.0 s. 30 wheels wholly off the deck left out, 30 patches reaching past the deck end. LIN, case L1A 01 alone (front axle at 70 mm: two front patches with 0.27 of their 0.4 m on the deck): **202.5 = 202.5 kN**. All 14 cases of L1A: **6202.5 = 6202.5 kN**. So SCIA takes a patch that reaches past the deck edge and loads only the part on the deck, as the toetsmodel assumes. |
| 4 | MCP server (`verify_app_rule.py`, a real MCP client with the token) on a copy of the demo `brugdek.esa` | `moving_load_preview` ok, 42 cases, 13 s; `moving_load_apply` ok, **138 of 138**, 54 s; the same apply on its output without `replace`: refused, "61 names already exist" per group. Server shut down through `/admin/shutdown` (200, exit 0), no error in its log, original and copy unchanged. LIN of the output, all 14 cases of L1C (centre lane): C1 = C4 1528.34, C2 = C3 1572.91 kN, **6202.5 = 6202.5 kN**. |

## Regression

- `pytest tests/unit`: 436 passed, 2 skipped (live tests).
- `tests/live/test_moving_load_live.py` (LLSM_LIVE=1, the plate fixture): 3 passed in 336 s: the phase 3 hand case, the same
  request again with and without `replace`, and the rule (42 cases, 138 of 138 patches, all at −937.5 kN/m²).
- Bedding on a copy of the kademuur demo model (`llsm bedding apply`, one clay layer, D 0.4 m): 32 of 32 confirmed, 40.9 s.
- The toetsmodel checkout was read and run, never changed (`git status` before and after: only its own untracked `.review_tmp/`).

## Natural request (2026-11-15)

Engineers do not type "volgens het toetsmodel", coordinates or a thickness: they name their model and say what
they want. Three things make that work, checked here.

**The model by its file name.** The chat has only LLSM's tools and runs in the repo folder, so "brugdek.esa" was
"Model not found". Every saved-model tool now looks a bare name up (`.esa` optional, any case) under Documents (Windows' known
Documents folder, on this PC `OneDrive - Gemeente Amsterdam\Documenten`, and the local `~\Documents` where the
demo models are) or the folders in `LLSM_MODEL_DIRS`, never in the server's own working folder, to four folders deep, skipping hidden ones, the last model's folder first.
Through the MCP server (`verify_app_rule.py`, `LLSM_MODEL_DIRS` = the work folder): `brugdek.esa` found;
`nergens.esa` refused, naming the folder searched; `dubbel.esa`, present twice, refused with both paths, newest
first; `report_preview` and `bedding_preview` (`kademuur_kaal`, no extension) find their models the same way. A
name that matched one model (or that the engineer then gave as a full path) keeps meaning that file for the
session, so a preview and its apply act on the same model even if another copy appears in between (unit tests).

**The thickness from the plate** (`thickness_probe.py`). Three plates of known thickness written through the XML
route (4 corners 0.25 m, 5 corners 0.45 m, a vertical wall 0.35 m) and the demo models: the corner count sits at
+47 after the plate record's name, the thickness at 293 + 48 n, and 48 bytes before it a nonzero u32 (an id:
0x13D on the fixture, 0x162 on the kademuur) followed by the u32 1. Read back: 0.25 / 0.45 / 0.35 m (probe),
0.3 m (plate fixture, brugdek and its LLSM copy), 0.4 m for deck E1 and wall E2 of every kademuur model and of a
real model with 319 line supports. Independent check on the kademuur deck: its mid-plane is at z = −1.1 m and the
wall's surface loads start at its top, z = −0.9 m, so 0.4 m. Without the marker the thickness stays unread and
the tool asks for `thickness_mm`; decks of different thickness are asked for too.

**The axis from the plate.** With a rule and no `path_mm` the axis runs between the midpoints of the plate's two
shorter (end) edges, also on a skewed deck; a deck along y drives along y (unit tests). More than one deck, or a
plate that does not have 4 corners: the tool asks for `path_mm`.

| Run | Result |
|---|---|
| `moving_load_preview(model_path="brugdek.esa", rule="toetsmodel")` | ok, 42 cases; axis (0, 2000) → (6000, 2000) mm "the long axis of D1"; thickness 300 mm "read from D1" |
| Same, `thickness_mm=400` | ok, 39 cases (13 positions per lane: start 0.9 × 0.4 m) |
| `moving_load_apply(model_path="brugdek.esa", rule="toetsmodel")` | 138 of 138 patches, 42 of 42 cases, 51 s; again without `replace` refused |
| **A real chat turn** (`chat_turn.py`: this build's MCP server and the panel's own chat backend, Claude Code CLI, only LLSM's tools; exits 1 unless preview then apply, each with only the file name and the rule): "Zet de verkeersbelasting LM1 op het brugdek (brugdek.esa) en schrijf het weg." | PASS, twice (87 s and, after the review, 82 s). The model called `moving_load_preview {"model_path": "brugdek.esa", "rule": "toetsmodel"}`, then `moving_load_apply` with the same two arguments, nothing else. Its answer: written to `brugdek_llsm-01.esa`, 138 wheel loads and 42 cases confirmed, axis (0, 2000) → (6000, 2000) mm, 300 mm from plate D1, the three groups with 14 cases each, 30 wheels off the deck left out, supports at the deck ends. |
| Two turns, looser: "Kun je verkeer op het brugdek zetten?", then "ja, schrijf maar weg" | PASS (30 s + 65 s). No file name given: the model tried `brugdek.esa`, found it, showed the preview (axis, 300 mm from D1, three groups of 14) and asked whether that was the model and whether to write; after "ja" it called `moving_load_apply` with the same two arguments. |

Regression: `pytest tests/unit` 463 passed; live tests 3 of 3 (291 s); the toetsmodel comparison again 0
mismatches over 455 cases.

## Open

1. Plate thickness is read from the `.esa` (see "Natural request"); a plate in another layout still asks for it.
2. Supports are not read from the model: the deck's ends per lane, unless `supports_mm` (chainages or support lines).
3. Two road zones ("real" lanes in two carriageways) are not offered; the toetsmodel's helpers for them are not copied.
4. v2.1: service vehicle, accidental vehicles (standard and Amsterdam), tram, UDL, dispersal (plan §4).
5. A plate with variable thickness was not measured: what the reader takes as its thickness is then unchecked.
   The preview always shows the thickness used and where it came from.
6. Free surface loads stored in another layout (another distribution, selected members): known by name only, so
   after a write they are checked for being there, not for their values.
