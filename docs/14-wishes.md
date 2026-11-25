# 14 — What the constructeurs asked for, and where it stands

Date: 2026-11-25. One register of every wish from the replies to "LLSM: is er behoefte aan een
chat-assistent voor SCIA Engineer?" (2026-10-30) and "Drie vragen uit je SCIA-werk voor een
chat-assistent" (2026-11-06 to 2026-11-24). Each row points at the plan that covers it; the plans
keep the detail. Quotes are from the mails, in Dutch. A status is what the code on `main` does
today, not what a plan proposes.

Status: **works** (built and verified) · **partly** (part of the wish is built) · **not yet** (no
code) · **rule** (a constraint every tool follows, not a feature) · **open question** (we do not
know yet what is wanted).

## 1. The register

| Who, date | Wish | Status | Backed by / plan | What blocks the rest |
|---|---|---|---|---|
| Sebastiaan, 10-30 | "beddingen uit een geotechnisch rapport/som/excel-export toepassen op de palen in een model" | works | `src/llsm/bedding/`, tools `bedding_preview` / `bedding_apply`, CLI `llsm bedding`; `docs/verify/phase1-bedding.md`; [09](09-plan-v0.4-existing-models.md) Phase 1 | Input is a layer table: in the chat the AI fills it from what the engineer pastes or attaches; the CLI takes .csv or .json. No reader for a geo calculation file or GEF ([09](09-plan-v0.4-existing-models.md) Phase 4) |
| Sophie, 11-06 | "Ik haat echt beddingen invoeren" … lagen "meer om de meter. Met een duidelijke omschrijving" … "met 1 druk op de knop" | partly | Same bedding tool: a layer table (NAP, k, description) goes on every pile in one write | Merging the kademuur tool's 20 cm steps from her "gif file" (probably GEF, to confirm: TODO People) into ~1 m layers is not built and needs a geotechnician's rule ([09](09-plan-v0.4-existing-models.md) Phase 4) |
| Sebastiaan, 10-30 | "belastinggevallen en -combinaties genereren conform de normen" … "Herkennen waar bepaalde belastinggevallen wel en niet van toepassing zijn, (vrijwel) foutloos de juiste belasting- en combinatiefactoren uit de norm overnemen" | not yet | [12](12-plan-combinations.md) (proposal) | The reader does not read combination contents (`src/llsm/scia/esa_file.py`); factor table needs a senior sign-off ([12](12-plan-combinations.md) §2) |
| Sophie, 11-06 (against) | "Belastingsgevallen aanmaken of combinaties doe ik toch liever zelf … Ga niet alle combis maken." | rule | [12](12-plan-combinations.md) §0: opt-in, propose first, never "all" | — |
| Sophie, 11-06 (against) | "Geometrie zie ik ook niet geautomatiseerd worden" | rule | The saved-model tools never create geometry; the chat builder for new models from a sentence exists, but is not pushed as the product ([09](09-plan-v0.4-existing-models.md) §1, "Deferred") | Geert's moves and pile copies (below) edit geometry the engineer made, on his instruction; they do not generate it |
| Berber, 11-12 | "Jouw voorbeeld van de belastingcombinaties + beddingen zouden erg handig zijn." | partly | Beddings: as Sebastiaan's bedding row; combinations: as his combinations row | Combinations: as that row |
| Sebastiaan, 10-30 | "resultaten in een excelbestand zetten die makkelijk in IDEA gekopieerd kan worden" | not yet | [11](11-plan-results-to-idea.md) (proposal) | See Berber and Geert below |
| Sebastiaan, 11-06 | Forces often go into IDEA by hand: "je moet altijd rekening houden met pieken in krachtswerking" | open question | [11](11-plan-results-to-idea.md) §3 (peaks flagged, never smoothed) | How engineers treat peaks: nobody has answered yet. Geert (11-24, §2 item 5) says what goes into IDEA, not how peaks are handled |
| Berber, 11-12 | "Momenten en krachten in platen van SCIA naar IDEA overzetten" … "vx en vy staan in IDEA en SCIA niet op dezelfde plek" … "Zowel de omhullende resultaten als de niet-omhullende" | not yet | [11](11-plan-results-to-idea.md) (proposal) | Her own Excel and IDEA screen decide the columns; route spike ([11](11-plan-results-to-idea.md) §2) |
| Sophie, 11-06; Tom, 11-12 | Traffic as "een soort treintje"; Tom: "zelf het voertuig bepalen, de stapgrootte" | works | `src/llsm/moving_load/`, tools `moving_load_preview` / `moving_load_apply`; `docs/verify/phase3-moving-load.md`, `docs/verify/phase4-traffic-rules.md`; [10](10-plan-moving-load.md) | — |
| Tom, 11-12 | "ter vervanging van de 2D verkeerslastenmodule" | partly | [10](10-plan-moving-load.md) §0: positions are stepped, as in the toetsmodel | SCIA's module searches governing positions on influence surfaces; LLSM does not |
| Tom, 11-12 | Then "uitlezen van de krachten en exporteren naar Excel, in excel er een grafiek van maken" | partly | `src/llsm/report/`: with `calculate` and `cases_group`, a copy is calculated and SCIA's report prints a chapter per moving-load case, also as XLSX (`docs/verify/report.md`) | The model must already carry a report; no table or graph of one force over the positions |
| Geert, 11-12 | Reuse the toetsmodel: "De broncode in python zouden we eventueel ook om kunnen bouwen" | works | Traffic rules reused, 0 mismatches over 455 cases (`docs/verify/phase4-traffic-rules.md`) | — |
| Sebastiaan, 11-12 | "Het voordeel van de componenten is weer dat je precies weet wat ze doen" | rule | Every tool is a tested component; the AI fills in the input ([10](10-plan-moving-load.md) §0) | — |
| Sebastiaan, 11-12; Berber, 11-12 | Sebastiaan: "een SCIA Engineering report … alle resultaten van de verschillende belastingsgevallen en resultaatklassen op alle onderdelen van de constructie geordend"; Berber: "het maken van een engineeringsreport" is "handmatig altijd veel werk" | partly | Tools `report_preview` / `report_write` / `report_status`, CLI `llsm report`; `docs/verify/report.md`; [13](13-plan-report.md) | The standard LLSM template and its gate ([13](13-plan-report.md) §2, §4.1) need SCIA's GUI |
| Sebastiaan, 10-30 | "geen licentie op een LLM" | partly | CLI `llsm bedding`, `llsm moving-load` and `llsm report` work without a chat; a local model from the menu builds new models (`docs/verify/local-models.md`) | The local chat has 18 tools: no beddings, traffic or report. Not run on Ollama or with a real SCIA push and calculation (`docs/verify/local-models.md`, "Not verified") |
| Geert, 11-24 | Move the whole model "inclusief alle belastingen en opleggingen met {x} meter" | not yet | — | See §2 |
| Geert, 11-24 | Two piles at {x}, {y} "met dezelfde lengte, doorsnede, beddingen en opleggingen", "scharnierend … zodat ze enkel een axiale kracht opnemen" | partly | Beddings on a pile the engineer drew: bedding tool | See §2 |
| Geert, 11-24 | Read the new belastingnota for kademuren, then "alle belastinggevallen aan je kademuur model toevoegt"; or a reference design's report | not yet | — | See §2 |
| Geert, 11-24 | "in alle BGT-frequente belastingcombinaties de combinatiefactor van belastinggevallen {x} t/m {x} aanpassen naar {x}" | not yet | [12](12-plan-combinations.md) §8 (edit mode) | See §2 |
| Geert, 11-24 | "op mogelijk maatgevende locaties voor een doorsnedetoets in UGT, de combinaties van optredende momenten, normaalkrachten en dwarskrachten klaarzetten om in IDEA" | partly | SCIA's own report of a saved model, calculated on a copy if needed (`docs/verify/report.md`); [11](11-plan-results-to-idea.md) §8 | See §2 |

**Calculation from the chat** (behind every results wish of a chat-built model): on `main`,
`run_analysis` reports `ok` only when SCIA returns forces, and on the gemeente licence it does not:
the OpenAPI's calculation is SCIA-internal only (`docs/verify/calc-run.md`). A route through a
start model and one ESA_XML batch run is on branch `claude-bot/t-626`, not merged as of this date.
For a saved model that already carries a report, the report route prints the forces, and can
calculate a copy first (`calculate`, linear only).

## 2. Geert's five (mail 2026-11-24), checked against the code

What the saved-model reader (`src/llsm/scia/esa_file.py`) knows: beams, line supports on beams
(counted per pile, their position along the pile not decoded), nodes, plates, load groups, load
cases, free point forces, point forces on beams, free surface loads (in the measured layout). It
does **not** read point supports in nodes, hinges, line loads on beams, loads on plates, or what is
inside a combination. Point supports exist only in the builder for models LLSM builds itself
(`src/llsm/scia/builder.py`). What LLSM writes into a saved model: line supports on beams (the
bedding tool, `src/llsm/scia/esa_xml.py`); variable load groups with their cases, point forces and
free surface loads, and linear combinations of one case each for the report route
(`src/llsm/scia/load_xml.py`). Nothing else: no nodes, members, hinges, permanent groups, line
loads or loads on plates.

1. **Move the model: not yet.** Two blockers, in order: (a) LLSM cannot read point supports,
   hinges, line loads and plate loads, so it cannot check afterwards that everything moved; (b)
   changing node coordinates of an existing model through the XML update has never been tried.
2. **Copy piles: partly.** Beddings on a pile Geert draws himself work (he gives the layer table
   again). Creating the pile, copying the beddings and supports of an existing pile, and a hinged
   connection: not yet, blocked on the same reader gap (point supports, hinges, the position of line
   supports along a pile).
3. **Belastingnota: not yet.** The chat can read a document. The load writer only takes variable
   groups with point forces and free surface loads, and only the moving-load tool uses it; a
   kademuur's loads also need permanent groups, line loads and loads on plates or walls. Any route
   must show the cases from the document as a table the engineer confirms before writing.
4. **Combination factors: not yet.** Only *new* linear combinations have been written: one case at
   coefficient 1 each in shipped code (`build_case_classes_update` in `load_xml.py`), three cases at
   1.0 in spike S8 (`docs/verify/phase3-moving-load.md`). A factor other than 1, and any change to an
   existing combination, have never been written. Reading the contents of the engineer's
   combinations is not built (TODO Phase 3 "Decode combinations in the reader").
5. **Forces for IDEA: partly.** SCIA's own report prints the forces of a saved model that carries a
   report, on a copy calculated first if needed.
   Choosing the sections and putting the concurrent M, N and V together is not built
   ([11](11-plan-results-to-idea.md) §8).

## 3. What recurs
- **Combinations**: Sebastiaan (10-30), Berber (11-12), Geert (11-24); Sophie against generating
  them all (11-06). With three asking, reading combination contents is the next piece to build
  ([09](09-plan-v0.4-existing-models.md), update 2026-11-25).
- **Piles and beddings**: Sebastiaan (10-30), Sophie (11-06), Berber (11-12), Geert (11-24, copying
  piles).
- **Forces to IDEA**: Sebastiaan (10-30, 11-06), Berber (plates, 11-12), Geert (1D members, 11-24).
- **Consistent edits to an existing model** (new with Geert): move everything, copy piles, hinge.
  The value is that nothing is forgotten, which only holds once LLSM can read back every object
  that has to follow. Sophie's "geometrie … niet geautomatiseerd" is about generating geometry, not
  about these edits; worth checking with her once a move works.
