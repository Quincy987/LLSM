# 01 — Research Findings

*Synthesis of web research conducted 2026-04-23. See individual source links at the bottom.*

## TL;DR

- SCIA Engineer ships with **OpenAPI**, an official .NET-based automation platform. It's production-grade, documented, and comes with example repos on GitHub under [`scia-garage`](https://github.com/scia-garage).
- **C# is the only fully-supported language** from v22.0 onward. Python/VB.NET are officially supported only up to **v21.1**, but Python via `pythonnet` still works against later versions (community-maintained, proven in Jupyter examples).
- OpenAPI is split into three subsystems — ADM (model), EsaAutomation (app control), RAPI (results) — which together cover read/create/edit/run/inspect.
- SCIA must be **installed locally with a valid license** to use OpenAPI.
- For the LLM layer, the **VibeModel pattern maps cleanly**: replace the Revit add-in with a Scia add-on (or out-of-process driver), keep the HTTP bridge + `IChatBackend` abstraction.

## What SCIA's OpenAPI actually does

### ADM (Analytical Data Model)
Create / read / modify structural model objects:
- Materials, cross-sections
- Nodes, beams (1D members), slabs (2D members)
- Supports (fixed, hinged, etc.)
- Load cases, load combinations
- Point loads, line loads (and other load types)
- The docs note experienced users "can use ADM directly" for functionality not yet wrapped by OpenAPI — so the ADM surface is broader than the OpenAPI wrappers.

### EsaAutomation
Remote-control SCIA itself:
- Start SCIA Engineer in background mode
- Open project / project template
- Run calculation (linear, and higher analyses depending on license)
- Import / export XML (SAF)
- Save / close project

### RAPI (Results API)
Read analysis results:
- Internal forces on 1D members
- Deformations / deflections (incl. per-mesh-element for slabs)
- Reactions at supports

## Key constraints & gotchas

1. **C#/.NET is first-class.** From v22.0 the Scia team stated their focus is maintaining only the C# workflow.
2. **Python via pythonnet works.** The `scia-garage/open-api-adm-jupyter` repo demonstrates: load DLLs from the install folder via registry discovery, wrap C# objects in Python. Gotcha: pythonnet will silently fall back to a non-parametric constructor if argument types don't match exactly — must pass `System.Double`/etc. explicitly.
3. **Runtime requirements:**
   - Windows only
   - SCIA Engineer installed locally (we're on **v24.0** — install path `C:\Program Files\SCIA\Engineer24.0\`)
   - Valid SCIA license (check whether ADM/RAPI/EsaAutomation require a specific license tier)
   - `STAThread` required for methods calling OpenAPI
   - Must dispose the `Environment` object (prefer `using` block)
4. **DLL location:** `C:\Program Files\SCIA\Engineer24.0\OpenAPI_dll\` — not next to the main .exe. Any external project must resolve assemblies from this path (either via `AppDomain.AssemblyResolve` hook or copying DLLs).
5. **SAF (Structural Analysis Format) XML** is a valid alternative input/output — Scia can import SAF via the `ESA_XML` routine. Useful for batch-style "LLM produces a complete model" workflows.

## Community resources found

- `scia-garage/open-api-adm-dotnet` — C# reference app
- `scia-garage/open-api-adm-jupyter` — Python + Jupyter notebook example
- `scia-garage/SciaOpenAPI_example_CSharp_simple_structure` — minimal C# structure creation
- `scia-garage/SciaOpenAPI_example_VB_simple_structure` — VB.NET (≤21.1)
- `scia-garage/OpenAPIComplexExampleWithADMCSharp` — larger C# sample
- SCIA OpenAPI reference: `help.scia.net/api/` (IntelliSense-grade class docs)
- VIKTOR platform — third party; already built a Python-based Scia integration for cloud parametric design

## What this means for LLSM

- ✅ The automation substrate is solid — we don't need to reverse-engineer anything.
- ✅ We can prototype fast in Python via pythonnet even though it's "unofficial" past v22.
- ⚠️ A production deployment on v24+ should probably use C# for longevity.
- ⚠️ License check required — verify our Scia license includes OpenAPI access before investing.
- 🎯 The LLM orchestration layer (prompt strategy, tool-calling loop, grounding) is 100% VibeModel-transferable. Only the "tool implementations" are Scia-specific.

## Sources

- [SCIA OpenAPI v24.0 help](https://help.scia.net/24.0/en/special_applications/openapi/openapi.htm)
- [SCIA OpenAPI v22.0 help](https://help.scia.net/22.0/en/special_applications/openapi/openapi.htm)
- [SCIA OpenAPI class reference](https://help.scia.net/api/)
- [ADM API with Python — FAQ](https://www.scia.net/en/support/faq/scia-engineer/other-topics/adm-api-python)
- [ADM API with C# — FAQ](https://www.scia.net/en/support/faq/scia-engineer/other-topics/adm-api-c)
- [scia-garage/open-api-adm-jupyter](https://github.com/scia-garage/open-api-adm-jupyter)
- [scia-garage/open-api-adm-dotnet](https://github.com/scia-garage/open-api-adm-dotnet)
- [VIKTOR + SCIA parametric models](https://www.viktor.ai/blog/10/parametric-design-python-SCIA)
