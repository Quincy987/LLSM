# 02 — VibeModel Reference Pattern

*How the existing VibeModel Revit integration is structured, so we can reuse its shape for Scia.*

## Problem VibeModel solves

Bridges a conversational LLM (Claude, local models) to Autodesk Revit so users can say *"create a wall from 0,0 to 5000,0, 3000mm high"* instead of learning the Revit API or UI.

## Architecture (one diagram, in words)

```
User (chat UI / Claude Code CLI)
        │
        │  natural language
        ▼
   IChatBackend  ──────────────► selects: Claude CLI / Anthropic API / Local LLM
        │
        │  tool_use tool call
        ▼
   HTTP GET http://localhost:18884/wall?args=...
        │
        ▼
   TcpListener (background thread, inside Revit.exe)
        │  enqueue CommandRequest
        │  raise ExternalEvent
        ▼
   Revit main thread  ──────────► RevitCommandHandler
                                   ├── resolve IClaudeCommand by name
                                   ├── execute inside Transaction
                                   └── signal ManualResetEvent
        │
        │  JSON result
        ▼
   HTTP 200 back to LLM ─────────► tool_result fed into next turn
```

## Key components

### 1. HTTP bridge (`TcpListener` on `localhost:18884`)
- Lives inside the Revit process (loaded by the add-in).
- Runs on a background thread so it doesn't block Revit's UI.
- **Why HTTP not COM/OLE?** No admin rights needed, no polling, trivially callable from any LLM environment (Python, Node, curl, MCP), and works with Claude Code CLI out of the box.

### 2. Thread dispatcher (`ExternalEvent`)
- Revit's API is single-threaded — all calls must happen on the main thread.
- Incoming HTTP request → `CommandRequest` is pushed into a thread-safe queue → `ExternalEvent.Raise()` wakes Revit's main thread → handler drains the queue → signals a `ManualResetEvent` when done → HTTP response goes back.
- **Scia analogue needed:** must identify Scia's equivalent mechanism (Scia's OpenAPI typically runs out-of-process via `EsaAutomation`, which sidesteps this problem; if we build an in-process plugin we'll need to verify the threading model).

### 3. Command registry (auto-discovery via reflection)
- `IClaudeCommand` interface with a single `Execute(args, UIApplication)` method.
- `ClaudeCommandRegistry.DiscoverCommands()` scans the assembly for implementers at startup.
- Add a new `.cs` file → automatically exposed as `/commandName`. No hardcoded routing.

**Commands split into three families:**
- **Query:** `info`, `selected`, `params`, `list`, `get`, `views`, `levels`, `geometry`, `family`, `categories`
- **Modify:** `wall`, `floor`, `delete`, `set`, `color`, `place`, `select`
- **Escape hatch:** `exec` (runs arbitrary C#), `batch` (multi-command POST)

### 4. Pluggable LLM backends (`IChatBackend`)
Three concrete backends selected in `VibeModelApp.CreateChatBackend()`:
- **`ClaudeCodeBackend`** — spawns `claude` CLI subprocess, pipes prompts, system prompt from `%LocalAppData%\VibeModel\system-prompt.txt`.
- **`AnthropicDirectBackend`** — calls `api.anthropic.com/v1/messages` directly. Pay-per-use. **Implements the full agentic tool-calling loop** (up to 25 iterations).
- **`LocalLlmBackend`** — OpenAI-compatible HTTP endpoint (works with Ollama, LM Studio, vLLM, llama.cpp server). Same tool-calling loop as Anthropic direct.

All three share the same agentic loop:
1. Send user message + tool definitions.
2. If `stop_reason == "tool_use"` → execute each tool by calling `http://localhost:18884/{command}`.
3. Append tool results to conversation.
4. Loop until `stop_reason != "tool_use"` or iteration cap hit.

### 5. Context grounding
Before every LLM turn, `GatherModelContext()` prepends a block like:
```
CURRENT REVIT CONTEXT:
Project: MyBuilding.rvt
Active view: Level 1
Selection: 3 walls, 1 door
Element counts: 142 walls, 87 doors, ...
```
This is **the single most important trick** — without it the LLM guesses instead of grounding on the live model.

### 6. Fallback
If port 18884 is blocked, falls back to file-based polling in `C:\RevitClaudeLink\` — LLM writes JSON command files, add-in polls and writes responses.

## Tech stack

- C# / .NET Framework 4.8
- `Autodesk.Revit.SDK` (version-specific per Revit release)
- `Markdig` for markdown rendering in WPF chat pane
- Build via `build.ps1` → auto-deploys to `%APPDATA%\Autodesk\Revit\Addins\{year}\`

## What transfers to LLSM

| Component | Transfers as-is? | Notes |
|-----------|------------------|-------|
| `IChatBackend` abstraction + 3 backends | ✅ 100% | LLM layer is app-agnostic |
| Agentic tool-calling loop | ✅ 100% | Same shape works for any tool set |
| Context-grounding pattern | ✅ 100% | Just swap the content (Scia project state) |
| HTTP bridge on localhost | ✅ If we build a plugin | Only needed if in-process; out-of-process doesn't need it |
| `ExternalEvent` dispatcher | ⚠️ Revit-specific | Scia's threading model is different |
| Command auto-discovery via reflection | ✅ Transfers | Works in any .NET or Python codebase |
| Command set (walls, floors, …) | ❌ | Different domain — replace with Scia ADM objects (beams, slabs, nodes, loads, supports) |
| Build / deploy mechanics | ❌ | Revit add-in folder replaced by Scia plugin folder (or out-of-process install) |

## What to keep in mind

- VibeModel's big insight isn't any single mechanism — it's the **layering**: LLM layer → tool layer → host-app layer. Each is swappable.
- The `exec` escape hatch is valuable: when the LLM encounters an unscripted case, it can just write C# inline rather than failing. We should replicate this for Scia (e.g., `exec_csharp` or `exec_python`).
- All three backends share one code path after "get next message" — makes adding a new provider cheap.
