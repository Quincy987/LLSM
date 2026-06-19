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
- `phase0/probe_structure.py` — reflection of `proj.Model` (`Structure`): no read-back accessor
- `phase0/spike_open_readback.py` — proves OpenAPI can't read an opened model back
- `phase0/spike_connector.py` — third-party connector attach attempt (no SCIA launch)
- `phase0/spike_attach_running.py` — boots real SCIA GUI, attempts connector attach
- `phase0/open-api-adm-jupyter/` — scia-garage reference repo (v24-patched) + populated venv

## Live-attach to an existing/open model — investigated, NOT achievable on v24 (q-662)

Question: can LLSM read/edit a model the engineer already has open (not one we built)?

Findings (all reflection + live spikes against v24.0):
- **OpenAPI is push-only.** `SciaEnvironment` has no attach/connect (only `RunSCIAEngineer` + `OpenProject(path)`), and `SciaSession` kills stale SCIA first. An opened model can't be read back: `proj.Model` (`Structure`) has no `AnalysisModel`; `TrySynchronizeModel`/`RefreshModel_FromSCIAEngineer` populate nothing; name→ID dicts are null after open. (Side fix: corrected the misleading `refresh_from_scia` docstrings in `tools.py`/`mcp_server.py`.)
- **A real connector SDK exists** — `dll_calatrava\SciaTools.ThirdParty.SDK[.ADM].dll` (+ native `ExternalAppLink.dll`, a shipped POC `SciaTools.ThirdParty.Poc.Wpf.exe`, Copyright SCIA nv 2022 / v22.1). It exposes exactly the missing surface: `GetAnalysisModelFromSciaEngineer()`, `GetSelectedObjectsFromSciaEngineer()`, `Send[Partial]AnalysisModelToSciaEngineer`, register/launch handshake, `SciaEngineerThirdPartyProjectSwitched` events.
- **No partner-key gate at the SDK** — a random app GUID is accepted.
- **But it does NOT attach to a running SCIA.** Both a connector-only attach and a freshly-booted real `SciaEngineer.exe` GUI (with and without a model loaded) fail at `LaunchedManually` with `E_CONNECTOR_PROCESS_NOT_FOUND` — *before* any model access. The connector is file/folder-message based and the flow is **SCIA-initiated**: SCIA must be configured to launch your app as a **registered external application**, which starts SCIA's third-party API + connector together (hence `LaunchedBySciaEngineer` + POC's `InitializeThroughSciaEngineer(configPath)`).
- **The wall = SCIA-side external-app registration.** Not in any shipped config/registry slot found; `ExternalAppLink.dll` is native (not trivially decompilable). Needs SCIA's official external-application / Open API connector docs (or SCIA/Nemetschek support), and possibly a license/module.

**Status: parked.** Capability is in the product but not self-serviceable from the install on v24. **Revisit on higher SCIA versions** — a newer release may document/open the external-app registration, expose the third-party API on a normally-running instance, or ship a usable sample. Re-run `phase0/spike_attach_running.py` against the new version as the first check.

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
