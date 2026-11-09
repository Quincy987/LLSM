# 09 — Plan v0.4: work on the engineer's existing model (beddings first)

Date: 2026-11-09. Supersedes the *order* of `08-plan-v0.4-vibemodel-parity.md`; keeps its trust,
verification and UX lessons (from `07-vibemodel-lessons-for-scia.md`). Status: proposal, nothing built.

## 1. What changed since doc 08

Doc 08 assumed the headline promise is "build a model from a sentence and tell me the deflection".
The first replies from the constructeurs (the design lead and one hands-on engineer; 2 of 6 asked) say
something else:

| Wish | Design lead | Hands-on engineer |
|---|---|---|
| Beddingen uit geo-rapport op palen | yes | **yes — "ik haat beddingen invoeren"**; pain is NAP → positie langs de paal; wants ~1 m layers with soil description instead of the kademuur tool's 20 cm steps; "1 druk op de knop" |
| Belastinggevallen/-combinaties per norm | yes | **no** — "doe ik liever zelf, kijk eerst wat maatgevend is" |
| Resultaten → Excel → IDEA | first yes, then: often done by hand because force peaks need judgement; ask how engineers handle peaks | — |
| Verkeersbelasting "treintje" (mobile-load licence usually taken) | — | yes; skewed crossing = lots of copying |
| Geometry generation | — | "zie ik niet geautomatiseerd worden" |
| LLM licence | has none | — |

Two conclusions:
1. **Every wanted feature operates on a model the engineer already has**, not on a model LLSM builds.
   The OpenAPI we use is push-only and cannot read an opened model (docs/06). That blocker is now
   the whole product, not a Phase-3 nicety.
2. **The LLM is not the value; the data entry is.** The arithmetic (NAP → pile position, k → spring
   stiffness, axle positions) must be deterministic code. The chat's job is reading messy input and
   taking instructions. One respondent has no LLM licence at all, so the core must also run without chat.

Product hypothesis to test in a pilot: *LLSM saves constructeurs time on tedious data entry into
existing SCIA models; it does not replace modelling.* This is a pivot from the README's positioning.

Evidence base is thin (n=2, and the two disagree on combinations). Section 6 lists what we collect
before committing beyond the spike.

## 2. The technical question: how to read and write an existing model

Routes, with what we know:

| Route | Read existing geometry | Write supports/loads to it | Evidence |
|---|---|---|---|
| OpenAPI ADM (current LLSM) | no (docs/06) | only objects it created itself | spikes in `phase0/` |
| Third-party connector / plugin (Track G) | yes, incl. selection | yes | blocked on SCIA-side registration; parked |
| **SCIA XML update via `ESA_XML.exe`** | export needs an *XML IO document* defined in the project (`-x`, `-m`); exit codes 8/9 if absent | `ESA_XML.exe NONE "proj.esa" "update.xml"` is an officially supported "XMLUpdateFile" mode; exit code 7 = bad update | usage strings in `C:\Program Files\SCIA\Engineer24.0\ESA_XML.exe`; VIKTOR kademuur app uses it (always with a generated whole model, never an update) |
| Same, by hand in the SCIA UI | File › Export › XML | File › Update from XML (matches by name) | engineers already know this dialog |
| SAF (Excel) export/import | SCIA exports whole model to SAF | import adds/updates by name | not tested; spec is public |
| OpenAPI `UpdateProject(TypeXML, path)` | — | same XML update, but through the OpenAPI session | exists in `SciaEnvironment`, never called |

**Decision rule:** the file route wins if, on a copy of a real model, one script can (a) list pile
members with begin/end coordinates, (b) add segmented flexible line supports to those members by
*name*, with the right units, and (c) read the result back and see exactly those supports and nothing
else changed. If (a) fails but (b)+(c) pass, the engineer exports XML by hand once per model — still a
one-click write. If (b) fails on both XML and SAF, stop and report; the fallback product is a paste-ready
table + a SCIA-side manual, which is honest but weak.

A side benefit of the file route: **the original file is never touched**. Every write goes to a new
version next to it (`model.esa` → `model_llsm-01.esa`). That is VibeModel's "one request = one rollback
unit" for free, and the reason we can drop the "always confirm" reflex.

## 3. Phases

Each phase is one build thread (plan → build → review → verify). Each ends with
`docs/verify/<phase>.md`: PASS/FAIL table, values read back from SCIA, hand-calculated expectations.

### Phase 0 — Route spike (gate for everything)
**Outcome 2026-11-09: GO with conditions, see `docs/verify/phase0-route.md`.** Two corrections to
the text below: saving needs `-tESA -o<output.esa>` on the ESA_XML command, and the stiffness literal
is in N/m², so kN/m² input has to be multiplied by 1000.

Inputs: a copy of a real pile-supported model (`C:\Development\Viktor\*.esa`, or one from the pilot engineer),
`paaltje.xml` / `viktor.xml.def` as syntax reference (`DataAddSupport.EP_LineSupportLine.1`, "Line
supports on beam"), ESA_XML usage above.
1. Export: can we get members + nodes out? Try `-x` with an XML IO document; if the project has none,
   try adding one via XML update, else document "engineer exports once".
2. Update: write two line supports on one existing beam by name, `ESA_XML.exe NONE copy.esa upd.xml`.
   Check exit code, open in SCIA, export again, diff. Also test: duplicate name, non-existent member,
   decimal comma (`paaltje.xml:354` uses one), v21 vs v24 file (`-r` resave).
3. Same two writes through `UpdateProject(TypeXML)` in the OpenAPI session, so we know whether the
   existing MCP server can host it.
4. Decide per the rule in §2. Write `docs/verify/phase0-route.md`.
Exit: decision recorded; one real pile carries two correct segments visible in SCIA.

### Phase 1 — Bedding core, no chat required (v0.4.0)
**Outcome 2026-11-09: built and checked on a real model copy, see `docs/verify/phase1-bedding.md`.**

`llsm.bedding` package, pure Python, fully unit-tested; CLI + MCP tool over the same core.
1. **Input model**: layers as `(top_NAP, bottom_NAP, value, unit, description)`. Accepted value kinds,
   each explicit: `kh [kN/m³]`, `E_menard [MPa]` (+ α, groepsfactor; reuse the logic of
   `soil_profile_utils.horizontal_spring_stiffness` from the kademuur app), or pre-computed
   `k_line [kN/m²]`. Variant `laag | gemiddeld | hoog` is a parameter, never implicit.
2. **Pile model**: from the Phase 0 read-back: begin/end node z, so position = (z_layer − z_begin)/(z_end − z_begin).
   Handle: inclined piles (use true z), z-datum ≠ NAP (explicit `model_z_of_NAP0` parameter, default 0,
   always printed back), piles split over several members, a configurable member filter (name prefix,
   layer, cross-section) instead of the kademuur `startswith("B")`.
3. **Rules the kademuur code gets wrong, fixed here**: a layer straddling the start level is clipped,
   not dropped (`scia_elements.py:505`); any error on any pile aborts the whole write (no half-bedded
   model, `scia_elements.py:530`); piles that already carry a line support are reported and skipped
   unless `replace=true` (no double stiffness).
4. **Preview first**: a table per pile (segment, from–to NAP, x1–x2, k, description) and totals.
   Nothing is written until the preview is accepted. Then write to a new file version, read back,
   compare, report `written / expected / mismatches`. Never `ok` with mismatches.
5. **Verification**: one pile hand-calculated end to end in `docs/verify/phase1-bedding.md`; unit tests
   for the geometry (vertical, inclined, split pile, straddling layer, datum shift); a golden XML.
Exit: a real geo table (or a realistic stand-in) applied to a real model copy in one command; a
geotechnical colleague has looked at the unit handling.

### Phase 2 — Pilot readiness (v0.4.1)
Minimum before any colleague touches it (VibeModel: setup failures, not model quality, blocked adoption).
1. Never kill or auto-click the engineer's SCIA: `session.py` only manages PIDs it started; `autoclick.py`
   whitelists the licence dialog and *reports* everything else (docs/08 B1–B2). ESA_XML runs get a
   timeout, a file-lock check (model open in SCIA ⇒ refuse with a clear message), and licence-seat errors
   surfaced verbatim.
2. Tool allowlist (`dontAsk` + `mcp__llsm__*`) and token on every MCP HTTP request (docs/08 B3–B4).
3. `install.cmd` + Diagnose report (Python, pythonnet, SCIA path/version, ESA_XML present, licence,
   write access to the model folder).
4. Two entry points: chat (Claude Code panel) and `llsm bedding apply …` for people without an LLM licence.
5. Prompt/answer rules: ≤5 lines, units on every number, tables; model-state stamp from the file version.
Exit: a fresh Windows user account installs and runs the bedding CLI on the sample model without help.

### Phase 3 — Pilot week
VibeModel's successcase protocol (`VibeModel/docs/successcases.md`): one case per question, replayed in a
fresh chat, score 1–5, time and tool calls logged. `scripts/successcase.py` ported. Cases never committed
(names). Output: `docs/pilot-2026-xx.md` with scores, and the ranked backlog for v0.5.

### Phase 4 — GEF → layers (v0.5.0, if the pilot confirms)
Reuse the kademuur GEF reader and `soil_profile_mesher`, then a **simplification rule** that must be
agreed with a geotechnician before coding: merge adjacent intervals into ≥ 1 m layers when the
classified soil type is the same; representative k per merged layer = conservative (min) by default,
mean on request; never merge across a soil-type boundary; description from the classification. Open
TODOs in that code (`soil_profile_utils.py:85`) are resolved, not copied.

### Phase 5 — Traffic-load positions ("treintje") (v0.5.x, if the pilot confirms)
Only after Phase 0 proves loads can be written to an existing model. Scope to agree with the requesting engineer first:
load model (EN 1991-2 LM1 axle set, or a custom axle list), a path (two points or a polyline on the deck),
step, skew; output = one load case per position + one envelope combination, named consistently. Not a
substitute for SCIA's mobile-load module — it writes what is now copied by hand.

### Deferred, with the reason
- **Combinations per code** (docs/08 D4/E5): one yes, one explicit no. Revisit after the pilot.
- **Results → IDEA**: wait for the answer on peaks. Note: ESA_XML can export results XML/XLSX
  (`output_xml (2).xml` is such an export), so the route exists if wanted — and it sidesteps the
  v24 `LoadResult` problem of docs/08 Phase A.
- **`build_model` from scratch, portal-frame benchmark** (docs/08 A4, D1): nobody asked.
- **Track G (plugin / live attach)**: still the end-state (memory: `project_direction`). One action only:
  ask SCIA support whether external-app registration is documented for 24/25. No code.

## 4. VibeModel lessons carried into every phase
Preview before write · work on a copy, never the original · read back and compare, never report success
from the tool's own return value · structured errors with a hint and candidate names · domain knowledge
as data (unit tables, variant names) not prompt prose · live verification with hand calcs ·
successcases with scores · zero-admin install + Diagnose · ≤5-line answers with units.
What we still do *not* copy: VibeModel's agent loop, euro display, prompt-rule known gaps.

## 5. Risks
- ESA_XML update may ignore or duplicate objects silently → Phase 0 diffs the export, and exit codes
  are not trusted alone.
- Unit mistakes (kN/m³ vs MN/m³, per-metre vs per-pile) are the most likely way to produce a wrong but
  plausible model → every value carries its unit through the code; preview shows them; one hand check.
- Over-fitting to one engineer → §6, and the pilot ranks the backlog, not us.
- SCIA version drift (v21 files, v24 install; a v25 may arrive) → `-r` resave tested in Phase 0.
- Licence seats: ESA_XML consumes a SCIA licence; running while the engineer's SCIA is open may fail
  → detect and explain.

## 6. People actions (owner: Quincy)
1. Reply to the hands-on engineer: ask for one real geo table (as received from geo) and one model copy;
   confirm "gif" = GEF; ask which k variant and units are delivered.
2. Ask the constructeurs the design lead's question: how do you take SCIA forces into IDEA, and what do you
   do with peaks?
3. Remind the three engineers who have not answered; add the one the design lead named who was not mailed.
4. Find a geotechnical colleague to sign off the layer-simplification rule (Phase 4) and unit handling.
5. Licence: for the pilot, LLSM must work without an LLM licence (Phase 2.4); keep the
   declare-it-back route of VibeModel as the chat option.

## 7. Housekeeping before Phase 0
Rebase `scia-attach-investigation` on `main` (main has the `mcp<2` pin and install fixes this branch
lacks); keep `phase0/` spikes. Mark docs/08 as "order superseded by 09, content still valid".
README positioning changes only after the pilot confirms the hypothesis.

## Prior attempts
- Live attach: parked (`scia_openapi_no_attach`, docs/06). This plan goes around it via files, it does
  not retry it.
- Bedding generation exists in the VIKTOR kademuur app, but for a model it generates itself; nobody has
  applied beddings to an *existing* SCIA model programmatically here. The XML "update" mode of ESA_XML is
  untested in any of our repos.
