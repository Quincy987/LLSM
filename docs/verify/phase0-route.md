# Phase 0 verification — reading and writing an existing SCIA model via XML update

Date: 2026-11-09. SCIA Engineer 24.0.3015.64, on copies only. Scripts: `phase0/xml_route/`
(models and logs in the gitignored `work/` folder). Plan: `docs/09-plan-v0.4-existing-models.md`.

Test models:
- **Real**: a kademuur model last saved in v19.1 (`+D_261_19.1.3030.32`), 50.7 MB, 32 piles, 319
  line supports.
- **Known**: `pile_base.esa`, built from `paaltje.xml` on the kademuur template; one pile B1.

## Decision

**GO for the file route, with two conditions.** Writing to an existing model works, and the stiffness
unit is measured. Two things stay open before a pilot:

1. Pile geometry comes from reading the `.esa` directly, because the supported export has no model
   tables in these projects. That format is undocumented: Phase 1 needs a SCIA version check on it,
   and a hand export as fallback (not yet tried).
2. A real update changes one numeric value in SCIA's code manager table, from 100 to 0, and it is
   not known which setting that is (see "What else changed").

## Questions

| Q | Question | Result | Evidence |
|---|---|---|---|
| a | List piles with coordinates, no OpenAPI | **PASS, via undocumented reader** | `parse_members.py` on the real copy: 32 members with begin/end coordinates, e.g. S1 (0.3, 1.31, -1.1) to (-3.02, 1.31, -19.5), L = 18.697 m. On the known model it matches `paaltje.xml`: B1 (0, 0, 0) to (0, -2.5, -10), and the same supports as the XML reader. |
| a | Same, through the supported XML export | **FAIL** | Real copy: exit 9, log "standaard document wordt gemist". Template model: exit 0 but only result tables. Creating a model document through an update: no table for it found, not attempted. By hand: NOT TESTED (needs the desktop). |
| b | Add two supports to an existing member by name | **PASS** | Real copy, `ESA_XML.exe NONE in.esa update.xml -tESA -oout.esa` with `viktor.xml.def`: exit 0, 74.9 s. Both records present with the written positions and stiffness. The name decides the member: "S4" sent with id 1 was stored against id 6. |
| b | Right units | **PASS, calculated** | `numeric_check.py`, see "Stiffness unit". SCIA's property panel itself: NOT TESTED. |
| c | Exactly those supports added | **PASS, with one open value** | `diff_esa.py` on all 768 tables, see "What else changed". |
| c | Result reopens | **PASS** | Headless reopen and re-save of the updated real copy: exit 0, both supports intact. GUI opened the updated known model (`img/phase0-updated-pile-in-scia.png`). |
| d | Same update through an OpenAPI session | **NOT TESTED** | Two attempts stopped at startup: "Server start timeout", HubServer did not start. `UpdateProject(TypeOfExtProject, string, ApiGuid)` exists but was never reached. Not needed: the MCP server can run ESA_XML as a subprocess, without the hub. |

The required screenshot of the two supports with their stiffness is **not met**. SCIA's Qt panels do
not render through window capture and are not exposed to UI Automation, and driving the real mouse
would have taken over a desktop in use. The image shows the updated pile model open in SCIA, with
no way to tell the two new supports apart. The calculation below replaces it as evidence for the unit.

## Stiffness unit

The literal in the XML is N/m². Measured on the known pile (D = 0.1 m, self-weight only), with all 25
supports overwritten through an XML update and SCIA's own result table read back. SCIA reports in SI
(axial force 1908 N, about the pile's weight). The update also adds a small spring about the pile's
axis; see the empty-results point below.

| | SCIA, literal 1500 | Hand, read as N/m² | Hand, read as kN/m² |
|---|---|---|---|
| Movement across the pile, top end | 57.1 mm | 51.5 mm | 0.052 mm |
| Movement across the pile, toe | 24.1 mm | 21.9 mm | 0.022 mm |

The hand value treats the pile as rigid (SCIA/hand 1.10 to 1.11); the alternative is off by a factor
of 1000. Doubling the literal to 3000 reduces the largest movement by 1.85.

So bedding in kN/m² has to be multiplied by 1000 when written. `paaltje.xml`'s 2450 looks like that mistake.

## What else changed

Compared with a plain re-save of the same input, and with a second re-save as control (revision
stamps masked). 758 of 768 tables are identical. 4 differ between the two re-saves (generated ids)
and cannot be judged this way; their object names are compared and unchanged. Member
geometry is unchanged, names added are `LLSM:1` and `LLSM:2`, none removed. An update with an empty
XML changes nothing at all. The script's own verdict is "OTHER DIFFERENCES FOUND", for these:

| Table | Change | Read as |
|---|---|---|
| Line supports | +1900 bytes, 319 to 321 objects | The two new supports. |
| Members | +8 bytes, geometry unchanged | Dependency list growing (not decoded). |
| Object count | 319 to 321 | Bookkeeping. |
| Revision log, registry | Revision numbers, one counter | Bookkeeping. |
| **Code manager** | **One value 100.0 to 0.0** | **Unknown. Has to be identified.** |

## Edge cases

Fresh copy each; judged on the saved file.

| Case | Exit | What SCIA did |
|---|---|---|
| Name right, id wrong (B1 / 999) | 0 | Attached to B1. Name wins. |
| Name wrong, id right (NOPE / 1) | 0 | Attached to id 1, silently. |
| Name and id wrong (NOPE / 999) | 0 | **Saved a support pointing at a member that does not exist.** No error. |
| Name of an existing support (Sn:1) | 0 | **Overwrote that support**, no new object. |
| Same update twice | 0 | No duplicates; second run changes nothing. |
| Decimal comma in stiffness | 0 | Accepted: "1500000,5" stored as 1500000.5. |
| v20 file, resave switch (`modeloud.esa`) | 0 | "Resave required, read 20.0.5007, actual 24.0.3015". Update on it NOT TESTED; the real model (v19.1) covers the upgrade path. |

## What Phase 1 has to take from this

- **Exit 0 proves nothing**, for updates and for calculations (an empty result table also gives
  exit 0). Check names against the member list before writing, and read back after.
- **An unstable model also gives an empty result table with exit 0.** The sample pile, free to twist
  about its own axis, returned empty tables for some stiffness values and not others. With a spring
  about the axis both runs gave results, which points at the twist, on two runs.
- **Support names are keys.** Re-running is safe; a name collision overwrites someone's support.
- **A save upgrades the file.** The real copy went from 50.7 to 40.7 MB (v19.1 to v24) and lost
  mesh-related objects, so diff against a plain re-save, and tell the engineer to recalculate.
- **The supports already in the real model use another record layout.** The reader returns no
  positions for those.
  Finding which piles already carry a bedding needs that decoded first.
- The reader compares its counts with the object counts SCIA logs in the file and stops on a
  mismatch. Positions can run just past the end of a pile (1.0044 in the sample).
- Real pile levels look like NAP (-1.1 to -19.5) in this model; not confirmed.
- `ESA_XML` only saves with `-tESA -o<path>`, so the source file stays untouched by construction.
  Its paths must be absolute.

## Licence behaviour

- An expired cached token gives exit 6, "Licence error: refresh access token timeout". Opening the
  SCIA GUI once renews it; ESA_XML alone does not. Diagnose has to report this.
- At startup SCIA can ask two Qt questions: modules taken by colleagues (here Professional Edition
  EN and Traffic Loads 2D), and "more than one mandatory licence". No ESA_XML run stopped on a
  question: all of them ended by themselves.

## Side effects of this spike

- Answering those questions with Return rewrote SCIA's licence selection on this machine
  (`UserSelection.json`, 14:32). The previous content was not saved. Check it in the licence settings.
  `openapi_update.py` no longer does this unless asked with `--answer-licence`.
- Three SCIA sessions were stopped by force (one licence probe, two OpenAPI attempts), which can
  hold a seat until the token expires (about an hour) and shows "Onjuist afgesloten projecten" at
  the next start. "Doorgaan" is safe; those entries are work copies.

## Open

1. Which setting the 100-to-0 value in the code manager is, and whether it matters.
2. Hand export of model tables, as the fallback for the undocumented reader.
3. SAF: not tested, and no longer needed for this decision.
4. A model that is open in SCIA while ESA_XML runs (file lock): not tested.

## Tests

`pytest tests/unit`: 41 passed, 1 failed. The failure, `test_credentials.py::test_delete_clears`,
is the environment leak listed in Phase 2. This work changed three things under `src/`: the login
error fix in the credentials wizard, and the description of `refresh_from_scia` in two places. No
SCIA behaviour changed.
