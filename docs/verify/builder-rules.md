# Verify: load-case action type follows the group; duplicate names refused

Date: 2026-11-16. Code: `src/llsm/scia/builder.py`, `src/llsm/scia/tools.py`, `src/llsm/mcp_server.py`.
Route: the live model-building tools (new_model → add_* → push_model). Saved-file routes untouched.

## What was wrong
- `add_load_case` wrote every case as `ActionType.Permanent`. Reproduced on the SCIA 24 SDK before the
  fix: a `Moving` group got a `Permanent` case. Combinations built on such a model treat traffic as permanent.
- Every `add_*` added the object to the model and then overwrote the name lookup, so a second object
  with the same name landed in SCIA and the first one became unreachable by name.

## The SDK's enums (pythonnet, SCIA 24, 2026-11-16)
- `LoadGroupType`: Accidental, Fire, Moving, Permanent, Seismic, Tensioning, Variable
- `ActionType`: Accidental, Permanent, Variable
- ADM's `ValidateModel` accepts every pairing (21 of 21 tried), so the mapping is LLSM's choice:

```
group kind   -> action type
Permanent       Permanent
Variable        Variable
Accidental      Accidental
Tensioning      Permanent    prestress is a permanent action
Moving          Variable     traffic
Fire            Accidental   EN 1990 6.4.3.3
Seismic         Accidental   EN 1990 6.4.3.4; ActionType has no Seismic
```
All seven group kinds stay accepted (refusing four that worked before would be a regression).
`add_load_case(action_type=...)` overrides the mapping. An unknown group kind is refused with the list.

## Results
- Unit suite: 479 passed, 2 skipped (was 465; 14 new in `tests/unit/test_builder_rules.py`), incl. the
  add-order case: check name → object lands in the model → register must not refuse itself.
- Real in-memory ADM (SDK loaded, no SCIA): groups Variable / Moving / Seismic / Tensioning / Permanent
  gave cases Variable / Variable / Accidental / Permanent / Permanent; the ADM case object stores
  `ActionType = Variable` for a Moving group (was `Permanent`). Override `Accidental` honoured.
  Duplicate node `N1`: `ok: False`, "an object named 'N1' already exists (StructuralPointConnection);
  choose another name or delete it first", item count unchanged, one `N1`. Delete then re-add `N1`: ok.
  Unknown group kind `Traffic`: `ok: False` with the seven known kinds. `ValidateModel`: ok.
- **Live SCIA push + calculation: NOT RUN.** At the time another session had SCIA open on the demo
  model (`brugdek.esa`, started by `scia_ctl.py`), and LLSM never starts a second SCIA beside someone's.
  To run it: `LLSM_LIVE=1 python -m pytest tests/live/test_builder_rules_live.py` on a free desktop
  (it skips itself while any SCIA runs). It checks Variable and Seismic→Accidental cases, the refused
  duplicate, then push_model and run_analysis.

## Not in this change
The error envelope with codes and closest-name suggestions (docs/08 step 1-2), a configurable `Load`
relation, combinations, and `action_type` in the API backends' tool schema (`llm/tools.py`; there the
default follows the group, so the fix holds without it).
