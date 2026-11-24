"""LLM-callable tool functions.

Each tool is a thin wrapper that:
- runs on the main thread for ADM building (off-SCIA)
- marshals SCIA-touching calls onto the session's STA thread via session.submit()

All tools return a JSON-serializable dict for the LLM to consume.
"""
from __future__ import annotations
from pathlib import Path
import shutil
import tempfile
import threading
import time

from . import calc, start_model
from .builder import DuplicateNameError, ModelBuilder, ReservedNameError
from .autoclick import OLD_SECTIONS_QUESTION, ScopedAnswer, SciaDialogPending
from .session import SciaSession
from ..jobs import MAX_WAIT_S, JobBoard

# One calculation at a time: a running one is polled by calling run_analysis again.
calc_jobs = JobBoard(
    "calc",
    busy_message="SCIA is already calculating (job {id}); call run_analysis again to wait for it.",
    running_message=("SCIA is still calculating a copy of the model. Call run_analysis again to wait for it "
                     "(each call waits up to 4 minutes); an edit meanwhile makes this result void."),
    failed_message="The calculation failed: {exc}",
)


class LlsmContext:
    """Holds the live SciaSession + the in-progress ModelBuilder.

    One context per LLM conversation. Tools mutate state on it.

    mesh_dirty tracks whether the FE mesh is stale relative to the ADM model:
    set after every successful push or edit; cleared after run_analysis remeshes.
    model_version goes up on every push and edit; results carry the version they were calculated
    for, so a calculation that finishes after an edit is thrown away, never shown.
    """
    def __init__(self) -> None:
        self.session: SciaSession | None = None
        self.builder: ModelBuilder | None = None
        self.project_opened: bool = False
        self.last_calc_ok: bool = False
        self.calc_checked: bool = False  # run_analysis has run for this project (found results or not)
        self.mesh_dirty: bool = False
        self.model_name: str | None = None
        self.model_version: int = 0
        self.results: dict | None = None  # {"version": int, "cases": {case: {member: {"forces", "deformations"}}}}
        self.start_copy: Path | None = None  # the start-model copy the open project is; None: SCIA's empty file
        self.pushed_items: int = 0
        self.lock = threading.Lock()  # results are stored from the calculation's thread

    def model_changed(self) -> None:
        """A push or an edit: earlier results no longer describe the model."""
        with self.lock:
            self.model_version += 1
            self.results = None
            self.last_calc_ok = False

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
            self.builder = _new_builder(name)
            self.model_name = name
        return self.builder

    def close(self) -> None:
        self.model_changed()  # a calculation still running must not store into a closed context
        self.project_opened, self.calc_checked = False, False
        if self.builder is not None:
            self.builder.dispose()
            self.builder = None
        if self.session is not None:
            self.session.close()
            self.session = None
        if self.start_copy is not None:
            self.start_copy.unlink(missing_ok=True)
            self.start_copy = None


def _new_builder(name: str) -> ModelBuilder:
    """A builder that refuses the names of the start model the model will be pushed into."""
    builder = ModelBuilder(name=name)
    found = start_model.find()
    if found is not None:
        builder.reserved = start_model.owned_names(found)
    return builder


# ===================================================================
# Tools: model building (off-SCIA, pure builder operations)
# ===================================================================


def _refused(name: str, exc: ValueError) -> dict:
    """A refused add (duplicate name, unknown kind, a case ADM will not take): nothing was added."""
    out = {"ok": False, "error": str(exc), "name": name}
    if isinstance(exc, ReservedNameError):
        out["reserved"] = True
    elif isinstance(exc, DuplicateNameError):
        out["duplicate"] = True
    return out


def new_model(ctx: LlsmContext, name: str = "LLSM_Model") -> dict:
    """Start a fresh in-memory ADM model."""
    if ctx.builder is not None:
        ctx.builder.dispose()
    ctx.builder = _new_builder(name)
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


def _members_and_cases(ctx: LlsmContext) -> tuple[list[str], list[str]]:
    snap = ctx.builder.snapshot() if ctx.builder is not None else {}
    return [b["name"] for b in snap.get("beams") or []], [c["name"] for c in snap.get("load_cases") or []]


def push_model(ctx: LlsmContext) -> dict:
    """Open a copy of the start model (or SCIA's empty file without one) and push the ADM model into it."""
    if ctx.builder is None:
        return {"ok": False, "error": "no builder initialized — call new_model first"}
    sess = ctx.ensure_session()
    ctx.builder.validate()

    import SCIA.OpenAPI.Utils as SciaUtils
    from SCIA.OpenAPI import SaveMode
    from SciaTools.Kernel.ModelExchangerExtension.Models.Exchange import AdmChangeStatus
    model = ctx.builder.model
    temp = sess.temp_folder
    start = start_model.find()

    def _open_and_push():
        if sess.project is not None:  # one project at a time; it also frees the previous start-model copy
            try:
                sess.project.CloseProject(SaveMode.SaveChangesNo)
            except Exception:
                pass
        if start is not None:
            template = temp / f"llsm-start-{time.time_ns()}.esa"
            shutil.copyfile(start, template)
        else:
            template = Path(SciaUtils.SciaFileGetter().PrepareBasicEmptyFile(str(temp)))
        proj = sess.env.OpenProject(str(template))
        sess.set_project(proj)

        errors = []
        for elem in model:
            r = proj.Model.CreateAdmObject(elem)
            if r.PartialAddResult.Status != AdmChangeStatus.Ok:
                errors.append({"name": elem.Name, "errors": [str(e) for e in r.PartialAddResult.Errors]})
        proj.Model.RefreshModel_ToSCIAEngineer()
        return template, errors

    # 5 minutes is generous for big models; the failure mode we care about
    # (AdmToAdm eErrFail) hangs forever, so any finite cap recovers cleanly.
    try:
        with ScopedAnswer(lambda: set(sess.own_pids), OLD_SECTIONS_QUESTION):
            template, errors = sess.submit(_open_and_push, timeout_s=300)
    except TimeoutError as e:
        return {"ok": False, "error": str(e), "wedged": True}
    ctx.project_opened = True
    ctx.mesh_dirty = True
    ctx.calc_checked = False
    ctx.start_copy = template if start is not None else None
    _remove_old_start_copies(temp, keep=ctx.start_copy)
    ctx.pushed_items = ctx.builder.item_count()
    ctx.model_changed()
    members, cases = _members_and_cases(ctx)
    out = {"ok": not errors, "errors": errors, "items_pushed": ctx.pushed_items, "mesh_dirty": ctx.mesh_dirty,
           "start_model": start is not None}
    if start is not None:
        out["calc_estimate_s"] = calc.estimate_seconds(len(members), len(cases))
    else:
        out["warning"] = NO_START_MODEL
    return out


def _remove_old_start_copies(folder: Path, keep: Path | None) -> None:
    """Start-model copies (36 MB each) of earlier pushes; one SCIA still has open is locked and stays."""
    for old in Path(folder).glob("llsm-start-*.esa"):
        if keep is None or old.resolve() != Path(keep).resolve():
            try:
                old.unlink()
            except OSError:
                pass


# Said by run_analysis and both result tools when the project is SCIA's empty file. Measured 2026-11-23 on
# SCIA 24.0.3015.64 (docs/verify/calc-run.md): the OpenAPI's RunCalculation sends SCIA's "hidden linear
# calculation", which SCIA's configuration allows for SCIA's internal use only; it calculates nothing.
NOT_CALCULATED = (
    "The model is in SCIA with a mesh, but SCIA made no results, so there is nothing to read. "
    "The calculation command LLSM can send is reserved for SCIA's own internal use and did nothing "
    "on this licence. Calculate the model in SCIA itself to see the results there; LLSM cannot read them back yet."
)
NO_START_MODEL = (
    "This computer has no LLSM start model, so the model is pushed into SCIA's empty file and cannot be "
    "calculated from the chat. Once: `llsm start-model <template.esa>` (docs/verify/calc-run.md)."
)
NO_ROWS = (
    "SCIA calculated a copy but gave no results for these members. Known causes: the model can move or "
    "twist freely (a single span on a pin and a roller can rotate about its own axis: give one end a fixed "
    "support), or a steel material (LLSM's steel materials are not calculated by SCIA yet)."
)
STALE = ("The model was changed while SCIA calculated, so that result is thrown away. "
         "Call run_analysis again to calculate the model as it is now.")
FROM_A_COPY = ("These numbers come from a calculated copy of the model; the SCIA window itself is not "
               "calculated. SCIA exports the member ends and the extremes per member, not every section.")
MATERIAL_NOTE = ("LLSM gives its materials no stiffness or weight yet; SCIA uses its default concrete "
                 "(self weight with 2500 kg/m3). Forces are right when all members share one material; "
                 "deflections are not given.")


def _calc_status(job_id: str, wait_s: float | None = None) -> dict:
    status = calc_jobs.wait(job_id, MAX_WAIT_S if wait_s is None else wait_s)
    status.setdefault("calculated", None if status.get("status") == "running" else False)
    return status


def _store_results(ctx: LlsmContext, version: int, run: dict, members: list[str], cases: list[str]) -> dict:
    """Keep a finished calculation's results, if the model is still the one that was calculated."""
    base = {"seconds": run.get("seconds"), "esa_xml": run.get("esa_xml")}
    with ctx.lock:
        if ctx.model_version != version:
            return {**base, "ok": False, "calculated": False, "stale": True, "error": STALE}
        if not run["ok"]:
            error = run["problems"][0] if run["problems"] else NO_ROWS
            return {**base, "ok": False, "calculated": False, "error": error, "problems": run["problems"]}
        ctx.results = {"version": version, "cases": run["results"]}
        ctx.last_calc_ok = True
    without = sorted(m for m in members if not any(m in run["results"].get(c, {}) for c in cases))
    out = {**base, "ok": True, "calculated": True, "load_cases": cases, "members": len(members),
           "note": FROM_A_COPY, "material_note": MATERIAL_NOTE}
    if without:
        out["members_without_results"] = without
        out["warning"] = NO_ROWS
    return out


def run_analysis(ctx: LlsmContext, confirm: bool = False) -> dict:
    """Calculate the pushed model: mesh it in SCIA, save a copy, let SCIA's batch program calculate it.

    ok is True only when SCIA's results came back for this model. A calculation that outlasts one call
    keeps running: call run_analysis again to wait for it. Above ``calc.CONFIRM_ABOVE_S`` estimated,
    nothing happens without ``confirm``. Without a start model nothing can be calculated.
    """
    if not ctx.project_opened:
        return {"ok": False, "error": "no project open — call push_model first"}
    running = calc_jobs.running()
    if running is not None:
        return _calc_status(running.id)
    sess = ctx.session
    needs_mesh = ctx.mesh_dirty

    def _mesh():
        if needs_mesh:
            sess.project.CreateMesh()

    if ctx.start_copy is None:
        try:
            sess.submit(_mesh, timeout_s=600)
        except TimeoutError as e:
            return {"ok": False, "error": str(e), "wedged": True}
        ctx.mesh_dirty, ctx.calc_checked = False, True
        return {"ok": False, "calculated": False, "remeshed": needs_mesh, "error": NOT_CALCULATED, "hint": NO_START_MODEL}

    if ctx.builder.item_count() != ctx.pushed_items:
        return {"ok": False, "calculated": False,
                "error": "Objects were added after push_model and are not in SCIA yet: call push_model again, then run_analysis."}
    members, cases = _members_and_cases(ctx)
    if not members or not cases:
        return {"ok": False, "calculated": False, "error": "The model has no member or no load case: nothing to calculate."}
    estimate = calc.estimate_seconds(len(members), len(cases))
    if estimate > calc.CONFIRM_ABOVE_S and not confirm:
        return {"ok": False, "calculated": False, "needs_confirmation": True, "calc_estimate_s": estimate,
                "message": f"Calculating takes about {estimate} s. Ask the engineer, then call run_analysis(confirm=true)."}

    try:
        sess.submit(_mesh, timeout_s=600)
    except TimeoutError as e:
        return {"ok": False, "error": str(e), "wedged": True}
    ctx.mesh_dirty, ctx.calc_checked = False, True
    folder = Path(tempfile.mkdtemp(prefix="llsm-calc-"))
    saved = folder / "model.esa"
    with ctx.lock:
        version = ctx.model_version
    try:
        if not sess.save_project_as(saved, timeout_s=300) or not saved.is_file():
            shutil.rmtree(folder, ignore_errors=True)
            return {"ok": False, "calculated": False, "error": "SCIA did not save a copy of the model to calculate."}
    except TimeoutError as e:
        shutil.rmtree(folder, ignore_errors=True)
        return {"ok": False, "error": str(e), "wedged": True}
    allow = sess.own_pids

    def work(progress) -> dict:
        try:
            run = calc.calculate(saved, cases, folder, allow, progress=progress)
        finally:
            shutil.rmtree(folder, ignore_errors=True)  # a 36 MB copy per calculation
        return _store_results(ctx, version, run, members, cases)

    job = calc_jobs.start(ctx.model_name or "model", work)
    return {**_calc_status(job.id), "calc_estimate_s": estimate, "remeshed": needs_mesh}


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
    ctx.model_changed()  # earlier results no longer describe the model
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
# Tools: results (from the last run_analysis of this model version)
# ===================================================================

RESULT_KINDS = ("inner_forces", "deformation")
# Display units for the SI values the export gives (N, Nm, m, rad).
_DISPLAY = {"N": ("kN", 1e-3), "Vy": ("kN", 1e-3), "Vz": ("kN", 1e-3),
            "Mx": ("kNm", 1e-3), "My": ("kNm", 1e-3), "Mz": ("kNm", 1e-3)}
DEFLECTION_NOT_GIVEN = (
    "Deflections are not given yet: LLSM gives its materials no stiffness, so SCIA calculates with its own "
    "default (the test beam deflected 2.2 x less than C30/37 by hand, docs/verify/calc-run.md). "
    "Forces are not affected when all members share one material."
)


def _results_or_error(ctx: LlsmContext) -> tuple[dict | None, dict | None]:
    with ctx.lock:
        results = ctx.results if ctx.results is not None and ctx.results["version"] == ctx.model_version else None
    if results is not None:
        return results, None
    if ctx.project_opened and ctx.calc_checked and ctx.start_copy is None:
        return None, {"ok": False, "calculated": False, "error": NOT_CALCULATED, "hint": NO_START_MODEL}
    return None, {"ok": False, "error": "no analysis results available — call run_analysis first"}


def _load_beam_result(ctx, beam_name: str, load_case: str, result_kind: str) -> dict:
    """One member's rows of one load case from the last calculation: positions and SI values per magnitude."""
    if result_kind not in RESULT_KINDS:
        return {"ok": False, "error": f"unknown result_kind {result_kind!r}; use 'inner_forces' or 'deformation'"}
    results, error = _results_or_error(ctx)
    if error is not None:
        return error
    if result_kind == "deformation":
        return {"ok": False, "calculated": True, "reliable": False, "error": DEFLECTION_NOT_GIVEN}
    case = results["cases"].get(load_case)
    if case is None:
        return {"ok": False, "error": f"no results for load case {load_case!r}; calculated cases: {sorted(results['cases'])}"}
    member = case.get(beam_name)
    if member is None or not member["forces"]:
        return {"ok": False, "error": f"no results for member {beam_name!r} in {load_case!r}; members with results: {sorted(case)}"}
    rows = member["forces"]
    magnitudes = {name: [r.get(name, 0.0) for r in rows] for name in _DISPLAY}
    summary = {name: {"min": min(v), "max": max(v), "abs_max": max(abs(x) for x in v)} for name, v in magnitudes.items()}
    shown = {
        f"{name} ({unit})": [round(v * factor, 2) for v in magnitudes[name]]
        for name, (unit, factor) in _DISPLAY.items() if any(abs(v) > 1e-9 for v in magnitudes[name])
    }
    return {
        "ok": True,
        "beam": beam_name,
        "load_case": load_case,
        "kind": result_kind,
        "sections": len(rows),
        "positions_m": [round(r["dx_m"], 3) for r in rows],
        "magnitudes": magnitudes,
        "summary_per_magnitude": summary,
        "values_kN_kNm": shown,
        "note": FROM_A_COPY,
        "material_note": MATERIAL_NOTE,
    }


def get_beam_inner_forces(ctx: LlsmContext, beam: str, load_case: str) -> dict:
    """Read beam internal forces (N, Vy, Vz, Mx, My, Mz) for a load case, from the last run_analysis.

    Positions in metres along the member; ``magnitudes`` in N and N·m, ``values_kN_kNm`` the same in kN
    and kNm. The sections are the ones SCIA exports: member ends and extremes.
    """
    return _load_beam_result(ctx, beam_name=beam, load_case=load_case, result_kind="inner_forces")


def get_beam_deflection(ctx: LlsmContext, beam: str, load_case: str) -> dict:
    """Deflections are not given yet (see DEFLECTION_NOT_GIVEN); the answer says why."""
    return _load_beam_result(ctx, beam_name=beam, load_case=load_case, result_kind="deformation")
