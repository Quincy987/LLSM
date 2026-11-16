# Verify: load-case action type follows the group; duplicate names refused

Date: 2026-11-16. Code: `src/llsm/scia/builder.py`, `src/llsm/scia/tools.py`, `src/llsm/mcp_server.py`,
`src/llsm/llm/tools.py`. Route: the live model-building tools (new_model → add_* → push_model).
Saved-file routes (bedding, moving load, report) untouched.

## What was wrong
- `add_load_case` wrote every case as `ActionType.Permanent`. Reproduced on the SCIA 24 SDK before the
  fix: a `Moving` group got a `Permanent` case. Combinations built on such a model treat traffic as permanent.
- Every `add_*` added the object to the model and then overwrote the name lookup, so a second object
  with the same name landed in SCIA and the first one became unreachable by name.

Found while reviewing the fix, same file, all existing before this change:
- `AddItemToModel` returns `False`, and raises nothing, when ADM refuses an object; no add looked at it.
  A **variable case without a `Duration` is refused that way**, so the first version of this fix made
  every variable case vanish without a word. The add now raises when ADM returns `False`.
- Objects read back from the model come as `IAnalysisObject`, without their own properties. So the
  delete guard found no dependents at all (a node a beam uses could be deleted), and the model snapshot
  of the API backends' sidebar was empty. They now use the concrete object (`__implementation__`).
- A line load holds its value in `Value1` and its beam in `StructuralReference.ReferenceMember`.
  `set_line_load_magnitude` set a `Value` that does not exist: it reported ok and changed nothing.
  The delete guard and the snapshot read the same wrong names.

## What ADM accepts (pythonnet, SCIA 24, 2026-11-16, each case checked to be in the model after the add)
- `LoadGroupType`: Accidental, Fire, Moving, Permanent, Seismic, Tensioning, Variable
- `ActionType`: Accidental, Permanent, Variable. `Duration`: Long, Medium, Short, Instantaneous
- Any group kind takes any action type. A Variable case needs a `Duration`; a Permanent case must not
  have one; an Accidental one takes either. `SelfWeight` only on a Permanent case.

```
group kind   -> action type
Permanent       Permanent
Variable        Variable     duration Short unless given
Accidental      Accidental
Tensioning      Permanent    prestress is a permanent action
Moving          Variable     traffic; duration Short unless given
Fire            Accidental   EN 1990 6.4.3.3
Seismic         Accidental   EN 1990 6.4.3.4; ActionType has no Seismic
```
All seven group kinds stay accepted. `add_load_case(action_type=..., duration=...)` overrides; a
combination ADM would drop (SelfWeight in a variable group, duration on a permanent case) is refused
with a reason. The tool reports the action type and duration read back from the stored case.

## Results
- Unit suite: 488 passed, 2 skipped (was 465; 23 new in `tests/unit/test_builder_rules.py`), incl. the
  add order (name check → lands → register must not refuse itself) and ADM returning `False`.
- Real in-memory ADM through the tools (SDK loaded, no SCIA): a case in each of the seven group kinds
  lands in the model with Permanent / Variable+Short / Accidental / Variable+Short / Accidental /
  Accidental / Permanent. Duration Long honoured. SelfWeight in a variable group and a duration on a
  permanent case: `ok: False` with the reason. Duplicate node `N1`: `ok: False`, "already exists
  (StructuralPointConnection)", nothing added. A node named like the model: accepted. Deleting `N1`:
  blocked by `B1`, `S1`. Line load edit -5 → -9 kN/m: read back -9. Snapshot lists the load on `B1`.
  `ValidateModel`: ok.
- **Live SCIA push + calculation: NOT RUN.** Another session had SCIA open on the demo model
  (`brugdek.esa`, started by `scia_ctl.py`), and LLSM never starts a second SCIA beside someone's.
  To run it: `LLSM_LIVE=1 python -m pytest tests/live/test_builder_rules_live.py` on a free desktop
  (it skips itself while any SCIA runs). It checks the Variable+Short and Seismic→Accidental cases land,
  the refused duplicate, then push_model and run_analysis. Open: whether SCIA keeps the duration on push.

## Not in this change
The error envelope with codes and closest-name suggestions (docs/08 step 1-2), a configurable `Load`
relation, combinations. Unknown references (a beam on a missing node) still raise `KeyError` as before.
