"""The chat pane's markdown-lite parser: every block type and VibeModel's edge cases."""
from llsm.conversation.markdown_lite import (
    Blank, CodeBlock, ListBlock, Paragraph, Rule, Span, Table, parse, parse_inline, plain_text,
)


def kinds(text):
    return [type(b).__name__ for b in parse(text)]


def test_paragraph_keeps_its_line_breaks():
    (p,) = parse("Eerste regel.\nTweede regel.")
    assert isinstance(p, Paragraph)
    assert [plain_text(line) for line in p.lines] == ["Eerste regel.", "Tweede regel."]


def test_headings_one_to_three_deeper_ones_count_as_three():
    blocks = parse("# Een\n## Twee\n### Drie\n#### Vier")
    assert [(b.level, plain_text(b.spans)) for b in blocks] == [(1, "Een"), (2, "Twee"), (3, "Drie"), (3, "Vier")]
    assert kinds("#geen kop") == ["Paragraph"]


def test_nested_bullets_and_numbered_items_keep_their_numbers():
    (lst,) = parse("- a\n  - b\n    - c\n- d\n3. drie\n4. vier")
    assert isinstance(lst, ListBlock)
    assert [(i.level, i.marker, plain_text(i.lines[0])) for i in lst.items] == [
        (0, "•", "a"), (1, "•", "b"), (2, "•", "c"), (0, "•", "d"), (0, "3.", "drie"), (0, "4.", "vier")]
    assert [i.ordered for i in lst.items] == [False] * 4 + [True] * 2


def test_list_item_continuation_line_and_blank_between_items():
    (lst,) = parse("1. een\n   vervolg\n\n2. twee")
    assert [[plain_text(line) for line in i.lines] for i in lst.items] == [["een", "vervolg"], ["twee"]]


def test_fenced_code_with_language_is_raw_text():
    (code,) = parse("```python\nx = **1**\n  y\n```")
    assert code == CodeBlock("python", ("x = **1**", "  y"), True)


def test_tilde_fence_and_an_unclosed_fence_runs_to_the_end():
    assert parse("~~~\na | b\n|--|--|\n~~~") == [CodeBlock("", ("a | b", "|--|--|"), True)]
    (code,) = parse("```\nnog open\n\n")
    assert code.lines == ("nog open",) and not code.closed


def test_pipe_table_with_alignment_and_ragged_rows():
    (t,) = parse("| Laag | kh (kN/m³) | NAP |\n|:--|--:|:-:|\n| **Klei** | 5 000 |\n| Zand | 8000 | -8 | extra |")
    assert isinstance(t, Table)
    assert t.header == ("Laag", "kh (kN/m³)", "NAP")
    assert t.align == ("left", "right", "center")
    assert t.rows == (("Klei", "5 000", ""), ("Zand", "8000", "-8"))


def test_table_without_outer_pipes_and_escaped_pipe():
    (t,) = parse("a | b\n--- | ---\nx \\| y | 2")
    assert t.header == ("a", "b") and t.rows == (("x | y", "2"),)


def test_pipes_without_a_delimiter_row_stay_a_paragraph():
    assert kinds("a | b\nc | d") == ["Paragraph"]
    assert kinds("| a | b |\n| c | d |") == ["Paragraph"]


def test_horizontal_rule_and_blank():
    assert kinds("boven\n\n---\n\nonder") == ["Paragraph", "Blank", "Rule", "Blank", "Paragraph"]
    assert kinds("\n\ntekst\n\n") == ["Paragraph"]
    assert isinstance(parse("a\n\nb")[1], Blank) and isinstance(parse("***")[0], Rule)


def test_inline_bold_italic_code_and_link():
    spans = parse_inline("**vet** en *schuin* en _ook_ en `code` en [leidraad](https://x.nl/a)")
    assert [(s.text, sorted(s.styles), s.url) for s in spans if s.text.strip() != "en"] == [
        ("vet", ["bold"], ""), ("schuin", ["italic"], ""), ("ook", ["italic"], ""),
        ("code", ["code"], ""), ("leidraad", [], "https://x.nl/a")]


def test_bold_with_italic_inside():
    assert parse_inline("**a *b* c**") == [
        Span("a ", frozenset({"bold"})), Span("b", frozenset({"bold", "italic"})), Span(" c", frozenset({"bold"}))]


def test_inline_code_is_never_parsed_for_bold():
    assert parse_inline("zie `**niet vet**` hier") == [
        Span("zie "), Span("**niet vet**", frozenset({"code"})), Span(" hier")]
    assert parse_inline("``a ` b``") == [Span("a ` b", frozenset({"code"}))]


def test_a_lone_backtick_does_not_swallow_the_rest():
    assert parse_inline("Een ` los teken met **vet**") == [Span("Een ` los teken met "), Span("vet", frozenset({"bold"}))]
    text = "Een ` los.\n\nFase **2**.\n\nEn ` nog een."
    assert kinds(text) == ["Paragraph", "Blank", "Paragraph", "Blank", "Paragraph"]
    assert Span("2", frozenset({"bold"})) in parse(text)[2].lines[0]


def test_snake_case_names_are_not_italic():
    assert parse_inline("roep bedding_preview en set_line_load_magnitude aan") == [
        Span("roep bedding_preview en set_line_load_magnitude aan")]
    assert parse_inline("2 * 3 * 4") == [Span("2 * 3 * 4")]


def test_table_cells_lose_their_markers():
    (t,) = parse("| `naam` | **12** |\n|---|---|\n| [a](u) | *b* |")
    assert t.header == ("naam", "12") and t.rows == (("a", "b"),)


def test_mixed_answer_keeps_block_order():
    text = "## Kop\nTekst\n- a\n\n| x | y |\n|---|---|\n| 1 | 2 |\n\n```\ncode\n```\n---\nslot"
    assert [k for k in kinds(text) if k != "Blank"] == [
        "Heading", "Paragraph", "ListBlock", "Table", "CodeBlock", "Rule", "Paragraph"]
