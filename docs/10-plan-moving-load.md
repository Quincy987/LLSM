# Plan — moving load positions ("treintje") on an existing SCIA model (v0.5)

Date: 2026-11-12. Status: plan, nothing built. Expands docs/09 "Phase 5". Prior attempts: none found
(`git log -i --grep=load` shows only the OpenAPI load-case fix 503f58f and tool parity; no moving-load work).

Asked by: Tom (mail 2026-11-06/12): "genereren van een rijdend belastinggeval … X belastinggevallen waarbij de
belasting steeds een stukje opschuift … zelf het voertuig bepalen, de stapgrootte … starten met het genereren van de
belastingstappen." Sophie: a traffic "treintje" placed by hand at governing positions because the SCIA mobile-load
licence (Traffic Loads 2D) is usually taken; a skewed crossing meant a lot of copying.
Not a replacement for SCIA's mobile-load module: it writes what is now copied by hand.

## 1. v1 scope
**In**
- Vehicle: an axle list (load kN, distance mm from the front axle, wheel track mm or none), or a preset
  `EN1991-2 LM1 TS lane 1/2/3` (2 axles 1200 mm apart, track 2000 mm, 300/200/100 kN per axle; α-factors
  a parameter, default 1.0 — NB values to confirm). LM1's UDL is not moving: not in v1.
- Path: two points or a polyline on the deck, mm, global X/Y (Z = deck level, given or read). Skew needs no
  separate angle: the path is drawn along the road axis and the wheels sit perpendicular to it, so a skewed
  deck is just a path at an angle to the deck edges. Optional lateral offset mm.
- Step mm. Positions: the whole vehicle on the path, front axle from `vehicle length` to `path length`.
- Deck type: **plate** (free point loads, one per wheel) or **single beam line** (point load per axle on the
  member, by name + relative position). Beam grids: v2 (load splitting between beams is a design choice).
- Output in a NEW file version (`<model>_llsm-NN.esa`, as bedding): one load group `LLSM-ML <name>`,
  Variable, relation **Exclusive** (so combinations take one position at a time); one load case per position
  `LLSM-ML <name> 01..NN`, description = front-axle chainage; the point loads, named `LLSM-ML <name> 01 W1..`.
- Envelope combination: only if the spike shows `EP_LoadCombi` writes cleanly — one "Envelope ULS" with all
  cases. Otherwise the engineer makes it (one click, since the group is exclusive).

**Not in v1**: running the calculation, reading results, Excel, chart, UDL, beam grids, curved roads by
arc, dynamic factors. **v2 route**: ESA_XML can calculate (exit 5 = failed) and export result tables to XML
(phase0 made such exports: `res_sample.xml.def`). v2 = calculate the copy → export internal forces per
load case at chosen sections/members → table "position × force" → xlsx with openpyxl + a chart (influence
line). Beware phase0's lesson: an unstable model returns empty tables with exit 0.

## 2. Route decision — spike first (step 1, ~one session, on copies only)
Choice: **ESA_XML file route** (as bedding). The OpenAPI route (builder.py/tools.py) only works for models LLSM
builds itself, has no point loads, and `UpdateProject` on an existing file was never reached (hub failed to start).

Discovery is half done: phase0's hand export, now in the repo as `phase0/xml_route/defs/viktor_full.xml.def`, already defines `DataSetScia.EP_LoadGroup.1`
(with Relation), `DataSetScia.EP_LoadCase.1` (Name, Action type, Load group, Load type, …),
`DataAddLoad.EP_PointForceFree.1` (Load case, Direction, Type, Validity, Select, Value F, Coord X/Y/Z, System),
`DataAddLoad.EP_PointForceLine.1` (member reference, Position x, Coord. definition, Origin, eccentricities) and
`DataSetSciaTom.EP_LoadCombi.1`. The spike trims these into `resources/loads.xml.def`; if a field's meaning is
unclear, make one load by hand in SCIA on the test model and export it (as phase0 did).

Test models: `pile_base.esa` (beam) and a new 6000 × 4000 mm plate deck on line supports; then a real bridge copy.

| # | Criterion | Pass when |
|---|---|---|
| S1 | Group + 3 cases + 6 free point loads in one update | exit 0, all objects present after reopen |
| S2 | Units and coordinates | SCIA GUI shows −100 kN at (1.70, 1.00) m for literal −100000 / 1.7 / 1.0 (expect N, m) |
| S3 | Loads land on the plate | after a calculation, sum of reactions per case = sum of loads (±0.5 %) |
| S4 | Point load on a beam by member name + relative x | position shown in GUI equals the written one |
| S5 | Nothing else changed | `diff_esa.py` vs a plain re-save: only the new objects + bookkeeping |
| S6 | Read-back | `esa_file.py` (or an XML export) finds cases and loads, count = SCIA's object log |
| S7 | Name collision behaviour | known: same name overwrites (as supports did) — confirm for cases |
| S8 | Combination | `EP_LoadCombi` envelope with 3 cases reopens; else drop from v1 |

S1–S3 and S6 are the gate. S6 also decides whether "path off the deck" can be checked from the file (plate
outline from the reader) or needs a deck outline as input. **Fallback** if S1/S2 fail: LLSM writes the same
update XML and the engineer imports it in SCIA by hand (File → Update/Import XML) — still removes the copying.
If S6 fails: a hand export of the load tables as read-back source. If S3 fails (free loads not picked up):
plate decks wait; v1 ships for beam lines only.

## 3. Data model and MCP tools
New package `src/llsm/moving_load/` (pure, no SCIA): `vehicle.py` (Axle, Vehicle, presets), `path.py`
(polyline, chainage → point + perpendicular), `plan.py` (positions → PlannedLoad per wheel/axle, names,
preview table), `apply.py` (write, read back, compare — mirror of `bedding/apply.py`), `cli.py` (`llsm moving-load`).
`esa_xml.py`: `run_update` takes the def file as a parameter; a second builder for load tables; SI conversion
(kN → N × 1000, mm → m ÷ 1000) in one place.

MCP (units mm and kN, downward load = negative Z, as the rest of LLSM):
- `moving_load_preview(model_path, path_mm, step_mm, vehicle_preset=None, axles=None, deck="plate",
  member=None, name="TS1", lateral_offset_mm=0, replace=False)` → table per position: case, front-axle
  chainage (mm), loads, total (kN), first wheel X/Y (mm). Writes nothing.
- `moving_load_apply(same args)` → new file version, read-back comparison per load, "X of Y confirmed".
- Server instructions: one line "for moving traffic positions on an existing model: moving_load_preview, then moving_load_apply".

Errors (structured, with a hint, as bedding):
- Path off the deck (any wheel outside the plate outline / beyond the member, tolerance 1 mm) → list positions.
- (path length − vehicle length) not a multiple of step → refuse, suggest the nearest dividing steps, or
  `last_step="short"` to add a final position at the path end.
- Vehicle longer than the path; zero/negative step; > 200 cases → warn, ask to confirm.
- Existing load cases/group/loads with the same names → refuse unless `replace=True` (SCIA silently overwrites).
- SCIA running / file locked / licence exit 6 → reuse `esa_xml` guards.

## 4. Verification (docs/verify/phase3-moving-load.md)
Hand case on the plate model: axles 200 kN (front) and 100 kN at 1200 mm behind, track 2000 mm; path
(500, 2000) → (4900, 2000) mm, step 1600 mm → 3 positions. Wheel y = 1000 and 3000 mm.

| Case | Front axle x | Rear axle x | Wheels (kN) | Total |
|---|---|---|---|---|
| 01 | 1700 | 500 | 2 × −100 at x 1700, 2 × −50 at x 500 | −300 kN |
| 02 | 3300 | 2100 | idem | −300 kN |
| 03 | 4900 | 3700 | idem | −300 kN |

Checked load by load after read-back (position ± 0.1 mm, value exact, case and group right), plus S3's
reaction sum, plus one GUI screenshot of case 02 in `docs/verify/img/` (phase0 could not capture Qt panels —
take it by hand if automation fails). Unit tests: plan maths incl. a 30° path (wheel offset (−sin, cos)·1000),
step errors, naming, collision refusal, XML text snapshot.

## 5. Questions for Tom and Sophie (draft mail)
> Onderwerp: Rijdende belasting in LLSM — vijf vragen
> Hoi Tom en Sophie, ik ga beginnen met het genereren van de belastingstappen (één belastinggeval per positie,
> in een nieuwe versie van je model). Om het goed te doen heb ik dit van jullie nodig:
> 1. Een kopie van een brugmodel waarop je dit nu met de hand doet (gaat niet verder dan mijn pc).
> 2. Jullie voertuig: assen met last (kN) en onderlinge afstand (mm), spoorbreedte; of LM1 tandem — met welke α-factoren?
> 3. Is het dek in je model een plaat of een balk(enrooster)? Bij een rooster: hoe verdeel je nu een wiel over de liggers?
> 4. Scheefheid: is het voldoende als je de rijlijn schuin over het dek tekent, met de wielen haaks op de rijlijn?
> 5. Naamgeving: welke namen voor belastinggevallen en groep wil je zien (nu: "LLSM-ML TS1 01..")? En wil je er
>    direct een omhullende combinatie bij?
> Groet, Quincy

## 6. Risks and scope
- Scope: 1 new package (~6 files), 3 changed (`esa_xml.py`, `esa_file.py`, `mcp_server.py`), 1 new def
  resource, ~4 test files, 1 verify doc, changelog. Subsystems: file route, undocumented reader, MCP surface.
- Biggest risk: the reader. Load cases, free loads and plate outlines are new record layouts in an
  undocumented format; every new reader is cross-checked against SCIA's object log, as now.
- Free loads that miss a plate may be ignored silently by SCIA → S3 is the only real proof; keep it in verify.
- Name keys: SCIA overwrites by name → collision check before every write.
- The unexplained code-manager value (100 → 0) from phase0 may reappear → diff in the spike, report it.
- Unit slips (kN vs N, mm vs m) give plausible wrong loads → one conversion function, hand case, GUI check.
- Large models: an update took 75 s on a 50 MB file; 100 cases is one update, not 100.
- Step 0 done: the source def file is in the repo (`phase0/xml_route/defs/viktor_full.xml.def`).
