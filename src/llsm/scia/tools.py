"""LLM-callable tool functions.

Each tool is a thin wrapper that:
- runs on the main thread for ADM building (off-SCIA)
- marshals SCIA-touching calls onto the session's STA thread via session.submit()

All tools return a JSON-serializable dict for the LLM to consume.
"""
from __future__ import annotations
from typing import Any
import time

from .builder import ModelBuilder
from .session import SciaSession


class LlsmContext:
    """Holds the live SciaSession + the in-progress ModelBuilder.

    One context per LLM conversation. Tools mutate state on it.
    """
    def __init__(self) -> None:
        self.session: SciaSession | None = None
        self.builder: ModelBuilder | None = None
        self.project_opened: bool = False
        self.last_calc_ok: bool = False
        self.rapi: Any = None

    def ensure_session(self, visible: bool = False) -> SciaSession:
        if self.session is None:
            self.session = SciaSession(visible=visible)
            self.session.start()
        return self.session

    def ensure_builder(self, name: str = "LLSM_Model") -> ModelBuilder:
        if self.builder is None:
            self.builder = ModelBuilder(name=name)
        return self.builder

    def close(self) -> None:
        if self.builder is not None:
            self.builder.dispose()
            self.builder = None
        if self.session is not None:
            self.session.close()
            self.session = None


# ===================================================================
# Tools: model building (off-SCIA, pure builder operations)
# ===================================================================

def new_model(ctx: LlsmContext, name: str = "LLSM_Model") -> dict:
    """Start a fresh in-memory ADM model."""
    if ctx.builder is not None:
        ctx.builder.dispose()
    ctx.builder = ModelBuilder(name=name)
    return {"ok": True, "model_name": name, "items": ctx.builder.item_count()}


def add_material(ctx: LlsmContext, name: str, kind: str = "Steel") -> dict:
    ctx.ensure_builder()
    ctx.builder.add_material(name, kind=kind)
    return {"ok": True, "name": name, "kind": kind}


def add_rectangular_section(ctx: LlsmContext, name: str, material: str, width_mm: float, height_mm: float) -> dict:
    ctx.ensure_builder()
    ctx.builder.add_rectangular_section(name, material, width_mm, height_mm)
    return {"ok": True, "name": name, "material": material, "size_mm": [width_mm, height_mm]}


def add_node(ctx: LlsmContext, name: str, x_mm: float, y_mm: float, z_mm: float) -> dict:
    ctx.ensure_builder()
    ctx.builder.add_node(name, x_mm, y_mm, z_mm)
    return {"ok": True, "name": name, "xyz_mm": [x_mm, y_mm, z_mm]}


def add_beam(ctx: LlsmContext, name: str, node_a: str, node_b: str, cross_section: str) -> dict:
    ctx.ensure_builder()
    ctx.builder.add_beam(name, node_a, node_b, cross_section)
    return {"ok": True, "name": name, "between": [node_a, node_b], "section": cross_section}


def add_fixed_support(ctx: LlsmContext, name: str, node: str) -> dict:
    ctx.ensure_builder()
    ctx.builder.add_fixed_support(name, node)
    return {"ok": True, "name": name, "node": node, "type": "fixed"}


def add_pinned_support(ctx: LlsmContext, name: str, node: str) -> dict:
    ctx.ensure_builder()
    ctx.builder.add_pinned_support(name, node)
    return {"ok": True, "name": name, "node": node, "type": "pinned"}


def add_roller_support(ctx: LlsmContext, name: str, node: str, direction: str = "Z") -> dict:
    ctx.ensure_builder()
    ctx.builder.add_roller_support(name, node, direction=direction)
    return {"ok": True, "name": name, "node": node, "type": f"roller_{direction}"}


def add_load_group(ctx: LlsmContext, name: str, kind: str = "Permanent") -> dict:
    ctx.ensure_builder()
    ctx.builder.add_load_group(name, kind=kind)
    return {"ok": True, "name": name, "kind": kind}


def add_load_case(ctx: LlsmContext, name: str, group: str, kind: str = "Standard") -> dict:
    ctx.ensure_builder()
    ctx.builder.add_load_case(name, group, kind=kind)
    return {"ok": True, "name": name, "group": group, "kind": kind}


def add_line_load(ctx: LlsmContext, name: str, beam: str, load_case: str, magnitude_kn_per_m: float, direction: str = "Z") -> dict:
    ctx.ensure_builder()
    ctx.builder.add_line_load(name, beam, load_case, magnitude_kn_per_m, direction=direction)
    return {"ok": True, "name": name, "beam": beam, "load_case": load_case, "q_kNpm": magnitude_kn_per_m, "direction": direction}


def model_summary(ctx: LlsmContext) -> dict:
    if ctx.builder is None:
        return {"ok": True, "items": 0, "names": []}
    return {"ok": True, "items": ctx.builder.item_count(), "names": ctx.builder.names()}


# ===================================================================
# Tools: SCIA operations (STA thread via session.submit)
# ===================================================================

def launch_scia(ctx: LlsmContext, visible: bool = False) -> dict:
    ctx.ensure_session(visible=visible)
    return {"ok": True, "visible": visible}


def push_model(ctx: LlsmContext) -> dict:
    """Open an empty SCIA project and push the current ADM model into it."""
    if ctx.builder is None:
        return {"ok": False, "error": "no builder initialized — call new_model first"}
    sess = ctx.ensure_session()
    ctx.builder.validate()

    import SCIA.OpenAPI.Utils as SciaUtils
    from SciaTools.Kernel.ModelExchangerExtension.Models.Exchange import AdmChangeStatus
    model = ctx.builder.model
    temp = sess.temp_folder

    def _open_and_push():
        template = SciaUtils.SciaFileGetter().PrepareBasicEmptyFile(str(temp))
        proj = sess.env.OpenProject(str(template))
        sess.set_project(proj)

        errors = []
        for elem in model:
            r = proj.Model.CreateAdmObject(elem)
            if r.PartialAddResult.Status != AdmChangeStatus.Ok:
                errors.append({"name": elem.Name, "errors": [str(e) for e in r.PartialAddResult.Errors]})
        proj.Model.RefreshModel_ToSCIAEngineer()
        return errors

    errors = sess.submit(_open_and_push)
    ctx.project_opened = True
    return {"ok": not errors, "errors": errors, "items_pushed": ctx.builder.item_count()}


def run_analysis(ctx: LlsmContext) -> dict:
    """Generate FE mesh and run linear analysis."""
    if not ctx.project_opened:
        return {"ok": False, "error": "no project open — call push_model first"}
    sess = ctx.session

    def _mesh_and_calc():
        sess.project.CreateMesh()
        sess.project.RunCalculation()
        time.sleep(5)  # small grace period for result files
        rapi = sess.project.Model.InitializeResultsAPI()
        return rapi

    rapi = sess.submit(_mesh_and_calc)
    ctx.rapi = rapi
    ctx.last_calc_ok = rapi is not None
    return {"ok": ctx.last_calc_ok, "rapi_initialized": ctx.last_calc_ok}


def close_scia(ctx: LlsmContext) -> dict:
    ctx.close()
    return {"ok": True}


# ===================================================================
# Tools: result extraction (RAPI queries, run after run_analysis)
# ===================================================================

def _load_beam_result(ctx, beam_name: str, load_case: str, result_kind: str) -> dict:
    """Internal: build a ResultKey, call RAPI.LoadResult, extract data.

    result_kind: 'inner_forces' or 'deformation'.
    Returns dict with sections (positions in m) + per-magnitude value lists.
    """
    if not ctx.last_calc_ok or ctx.rapi is None:
        return {"ok": False, "error": "no analysis results available — call run_analysis first"}
    if ctx.builder is None:
        return {"ok": False, "error": "no model builder"}

    sess = ctx.session
    builder = ctx.builder

    # find ADM Guid for the load case (still on Python side; the ApiGuid wrapper is created via SCIA marshalling)
    try:
        case_obj = builder.get(load_case)
    except KeyError:
        return {"ok": False, "error": f"unknown load case {load_case!r}"}

    import SCIA.OpenAPI.Results as SciaResults
    from SCIA.OpenAPI import ApiGuid as SciaApiGuid
    import Results64Enums as RE

    if result_kind == "inner_forces":
        rt = RE.eResultType.eFemBeamInnerForces
    elif result_kind == "deformation":
        rt = RE.eResultType.eFemBeamDeformation
    else:
        return {"ok": False, "error": f"unknown result_kind {result_kind!r}; use 'inner_forces' or 'deformation'"}

    case_id_raw = case_obj.Id  # System.Guid value

    def _query():
        rk = SciaResults.ResultKey()
        rk.CaseType = RE.eDsElementType.eDsElementType_LoadCase
        # Assign raw System.Guid - the C# example assigns Guid directly via
        # implicit conversion to ApiGuid. Wrapping in SciaApiGuid in pythonnet
        # produces a type-name string in the lookup table.
        rk.CaseId = case_id_raw
        rk.EntityType = RE.eDsElementType.eDsElementType_Beam
        rk.EntityName = beam_name
        rk.Dimension = RE.eDimension.eDim_1D
        rk.ResultType = rt
        rk.CoordSystem = RE.eCoordSystem.eCoordSys_Local
        return ctx.rapi.LoadResult(rk)

    try:
        result = sess.submit(_query)
    except Exception as e:
        return {"ok": False, "error": f"LoadResult failed: {type(e).__name__}: {e}"}

    if result is None:
        return {
            "ok": False,
            "error": "LoadResult returned None — common causes: beam name mismatch (check model_summary), wrong load_case, or no result of this type was generated",
            "tried": {"beam": beam_name, "case": load_case, "kind": result_kind},
        }

    def _extract():
        sections_count = result.GetSectionCount()
        magnitudes_count = result.GetMagnitudesCount()
        positions = [result.GetSectionPosX(i) for i in range(sections_count)]
        magnitudes = {}
        for m in range(magnitudes_count):
            name = result.GetMagnitudeName(m)
            magnitudes[name] = [result.GetValue(m, i) for i in range(sections_count)]
        return positions, magnitudes

    try:
        positions, magnitudes = sess.submit(_extract)
    except Exception as e:
        return {"ok": False, "error": f"result extraction failed: {type(e).__name__}: {e}"}

    summary = {}
    for name, vals in magnitudes.items():
        if not vals:
            continue
        abs_max = max(abs(v) for v in vals)
        summary[name] = {"min": min(vals), "max": max(vals), "abs_max": abs_max}

    return {
        "ok": True,
        "beam": beam_name,
        "load_case": load_case,
        "kind": result_kind,
        "sections": len(positions),
        "positions_m": positions,
        "magnitudes": magnitudes,
        "summary_per_magnitude": summary,
    }


def get_beam_inner_forces(ctx: LlsmContext, beam: str, load_case: str) -> dict:
    """Read beam internal forces (N, Vy, Vz, Mx, My, Mz per section) for a load case.

    Returns positions in metres + per-magnitude value lists, plus min/max/abs_max summary.
    Forces in N, moments in N·m. Convert to kN/kNm by dividing by 1000.
    """
    return _load_beam_result(ctx, beam_name=beam, load_case=load_case, result_kind="inner_forces")


def get_beam_deflection(ctx: LlsmContext, beam: str, load_case: str) -> dict:
    """Read beam deformation (Ux, Uy, Uz, Fix, Fiy, Fiz per section) for a load case.

    Translations Ux/Uy/Uz in metres. Convert to mm by multiplying by 1000.
    """
    return _load_beam_result(ctx, beam_name=beam, load_case=load_case, result_kind="deformation")


