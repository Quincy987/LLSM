"""The builder keeps what the engineer asked for per support and per material, and holds extras on top."""
from types import SimpleNamespace

import pytest

from llsm.scia import tools
from llsm.scia.builder import ModelBuilder, ReservedNameError
from llsm.scia.grades import GradeError

from .test_builder_rules import _bare_builder, _obj


def _builder():
    b = _bare_builder()
    b._svc = SimpleNamespace(AddItemToModel=lambda model, obj: model.append(obj) or True,
                             RemoveItemFromModel=lambda model, obj: model.remove(obj))
    b._System = SimpleNamespace(Guid=SimpleNamespace(NewGuid=lambda: "guid"))

    def support(guid, name, node):
        return _obj(name, "StructuralPointSupport", Node=node)

    StructuralMaterial = type("StructuralMaterial", (SimpleNamespace,), {})  # snapshot reads the type's name

    def material(guid, name, kind, quality):
        return StructuralMaterial(Name=name, MaterialType=kind, Quality=quality,
                                  GetType=lambda: SimpleNamespace(Name="StructuralMaterial"))

    b._adm = SimpleNamespace(
        Enums=SimpleNamespace(ConstraintType=SimpleNamespace(Rigid="Rigid", Free="Free"), MaterialType=SimpleNamespace(Steel="Steel", Concrete="Concrete", Timber="Timber",
                                                           Aluminium="Aluminium")),
        StructuralElements=SimpleNamespace(StructuralPointSupport=support),
        Libraries=SimpleNamespace(StructuralMaterial=material),
    )
    b._un = SimpleNamespace()
    b._constraint_trans = lambda kind: SimpleNamespace(Type=kind)
    b._constraint_rot = lambda kind: SimpleNamespace(Type=kind, rotation=True)
    b._register("A", _obj("A"))
    b._register("B", _obj("B"))
    return b


def _held(support) -> set[str]:
    return {dof for dof, prop in ModelBuilder._DOF_PROPS.items() if getattr(support, prop).Type == "Rigid"}


def test_supports_hold_what_was_asked():
    b = _builder()
    assert _held(b.add_pinned_support("SA", "A")) == {"X", "Y", "Z"}
    assert _held(b.add_roller_support("SB", "B", "Z")) == {"Z"}
    assert b.requested_supports()["SB"] == {"node": "B", "kind": "roller", "direction": "Z", "held": frozenset({"Z"})}
    with pytest.raises(ValueError, match="roller direction"):
        b.add_roller_support("SC", "B", "Q")


def test_extras_are_held_on_top_and_released_again():
    b = _builder()
    sa, sb = b.add_pinned_support("SA", "A"), b.add_roller_support("SB", "B")
    assert b.apply_extras({"SB": ["Y"], "SA": ["RX"]}) is True
    assert _held(sb) == {"Z", "Y"} and _held(sa) == {"X", "Y", "Z", "RX"}
    assert b.apply_extras({"SB": ["Y"], "SA": ["RX"]}) is False  # nothing changed, nothing written
    assert b.apply_extras({}) is True
    assert _held(sb) == {"Z"} and _held(sa) == {"X", "Y", "Z"}


def test_a_material_gets_its_grade_from_the_library():
    b = _builder()
    b.library = ("C30/37", "S 235")
    m = b.add_material("Beton ligger", grade="C30/37")
    assert m.Quality == "C30/37" and m.MaterialType == "Concrete"
    assert b.add_material("S235").Quality == "S 235"
    assert b.grades["S235"] == {"kind": "Steel", "grade": "S 235", "validated": True, "scia_name": "S235 (LLSM)"}
    ungraded = b.add_material("Iets", kind="Concrete")
    assert ungraded.Quality == "Iets" and b.grades["Iets"]["grade"] is None
    with pytest.raises(GradeError):
        b.add_material("Staal", kind="Steel", grade="C30/37")
    assert "Staal" not in b._objects_by_name


def test_a_material_named_like_a_library_grade_is_renamed_inside_scia():
    """try 20 P2: a material with a library name makes SCIA drop the whole pushed model."""
    b = _builder()
    b.library = ("C30/37", "S 235")
    b.reserved = frozenset(b.library)
    m = b.add_material("C30/37")
    assert m.Name == "C30/37 (LLSM)" and m.Quality == "C30/37"
    assert b.get("C30/37") is m and b.grades["C30/37"]["scia_name"] == "C30/37 (LLSM)"
    with pytest.raises(ValueError, match="already exists"):
        b.add_material("C30/37")
    with pytest.raises(ReservedNameError):
        b.add_material("S 235", kind="Concrete", grade="C30/37")  # a library name that is not its grade
    snap = b.snapshot()
    assert snap["materials"] == ["C30/37"]
    assert b.delete_object("C30/37") == ["C30/37"] and "C30/37 (LLSM)" not in b._objects_by_name


def test_a_reserved_name_is_refused_ignoring_case_and_spaces():
    b = _builder()
    b.reserved = frozenset({"LG1", "S 235"})
    with pytest.raises(ReservedNameError):
        b._check_free("lg1")
    with pytest.raises(ReservedNameError):
        b._check_free("S235")


def test_materials_in_use_follow_the_members_sections(monkeypatch):
    b = _builder()
    b.grades = {"G": {"grade": "C30/37", "validated": True}, "U": {"grade": None, "validated": False}}
    b._section_material = {"R1": "G", "R2": "U"}
    monkeypatch.setattr(b, "snapshot", lambda: {"beams": [{"name": "V", "section": "R1"}]})
    assert b.materials_in_use() == ["G"] and b.ungraded_in_use() == []
    monkeypatch.setattr(b, "snapshot", lambda: {"beams": [{"name": "V", "section": "R1"}, {"name": "W", "section": "R2"}]})
    assert b.ungraded_in_use() == ["U"]


def test_the_material_tool_says_when_there_is_no_grade():
    class _B:
        grades = {}

        def add_material(self, name, kind=None, grade=None):
            self.grades[name] = {"kind": kind or "Concrete", "grade": grade, "validated": bool(grade)}

    ctx = tools.LlsmContext()
    ctx.builder = _B()
    out = tools.add_material(ctx, "Beton", kind="Concrete")
    assert out["ok"] and out["grade"] is None and "default concrete" in out["warning"]
    out = tools.add_material(ctx, "C30", grade="C30/37")
    assert out["grade"] == "C30/37" and "warning" not in out


def test_both_tool_registries_offer_the_grade():
    from llsm.llm.tools import TOOL_DEFS
    defs = {d["name"]: d["input_schema"]["properties"] for d in TOOL_DEFS}
    assert {"name", "grade", "kind"} == set(defs["add_material"])
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "src" / "llsm" / "mcp_server.py").read_text(encoding="utf-8")
    assert "def add_material(name: str, grade: str | None = None, kind: str | None = None)" in src
