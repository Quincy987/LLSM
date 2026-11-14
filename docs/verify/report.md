# Verify — results report of a saved model (v1)

Date: 2026-11-13/14. SCIA Engineer 24.0.3015.64, ESA_XML, copies only, SCIA closed for every
run. Plan: `docs/13-plan-report.md`; spike: `docs/verify/report-route.md`. Scripts:
`phase0/report/verify_app.py` (the app runs below), `phase0/report/demo/take7.py` (the demo);
outputs in the gitignored `phase0/report/work/`.

## What v1 does

`report_write` (MCP) / `llsm report write` (CLI) prints the engineering report stored in a saved
`.esa` with ESA_XML (`NONE <copy> -sd -t<fmt> -o<out> -d<report>`), always as XLSX first, then the
asked formats (PDF by default). The XLSX is read back by `llsm.report.check`: `ok` only when every
results table holds data rows and no results item is of the old type. A failed check stops before
the PDF and keeps the XLSX as `_FAILED`. `calculate` runs `LIN` on a copy first; `cases_group`
writes, on that copy only, one linear combination (coefficient 1) and one result class per load
case of an LLSM moving-load group, so the report prints a chapter per case.

## Through the app

The MCP server started from this build (`python -m llsm.mcp_server --http`, PYTHONPATH = this
checkout's src), called with a real MCP client and its token, as the panel's chat does; then the
CLI. Each model was copied first; the SHA-256 of all three copies is unchanged after all runs.

| Run | Result |
|---|---|
| `report_preview` plate (calculated, `Report_1`) | ok, 3 s; report, formats xlsx + pdf, outputs planned |
| `report_write` plate, `report_name="Report_9"` | refused, 3 s: "No report named 'Report_9'; reports in the model: Report_1"; no ESA_XML run |
| `report_write` brugdek_llsm-01 (moving-load copy, not calculated) | refused, 2 s: no results stored, use calculate |
| … with `cases_group` but no `calculate` | refused: cases_group needs calculate |
| … `calculate`, `cases_group="LLSM-ML TS9"` | refused, 14 s: group not found; groups LG1, LLSM-ML TS1 |
| `report_write` plate | **ok, 609 s**: 21 of 21 results tables filled; PDF 32 pages, 7.3 MB |
| `report_preview` brugdek, calculate + cases_group | ok, 14 s |
| `report_write` brugdek, calculate + cases_group "LLSM-ML TS1" | **ok, 933 s**: 35 of 35 filled; class *Alle UGT* reported as empty class (see below); PDF 55 pages, 11.5 MB |
| CLI `llsm report write` on the v19.1 kademuur copy | **refused, 498 s** (exit 1): 0 of 10 results items with results, 10 of the old type; reason and fix printed; `…_rapport-01_FAILED.xlsx`; no PDF made |

Values, read from the printed XLSX:

- Plate, *Alle UGT*: column forces C1 −232.68, C2 −217.32, C3 −217.32, C4 −232.68 kN; resultant of
  reactions R_z = 900 kN. The same as the spike's and S3's hand check (−232.7 / −217.3, 900).
- Brugdek, one chapter per case: resultant of reactions 600 kN in *LLSM-ML TS1 01*, *02* and *03*,
  each from its own combination (`LLSM-ML TS1 0n/1`) = LM1 TS1, 2 × 300 kN. Column forces per case
  (kN): 01: −248.82 ×2, −51.18 ×2; 02: −165.94 ×2, −134.06 ×2; 03: −218.4 ×2, −81.6 ×2; each sums
  to −600 kN.

## Found on the way

- **SCIA files the new combinations into the template's classes itself.** After the update, the
  template's *Alle BGT* and *Alle UGT+BGT* list the three new combinations (type *Lineair - BGT*),
  so they print all three cases' extremes too. *Alle UGT* stays empty: its tables print SCIA's
  "Fout E-C04: Niet alle items in deze klasse zijn berekend" and no data. The check reports such a
  class (one whose class sheet lists no combinations) apart, as `empty_classes`, with a warning:
  it is the model's class, not a print failure. A table without data in a class that has
  combinations still fails the check.
- **The per-case combinations also land in the model's own classes.** The template's *Alle BGT*
  and *Alle UGT+BGT* take every combination of their type, so on the copy they list the three
  per-case combinations too, and their chapters print those cases' extremes. On a real model that
  could change what such a chapter shows. The check now reads each class's list of combinations
  and the result names the model's classes they joined (here *Alle BGT*, *Alle UGT+BGT*).
- **The stored-results count is result sets, not load cases**: 4 on the plate (4 cases), 32 on the
  real model (8 cases, 22 combinations). It shows whether results are stored; "fewer result sets
  than load cases" is used only as a hint that cases were added after the last calculation.
- **Run times**: XLSX 3–5 min and PDF 5–10 min on a 36 MB model; with `calculate` 1.5 min more.
  A panel turn could only last 10 min: the limit is now 45 min.
- **No openpyxl needed**: the XLSX is read with the standard library (`llsm.report.xlsx`); on the
  spike's 54-sheet export it gives the same cells as openpyxl.

## Through the panel (take 7)

The real panel, run from this build (`phase0/report/demo/take7.py`), one typed message:
"Maak een rapport van …\brugdek_llsm-01.esa, per belastinggeval, met doorrekenen."

- **First take: failed, and found two bugs.** `report_write` was one blocking call. Claude Code
  2.1.318 ends an MCP call over HTTP after 300 s, also with `MCP_TOOL_TIMEOUT` raised, so the chat
  answered "after 300 s cut off by the MCP client's time-out" while ESA_XML went on printing; and
  when the panel closed, that ESA_XML kept running as an orphan (stopped by hand; it was ours).
  Fix: `report_write` checks the file, starts the print as a job and returns at once;
  `report_status(job)` waits at most 240 s per call and returns the full result when done; one
  print at a time. On server shutdown `esa_xml.stop_all()` stops this server's own ESA_XML runs.
- **Second take: works.** Tools called: report_preview, report_preview (with the options),
  report_write, report_status, report_status. 7 min 6 s. The answer named both files, "Alle 35
  verwachte hoofdstukken zijn geschreven", the empty class *Alle UGT* with its E-C04 and why, and
  that the model itself is unchanged. The panel showed `report_write` with a red cross: a started
  job returned `ok: false`, which the panel reads as failed. Fixed: a running job is `ok: null`.
- **Third take**, with that fix: the demo video, `LLSM demo\LLSM-report-demo.mp4`.

## Fail cases

| Case | Where | Result |
|---|---|---|
| Wrong report name | MCP, plate | refused with the names found, before ESA_XML |
| No report in the model | unit test | refused, with the fix text |
| Several reports, none named | unit test | refused, asks which |
| Not calculated, no `calculate` | MCP, brugdek | refused |
| Unknown `cases_group` | MCP, brugdek | refused with the groups found |
| Nonlinear combinations + `calculate` | unit test | refused: LIN only |
| Old-type report | CLI, real model | refused; PDF not made |
| Empty results table | unit test | refused |
| ESA_XML exit ≠ 0 | unit test | refused, log tail in the problem; nothing written |
| Server shut down during a print | `phase0/report/verify_shutdown.py`, live | `report_write` started a job, ESA_XML ran; `/admin/shutdown` (the panel's first layer): server exit 0, no ESA_XML left, and (after a fix: the teardown now waits for the job to clean up before the hard exit) no 36 MB working copy left in Temp |
| Job route after the second review | `phase0/report/verify_job.py`, live | `report_write` returned in 1.2 s (running, `ok` null, version stamp); one `report_status` returned done after 188 s: 21 of 21 tables, XLSX next to the model |
| Final run after the third review | `verify_job.py` ×2 + CLI, live | plate: `report_write` 2.9 s, done after 183 s, 21 of 21; v19.1 copy: done after 217 s, refused, 10 old-type items, fix text, `_FAILED` XLSX, no PDF; CLI preview plans `_rapport-02` next to an existing `-01`. No ESA_XML, port or temp folder left |
| Two status calls at once | in-process, MCP server | 3.0 s wall time for two 3-s waits and another tool: the waits no longer queue on the shared worker |
| SCIA open | `esa_xml.run_on_copy` → `assert_no_scia` | not triggered live: opening SCIA on the desktop for it was not worth the risk; the guard is the one bedding and moving load use (`tests/unit/test_esa_xml.py`) |
| 0-byte output | — | not reproducible with `-sd` (only `-tTXT` did it in the spike); the check stays in the code |

## Tests

- `pytest tests/unit`: 389 passed (27 new in `test_report.py`, on XLSX fixtures cut from the
  spike's and these exports).
- Live (LLSM_LIVE=1): `tests/live/test_bedding_live.py`, `test_moving_load_live.py` and the new
  `test_report_live.py` (plate, XLSX): 5 passed in 7 min 54 s.

## Open

1. The standard LLSM report template (plan 13 §2) and its gate (§4.1): does a template made on
   one model pick up another model's members and result classes? Needs SCIA's GUI.
2. The questions of plan 13 §6 for Sebastiaan and Berber.
3. The English UI texts the check looks for ("No supported task", "Empty table") are a guess;
   only the Dutch texts were seen.
