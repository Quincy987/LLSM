# 03 — SCIA Integration (v24.0)

*How we actually talk to SCIA Engineer. Version-specific — we are targeting **SCIA Engineer 24.0**.*

## Install layout (v24.0)

- Install root: `C:\Program Files\SCIA\Engineer24.0\`
- OpenAPI DLLs: `C:\Program Files\SCIA\Engineer24.0\OpenAPI_dll\`
- Registry entry (for programmatic discovery): under `HKLM\SOFTWARE\SCIA\Engineer\24.0` (verify exact key — the Jupyter example repo does registry-based discovery).

Any consuming project must either:
- Copy DLLs next to its output, **or**
- Hook `AppDomain.CurrentDomain.AssemblyResolve` to load from `OpenAPI_dll`, **or**
- Point `sys.path` / probing paths at the install folder (Python case).

## Supported languages on v24

| Language | Status on v24 |
|----------|---------------|
| **C# / .NET** | Fully supported. Official Visual Studio project template (2019 / 2022). |
| **Python via pythonnet** | Unofficial but works — community-maintained (see Jupyter example repo). Must pass exact .NET types (e.g. `System.Double`) to avoid silent constructor mismatch. |
| **VB.NET** | Officially only ≤21.1. |
| **Native Python (without .NET interop)** | Not supported — OpenAPI is .NET. |

## The three OpenAPI subsystems

### ADM — Analytical Data Model
Namespace: `SCIA.OpenAPI` / `SCIA.OpenAPI.Structure` / ADM internals.

Create / read / edit objects:
- **Materials** (concrete, steel grades, timber, etc.)
- **Cross-sections** (library picks + parametric)
- **Nodes** (cannot create orphan nodes — must be referenced by a member)
- **1D members** — beams, columns (with start/end nodes, cross-section, material, local axes)
- **2D members** — slabs, walls, shells (with boundary, thickness)
- **Supports** — point (on node), line (on edge), surface (on slab)
- **Load cases** and **load combinations**
- **Loads** — point force, point moment, line load (uniform / trapezoidal), surface load, temperature, etc.

> Note: the OpenAPI docs explicitly say experienced users can drop to ADM directly for things not yet exposed through the wrapper. So the *real* surface is broader than the public OpenAPI classes.

### EsaAutomation — SCIA application control
- `EsaAutomation.Start(...)` — launch SCIA in background (headless mode)
- Open project (.esa file) or project template
- Run calculation
- Import from XML (SAF) / export to XML
- Save / close / dispose

All methods must run on an **STAThread**. The `Environment` object must be disposed (prefer `using` block) — otherwise SCIA processes leak.

### RAPI — Results API
- Internal forces on 1D members (N, Vy, Vz, Mx, My, Mz per section)
- Deformations (ux, uy, uz, rotations)
- Support reactions
- Results per load case / combination / mesh element (for slabs)

### SAF / ESA_XML (adjacent, not strictly OpenAPI)
- SAF = Structural Analysis Format. XML-based, openBIM-aligned.
- Can be produced outside SCIA (even by hand, or by an LLM), then imported.
- `ESA_XML` routine runs the whole pipeline: import → analyse → export results.
- Useful for **batch-mode workflows** where no live SCIA session is needed.

## Hard rules for any integration

1. **Windows only.** SCIA is Windows-native and so are the DLLs.
2. **`STAThread` on any thread that touches OpenAPI.**
3. **Always dispose `Environment`.** Use `using` (C#) or `__del__` / context manager (Python).
4. **Single-threaded SCIA model.** Do not call OpenAPI from multiple threads in parallel — serialize.
5. **Type precision in pythonnet.** Wrap numeric args with the expected .NET type; `pythonnet` silently picks the non-parametric constructor if types don't match.
6. **License.** Must be resolved on the machine running the code. Some features may require specific license tiers — worth confirming with Nemetschek/Scia that our license covers OpenAPI/ADM/RAPI before building on it.

## Open questions to resolve before coding

- [ ] Does our current Scia v24 license include OpenAPI/ADM/EsaAutomation access? (Some license tiers gate it.)
- [ ] Does Scia v24 support **in-process plugins** the way Revit does? Is there a first-class add-on SDK, and does it allow us to embed a custom thread / HTTP listener?
- [ ] For the in-process path: what's the threading model — is there a `ExternalEvent`-equivalent for driving model mutations from a non-UI thread?
- [ ] Confirm the v24.0 registry key name for install-path discovery.
- [ ] Does `EsaAutomation` support running against an **already-open** interactive SCIA session, or only a background one? (This drives whether we can do "edit the model the engineer is looking at" vs "edit a file on disk".)

## What we can confidently do today (no license/SDK questions needed)

- Produce SAF XML from an LLM, import into SCIA manually → works.
- Run `EsaAutomation` in headless mode against a .esa file, assuming license allows → works.
- Use pythonnet + ADM DLLs to programmatically build models and read results → works (community-proven on v24+).

## Reference

- Install & DLL layout: search result confirmed `\Program Files\SCIA\Engineer$$.$\OpenAPI_dll\`
- Python + pythonnet: `scia-garage/open-api-adm-jupyter`
- C# reference app: `scia-garage/open-api-adm-dotnet`
- Official API class listing: https://help.scia.net/api/
