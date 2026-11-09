# LLSM — Local LLM for SCIA Model

Drive **SCIA Engineer** via natural language. An engineer types *"build a 6m x 4m steel portal frame with fixed bases and 5 kN/m gravity on the rafter"*, and LLSM builds, pushes, and analyses the model.

Inspired by VibeModel (the Revit equivalent, internal).

**Target:** SCIA Engineer 24.0 on Windows.

## Status

- ✅ **Phase 0 complete** — autonomous end-to-end pipeline proven. See [`docs/06-phase0-results.md`](docs/06-phase0-results.md).
- ✅ **Phase 1** — Python package, 25 SCIA tools, multi-provider LLM support, chat panel, MCP integration, CLI.

## Installation

Prerequisites: **Windows**, **Python 3.11+**, and **SCIA Engineer 24.0** installed (the pipeline drives SCIA via its OpenAPI; without SCIA you can still run the chat panel in chat-only mode).

```powershell
git clone https://github.com/Quincy987/LLSM.git
cd LLSM
install.cmd
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
3. Launch the panel:

```powershell
.venv\Scripts\llsm-panel --backend openai-compatible
```

4. In the panel, open **Tools → Set up credentials…**, choose *OpenAI-compatible*, and enter the base URL (API key is optional — local servers accept anything). Then pick your model via **Switch Model**.

Notes for local models:

- **SCIA tool-use is OFF by default** for this backend — small local models often misuse tool calls. Enable it with the *SCIA tools* checkbox in the panel (per-conversation). Use a tool-capable model (e.g. `qwen2.5`, `llama3.1`) for driving SCIA; anything works for chat-only.
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

## Project layout

```
LLSM/
├── .mcp.json                   # Claude Code auto-loads the llsm MCP server
├── src/llsm/
│   ├── mcp_server.py           # MCP wrapper — SCIA tools exposed to Claude Code
│   ├── cli.py                  # headless Anthropic-API CLI
│   ├── panel.py                # Tk chat panel docked to SCIA
│   ├── scia/
│   │   ├── loader.py           # DLL discovery + load (pythonnet)
│   │   ├── autoclick.py        # background thread that dismisses SCIA dialogs
│   │   ├── session.py          # SciaSession — SCIA lifecycle + STA dispatcher
│   │   ├── builder.py          # ADM model builder helpers
│   │   └── tools.py            # LLM-callable tool functions
│   └── llm/                    # backends: claude-code, api, openai, gemini,
│                               # openai-compatible + credentials + tool loop
├── tests/
├── phase0/                     # Phase 0 verification artifacts
└── docs/                       # design + research + Phase 0 results
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

## Current tool surface (27 tools)

- **Model defs**: `new_model`, `add_material`, `add_rectangular_section`
- **Geometry**: `add_node`, `add_beam`, `update_node`, `set_member_endpoints`, `set_member_cross_section`, `delete_object`
- **Supports**: `add_fixed_support`, `add_pinned_support`, `add_roller_support`
- **Loads**: `add_load_group`, `add_load_case`, `add_line_load`, `set_line_load_magnitude`
- **Query**: `model_summary`, `get_node_position`, `refresh_from_scia`
- **Results**: `get_beam_deflection`, `get_beam_inner_forces`
- **SCIA drive**: `launch_scia`, `push_model`, `run_analysis`, `close_scia`

## Known limitations (Phase 2)

- Cross-sections are rectangular-only (no IPE/HEA library lookup yet).
- No point loads, surface loads, slabs, or load combinations yet.
- Single SCIA install assumed on the machine.
