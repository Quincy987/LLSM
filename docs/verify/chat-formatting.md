# Chat formatting verification

Date: 2026-11-10. Claude Code 2.1.318 (Opus) as the backend, the real panel driven in-process
(`demo_out/fmt/verify_panel.py`, screenshots by PrintWindow of the panel window). No SCIA was
launched: the table question ran `bedding_preview` on a copy of the demo model, the others were
chat-only.

## Results

| Check | Result | Evidence |
|---|---|---|
| Table with units from a tool result | PASS | `bedding_preview` answer: bold header on a fill, alternating rows, numeric columns (kh, k lijn) right-aligned, the NAP range column left-aligned, units in the header cells. [table-answer](img/chat-formatting-table-answer.png) |
| Row fill stops at the table edge | PASS | Narrow table in a wide pane: the fill ends at the last column. [long-answer](img/chat-formatting-long-answer.png) |
| Refit on resize | PASS | Sidebar on and window at its minimum width: the tab stops went from 11/106/355/480 px to 11/70/206/292 px, text wraps inside its cell, numbers stay whole, no sideways scrolling. [table-narrow](img/chat-formatting-table-narrow.png) |
| Kopiëren puts TSV on the clipboard | PASS | Clicked through a real `<Button-1>` on the link, clipboard read back with win32clipboard: 4 lines (header + 3 rows), 12 tabs = 4 × 3, CRLF line ends, `–`, `³`, `²` intact. The link reads "Gekopieerd" for 1.5 s. [table-copied](img/chat-formatting-table-copied.png) |
| Activity block while running | PASS | "◐ Bezig… 0:06" with "▶ bedding_preview (model_path=…, layers=…) · 0:01", fed by the stream-json events of the claude CLI. [activity-running](img/chat-formatting-table-activity-running.png) |
| Activity block after the turn | PASS | Collapsed to "▸ 2 stappen · 19 s" above the answer; turns without tools leave no block. [table-narrow](img/chat-formatting-table-narrow.png), [code-answer](img/chat-formatting-code-answer.png) |
| Code block and nested list | PASS | Monospace on a tint with a thin border; numbered list keeps its numbers, sub-items indented with "◦", inline code as pills. [code-answer](img/chat-formatting-code-answer.png) |
| Long table folds at 30 rows | PASS | 40 rows: P1–P30 shown, "… nog 10 rijen" next to Kopiëren; a click shows all 40. [long-folded](img/chat-formatting-long-folded.png), [long-unfolded](img/chat-formatting-long-unfolded.png) |
| Long code block folds at 20 lines | PASS | Harness (`demo_out/fmt/check_fold_copy.py`): 25 lines show 12 and "… nog 13 regels"; a click shows 25 and the fold line goes. |
| Selecting and copying text | PASS | Same harness: Ctrl+C on a selection copies table rows as `Laag\tkh (kN/m³)`, without the leading tab, and inline code without its padding spaces. |
| History → resume renders the same | PASS | Resuming the conversation re-rendered both tables (2 before, 2 after) through the same renderer. [resumed](img/chat-formatting-resumed-first-answer.png) |
| Dark theme | PASS | Harness with a dark background: fills, code tint, links and activity colours follow the theme colours. Not run in the full panel (it has no dark ttk theme). |
| Unit tests | PASS | `pytest tests/unit -q`: 245 passed (204 before; the four old `_format_table` tests replaced). |
| `ruff check --select E9,F63,F7,F82 .` | PASS | All checks passed. |

## Notes

- Tables are tab stops on pre-wrapped cells in the one Text widget, not embedded Frame/Label grids:
  text stays selectable and copyable, the mouse wheel keeps scrolling, and a 40-row table stays one
  widget. A Tk detail found on the way: with `wrap="none"` Tk ignores the right margin and the row
  fill runs to the pane edge, so table lines use the normal wrap and fit by construction.
- The timestamps of resumed messages show the stored UTC time (02:12 for a 03:12 message). That
  is the existing replay code, unchanged here.
