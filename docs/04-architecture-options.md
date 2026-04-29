# 04 — Architecture Options

*Four realistic shapes for LLSM, ranked from most-VibeModel-faithful to most-batch.*

## Option A — In-process Scia plugin (C#)

**Shape:** Build a C# add-on that loads inside `SciaEngineer.exe`, hosts an HTTP listener on `localhost:18884` (or similar), dispatches LLM tool calls onto Scia's main thread.

Mirror of VibeModel, verbatim.

**Pros**
- Live interactive editing — engineer sees model change in real time
- Rich "current state" grounding (active project, current selection, current view)
- Single process, no IPC overhead
- Best UX for "collaborate with the LLM while modelling"

**Cons**
- Need to confirm Scia v24 exposes an add-on SDK with the necessary hooks (thread dispatch, UI integration)
- Need to confirm whether we can embed our own thread / HTTP listener inside the Scia process
- Deployment: each engineer's install needs the plugin registered
- Highest up-front cost before seeing *anything* working

**Risk:** **Highest** — everything hinges on plugin-SDK capability that we haven't yet verified.

---

## Option B — Out-of-process driver via EsaAutomation (headless)

**Shape:** LLSM is a separate process (Python or C#). It uses `EsaAutomation` to spin up SCIA in background mode, ADM to manipulate the model, RAPI to read results. SCIA runs headless — no UI.

**Pros**
- Simplest to ship — no plugin registration, no deployment ceremony
- Easy to iterate; kill + restart SCIA at will
- Natural CI/CD integration — tests can run in headless containers (if license permits)
- Works today on v24 with current official stack

**Cons**
- No visual feedback for the engineer during editing — they see results after the run, not during
- "What is the user currently working on?" grounding is weaker — we only know what's in the file
- Launching SCIA is slow (seconds per cold start)

**Risk:** **Low.** Well-trodden path. VIKTOR does this today.

---

## Option C — SAF XML generator (batch)

**Shape:** LLM emits SAF-compliant XML. We validate. We pipe into SCIA via the `ESA_XML` routine (headless run → results come back as XML).

**Pros**
- Zero runtime coupling to the SCIA process during generation
- LLM → XML is a pure text-generation problem, very well-suited to LLMs
- Trivially testable, trivially reproducible
- Easy to deploy anywhere (browser, chat, CLI)

**Cons**
- **Batch-only** — no live back-and-forth, no "now edit this beam" once the model exists
- Full-model regeneration is wasteful for small edits
- Poor fit for "read / inspect what's already in the model"
- SAF schema must be learned — LLMs will hallucinate fields unless grounded with schema + examples

**Risk:** **Lowest** — it's just file generation. But the feature ceiling is low.

---

## Option D — Hybrid (recommended start)

**Shape:**
- **Phase 1:** Python + pythonnet harness running out-of-process (Option B style), with ADM/RAPI/EsaAutomation calls wrapped as LLM tools. Same `IChatBackend` pluggability as VibeModel (Claude CLI / Anthropic / local Ollama).
- **Phase 2:** Optionally add a thin C# plugin later, only if we need *live* grounding on the engineer's active session (Option A).
- SAF XML (Option C) is available as an escape hatch for "generate a fresh model from a brief" flows.

**Pros**
- Lowest time-to-first-demo (Python + pythonnet + a handful of tools can be running in days)
- Keeps the LLM layer in Python, where the ecosystem (MCP, OpenAI-compat, LangChain-adjacent tooling) is strongest
- Preserves the option to build a plugin later without re-architecting the LLM side
- De-risks: if the plugin SDK turns out to be thin/undocumented, we haven't invested in it

**Cons**
- Initial UX isn't "live in the SCIA UI" — it's "chat window drives a headless SCIA"
- pythonnet on v24 is unofficial (but community-proven)
- Eventually two codebases to maintain if we do add the plugin

**Risk:** **Low.**

---

## Decision matrix

| Criterion | A (plugin) | B (headless) | C (SAF) | D (hybrid) |
|-----------|:----------:|:------------:|:-------:|:----------:|
| Time to first demo | Slow | Medium | Fast | **Fast** |
| Live UX | **Best** | Weak | None | Weak → Best (phased) |
| Read current selection | **Yes** | Weak | No | Weak → Yes (phased) |
| Risk of blocker | High | Low | Low | **Low** |
| Production longevity | High | High | Low | **High** |
| Dev complexity | High | Medium | Low | Medium |
| Fits v24 supported stack | ⚠️ SDK TBC | ✅ | ✅ | ✅ |

## Recommendation

**Go with Option D** — start headless in Python, keep the LLM layer swappable, add the C# plugin only when the tool surface has stabilised and we've proven that "live UI grounding" is actually the bottleneck.

Rationale:
1. Gets the agentic loop + tool surface working fastest
2. Lets us iterate on prompt strategy and tool design without plugin-deployment friction
3. Leaves Option A on the table as a follow-up
4. Option C (SAF) can be added as one more tool inside Option D's harness — not mutually exclusive
