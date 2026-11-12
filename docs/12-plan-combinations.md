# 12 — Plan: propose load combinations for an existing SCIA model

Date: 2026-11-12. Status: proposal, nothing built. Builds on `09-plan-v0.4-existing-models.md`
("Deferred: Combinations per code") and `08-plan-v0.4-vibemodel-parity.md` D4/E5.
Prior attempts: none in code (`git log -i --grep=combination` is empty); D4/E5 were planned, never built.
`builder.add_load_case` exists for live-built models only and always writes `ActionType.Permanent`.

## 0. Who asked, and the rule that follows
- Berber (2026-11-12): "Jouw voorbeeld van de belastingcombinaties + beddingen zouden erg handig zijn."
- Sophie (2026-11-06), against: "Ga niet alle combis maken … kijk eerst wat maatgevend is."

**Not negotiable:** the tool *proposes* a table and writes nothing until the engineer confirms it.
Dropping rows is one sentence. It never generates "all" combinations: every row needs a reason
(a named leading variable load, a named SLS check). It is opt-in: LLSM never offers combinations
unprompted, and nothing in the bedding flow touches them.

## 1. v1 scope
**Input.** A saved `.esa`. The tool reads, without SCIA open:
- load groups: name, permanent/variable, relation (standard / exclusive / together), load type
  (SCIA's "Load": domestic, office, snow, wind, traffic, …, which decides ψ);
- load cases: name, action type, group;
- existing combinations: name only (for collisions, §4).

**The engineer's one line.** Code + situation, e.g. *"NEN-EN 1990 + NB, STR set B, CC2, plus SLS
quasi-permanent, verkeer leidend"*. Parsed into a fixed request (code, set, CC, SLS kinds, leading
load). Anything missing is asked, never defaulted silently; only CC has a stated default (CC2, shown).

**Proposal table.** One row per combination: `#`, name, type (ULS 6.10a / 6.10b / SLS char / freq / qp),
`case × factor` for every case, and a source column (§2). Size rules: one 6.10a row; one 6.10b row per
*named* leading variable load; one row per named SLS kind. Exclusive groups: one row per case in the
group only if the engineer asks; otherwise the proposal asks which case governs. Hard cap 12 rows:
above it the tool refuses and asks which load leads. Favourable permanent (γG,inf) only on request
(uplift / stability of a kademuur is the engineer's call, not ours).

**Then** the engineer drops/edits rows, confirms, the rows are written to a *new* file
(`<model>_llsm-N.esa`, as for beddings) and read back.

**Code combination vs. linear.** SCIA has both:
- *Code combination* (EN-ULS set B/C, EN-SLS char/freq/qp): SCIA expands it itself from the group
  types/relations and the code manager settings (NB, consequence class). That expansion **is**
  "all combinations", and its factors live in settings we cannot read reliably (the unknown
  100 → 0 change in the code manager table, `docs/verify/phase0-route.md`).
- *Linear combination* (linear ULS / SLS): explicit factors per case, exactly the confirmed table.

**v1 writes linear combinations only.** What the engineer confirmed is what is in the model, every
factor has a visible source, and nothing depends on SCIA's code-manager state. A code combination
row is v1.1, only if Berber asks for it (useful as SCIA's own envelope check next to ours).

## 2. Factors as data, not prompt prose
File per code + NB: `src/llsm/standards/nen-en-1990-nb-2011.toml` (tomllib, stdlib, comments allowed).
```
[meta]   code, annex (A1 buildings), nb edition, status = "unconfirmed", confirmed_by, confirmed_on
[[gamma]] id, set (B), cc (CC1/2/3), expression (6.10a/6.10b), G_sup, G_inf, Q, Q_times_psi0, source
[[psi]]   id, category (A…H, snow, wind, temp), scia_load (the SCIA "Load" enum value), psi0, psi1, psi2, source
[[sls]]   kind (char/freq/qp), which psi applies to leading / accompanying, source
```
- Every row has `source` = clause/table + edition (e.g. "NB:2011 tabel A1.2(B), CC2") and `status`.
- Loader rejects unknown keys, missing sources, and a `scia_load` that maps to two categories.
- Each proposal row carries the ids of the factor rows it used; the answer prints them (§7).
- Code (`combinations/plan.py`) only does the arithmetic and the row rules; no factor in Python.

**Senior engineer must confirm before shipping (flagged `unconfirmed` until then):**
γG,sup / γG,inf / γQ per CC for 6.10a and 6.10b (incl. the NB's ξ-reduction), the ψ0/1/2 table,
the SCIA-load-type → category mapping, the SLS ψ rules, and whether 6.10a+b (not 6.10) is the office
practice. Until `status = "confirmed"` apply only works with a developer flag, and every answer says
"factoren nog niet bevestigd".

## 3. Step 1: route spike (gate), on copies of a real model
| # | Question | Pass | Fail → fallback |
|---|---|---|---|
| R1 | Read groups (type, relation, load) and cases (action, group) from the `.esa` | Extend `esa_file.py` reader: find the progids with `phase0/xml_route/esa_names.py`; counts match SCIA's object log; names/types match the model in the GUI on 2 models | Engineer's hand XML export of the load tables (`read_xml` path); if that also fails: engineer pastes the table into chat |
| R2 | Same via ESA_XML export | Model tables present | Expected to fail as in Phase 0 (no XML IO document); not blocking |
| R3 | Read existing combination names | As R1 | Ask the engineer for the prefix used |
| W1 | Write 2 linear combinations (ULS, SLS; 3 cases each) via ESA_XML update | Need the combination table def (trim `DataSetSciaTom.EP_LoadCombi.1` from `phase0/xml_route/defs/viktor_full.xml.def`, store as `resources/combinations.xml.def`). Saved file reopens; read-back factors exact; `diff_esa.py` shows only the combination table + bookkeeping | XLSX update (ESA_XML mode 10) → OpenAPI on an opened project (`UpdateProject`, never reached in Phase 0: HubServer timeout; needs existing case GUIDs) → manual: we deliver the confirmed table, engineer types it |
| W2 | Edge cases | Wrong case name, existing combination name, same update twice: record what SCIA does (supports: wrong names attached silently, same name overwrote) | — |
| W3 | Code manager value 100 → 0 | Identify it (`codemanager_probe.py`) before shipping: it may be a combination setting | Block shipping until known |
Report in `docs/verify/combinations-route.md`, same format as phase0-route.

## 4. MCP tools and the chat interaction
Package `src/llsm/combinations/` mirroring `bedding/`: `read.py` (cases/groups), `request.py`
(the one line → fixed request), `plan.py` (rows), `apply.py` (write + read back), `cli.py` (no-chat use).
- `combinations_preview(model, code, set, cc, sls, leading, drop=[], edit=[])` → numbered table,
  sources, warnings (variable group holding a permanent case, case in no group, unknown load type),
  and `proposal_hash`. Writes nothing.
- `combinations_apply(model, rows, proposal_hash, replace=false)` → takes the *confirmed rows*
  explicitly; refuses if the hash doesn't match the model as read now, if a case name is not in the
  model, or if a factor differs from the data file without `override=true` on that row.
- **Dropping rows:** "laat 3 en 5 weg", "geen SLS", "alleen 6.10b met verkeer" → the agent calls
  preview again with `drop`/`edit`; the panel shows the new numbered table. Apply only after an
  explicit "akkoord/schrijf maar".
- **Names:** `LLSM <type> <lead>`, e.g. `LLSM ULS 6.10b Verkeer`, `LLSM SLS qp`; ≤ SCIA's name
  length (spike W2). **Collisions:** an existing name is refused; `replace=true` only overwrites
  combinations starting with `LLSM `; the engineer's own combinations are never touched.
- Fix the `builder.py:176` bug in the same release (action type from the group), so LLSM-built
  models don't read back as all-permanent.

## 5. Verification
- **Hand check:** 3 cases (G permanent, Q1 domestic/traffic, Q2 snow or wind), CC2, set B + SLS qp.
  Compare row by row with a worked example the senior engineer picks (NB table A1.2(B) applied by
  hand, or a Dutch Eurocode handbook example: page reference recorded). Every factor equal, every
  row present, no extra rows.
- **Read-back** after writing: names, types, case × factor exact; no other table changed (diff vs a
  plain re-save). `ok` only from the read-back, never from the exit code.
- **Optional SCIA cross-check:** on one copy, add SCIA's own EN-ULS set B code combination by hand;
  our linear rows must be among the combinations it generates.
- Tests: data-file loader, row rules (cap, exclusive groups, missing lead), request parser, apply
  against a fake writer, as `tests/test_bedding_*`.

## 6. Questions for Berber, and Sophie's objection
Sophie's objection stays honoured by design: opt-in, propose-only until confirmed, no "all", her
own combinations untouchable. Ask her one thing later: is the proposal table useful to her as a
*check* of what she made by hand (no write)? No push.

Mail draft (Berber):
```
Hoi Berber, Leuk dat de combinaties + beddingen je handig lijken. Voordat ik iets bouw wil ik zeker weten dat het past bij jouw werk: 1) Welke norm en situatie bedoel je: NEN-EN 1990 + NB voor gebouwen, bijlage A2 (bruggen/verkeer), of NEN 8700 voor bestaande constructies (kademuren)? 2) Welke combinaties maak je nu meestal: STR/GEO set B (6.10a/b), set C, en welke BGT-combinaties (karakteristiek, frequent, quasi-permanent)? Welke gevolgklasse? 3) Mag ik een kopie van een recent model met jouw belastinggevallen en -groepen, plus de combinaties die je er zelf in gezet hebt? Dan vergelijk ik mijn voorstel rij voor rij met het jouwe. Hoe het gaat werken: LLSM leest de belastinggevallen uit je .esa en stelt een tabel voor (naam, factoren per geval, met de bron uit de NB). Er wordt niets geschreven tot jij akkoord geeft, je streept rijen weg die je niet wilt, en hij maakt nooit "alle" combinaties. Het resultaat komt in een nieuw bestand, je origineel blijft onaangeroerd. Groet, Quincy
```

## 7. Risks and effort
- **Wrong factors are a safety risk.** Mitigations: data file with sources and a senior sign-off;
  every answer shows per row "bron: NB:2011 tabel A1.2(B) CC2; A1.1 cat. A" and the file status;
  the hand check in §5 is a release gate; nothing is written without explicit confirmation.
- Wrong load type in the engineer's model → wrong ψ. The preview lists the type per group and asks
  when it is unknown or unusual (variable group with "permanent" load type).
- Undocumented `.esa` layout for load tables: same guard as members (counts vs SCIA's log, stop on
  mismatch, tested versions only).
- Scope creep into "all combinations" or code combinations: §1 cap, linear-only v1.
- Thin demand (1 yes, 1 no): build only after Berber answers and sends a model; spike first.

**Effort, in scope:** spike (reader extension + one def + 5 edge runs, on copies). Then one new
package mirroring `bedding/`, one data file, one def resource, 2 MCP tools, one builder bug fix,
tests and a verify doc. Roughly the size of the v0.3.6 bedding release; the factor sign-off by a
senior engineer is the long pole, not the code.
