"""LLM-callable tool functions.

Each tool is a thin wrapper that:
- runs on the main thread for ADM building (off-SCIA)
- marshals SCIA-touching calls onto the session's STA thread via session.submit()

All tools return a JSON-serializable dict for the LLM to consume.
"""
from __future__ import annotations
from typing import Any
import time

from .builder import DuplicateNameError, ModelBuilder
from .autoclick import SciaDialogPending
from .session import SciaSession


class LlsmContext:
    """Holds the live SciaSession + the in-progress ModelBuilder.

    One context per LLM conversation. Tools mutate state on it.

    mesh_dirty tracks whether the FE mesh is stale relative to the ADM model:
    set after every successful push or edit; cleared after run_analysis remeshes.
    """
    def __init__(self) -> None:
        self.session: SciaSession | None = None
        self.builder: ModelBuilder | None = None
        self.project_opened: bool = False
        self.last_calc_ok: bool = False
        self.calc_checked: bool = False  # run_analysis looked for results (found or not)
        self.rapi: Any = None
        self.mesh_dirty: bool = False
        self.model_name: str | None = None

    def ensure_session(self, visible: bool = False) -> SciaSession:
        if self.session is None:
            self.session = SciaSession(visible=visible)
        try:
            self.session.start()
        except SciaDialogPending:
            raise  # keep the launch: the user answers, the caller retries
        except Exception:
            failed, self.session = self.session, None
            failed.close()
            raise
        return self.session

    def ensure_builder(self, name: str = "LLSM_Model") -> ModelBuilder:
        if self.builder is None:
            self.builder = ModelBuilder(name=name)
            self.model_name = name
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


def _refused(name: str, exc: ValueError) -> dict:
    """A refused add (duplicate name, unknown kind, a case ADM will not take): nothing was added."""
    out = {"ok": False, "error": str(exc), "name": name}
    if isinstance(exc, DuplicateNameError):
        out["duplicate"] = True
    return out


def new_model(ctx: LlsmContext, name: str = "LLSM_Model") -> dict:
    """Start a fresh in-memory ADM model."""
    if ctx.builder is not None:
        ctx.builder.dispose()
    ctx.builder = ModelBuilder(name=name)
    ctx.model_name = name
    return {"ok": True, "model_name": name, "items": ctx.builder.item_count()}


def add_material(ctx: LlsmContext, name: str, kind: str = "Steel") -> dict:
    ctx.ensure_builder()
    try:
        ctx.builder.add_material(name, kind=kind)
    except ValueError as exc:
        return _refused(name, exc)
    return {"ok": True, "name": name, "kind": kind}


def add_rectangular_section(ctx: LlsmContext, name: str, material: str, width_mm: float, height_mm: float) -> dict:
    ctx.ensure_builder()
    try:
        ctx.builder.add_rectangular_section(name, material, width_mm, height_mm)
    except ValueError as exc:
        return _refused(name, exc)
    return {"ok": True, "name": name, "material": material, "size_mm": [width_mm, height_mm]}


def add_node(ctx: LlsmContext, name: str, x_mm: float, y_mm: float, z_mm: float) -> dict:
    ctx.ensure_builder()
    try:
        ctx.builder.add_node(name, x_mm, y_mm, z_mm)
    except ValueError as exc:
        return _refused(name, exc)
    return {"ok": True, "name": name, "xyz_mm": [x_mm, y_mm, z_mm]}


def add_beam(ctx: LlsmContext, name: str, node_a: str, node_b: str, cross_section: str) -> dict:
    ctx.ensure_builder()
    try:
        ctx.builder.add_beam(name, node_a, node_b, cross_section)
    except ValueError as exc:
        return _refused(name, exc)
    return {"ok": True, "name": name, "between": [node_a, node_b], "section": cross_section}


def add_fixed_support(ctx: LlsmContext, name: str, node: str) -> dict:
    ctx.ensure_builder()
    try:
        ctx.builder.add_fixed_support(name, node)
    except ValueError as exc:
        return _refused(name, exc)
    return {"ok": True, "name": name, "node": node, "type": "fixed"}


def add_pinned_support(ctx: LlsmContext, name: str, node: str) -> dict:
    ctx.ensure_builder()
    try:
        ctx.builder.add_pinned_support(name, node)
    except ValueError as exc:
        return _refused(name, exc)
    return {"ok": True, "name": name, "node": node, "type": "pinned"}


def add_roller_support(ctx: LlsmContext, name: str, node: str, direction: str = "Z") -> dict:
    ctx.ensure_builder()
    try:
        ctx.builder.add_roller_support(name, node, direction=direction)
    except ValueError as exc:
        return _refused(name, exc)
    return {"ok": True, "name": name, "node": node, "type": f"roller_{direction}"}


def add_load_group(ctx: LlsmContext, name: str, kind: str = "Permanent") -> dict:
    ctx.ensure_builder()
    try:
        ctx.builder.add_load_group(name, kind=kind)
    except ValueError as exc:
        return _refused(name, exc)
    return {"ok": True, "name": name, "kind": kind}


def add_load_case(ctx: LlsmContext, name: str, group: str, kind: str = "Standard",
                  action_type: str | None = None, duration: str | None = None) -> dict:
    """action_type follows the group (Permanent/Variable/...) unless given; duration for variable cases."""
    ctx.ensure_builder()
    try:
        case = ctx.builder.add_load_case(name, group, kind=kind, action_type=action_type, duration=duration)
    except ValueError as exc:
        return _refused(name, exc)
    # Read back from the stored case, so the result says what landed, not what was asked for.
    out = {"ok": True, "name": name, "group": group, "kind": str(getattr(case, "LoadType", kind)),
           "action_type": str(case.ActionType)}
    if out["action_type"] == "Variable":
        out["duration"] = str(case.Duration)
    return out


def add_line_load(ctx: LlsmContext, name: str, beam: str, load_case: str, magnitude_kn_per_m: float, direction: str = "Z") -> dict:
    ctx.ensure_builder()
    try:
        ctx.builder.add_line_load(name, beam, load_case, magnitude_kn_per_m, direction=direction)
    except ValueError as exc:
        return _refused(name, exc)
    return {"ok": True, "name": name, "beam": beam, "load_case": load_case, "q_kNpm": magnitude_kn_per_m, "direction": direction}


def model_summary(ctx: LlsmContext) -> dict:
    if ctx.builder is None:
        return {"ok": True, "items": 0, "names": []}
    return {"ok": True, "items": ctx.builder.item_count(), "names": ctx.builder.names()}


# ===================================================================
# Tools: SCIA operations (STA thread via session.submit)
# ===================================================================

def launch_scia(ctx: LlsmContext, visible: bool = False) -> dict:
    session = ctx.ensure_session(visible=visible)
    result = {"ok": True, "visible": visible}
    if session.foreign_at_start:
        result["warning"] = "Another SCIA is running on this computer; LLSM leaves it alone."
    return result


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

    # 5 minutes is generous for big models; the failure mode we care about
    # (AdmToAdm eErrFail) hangs forever, so any finite cap recovers cleanly.
    try:
        errors = sess.submit(_open_and_push, timeout_s=300)
    except TimeoutError as e:
        return {"ok": False, "error": str(e), "wedged": True}
    ctx.project_opened = True
    ctx.mesh_dirty = True
    ctx.calc_checked, ctx.last_calc_ok, ctx.rapi = False, False, None  # a new project has no results yet
    return {"ok": not errors, "errors": errors, "items_pushed": ctx.builder.item_count(),
            "mesh_dirty": ctx.mesh_dirty}


# Said by run_analysis and both result tools when SCIA made no results. Measured 2026-11-23 on SCIA 24.0.3015.64
# (docs/verify/calc-run.md): the OpenAPI's RunCalculation sends SCIA's "hidden linear calculation", which
# SCIA's own configuration allows for SCIA's internal use only; it returns at once and calculates nothing.
NOT_CALCULATED = (
    "The model is in SCIA with a mesh, but SCIA did not calculate it, so there are no results to read. "
    "The calculation command LLSM can send is not allowed by this SCIA licence. "
    "Calculate it in SCIA itself to see the results there; LLSM cannot read them back yet."
)
RESULTS_WAIT_S = 20.0  # how long run_analysis looks for results after the calculation command


def _first_beam_and_case(ctx: LlsmContext) -> tuple[str | None, str | None]:
    snap = ctx.builder.snapshot() if ctx.builder is not None else {}
    beams, cases = snap.get("beams") or [], snap.get("load_cases") or []
    return (beams[0]["name"] if beams else None), (cases[0]["name"] if cases else None)


def _results_exist(ctx: LlsmContext, rapi: Any, beam: str, case: str) -> bool:
    """True when SCIA returns inner forces for ``beam`` in ``case``: the only proof that it calculated."""
    if rapi is None:
        return False
    key = _result_key(ctx, beam, case, "inner_forces")
    result = ctx.session.submit(lambda: rapi.LoadResult(key), timeout_s=120)
    return result is not None


def run_analysis(ctx: LlsmContext) -> dict:
    """Generate the FE mesh (if dirty), send SCIA's linear calculation, and check that results exist.

    ok is True only when SCIA returns inner forces for the first beam and load case: the calculation
    command and the results API both report success also when SCIA calculated nothing. Skips
    re-meshing when mesh_dirty is False. Edit tools always set mesh_dirty=True.
    """
    if not ctx.project_opened:
        return {"ok": False, "error": "no project open — call push_model first"}
    sess = ctx.session
    needs_mesh = ctx.mesh_dirty
    beam, case = _first_beam_and_case(ctx)
    ctx.rapi, ctx.last_calc_ok = None, False

    def _mesh_and_calc():
        if needs_mesh:
            sess.project.CreateMesh()
        sess.project.RunCalculation()

    try:
        sess.submit(_mesh_and_calc, timeout_s=600)
    except TimeoutError as e:
        return {"ok": False, "error": str(e), "wedged": True}
    ctx.mesh_dirty = False  # the mesh is fresh whether or not SCIA calculated
    ctx.calc_checked = True
    if beam is None or case is None:
        return {"ok": False, "calculated": False, "remeshed": needs_mesh, "mesh_dirty": ctx.mesh_dirty,
                "error": "The model has no beam or no load case, so there is nothing to check results on."}

    deadline = time.time() + RESULTS_WAIT_S
    rapi = None
    while True:
        try:
            rapi = sess.submit(lambda: sess.project.Model.InitializeResultsAPI(), timeout_s=120)
            if _results_exist(ctx, rapi, beam, case):
                ctx.rapi, ctx.last_calc_ok = rapi, True
                return {"ok": True, "calculated": True, "remeshed": needs_mesh, "mesh_dirty": ctx.mesh_dirty,
                        "checked": {"beam": beam, "load_case": case}}
        except TimeoutError as e:
            return {"ok": False, "error": str(e), "wedged": True}
        if time.time() >= deadline:
            break
        time.sleep(2.0)
    return {"ok": False, "calculated": False, "remeshed": needs_mesh, "mesh_dirty": ctx.mesh_dirty,
            "error": NOT_CALCULATED, "checked": {"beam": beam, "load_case": case}}


def close_scia(ctx: LlsmContext) -> dict:
    ctx.close()
    return {"ok": True}


# ===================================================================
# Tools: model edits (in-memory ADM mutation + auto re-sync to SCIA)
# ===================================================================

def _propagate_edit(ctx: LlsmContext) -> str | None:
    """Auto-push the in-memory ADM change to SCIA's data server.

    Returns None on success, or a short error string the caller should attach
    as `sync_warning` in the tool response. We never raise: the ADM mutation
    has already happened and the LLM needs a structured result, not an
    exception bubbling out of a successful edit path.

    Sets mesh_dirty unconditionally so the next run_analysis re-meshes —
    even when the SCIA-side sync fails, the ADM is now ahead of the mesh.

    Skips the SCIA sync when no project is open yet: edits live in the
    builder and the next push_model will carry them across.

    Diagnosed bug (q-388): bare RefreshModel_ToSCIAEngineer() does NOT push
    in-place mutations of existing ADM objects to SCIA's data store. It only
    appears to flush newly-created objects (the push_model path queues them
    via CreateAdmObject before refreshing). For updates, we use
    TrySynchronizeModel(adm_model) which does a diff-aware full-model sync
    and can propagate changes to existing items. RefreshModel is still
    called as a belt-and-suspenders flush after.
    """
    ctx.mesh_dirty = True
    if not ctx.project_opened or ctx.session is None:
        return None
    sess = ctx.session
    proj = sess.project
    if proj is None:
        return None
    if ctx.builder is None:
        return None
    adm_model = ctx.builder.model
    def _sync():
        # TrySynchronizeModel does a diff-and-sync of the WHOLE model to
        # SCIA's data store, including in-place mutations. Returns Boolean
        # indicating success.
        ok = proj.Model.TrySynchronizeModel(adm_model)
        # Belt-and-suspenders: flush any pending changes to the UI as well.
        proj.Model.RefreshModel_ToSCIAEngineer()
        return ok
    try:
        ok = sess.submit(_sync)
    except Exception as e:
        return f"ADM updated but SCIA sync failed: {type(e).__name__}: {e}"
    if ok is False:
        return "TrySynchronizeModel returned False — SCIA may not have applied the edit"
    return None


def _attach_warning(result: dict, warn: str | None) -> dict:
    if warn:
        result["sync_warning"] = warn
    return result


def update_node(ctx: LlsmContext, name: str, x_mm: float, y_mm: float, z_mm: float) -> dict:
    """Move an existing node to new coordinates.

    The builder rolls back the X/Y/Z assignment if the .NET property write
    throws (rare for in-memory ADM); the resulting model is not validated
    here, so structural problems surface later when SCIA meshes the model.
    On failure we return the list of beams that reference this node, so the
    LLM has context about what's coupled to the move (it may want to delete
    or re-route them).
    """
    if ctx.builder is None:
        return {"ok": False, "error": "no builder — call new_model first"}
    try:
        ctx.builder.update_node(name, x_mm, y_mm, z_mm)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        deps: list[str] = []
        try:
            deps = ctx.builder.find_dependents(name)
        except Exception:
            pass
        return {"ok": False, "error": f"update rejected: {type(e).__name__}: {e}",
                "rolled_back": True, "dependent_beams": deps}
    warn = _propagate_edit(ctx)
    return _attach_warning(
        {"ok": True, "name": name, "xyz_mm": [x_mm, y_mm, z_mm], "mesh_dirty": True},
        warn,
    )


def refresh_from_scia(ctx: LlsmContext) -> dict:
    """Trigger SCIA's internal engine->store refresh. Does NOT read ADM back.

    IMPORTANT (verified, phase0/spike_open_readback.py against SCIA v24):
    the OpenAPI is push-only. RefreshModel_FromSCIAEngineer() returns void and
    there is no way to read SCIA's model back into the in-memory ADM — the
    Structure exposes no AnalysisModel, and TrySynchronizeModel does not fill a
    passed-in model either. So this call does NOT update the builder; an
    immediately-following get_node_position / model_summary still reflects the
    in-memory ADM (what we pushed), not independently re-read SCIA truth.

    Kept only as a low-level pass-through to SCIA's refresh routine. A model the
    OpenAPI did not itself build cannot be inspected or edited through this API;
    see docs/verify/phase0-route.md for the file route.
    """
    if ctx.session is None or ctx.session.project is None:
        return {"ok": False, "error": "no project open"}
    sess = ctx.session
    proj = sess.project
    try:
        sess.submit(lambda: proj.Model.RefreshModel_FromSCIAEngineer())
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "info": "SCIA engine->store refresh triggered; "
            "in-memory ADM is unchanged (OpenAPI read-back is not supported)"}


def get_node_position(ctx: LlsmContext, name: str) -> dict:
    """Read the (x, y, z) position of a node from the in-memory ADM model.

    Useful for the LLM to verify its own edits ('did update_node actually
    land?') and for human/diagnostic introspection without leaving SCIA's
    UI as the only source of truth.
    """
    if ctx.builder is None:
        return {"ok": False, "error": "no builder - call new_model first"}
    try:
        x, y, z = ctx.builder.get_node_position(name)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "name": name, "xyz_mm": [x, y, z]}


def delete_object(ctx: LlsmContext, name: str, cascade: bool = False) -> dict:
    """Delete an ADM object by name.

    Without cascade, refuses if dependents exist and returns the blocker list.
    With cascade=True, recursively removes dependents first.
    """
    if ctx.builder is None:
        return {"ok": False, "error": "no builder — call new_model first"}
    try:
        deleted = ctx.builder.delete_object(name, cascade=cascade)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    except ValueError as e:
        # The builder packs blocker names into args[0] when dependents exist
        blockers = e.args[0] if e.args and isinstance(e.args[0], list) else []
        return {"ok": False, "error": "dependents exist — pass cascade=True or delete them first",
                "blockers": blockers}
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}
    warn = _propagate_edit(ctx)
    return _attach_warning({"ok": True, "deleted": deleted, "mesh_dirty": True}, warn)


def set_line_load_magnitude(ctx: LlsmContext, name: str, magnitude_kn_per_m: float) -> dict:
    """Adjust the magnitude of an existing line load (kN/m)."""
    if ctx.builder is None:
        return {"ok": False, "error": "no builder — call new_model first"}
    try:
        ctx.builder.set_line_load_magnitude(name, magnitude_kn_per_m)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    warn = _propagate_edit(ctx)
    return _attach_warning(
        {"ok": True, "name": name, "q_kNpm": magnitude_kn_per_m, "mesh_dirty": True},
        warn,
    )


def set_member_cross_section(ctx: LlsmContext, name: str, cross_section: str) -> dict:
    """Swap the cross-section of an existing curve member."""
    if ctx.builder is None:
        return {"ok": False, "error": "no builder — call new_model first"}
    try:
        ctx.builder.set_member_cross_section(name, cross_section)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    warn = _propagate_edit(ctx)
    return _attach_warning(
        {"ok": True, "name": name, "section": cross_section, "mesh_dirty": True},
        warn,
    )


def set_member_endpoints(ctx: LlsmContext, name: str, node_a: str, node_b: str) -> dict:
    """Re-route a beam between two existing nodes. Try-or-rollback."""
    if ctx.builder is None:
        return {"ok": False, "error": "no builder — call new_model first"}
    try:
        ctx.builder.set_member_endpoints(name, node_a, node_b)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"endpoint reroute rejected: {type(e).__name__}: {e}",
                "rolled_back": True}
    warn = _propagate_edit(ctx)
    return _attach_warning(
        {"ok": True, "name": name, "between": [node_a, node_b], "mesh_dirty": True},
        warn,
    )


# ===================================================================
# Tools: result extraction (RAPI queries, run after run_analysis)
# ===================================================================

RESULT_KINDS = ("inner_forces", "deformation")


def _result_key(ctx, beam_name: str, load_case: str, result_kind: str) -> Any:
    """SCIA's ResultKey for one beam, one load case, one result kind (local axes)."""
    import SCIA.OpenAPI.Results as SciaResults
    from SCIA.OpenAPI import ApiGuid as SciaApiGuid
    import Results64Enums as RE

    rt = {"inner_forces": RE.eResultType.eFemBeamInnerForces,
          "deformation": RE.eResultType.eFemBeamDeformation}[result_kind]
    rk = SciaResults.ResultKey()
    rk.CaseType = RE.eDsElementType.eDsElementType_LoadCase
    # pythonnet does not apply the implicit Guid -> ApiGuid conversion on assignment, so wrap it.
    # SCIA turns it back into the Guid (op_Implicit) to find its own case number; that lookup works
    # (checked 2026-11-23, docs/verify/calc-run.md). The docs/06 "ApiGuid.ToString" cause was wrong.
    rk.CaseId = SciaApiGuid(ctx.builder.get(load_case).Id)
    rk.EntityType = RE.eDsElementType.eDsElementType_Beam
    rk.EntityName = beam_name
    rk.Dimension = RE.eDimension.eDim_1D
    rk.ResultType = rt
    rk.CoordSystem = RE.eCoordSystem.eCoordSys_Local
    return rk


def _load_beam_result(ctx, beam_name: str, load_case: str, result_kind: str) -> dict:
    """Internal: build a ResultKey, call RAPI.LoadResult, extract data.

    result_kind: 'inner_forces' or 'deformation'.
    Returns dict with sections (positions in m) + per-magnitude value lists.
    """
    if result_kind not in RESULT_KINDS:
        return {"ok": False, "error": f"unknown result_kind {result_kind!r}; use 'inner_forces' or 'deformation'"}
    if not ctx.last_calc_ok or ctx.rapi is None:
        if ctx.project_opened and ctx.calc_checked:  # run_analysis looked and SCIA had no results
            return {"ok": False, "calculated": False, "error": NOT_CALCULATED}
        return {"ok": False, "error": "no analysis results available — call run_analysis first"}
    if ctx.builder is None:
        return {"ok": False, "error": "no model builder"}

    sess = ctx.session
    try:
        key = _result_key(ctx, beam_name, load_case, result_kind)
    except KeyError:
        return {"ok": False, "error": f"unknown load case {load_case!r}"}

    try:
        result = sess.submit(lambda: ctx.rapi.LoadResult(key))
    except Exception as e:
        return {"ok": False, "error": f"LoadResult failed: {type(e).__name__}: {e}"}

    if result is None:
        # run_analysis saw results for its check beam and case, so the model was calculated
        return {
            "ok": False,
            "error": f"SCIA has no {result_kind.replace('_', ' ')} for beam {beam_name!r} in load case {load_case!r}; "
                     "the model was calculated, so check the names with model_summary.",
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


