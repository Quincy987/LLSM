# 11 — Plan: plate results from a saved SCIA model, in IDEA's column order

Date: 2026-11-12. Status: proposal, nothing built. Asked by Berber (mail 2026-11-12): "Momenten en
krachten in platen van SCIA naar IDEA overzetten … vx en vy staan in IDEA en SCIA niet op dezelfde
plek. Zowel de omhullende resultaten als de niet-omhullende." Partly answers TODO "Ask constructeurs
how forces go to IDEA / peaks" (plates only; beams and the peaks question stay open).

**Compare before building (added 2026-11-13).** The plaatbruggen toetsmodel already maps SCIA
results to IDEA: `src/integrations/scia_integration/results/scia_integration_strips_to_idea.py`
(pandas only, no viktor; tested in `tests/test_scia_to_idea_mapping.py`). It works on integration
strips, not on plate forces per mesh node: x strips → "dwars" cross-section, y strips → "langs";
N → N, Vz → Qz, Mx or My → My; one extreme per zone and "gefilterd voor" value, ULS and SLS freq
together. Before §2's spike: check whether Berber's IDEA input is per cross-section (then this
mapping, its sign choices and its test settle part of the column question) or per node (then it is
a reference, not a replacement). Same reuse terms as docs/10 §3.1 and §6 (pinned copy, EUPL-1.2,
ownership with Geert's team).

## 1. v1 scope
- **In**: a saved, calculated `.esa` (SCIA closed) + 2D member name(s) + load cases and/or
  combinations, each either *per case* or *envelope* (as SCIA defines the combination).
- **Out**: `.xlsx` (one sheet per member × case/envelope, plus a summary sheet), or the same rows as
  tab-separated text for the panel's "Kopiëren". Columns in IDEA's order, units in the header.
- **Also in v1**: the same reorder for a table Berber exports or copies from SCIA herself. Her pain
  is the column shuffle; this path removes it even if the file route fails, and is the fallback.
- **Out of scope**: beams, design forces with reinforcement, the engineering report (follow-up
  question in §6), writing anything back into the model.

**Column order is an OPEN QUESTION.** Nothing is hard-coded until we have Berber's own Excel as she
pastes it. The mapping lives in one data file (`idea_columns.toml`: source name → IDEA position,
factor, sign), so her answer is a data change, not a code change.
- SCIA order (2D internal forces, basic magnitudes): `mx, my, mxy, vx, vy, nx, ny, nxy`.
- Best hypothesis for IDEA (RCS / Detail 2D-member input): `nx, ny, nxy, mx, my, mxy, vx, vy` —
  membrane, bending, shear. That fits "vx en vy staan niet op dezelfde plek" (positions 4–5 → 7–8).
  Second candidate: `mx, my, mxy, nx, ny, nxy, vx, vy`. Her file decides.
- Units: SCIA's XML export is SI (N/m, Nm/m, seen in phase 0); IDEA expects kN/m and kNm/m (to
  confirm). Factor 1e-3 applied in the mapper, printed in the header.
- Signs, unverified: SCIA mx = moment giving σx, positive = tension on the −z face (to check);
  mxy and the shear signs are where codes most often differ. Each sign is set by the §5 test model,
  then checked against one row Berber already pasted into IDEA. No sign flip without both.

## 2. Route decision — spike first (step 1, gate)
Evidence so far: `ESA_XML` exports results three ways: an XML IO document (`-x out.xml -m DOC`), an
engineering report (`-sd -tXLSX -o out.xlsx -d DOC`), and an old-style document (`-tTXT`). The
kademuur def `C:\Development\Viktor\LBG_N1c_…gem eindfase.xml.def` already declares a
`BasicResults.10.00.EP_InternalForces2D_Shell.1` table named `XML\default`. `output_xml (2).xml`
holds 2D displacement/stress tables with rows per `Element: n; Knoop: m`, with x, y, z and a load
column such as `UGT-Set B (automatisch)/1`, but only ~10 rows per table: extremes, not the full mesh.
Phase 0 showed a real copy has no default document (exit 9, "standaard document wordt gemist").

**Spike** (`phase0/results_route/`, copies only, SCIA closed):
1. On a real calculated plate model from Berber: `ESA_XML NONE copy.esa -xout.xml -m<doc>` with an XML
   IO document added once by hand in SCIA (Tabel › XML-document, table "2D-interne krachten", member,
   case, Extreme = *Geen*, location as she uses it), saved in the model copy. Reuse = keep that copy
   as a template and test whether an XML update with our `.def` can add the same document to other models.
2. Same through the engineering report: if her model already has a report chapter with the 2D
   forces table, `-sd -tXLSX -d<chapter>` may be the zero-setup route.
3. `NONE` must read the stored results. If results are not stored in the file, `LIN` recalculates on
   the copy — allowed only when the model's own analysis is linear; otherwise refuse and say why.
4. OpenAPI on an opened existing project only if 1–2 fail: blocked twice before (HubServer start
   timeout, phase 0 d; `ResultKey` mapping miss, docs/08 A). One attempt, time-boxed.

**PASS** when, for one member and one case: row count = mesh nodes × elements as SCIA's own table
shows; three rows match SCIA's GUI table value-for-value; a combination gives both the per-case and
envelope variants; the run fails loudly (not exit 0 with zero rows) on a wrong member, a wrong case
and an uncalculated model. **FAIL** on any silent empty table. FAIL on both routes → ship only the
reorder-her-own-export path.

**Location** (mesh node per element, node averaged, element centre, section): ask Berber which she
pastes today. Default = what she uses; the setting is read from the export and printed, never changed.

## 3. Peaks — stay honest
- Export raw values, exactly as SCIA's table has them. No smoothing, clipping, averaging or
  rounding beyond display. Location setting and mesh size are printed with the data.
- Envelope rows stay whole: each row is one concurrent force set from the governing combination,
  labelled with the component it maximises. Never merge maxima from different combinations into
  one row (unsafe for IDEA's interaction checks).
- Summary sheet per member × case: per component max/min with element, node, x/y/z; the 95th-
  percentile value; ratio max/P95. Ratio > 1.5 (threshold to agree) ⇒ "lokale piek? beoordeel",
  plus whether it sits on a support or edge node. The tool flags; the engineer decides.
- In the data sheets, the max/min cells are highlighted, not altered. Same flag in the chat preview.

## 4. MCP tools (`llsm.results` core, pure Python, plus CLI `llsm results …` for no-LLM users)
- `plate_results_preview(model_path, members[], cases[], mode="case"|"envelope", location=None)` →
  source route used, SCIA doc/location, counts (rows per member × case), first 10 rows in IDEA order
  with units, the max/min flags, column mapping and its source. Unknown member/case ⇒ error with
  candidate names taken from the export. Nothing written.
- `plate_results_export(preview_id, out_path=None, clipboard=False)` → writes `.xlsx` next to the
  model (`model_llsm-results-01.xlsx`, never overwrite) or TSV for Kopiëren; then reads the file
  back and compares row count, column headers and every value with the preview data. Reports
  `written / expected / mismatches`; never "ok" with mismatches.
- `plate_results_from_table(path_or_text)` → the same mapping on a table Berber exported/copied
  (detects decimal comma, SCIA's Dutch headers, units row).
- Units: lengths m (coordinates as SCIA gives them), forces kN/m, moments kNm/m, unless her file says otherwise.

## 5. Verification — `docs/verify/plate-results.md`
Test model generated reproducibly via XML (the kademuur def already carries 2D members), not by hand:
rectangular plate a = 4 m (x) × b = 8 m (y), t = 0.2 m, C30/37, ν = 0.3 (set explicitly), simply
supported on all edges (hinged lines), q = 10 kN/m², mesh ≤ 0.2 m. Timoshenko & Woinowsky-Krieger,
table 8, b/a = 2, ν = 0.3, at the centre:
- mx = 0.1017 q a² = 16.27 kNm/m; my = 0.0464 q a² = 7.42 kNm/m — tolerance 3 % (mesh, Mindlin).
  The 2.2 ratio is what proves the mx/my columns are not swapped.
- vx at mid long edge = 0.503 q a = 20.1 kN/m (Kirchhoff value; SCIA's Mindlin shear is lower near
  corners), tolerance 10 % — proves vx/vy identity and sign.
- Second case: in-plane edge tension 50 kN/m on the x-edges only ⇒ nx = 50 kN/m, ny ≈ 0, nxy ≈ 0 at
  the centre (2 %), proves nx/ny identity.
- Square variant a = b = 4 m: mx = my = 0.0479 q a² = 7.66 kNm/m (symmetry check).
- Combination of both cases ⇒ envelope rows checked against the per-case rows.
- One real row: a value Berber pasted in IDEA, reproduced by the tool, cell for cell.
Plus unit tests for the mapper (order, factor, sign, decimal comma), a golden XML, and the
fail-loud cases from §2.

## 6. Mail draft for Berber
> Hoi Berber,
> Dank voor je mail; het overzetten van plaatresultaten naar IDEA ga ik oppakken. Om de kolommen
> niet te raden heb ik vier dingen nodig:
> 1. Een Excel zoals jij hem in IDEA plakt, met een paar regels erin (omhullend én niet-omhullend), en
>    het SCIA-tabelletje waar je die regels uit hebt.
> 2. Een kopie van een SCIA-model waarvoor je dit gedaan hebt (berekend opgeslagen).
> 3. In welk deel van IDEA plak je het (RCS, Detail, …) en in welk invoerscherm? Een screenshot helpt.
>    Welke eenheden en tekenafspraak verwacht IDEA daar?
> 4. Welke resultaten neem je: per netknoop, gemiddeld per knoop, of in een snede? En wat doe je
>    met pieken bij steunpunten?
> Los daarvan: je schreef dat een rapportage maken veel handwerk is. Wat staat er in zo'n rapport,
> en welke stukken neem je nu met de hand over? Dat is een volgende stap, niet deze.
> Groet, Quincy

## 7. Risks and effort (scope)
- **Wrong column or sign = plausible but wrong IDEA check.** Biggest risk; covered by her file, the
  §5 model and one real row. No release without all three.
- Results not stored in the `.esa` ⇒ recalculation on a copy; only for linear models (§2.3).
- The XML IO document must be added once per model by hand if XML update cannot create it; that is
  a one-time step, documented with screenshots.
- Big meshes: 10⁴–10⁵ rows; xlsx fine, clipboard maybe not ⇒ clipboard limited to the filtered rows.
- Licence seat and file lock (as in phase 2): reused guards, no new mechanism.
- Scope: one new core package (reader per route + mapper + summary), three MCP tools, one CLI
  command, one test model generator, one verify doc. Reuses the ESA_XML runner and guards from the
  bedding work; touches no existing tool. Spike is the gate; the reorder-her-export path can ship alone.

## Prior attempts
- Beam results via OpenAPI (`get_beam_inner_forces`): v0.3.4 fixed a TypeError, the `ResultKey`
  mapping miss on v24 is still open (docs/08 Phase A). Only works for models LLSM built live.
- `numeric_check.py` (phase 0) already read a result table (`1D-vervormingen`) from an ESA_XML
  export; that is the precedent for this route. No 2D results read anywhere yet.
