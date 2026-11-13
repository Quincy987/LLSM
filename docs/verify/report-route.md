# Spike — a results report for a saved SCIA model

Date: 2026-11-13. SCIA Engineer 24.0.3015.64, on copies only, SCIA closed for every run (the
runner refuses when any SCIA process is open). Scripts: `phase0/report/`; models, logs and
outputs in the gitignored `phase0/xml_route/work/report/`. Plan: `docs/13-plan-report.md`.

Asked by Sebastiaan van Rossum (mail 2026-11-12): "De LLSM vragen een SCIA Engineering report te
maken waar alle resultaten van de verschillende belastingsgevallen en resultaatklassen op alle
onderdelen van de constructie geordend worden weergegeven." Berber Renckens: a report is a lot
of manual work.

## Outcome

- **Route A (SCIA's own engineering report) works headlessly for the template's report** (whose
  items are of the current post-processor type):
  `ESA_XML NONE copy.esa -sd -t<FMT> -o<out> -d<name>` printed `Report_1` of a calculated, saved
  plate model, without recalculation, with every results chapter filled; column forces
  −232.68 / −217.32 / −217.32 / −232.68 kN (hand check −232.7 / −217.3), reactions sum 900 kN.
- **On a real model saved in v19.1 the engineer's report prints, but without results**: all 10
  results chapters say "Geen ondersteunde taak. Schakel over naar de naverwerkingsomgeving 'v16 en
  ouder' (beschikbaar in de 32-bitversie)". Exit 0. The input chapters (36 sheets) are fine.
- **Route B (Word report from the XML IO export) works where the model has an XML IO document**:
  on the plate, 15 tables, 126 rows, 1 875 cells read back, 0 mismatches, same values as the
  report. On the real copy there is none: exit 9, "standaard document wordt gemist" (as phase 0).
- **No XML route found to add either document** (the definition declares no documents; its one
  results container is read-only). For models not built from the template, both routes need one
  step in the SCIA GUI first.
- A report keeps its author's **selections**: the template's chapter "Wand" selects *Alle*, so on
  the plate it shows the floor's results under the heading "Wand".

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
*Interne 2D-krachten; m_xD± / m_yD±* for floor and wall. That is the ordering Sebastiaan asks
for (per part, per result class), made once by hand in the template; but each item prints the
extremes its author chose (*Extreem: Globaal*), not every row.

How the formats differ:
- **XLSX**: one sheet per *table*, titled with the chapter number cut at Excel's 31 characters
  (`3.3.1.1. Resultaatklasses - All`), sheets in reverse order. Pictures are dropped. Empty tables
  are written as a single cell "Lege tabel", so an empty chapter is detectable.
- **PDF / RTF**: the full report with the pictures (one image per result item on the results pages).
- On the uncalculated template the results chapters hold only the result-class name and empty
  pictures; on a calculated model they fill (next section).

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

Run on `C:\Development\Viktor\output_xml (2).xml` (a real kademuur export, 4 combinations); the
read-back compares the .docx with the parsed export, so it checks rendering, not values:

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

## Calculated models (`route_a_calc.py all`, 01:41–02:06, SCIA closed)

| Run | Exit | Time | Output |
|---|---|---|---|
| plate: `make_plate.py` (from the template, inherits `Report_1`) | 0 | 97 s | `plate_base.esa` 35.97 MB |
| plate: S3's 12 wheel loads in combination *BGT1_kar*, put in result class *Alle UGT*, `LIN`, `-tESA` | 0 | 44 s | `plate_calc.esa` 36.76 MB (+0.8 MB: results stored) |
| plate, saved file, **NONE**: `-sd -tXLSX -dReport_1` | 0 | 186 s | 71 470 B (template: 29 423 B) |
| plate, NONE: `-sd -tPDF -dReport_1` | 0 | 413 s | 7.3 MB (template: 0.12 MB) |
| plate, NONE: `-x` | 0 | 14 s | 92 421 B, 15 result tables |
| real v19.1 copy, NONE: `-sd -tXLSX -dReport_2` | 0 | 223 s | 70 019 B, 36 sheets |
| real, NONE: `-sd -tPDF -dReport_2` | 0 | 458 s | 9.2 MB, 58 pages |
| real, NONE: `-x` | **9** | 20 s | none; "standaard document wordt gemist" |

**Plate: the report fills from stored results, no recalculation.** Each results table starts
with SCIA's own settings lines (*Lineaire berekening · Klasse: Alle UGT · Extreem: Globaal ·
Selectie: Alle*) and units in a header row:
- 3.2 Steunpunten / Alle UGT / Reacties: Sn1–Sn4 R_z = 232.68 / 217.32 / 217.32 / 232.68 kN;
  *Resultante van reacties* R_z = 900 kN at (3, 2, −1) m, = the 900.0 kN of loads read back in S3.
- 3.3 Palen / Alle UGT / Interne 1D-krachten: C1 −232.68, C2 −217.32, C3 −217.32, C4 −232.68 kN
  (S3: −232.7 / −217.3 / −217.3 / −232.7). Combination key printed: *BGT1_kar/1 = LLSM-ML TS1 01
  + 02 + 03*. The same values in *Alle BGT* and *Alle UGT+BGT*: those result classes of the
  template also list *BGT1_kar* in their "Lijst" (the update only wrote *Alle UGT*; why the other two picked it up was not checked).
- 3.4 Vloer / Interne 2D-krachten: D1, element 12 / node 25, m_x = 155.9 kNm/m, location
  "In knooppunten gem. bij macro". The `-x` export has the same row as 155 904.5 Nm/m.
- **3.5 Wand shows the same D1 rows**: its selection is *Alle*, the floor's is *D1*. The plate has
  no wall. A report built for one model and reused on another prints what its selections match,
  under its author's headings.
- Tables hold the extremes per the report item's setting (*Extreem: Globaal*): 4 rows for 4
  columns, not every section. That is the author's choice per item, not something LLSM sets.

**Plate, route B on the same model's `-x`:** 15 tables (5 result types × 3 result classes),
126 rows, 1 875 cells read back from the .docx, 0 mismatches. That read-back proves the rendering
(the .docx says what the parser read), not the values. The independent checks are C1 N =
−232,68 kN and D1 m_x = 155,90 kNm/m, the same as SCIA's own report and S3's hand check. The run found
two unit errors of the prototype's own: 2D moments and shear forces were labelled kNm and kN, not
kNm/m and kN/m (fixed, by table name). Five 2D design columns (`m_cD±`, `n_xD`, `n_yD`, `n_cD`)
and `V_r` have no unit in its table and are printed "[SI]", not guessed.

**Real v19.1 copy: input yes, results no.** The 36 sheets hold project data, cross-sections,
materials, mesh and solver settings, nodes (76 sheet rows), members (34), 2D members, node
supports, line supports on members (321 rows; phase 0 counted 319 objects), 8 load cases,
combinations (90 rows), load groups. Row counts include header rows. All 10 results items (17 Palen:
BGT/UGT paalreacties, schoorpalen, loodpalen; 19–23 UGT/BGT Vloer and Wand) print one line:
"Geen ondersteunde taak. Schakel over naar de naverwerkingsomgeving 'v16 en ouder' (beschikbaar in
de 32-bitversie)." The PDF has it on 6 pages. The log repeats "Old result" 398 times. Exit 0 and
a 9 MB PDF: **nothing in the exit code or file size shows that the results are missing**; only
the text does. The message points at the item type ("v16 en ouder"), not at the v19.1 save.
Not tested: whether recalculating changes it (likely not, for that reason), and whether
rebuilding the items in v24's report editor fixes it (the likely remedy, no source). The
Houtmankade model (146 MB, `Report_2`, `Report_3`) was not printed.

## Side effects

None on the models: every run on a fresh copy, originals not opened for writing. No SCIA session
was started next to the open one, and none was stopped.
