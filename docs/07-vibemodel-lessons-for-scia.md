# 07 — VibeModel lessons for LLSM (SCIA)

Date: 2026-10-27. Based on VibeModel v1.2.14 (plans 01–19, CHANGELOG, src) and LLSM v0.3.4 (`scia-attach-investigation` + `main`).

## TL;DR

VibeModel did not win on agent-loop sophistication — its loop has no prompt caching, no live streaming, no retry, no compaction. It won on the **host side**: honest structured errors with hints, every request reversible, every response stamped with model state, an exec escape hatch grounded by reflection over the live API, domain knowledge as data, and live verification against hand-computed values.

Almost all of that requires **code running inside the host process**. LLSM today is outside SCIA, push-only, and cannot read back what SCIA has open (see `scia_openapi_no_attach`, docs/06). That one fact decides most of what follows:

- Some lessons can be applied **now** in Python (errors, verification, batch model tool, safety, tests, UX).
- The ones that made VibeModel feel magic (grounding on the user's model, selection, state stamp, user-edit detection) need the **in-SCIA plugin** — or a SCIA version whose API can attach. That is the critical research item, not more Python tools.

Second blocker: **numeric results are probably still broken on v24** (`LoadResult` returns None because of the ApiGuid mapping miss, docs/06). "Build it and tell me the deflection" is the headline promise. Until that is verified end-to-end, everything else is polish.

## 1. Blockers to resolve first

**1.1 Results read-back on v24.** `phase0/spike_resultkey_guid.py` tries 5 ways to set `CaseId`; its output is not recorded anywhere. Run it, record the outcome in docs/06. If none work, build the ~50-line C# helper DLL that builds the `ResultKey` and calls `LoadResult` on the .NET side (pythonnet's missing implicit conversion is the problem, not SCIA). This helper is also the first brick of the plugin.

**1.2 The grounding question.** Re-open the attach investigation with a sharper target:
- `SciaTools.ThirdParty.SDK[.ADM].dll` has `GetAnalysisModelFromSciaEngineer()` and `GetSelectedObjectsFromSciaEngineer()` but needs SCIA to start your app as a registered external application. Find out how registration works (SCIA support / Nemetschek forum / dll_calatrava config files / decompile the SDK with ILSpy).
- Check whether SCIA 25 / 26 changed the OpenAPI (attach, results fix). If a newer version is available to the team, test it before investing more in v24 workarounds.
- Plugin route: what is the official SCIA plugin model (Esa.* add-in, "external application" menu entry)? Can a plugin host a loopback HTTP server the way VibeModel does inside Revit? That is the architecture VibeModel proves works.

**1.3 Don't kill the user's SCIA.** `session.py:31-42,96-99,181` kills every running SCIA on start and close, including the engineer's own windows. For a colleague-facing POC this is a trust-breaker. Only kill processes LLSM started (track PIDs).

## 2. Lessons that apply now (Python, no plugin)

**2.1 Structured errors with a `Hint:` (VibeModel plan 03 — its "highest-value, lowest-risk slice").**
VibeModel: `{"ok":false,"error":{code,message,suggestion}}`, fixed code list, ambiguous/unknown names list the candidates.
LLSM: plain dicts, inconsistent; `add_*` tools don't catch exceptions at all; duplicate names silently overwrite the lookup.
To do: one error envelope for every tool; codes like `BAD_ARGS`, `NOT_FOUND`, `DUPLICATE_NAME`, `SCIA_FAILURE`, `WEDGED`, `NO_RESULTS`; always a suggestion; on unknown material/section/node names return the closest existing names; refuse duplicate names.

**2.2 Never report silent failure as success — read back (v1.2.9, IFC piles, plan 12).**
LLSM: `run_analysis` sleeps 5 s and calls "results API not None" success; `TrySynchronizeModel` returning True proved nothing on open (docs/06).
To do: after analysis, prove it with a number (e.g. sum of reactions vs sum of applied loads — a free equilibrium check). After push, count objects SCIA reports vs what was pushed where the API allows. Report mismatches as warnings, never as ok.

**2.3 Domain correctness bugs found in this review.**
- Every load case is created as `ActionType.Permanent`, even in Variable groups (`builder.py:176`). That silently breaks any later load combination.
- Load groups are always `Load=Domestic` (`builder.py:166`).
- The dialog auto-clicker accepts *any* SCIA dialog incl. save/delete prompts (`autoclick.py`). VibeModel's equivalent (v1.2.9 failure policy) whitelists: dismiss warnings, auto-resolve known-safe ones, roll back the rest and report SCIA's reason to the model. Do the same: whitelist the licence "&Ja" dialog, log/report everything else.

**2.4 Fewer, bigger tool calls (batch / declarative model).**
VibeModel: `POST /batch`, domain commands with correct defaults (`place --along --spacing`), one request = one undo.
LLSM: one call per node/beam/support — slow and token-expensive for a 20-member frame.
To do: a `build_model` tool taking a whole JSON model (materials, sections, nodes, members, supports, loads) with validation up front, returning every problem at once. Keep the fine-grained tools for edits. Consider SAF (Excel/JSON structural exchange) as the format — SCIA imports it natively, and it's also an escape from the push-only API.

**2.5 Coverage engineers actually need.** Gaps vs a real POC: library profiles (IPE/HEA/HEB), load combinations (EN 1990), point loads/moments, reactions as a result, releases/hinges, save-to-path (models now stay in SCIA temp). `phase0/scia-examples/.../Program.cs` already shows surfaces, openings, point/surface actions, `StructuralLoadCombination` (line 1126) and `CloseProject(SaveMode)` — port from there. Plates/slabs after that.

**2.6 An exec escape hatch, grounded by reflection (plans 16, 17, 18).**
VibeModel's `exec` covers the long tail; `apilookup` reflects over the loaded assemblies (no shipped index); a failed compile automatically appends the matching lookup; `KnownGaps` is attached on demand, deliberately not a prompt rule; runaway loops are stopped by an injected tick, then rolled back.
For LLSM: an `exec_python` tool running against the loaded SCIA OpenAPI assemblies (pythonnet makes reflection trivial: `dir()`, `clr` type info) plus an `api_lookup(name)` tool. Seed a known-gaps table from what we already measured (ApiGuid conversion, `ToString()` returns the type name, `CreateMesh()` before `RunCalculation()`, `RefreshModel_ToSCIAEngineer` only sends *new* objects, `DeleteItemFromModel` absent on v24, no read-back after `OpenProject`). Then **promote** recurring exec patterns to typed tools, as VibeModel did in v1.2.8.
Caveat: only with the safety work in §3 — exec without rollback is a footgun.

**2.7 Domain knowledge as data, not prompt prose (plan 11).**
VibeModel: standards packs set command *defaults* and run post-command *checks* on just what was created; the prompt stays small with hard byte caps per block.
For LLSM: a pack with valid material grade names (now the model guesses), Eurocode partial factors / ψ-values / standard combination sets, consequence class, deflection limits (L/250, L/300), and checks like "beam has no support path", "load case not in any combination", "unit looks like N instead of kN". Ask the colleagues what their office conventions are — this is where LLSM can be *more* useful than VibeModel, because structural checks have hard numeric answers.

## 3. Safety and trust

- **One request = one rollback unit (plan 02, v1.2.7).** Reliable undo is what let VibeModel delete its "always confirm" rule and stop refusals. LLSM: snapshot the in-memory model before every modifying tool / batch; on failure restore and resync. Expose `undo_last`. With SCIA push-only, "undo" means rebuild-from-snapshot — fine for POC-sized models.
- **Preview before keeping (plan 15).** `preview=true` on modifying tools: report what would be added/changed/deleted without pushing.
- **Claude Code runs with `bypassPermissions`** (`claude_code_backend.py:94-110`), so the in-panel agent has full shell and file access in the repo, not just SCIA tools. VibeModel moved to `dontAsk` + an explicit tool allowlist (v1.2.7). Do the same: allow only the `mcp__llsm__*` tools.
- **Loopback server auth (v1.2.11).** VibeModel found any web page could hit its loopback server via `<img src=...>`. LLSM's HTTP MCP server on 127.0.0.1:8765 has a token only on `/admin/shutdown`. Once exec exists this becomes remote code execution from a browser tab. Token on every request + refuse `Origin`/`Sec-Fetch-*` headers. Must land *before* exec.
- **Test secret leak.** `tests/unit/test_credentials.py::test_delete_clears` falls back to real environment variables and printed a real `OPENAI_API_KEY` in test output. Isolate env in the test (monkeypatch) — and consider rotating that key.

## 4. Verification discipline (plans 11–18, `docs/verify/`)

VibeModel: ~958 headless tests on pure logic split out of host code; live verify on a separate instance with a throwaway model copy; values read back from the model, compared to hand calculations; PASS / FAIL→FIXED / NOT TESTED tables; several review rounds per plan.

LLSM has 42 unit tests, none on `builder.py` or `scia/tools.py`, no CI, and live scripts that print without asserting. Structural analysis is ideal for VibeModel's approach because answers are closed-form:
- Benchmark set with hand calcs: simply supported beam UDL (M = qL²/8, δ = 5qL⁴/384EI), cantilever point load, two-span continuous beam, portal frame. Assert SCIA results within tolerance.
- Headless tests for the builder/validation/error envelope (mock the ADM services).
- A `docs/verify/` page per release with the PASS/FAIL table.
- Successcases (VibeModel's `docs/successcases.md`): collect real questions from colleagues, replay in a fresh chat, score 1–5, log time and tool-call count. Tune prompts by replaying logged turns old-vs-new, not by taste.

## 5. Grounding & context (needs plugin or attach — design for it now)

What VibeModel does and LLSM cannot yet:
- One `/context` call before every turn (active view, selection, model info) prepended to the user message — docs/02 already called this "the single most important trick".
- A model-state stamp on every tool response, incl. "2 USER edits since last command" so the model re-reads instead of acting on stale state.
- Screenshots (plan 01 was their first priority). SCIA can probably export a view image; worth a spike even headless — vision catches wrong geometry instantly.

What LLSM can do now: a compact model-state block (counts, names, last analysis status, units) appended to every tool result from the in-memory model, and fill the panel sidebar in Claude Code mode (it's always empty because the model lives in the MCP subprocess).

## 6. UX & ops (cheap, high adoption impact)

- **Activity rows** with ✓/✗ per tool step and elapsed time; "SCIA may be busy" after 60 s (v1.2.7). Long SCIA calls currently look like a hang.
- **Answers ≤5 lines, units on every number, tables for results** (v1.2.8/1.2.9). Engineers want "M_max = 45.0 kNm at x = 3.0 m", not prose.
- **Token/length indicator + "start a new chat" nudge**, no euros (v1.2.13).
- **Zero-admin install + Diagnose button** (v1.2.10): Python/venv/pythonnet/SCIA path/licence/registry key/API reachability in one copyable report. Setup failures, not model quality, blocked VibeModel adoption.
- **One backend contract.** Direct-API backends get 18 tools, MCP gets 24; the API prompt closes SCIA every turn so iterating is impossible. VibeModel routes every backend through the same endpoint. Make the API backends call the same tool registry (the unmerged `claude-bot/t-246` de-dup refactor may already be half of this).

## 7. Housekeeping found during the review

- 7 commits tagged `v0.2.0` are not on `main` (b4f9034…05e6c7f), incl. closing the previous project (`CloseActiveProject(SaveChangesNo)`) before a new push. Later releases redid the delete fix but not the close-before-reopen. Triage what else is stranded.
- This branch lacks main's `mcp<2` pin and 3 other main commits — rebase before more work.
- README is stale (docking to SCIA is dead code; tool counts and limitations wrong). TODO.md is empty — this doc should feed it.

## 8. What NOT to copy

- VibeModel's agent loop internals. Use Claude Code / the SDK properly: prompt caching, real streaming, retries. That's free wins VibeModel never took.
- Euro cost display, "next steps" lists, prompt-rule known-gaps, "always confirm destructive ops" — all tried and removed there.
- `localhost` (IPv6 `::1` drops → 2 s timeouts; use 127.0.0.1), self-signed builds (worse than unsigned), a shared prompt file across instances.

## 9. Suggested order

1. Prove results on v24 (spike → C# helper if needed) + benchmark tests with hand calcs.
2. Safety basics: stop killing user SCIA, whitelist auto-clicker, allowlist tools, server token, fix test secret leak.
3. Error envelope + duplicate/closest-name handling + Variable action type fix.
4. `build_model` batch tool + library profiles + combinations + reactions + save.
5. Rollback snapshots + preview, then `exec_python` + `api_lookup` + known-gaps.
6. Standards pack (grades, EN 1990 combos, deflection checks) — interview colleagues.
7. Grounding research in parallel: SDK external-app registration, newer SCIA versions, plugin hosting a loopback server. Go/no-go on the plugin based on that.
