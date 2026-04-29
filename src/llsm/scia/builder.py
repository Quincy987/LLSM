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

    def validate(self) -> None:
        self._svc.ValidateModel(self._model)

    def dispose(self) -> None:
        try:
            self._scope.Dispose()
        except Exception:
            pass
