# Spike: does a chat-built model calculate, and can LLSM read the forces back?

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
| 8 | After SCIA's Calculate: results API | `InitializeResultsAPI` returns None ("ResultsApi.Init failed"). SCIA's result-table request answers a table with headers and no rows. |
| 9 | After Calculate: refresh from SCIA, `SynchronizeDS`, `SaveToFile` + reopen | No results API in any. The project folder the results API reads gets no new files. |
| 10 | Batch: `SaveToFile` (answer "Gegevens converteren?" Ja), close SCIA, `ESA_XML LIN` on a copy (Succeeded, 23.5 s), open it, `LoadResult` with the build's ids | API starts; `LoadResult` None for both beams and cases. |
| 11 | Same file, native key with SCIA's own numbers (beam 1/2, case 1/2) | None for all four. |
| 11 | Control: kademuur.esa (32 result sets, saved by an engineer) | `InitializeResultsAPI` returns None. |
| 12 | After Calculate: SCIA's own "1D internal forces" and "Reactions" commands, picture | No values drawn (picture of the main window only; the properties panel was not visible). |
| 13 | Control: kademuur.esa, `SaveToFile` | 32 result sets kept. |
| 13 | Same project, SCIA's Calculate (7.3 s), `SaveToFile` | 0 result sets: the calculation removed the results and made none. |
| ctrl | `ESA_XML LIN -tESA` on `kademuur_kaal.esa` (53 s, Succeeded) | 32 → 0 result sets: the output file keeps no results. |
| ctrl | brugdek with traffic: `llsm report write --calculate --cases-group "LLSM-ML TS L1A" --format xlsx` (1004 s) | OK: 112 of 112 results tables with rows; L1A 14 reaction resultant R_z 202.5 kN = the preview's total. |
| 14 | The owner's route: the test beam pushed into a copy of brugdek.esa (it has Report_1), saved, same report command with a group "LLSM-ML CB" | TRY14 |

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
selected: SEN.ED.FUN.EN_002, SEN.ED.STA.EN_003). Two starts failed because the licence check took
over 3 minutes (HubServer limit).

## What changed in LLSM

- `run_analysis` is ok only when SCIA returns inner forces for the first beam and load case (it
  looks for 20 s). Otherwise ok false, `calculated: false`, and a plain message: the model is in SCIA
  with a mesh, SCIA did not calculate it, the command LLSM can send is not allowed by this licence,
  calculate in SCIA itself. The two result tools give the same message. The fixed 5 s wait is gone.
- A variable or accidental *Standard* case is written as *Static*; the tool result names the stored
  type.
- The result key is built in one place; its comment no longer repeats the docs/06 cause.

## Open, and a proposal

1. **Rerun try 7 and try 13 when "Professional Edition EN" is free** (no licence notice at start). If
   SCIA's Calculate then makes results (try 13 keeps or adds result sets), the calculation can be
   driven: `ExecuteCommandString("CMD.TOOLS.CALCULATION")`, press Bereken, read SCIA's closing
   message (it says which step failed). That is GUI automation of SCIA's own dialog, so it needs the
   same care as the licence notices (only LLSM's own SCIA, only that dialog).
2. **Numbers back in the chat:** the results API is out on this licence (it does not start even on a
   model with results). The routes left are SCIA's printed output: the engineering report (`llsm
   report`, needs a report in the model, made once in SCIA 24) or the XML IO export (`-x`, also a
   document made once in the GUI). Both work on a saved file with SCIA closed and take minutes, not
   seconds. That is the owner's suggestion, and it fits a "calculate and report" request, not a quick
   question in a chat about an open model.
3. **Ask SCIA (Nemetschek)** whether the hidden linear calculation can be enabled for the
   gemeente's licence. It is the command their own OpenAPI uses; without it the OpenAPI cannot
   calculate on a customer licence at all.
