"""A model named by its file name is found under the model folders; several or none are said so."""
import os

import pytest

from llsm.scia import model_lookup
from llsm.scia.model_lookup import resolve_model


@pytest.fixture
def tree(tmp_path, monkeypatch):
    (tmp_path / "LLSM demo").mkdir()
    (tmp_path / "LLSM demo" / "brugdek.esa").write_bytes(b"x")
    (tmp_path / "Projecten" / "Kade 12").mkdir(parents=True)
    (tmp_path / "Projecten" / "Kade 12" / "kademuur.esa").write_bytes(b"x")
    (tmp_path / "Oud").mkdir()
    (tmp_path / "Oud" / "kademuur.esa").write_bytes(b"x")
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache" / "verborgen.esa").write_bytes(b"x")
    monkeypatch.setenv("LLSM_MODEL_DIRS", str(tmp_path))
    model_lookup.forget()
    yield tmp_path
    model_lookup.forget()


def test_unique_name_is_found(tree):
    assert resolve_model("brugdek.esa").path == (tree / "LLSM demo" / "brugdek.esa").resolve()


def test_extension_and_case_do_not_matter(tree):
    assert resolve_model("Brugdek").path == (tree / "LLSM demo" / "brugdek.esa").resolve()


def test_several_are_listed_newest_first(tree):
    os.utime(tree / "Oud" / "kademuur.esa", (1_000_000_000, 1_000_000_000))
    found = resolve_model("kademuur.esa")
    assert found.path is None and "Several models called kademuur.esa" in found.problem
    assert [c["path"] for c in found.candidates] == [
        str((tree / "Projecten" / "Kade 12" / "kademuur.esa").resolve()), str((tree / "Oud" / "kademuur.esa").resolve()),
    ]


def test_none_says_where_it_looked(tree):
    found = resolve_model("nergens.esa")
    assert found.path is None and f"No model called nergens.esa under {tree}" in found.problem


def test_hidden_folders_are_skipped(tree):
    assert resolve_model("verborgen.esa").path is None


def test_an_existing_path_is_used_and_its_folder_searched_first(tree, tmp_path_factory):
    other = tmp_path_factory.mktemp("elders")
    (other / "brugdek.esa").write_bytes(b"y")
    assert resolve_model(str(other / "brugdek.esa")).path == (other / "brugdek.esa").resolve()
    found = resolve_model("brugdek.esa")  # now in two places: the last model's folder and the tree
    assert found.path is None and len(found.candidates) == 2


def test_a_path_that_does_not_exist_is_not_searched(tree):
    assert resolve_model(str(tree / "elders" / "brugdek.esa")).problem.startswith("Model not found")
