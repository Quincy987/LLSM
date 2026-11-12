# Spike — a results report for a saved SCIA model

Date: 2026-11-13. SCIA Engineer 24.0.3015.64, on copies only, SCIA closed for every run (the
runner refuses when any SCIA process is open). Scripts: `phase0/report/`; models, logs and
outputs in the gitignored `phase0/xml_route/work/report/`. Plan: `docs/13-plan-report.md`.

Asked by Sebastiaan van Rossum (mail 2026-11-12): "De LLSM vragen een SCIA Engineering report te
maken waar alle resultaten van de verschillende belastingsgevallen en resultaatklassen op alle
onderdelen van de constructie geordend worden weergegeven." Berber Renckens: a report is a lot
of manual work.

## Decision so far

- **Route A (SCIA's own engineering report) works headlessly on the template**: `ESA_XML NONE
  copy.esa -sd -t<FMT> -o<out> -d<name>` prints the report stored in the model as XLSX, PDF or RTF.
  It needs a report that already exists in the model; an XML update cannot add one.
- **Route B (Word report from the XML IO export) works as a prototype** on an existing SCIA
  export: 28 tables, 174 rows, every cell read back from the .docx, 0 mismatches.
- **Not yet shown: either route on a calculated model.** The runs on the plate fixture and on a
  real kademuur copy are prepared (`route_a_calc.py`) but did not run: someone's SCIA was open on
  this machine from 00:46 and this spike never runs next to it. Section "Pending" lists them.

## Route A — SCIA's engineering report through ESA_XML

### Which document must exist

ESA_XML's help: `-sd` = engineering report, `-t` HTML/RTF/TST/PDF/DDS/XLSX, `-d<name>` = document
name ("when DOCNAME isn't valid document name then export fails"). Without `-d` the "actual"
document is used. The names are stored in the `.esa` as small zlib blocks
(`DOCUMENTVIEWDATASTORAGE_DOCUMENTINFO` → `<Name>`); `esa_docs.py` lists them without SCIA:

| Model | Engineering reports found |
|---|---|
| Kademuur template (`model.esa`, 35.9 MB) | `Report_1` |
| Real `LBG_N1c … gem eindfase.esa` (v19.1, 50.7 MB) | `Report_2` |
| Real `Houtmankade (+1.0) eindfase-hoog.esa` (146 MB) | `Report_3`, `Report_2` |

So the models we have all carry an engineer-made report. This is a different object from the
XML IO document behind `-x`, which phase 0 found missing in the real copy (exit 9).

### Template runs (`route_a.py`, NONE, no calculation)

| Run | Exit | Time | Output |
|---|---|---|---|
| `-sd -tXLSX` (no `-d`) | 0 | 296 s | 29 425 B, 27 sheets |
| `-sd -tXLSX -dReport_1` | 0 | 157 s | 29 423 B, same 27 sheets |
| `-sd -tPDF -dReport_1` | 0 | 323 s | 122 126 B, 19 pages |
| `-sd -tRTF -dReport_1` | 0 | 284 s | 777 761 B |
| `-sd -tXLSX -dNO_SUCH_DOC` | **8** | 82 s | none; log "Unable to open existing document with name NO_SUCH_DOC !" |
| `-tTXT` (old document, no `-sd`) | **0** | 20 s | **0 bytes**: exit 0 with nothing written |

What is in `Report_1` (PDF table of contents, XLSX sheet names): 1 Inhoudsopgave · 2 Invoer
(materials, load cases, load groups, combinations, result classes, mesh and solver settings,
nodes, members, 2D members, supports) · **3 Resultaten**: 3.1 Verplaatsingen per load case (BG1,
3D displacement picture) · 3.2 Steunpunten · 3.3 Palen · 3.4 Vloer · 3.5 Wand, each split by
result class *Alle UGT / Alle BGT / Alle UGT+BGT*, with *Interne 1D-krachten; N* for the piles and
*Interne 2D-krachten; m_xD± / m_yD±* for floor and wall. That is the structure Sebastiaan asks
for (per part, per result class), made once by hand in the template.

How the formats differ:
- **XLSX**: one sheet per *table*, titled with the chapter number cut at Excel's 31 characters
  (`3.3.1.1. Resultaatklasses - All`), sheets in reverse order. Pictures are dropped. Empty tables
  are written as a single cell "Lege tabel", so an empty chapter is detectable.
- **PDF / RTF**: the full report with the pictures (one image per result item on the results pages).
- On the uncalculated template the results chapters hold only the result-class name and empty
  pictures. Whether they fill on a calculated model is the pending test.

### Can an XML update add a report?

No route found. The XML definition we write with (`viktor_full.xml.def`) declares model, load,
combination and result-class tables only; nothing for documents. Its one results container
(`BasicResults.EP_ResultsTable.1`, table `XML\default`, node reactions) is marked
`readonly="yes"`: it describes what `-x` exports, it cannot write an XML IO document either. So
route B's data source is also made once in the GUI (or comes with the template, as for models the
kademuren app builds). The report lives in
`EP_DsDocument.*` / `XEP_DocumentData.*` objects that no definition file here declares. A report
is made once in the SCIA GUI; SCIA's own report templates (save/load in the report editor) are the
way to reuse it across models. Not tested here: that is a GUI step.

## Route B — Word report from the XML IO export

`route_b.py RESULT.xml OUT.docx [--by case|member]`, pure Python, no SCIA:

1. **Parse.** The `-x` export is a flat list of tables: a selection table (`Combinaties - X`,
   `Resultaatklasses - X`, `Belastingsgevallen - X`) followed by the result tables for it
   (`Reacties`, `1D-vervormingen`, `2D-verplaatsing`, `Verplaatsing van knopen`, `1D-spanningen`,
   `2D-spanning/-rek`, …). A table can hold several sections (`Reacties` has *Knoopreacties* and
   *Lineaire intensiteit*, with different columns); each becomes its own table.
2. **Units by column name**, SI to kN, kNm, kN/m, kNm/m, mm, mrad, MPa; a column with no known
   unit is kept SI and listed by name in the preview (none left in this export).
3. **Order** `--by case`: chapter per load case / combination / result class, table per result type,
   rows by member (natural sort). `--by member`: chapter per member, then per selection.
4. **Render** with docxtpl: a template built in code (heading, a `{%p for %}` loop over sections, a
   `{{p table }}` subdoc per table, style *Table Grid*), the pattern of both apps.
5. **Read back**: reopen the .docx and compare every table cell with what was meant to be written.

Run on `C:\Development\Viktor\output_xml (2).xml` (a real kademuur export, 4 combinations):

| Order | Tables in export | Tables in .docx | Rows | Cells compared | Mismatches |
|---|---|---|---|---|---|
| by case | 28 | 28 | 174 | 2 238 | 0 |
| by member | 28 | 90 | 174 | — | 0 |
| empty export | 0 | 0 | 0 | 0 | — → **fails** (`ok: false`) |

Spot check: support `Sn:p2/K:p2_b`, *UGT-Set B*, raw `R_z = 54225.007988898084` (N) → `54,23` kN.
Line reactions come out in kN/m (the first run labelled them kN; caught and fixed).

Limits seen: this export holds the **extremes only** (6–11 rows per table): what is in the export
is decided by the XML IO document's settings in the model (members, selection, *Extreme*), not by
the reader. And it carries no pictures.

### What the two apps give (survey, read-only)

Both get SCIA results the same way: VIKTOR's `SciaAnalysis` (ESA_XML with the XML IO document in
the `.esa` template). Neither uses SCIA's engineering report.

- **Plaatbruggen** (`automatisch-toetsmodel-plaatbruggen`): `src/report/report_functions.py` fills a
  docxtpl template with params and the governing IDEA unity checks; **no SCIA results in the Word
  output**. Reusable: the docxtpl render pattern (PDF step aside, which needs VIKTOR); the
  VIKTOR-free XML table parser `_parse_table_direct` (`app/bridge/scia_model_builder.py:761-854`);
  `SciaUnitConverter` (`results/scia_unit_conversion.py`, N→kN, Nm→kNm, 2D per metre).
- **Kademuren** (`amsterdam-kademuren`): `design_report_template.docx` uses `{%tr for %}` row loops,
  `{%tc for %}` column loops and pictures, style *TableGrid*, generated by `prepare_template.py`;
  SCIA content is four governing pile values and two tables. `parse_pile_forces` reads
  *Interne 1D-krachten* under `Resultaatklasses - {rc}` for piles only, `abs()/1000`, maximum per
  pile, components not concurrent. Number format: 2 decimals, decimal comma.
- **Not reusable**: `report_scia.docx` and both `make_scia_results_report` versions (the current one
  is never called; the old folder is commented out): one fixed tag per cell (138 and 440 tags), so
  any change in member or column count breaks them. Most likely why they were dropped.

What `route_b.py` took: the docxtpl pattern, *Table Grid*, decimal comma with 2 decimals, the
SI factors; it uses per-table subdocs instead of fixed tags, which is what the old reports lacked.

## Pending (not run: SCIA open on this machine)

`route_a_calc.py`, all on copies:
1. `plate`: build `plate_base.esa` (`make_plate.py`, from the template so it inherits `Report_1`),
   add the moving-load spike's 12 wheel loads in combination *BGT1_kar*, put that combination in
   the template's existing result class *Alle UGT* (name is the key), `LIN`, save.
2. `reports`: on the saved calculated file, **no recalculation** (NONE): `Report_1` as XLSX and PDF,
   and `-x`. PASS when the results chapters carry numbers, the column forces in *Palen / Alle UGT*
   are −232.7 / −217.3 / −217.3 / −232.7 kN (S3's hand-checked values) in both the report and the
   `-x` export, and `route_b.py` on that export reads back with 0 mismatches.
3. `real`: the v19.1 kademuur copy, NONE: `-sd -dReport_2` XLSX and PDF, and `-x`. Shows whether
   stored results survive the open (phase 0: a v24 save loses mesh objects) and whether the
   engineer's report prints without recalculation.

## Side effects

None on the models: every run on a fresh copy, originals not opened for writing. No SCIA session
was started next to the open one, and none was stopped.
