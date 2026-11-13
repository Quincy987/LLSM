"""Print the sheets (in report order) and first rows of an engineering-report XLSX. Read-only.

  python dump_xlsx.py FILE.xlsx [max_rows]

SCIA writes one sheet per report table, titled with the chapter number and cut at Excel's
31 characters, in reverse order; this prints them sorted back into chapter order.
"""
import io
import sys
from pathlib import Path

import openpyxl

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
wb = openpyxl.load_workbook(Path(sys.argv[1]), read_only=True, data_only=True)
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 40
for ws in reversed(wb.worksheets):
    rows = list(ws.iter_rows(values_only=True))
    print(f"=== sheet {ws.title!r}: {len(rows)} rows")
    for r in rows[:limit]:
        cells = [c for c in r if c not in (None, "")]
        if cells:
            print("  ", " | ".join(str(c) for c in cells)[:220])
