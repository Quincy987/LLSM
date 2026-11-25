# LLSM — Local LLM for SCIA Model

Drive **SCIA Engineer** via natural language. An engineer types *"build a 6m x 4m steel portal frame with fixed bases and 5 kN/m gravity on the rafter"*, and LLSM builds the model and puts it in SCIA with a mesh. Calculating it from the chat does not work on the gemeente's SCIA licence yet ([`docs/verify/calc-run.md`](docs/verify/calc-run.md)).

Inspired by VibeModel (the Revit equivalent, internal).

**Target:** SCIA Engineer 24.0 on Windows.

## Status

Every release and what it changed: [`CHANGELOG.md`](CHANGELOG.md).

- ✅ **Build a model from a chat** (chat → ADM model → SCIA with a mesh): chat panel, CLI and an MCP server (32 tools in all), Claude Code / API / OpenAI / Gemini / local backends. [`docs/06-phase0-results.md`](docs/06-phase0-results.md)
- ❌ **Calculate it from the chat and read forces back**: on this licence SCIA calculates nothing for the OpenAPI, and its results API returns nothing; `run_analysis` says so instead of reporting ok. [`docs/verify/calc-run.md`](docs/verify/calc-run.md)
- ✅ **Beddings on a saved model** (v0.3.6): soil layers onto the piles, a checked new file next to the model. [`docs/verify/phase1-bedding.md`](docs/verify/phase1-bedding.md)
- ✅ **Pilot readiness** (v0.3.7): LLSM only manages the SCIA it started, `install.cmd`, `llsm diagnose`. [`docs/verify/phase2-pilot.md`](docs/verify/phase2-pilot.md)
- ✅ **Moving load on a saved model** (v0.3.11): one load case per vehicle position along a road. [`docs/verify/phase3-moving-load.md`](docs/verify/phase3-moving-load.md)
- ✅ **Results report of a saved model** (v0.3.12): SCIA's own engineering report as PDF, checked for results. [`docs/verify/report.md`](docs/verify/report.md)
- ✅ **Traffic by the toetsmodel's rules** (v0.3.13, v0.3.14): LM1 per notional lane at the plaatbruggen toetsmodel's positions, from a plain request that names the file. [`docs/verify/phase4-traffic-rules.md`](docs/verify/phase4-traffic-rules.md)
- ✅ **Model builder fixes** (v0.3.15): a load case takes its action type from its group, duplicate names are refused. [`docs/verify/builder-rules.md`](docs/verify/builder-rules.md)
- ✅ **Locally hosted models**: a local server's models are listed under Switch Model, a local chat has the SCIA tools on, and a 35B model builds simple beam models; 7B models do not. [`docs/verify/local-models.md`](docs/verify/local-models.md)

## Installation

Prerequisites: **Windows**, **Python 3.11+**, and **SCIA Engineer 24.0** installed (the pipeline drives SCIA via its OpenAPI; without SCIA you can still run the chat panel in chat-only mode).

```powershell
git clone https://github.com/Quincy987/LLSM.git
cd LLSM
.\install.cmd
```

`install.cmd` needs no administrator rights and can be run again. It ends with `llsm diagnose`, a report on Python, the packages, SCIA, the licence and the Claude CLI. Run it any time with `.venv\Scripts\llsm diagnose`, or use **Tools > Diagnose…** in the panel.

Optional extras:

```powershell
.venv\Scripts\pip install -e .[all]      # openai + gemini + keyring (credentials wizard)
.venv\Scripts\pip install -e .[openai]   # needed for local models / OpenAI-compatible servers
```

There are three ways to hook up an LLM — pick one below.

---

## Option A — Claude Code (recommended, no API key)

Uses your existing Claude subscription through the `claude` CLI. Requires [Claude Code](https://claude.com/claude-code) installed and logged in.

**Chat panel docked to SCIA** (the demo path):

```powershell
.venv\Scripts\llsm-panel
```

A chat window appears and auto-snaps to SCIA's right edge as an owned Win32 window — moves and closes with SCIA, no DLL injection, EULA-safe. Claude Code is the default backend (`claude -p` subprocess); it auto-loads the LLSM MCP server from `.mcp.json`, so all SCIA tools are available out of the box.

The panel is styled like a SCIA side panel. **View > Theme** switches between Light, Dark and Follow Windows (the default); the choice is remembered. The model sidebar is closed by default; turn it on with **View > Show Model Sidebar**.

**Or drive it from the Claude Code terminal directly** (no panel):

```powershell
cd LLSM
claude
> build a 6m wide steel portal frame with fixed bases and 5 kN/m gravity on the rafter
```

Claude Code auto-detects `.mcp.json` and exposes the SCIA tools to whatever model you've configured.

## Option B — Anthropic API directly

Bring your own API key (`sk-ant-...`). Two front-ends:

**Chat panel:**

```powershell
.venv\Scripts\llsm-panel --backend api
```

Set the key once via **Tools → Set up credentials…** in the panel (stored in Windows Credential Manager, or a DPAPI-encrypted file under `~/.llsm/`). An already-set `ANTHROPIC_API_KEY` env var is picked up automatically.

**Headless CLI:**

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
.venv\Scripts\llsm "build a 3m simply supported beam, 10 kN/m load"
```

Full native tool-use — the model drives SCIA in an agentic loop. `--model` overrides the default model.

## Option C — Locally hosted model (Ollama / LM Studio / vLLM / …)

Any server that speaks the OpenAI Chat Completions API works. No cloud, no API key.

1. Install the OpenAI SDK extra: `.venv\Scripts\pip install -e .[openai]`
2. Start your local server and note its base URL:
   - Ollama: `http://localhost:11434/v1`
   - LM Studio: `http://localhost:1234/v1`
   - vLLM: `http://localhost:8000/v1`
3. Launch the panel (`.venv\Scripts\llsm-panel`), open **Tools → Set up credentials…**, choose *OpenAI-compatible*, and enter the base URL (API key is optional — local servers accept anything).
4. Open **File → Switch Model → OpenAI-compatible**: it lists the server's models (asked in the background; a model you pull later shows up the next time you open the menu). Pick one; that starts a new chat on it.

Notes for local models:

- **Model size matters.** Measured on simple beam models ([`docs/verify/local-models.md`](docs/verify/local-models.md)): a 35B model (Qwen3.5-35B-A3B, ~15 GB) built a model that passed every check in 17 of 18 runs, 16 of them with a final answer; a 7B model passed 1 of 9. Use the 20-30 GB class (e.g. `qwen3-coder:30b`, `gpt-oss:20b`; not measured here), preferably with thinking off, and give the server a context of at least 8,000 tokens; a beam chat reached ~4,900.
- **Measure your own model** before relying on it: `.venv\Scripts\python scripts\local_model_trial.py --model qwen3-coder:30b` (Ollama's URL is the default; `--base-url` for another server).
- **SCIA tools are on** in a new local chat. **Tools → Enable SCIA tools for this chat** turns a chat into plain chat.
- If the menu says *server not reachable*, the server is not running at the URL you entered; *server has no models* means it runs but has nothing pulled.
- Cloud services with OpenAI-compatible APIs (OpenRouter, Groq, …) work the same way — just use their base URL and a real API key.

### Other cloud providers

OpenAI (`--backend openai`) and Google Gemini (`--backend gemini`) are also supported with full SCIA tool-use since v0.3.1. Install with `pip install -e .[openai]` / `.[gemini]`, set the key via the credentials wizard (or `OPENAI_API_KEY` / `GOOGLE_API_KEY` env vars). Switching provider mid-session is one click: **Switch Model → \<provider\> → \<model\>**.

### No LLM at all (dev / smoke test)

```powershell
.venv\Scripts\python tests\test_direct_portal_frame.py
```

---

## Beddings on an existing model

Put soil beddings on the piles of a saved SCIA model, without chat or LLM credentials. The model is never changed: the result is a new `<model>_llsm-NN.esa` next to it, read back and checked. Close SCIA first.

The layer table is a CSV or JSON file, top down, with levels in m NAP:

```text
top;bottom;value;unit;description
-2;-8;5000;kh_kN_m3;klei
-8;-14;8000;kh_kN_m3;zand, kleiig
```

`unit` is `kh_kN_m3`, `E_menard_MPa` or `k_line_kN_m2`.

```powershell
.venv\Scripts\llsm bedding preview model.esa layers.csv --diameter 0.45 --start-level -1.1 --piles "S*"
.venv\Scripts\llsm bedding apply model.esa layers.csv --diameter 0.45 --start-level -1.1 --piles "S*"
```

Piles that already carry other line supports are refused unless you pass `--allow-existing`, because the stiffness would add up.

---

## Moving load on an existing model

In the chat, just say what you want: "Zet de verkeersbelasting LM1 op het brugdek (brugdek.esa)". LLSM finds the model by its file name (under Documents: Windows' own Documents folder, also when it is on OneDrive, and the local one; or the folders in `LLSM_MODEL_DIRS`, separated by `;`), takes the deck plate's long axis and thickness from the model, and puts the LM1 tandem in every notional lane at the positions of the plaatbruggen toetsmodel: one exclusive group per lane and configuration, wheels as 0.4 × 0.4 m surface loads. Same rules as beddings: a new `<model>_llsm-NN.esa`, read back and checked; SCIA closed.

The other route puts a vehicle at every step along a road: one load case per position of the front axle, all in one exclusive variable load group `LLSM-ML <name>`.

```powershell
.venv\Scripts\llsm moving-load preview bridge.esa --rule toetsmodel
.venv\Scripts\llsm moving-load preview bridge.esa --path "500,2000;4900,2000" --step 1600 --preset LM1-TS1
.venv\Scripts\llsm moving-load apply bridge.esa --path "500,2000;4900,2000" --step 1600 --axles "200@0,100@1200" --track 2000
```

Coordinates in mm, axle loads in kN (downward), offsets behind the front axle. On a plate deck each wheel gets a free point load; `--deck beam --member L1` puts one load per axle on a beam. A skewed deck needs no angle: draw the path along the road. Details and limits: `docs/10-plan-moving-load.md`, `docs/verify/phase3-moving-load.md`, `docs/verify/phase4-traffic-rules.md`.

---

## Results report of an existing model

Print the engineering report stored in a saved SCIA model (SCIA's own report: tables, units, pictures) as PDF, and check that it really holds results. SCIA prints from a copy; the model is never changed. Close SCIA first; a print takes a few minutes per format.

```powershell
.venv\Scripts\llsm report preview model.esa
.venv\Scripts\llsm report write model.esa
.venv\Scripts\llsm report write bridge_llsm-01.esa --calculate --cases-group "LLSM-ML TS1"
```

The XLSX is always made too: it is what the check reads. `--calculate` runs a linear calculation of a copy first (for a model without fresh results); `--cases-group` adds a chapter per load case of an LLSM moving-load group, on the copy only. A report made before SCIA 17 prints its input but no results ("Geen ondersteunde taak"): the check refuses it and says what to do. Details: `docs/13-plan-report.md`, `docs/verify/report.md`.

---

## Project layout

```
LLSM/
├── .mcp.json                   # Claude Code auto-loads the llsm MCP server
├── install.cmd                 # venv + packages, ends with llsm diagnose
├── LLSM Panel.bat              # double-click launcher for the panel
├── src/llsm/
│   ├── mcp_server.py           # MCP wrapper — SCIA tools exposed to Claude Code
│   ├── mcp_supervisor.py       # keeps the persistent MCP server child running
│   ├── mcp_guard.py            # who may talk to the local MCP server (token)
│   ├── cli.py                  # llsm: headless CLI + bedding / moving-load / report / diagnose
│   ├── panel.py                # Tk chat panel docked to SCIA
│   ├── diagnose.py             # llsm diagnose: can this computer run LLSM?
│   ├── answer_rules.py         # how an answer is shaped for the chat pane
│   ├── dev_watcher.py          # file watcher for the panel's --dev hot reload
│   ├── scia/
│   │   ├── loader.py           # DLL discovery + load (pythonnet)
│   │   ├── autoclick.py        # watches the dialogs of the SCIA this session started
│   │   ├── session.py          # SciaSession — SCIA lifecycle + STA dispatcher
│   │   ├── builder.py          # ADM model builder (new models)
│   │   ├── tools.py            # LLM-callable tool functions
│   │   ├── esa_file.py         # read a saved .esa without SCIA
│   │   ├── esa_xml.py          # update a saved model through ESA_XML.exe
│   │   ├── load_xml.py         # XML for load groups, cases, point and surface loads
│   │   ├── model_lookup.py     # find a saved model by its file name
│   │   └── output_files.py     # where an updated model goes (<model>_llsm-NN.esa)
│   ├── bedding/                # soil beddings onto the piles of a saved model
│   ├── moving_load/            # one load case per vehicle position on a saved model
│   ├── traffic_rules/          # the toetsmodel's lane and position rules mapped onto a deck
│   │   └── _toetsmodel/        # vendored from the plaatbruggen toetsmodel (UPSTREAM.md)
│   ├── report/                 # print and check a saved model's engineering report
│   ├── llm/                    # backends: claude-code, api, openai, gemini,
│   │                           # openai-compatible + credentials + tool loop
│   ├── conversation/           # panel session logic, view, menu, theme
│   └── history/                # chat history + recent models
├── scripts/                    # vendor_toetsmodel_rules.py
├── tests/
│   ├── unit/                   # no SCIA needed
│   └── live/                   # against SCIA; LLSM_LIVE=1, skip while a SCIA runs
├── phase0/                     # spikes and demo scripts
└── docs/                       # design, plans, research
    └── verify/                 # verification report per phase
```

## Docs

| # | Doc | What it covers |
|---|-----|----------------|
| 01 | [`docs/01-research-findings.md`](docs/01-research-findings.md) | SCIA OpenAPI surface (web research) |
| 02 | [`docs/02-vibemodel-reference.md`](docs/02-vibemodel-reference.md) | How VibeModel bridges Claude to Revit |
| 03 | [`docs/03-scia-integration.md`](docs/03-scia-integration.md) | SCIA v24 DLLs, licensing, constraints |
| 04 | [`docs/04-architecture-options.md`](docs/04-architecture-options.md) | Four architectural paths + tradeoffs |
| 05 | [`docs/05-plan.md`](docs/05-plan.md) | Phased roadmap |
| 06 | [`docs/06-phase0-results.md`](docs/06-phase0-results.md) | Phase 0 verification |
| 07 | [`docs/07-vibemodel-lessons-for-scia.md`](docs/07-vibemodel-lessons-for-scia.md) | VibeModel lessons for LLSM |
| 08 | [`docs/08-plan-v0.4-vibemodel-parity.md`](docs/08-plan-v0.4-vibemodel-parity.md) | Plan: VibeModel parity (errors, trust, verify) |
| 09 | [`docs/09-plan-v0.4-existing-models.md`](docs/09-plan-v0.4-existing-models.md) | Plan: work on the engineer's existing model (beddings first) |
| 10 | [`docs/10-plan-moving-load.md`](docs/10-plan-moving-load.md) | Plan: traffic loads on a saved model, with the toetsmodel's rules |
| 11 | [`docs/11-plan-results-to-idea.md`](docs/11-plan-results-to-idea.md) | Plan: plate results in IDEA's column order |
| 12 | [`docs/12-plan-combinations.md`](docs/12-plan-combinations.md) | Plan: propose load combinations (proposal, not built) |
| 13 | [`docs/13-plan-report.md`](docs/13-plan-report.md) | Plan: a results report for a saved model |

What was tested, and how, per phase: [`docs/verify/`](docs/verify/) (route spike, beddings, pilot, moving load, traffic rules, report, builder rules, chat formatting).

## Current tool surface (32 tools)

- **Model defs**: `new_model`, `add_material`, `add_rectangular_section`
- **Geometry**: `add_node`, `add_beam`, `update_node`, `set_member_endpoints`, `set_member_cross_section`, `delete_object`
- **Supports**: `add_fixed_support`, `add_pinned_support`, `add_roller_support`
- **Loads**: `add_load_group`, `add_load_case`, `add_line_load`, `set_line_load_magnitude` (a load case takes its action type from its group: Variable and Moving give a variable case with duration Short, Fire and Seismic an accidental one, Tensioning a permanent one; `action_type` and `duration` override)
- **Query**: `model_summary`, `get_node_position`, `refresh_from_scia`
- **Results**: `get_beam_deflection`, `get_beam_inner_forces`
- **SCIA drive**: `launch_scia`, `push_model`, `run_analysis`, `close_scia`
- **Beddings on a saved model**: `bedding_preview`, `bedding_apply`
- **Moving load on a saved model**: `moving_load_preview`, `moving_load_apply` (an even step, or `rule='toetsmodel'`: LM1 per notional lane at the plaatbruggen toetsmodel's positions, wheels as 0.4 × 0.4 m surface loads)
- **Results report of a saved model**: `report_preview`, `report_write`, `report_status`

Names are unique per model: an `add_*` with a name that already exists is refused and adds nothing.

## Known limitations today

- Cross-sections are rectangular-only (no IPE/HEA library lookup yet).
- Building a model from scratch: no point loads, surface loads or slabs yet (point and surface loads on a saved model: see Moving load).
- LLSM writes no load combinations into a model, neither when building nor on a saved one ([`docs/12-plan-combinations.md`](docs/12-plan-combinations.md) is a proposal; the moving-load envelope is open). Only the report's chapter per load case uses one combination per case, on its throwaway print copy.
- The OpenAPI cannot attach to a SCIA the engineer opened by hand, nor read back a model it did not build ([`docs/03-scia-integration.md`](docs/03-scia-integration.md)). So the saved-model routes work on the file through ESA_XML, with SCIA closed: beddings and moving load write a new `<model>_llsm-NN.esa` next to it; the report prints from a copy.
- The chat cannot calculate a model or read forces back: the OpenAPI's calculation command is one SCIA allows for its own internal use only, and the results API returns nothing on this licence ([`docs/verify/calc-run.md`](docs/verify/calc-run.md)). Calculate in SCIA itself.
- Single SCIA install assumed on the machine.
