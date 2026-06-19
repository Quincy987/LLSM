# 05 — Recommended Plan

*Phased plan for LLSM, following the hybrid architecture (Option D from `04-architecture-options.md`).*

## Guiding principles

1. **Demo-first.** Optimise for "engineer talks to LLM, something happens in a Scia project" ASAP — cut scope aggressively.
2. **Reuse VibeModel''s layering.** LLM layer is app-agnostic; port it largely unchanged. Only tool implementations are Scia-specific.
3. **Don''t ship in-process plugin until we know the headless tool surface is stable.**
4. **Always have an escape hatch** (`exec_csharp` or `exec_python` tool) so the LLM is never fully stuck when a scripted command is missing.
5. **Safety first for edits.** Structural models shouldn''t silently mutate. Preview + confirm from day one for anything destructive.

## Phase 0 — Preflight (before writing code)

**Goal:** clear the risks that would invalidate the rest of the plan.

- [ ] Confirm our SCIA v24 license includes OpenAPI / ADM / EsaAutomation access (email Nemetschek/Scia if unclear).
- [ ] Install path sanity-check: `C:\Program Files\SCIA\Engineer24.0\OpenAPI_dll\` exists and contains ADM + OpenAPI DLLs.
- [ ] Clone and run `scia-garage/open-api-adm-jupyter` locally against v24. If it creates a node + beam end-to-end, we''re green.
- [ ] Decide first host environment for LLM: local Ollama (privacy, no bill) vs Anthropic API (quality). Can do both but pick primary.

**Exit criterion:** we''ve run someone else''s code against our v24 install and it produced a SCIA project.

---

## Phase 1 — Headless agent MVP

**Goal:** an LLM can, via chat, create a trivial SCIA model from scratch and read back its results.

### Stack
- Language: **Python** (fastest iteration; we can migrate the tool layer to C# later if needed)
- Bridge to SCIA: `pythonnet` + ADM DLLs from `Engineer24.0\OpenAPI_dll\`
- LLM: start with **Anthropic API direct** (proven tool-calling quality), add **local OpenAI-compatible** (Ollama / LM Studio) as a second backend
- Control surface: CLI first. No GUI yet.

### Suggested repo layout
```
LLSM/
├─ docs/                         (this folder)
├─ src/llsm/
│  ├─ scia/
│  │  ├─ __init__.py
│  │  ├─ loader.py               (registry discovery + assembly resolve)
│  │  ├─ session.py              (Environment lifetime, STAThread)
│  │  └─ tools/                  (one file per tool, auto-discovered)
│  │     ├─ create_beam.py
│  │     ├─ create_node.py
│  │     ├─ list_members.py
│  │     └─ ...
│  ├─ llm/
│  │  ├─ backend.py              (IChatBackend abstraction)
│  │  ├─ anthropic_backend.py
│  │  ├─ local_backend.py
│  │  └─ loop.py                 (agentic tool-calling loop)
│  ├─ grounding.py               (current-project context block)
│  └─ cli.py
├─ examples/
│  └─ simple_frame.md            (scripted conversation producing a 1-span frame)
├─ tests/
└─ pyproject.toml
```

### Tool set for MVP (8–10 tools is enough)

| Family  | Tool                   | Notes                                       |
|---------|------------------------|---------------------------------------------|
| Project | `open_project`         | ⚠️ Via OpenAPI: can open a .esa but it's push-only — opened models can't be read/enumerated/edited (q-662). Possible lead: the third-party connector SDK (`dll_calatrava\SciaTools.ThirdParty.SDK*`) exposes `GetAnalysisModelFromSciaEngineer()` — read-back exists there; gated/unverified. |
| Project | `new_project`          | fresh project with default template         |
| Project | `save_project`         |                                             |
| Query   | `project_info`         | name, path, counts — used for grounding     |
| Query   | `list_members`         |                                             |
| Query   | `get_member`           | by id                                       |
| Create  | `create_node`          | x, y, z                                     |
| Create  | `create_beam`          | two node ids + cross-section + material     |
| Create  | `create_point_support` | on node                                     |
| Create  | `create_point_load`    | magnitude + direction + node/member         |
| Execute | `run_linear_analysis`  |                                             |
| Query   | `get_reactions`        | after analysis                              |
| Escape  | `exec_python`          | run arbitrary Python against `Environment`  |

### Deliverable
Scripted demo: user types *"Build a 5m simply-supported beam, IPE240, S235, with a 10 kN point load at midspan, and tell me the max deflection."* LLSM creates the model, runs the analysis, reports the number.

### Exit criteria
- Anthropic backend + local backend both work with the same tool set
- At least 3 example conversations in `examples/` produce correct models
- Grounding block injects current project state before every turn

---

## Phase 2 — Broaden tool surface + safety

**Goal:** cover the most common structural-engineering workflows, add safety rails.

- Add 2D members (slabs, walls) — `create_slab`, `set_slab_thickness`
- Add load cases, combinations, more load types (line, surface, moment)
- Add result queries beyond reactions — `get_internal_forces`, `get_deflections`
- **Dry-run / preview mode**: for any tool that mutates the model, preview the SAF XML diff before applying. Require explicit confirmation step for batches above N changes.
- **Undo**: wrap each tool call in a transaction / snapshot so `/undo` reverts the last operation.
- Error-handling pass: what happens when ADM throws? What happens when license is missing at runtime? Tool calls must return structured errors the LLM can reason about.

---

## Phase 3 — SAF batch mode

**Goal:** support "generate a whole model from a textual brief" without driving a live Scia session.

- Tool: `generate_saf_from_brief` — LLM emits SAF XML directly, we validate against schema, pipe into `ESA_XML`.
- Use for "given these requirements, produce a candidate model" before opening it in Scia.
- Good for CI/CD and for onboarding new engineers ("write me a starter model").

---

## Phase 4 — Live UI integration (optional, scope TBD)

**Goal:** engineer chats with LLSM *while* Scia is open, sees model update live, can say "change *that* beam" referring to current selection.

- Prerequisite: investigate Scia v24''s plugin SDK. If acceptable, build a C# add-on that:
  - Hosts an HTTP endpoint (mirror VibeModel''s `localhost:18884`)
  - Exposes "current selection" and "active project" to the headless Python harness via HTTP
  - Eventually routes mutation calls through the live session instead of a headless one
- If the plugin SDK is too limited, fall back to: engineer manually saves, LLSM reloads, operates on the saved version. Workable but clunky.

---

## Phase 5 — Domain intelligence (continuous)

**Goal:** the LLM doesn''t just orchestrate tools — it can reason about structural engineering within constraints.

- RAG over Scia help docs + Eurocode (EC0 / EC1 / EC2 / EC3 / EC5) snippets
- Project conventions (unit systems, standard sections, firm-specific defaults) injected into system prompt
- Code checks: wrap Scia''s existing code-check routines as tools (`check_ec3_member`, `check_ec2_slab`)
- Scenario tools: "what if this span were 6m instead of 5m?" — create modified copy, compare

This is an ongoing axis, not a phase with an exit criterion.

---

## Risks and mitigations

| Risk                                        | Mitigation                                                           |
|---------------------------------------------|----------------------------------------------------------------------|
| License doesn''t cover OpenAPI              | Resolve in Phase 0. Worst case: fall back to pure SAF XML (Option C) |
| pythonnet silently picks wrong constructor  | Wrap all args in explicit `System.Double` etc.; unit-test each tool  |
| LLM hallucinates cross-section names        | Ground with `list_available_sections`, `list_available_materials`    |
| LLM makes destructive changes               | Phase 2 dry-run/confirm; transactional undo                          |
| SCIA headless startup is slow               | Reuse long-lived `Environment` per session; don''t restart per call  |
| Plugin SDK on v24 is limiting               | Phase 4 is explicitly optional; Phases 1–3 deliver value without it  |

---

## What is explicitly NOT in scope (yet)

- Multi-user / server-hosted LLSM (single-engineer-desktop only for now)
- Revit interop (VibeModel already does that; this is sibling, not merger)
- Non-Scia solvers (Robot, Autodesk, etc.)
- Mobile / web UI — CLI first, revisit after Phase 2

---

## Open questions (for the user / stakeholders)

1. Primary user: you personally, or a team? (Affects deployment model.)
2. Acceptable "time to first demo" target?
3. Preference for local LLM vs cloud LLM for the first build? (Driven by data-sensitivity of project models.)
4. Any specific first use-case beyond the beam demo? (e.g., "quickly model a typical floor plate", "extract reactions for foundation design") — would let us tune the MVP tool set to what''s actually useful on day 1.

