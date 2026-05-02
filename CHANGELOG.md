# Changelog

All notable changes to LLSM are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `llsm-panel --dev` flag for developer hot-reload: watches `src/llsm/`
  for source changes and auto-restarts the MCP server on edits to
  `scia/**` or `mcp_server.py`. Panel-side edits (panel.py, llm/**,
  mcp_supervisor.py) emit a one-line SYS nudge ("restart panel manually")
  debounced per-file so format-on-save doesn't spam chat.log. Off by
  default; opt-in via `pip install -e .[dev]` to pull in the new
  `watchdog` optional dependency. Colleagues running the POC install
  without `[dev]` see a SYS warning and `--dev` becomes a no-op.
- `src/llsm/dev_watcher.py`: standalone `_DevWatcher` with watchdog
  Observer, 500 ms debounce that coalesces rapid save bursts, MCP-busy
  defer (so dev reloads don't fight the existing watchdog), and a
  per-file 30 s nudge cooldown.
- `[project.optional-dependencies] dev = ["watchdog>=4.0"]` in
  `pyproject.toml`.

### Changed
- `panel.py:_auto_restart_mcp` split into a watchdog wrapper plus a
  shared `_restart_mcp_core` body. The dev hot-reload path reuses the
  kill+respawn machinery without consuming the watchdog's per-session
  `MCP_MAX_RESTARTS_PER_SESSION` budget. User/menu-triggered restarts
  continue to count as before.

## v0.1.1 — Persistent MCP server (2026-04-29)

### Added
- Persistent MCP server (`--http` transport) so SCIA Engineer and the in-memory
  ADM model survive between LLM turns. Started once by the panel; the engineer
  can iteratively edit ("now move the support 1 m left") instead of rebuilding
  from scratch.
- Token-gated admin surface on the persistent server: `POST /admin/shutdown`
  (header `X-LLSM-Shutdown-Token`) and `GET /admin/ping`.
- Five edit primitives in `scia/builder.py` exposed as MCP tools:
  `update_node`, `delete_object` (with cascade-blocker reporting),
  `set_line_load_magnitude`, `set_member_cross_section`,
  `set_member_endpoints`.
- `LlsmContext.mesh_dirty` flag: set after every successful push or edit;
  `run_analysis` re-meshes only when dirty (fast path for repeated result
  queries on an unchanged model).
- `mcp_supervisor.py`: panel-side child-process supervisor with stale-cleanup
  (PID-and-cmdline match), atomic port-file polling, and a layered graceful
  shutdown (admin route → CTRL_BREAK_EVENT → terminate).
- Child-process logs at `~/.llsm/mcp.log`; failures surface a tail of the log
  in supervisor error messages.

### Changed
- `mcp_server.py` split tool registration into common vs stdio-only:
  `close_scia` is registered ONLY in stdio mode, hidden from the LLM in HTTP
  mode so it can no longer kill the persistent session.
- `claude_code_backend.py` accepts `mcp_config_json`; when set, every chat
  turn passes `--mcp-config <json> --strict-mcp-config` plus a
  `PERSISTENT_SYSTEM_PROMPT` teaching the LLM to prefer edits over rebuilds
  and to skip `push_model` after edits.
- `panel.py` spawns the MCP supervisor on startup (claude-code backend only),
  hands the live `mcp_config_json` to the backend, and wires
  `WM_DELETE_WINDOW` to a graceful supervisor shutdown.
- Edit-tool wrappers attach SCIA-sync failures as `sync_warning` on the
  success result (rather than raising), so the LLM always sees structured
  output.

### Fixed
- Pipe-buffer deadlock: child stdio now goes to a log file instead of an
  undrained `subprocess.PIPE` (which would have hung the child once the
  buffer filled during a long session).
- `find_dependents` now compares by `Name` string instead of Python
  `is`-identity; pythonnet returns fresh proxies on each managed-attribute
  access, so the previous identity check silently returned `[]` and broke
  the cascade-blocker feature.
- `delete_object` cascade tolerates an already-removed dependent reachable
  through two paths (no longer raises `KeyError` on the second visit).
- `mcp_supervisor.spawn` unconditionally clears `PORT_FILE`/`PID_FILE` after
  `cleanup_stale_server`, so a stray port-file from a previous run can no
  longer make the supervisor return a stale port to the panel.
- Renamed `update_node` failure field `invalidated_beams` → `dependent_beams`
  in `scia/tools.py`, the MCP tool docstring, `INSTRUCTIONS_PERSISTENT`, and
  `PERSISTENT_SYSTEM_PROMPT` (the field is every beam referencing the moved
  node, not just structurally invalid ones — the previous label misled the
  LLM).
- `mcp_config_json` uses compact JSON separators so the value can never carry
  embedded spaces.

### Notes
- Stdio transport (legacy `.mcp.json` flow) is preserved for backward
  compatibility; it still exposes `close_scia`.
- Result extraction (the v24 ApiGuid bug) is still deferred; not addressed
  in this release.
- The direct-API backend (`--backend api`) does not yet expose the new edit
  primitives via `llm/tools.py:TOOL_DEFS` — the persistent-session features
  apply only to the Claude Code backend.
