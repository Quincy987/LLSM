# TODO

See `docs/08-plan-v0.4-vibemodel-parity.md` for the phased plan and `docs/07-vibemodel-lessons-for-scia.md` for the reasoning.

## Next up (Phase A — results must work)
- [ ] Run `phase0/spike_resultkey_guid.py` against live SCIA 24, record output in docs/06
- [ ] If no variant works: C# helper DLL that builds `ResultKey` + calls `LoadResult`
- [ ] Benchmark tests with hand calcs (SS beam qL²/8, two-span continuous)
- [ ] Equilibrium check (Σ reactions vs Σ loads) in `run_analysis`

## Trust (Phase B)
- [ ] Only kill SCIA processes LLSM launched
- [ ] Auto-clicker whitelist; report unknown dialogs instead of clicking
- [ ] `dontAsk` + `mcp__llsm__*` allowlist instead of `bypassPermissions`
- [ ] Token on every MCP HTTP request; refuse browser-origin requests
- [ ] Test env leak in `test_credentials.py` (rotate the exposed key)
- [ ] Rebase on main; salvage stranded v0.2.0 commits (close-before-repush)

## Known bugs
- [ ] All load cases created as `ActionType.Permanent` (`builder.py:176`)
- [ ] Duplicate names silently overwrite lookup
- [ ] README stale (docking, tool counts)
