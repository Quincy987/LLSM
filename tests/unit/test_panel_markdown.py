"""Pipe tables in chat answers become aligned text instead of raw bars."""
from llsm.panel import _format_table, _is_table_row


def test_table_is_aligned_and_the_rule_dropped():
    rows = ["| Laag | NAP | kh |", "|---|---|---|", "| Klei | -1,1 m tot -8,0 m | 5000 kN/m³ |", "| Zand | -8 m tot -14 m | 8000 kN/m³ |"]
    assert _format_table(rows) == [
        "Laag  NAP                kh",
        "Klei  -1,1 m tot -8,0 m  5000 kN/m³",
        "Zand  -8 m tot -14 m     8000 kN/m³",
    ]


def test_ragged_rows_are_padded():
    assert _format_table(["| a | b |", "| c |"]) == ["a  b", "c"]


def test_alignment_markers_in_the_rule_are_recognised():
    assert _format_table(["| x | y |", "|:--|--:|", "| 1 | 2 |"]) == ["x  y", "1  2"]


def test_only_pipe_lines_count_as_rows():
    assert _is_table_row("| a | b |") and not _is_table_row("a | b") and not _is_table_row("|")
