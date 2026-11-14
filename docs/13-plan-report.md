# 13 — Plan: a results report for a saved SCIA model

Date: 2026-11-13. Status: v1 built (2026-11-14, `docs/verify/report.md`): §3 without the standard
template of §2 and its gate (§4.1), which still need SCIA's GUI. Built differently from §3: no
preview id (`report_write` takes the same arguments as `report_preview`), and in the chat the print
is a job (`report_write` returns at once, `report_status` waits up to 4 min per call), because
Claude Code ends an MCP call after 300 s. Added: `cases_group`, a chapter per moving-load case.
Proposal after a spike (`docs/verify/report-route.md`).
Asked by Sebastiaan van Rossum (mail 2026-11-12, "een SCIA Engineering report … alle resultaten van
de verschillende belastingsgevallen en resultaatklassen op alle onderdelen van de constructie
geordend") and Berber Renckens (a report is a lot of manual work).

## 1. Recommendation: SCIA's own report as the print engine, around one standard LLSM template

v1 = **route A** (ESA_XML prints the engineering report stored in the model), with **one standard
report template** that LLSM provides and the engineer loads once per model. Why, from the spike:

- On a calculated, saved model it works without recalculation: PDF/XLSX/RTF, SCIA's own tables,
  units, signs, combination keys and pictures. Values checked: column forces −232.68 / −217.32 kN
  (hand −232.7 / −217.3), reactions 900 kN. No column or unit mapping by LLSM: the Word prototype
  of route B got two units wrong before the run caught them.
- Engineers' existing reports are not enough: the real v19.1 model's `Report_2` printed its input
  but all 10 results items said "Geen ondersteunde taak" (old post-processor item type), exit 0,
  and still did after recalculating a copy: the items have to be rebuilt in v24.
- Results are split per result class as asked: UGT 1.5× and BGT 1.0× of the same loads printed
  C1 N = −349.03 and −232.68 kN under their own classes, and an item set to *all result classes*
  added a chapter by itself when the model got a fourth class.
- No XML route was found to add either a report or an XML IO document, so every route needs one
  GUI step on a hand-made model. A standard template makes that one step the same everywhere, and
  gives the same chapters on every model, which is the "geordend" Sebastiaan asks for.
- The template's XLSX output (one sheet per table, with SCIA's settings lines) is what LLSM checks,
  and later the input for a Word report (route B) without a second document in the model.

Route B (LLSM writes Word from the XML IO export) is kept for later, when they want house style or
the report combined with other chapters. Its prototype stays in `phase0/report/route_b.py`.

## 2. The standard template (made once, in SCIA 24.0.3015.64)

- Generic chapters, so selections cannot put one part's results under another's heading (the
  template's "Wand" item selects *Alle* and showed the floor's results on a model without a wall):
  1 Invoer (materials, cross-sections, load cases, groups, combinations, result classes, mesh and
  solver settings, members, supports) · 2 Reacties · 3 1D-elementen (N, V, M per member) ·
  4 2D-elementen (m, n, v basis grootheden; design moments) · 5 Vervormingen (1D, 2D) — each split
  per result class, then per load case if asked (§6).
- Selections *Alle* per member type, so it fits any model; result classes as *all result
  classes* (as the kademuur template's items do), so every class of the model gets its own
  chapter without naming them. Seen working: a fourth class got its own sub-chapters.
- Stored as a SCIA report template file and as a template model, with its SCIA version.
- One-page procedure for the engineer: open model, load *LLSM-standaard*, check result classes,
  save. The same text is LLSM's message when a model has no usable report.

## 3. v1 scope and tools (core `llsm.report`; MCP tools plus CLI `llsm report …`)

- **In**: a saved `.esa` (SCIA closed), report name (default *LLSM-standaard*, else the only one;
  if several, the engineer picks), formats (PDF default; the XLSX is always made for the checks).
- **Out**: `<model>_rapport-01.pdf` (+ `.xlsx`) next to the model, never overwriting; the source
  model never written (ESA_XML on a copy).
- **Content**: what the template's items select. Tables hold the **extremes per item**
  (*Extreem: Globaal* or per member), not every mesh node: "all results" means all cases, result
  classes and parts, each with its extremes, unless the engineers ask otherwise (§6).
- `report_preview(model_path)`: from the `.esa` only, no SCIA (~1 min on 50 MB): reports present,
  their item tree; whether results are stored. Old-type items cannot be told from the file (the
  only candidate marker, the item block's container GUID, is per report, not per type), so the
  preview says "checked on first write" and the first write refuses with the procedure of §2.
  A first write gives the real chapter list; a preview from SCIA would take 3–5 min.
- `report_write(preview_id, formats)`: ESA_XML on a fresh copy, timeout scaled to file size
  (template 3–7 min per format; 50 MB model 4–8 min). Then **checks the XLSX** and reports
  `results chapters written / expected / empty / unsupported`, plus each results table's own
  settings lines (*Klasse, Selectie, Extreem*) so a wrong heading is visible. It fails on: exit ≠ 0;
  0-byte output (seen with `-tTXT`, exit 0); "Geen ondersteunde taak" / its English text; "Lege
  tabel" in a results chapter; a results sheet with no data rows. Never "done" with any of these.
- "Lege tabel" counts only in results chapters: the plate's report also prints it for an empty
  input table (internal edges of 2D members).
- Reuses the ESA_XML runner, SCIA-open guard, licence (exit 6) and file-lock guards of the bedding
  work; `esa_docs.py`'s reader moves into `llsm.scia.esa_file`.

## 4. Gate before building (on copies, SCIA closed)

1. Build *LLSM-standaard* in SCIA 24 on the plate fixture; load it into a **second** model (the
   v19.1 kademuur copy) and confirm it picks up that model's members and result classes. Untested
   so far; it is the whole premise of §2.
2. On that copy: print headlessly and compare three values per results chapter with SCIA's own
   table in the GUI. Its stored results are old ("Old result"); recalculating a copy (`LIN`,
   107 s) worked but did not change the old items, so whether the new items need a recalculation
   is still open. Only for a linear model may LLSM recalculate a copy.
3. ~~Two combinations in two result classes~~: **done**, second run of the spike: each class
   prints its own values (UGT = 1.5 × BGT).
4. The 146 MB Houtmankade model: run time and output size.

FAIL on 1 ⇒ the template has to be rebuilt per model; then reconsider route B with a standard XML
IO document as the one GUI step instead.

## 5. Verification — `docs/verify/report.md`

The §4 gate results; the plate's −232.68 / −217.32 kN and 900 kN through the tool; every fail
case of §3 triggered once (wrong name ⇒ exit 8 with the names found; old-type items; no report;
uncalculated model; SCIA open; licence expired). Unit tests: `.esa` report reader on a golden
block, XLSX checker on the three outputs of this spike (template, plate, real), output naming.

## 6. Questions for Sebastiaan and Berber

> Hoi Sebastiaan en Berber,
> Ik heb uitgezocht hoe LLSM een rapport van de SCIA-resultaten kan maken. SCIA kan het
> engineering report in een model automatisch afdrukken (PDF, Excel of RTF) zonder SCIA te
> openen, en dat werkt. Twee dingen vielen op:
> - In het kademuurmodel dat ik had (opgeslagen in v19) drukt het rapport geen resultaten af: de
>   resultaatonderdelen zijn van een oud type ("Geen ondersteunde taak … v16 en ouder").
> - Een rapport kan alleen in SCIA zelf worden opgebouwd, niet van buitenaf.
> Mijn voorstel: één standaard-rapporttemplate dat je per model één keer inlaadt; daarna maakt
> LLSM het rapport en controleert het of alle hoofdstukken resultaten bevatten. Vragen:
> 1. Is één vast template werkbaar, en wie beheert het?
> 2. Welke hoofdstukken en resultaten: reacties, interne krachten 1D en 2D, vervormingen,
>    spanningen? Per belastingsgeval, per combinatie, per resultaatklasse?
> 3. Per onderdeel of per resultaatklasse eerst? En alleen extremen (per staaf of globaal), of
>    volledige tabellen (dat kunnen voor een vloer duizenden regels zijn)?
> 4. Met figuren (momentenlijnen, contouren)? PDF genoeg, of Word in huisstijl?
> 5. Mag ik een rapport dat jullie nu met de hand maken als voorbeeld?
> Groet, Quincy

## 7. Risks and scope

- Template reuse across models fails (gate 1) ⇒ per-model setup; reconsider route B.
- Big models: minutes per format; one write at a time, the preview avoids SCIA.
- SCIA version: the template is pinned to 24.0.3015.64; a SCIA update needs one re-check. A non-
  Dutch UI changes the texts the checker looks for: both languages, and the check keys on the
  settings lines' structure as well.
- Results not stored, or old: recalculation on a copy, linear only, otherwise refuse and say why.
- Scope: one core module (reader, runner, XLSX checker), two MCP tools, one CLI command, one
  template plus procedure, one verify doc. Touches no existing tool.

## Prior attempts

- This plan's first version (same branch, 2026-11-13 02:10) left the result-class split and the
  recalculation of old reports open; the second spike run answered both (verify doc, "Second run").

- docs/11 §2 listed `-sd -tXLSX -d<doc>` as untested; phase 0 found no XML IO document in a real
  copy (exit 9), confirmed here. This spike printed the engineering report itself: works on the
  template's report, gives no results on the old real model's report.
- The apps' SCIA reports (`report_scia.docx`, `make_scia_results_report`) used one fixed tag per
  cell and were dropped; route B's prototype uses per-table subdocs instead.
