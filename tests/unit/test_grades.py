"""Material grades matched to SCIA's library: what the engineer says becomes a name SCIA knows."""
import pytest

from llsm.scia.grades import GradeError, kind_of, match, resolve

LIBRARY = ("C20/25", "C30/37", "C45/55", "C30/37(EN1992-2)", "C30/37-30kg/m3-5D65/60BG", "S 235", "S 355",
           "S 355 J2 (EN 10025-2)", "C24 (EN 338)", "C30 (EN 338)", "GL 24h (EN 14080)", "B 500B")


@pytest.mark.parametrize("typed,name", [("C30/37", "C30/37"), ("c30/37", "C30/37"), ("C 30/37", "C30/37"),
                                        ("S235", "S 235"), ("s 355", "S 355"), ("C24", "C24 (EN 338)")])
def test_grades_are_found_ignoring_case_and_spaces(typed, name):
    assert match(typed, LIBRARY) == name


def test_an_unknown_grade_is_refused_with_the_closest():
    with pytest.raises(GradeError, match="Closest: C30/37"):
        match("C31/37", LIBRARY)


def test_kind_follows_from_the_grade():
    assert kind_of("C30/37") == "Concrete" and kind_of("LC25/28") == "Concrete"
    assert kind_of("S 355") == "Steel" and kind_of("C24 (EN 338)") == "Timber"
    assert kind_of("B 500B") is None


def test_a_name_that_is_a_grade_is_its_own_grade():
    assert resolve("C30/37", None, None, LIBRARY) == ("Concrete", "C30/37", True)
    assert resolve("S235", None, None, LIBRARY) == ("Steel", "S 235", True)


def test_a_named_material_with_a_grade():
    assert resolve("Beton ligger", None, "C30/37", LIBRARY) == ("Concrete", "C30/37", True)
    assert resolve("Beton ligger", "Concrete", "c 30/37", LIBRARY) == ("Concrete", "C30/37", True)


def test_a_kind_against_the_grade_is_refused():
    with pytest.raises(GradeError, match="concrete grade, not steel"):
        resolve("Staal", "Steel", "C30/37", LIBRARY)
    with pytest.raises(GradeError, match="timber grade, not concrete"):
        resolve("C30", "Concrete", None, LIBRARY)  # C30 alone is a timber class


def test_without_a_grade_the_material_is_ungraded():
    assert resolve("Beton", "Concrete", None, LIBRARY) == ("Concrete", None, False)
    with pytest.raises(GradeError, match="say what the material is"):
        resolve("Beton", None, None, LIBRARY)


def test_a_grade_whose_kind_is_unclear_needs_the_kind():
    with pytest.raises(GradeError, match="give the kind"):
        resolve("Wapening", None, "B500B", LIBRARY)
    assert resolve("Wapening", "Steel", "B500B", LIBRARY) == ("Steel", "B 500B", True)


def test_several_matches_ask_for_the_full_name():
    with pytest.raises(GradeError, match="several"):
        match("S355J2", ("S 355 J2 (EN 10025-2)", "S 355 J2 (EN 10025-6)"))


def test_without_a_library_the_grade_is_kept_unchecked():
    assert resolve("C30/37", None, None, ()) == ("Concrete", "C30/37", False)
    assert resolve("Ligger", None, "S235", ()) == ("Steel", "S235", False)


def test_an_unknown_kind_is_refused():
    with pytest.raises(GradeError, match="unknown material kind"):
        resolve("X", "Stone", None, LIBRARY)
