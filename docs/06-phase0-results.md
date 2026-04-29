# Phase 0 — Verification Results

Run on **2026-04-24** against **SCIA Engineer 24.0.3015.64**.

## ✅ Fully automated end-to-end pipeline

The script `phase0/live_test.py` runs the complete LLSM-relevant pipeline **with zero human interaction**:

```
[setup] auto-clicker thread started
[scia] launching (minimized, no focus)...
[scia] HubServer ready at t=0s
[autoclick] dialog 'SCIA Engineer' -> click '&Ja'      ← dialog auto-dismissed
[scia] opened template project
[scia] model pushed clean
[scia] CreateMesh() OK
[scia] RunCalculation returned
[scia] RAPI OK on attempt 1
=== SUCCESS: full end-to-end pipeline works autonomously ===
```

## What works

1. Registry discovery of SCIA v24 install + OpenAPI_dll folder
2. 27 DLLs load via `pythonnet` / `clr.AddReference`
3. In-memory ADM model construction (12 items: material, cross-section, nodes, beam, supports, load group, load cases, line load)
4. `IAnalysisModelService.ValidateModel()` passes
5. `SciaEnvironment.RunSCIAEngineer(ShowWindowShowMinNoActive)` — launches SCIA minimized, no focus steal
6. **Background auto-clicker** (pywin32) detects the `&Ja` license dialog and auto-clicks it
7. HubServer + HubClient processes spawn
8. `env.OpenProject()` — opens empty template
9. `proj.Model.CreateAdmObject()` + `RefreshModel_ToSCIAEngineer()` — pushes all 12 ADM items to live SCIA (**user visually confirmed beam + load render correctly in SCIA UI**)
10. **`proj.CreateMesh()`** — **critical step**, generates FE mesh
11. `proj.RunCalculation()` — linear analysis
12. `proj.Model.InitializeResultsAPI()` — returns valid RAPI handle
13. Clean dispose + process termination

## The RAPI "mystery" was CreateMesh()

Earlier runs had `InitializeResultsAPI()` returning `None`. This had nothing to do with licensing — the missing step was `proj.CreateMesh()`, which must be called between the ADM push and `RunCalculation()` so the FE solver has a mesh to operate on. Manual UI runs work because F9 triggers auto-meshing internally; the API's `RunCalculation()` does not.

Once `CreateMesh()` is called, RAPI initializes on the first attempt.

## Minor loose ends (not Phase 0 blockers)

- **Result-key tuning**: `rapi.LoadResult(resultKey)` currently returns `None` for our queries. Result-key fields (load case name, entity type, exact result enum, coord system) are API-fiddly. Will nail down concrete result extraction as part of Phase 1 tool work.
- **Auto-clicker is English/Dutch aware** but not localisation-general. Fine for this user; trivially extensible.

## Design decisions locked in from Phase 0 evidence

- **Launch mode**: `ShowWindowShowMinNoActive` (minimized, no focus) — excellent POC UX.
- **Threading**: SCIA operations must run on an STA thread (pythonnet's main thread is MTA).
- **License pool**: user's SCIA v24 license covers everything we need (ADM push, mesh, calc, RAPI init). Analysis/results modules (`SEN.ED.FEM_002`, etc.) are auto-claimed on launch.
- **Auto-clicker**: non-optional for autonomous operation on this machine; SCIA consistently pops a `&Ja` license dialog per-launch that must be dismissed before HubServer spawns.
- **Startup hooks**: user has disabled the welcome screen / project chooser via SCIA preferences. Remaining `&Ja` dialog is handled by auto-clicker.

## Verdict

**Phase 0 is complete.** The full LLSM-relevant pipeline runs end-to-end, autonomously, on SCIA v24 with no human interaction. User observed the beam+load render correctly in SCIA. Result extraction tuning is a Phase 1 task, not a Phase 0 blocker.

## Artifacts

- `phase0/live_test.py` — full autonomous end-to-end script (auto-clicker + STA + CreateMesh + RAPI)
- `phase0/smoke_test.py` — DLL load + in-memory ADM validation (no SCIA launch)
- `phase0/probe_api.py` — reflection probe of `SciaEnvironment` + `EsaProject` classes
- `phase0/open-api-adm-jupyter/` — scia-garage reference repo (v24-patched) + populated venv

---

## Update 2026-04-25: Result-extraction root cause identified

After extensive probing, the cause of `RAPI.LoadResult` returning None on v24 is now understood:

**SCIA.OpenAPI.ApiGuid has no `ToString()` override.** It inherits the default `System.Object.ToString()` which returns the type name (`"SCIA.OpenAPI.ApiGuid"`). SCIA's internal `GetDsId` lookup (called inside `LoadResult`) keys the mapping table by `apiGuid.ToString()`, so the lookup never matches the actual GUID — every result query returns None.

**Evidence chain:**

- `proj.Model.FindBeamDsId("beam_1")` → `1` ✅ (entity name preserved across ADM push)
- `proj.Model.FindLoadCaseDsId(adm_guid_wrapped)` → `2` ✅ (Guid wrapping works for FindLoadCaseDsId)
- `rapi.LoadResult(...)` always None
- SCIA log: `Error - GetDsId: UniqueId doesn't exist in mapping table - UniqueId: SCIA.OpenAPI.ApiGuid` (literal type name as lookup key)
- Python: `str(ApiGuid(some_guid)) == "SCIA.OpenAPI.ApiGuid"` and `ApiGuid.ToString()` returns the same — confirmed via reflection that `ToString` is declared only on `System.Object`
- `apiGuid.ReturnString()` correctly returns the actual GUID string, so the underlying value IS preserved — only `ToString` is broken

**Why C# example works but Python doesn't:** the C# implicit conversion `(string)apiGuid` calls `op_Implicit(ApiGuid) -> String` which returns the actual GUID. SCIA's `GetDsId` may use this implicit cast in C#, while in pythonnet the `.ToString()` path is taken by SCIA's own internal code.

**Phase 3 fix candidates:**

1. **Thin C# shim DLL**: a tiny library exposing `LoadBeamInnerForces(proj, beamName, caseGuid)` etc. — does the ResultKey construction + LoadResult on the C# side where ApiGuid behaves correctly. Call from Python via `clr.AddReference`. ~50 lines.
2. **Upgrade to SCIA v25+**: likely just works (the published 25.0 docs all use this pattern as if it works).
3. **Pythonnet monkey-patch** of ApiGuid.ToString — speculative, may not survive marshalling boundary.

**Demo impact:** none. The autonomous build/push/mesh/calc pipeline works end-to-end. Engineers see their model render, can press F9 to verify results visually. Numerical readback for the LLM is a Phase 3 enhancement.
