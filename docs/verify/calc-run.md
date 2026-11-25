# Spike: does a chat-built model calculate, and can LLSM read the forces back?

> **Update 2026-11-24: yes, the way the VIKTOR apps do it.** A chat-built model is pushed into a copy
> of LLSM's start model (a project that carries an XML results export), a copy is saved, and SCIA's
> batch program calculates it and exports the results in one run, while LLSM's own SCIA stays open.
> The 2 x 12 m beam gives My = -161.19 kNm at the middle support (hand 162 kNm), 214.93 after the load
> went from 9 to 12 kN/m (hand 216). Deflections are not given: LLSM's materials carry no stiffness.
> See "2026-11-24: the calculation, built" at the end. The sections in between are the 2026-11-23
> spike that found why the OpenAPI calculation does nothing.

Date: 2026-11-23. SCIA Engineer 24.0.3015.64. Scripts: `phase0/calc/` (one per try, shared harness
`common.py`, IL reader `ildump.py`); results per try in the gitignored `phase0/calc/work/*.json`.

## Answer

- **The chat does not calculate on this licence.** The OpenAPI's `RunCalculation` sends SCIA's
  "hidden linear calculation". SCIA's own configuration allows that command for SCIA's internal use
  only. It answers "succeeded" at once and calculates nothing. `run_analysis` now says so, instead
  of reporting ok.
- **A second fault in LLSM, found on the way:** a variable load case was written with SCIA's load
  type *Standard*. SCIA's solver refuses it ("Voorbereiding van statische belastingsgevallen: NIET
  OK"); a variable case must be *Static*. The builder now writes Static. That fault would also have
  stopped an engineer who pressed Calculate in SCIA on a chat-built model.
- **The owner's suggestion works: forces from SCIA's own report.** The test beam, pushed into a copy of
  a model that carries an engineering report (brugdek.esa), saved, then calculated and printed by
  LLSM's existing report route, gives My = −161.19 kNm at the middle support (hand 162 kNm, −0.5 %),
  in 226 s, with SCIA closed (try 14). What it needs is a start model with a report in it.
- **Reading forces back through the OpenAPI is not possible here.** The results API does not even
  start on a calculated model saved by an engineer (kademuur.esa, 32 result sets). The old cause in
  `docs/06-phase0-results.md` (ApiGuid.ToString, "fix with a C# shim") is wrong: SCIA's lookup is
  correct, and a C# helper would run the same code.
- **SCIA does calculate today, in batch.** The control (brugdek with traffic, `llsm report write
  --calculate --cases-group`, the route verified on 2026-11-14) printed 112 of 112 results tables with
  rows; position 14 of lane 1A gives a reaction resultant of 202.5 kN, the total the traffic preview
  planned. So the licence state of the day (below) does not stop the solver for ESA_XML.
- **Not settled:** SCIA's normal Calculate driven through COM. It accepts the corrected model, but on
  the engineer's kademuur model it removed the stored results and made none (try 13). Whether that is
  the reduced functionality SCIA starts with when "Professional Edition EN" is in use, or the way it is
  driven, is open.

## Yardstick, and what turned out not to measure anything

The plan's yardstick: (a) files after the calculation (`SolverLinkData.dat` above 999 bytes), (b)
`LoadResult` returns sections. Two parts of that proved useless and are not used in the conclusions:

- `SolverLinkData.dat` scales with the model (34 kB for a large model), not with "calculated": 999 /
  811 / 763 bytes for the 2-beam model in every state.
- The stored-results marker (`ReportIndex.result_sets`) drops to 0 after `ESA_XML LIN -tESA` even
  on a real model that had 32 (`kademuur_kaal`), while the same day LIN plus a print in one run gives
  full results tables: the `-tESA` output keeps no results. A 0 after LIN proves nothing, and a file
  calculated that way cannot be read back later (tries 10 and 11 read exactly such a file).

What does measure: numbers read back, and the result sets in a file saved with
`IEnvESAProject.SaveToFile`, which keeps them (32 → 32, try 13).

## Tries

Test model (all tries): continuous beam 2 x 12 m, 1000 x 600 mm C30/37, pinned at A, Z rollers at B
and C, EG (self weight) and Verkeer-UDL (-9 kN/m on both spans). Hand value for Verkeer-UDL:
|My| at B = 9 x 12² / 8 = 162 kNm, reactions 216 kN in all.

| Try | What | Outcome |
|---|---|---|
| 1 | Baseline: LLSM's push, mesh command, calculation command | Mesh: 33 files in 4.9 s. Calculation: `CommandESA` returns True in 0.0 s, 0 files in 25 s. `LoadResult` None. |
| 2 | Logs during the calculation command | Only "ESAApp command {C35839BF…} called successfully". No refusal anywhere. |
| 5 | SCIA's configuration (`Configuration/Commands.xml`, `CommandsEvaluation.xml`) | `{C35839BF…}` is `CMD.TOOLS.HIDDEN_CALCULATION_LINEAR`, gated by `<evaluator type="license" tech_module="446" module_name="IS_SCIA_FOR_INTERNAL_USE_ONLY"/>`. Only it and `CMD.TOOLS.HIDDEN_CALCULATION` carry that gate. The mesh command has none. |
| 5 | COM: `ExecuteCommandClsid` with both hidden ids | eSucceeded, 0.0-0.3 s, 0 files. |
| 5 | COM: `ExecuteCommandString("CMD.TOOLS.CALCULATION")` | Opens SCIA's own Win32 dialog "EE berekening" (Lineaire analyse, 2 load cases, button Bereken) and waits. |
| 7 | Same, LLSM presses Bereken (`BM_CLICK`) and reads SCIA's Qt message through UI Automation | "Voorbereiding voor genereren van net: OK / Voorbereiding van statische belastingsgevallen: NIET OK". |
| 7 | Variants, same route | Only EG: no message. Only Verkeer-UDL (variable, *Standard*): NIET OK. Verkeer-UDL as permanent *Standard*: no message. Verkeer-UDL variable *Static*: no message. Both cases with Static: no message. |
| 1 | Corrected model (Static), LLSM's calculation command | Still True in 0.0 s, 0 files: the gate, not the model. |
| 8 | After SCIA's Calculate: results API | `InitializeResultsAPI` returns None. SCIA's result-table request answers a table with headers and no rows. |
| 9 | After Calculate: refresh from SCIA, `SynchronizeDS`, `SaveToFile` + reopen | No results API in any. The project folder the results API reads gets no new files. |
| 10 | Batch: `SaveToFile` (answer "Gegevens converteren?" Ja), close SCIA, `ESA_XML LIN` on a copy (Succeeded, 23.5 s), open it, `LoadResult` with the build's ids | API starts; `LoadResult` None for both beams and cases. |
| 11 | Same file, native key with SCIA's own numbers (beam 1/2, case 1/2) | None for all four. |
| 11 | Control: kademuur.esa (32 result sets, saved by an engineer) | `InitializeResultsAPI` returns None. |
| 12 | After Calculate: SCIA's own "1D internal forces" and "Reactions" commands, picture | No values drawn (picture of the main window only; the properties panel was not visible). |
| 13 | Control: kademuur.esa, `SaveToFile` | 32 result sets kept. |
| 13 | Same project, SCIA's Calculate (7.3 s), `SaveToFile` | 0 result sets: the calculation removed the results and made none. |
| ctrl | `ESA_XML LIN -tESA` on `kademuur_kaal.esa` (53 s, Succeeded) | 32 → 0 result sets. With the control below (LIN plus a print in one run gives full tables) the reading is that the output file keeps no results. |
| ctrl | brugdek with traffic: `llsm report write --calculate --cases-group "LLSM-ML TS L1A" --format xlsx` (1004 s) | OK: 112 of 112 results tables with rows; L1A 14 reaction resultant R_z 202.5 kN = the preview's total. |
| 14 | The owner's route: the test beam pushed into a copy of brugdek.esa (it has Report_1), saved, same report command with a group "LLSM-ML CB" | **OK, 226 s** (LIN + XLSX print). Class *LLSM-ML CB 01*, 1D internal forces: CB_Veld1 at x = 12 m and CB_Veld2 at 0: My = −161.19 kNm, Vz = ∓67.43 kN; CB_Veld1 at 4.667 m: My = 91.31 kNm. Hand: 162 kNm (−0.5 %), 5qL/8 = 67.5 kN, 9qL²/128 = 91.1 kNm. The report's member table lists CB_Veld1/2 next to the deck's columns. |

Not run: plan step 3b (a start model saved by SCIA 24) and step 4 (fixed support, combination). Try
1 on the corrected model showed the hidden calculation is inert whatever the model, and the Calculate
route accepted the model once the load type was right, so neither could change the answer.

## Why the docs/06 cause was wrong

Read from SCIA.OpenAPI.dll (`ildump.py`) and checked live:
`ResultsAPI.LoadResult` → `ConvertKey` → `GetCaseDsId` → `UniqueIdMappingTables.GetDsId`, which converts
the ApiGuid with `op_Implicit(ApiGuid) -> Guid` before the lookup. The type name in the old log line is
only the error text formatting the object. Live: beam Veld1 → 1, Verkeer-UDL Guid → 2, native key
`Esa.Beam1.FEM Beam Inner Forces.LoadCase2.LCS.Rib`. The key is right; the store is empty.

## Licence state during the spike

At every start: the Qt notice "Professional Edition EN" not available (in use by colleagues), answered
with Return (`LLSM_ANSWER_LICENCE_NOTICES=1`), and the Win32 question "Uw huidige licentie is
onvoldoende … Overzichtstekeningen … verder met gereduceerde functionaliteit?", answered Ja by LLSM.
Licence selection unchanged all day (selected: SEN.ED.FEM_002, SEN.ED.PR.EN_007 and others; not
selected: SEN.ED.FUN.EN_002, SEN.ED.STA.EN_003). Three starts failed because the licence check took
over 3 minutes (HubServer limit); each worked on the next try.

## What changed in LLSM

- `run_analysis` is ok only when SCIA returns inner forces for the first beam and load case (it
  looks for 20 s, a fresh results API each time, the unused ones freed). Otherwise ok false,
  `calculated: false`, and a plain message: the model is in SCIA with a mesh, SCIA made no results,
  the command LLSM can send is reserved for SCIA's internal use and did nothing on this licence,
  calculate in SCIA itself. The two result tools give the same message once `run_analysis` has looked.
  The fixed 5 s wait is gone.
- Results are dropped on a new push and on every edit, so the result tools never answer from a
  calculation of an earlier model.
- A variable or accidental *Standard* case is written as *Static*; the tool result names the stored
  type. Measured for a variable case (try 7); for an accidental case it follows SCIA's rule that only
  permanent cases are Standard, and ADM accepts it (live builder test), but no accidental case was
  calculated. An unknown kind is refused by name instead of raising.
- The result key is built in one place; its comment no longer repeats the docs/06 cause.

## Open, and a proposal

1. **Rerun try 7 and try 13 when "Professional Edition EN" is free** (no licence notice at start). If
   SCIA's Calculate then makes results (try 13 keeps or adds result sets), the calculation can be
   driven: `ExecuteCommandString("CMD.TOOLS.CALCULATION")`, press Bereken, read SCIA's closing
   message (it says which step failed). That is GUI automation of SCIA's own dialog, so it needs the
   same care as the licence notices (only LLSM's own SCIA, only that dialog).
2. **Numbers back in the chat: built on 2026-11-24**, with the XML export instead of the report
   (about 30 s instead of 226 s, and SCIA may stay open); see "2026-11-24: the calculation, built".
3. **Ask SCIA (Nemetschek)** whether the hidden linear calculation can be enabled for the
   gemeente's licence. It is the command their own OpenAPI uses; without it the OpenAPI cannot
   calculate on a customer licence at all.

## Verification of the change (2026-11-23)

| Check | Result |
|---|---|
| Unit suite (`tests/unit`, incl. new `test_run_analysis.py` and load-type tests) | 504 passed, 2 skipped |
| Live `tests/live/test_calc_live.py` (test beam through the tool functions the MCP server calls) | passed (run twice, last after the review fixes): `run_analysis` → `ok: false, calculated: false`, the plain message; `get_beam_inner_forces` → the same message; the case is stored as Static |
| Live `tests/live/test_builder_rules_live.py` | passed (now accepts "ok only with results") |
| Traffic preview on brugdek.esa with the changed code | 42 cases, 138 loads, no problems (unchanged) |
| Report route (control and try 14) with the changed code | OK, see the table above |
| SCIA processes after every run | none left |

On every close SCIA's COM connection drops while it quits (`InvalidComObjectException` / pytest's
"Windows fatal exception: code 0x800706be" in `kill_remembered`); the runs still pass and leave no
process. That was so before this change too.


## 2026-11-24: the calculation, built

Scripts: `phase0/calc/try16_start_model.py` (make, repeat, clash, timing) and
`phase0/calc/try17_stability_material.py`; results in the gitignored `phase0/calc/work/`.

### The route (what the VIKTOR apps do)

The plaatbruggen and kademuren apps never use SCIA's calculation command: VIKTOR's `SciaAnalysis`
gives SCIA's batch program (ESA_XML) a start model with an XML IO document and reads the XML it
exports. LLSM now does the same with a chat-built model:

1. `push_model` opens a fresh copy of the start model (`~/.llsm/start_model.esa`, a SCIA-24 copy of the
   kademuren app's `app/segment/scia_integration/scia/model.esa`, made once with `llsm start-model`)
   and pushes the ADM model into it, as before into SCIA's empty file.
2. `run_analysis` meshes it in SCIA (so the engineer sees the mesh), saves a copy
   (`IEnvESAProject.SaveToFile`) and runs ONE `ESA_XML LIN <copy> classes.xml -x<out.xml>`, with a
   linear combination and a result class per load case (`build_case_classes_update`). LLSM's own SCIA
   stays open: the batch guard now lets only that session's processes run alongside.
3. The export's tables ("Interne 1D-krachten" per result class) are read per case and member and kept
   for that model version; `get_beam_inner_forces` answers from them.

### Measurements

| Try | What | Outcome |
|---|---|---|
| 15 | test beam into a copy of the kademuren start model, batch with SCIA closed / open | identical rows; -161.19 / -80.60 kNm (hand 162 / 81); 24.6 s closed, 38.2 s open |
| 16 make | v24 copy of the start model | the first save asks "Gegevens converteren?" (answered Ja); a save of the v24 copy asks nothing |
| 16 repeat | three batch runs with SCIA open | exit 0 three times, identical: -161.19 and -80.60 kNm; 30.9 / 20.6 / 21.3 s |
| 16 repeat | edit after push (both spans 12 kN/m through `set_line_load_magnitude`), save, batch | -214.93 kNm (hand 216, the same -0.5 % as at 9 kN/m); the start model's export tables survive the edit |
| 16 clash | names the start model owns | load group LG1 and load case BG1: SCIA refuses ("The name 'LG1' has already been used"), and every object that refers to them fails; with BG1 the export showed the start model's own BG1. Material C30/37: pushed without error, batch Succeeded, but the export has no rows at all. Material C20/25 (also in its library): calculated normally. |
| 17 | two single 6 m beams, pin + roller Z | no rows at all: free to rotate about their own axis |
| 17 | the same with a fixed support at one end | rows; My at the clamp -44.92 kNm (hand q L^2 / 8 = 45) |
| 17 | self weight of the beam (1000 x 600) | My at B -263.55 kNm, i.e. SCIA weighs it with 2500 kg/m3 (264.9 by hand) |
| 17 | deflection and end rotation of the beam (9 kN/m) | 0.776 mm at 0.4 L and 0.243 mrad at A, against 1.70 mm and 0.545 mrad by hand for E = 33 GPa: SCIA calculates with an unknown default stiffness |
| 17 | LLSM material with E, G, nu, density (and alpha) set in ADM | the whole model did not reach SCIA (the receiver never finished); without alpha it landed but the export had no rows |
| 17 | quality set to a library grade (C30/37, C45/55) | landed, no rows |
| 17 | steel material (S-quality unknown to SCIA) | no rows |

**Corrected 2026-11-25** (section "materials, sections and free motions" below): the cause was the pin + roller test beam, the turned section and the quality = name, not the material route. As written then: why the material cases fail is not known; what is known: LLSM's materials are made without stiffness
or weight (E = 0, density 0), SCIA then uses a default concrete that gives right forces when every
member has the same material, and every way tried to give a material its own properties stopped the
calculation. So deflections are not given and steel models are not calculated (a NO_ROWS answer).

### Time against model size

Beam grids of 6 m beams (fixed + roller), each case loading every beam; push into the start model,
mesh, save, batch, with SCIA open. "Calculation" is what `run_analysis` does (mesh + save + batch).

| Members | Cases | Push | Mesh | Save | Batch | Calculation | Estimate |
|---|---|---|---|---|---|---|---|
| 2 | 2 | 10.8 s | 4.9 s | 1.1 s | 28.5 s | 34.5 s | 30 s |
| 2 | 10 | 7.5 s | 4.3 s | 0.8 s | 42.0 s | 47.1 s | 55 s |
| 50 | 2 | 10.6 s | 5.2 s | 0.8 s | 44.0 s | 50.0 s | 55 s |
| 50 | 10 | 14.2 s | 6.0 s | 0.8 s | 77.7 s | 84.5 s | 75 s |
| 200 | 2 | 22.8 s | 6.9 s | 0.9 s | 109.9 s | 117.7 s | 115 s |
| 200 | 10 | 37.8 s | 10.3 s | 1.0 s | 139.1 s | 150.4 s | 150 s |

Estimate = 26.4 + 0.41 members + 2.54 cases + 0.0093 members x cases seconds (least squares, within
8 s of each run, rounded to 5 s). Above 120 s `run_analysis` asks once before it starts. Building the
model with the add tools is slower than all of this for large models (117 s for 200 members x 10
cases): the builder checks each name against the whole model.

### What the tables hold

Per result class (one per load case): "Interne 1D-krachten" with per member the two ends and the
extreme sections (N, V_y, V_z, M_x, M_y, M_z, SI), and "1D-vervormingen" with the extremes over the
whole model, not per member. Reactions are not in this start model's export.

### What changed in LLSM

- `push_model` uses the start model when there is one (`start_model: true`, `calc_estimate_s`); without
  one it pushes into SCIA's empty file as before and says the model cannot be calculated.
- `run_analysis(confirm=False)`: mesh, save a copy, batch calculation in a job (`llsm.jobs`, shared
  with the report prints); a call waits up to 240 s, a calculation still running is polled by calling
  `run_analysis` again; above 120 s estimated it returns `needs_confirmation` first. A call after a
  finished calculation of the same model version returns that result (`already_calculated`) instead of
  calculating again. ok only when the export holds rows for this model's members; otherwise the known
  causes (a model free to move or twist, a steel material).
  The OpenAPI calculation call and the results-API read path are gone.
- Results belong to a model version: every push and edit raises it, so a calculation that finishes
  after an edit or a push is thrown away (`stale`), never shown.
- `get_beam_inner_forces` answers from the export (SI as before, plus kN/kNm); `get_beam_deflection`
  says why deflections are not given.
- The builder refuses the start model's names (563: SCIA's material library, its sections, LG1, BG1,
  its layers), read from the start model's file.
- `llsm start-model <template.esa>` makes the start model (SCIA-24 copy, export checked); `llsm
  diagnose` reports it. The XML-export reader moved from the spike into `llsm/scia/xmlio.py`; the
  report route and the calculation share `calc.lin_with_case_classes`.

### Verification of the build (2026-11-24)

| Check | Result |
|---|---|
| `llsm start-model` on the kademuren template | OK: `~/.llsm/start_model.esa`, saved by 24.0.3015.64, results export checked (5.8 s), 563 names reserved |
| `llsm diagnose` | "LLSM start model OK" with the path and version |
| Unit suite (`tests/unit`, incl. `test_calc_route.py` on a captured export of the test beam) | 521 passed, 2 skipped |
| Live `tests/live/test_calc_live.py` | passed: C30/37 refused as reserved; case stored as Static; push with `calc_estimate_s` 30; `run_analysis` done in 30.4 s; My at B within 1 % of 162 kNm; deflection refused; after 12 kN/m the old result is gone, then within 1 % of 216 kNm |
| Live `tests/live/test_builder_rules_live.py` (a steel model) | passed: `calculated: false` with the plain NO_ROWS answer, never ok without results |
| Traffic preview on brugdek.esa | 42 cases, 138 loads, no problems (unchanged) |
| Report route | unit tests unchanged and passing; it now calls the shared calculation step |
| Left behind | no SCIA process, no start-model copy, no calculation folder |

SCIA's start-up licence check took over 3 minutes four times today (the HubServer limit); the live
tests try the start up to three times. That limit is older than this change.

## 2026-11-25: materials, sections and free motions (try 19 and try 20)

The try-17 conclusion above ("every way tried to give a material its own properties stopped the
calculation") was wrong. All those variants were pin + roller test beams, and the cause was the beam,
not the material. Three causes, each proved by numbers:

1. **Sections were turned by 90 degrees.** SCIA's ADM rectangle takes its parameters as [height, width]
   (SCIA's OpenAPI example, `Program.cs:582`); the builder passed [width, height]. A 1000 x 600 mm beam had
   I = 0.05 instead of 0.018 m4: deflections 2.78 x too small.
2. **No real material.** The builder used the material's name as its ADM `Quality`. SCIA does not know
   such a name and uses a default concrete (E about 27 GPa). With `Quality` = a name of SCIA's library
   ("C30/37", "C20/25", "S 235" with the space) SCIA uses that grade's E and weight, for steel too.
3. **The test beam could move without deforming.** A pin holds three translations, a Z roller one: the
   beam can still turn about its own axis and swing sideways about the pin. Whether SCIA's solver then
   gives rows depends on the stiffnesses, hence the erratic "no rows" of tries 16 and 17.

### Try 19 (spike scripts and results in `phase0/calc/work/try19/`, gitignored)

Two spans 2 x 12 m, 9 kN/m, A FIXED (so nothing can move freely), rollers at B and C, 1000 x 600 mm,
section order corrected, ADM Quality from the library, no XML. Hand values: M_A = -q L^2 / 14 = -92.57
kNm, M_B = -3 q L^2 / 28 = -138.86 kNm, rotation at C = 370280 / (E I).

| Quality | M_B (kNm) | rotation at C (mrad) | hand (mrad) |
|---|---|---|---|
| C30/37 | -138.64 | 0.630 | 0.627 |
| C20/25 | -138.64 | 0.688 | 0.686 |
| S 235 | -138.63 | 0.098 | 0.098 |

The same numbers came from an XML cross-section update (catalogue item 2 = rectangle H, B; materials by
library name), which also works but leaves the open SCIA window with the placeholder material.

### Try 20: the proofs before the build (`phase0/calc/try20_proofs.py`)

Each run is one SCIA session with one model, through LLSM's own route (push into the start-model copy,
`run_analysis`). Hand values for 9 kN/m on 1000 x 600 mm: single span 12 m M = q L^2 / 8 = 162 kNm,
deflection 5 q L^4 / (384 E I), end rotation q L^3 / (24 E I); two spans M_B = 162 kNm, largest
deflection 0.00542 q L^4 / (E I). E: C30/37 32.8, C45/55 36.3, S 355 and S 235 210 GPa.

| Proof | What | Result |
|---|---|---|
| P1 | pin + Z rollers, LLSM's extra restraints set by hand (roller held in Y, or X for the 30-degree beam; pin held about X), five beams in one model, 41 s | every beam calculated |
| P1 | single span C30/37 | 162.0 kNm; 4.14 mm (hand 4.11); end rotation 1.098 mrad (hand 1.096) |
| P1 | two spans C30/37 | M_B -161.71 kNm (hand 162); 1.734 mm at 4.8 m (hand max 1.711) |
| P1 | single span at 30 degrees in plan, C30/37 | 162.0 kNm; 4.14 mm |
| P1 | single span S 355 / C45/55 | 0.647 mm (hand 0.643) / 3.74 mm (hand 3.72); both 162.0 kNm |
| P2 | a material NAMED "C30/37" (name = quality), stable beam | SCIA dropped the pushed model silently (`LoadAnalysisModelFromDiscInReceiver FAIL`, then the batch run did not know the load case): the reserved names stay |
| P3 | kind Steel with quality C30/37; kind Concrete with quality S 235 | both calculated, but SCIA ignored the quality: 0.647 mm (steel's E) and 5.01 mm (default concrete) |
| P4 | after the first calculation, the pin's rotation about Y freed through the edit sync (TrySynchronizeModel), then held again | A fixed: -92.76 / -138.64 kNm; A pinned: 0 / -161.71 kNm; held again: -92.76 / -138.64 kNm. A support change reaches SCIA through the normal edit sync |

The deformation rows: a few sections per member (its ends and extremes); a member can miss some (in the
two-span beam the second span had only its end).

### What changed in LLSM

- Sections: [height, width] to SCIA; tools keep saying width x height.
- Materials: `add_material(name, grade=..., kind=...)`. The grade is matched against the start model's
  material library (case and spaces ignored: "S235" finds "S 235"; "C24" finds "C24 (EN 338)"); an unknown
  grade is refused with the closest names; the kind follows from the grade and a contradicting kind is
  refused (P3). A name that is a grade is its own grade; because of P2 it is called "C30/37 (LLSM)" inside
  SCIA, while the chat keeps using "C30/37". Without a grade a material is ungraded: forces only when all
  members share it, no deflections.
- Free motions (`llsm/scia/stability.py`): before every push, edit sync and calculation LLSM finds the
  rigid-body motions the supports leave free, per group of connected members. A free motion no load acts
  on (at any point of a loaded member; self weight counts when there is a self-weight case) is held by
  extra restraints at existing supports, exactly one per free motion, so their reactions are zero and the
  forces do not change. A free motion a load acts on is a mechanism: `run_analysis` refuses before
  calculating and says what can move. The sidebar keeps the support type the engineer asked for.
- Deflections: `get_beam_deflection` gives uz (mm) and rotations (mrad) at the exported sections and the
  model's largest deflection in that case, when every material in use has a grade.
