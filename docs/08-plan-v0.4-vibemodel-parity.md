# 08 — Plan v0.4 → v0.6: VibeModel parity for SCIA

Date: 2026-10-27. Source: `07-vibemodel-lessons-for-scia.md`. Supersedes the open phases of `05-plan.md`.
Target: SCIA Engineer 24.0 (v24.0.3015.64), Python + pythonnet, Claude Code + MCP as primary backend.

Each phase is one build thread (plan → build → review → verify → release). Every phase ends with a
`docs/verify/<phase>.md` PASS/FAIL table produced against a live SCIA on this machine, VibeModel-style:
expected values computed by hand, results read back from SCIA, not from the tool's own return value.

## Phase A — Results actually work (v0.4.0)  ← gate for everything else

Problem: `get_beam_inner_forces` / `get_beam_deflection` most likely return nothing on v24. `LoadResult`
gets a `ResultKey` whose `CaseId` is an `ApiGuid` whose `ToString()` yields the type name, so SCIA's
mapping lookup misses (docs/06). `phase0/spike_resultkey_guid.py` tries 5 variants; outcome never recorded.

Steps
1. Run the spike against live SCIA, record the output verbatim in docs/06 (append "2026-10 ResultKey spike").
2. If one variant works → wire it into `scia/tools.py`. Done.
3. If none works → `src/llsm/scia/native/LlsmResults.csproj`: a ≤100-line C# class library
   (`net48`, references `SCIA.OpenAPI.dll` + `Results64Enums`) exposing
   `double[][] LoadBeamResult(Project p, string beamName, string caseName, ResultType t)`.
   Built by `scripts/build_native.ps1` (msbuild from the VS Build Tools already on this machine), DLL
   committed under `src/llsm/scia/native/bin/` so colleagues don't need a compiler. pythonnet loads it.
4. Benchmark suite `tests/live/test_benchmarks.py` (opt-in via `LLSM_LIVE=1`), each with hand calc:
   - simply supported beam, L=6 m, q=10 kN/m, rect 300×500 C30/37: M=qL²/8=45 kNm, V=30 kN, δ=5qL⁴/384EI
   - cantilever L=3 m, point load 20 kN at tip (needs point load → Phase C; stub as line load for now)
   - two-span continuous beam 2×5 m, q=10 kN/m: support moment qL²/8 = 31.25 kNm, mid-span 9qL²/128
   Tolerance 2 % (mesh). FAIL if `LoadResult` is None.
5. Equilibrium check inside `run_analysis`: Σ reactions Z vs Σ applied vertical load; report as
   `equilibrium: {applied_kN, reactions_kN, ok}`. Needs reactions → read via `eDsElementType_Node` +
   `eFemNodeReactions`; if reactions aren't readable on v24 say so in the result, never `ok: true`.

Exit: three benchmarks PASS from a fresh chat ("build a 6 m beam… what's the max moment?") with numbers within 2 %.

## Phase B — Trust & safety basics (v0.4.1)

1. `session.py`: track the PID of the SCIA process LLSM launched; `_kill_stale_scia()` only kills PIDs LLSM
   owns (persist to `%LOCALAPPDATA%\LLSM\sessions.json`). Never touch an engineer's own SCIA.
2. `autoclick.py`: whitelist by window title/button text (licence "&Ja", "Information", known mesh
   warnings). Any other dialog: screenshot to `%LOCALAPPDATA%\LLSM\dialogs\`, log title + buttons, do NOT
   click, return `{"error": {"code": "SCIA_DIALOG", "message": title, "suggestion": "..."}}`.
3. `claude_code_backend.py`: replace `--permission-mode bypassPermissions` with
   `--permission-mode dontAsk --allowedTools "mcp__llsm__*"`. (VibeModel v1.2.7: auto-mode classifier
   refused approved deletes; `dontAsk` + allowlist is the working combination.)
4. HTTP MCP server: per-session 256-bit token in `%LOCALAPPDATA%\LLSM\servers\<port>.json`, required on
   every request (`X-LLSM-Token`, constant-time compare); refuse requests carrying `Origin`,
   `Sec-Fetch-*` or a non-loopback `Host`. The panel passes the token through `--mcp-config` headers.
5. `tests/unit/test_credentials.py`: `monkeypatch.delenv` all `*_API_KEY` in a fixture. Rotate the key
   that was printed.
6. Rebase branch on `main`; cherry-pick what is still missing from the stranded `v0.2.0` commits
   (at least `CloseActiveProject(SaveChangesNo)` before re-push in `push_model`).

Exit: pytest green with no env leakage; launching LLSM next to a hand-opened SCIA leaves it alone
(verify item); a `curl` without the token gets 401.

## Phase C — Error envelope + model correctness (v0.4.2)

1. `src/llsm/scia/errors.py`: `ToolError(code, message, suggestion)`; one envelope
   `{"ok": false, "error": {"code", "message", "suggestion"}}` for every tool, both MCP and API paths.
   Codes: `BAD_ARGS`, `NOT_FOUND`, `DUPLICATE_NAME`, `DEPENDENTS_EXIST`, `SCIA_FAILURE`, `SCIA_DIALOG`,
   `WEDGED`, `NOT_ANALYSED`, `NO_RESULTS`, `NOT_SUPPORTED`.
2. `NOT_FOUND` lists the closest existing names (difflib, ≤5). `DUPLICATE_NAME` refuses.
3. Fix `builder.py`: load case `ActionType` follows the group (Permanent/Variable/Accidental);
   `Load` relation configurable (`Domestic` default, but exposed).
4. Material grades: `add_material` validates against the pack list (Phase E) or returns candidates.
5. Every tool result carries a compact `state` block: `{model, counts, scia: launched|pushed|analysed,
   units}` — VibeModel's response stamp, from the in-memory model for now.
6. One tool registry for all backends: the direct-API backends import the same tool table the MCP
   server uses (check the unmerged `claude-bot/t-246` refactor first).

Exit: unit tests on the envelope and name resolution; a wrong material name in chat yields a one-shot
self-correction (log shows one retry, not three).

## Phase D — Engineer-grade coverage + batch build (v0.5.0)

1. `build_model(model_json)`: whole model in one call, validated up front, all problems returned at once,
   nothing pushed on validation failure. Schema in `docs/reference/model-schema.md`.
2. Library profiles: `add_section(profile="IPE 300")` via `StructuralManufacturedCrossSection`
   (see `phase0/scia-examples/.../Program.cs`). Steel grades S235/S355, concrete C20/25…C50/60, timber C24/GL24h.
3. Loads: point load (node & on-beam), moment, trapezoidal line load. Self-weight case helper.
4. Load combinations: `add_combination(name, kind=EN-ULS|EN-SLS|linear, factors)`; standard EN 1990
   6.10a/b set generator from the pack (Phase E).
5. Releases/hinges on member ends; line support later.
6. Results: reactions per support & load case/combination; envelope over a combination.
7. `save_project(path)` with `CloseProject(SaveMode)`; `open_project(path)` stays push-only (documented
   as such in the tool description so the model doesn't expect read-back).

Exit: a 2-bay portal frame with IPE profiles, ULS combination and reactions, built from one chat
message, matches a hand-calculated frame (reactions and support moment) within 2 %.

## Phase E — Rollback, preview, exec escape hatch, standards pack (v0.5.x)

1. Snapshot the in-memory model before every modifying tool; on `SCIA_FAILURE` restore + resync;
   `undo_last` tool; `preview=true` reports add/modify/delete counts without pushing.
2. `exec_python(code, budget_s=25)`: runs in the STA worker with `env`, `project`, `adm`, `model`
   bound; hard timeout; snapshot/rollback around it; output + exceptions returned in the envelope.
3. `api_lookup(name)`: reflection over the loaded SCIA assemblies (`clr` + `System.Reflection`):
   namespace, members, enum values, closest-name suggestions. On `exec_python` exceptions, auto-append
   the lookup for the failing identifier.
4. `docs/reference/known-gaps.md` + `scia/known_gaps.py`: ApiGuid conversion, `ToString()` quirk,
   `CreateMesh` before `RunCalculation`, refresh-only-sends-new-objects, no `DeleteItemFromModel` on
   v24, push-only `OpenProject`. Attached to lookup/exec results only, not to the prompt.
5. Standards pack `standards/nl-office/` (data, not prompt): material grade table, EN 1990 combination
   factors (γ, ψ0/1/2 per category), deflection limits, default consequence class; `checks.json`
   (unsupported member, load case in no combination, suspicious unit magnitude). Checks run after each
   modifying tool on what it created, reported as a `check:` line.
6. Promote recurring exec snippets to typed tools (review the verify log for what was exec'd).

Exit: a failed push leaves the model exactly as before (verify item); `exec_python` can add an edge
hinge not covered by a tool, and the lookup answers "what fields does StructuralCurveMember have".

## Phase F — Panel UX + install (v0.6.0)

1. Activity rows per tool step (✓/✗, first error line, elapsed; "SCIA may be busy" after 60 s).
2. Answer rules in the shared prompt block: ≤5 lines, units on every number, results as tables.
3. Context-length dot + one-time "start a new chat" nudge (tokens, no euros).
4. Sidebar shows the live model state in Claude Code mode (read it from the MCP `state` block).
5. `install.cmd`: per-user, idempotent, exit codes; `Diagnose` button: Python/venv/pythonnet/SCIA
   path/registry key/licence/HubServer/API reachability in a copyable report.
6. Successcases: `docs/successcases.md` + `scripts/successcase.py` — collect colleagues' real questions,
   replay in a fresh chat, score 1–5, record time and tool-call count.

## Track G — Grounding research (parallel, no code commitment)

Goal: a go/no-go on the in-SCIA plugin, with evidence.
1. `SciaTools.ThirdParty.SDK.ADM.dll`: decompile with ILSpy; find how `LaunchedManually` resolves the
   connector process and what "registered external application" means (registry? `*.xml` under
   `dll_calatrava`? SCIA's Tools › External applications menu?). Try to register LLSM.
2. Ask SCIA support (via the licence contact) two questions: (a) can an external app attach to a running
   SCIA 24/25, (b) is the `ResultKey`/`ApiGuid` behaviour fixed in 25.
3. If a SCIA 25/26 install is reachable in the office: rerun `phase0/spike_attach_running.py` and the
   ResultKey spike there.
4. Prototype: a minimal C# add-in/external app that, when started by SCIA, calls
   `GetSelectedObjectsFromSciaEngineer()` and serves it over 127.0.0.1 HTTP with a token (VibeModel's
   `RevitHttpServer` pattern). If this works, LLSM's Python tools become clients of that server and
   everything in §5 of doc 07 (context call, state stamp, user-edit detection, selection) unlocks.
5. Spike: can SCIA export a picture of the current view headless (for a `screenshot` tool)?

Write-up in `docs/09-grounding-decision.md`.

## Non-goals for this plan

- Plates/2D members, nonlinear analysis, Eurocode member checks: after v0.6, once results and
  combinations are trusted.
- Replacing Claude Code as primary backend. Do use its native prompt caching/streaming; don't rebuild
  an agent loop.
- Docking the panel into SCIA (dead code in `panel.py`; remove it).

## Prior attempts

- Attach: `scia-attach-investigation` branch, docs/06, `phase0/spike_*` — parked on
  `E_CONNECTOR_PROCESS_NOT_FOUND`; Track G reopens it with the registration question, not the same spike.
- Delete on v24: fixed in stranded `v0.2.0` and redone later on main (`RemoveItemFromModel`).
- ResultKey: v0.3.4 fixed the TypeError (ApiGuid wrap); the mapping miss remains — Phase A.
