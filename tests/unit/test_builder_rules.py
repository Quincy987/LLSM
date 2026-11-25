"""Builder rules that need no SCIA: action type follows the load group, duplicate names are refused."""
from types import SimpleNamespace

import pytest

from llsm.scia import tools
from llsm.scia.builder import (
    ACTION_TYPE_BY_GROUP_KIND,
    AdmRefusedError,
    DuplicateNameError,
    ModelBuilder,
    _unwrap,
    action_type_for,
    case_duration,
    case_load_type,
    check_action_type,
)


def _bare_builder() -> ModelBuilder:
    b = ModelBuilder.__new__(ModelBuilder)
    b._objects_by_name = {}
    b._group_kinds = {}
    b._model = []  # stands in for the ADM model: _check_free only iterates it and reads .Name
    b._supports, b._extras, b._self_weight_cases, b.grades, b._section_material = {}, {}, set(), {}, {}
    b._member_section = {}
    b.reserved, b.library = frozenset(), ()
    return b


def _obj(name: str, kind: str = "StructuralPointConnection", **attrs):
    """A stand-in ADM object: Name, and GetType().Name like the .NET one."""
    return SimpleNamespace(Name=name, GetType=lambda: SimpleNamespace(Name=kind), **attrs)


# --- action type ---------------------------------------------------------

def test_action_type_follows_the_shared_kinds():
    assert action_type_for("Variable") == "Variable"
    assert action_type_for("Permanent") == "Permanent"
    assert action_type_for("Accidental") == "Accidental"


def test_action_type_for_the_other_group_kinds():
    assert action_type_for("Moving") == "Variable"
    assert action_type_for("Tensioning") == "Permanent"
    assert action_type_for("Fire") == "Accidental"
    assert action_type_for("Seismic") == "Accidental"
    assert set(ACTION_TYPE_BY_GROUP_KIND.values()) == {"Permanent", "Variable", "Accidental"}


def test_unknown_group_kind_names_the_known_ones():
    with pytest.raises(ValueError) as exc:
        action_type_for("Traffic")
    assert "Traffic" in str(exc.value)
    assert "Variable" in str(exc.value) and "Moving" in str(exc.value)


def test_unknown_action_type_refused():
    assert check_action_type("Variable") == "Variable"
    with pytest.raises(ValueError):
        check_action_type("Seismic")


def test_resolve_uses_the_registered_group_kind():
    b = _bare_builder()
    b._register("LG", _obj("LG"), group_kind="Variable")
    assert b._group_kinds["LG"] == "Variable"
    assert b.resolve_action_type("LG") == "Variable"
    assert b.resolve_action_type("LG", "Accidental") == "Accidental"
    assert b.resolve_action_type("LG", "") == "Variable"  # an empty optional argument means: not given


def test_resolve_reads_a_group_the_builder_did_not_add():
    """Read back from the model, an object is an IAnalysisObject; get() unwraps it to the group."""
    b = _bare_builder()
    group = _obj("LG2", "StructuralLoadGroup", LoadGroupType="Moving")
    b._model.append(SimpleNamespace(Name="LG2", __implementation__=group))
    assert b.resolve_action_type("LG2") == "Variable"


def test_resolve_refuses_what_is_not_a_load_group():
    b = _bare_builder()
    b._model.append(_obj("N1"))  # a node: no LoadGroupType
    with pytest.raises(ValueError) as exc:
        b.resolve_action_type("N1")
    assert "not a load group" in str(exc.value) and "StructuralPointConnection" in str(exc.value)


# --- what ADM accepts for a load case --------------------------------------

def test_variable_case_gets_a_duration():
    assert case_duration("Standard", "Variable", None) == "Short"
    assert case_duration("Standard", "Variable", "Long") == "Long"


def test_permanent_and_accidental_cases_get_none():
    assert case_duration("Standard", "Permanent", None) is None
    assert case_duration("SelfWeight", "Permanent", "") is None
    assert case_duration("Standard", "Accidental", None) is None


def test_case_combinations_adm_would_drop_are_refused():
    with pytest.raises(ValueError, match="variable case only"):
        case_duration("Standard", "Permanent", "Short")
    with pytest.raises(ValueError, match="SelfWeight case must be permanent"):
        case_duration("SelfWeight", "Variable", None)
    with pytest.raises(ValueError, match="unknown duration"):
        case_duration("Standard", "Variable", "Forever")


# --- duplicate names -----------------------------------------------------

def test_check_free_after_register_refuses():
    b = _bare_builder()
    b._check_free("A")
    b._register("A", _obj("A"))
    with pytest.raises(DuplicateNameError) as exc:
        b._check_free("A")
    assert "'A'" in str(exc.value)


def test_check_free_sees_objects_in_the_model():
    b = _bare_builder()
    b._model.append(_obj("B"))
    with pytest.raises(DuplicateNameError):
        b._check_free("B")


def test_real_add_order_does_not_refuse_itself():
    b = _bare_builder()
    obj = _obj("C")
    b._check_free("C")
    b._model.append(obj)  # what AddItemToModel does
    b._register("C", obj)  # must not scan or raise
    assert b._objects_by_name["C"] is obj


def test_model_information_does_not_block_a_name():
    b = _bare_builder()
    b._model.append(_obj("portaal", "ModelInformation"))
    b._check_free("portaal")  # a node named like the model is fine
    with pytest.raises(KeyError):
        b.get("portaal")  # nor does a lookup find it, so it can never be cached as that name
    assert "portaal" not in b._objects_by_name


def test_refusals_are_value_errors():
    assert issubclass(DuplicateNameError, ValueError) and issubclass(AdmRefusedError, ValueError)


def test_add_refuses_when_adm_returns_false():
    b = _bare_builder()
    b._svc = SimpleNamespace(AddItemToModel=lambda model, obj: False)
    with pytest.raises(AdmRefusedError, match="nothing was added"):
        b._add("C", _obj("C", "StructuralLoadCase"))
    assert "C" not in b._objects_by_name


def test_add_registers_what_landed():
    b = _bare_builder()
    b._svc = SimpleNamespace(AddItemToModel=lambda model, obj: model.append(obj) or True)
    lg = _obj("LG", "StructuralLoadGroup")
    b._add("LG", lg, group_kind="Moving")
    assert b._objects_by_name["LG"] is lg and b._group_kinds["LG"] == "Moving"
    with pytest.raises(DuplicateNameError):
        b._add("LG", _obj("LG", "StructuralLoadGroup"))


def test_unwrap_returns_the_concrete_object():
    inner = _obj("A")
    assert _unwrap(SimpleNamespace(__implementation__=inner)) is inner
    assert _unwrap(inner) is inner


# --- tools layer ---------------------------------------------------------

class _RefusingBuilder:
    def __init__(self, exc):
        self.exc = exc

    def add_load_case(self, *a, **k):
        raise self.exc

    def add_node(self, *a, **k):
        raise self.exc


def _ctx(builder):
    ctx = tools.LlsmContext()
    ctx.builder = builder
    return ctx


def test_tool_returns_ok_false_on_duplicate_node():
    ctx = _ctx(_RefusingBuilder(DuplicateNameError("an object named 'N1' already exists")))
    out = tools.add_node(ctx, "N1", 0, 0, 0)
    assert out["ok"] is False
    assert "already exists" in out["error"]
    assert out["name"] == "N1" and out["duplicate"] is True


def test_tool_returns_ok_false_on_duplicate_load_case():
    ctx = _ctx(_RefusingBuilder(DuplicateNameError("an object named 'LC1' already exists")))
    out = tools.add_load_case(ctx, "LC1", "LG")
    assert out["ok"] is False and "already exists" in out["error"]


class _StoringBuilder:
    """Returns a case whose stored values differ from what was asked, to prove the tool reads them back."""

    def __init__(self, stored_action, stored_duration=None):
        self.case = SimpleNamespace(ActionType=stored_action, Duration=stored_duration)

    def add_load_case(self, name, group, kind="Standard", action_type=None, duration=None):
        self.asked = (action_type, duration)
        return self.case


def test_variable_and_accidental_cases_get_scias_static_type():
    # SCIA refuses a variable case of type Standard ("statische belastingsgevallen: NIET OK")
    assert case_load_type("Standard", "Variable") == "Static"
    assert case_load_type("Standard", "Accidental") == "Static"
    assert case_load_type("Static", "Variable") == "Static"


def test_permanent_cases_keep_standard_and_self_weight():
    assert case_load_type("Standard", "Permanent") == "Standard"
    assert case_load_type("Static", "Permanent") == "Standard"
    assert case_load_type("SelfWeight", "Permanent") == "SelfWeight"
    assert case_load_type("Prestress", "Permanent") == "Prestress"


def _case_builder():
    """A bare builder with just enough ADM to build a load case; records what the case was made with."""
    b = _bare_builder()
    b._svc = SimpleNamespace(AddItemToModel=lambda model, obj: model.append(obj) or True)
    b._System = SimpleNamespace(Guid=SimpleNamespace(NewGuid=lambda: "guid"))
    made = []

    def case(guid, name, action, group, load_type):
        made.append({"action": action, "load_type": load_type})
        return _obj(name, "StructuralLoadCase", ActionType=action, LoadType=load_type)

    enums = SimpleNamespace(
        LoadCaseType=SimpleNamespace(Standard="Standard", Static="Static", SelfWeight="SelfWeight"),
        ActionType=SimpleNamespace(Permanent="Permanent", Variable="Variable", Accidental="Accidental"),
        Duration=SimpleNamespace(Short="Short", Long="Long"),
    )
    b._adm = SimpleNamespace(Enums=enums, Loads=SimpleNamespace(StructuralLoadCase=case))
    b._register("LG-V", _obj("LG-V", "StructuralLoadGroup"), group_kind="Variable")
    b._register("LG-P", _obj("LG-P", "StructuralLoadGroup"), group_kind="Permanent")
    return b, made


def test_builder_writes_a_variable_standard_case_as_static():
    b, made = _case_builder()
    b.add_load_case("Q", "LG-V")
    b.add_load_case("G", "LG-P")
    assert made == [{"action": "Variable", "load_type": "Static"}, {"action": "Permanent", "load_type": "Standard"}]


def test_unknown_case_kind_is_refused_by_name():
    b, made = _case_builder()
    with pytest.raises(ValueError, match="unknown load case kind 'Bogus'"):
        b.add_load_case("Q", "LG-V", kind="Bogus")
    assert made == [] and "Q" not in b._objects_by_name


def test_load_case_tool_reports_the_stored_load_type():
    b = _StoringBuilder("Variable", "Short")
    b.case.LoadType = "Static"
    out = tools.add_load_case(_ctx(b), "LC1", "LG")
    assert out["kind"] == "Static"


def test_load_case_tool_reports_what_was_stored():
    b = _StoringBuilder("Variable", "Short")
    out = tools.add_load_case(_ctx(b), "LC1", "LG")
    assert out == {"ok": True, "name": "LC1", "group": "LG", "kind": "Standard",
                   "action_type": "Variable", "duration": "Short"}
    assert b.asked == (None, None)


def test_load_case_tool_has_no_duration_for_a_permanent_case():
    out = tools.add_load_case(_ctx(_StoringBuilder("Permanent")), "LC1", "LG", action_type="Permanent")
    assert out["action_type"] == "Permanent" and "duration" not in out


def test_api_schema_offers_every_group_kind_and_the_case_options():
    from llsm.llm.tools import TOOL_DEFS
    defs = {d["name"]: d["input_schema"]["properties"] for d in TOOL_DEFS}
    assert defs["add_load_group"]["kind"]["enum"] == list(ACTION_TYPE_BY_GROUP_KIND)
    assert {"action_type", "duration"} <= set(defs["add_load_case"])
