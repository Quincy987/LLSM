# 13 — Plan: a results report for a saved SCIA model

Date: 2026-11-13. Status: proposal after a spike (`docs/verify/report-route.md`), nothing built.
Asked by Sebastiaan van Rossum (mail 2026-11-12, "een SCIA Engineering report … alle resultaten van
de verschillende belastingsgevallen en resultaatklassen op alle onderdelen van de constructie
geordend") and Berber Renckens (a report is a lot of manual work).

## 1. Recommendation: v1 prints the model's own SCIA engineering report (route A)

Why this route, from the spike:
- It is literally what was asked: SCIA's engineering report, with SCIA's own tables, units, signs
  and pictures. LLSM maps no columns, so the column/sign risk of docs/11 does not arise.
- The real models we have already carry one (`Report_2`, `Report_3`); the kademuur template has
  `Report_1`, built exactly as asked: per part (supports, piles, floor, wall), then per result
  class. The export document route B needs was **missing** in the real copy (phase 0, exit 9).
- `ESA_XML NONE copy -sd -t{PDF|XLSX|RTF} -o… -d<name>` printed it headlessly; a wrong name fails
  (exit 8). Neither SCIA document can be added by an XML update (the results container in the
  definition is read-only; documents are not declared at all), so in both routes the engineer
  makes the layout once in SCIA. Route A asks for the step they already do.

**Gate before building:** the pending runs in the verify doc (results filled on a calculated
plate, values −232.7 / −217.3 kN in the report, and the real v19.1 copy printing from stored
results). If stored results do not survive, v1 recalculates on the copy, and only when the
model's own analysis is linear (as docs/11 §2.3); otherwise it refuses and says why.

Route B (LLSM writes a Word report from the export) stays the follow-up for what route A cannot do:
a house-style Word document, all rows instead of SCIA's selection, ordering across result classes
per member, combining with the apps' design report. The prototype (`phase0/report/route_b.py`) is
kept for that; it reads back every cell.

## 2. v1 scope

- **In**: a saved `.esa` (SCIA closed), optionally a report name (default: the only one; if
  several, the engineer picks), format PDF (default) and/or XLSX (tables only) or RTF (editable).
- **Out**: `<model>_rapport-01.pdf` next to the model, never overwriting (`-02`, …); the source
  model is never written (ESA_XML on a copy).
- **Ordering**: SCIA's report tree, as the engineer built it (chapter per part, then load case /
  result class). LLSM does not reorder in v1; ask in §5 whether that is enough.
- **Out of scope**: composing chapters, adding results to a report, writing to the model, Word.

## 3. Tools (core in `llsm.report`, MCP tools plus CLI `llsm report …`)

- `report_preview(model_path)` → reports in the model (read from the file, no SCIA, ~1 min on
  50 MB), their chapter tree (from one XLSX dry run on a copy: sheet titles = chapter numbers
  and table names), which tables are "Lege tabel", whether the model holds results (and whether
  recalculation would be needed), estimated run time. Nothing written next to the model.
- `report_write(preview_id, formats=["PDF"])` → runs ESA_XML on a fresh copy, writes the file(s),
  then checks: file exists and is non-empty (exit 0 with 0 bytes happened, `-tTXT`), PDF page
  count > 0, XLSX: every results chapter from the preview present and not "Lege tabel". Reports
  `chapters written / expected / empty`; never "done" with an empty results chapter unflagged.
- Reuses: the ESA_XML runner, SCIA-open guard, licence diagnosis (exit 6) and file-lock guards from
  the bedding work; `esa_docs.py`'s block reader moves into `llsm.scia.esa_file`.

## 4. Verification — `docs/verify/report.md`

- Plate fixture (from the template, so it has `Report_1`): the S3 wheel loads in *Alle UGT*;
  report and `-x` both give column forces −232.7 / −217.3 / −217.3 / −232.7 kN (sum 900.0 kN =
  loads read back). Proves the report shows the right case and result class.
- Real v19.1 kademuur copy: `Report_2` printed; three values from its PDF matched with SCIA's GUI
  table for the same chapter (by the engineer or on the desktop when free).
- Fail-loud cases: wrong report name (exit 8 → message with the names found), no report in the
  model (message: make one in SCIA, or load SCIA's report template), uncalculated model, empty
  results chapter, 0-byte output, SCIA open, licence expired.
- Unit tests: report-name reader on a golden `.esa` block, XLSX chapter/empty-table checker,
  output naming.

## 5. Questions for Sebastiaan and Berber

> Hoi Sebastiaan en Berber,
> Ik heb uitgezocht hoe LLSM een rapport van de SCIA-resultaten kan maken. SCIA kan het
> engineering report dat al in een model zit automatisch afdrukken (PDF, Excel of RTF), zonder
> SCIA te openen. Een nieuw rapport opbouwen kan SCIA alleen in de editor zelf. Daarom vijf vragen:
> 1. Hebben jullie modellen al een engineering report met de hoofdstukken die je wilt? Zo nee:
>    willen jullie een vaste rapport-template in SCIA (één keer maken, per model inladen)?
> 2. Welke hoofdstukken en resultaten moeten erin: interne krachten, vervormingen, reacties,
>    spanningen? Per belastingsgeval, per combinatie, per resultaatklasse, of alleen omhullenden?
> 3. Per onderdeel geordend (paal, vloer, wand) en daarbinnen per resultaatklasse, zoals het
>    kademuur-template nu doet, of andersom?
> 4. Volledige tabellen of alleen extremen per onderdeel? Met of zonder figuren?
> 5. Is een PDF genoeg, of moet het in jullie Word-huisstijl en samen met andere hoofdstukken?
>    Dat bepaalt of we daarna ook een eigen Word-rapport maken.
> Een voorbeeldrapport dat jullie nu met de hand maken helpt het meest.
> Groet, Quincy

## 6. Risks and scope

- **Results not stored or lost on open** (v19.1 → v24) ⇒ recalculation on a copy, linear only.
- Large models: the template report took 160–320 s per format; a 146 MB model will be longer.
  The preview states the estimate; one format per run by default.
- The report shows what its author selected (members, extremes); LLSM prints it as is and says so.
- Licence seat and file lock: reused guards.
- Scope: one core module (reader + runner + checker), two MCP tools, one CLI command, one verify
  doc. Touches no existing tool.

## Prior attempts

- docs/11 §2 listed `-sd -tXLSX -d<doc>` as a route, untested; phase 0 found no XML IO document in
  a real copy (exit 9). This spike tested the engineering report itself: a different object,
  present in all three models looked at.
- The apps' SCIA reports (`report_scia.docx`, `make_scia_results_report`) used one fixed tag per
  cell and were dropped; route B's prototype uses per-table subdocs instead.
