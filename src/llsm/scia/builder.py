"""ADM model builder — convenience wrappers around SCIA's ADM C# API.

Everything here runs off-SCIA-thread (pure model-in-memory construction),
then gets pushed into SCIA via SciaSession.push_model().
"""
from __future__ import annotations
from typing import Any


class ModelBuilder:
    """Builds an ADM analysis model in memory.

    Import-heavy: needs loader.load() called first. All .NET types are
    looked up lazily so the module can be imported before DLLs load.
    """

    def __init__(self, name: str = "LLSM_Model") -> None:
        self._import_namespaces()
        import System
        self._System = System

        self._model = self._adm.Models.AnalysisModel()
        self._bootstrapper = self._adm.Integration.Bootstrapper.AnalysisDataModelBootstrapper()
        self._scope = self._bootstrapper.CreateThreadedScope()
        self._svc = self._scope.GetService[self._adm.Contracts.IAnalysisModelService]()

        # Model information
        info = self._adm.ModelInformation(System.Guid.NewGuid(), name)
        info.NationalCode = self._adm.Enums.NationalCode.EC_Standard_EN
        info.SourceApplication = "LLSM"
        self._svc.AddItemToModel(self._model, info)

        self._objects_by_name: dict[str, Any] = {}

    def _import_namespaces(self) -> None:
        import ModelExchanger.AnalysisDataModel as adm
        import CSInfrastructure as cs
        import UnitsNet
        self._adm = adm
        self._cs = cs
        self._un = UnitsNet

    # --- Materials ---------------------------------------------------------

    def add_material(self, name: str, kind: str = "Steel") -> Any:
        """kind: Steel, Concrete, Timber, Aluminium."""
        mt = getattr(self._adm.Enums.MaterialType, kind)
        m = self._adm.Libraries.StructuralMaterial(
            self._System.Guid.NewGuid(), name, mt, name
        )
        self._svc.AddItemToModel(self._model, m)
        self._objects_by_name[name] = m
        return m

    # --- Cross-sections ----------------------------------------------------

    def add_rectangular_section(self, name: str, material: str, width_mm: float, height_mm: float) -> Any:
        mat = self.get(material)
        params = self._System.Collections.Generic.List[self._un.Length]()
        params.Add(self._un.Length(width_mm, self._un.Units.LengthUnit.Millimeter))
        params.Add(self._un.Length(height_mm, self._un.Units.LengthUnit.Millimeter))
        xs = self._adm.Libraries.StructuralParametricCrossSection(
            self._System.Guid.NewGuid(), name, mat,
            self._adm.Enums.ProfileLibraryId.Rectangle, params
        )
        self._svc.AddItemToModel(self._model, xs)
        self._objects_by_name[name] = xs
        return xs

    # --- Geometry ----------------------------------------------------------

    def add_node(self, name: str, x_mm: float, y_mm: float, z_mm: float) -> Any:
        n = self._adm.StructuralElements.StructuralPointConnection(
            self._System.Guid.NewGuid(), name,
            self._un.Length(x_mm, self._un.Units.LengthUnit.Millimeter),
            self._un.Length(y_mm, self._un.Units.LengthUnit.Millimeter),
            self._un.Length(z_mm, self._un.Units.LengthUnit.Millimeter),
        )
        self._svc.AddItemToModel(self._model, n)
        self._objects_by_name[name] = n
        return n

    def add_beam(self, name: str, node_a: str, node_b: str, cross_section: str) -> Any:
        """Straight-line beam between two existing nodes."""
        na, nb = self.get(node_a), self.get(node_b)
        nodes = self._System.Collections.Generic.List[self._adm.StructuralElements.StructuralPointConnection]()
        nodes.Add(na)
        nodes.Add(nb)
        line = self._adm.Subtypes.Curve[self._adm.StructuralElements.StructuralPointConnection](
            self._adm.Enums.CurveGeometricalShape.Line, nodes
        )
        curves = self._System.Collections.Generic.List[
            self._adm.Subtypes.Curve[self._adm.StructuralElements.StructuralPointConnection]
        ]()
        curves.Add(line)
        beam = self._adm.StructuralElements.StructuralCurveMember(
            self._System.Guid.NewGuid(), name, curves, self.get(cross_section)
        )
        beam.Layer = "LayerADM"
        self._svc.AddItemToModel(self._model, beam)
        self._objects_by_name[name] = beam
        return beam

    # --- Supports ----------------------------------------------------------

    def _constraint_trans(self, kind: str) -> Any:
        c = self._adm.Subtypes.Constraint[self._System.Nullable[self._un.ForcePerLength]]()
        c.Type = getattr(self._adm.Enums.ConstraintType, kind)
        return c

    def _constraint_rot(self, kind: str) -> Any:
        c = self._adm.Subtypes.Constraint[self._System.Nullable[self._un.RotationalStiffness]]()
        c.Type = getattr(self._adm.Enums.ConstraintType, kind)
        return c

    def add_fixed_support(self, name: str, node: str) -> Any:
        """Fully restrained 3D support on a node."""
        s = self._adm.StructuralElements.StructuralPointSupport(
            self._System.Guid.NewGuid(), name, self.get(node)
        )
        tr = self._constraint_trans("Rigid")
        ro = self._constraint_rot("Rigid")
        s.TranslationX = tr; s.TranslationY = tr; s.TranslationZ = tr
        s.RotationX = ro;    s.RotationY = ro;    s.RotationZ = ro
        self._svc.AddItemToModel(self._model, s)
        self._objects_by_name[name] = s
        return s

    def add_pinned_support(self, name: str, node: str) -> Any:
        """Pinned support — translations rigid, rotations free."""
        s = self._adm.StructuralElements.StructuralPointSupport(
            self._System.Guid.NewGuid(), name, self.get(node)
        )
        tr = self._constraint_trans("Rigid")
        ro = self._constraint_rot("Free")
        s.TranslationX = tr; s.TranslationY = tr; s.TranslationZ = tr
        s.RotationX = ro;    s.RotationY = ro;    s.RotationZ = ro
        self._svc.AddItemToModel(self._model, s)
        self._objects_by_name[name] = s
        return s

    def add_roller_support(self, name: str, node: str, direction: str = "Z") -> Any:
        """Roller — rigid only in `direction`, free in others."""
        s = self._adm.StructuralElements.StructuralPointSupport(
            self._System.Guid.NewGuid(), name, self.get(node)
        )
        tr_r = self._constraint_trans("Rigid")
        tr_f = self._constraint_trans("Free")
        ro_f = self._constraint_rot("Free")
        s.TranslationX = tr_r if direction == "X" else tr_f
        s.TranslationY = tr_r if direction == "Y" else tr_f
        s.TranslationZ = tr_r if direction == "Z" else tr_f
        s.RotationX = ro_f; s.RotationY = ro_f; s.RotationZ = ro_f
        self._svc.AddItemToModel(self._model, s)
        self._objects_by_name[name] = s
        return s

    # --- Loads -------------------------------------------------------------

    def add_load_group(self, name: str, kind: str = "Permanent") -> Any:
        lg = self._adm.Loads.StructuralLoadGroup(
            self._System.Guid.NewGuid(), name,
            getattr(self._adm.Enums.LoadGroupType, kind)
        )
        lg.Relation = self._adm.Enums.Relation.Standard
        lg.Load = self._cs.FlexibleEnum[self._adm.Enums.Load](self._adm.Enums.Load.Domestic)
        self._svc.AddItemToModel(self._model, lg)
        self._objects_by_name[name] = lg
        return lg

    def add_load_case(self, name: str, group: str, kind: str = "Standard") -> Any:
        """kind: Standard, SelfWeight."""
        lc_type = getattr(self._adm.Enums.LoadCaseType, kind)
        lc = self._adm.Loads.StructuralLoadCase(
            self._System.Guid.NewGuid(), name,
            self._adm.Enums.ActionType.Permanent,
            self.get(group), lc_type
        )
        self._svc.AddItemToModel(self._model, lc)
        self._objects_by_name[name] = lc
        return lc

    def add_line_load(
        self, name: str, beam: str, load_case: str,
        magnitude_kn_per_m: float, direction: str = "Z"
    ) -> Any:
        beam_obj = self.get(beam)
        bref = self._adm.StructuralReferences.Curves.CurveStructuralReferenceOnBeam(beam_obj)
        ld = self._adm.Loads.StructuralCurveAction[
            self._adm.StructuralReferences.Curves.CurveStructuralReferenceOnBeam
        ](
            self._System.Guid.NewGuid(), name,
            self._adm.Enums.CurveForceAction.OnBeam,
            self._un.ForcePerLength(magnitude_kn_per_m, self._un.Units.ForcePerLengthUnit.KilonewtonPerMeter),
            self.get(load_case), bref,
        )
        ld.Direction = getattr(self._adm.Enums.ActionDirection, direction)
        self._svc.AddItemToModel(self._model, ld)
        self._objects_by_name[name] = ld
        return ld

    # --- Edit primitives ---------------------------------------------------

    def update_node(self, name: str, x_mm: float, y_mm: float, z_mm: float) -> Any:
        """Move an existing node. Try-or-rollback semantics.

        On any exception while mutating, restore the original X/Y/Z and re-raise.
        Caller is responsible for RefreshModel_ToSCIAEngineer + ValidateModel after.
        """
        node = self.get(name)
        prev = (node.X, node.Y, node.Z)
        try:
            node.X = self._un.Length(x_mm, self._un.Units.LengthUnit.Millimeter)
            node.Y = self._un.Length(y_mm, self._un.Units.LengthUnit.Millimeter)
            node.Z = self._un.Length(z_mm, self._un.Units.LengthUnit.Millimeter)
        except Exception:
            node.X, node.Y, node.Z = prev
            raise
        return node

    def get_node_position(self, name: str) -> tuple[float, float, float]:
        """Return (x, y, z) of the named node in millimetres.

        Reads from the in-memory ADM model — useful for diagnosing whether
        an edit landed in the model (separate from the question of whether
        SCIA's display refreshed). Raises KeyError if the node isn't found.
        """
        node = self.get(name)
        # node.X/Y/Z are UnitsNet.Length values; convert to mm explicitly.
        return (
            float(node.X.As(self._un.Units.LengthUnit.Millimeter)),
            float(node.Y.As(self._un.Units.LengthUnit.Millimeter)),
            float(node.Z.As(self._un.Units.LengthUnit.Millimeter)),
        )

    def set_line_load_magnitude(self, name: str, magnitude_kn_per_m: float) -> Any:
        """Adjust the magnitude of an existing line load (kN/m)."""
        load = self.get(name)
        load.Value = self._un.ForcePerLength(
            magnitude_kn_per_m, self._un.Units.ForcePerLengthUnit.KilonewtonPerMeter
        )
        return load

    def set_member_cross_section(self, name: str, cross_section: str) -> Any:
        """Swap the cross-section of an existing curve member."""
        beam = self.get(name)
        xs = self.get(cross_section)
        beam.CrossSection = xs
        return beam

    def set_member_endpoints(self, name: str, node_a: str, node_b: str) -> Any:
        """Re-route a beam between two existing nodes. Try-or-rollback."""
        beam = self.get(name)
        na, nb = self.get(node_a), self.get(node_b)
        if na is nb:
            raise ValueError(f"endpoints must differ (got both = {node_a!r})")
        prev_curves = beam.Curves
        try:
            nodes = self._System.Collections.Generic.List[
                self._adm.StructuralElements.StructuralPointConnection
            ]()
            nodes.Add(na)
            nodes.Add(nb)
            line = self._adm.Subtypes.Curve[self._adm.StructuralElements.StructuralPointConnection](
                self._adm.Enums.CurveGeometricalShape.Line, nodes
            )
            curves = self._System.Collections.Generic.List[
                self._adm.Subtypes.Curve[self._adm.StructuralElements.StructuralPointConnection]
            ]()
            curves.Add(line)
            beam.Curves = curves
        except Exception:
            try:
                beam.Curves = prev_curves
            except Exception:
                pass
            raise
        return beam

    def find_dependents(self, name: str) -> list[str]:
        """Return names of objects that reference `name`.

        Cascade-blocker check for delete_object. Walks the ADM model looking for
        references on common attributes (cross-section, curve nodes, support node,
        load case, load group, beam-load reference).

        We compare by Name string rather than Python object identity because
        pythonnet returns a fresh Python proxy on each managed-attribute access;
        `obj.CrossSection is target` is unreliable. Names are unique within the
        builder's bookkeeping, so name-equality is the safest stable comparison.
        """
        target_name = name
        deps: list[str] = []

        def _name_of(x: Any) -> str | None:
            if x is None:
                return None
            try:
                return x.Name
            except Exception:
                return None

        for obj in self._model:
            obj_name = _name_of(obj)
            if obj_name is None or obj_name == target_name:
                continue
            try:
                if _name_of(getattr(obj, "CrossSection", None)) == target_name:
                    deps.append(obj_name); continue
            except Exception:
                pass
            try:
                if _name_of(getattr(obj, "Material", None)) == target_name:
                    deps.append(obj_name); continue
            except Exception:
                pass
            try:
                curves = getattr(obj, "Curves", None)
                if curves is not None:
                    matched = False
                    for c in curves:
                        for n in (getattr(c, "Nodes", None) or []):
                            if _name_of(n) == target_name:
                                matched = True; break
                        if matched:
                            break
                    if matched:
                        deps.append(obj_name); continue
            except Exception:
                pass
            try:
                if _name_of(getattr(obj, "Node", None)) == target_name:
                    deps.append(obj_name); continue
            except Exception:
                pass
            try:
                if _name_of(getattr(obj, "LoadGroup", None)) == target_name:
                    deps.append(obj_name); continue
            except Exception:
                pass
            try:
                if _name_of(getattr(obj, "LoadCase", None)) == target_name:
                    deps.append(obj_name); continue
            except Exception:
                pass
            try:
                ref = getattr(obj, "Reference", None)
                if ref is not None and _name_of(getattr(ref, "Beam", None)) == target_name:
                    deps.append(obj_name); continue
            except Exception:
                pass
        return deps

    def delete_object(self, name: str, *, cascade: bool = False) -> list[str]:
        """Delete an ADM object by name.

        Returns the list of names that were deleted (the target plus any cascaded
        dependents). With cascade=False, raises ValueError(blockers_list) if
        dependents exist. With cascade=True, recursively deletes dependents first.
        """
        target = self.get(name)
        deps = self.find_dependents(name)
        if deps and not cascade:
            raise ValueError(deps)

        deleted: list[str] = []
        if cascade:
            for d in deps:
                # A dependent may have already been removed via a different
                # cascade path (e.g. a load that depends on both a beam and a
                # node when both are being cascaded). Tolerate that.
                try:
                    deleted.extend(self.delete_object(d, cascade=True))
                except KeyError:
                    continue

        last_exc: Exception | None = None
        for attempt in (
            lambda: self._svc.RemoveItemFromModel(self._model, target),
            lambda: self._model.Remove(target),
            lambda: self._svc.DeleteItemFromModel(self._model, target),
        ):
            try:
                attempt()
                last_exc = None
                break
            except Exception as e:
                last_exc = e
                continue
        if last_exc is not None:
            raise RuntimeError(
                f"ADM delete API not available in this SCIA version: "
                f"{type(last_exc).__name__}: {last_exc}"
            )

        self._objects_by_name.pop(name, None)
        deleted.append(name)
        return deleted

    # --- Query + finalize --------------------------------------------------

    def get(self, name: str) -> Any:
        if name in self._objects_by_name:
            return self._objects_by_name[name]
        for obj in self._model:
            if obj.Name == name:
                self._objects_by_name[name] = obj
                return obj
        raise KeyError(f"no ADM object named {name!r}")

    @property
    def model(self) -> Any:
        return self._model

    def item_count(self) -> int:
        return sum(1 for _ in self._model)

    def names(self) -> list[str]:
        return [obj.Name for obj in self._model]

    def snapshot(self) -> dict:
        """Structured dict of the current model, per the panel sidebar contract.

        Returns exactly these eight top-level keys (empty list if a category
        has no objects). All names are strings; distances are millimetres;
        line-load magnitudes are kN/m, signed (negative = downward in Z).

        Implementation walks the live ADM model so we don't drift from
        whatever the C# layer actually thinks is in there. Each per-object
        accessor is wrapped in try/except: pythonnet attribute reads can
        raise on partially-constructed objects, and the panel sidebar must
        survive that — better to show a name with missing fields than to
        crash the whole snapshot.
        """
        out: dict[str, list] = {
            "materials":   [],
            "sections":    [],
            "nodes":       [],
            "beams":       [],
            "supports":    [],
            "load_groups": [],
            "load_cases":  [],
            "loads":       [],
        }

        un = self._un
        adm = self._adm

        def _name(obj):
            try:
                return obj.Name
            except Exception:
                return "?"

        def _mm(length):
            try:
                return float(length.As(un.Units.LengthUnit.Millimeter))
            except Exception:
                return 0.0

        def _knpm(force):
            try:
                return float(force.As(un.Units.ForcePerLengthUnit.KilonewtonPerMeter))
            except Exception:
                return 0.0

        # Heuristic kind classification: Rigid in all 3 trans + 3 rot = fixed,
        # Rigid in 3 trans + Free in 3 rot = pinned, otherwise roller (or unknown).
        def _support_kind(s):
            try:
                rigid_t = adm.Enums.ConstraintType.Rigid
                free_r  = adm.Enums.ConstraintType.Free
                tx, ty, tz = s.TranslationX.Type, s.TranslationY.Type, s.TranslationZ.Type
                rx, ry, rz = s.RotationX.Type, s.RotationY.Type, s.RotationZ.Type
                all_t_rigid = (tx == rigid_t and ty == rigid_t and tz == rigid_t)
                all_r_rigid = (rx == rigid_t and ry == rigid_t and rz == rigid_t)
                all_r_free  = (rx == free_r  and ry == free_r  and rz == free_r)
                if all_t_rigid and all_r_rigid:
                    return "fixed"
                if all_t_rigid and all_r_free:
                    return "pinned"
                return "roller"
            except Exception:
                return "?"

        for obj in self._model:
            try:
                t = type(obj).__name__
            except Exception:
                continue
            try:
                if t == "StructuralMaterial":
                    out["materials"].append(_name(obj))
                elif t == "StructuralParametricCrossSection":
                    out["sections"].append(_name(obj))
                elif t == "StructuralPointConnection":
                    out["nodes"].append({
                        "name": _name(obj),
                        "x_mm": _mm(obj.X), "y_mm": _mm(obj.Y), "z_mm": _mm(obj.Z),
                    })
                elif t == "StructuralCurveMember":
                    n1 = n2 = "?"
                    sec = ""
                    try:
                        # Curves[0].Nodes are the two endpoints for a straight line.
                        first_curve = next(iter(obj.Curves))
                        nodes = list(first_curve.Nodes)
                        if len(nodes) >= 2:
                            n1, n2 = _name(nodes[0]), _name(nodes[-1])
                    except Exception:
                        pass
                    try:
                        sec = _name(obj.CrossSection)
                    except Exception:
                        pass
                    out["beams"].append({
                        "name": _name(obj), "n1": n1, "n2": n2, "section": sec,
                    })
                elif t == "StructuralPointSupport":
                    node_name = "?"
                    try:
                        node_name = _name(obj.Node)
                    except Exception:
                        pass
                    out["supports"].append({
                        "name": _name(obj), "node": node_name, "kind": _support_kind(obj),
                    })
                elif t == "StructuralLoadGroup":
                    out["load_groups"].append(_name(obj))
                elif t == "StructuralLoadCase":
                    grp = "?"
                    try:
                        grp = _name(obj.LoadGroup)
                    except Exception:
                        pass
                    out["load_cases"].append({"name": _name(obj), "group": grp})
                elif t.startswith("StructuralCurveAction"):
                    case_name = "?"
                    target = "?"
                    direction = "?"
                    mag = 0.0
                    try:
                        case_name = _name(obj.LoadCase)
                    except Exception:
                        pass
                    try:
                        target = _name(obj.Reference.Beam)
                    except Exception:
                        pass
                    try:
                        direction = obj.Direction.ToString()
                    except Exception:
                        pass
                    try:
                        mag = _knpm(obj.Value)
                    except Exception:
                        pass
                    out["loads"].append({
                        "name": _name(obj),
                        "case": case_name,
                        "kind": "line",
                        "target": target,
                        "magnitude_kN_per_m": mag,
                        "direction": direction,
                    })
            except Exception:
                # Skip objects we can't classify; sidebar tolerates partial state.
                continue
        return out

    def validate(self) -> None:
        self._svc.ValidateModel(self._model)

    def dispose(self) -> None:
        try:
            self._scope.Dispose()
        except Exception:
            pass
