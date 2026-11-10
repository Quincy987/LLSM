"""Pure layout helpers behind the chat table (ported from VibeModel's TableLayout tests)."""
import pytest

from llsm.conversation import table_layout as tl


@pytest.mark.parametrize("cells,expected", [
    (["3000", "12 345", "-1,5"], True),
    (["3000 mm", "2,5 m²", "45°", "≈ 300 kg"], True),
    (["5000 kN/m³", "8 000 kN/m3", "12,5 kNm"], True),
    (["3000", "", "—", "n.v.t."], True),
    (["1543814", "1543815"], True),
    (["3000", "Wand A"], False),
    (["3 wanden"], False),
    (["1:50"], False),
    (["", "-"], False),
    (["-1,1 tot -8,0"], False),
])
def test_numeric_columns(cells, expected):
    assert tl.is_numeric_column(cells) is expected


def test_numbers_grouped_with_non_breaking_spaces_are_numeric():
    assert tl.is_numeric_column(["12 345 mm", "1 000"])


def test_explicit_alignment_wins_over_numeric_detection():
    rows = [("a", "1", "2"), ("b", "3", "x")]
    assert tl.column_alignments(("n", "v", "w"), (None, "left", None), rows) == ["left", "left", "left"]
    assert tl.column_alignments(("n", "v", "w"), (None, None, None), rows) == ["left", "right", "left"]
    assert tl.column_alignments(("n", "v"), ("center",), rows) == ["center", "right"]


def test_tsv_has_header_and_rows_with_tabs_and_no_stray_whitespace():
    tsv = tl.to_tsv(["Naam", "Lengte (mm)"], [["W1\tx", "3000"], ["W2", " 4 500 "]])
    assert tsv == "Naam\tLengte (mm)\r\nW1 x\t3000\r\nW2\t4 500\r\n"
    assert tsv.count("\t") == 3


def seven(text, bold):
    return len(text) * 7.0


def test_measure_columns_min_is_the_longest_word_and_numbers_never_break():
    needs = tl.measure_columns(["Wand", "Lengte (mm)", "Opmerking"], [["W1", "12 000", "een heel lange opmerking"]],
                               [False, True, False], seven)
    ch = tl.CELL_CHROME
    assert needs[0].min == 4 * 7 + ch
    assert needs[1].min == 6 * 7 + ch          # "Lengte" / "12 000" whole
    assert needs[1].max == 11 * 7 + ch         # "Lengte (mm)" on one line
    assert needs[2].min == 9 * 7 + ch          # "opmerking"
    assert needs[2].max == 24 * 7 + ch
    assert [n.numeric for n in needs] == [False, True, False]


def test_fit_one_line_when_it_fits_else_words_first_else_proportional():
    needs = [tl.ColumnNeed(100, 150), tl.ColumnNeed(50, 50), tl.ColumnNeed(70, 170)]
    assert tl.fit(needs, 500) == [150, 50, 170]          # compact, not stretched
    mid = tl.fit(needs, 300)                            # 80 spare over the words
    assert mid == [126, 50, 123] and sum(mid) <= 300
    assert tl.fit(needs, 176) == [80, 40, 56]           # below the words, no numeric column: proportional
    needs[1].numeric = True
    assert tl.fit(needs, 176) == [74, 50, 51]           # "35,41" stays whole, the text gives way
    assert tl.fit(needs, 80) == [36, 18, 25]            # numbers over half the room: proportional


def test_wrap_cell_by_words_then_by_characters():
    assert tl.wrap_cell("een twee drie", 100, seven) == ["een twee drie"]
    assert tl.wrap_cell("een twee drie", 50, seven) == ["een", "twee", "drie"]
    assert tl.wrap_cell("een twee drie", 70, seven) == ["een twee", "drie"]
    assert tl.wrap_cell("Beton_Metselwerk", 49, seven) == ["Beton_M", "etselwe", "rk"]
    assert tl.wrap_cell("", 50, seven) == [""]
    for line in tl.wrap_cell("a bb ccc dddddddddddd", 35, seven):
        assert seven(line, False) <= 35


def test_hidden_rows_only_beyond_thirty():
    assert tl.hidden_row_count(30) == 0
    assert tl.hidden_row_count(45) == 15
