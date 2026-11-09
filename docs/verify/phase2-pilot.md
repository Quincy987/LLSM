# Phase 2 verification — pilot readiness

Date: 2026-11-10. SCIA Engineer 24.0.3015.64 on this machine, Claude Code 2.1.318.

The plan as first written decided which SCIA processes were ours after HubServer appeared, but the licence dialog
has to be dealt with before that, so a launch would have hung. Ownership is therefore settled from the moment of launch:
descendants of the LLSM process, seen every half second. Later additions to the plan are the Qt licence notices and
`--tools ""`; see below.

## Results

| Check | Result | Evidence |
|---|---|---|
| Fresh clone, fresh venv, `install.cmd` | PASS | Folder with spaces in the name: exit 0 in 338 s, ending in a full `llsm diagnose`. A fresh Windows account needs admin rights to create; this stands in for it. |
| `install.cmd` a second time | PASS | "Virtual environment already there", no errors. |
| `llsm bedding preview` from the fresh install | PASS | Correct table on a model copy. |
| `llsm diagnose` | PASS | All lines correct; an expired licence token shows as a warning with what to do; no token text in the report. |
| Tools > Diagnose… | PASS | The real method on a Tk root: report shown, Copy puts it on the clipboard. The full panel was not opened. |
| Hand-started SCIA, `llsm bedding apply` | PASS | Refused: "SCIA is running". The hand-started SCIA stayed open. |
| Session launched next to a hand-started SCIA | PASS | Nothing of it in the ledger, hand-started processes alive afterwards. The launch failed with "another SCIA is already running". It took 333 s then; it now stops as soon as `RunSCIAEngineer` returns without a SCIA of ours, which was not re-run. |
| Licence notice during a launch | PASS | Reported as `SCIA_LICENCE_NOTICE` after 11.5 s. Phase 0's run hung until it timed out. |
| Own processes only | PASS | Ledger held SciaEngineer and the startup screen; `close()` stopped those and cleared the ledger. |
| Full launch up to HubServer | PASS | 26.5 s with `LLSM_ANSWER_LICENCE_NOTICES=1`: four processes, HubServer among our descendants, SCIA quit on close. This was the one run that answered the notices. |
| Whole model through the session | PASS | New beam, launch 19.8 s, push 13.9 s, analysis 13.5 s, no false dialog during the calculation, clean close. SCIA's Win32 licence question came up during the push and was clicked. |
| MCP server without token | PASS | 401, also on `/mcp` and `/admin/shutdown`. |
| With an `Origin` or `Sec-Fetch-Site` header | PASS | 403, even with the right token. Wrong `Host`: 403. |
| With the token | PASS | 200; the shutdown route ends the server. |
| Claude turn through the MCP config with the token | PASS | Reached the LLSM tools (`model_summary`, see below). |
| Claude has no other tools | PASS | Asked to run a shell command: "I don't have a Bash tool in this session". |
| `pytest tests/unit` | PASS | All pass, including the former environment leak. |
| `ruff check --select E9,F63,F7,F82 .` | PASS | Clean. |

## Tool permissions

`dontAsk` with `--allowedTools "mcp__llsm__*"` still let a read-only shell command through (`echo` ran, no denial
recorded). So the backend also passes `--tools ""`, which removes the built-in tools altogether; with that, the shell
is gone and the LLSM tools still work.

## Not tested

- "Onjuist afgesloten projecten" on a real launch: it comes after the licence notices, and only after a forced stop. The
  reporting is covered by unit tests.
- The full panel with a live chat. The backend's command line and each part were tested separately.
- A real fresh Windows account.
- `--geometry-from` on a real hand export from SCIA; the unit tests use the sample's table layout.
- `bedding_preview` by name in a chat turn: the small model used for the check called `model_summary` instead. The earlier
  MCP dispatch test in `phase1-bedding.md` covers that tool.

## One launch timed out

Of the five launches through this code, one ended after about six minutes in "HubServer did not spawn", right after the cached
licence token had expired; the two launches after it, one the same script, took 20 to 24 s. The error reached the caller and
the session was cleaned up, so a retry works. The cause is not known.

## Dependency pin

The fresh install pulled pythonnet 3.2.1, newer than the 3.0.5 everything here was tested on, so `pyproject.toml` now
keeps pythonnet below 3.1.

## The code manager value

`phase0/xml_route/codemanager_probe.py` on the Phase 0 files: one double at offset 2391 of the `EP_CodeManager` table,
100 to 0, in a run of doubles that are 100 or 0. That table holds no readable names near it, so which setting it is
stays **unknown**. Before a pilot it needs one comparison in SCIA of the code settings before and after an update.

## Side effect

The full launches answered SCIA's licence notices (`LLSM_ANSWER_LICENCE_NOTICES=1`), as the first Phase 0 runs did: `UserSelection.json` was written again.
