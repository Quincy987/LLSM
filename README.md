# LLSM — Local LLM for SCIA Model

Drive **SCIA Engineer** via natural language. An engineer types *"build a 6m x 4m steel portal frame with fixed bases and 5 kN/m gravity on the rafter"*, and LLSM builds, pushes, and analyses the model.

Inspired by [VibeModel](C:/Development/VibeModel) (the Revit equivalent).

**Target:** SCIA Engineer 24.0 on Windows.

## Status

- ✅ **Phase 0 complete** — autonomous end-to-end pipeline proven. See [`docs/06-phase0-results.md`](docs/06-phase0-results.md).
- ✅ **Phase 1 scaffold complete** — Python package, 16 SCIA tools, Anthropic backend, Claude Code MCP integration, CLI.

## Four ways to run it

### 1. Chat panel docked to SCIA (the colleague-demo path)

```powershell
cd C:\Development\LLSM
.venv\Scripts\llsm-panel
```

A Tk chat window appears. Launch SCIA (manually or via the panel asking for it), and the panel auto-snaps to SCIA's right edge as an owned Win32 window — moves and closes with SCIA, no DLL injection or unsupported hooks. EULA-safe.

By default the panel uses **Claude Code as the LLM backend** via subprocess (`claude -p` with your Max subscription) — no API key needed, Opus 4.7 by default. It auto-loads the LLSM MCP server from `.mcp.json` so all 18 SCIA tools are available.

```powershell
.venv\Scripts\llsm-panel --backend api    # fall back to direct Anthropic API (needs key)
.venv\Scripts\llsm-panel --model sonnet   # pick a different model
```

### 2. Claude Code CLI directly (no UI)

```powershell
cd C:\Development\LLSM
claude
> build a 6m wide steel portal frame with fixed bases and 5 kN/m gravity on the rafter
```

Claude Code auto-detects `.mcp.json`, loads the LLSM MCP server, exposes the 18 tools to whatever model you've configured (Opus 4.7 by default).

### 3. Standalone Anthropic-API CLI

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
.venv\Scripts\llsm "build a 3m simply supported beam, 10 kN/m load"
```

### 4. Direct Python (no LLM — dev / smoke test)

```powershell
.venv\Scripts\python tests	est_direct_portal_frame.py
```

## One-time setup

```powershell
cd C:\Development\LLSM
python -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\pip install "mcp[cli]"
```

Then set `$env:ANTHROPIC_API_KEY` if you want the built-in CLI; skip it if you're using Claude Code (which handles auth itself).

## Project layout

```
LLSM/
├── .mcp.json                   # Claude Code auto-loads the llsm MCP server
├── src/llsm/
│   ├── mcp_server.py           # MCP wrapper — 16 tools exposed to Claude Code
│   ├── cli.py                  # direct Anthropic-API CLI
│   ├── scia/
│   │   ├── loader.py           # DLL discovery + load (pythonnet)
│   │   ├── autoclick.py        # background thread that dismisses SCIA dialogs
│   │   ├── session.py          # SciaSession — SCIA lifecycle + STA dispatcher
│   │   ├── builder.py          # ADM model builder helpers
│   │   └── tools.py            # LLM-callable tool functions
│   └── llm/
│       ├── tools.py            # JSON schemas for Anthropic tool-use
│       └── anthropic_backend.py # agentic tool-calling loop (for option 2)
├── tests/
│   └── test_direct_portal_frame.py
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

## Current tool surface (16 tools)

- **Model defs**: `new_model`, `add_material`, `add_rectangular_section`
- **Geometry**: `add_node`, `add_beam`
- **Supports**: `add_fixed_support`, `add_pinned_support`, `add_roller_support`
- **Loads**: `add_load_group`, `add_load_case`, `add_line_load`
- **Query**: `model_summary`
- **SCIA drive**: `launch_scia`, `push_model`, `run_analysis`, `close_scia`

## Known limitations (Phase 2)

- Cross-sections are rectangular-only (no IPE/HEA library lookup yet).
- `run_analysis` confirms the calc ran but doesn't return numerical results. RAPI initializes; `LoadResult(resultKey)` tuning is next.
- No point loads, surface loads, slabs, or load combinations yet.
- Single SCIA install assumed on the machine.
