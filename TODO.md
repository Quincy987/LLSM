# TODO

Plan: `docs/09-plan-v0.4-existing-models.md` (order) + `docs/08-plan-v0.4-vibemodel-parity.md` (trust/verify detail) + `docs/07-vibemodel-lessons-for-scia.md` (why).

## Phase 0 — route spike on a copy of a real model (gate)
- [x] Read members+nodes: works from the `.esa` directly (undocumented reader); `ESA_XML.exe -x` has no model tables in these projects
- [x] Write 2 line supports on an existing beam by name via `ESA_XML.exe NONE copy.esa upd.xml -tESA -oout.esa`; read back; diff against a re-save
- [ ] Same via OpenAPI `UpdateProject(TypeXML)` (not reached: HubServer did not start, twice; not needed for the decision)
- [x] Edge cases: duplicate name, missing member, decimal comma, older file (`-r`)
- [x] Decision in `docs/verify/phase0-route.md`: GO, with conditions
- [ ] Find out what the one setting is that an update changes in the code manager table (100 to 0)
- [ ] Hand export of model tables, as fallback for the reader

## Phase 1 — bedding core (no chat required)
- [x] `llsm.bedding`: layers (NAP, value+unit, description) x piles (begin/end z) -> segments; variant laag/gem/hoog explicit
- [x] Clip straddling layer; abort on any pile error; skip/replace piles that already have supports
- [x] Preview table -> write new file version -> read back -> compare
- [x] Unit tests (vertical, inclined, split pile, datum shift) + one hand-calculated pile in docs/verify
- [ ] Decode where existing supports sit on a pile (now only counted)

## Phase 2 — pilot readiness
- [ ] Only manage SCIA PIDs LLSM started; auto-clicker whitelist, report unknown dialogs
- [ ] ESA_XML: timeout, file-lock check, licence errors verbatim
- [ ] `dontAsk` + `mcp__llsm__*` allowlist; token on every MCP HTTP request
- [ ] install.cmd + Diagnose; CLI entry point for colleagues without an LLM licence
- [ ] Fix test env leak in `test_credentials.py` (rotate the exposed key)

## People
- [ ] Reply to the hands-on engineer: real geo table + model copy; confirm GEF; k variant/units
- [ ] Ask constructeurs how forces go to IDEA / peaks
- [ ] Remind the other engineers; mail the one not yet asked
- [ ] Geotechnical colleague for layer rule + units

## Housekeeping
- [x] Rebase branch on main (mcp<2 pin, install fixes)
- [ ] Known bugs: load cases always `ActionType.Permanent` (`builder.py:176`); duplicate names overwrite; README stale
