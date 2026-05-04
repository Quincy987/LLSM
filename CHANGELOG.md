# Changelog

All notable changes to LLSM are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## v0.3.0 — multi-provider LLM support + credentials wizard (2026-05-04)

### Added
- **OpenAI** backend (`backend_kind="openai"`, default model `gpt-5`).
  Streaming chat with mid-call cancel. Live `/v1/models` filtered to chat-
  capable families. SDK is an optional extra (`pip install llsm[openai]`).
  `src/llsm/llm/openai_backend.py`.
- **Google Gemini** backend (`backend_kind="gemini"`, default
  `gemini-2.5-pro`). Uses the new `google-genai` SDK. Streaming chat.
  Optional extra (`pip install llsm[gemini]`).
  `src/llsm/llm/gemini_backend.py`.
- **OpenAI-compatible** backend (`backend_kind="openai-compatible"`):
  one backend covers Ollama, LM Studio, llama.cpp server, vLLM, OpenRouter,
  Groq, Together, DeepSeek, Mistral. Configurable `base_url` + optional
  `api_key`. ModelRefs prefix the host (e.g. `[ollama] llama3.1:70b`) so
  they don't collide with cloud providers in the menu.
  `src/llsm/llm/openai_compatible_backend.py`.
- **Provider registry** (`src/llsm/llm/providers.py`) — data-driven specs
  for all five backend kinds. Lazy SDK imports via dotted-path resolve
  (CR-1): importing the registry does NOT pull in `openai`, `google.genai`,
  or `anthropic`. SDKs declared as optional extras.
- **Credential storage** (`src/llsm/llm/credentials.py`) — keyring (Windows
  Credential Manager) primary, DPAPI-encrypted file fallback, env-var
  legacy. Profile-keyed schema (`<provider>:<profile>:<field>`) reserves
  the slot for future named profiles without a migration.
- **Credentials wizard** under Tools → "Set up credentials…" — three-step
  Tk modal: provider → auth method → key/login/base_url. Test button
  validates via a worker thread (UI stays responsive). Subscription-login
  path streams `claude setup-token` output to a scrolling log.
  `src/llsm/conversation/credential_wizard.py`.
- **Manage providers…** dialog with Edit / Remove for each configured
  provider.
- **Cascading Switch Model menu** by provider. Providers with missing SDKs
  show their `sdk_install_hint` inline; providers without configured
  credentials show static fallback or "(set up credentials first)".
- **Cross-provider runtime switching**: picking a model from a different
  provider auto-creates a fresh conversation under that provider after a
  one-time confirm modal ("Don't ask again" suppression in
  `~/.llsm/ui_settings.json`). New chats default-titled
  `"New chat ({display_name})"` until the first user turn auto-titles.
- `ModelCatalog.invalidate(kind)` method called by the wizard's Save
  handler so the next menu cascade re-fetches with the new credential.
- Five-way `--backend` choices on `llsm-panel`
  (`claude-code|api|openai|gemini|openai-compatible`).

### Changed
- Re-exported `concurrent.futures.CancelledError` from `llsm.llm.base`
  and `llsm.llm.providers` (CR-4) so every backend and the panel worker
  catch the same canonical symbol.
- Existing `anthropic_backend.py` and `claude_code_backend.py` grew
  `build_backend(panel)` / `static_models()` registry wrappers (A4d /
  CR-5) so `_ensure_backend` is now a single
  `_resolve(spec.build_backend_path)(panel)` call regardless of kind.
- Auto-title logic recognises both `"New chat"` and provider-tagged
  variants (e.g. `"New chat (OpenAI)"`) so cross-provider auto-new-chats
  also get titled from their first user turn.
- `llsm` CLI now accepts `--provider` but scopes it to `api` only in
  v0.3.0 (the registry factory is panel-coupled; headless construction
  lands in v0.3.2).

### Documentation
- README updated with the provider lineup and install extras.

## v0.2.7 — chat history + recent models (2026-05-03)

### Added
- File menu in the LLSM panel: New Chat (Ctrl+N), Recent Chats (last 10),
  Open Chat History… (Ctrl+H, searchable picker with rename/pin/delete),
  Switch Model (recent first then full backend catalog).
  `src/llsm/conversation/menu.py:FileMenuBuilder`, `HistoryDialog`.
- Click-to-rename conversation title bar showing the active conversation
  title and model. Renders `(unavailable)` when the active model is no
  longer in the backend's catalog.
  `src/llsm/panel.py:_refresh_status_title`, `_on_rename_active_conv`.
- Per-conversation persistence under `~/.llsm/conversations/`:
  `<id>/meta.json` is the source of truth, `index.json` is a derived
  cache (atomic write + rebuild-from-meta on parse failure, mutations
  patch in place rather than re-scanning every meta), `active.json`
  is the one-line active-conv pointer, `transcript.jsonl` is append-only.
  `src/llsm/history/store.py:ConversationStore`.
- `~/.llsm/model_usage.json` tracking last-used model per
  `(backend_kind, model_id)`, capped at 50 entries.
  `src/llsm/history/model_usage.py:ModelUsage`.
- Backend Protocol: `available_models()` on each backend (instance method
  delegates to a module-level function so the catalog can query without
  instantiating, which avoids requiring `ANTHROPIC_API_KEY` just to render
  a menu). `AnthropicBackend.restore_messages()` replays a transcript on
  resume (best-effort: tool_use blocks are not persisted).
  `src/llsm/llm/base.py`, `src/llsm/llm/claude_code_backend.py:available_models`,
  `src/llsm/llm/anthropic_backend.py:available_models`, `restore_messages`.
- One-shot migration from legacy `chat.log` + `mcp.session-id` to the
  new per-conversation layout: imports user/llsm/error lines (SYS lines
  are panel-internal noise and skipped) and unlinks the legacy session-id
  file. Idempotent on re-launch.
  `src/llsm/history/migration.py:migrate_legacy`.

### Changed
- `ConversationStore` is now authoritative for the claude session id.
  `mcp_supervisor.read_session_id` / `write_session_id` /
  `clear_session_id` / `SESSION_ID_FILE` and `McpHandle.session_id` are
  removed; the four panel call sites (`_start_mcp_server` spawn +
  reattach, `_restart_mcp_core`, `_ensure_backend`,
  `_on_session_rotated`) read and write through the store. Rotations are
  pinned to the in-flight conv id rather than the currently-active one,
  so a mid-flight conversation switch can't contaminate the new conv
  with the old conv's rotated session id.
  `src/llsm/mcp_supervisor.py`, `src/llsm/panel.py:_ensure_backend`,
  `_on_session_rotated`.
- `_send` now materialises the backend on the Tk thread and passes the
  reference into the chat worker. Without this, a "New Chat" click
  immediately after sending would cause the worker's `_ensure_backend`
  to construct a backend for the NEW conversation and send the user's
  message into a freshly-created claude session for it.
  `src/llsm/panel.py:_send`, `_chat_thread`.
- Mid-flight conversation switches are guarded by `_inflight_conv_id`.
  Replies that arrive after a switch are not rendered into the now-
  active conv's chat widget, not recorded into its transcript, and a
  discreet sysline notes the discard.
  `src/llsm/panel.py:_dispatch_outbox_event`, `_reply_belongs_to_active`.
- Transcript replay now uses each message's ORIGINAL timestamp instead
  of "now"; `_append` accepts an optional `ts` argument.
  `src/llsm/panel.py:_append`, `_render_replay_message`.

### Internal
- New `llsm.history` package (pure-data layer, no Tk imports, no `llm/`
  imports) and new `llsm.conversation` package (headless
  `ConversationSession` + thin Tk `ConversationView` + `FileMenuBuilder`
  / `HistoryDialog`).
- Shared `now_iso` and `atomic_write_json` in `llsm/history/_io.py`.
- `ModelCatalog` accepts a providers map (`backend_kind -> callable`) at
  construction so the history layer never imports llm modules.
  `src/llsm/history/model_catalog.py`.

## v0.2.6 — UTF-8 panel output (2026-05-03)

### Fixed
- Panel chat no longer mojibakes non-ASCII characters from Claude CLI
  output (em-dash, arrows, minus, etc. showed as `â€"`, `â†'`). Root
  cause: `subprocess.Popen(..., text=True)` on Windows decodes via
  `locale.getpreferredencoding()` (cp1252) while the Claude CLI emits
  UTF-8. Now passes `encoding="utf-8", errors="replace"` explicitly.
  `src/llsm/llm/claude_code_backend.py:_run_once`.

## v0.2.5 — restart-app menu item (2026-05-03)

### Added
- `Tools > Restart app  Ctrl+Shift+R` menu item. Closes the running
  panel cleanly (releases the single-instance mutex, shuts down MCP)
  and spawns a fresh panel process with the same CLI args, so a user
  can apply a code update with one click instead of manually closing
  and relaunching. The respawn happens inside `main()`'s `finally`
  AFTER the mutex release so the child doesn't bounce off
  `ERROR_ALREADY_EXISTS`. Spawned with `DETACHED_PROCESS |
  CREATE_NEW_PROCESS_GROUP` on Windows so the child outlives us with
  no inherited console.
  `src/llsm/panel.py:_on_menu_restart_app`, `main`.

## v0.2.4 — claude session recovery (2026-05-03)

### Fixed
- `ClaudeCodeBackend._is_unknown_session()` now recognises the wording
  used by current `claude` CLI versions: `"No conversation found with
  session ID: ..."`. The previous detector only matched
  `"session not found"` / `"unknown session"` / `"no such session"`,
  so when claude rejected a stale persisted session id, the panel raised
  `RuntimeError: claude exit 1: ...` instead of rotating to a fresh
  session and retrying. Surfaced during a v0.2.2 launch test on a panel
  that resumed an old session id from disk.
  `src/llsm/llm/claude_code_backend.py:_is_unknown_session`.

## v0.2.3 — Double-click launcher (2026-05-03)

### Added
- `LLSM Panel.bat` at the repo root: double-click launcher that `cd`s
  to the repo, verifies `.venv\Scripts\pythonw.exe` exists (with a
  one-line setup hint + `pause` if not), and spawns the panel via
  `start "" pythonw -m llsm.panel %*` so no console window lingers
  and CLI args still pass through. Pin to taskbar or drop into
  `shell:startup` for a one-tap launch.

## v0.2.2 — panel UX overhaul (2026-05-02)

### Added
- Status pill above the chat: live indicator that cycles
  `● Idle` → `◐ Thinking…` → `▶ Running: <tool>(args)` → `● Idle`,
  driven by `tool_start` / `tool_end` / `__done__` events on the existing
  outbox queue. Tool-error flashes `⚠ Error` for 3 s. Implemented as a
  `tk.Label` (not `ttk.Label`) so the bg color renders on Windows
  `vista`/`winnative` themes. `panel.py:_set_pill`.
- Collapsible right-side model sidebar (`ttk.Treeview`) with eight
  pre-populated categories (materials, sections, nodes, beams, supports,
  load groups, load cases, loads). Toggleable via `View → Show Model
  Sidebar`. `panel.py:_build_ui`, `_on_toggle_sidebar`.
- Async sidebar refresh path that keeps the Tk main thread non-blocking
  even when claude-code mode would issue MCP I/O: `_request_sidebar_refresh`
  spawns a daemon thread, the result returns via outbox as a
  `('snapshot_ready', payload)` event, repopulation runs on the Tk thread.
  An `_sidebar_fetch_inflight` flag collapses redundant requests; a
  `_sidebar_fetch_dirty` flag triggers a re-fetch on completion when
  `__done__`s arrived during the in-flight call (avoids one-turn staleness).
  `panel.py:_request_sidebar_refresh`, `_sidebar_fetch_thread`.
- `ModelBuilder.snapshot()` returns the locked 8-key contract dict
  (materials/sections/nodes/beams/supports/load_groups/load_cases/loads),
  walking the live ADM model and tolerating partial state via per-object
  try/except. `scia/builder.py:snapshot`.
- `AnthropicBackend.model_snapshot()` (delegates to `ModelBuilder.snapshot`),
  `AnthropicBackend.cancel()` (no-op for parity with `ClaudeCodeBackend`),
  and an optional `on_tool_event(phase, name, payload)` callback fired
  around every `_invoke_tool` so the panel pill can show live per-tool
  status. `llm/anthropic_backend.py`.
- `ClaudeCodeBackend.model_snapshot()` returns an empty-but-conformant
  schema (degraded mode — accepted in plan since the builder lives in
  the MCP subprocess we can't query directly today).
  `llm/claude_code_backend.py`.
- `[YOU] / [LLSM] / [ERROR] / [SYS]` chat badges with `▎` accent bar,
  per-turn timestamp, role-tinted body color, and `─`-row separator
  between turns. State is never conveyed by color alone — every role and
  pill state has a glyph too (`● ◐ ▶ ⚠ ▎`) so colorblind users and dark
  themes both stay legible. `panel.py:_append`, `_sysline`,
  `_GLYPH_*`, `_THEME_REFERENCE`.
- Auto-grow input (4–10 lines on `<KeyRelease>`) and a greyed
  `"Describe your model... (Enter sends, Shift+Enter newline)"`
  placeholder that hides on focus, restores on blur, and is suppressed
  by `_send` so it never gets accidentally submitted. `panel.py`.

### Changed
- `_drain_outbox` rebuilt for chat-pipeline isolation: each event-kind
  branch is wrapped in its own try/except via `_dispatch_outbox_event`,
  and the `_safe_after(80, self._drain_outbox)` reschedule moved into a
  `finally` block so a misbehaving handler (sidebar, snapshot, pill)
  cannot freeze the chat heartbeat. `panel.py:_drain_outbox`,
  `_dispatch_outbox_event`.
- Theme handling extracted into `_tint()` and `_apply_theme()` helpers.
  `_tint` resolves any Tk color spec (`#hex`, named, symbolic
  `SystemFoo`) via `root.winfo_rgb()` to avoid the silent garbage-output
  trap when `ttk.Style().lookup()` returns symbolic system color names
  on Windows. `_apply_theme` covers BOTH chat tags AND `tk.Text` widget
  surfaces (`background` / `foreground` / `insertbackground`), since
  `tk.Text` does not inherit a `ttk` theme. `panel.py:_tint`,
  `_apply_theme`, `_resolve_rgb`.
- `_append` and `_sysline` now render each turn as a three-line block
  (badge + timestamp / body / separator) instead of a flat
  `"role: text\n\n"`, which is what made the previous chat look like a
  console log. `panel.py`.
- `AnthropicBackend._invoke_tool` emits paired `start`/`end` tool events
  (success, exception, unknown-tool, and not-implemented all symmetric)
  via `_emit_tool_event`. The panel's `_on_tool_event_from_worker`
  bridges these onto the `outbox` queue from the worker thread.
  `llm/anthropic_backend.py`.

### Fixed
- Sidebar category fail-fast: a single corrupt item used to drop every
  later item in the same category while the count label still showed
  the full count. Per-item insert is now isolated by its own try/except.
  `panel.py:_refresh_sidebar_widget`.
- `f'{None:g}'` `TypeError` when a snapshot has explicit `None` numeric
  fields. Added `_num()` helper that collapses missing/None/non-numeric
  inputs to `0.0`. Promoted `_format_sidebar_item` to a `classmethod`.
  `panel.py:_num`, `_format_sidebar_item`.
- Status pill auto-clear race: a 3-second error auto-clear could later
  overwrite a fresh `running:tool` or `thinking` state from the next
  turn. `_set_pill` now cancels any pending auto-clear when the new
  state is anything other than `error`. `panel.py:_set_pill`.
- Sidebar pack-order on toggle: `pack_forget()` followed by `pack(...)`
  appends the slave to the END of the pack order, but `self.history`
  has `fill=BOTH+expand=True` and would have already claimed the cavity
  — the re-shown sidebar was being allocated zero size. Fixed by
  passing `before=self.history` to the re-pack call. `panel.py:_on_toggle_sidebar`.

## v0.2.1 — dev-mode hot-reload (2026-05-02)

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
