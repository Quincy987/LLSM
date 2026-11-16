"""Builder rules that need no SCIA: action type follows the load group, duplicate names are refused."""
from types import SimpleNamespace

import pytest

from llsm.scia import tools
from llsm.scia.builder import (
    ACTION_TYPE_BY_GROUP_KIND,
    DuplicateNameError,
    ModelBuilder,
    action_type_for,
    check_action_type,
)


def _bare_builder() -> ModelBuilder:
    b = ModelBuilder.__new__(ModelBuilder)
    b._objects_by_name = {}
    b._group_kinds = {}
    b._model = []  # stands in for the ADM model: _check_free only iterates it and reads .Name
    return b


def _obj(name: str):
    return SimpleNamespace(Name=name)


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
    assert b.resolve_action_type("LG") == ("Variable", None)
    assert b.resolve_action_type("LG", "Accidental") == ("Accidental", None)


def test_resolve_reads_a_group_the_builder_did_not_add():
    b = _bare_builder()
    group = SimpleNamespace(Name="LG2", LoadGroupType=SimpleNamespace(ToString=lambda: "Moving"))
    b._model.append(group)
    assert b.resolve_action_type("LG2") == ("Variable", None)


def test_resolve_falls_back_to_permanent_with_a_warning():
    b = _bare_builder()
    b._model.append(_obj("Odd"))  # no LoadGroupType attribute
    resolved, warning = b.resolve_action_type("Odd")
    assert resolved == "Permanent"
    assert warning and "Permanent" in warning


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


def test_duplicate_is_a_value_error():
    assert issubclass(DuplicateNameError, ValueError)


# --- tools layer ---------------------------------------------------------

class _RefusingBuilder:
    def __init__(self, exc):
        self.exc = exc

    def resolve_action_type(self, group, action_type=None):
        return "Permanent", None

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


def test_load_case_tool_reports_action_type_and_warning():
    class B(_RefusingBuilder):
        def resolve_action_type(self, group, action_type=None):
            return "Permanent", "action type assumed Permanent"

        def add_load_case(self, name, group, kind="Standard", action_type=None):
            self.used = action_type

    b = B(None)
    out = tools.add_load_case(_ctx(b), "LC1", "LG")
    assert out["ok"] is True and out["action_type"] == "Permanent"
    assert out["warning"] == "action type assumed Permanent"
    assert b.used == "Permanent"
