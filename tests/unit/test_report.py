"""The report tool: the XLSX check on SCIA's real layout, the .esa report reader, naming, the flow.

The fixtures in data/report are cut from the spike's exports (docs/verify/report-route.md): a
calculated plate whose report prints results, and a v19.1 model whose results items are of the
old type ("Geen ondersteunde taak").
"""
import hashlib
import struct
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path

import pytest

from llsm.report import ReportRequest, cli, preview_report, write_report
from llsm.report.check import check_xlsx, parse_table
from llsm.report.xlsx import read_sheets
from llsm.scia.esa_file import LoadCase, LoadGroup, LoadModel, ReportIndex, StoredReport, read_report_index
from llsm.scia.esa_xml import RunResult
from llsm.scia.load_xml import build_case_classes_update
from llsm.scia.output_files import next_output_set

DATA = Path(__file__).parent / "data" / "report"
OK = RunResult(0, "Succeeded", 1.0, "", "")


# --- the check ---------------------------------------------------------------------------------

def test_plate_report_holds_results_with_the_spikes_values():
    check = check_xlsx(DATA / "plate.xlsx")
    assert check.ok, check.problems
    assert check.summary() == {"expected": 4, "written": 4, "empty": 0, "unsupported": 0, "empty_classes": [], "input_tables": 2}
    forces = next(t for t in check.tables if "Interne 1D" in t.title and t.result_class == "Alle UGT")
    assert dict(zip(forces.column("Naam"), forces.column("N"))) == {"C3": -217.32, "C1": -232.68, "C2": -217.32, "C4": -232.68}
    assert forces.units[:2] == ["[m]", "[kN]"] and forces.settings["Selectie"] == "Alle"
    resultant = next(t for t in check.tables if t.title.startswith("Resultante"))
    assert resultant.column("R_{z}") == [900]


def test_old_type_results_items_are_refused():
    check = check_xlsx(DATA / "old_type.xlsx")
    assert not check.ok and check.summary()["unsupported"] == 3 and check.written == 0
    assert "Geen ondersteunde taak" in check.problems[0]


def test_empty_input_table_is_not_a_failure_but_an_empty_results_table_is(tmp_path):
    rows_ok = check_xlsx(DATA / "plate.xlsx")
    assert rows_ok.ok  # 2.4.4 "Lege tabel" is an input chapter
    path = _workbook(tmp_path, {"3.1.1. Reacties": [["Lege tabel"]], "3.1.2. Reacties": _table_rows([["Sn1", 1.0]])})
    check = check_xlsx(path)
    assert not check.ok and check.empty == ["3.1.1. Reacties"]


def test_results_table_without_data_rows_is_empty(tmp_path):
    check = check_xlsx(_workbook(tmp_path, {"3.1. Reacties": _table_rows([])}))
    assert not check.ok and check.summary()["empty"] == 1


def test_report_without_results_chapters_is_refused(tmp_path):
    check = check_xlsx(_workbook(tmp_path, {"2.1. Materialen": [["Naam", "E"], ["C20/25", 30000]]}))
    assert not check.ok and "no results chapters" in check.problems[0]


def test_unreadable_xlsx_is_a_problem_not_a_crash(tmp_path):
    bad = tmp_path / "x.xlsx"
    bad.write_bytes(b"not a zip")
    assert not check_xlsx(bad).ok


def test_settings_lines_and_subheading_are_parsed():
    table = parse_table("3.2.1.1.1. Reacties", _table_rows([["Sn1", 232.68]], subheading="Knoopreacties"))
    assert table.settings == {"Klasse": "Alle UGT", "Extreem": "Globaal", "Selectie": "Alle"}
    assert table.columns == ["Naam", "R_{z}"] and table.rows == [["Sn1", 232.68]]


def test_stdlib_reader_reads_strings_numbers_and_order():
    sheets = read_sheets(DATA / "plate.xlsx")
    assert sheets[0][0].startswith("3.3.1.2.1.")  # workbook order: SCIA writes chapters in reverse
    flat = [c for _, rows in sheets for row in rows for c in row]
    assert -232.68 in flat and "Lineaire berekening" in flat


def _table_rows(data, subheading=None):
    rows = [["Lineaire berekening"], ["Klasse: Alle UGT"], ["Extreem: Globaal"], ["Selectie: Alle"]]
    if subheading:
        rows.append([subheading])
    return rows + [["Naam", "R_{z}"], ["[kN]"], *data, ["Naam", "Combinatiesleutel"], ["BGT1/1", "BG1"]]


def _workbook(tmp_path, sheets: dict[str, list[list]]) -> Path:
    """A minimal .xlsx with inline strings, the way any writer may store them."""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    path = tmp_path / "book.xlsx"
    with zipfile.ZipFile(path, "w") as z:
        entries = "".join(
            f'<sheet name="{name}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, name in enumerate(sheets)
        )
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/'
                   f'officeDocument/2006/relationships"><sheets>{entries}</sheets></workbook>')
        rels = "".join(
            f'<Relationship Id="rId{i + 1}" Target="worksheets/sheet{i + 1}.xml" Type="x"/>' for i in range(len(sheets))
        )
        z.writestr("xl/_rels/workbook.xml.rels",
                   f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>')
        for i, rows in enumerate(sheets.values()):
            body = ""
            for r, row in enumerate(rows, 1):
                cells = ""
                for c, value in enumerate(row):
                    ref = f"{chr(65 + c)}{r}"
                    if isinstance(value, (int, float)):
                        cells += f'<c r="{ref}"><v>{value}</v></c>'
                    else:
                        cells += f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'
                body += f'<row r="{r}">{cells}</row>'
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", f'<worksheet xmlns="{ns}"><sheetData>{body}</sheetData></worksheet>')
    return path


# --- the .esa reader ---------------------------------------------------------------------------

def _esa_with_report(tmp_path, names=("Report_1",), results=4, cases=4, nonlinear=0) -> Path:
    blocks = []
    for name in names:
        blocks.append(f'<?xml version="1.0"?><DOCUMENTVIEWDATASTORAGE_DOCUMENTINFO Version="1"><Name>{name}</Name>'
                      f'<CountOfPages>54</CountOfPages></DOCUMENTVIEWDATASTORAGE_DOCUMENTINFO>'.encode())
        blocks.append(b'<?xml version="1.0"?><DOCUMENTVIEWDATASTORAGE_ITEMS_ORDER Version="2">'
                      + b'<DocumentItem Id="1"></DocumentItem>' * 7 + b"</DOCUMENTVIEWDATASTORAGE_ITEMS_ORDER>")
    log = (f"- 1 - {results} - EP_Results.IntForcesAddCases.1 - x\n- 1 - {cases} - DataSetScia.EP_LoadCase.1 - x\n"
           f"- 1 - {nonlinear} - DataSetSciaTom.EP_NonlinearCombi.1 - x\n").encode()
    stamp = bytes(32) + "+D_344_24.0.3015.64".encode("utf-16le") + bytes(32)
    path = tmp_path / "m.esa"
    path.write_bytes(b"SEN\x00" + bytes(40) + b"".join(zlib.compress(b) + bytes(9) for b in [*blocks, log + stamp]))
    return path


def test_reports_counts_and_version_are_read_from_the_file(tmp_path):
    index = read_report_index(_esa_with_report(tmp_path, names=("Report_3", "Report_2")))
    assert [(r.name, r.pages, r.items) for r in index.reports] == [("Report_3", 54, 7), ("Report_2", 54, 7)]
    assert index.result_sets == 4 and index.saved_by == "+D_344_24.0.3015.64" and not index.nonlinear


# --- naming and the combination XML ----------------------------------------------------------------

def test_report_names_keep_the_whole_stem_and_share_one_number(tmp_path):
    model = tmp_path / "brugdek_llsm-01.esa"
    first = next_output_set(model, "rapport", ["xlsx", "pdf"])
    assert first["pdf"].name == "brugdek_llsm-01_rapport-01.pdf" and first["xlsx"].name == "brugdek_llsm-01_rapport-01.xlsx"
    first["xlsx"].with_name("brugdek_llsm-01_rapport-01_FAILED.xlsx").write_bytes(b"x")
    assert next_output_set(model, "rapport", ["xlsx", "pdf"])["pdf"].name == "brugdek_llsm-01_rapport-02.pdf"


def test_case_classes_xml_has_one_combination_and_one_class_per_case():
    root = ET.fromstring(build_case_classes_update(["LLSM-ML TS1 01", "LLSM-ML TS1 02"]))
    ns = {"s": "http://www.scia.cz"}
    combos = root.findall(".//s:table[@t='DataSetSciaTom.EP_LoadCombi.1']/s:obj", ns)
    classes = root.findall(".//s:table[@t='DataSetSciaTom.EP_SelectedLC.1']/s:obj", ns)
    assert [c.get("nm") for c in combos] == [c.get("nm") for c in classes] == ["LLSM-ML TS1 01", "LLSM-ML TS1 02"]
    assert combos[0].find("s:p1", ns).get("v") == "3"  # Lineair - BGT
    assert combos[0].find("s:p2/s:row/s:p2", ns).get("n") == "LLSM-ML TS1 01"
    assert classes[1].find("s:p1/s:row/s:p0", ns).get("i") == combos[1].get("id")
    assert int(combos[0].get("id")) > 9000  # far from SCIA's own ids
    with pytest.raises(ValueError):
        build_case_classes_update(["A", "A"])


# --- the flow, with a fake ESA_XML ---------------------------------------------------------------

def index(reports=("Report_1",), results=4, cases=4, nonlinear=0):
    counts = {"EP_Results.IntForcesAddCases.1": results, "DataSetScia.EP_LoadCase.1": cases}
    if nonlinear:
        counts["DataSetSciaTom.EP_NonlinearCombi.1"] = nonlinear
    return ReportIndex([StoredReport(n, 54, 53) for n in reports], counts, "+D_344_24.0.3015.64")


MOVING = LoadModel([], [], [], [LoadGroup("LLSM-ML TS1", 1, 1)],
                   [LoadCase("BG1", 0), *(LoadCase(f"LLSM-ML TS1 0{i}", 1) for i in (1, 2, 3))], [])


class Fake:
    """ESA_XML: records the calls and writes what a run would write."""

    def __init__(self, tmp_path, xlsx=DATA / "plate.xlsx", idx=None, fail_at=None):
        self.model = tmp_path / "deck.esa"
        self.model.write_bytes(b"original model")
        self.xlsx, self.idx, self.fail_at, self.calls = xlsx, idx or index(), fail_at, []

    def runner(self, source, folder, calc, switches, timeout):
        self.calls.append((Path(source).name, calc, switches))
        assert Path(source) != self.model or calc == "NONE" or calc == "LIN"
        if self.fail_at is not None and len(self.calls) - 1 == self.fail_at:
            return RunResult(9, "Error while creating XML outputs", 1.0, "", "standaard document wordt gemist")
        out = next((s[2:] for s in switches if s.startswith("-o")), None)
        if calc == "LIN":
            Path(out).write_bytes(b"calculated")
        elif out.endswith(".xlsx"):
            Path(out).write_bytes(self.xlsx.read_bytes())
        else:
            Path(out).write_bytes(b"%PDF report")
        return OK

    def write(self, **request):
        return write_report(self.model, ReportRequest(**request), runner=self.runner,
                            index_reader=lambda p: self.idx, load_reader=lambda p: MOVING)


def test_preview_touches_nothing_and_plans_one_numbered_pair(tmp_path):
    fake = Fake(tmp_path)
    result = preview_report(fake.model, ReportRequest(), index_reader=lambda p: fake.idx)
    assert result["ok"] and result["report"] == "Report_1" and result["formats"] == ["xlsx", "pdf"]
    assert Path(result["outputs_planned"]["pdf"]).name == "deck_rapport-01.pdf"
    assert result["saved_by"] and [p.name for p in tmp_path.iterdir()] == ["deck.esa"]


def test_good_print_is_ok_and_leaves_the_model_alone(tmp_path):
    fake = Fake(tmp_path)
    before = hashlib.sha256(fake.model.read_bytes()).hexdigest()
    result = fake.write()
    assert result["ok"], result["problems"]
    assert result["chapters"]["written"] == result["chapters"]["expected"] == 4
    assert {k: Path(v).name for k, v in result["outputs"].items()} == {"xlsx": "deck_rapport-01.xlsx", "pdf": "deck_rapport-01.pdf"}
    assert [c[1] for c in fake.calls] == ["NONE", "NONE"] and "-dReport_1" in fake.calls[0][2]
    assert "-tXLSX" in fake.calls[0][2] and "-tPDF" in fake.calls[1][2]  # the check first
    assert hashlib.sha256(fake.model.read_bytes()).hexdigest() == before


def test_old_type_report_stops_before_the_pdf_and_is_marked_failed(tmp_path):
    fake = Fake(tmp_path, xlsx=DATA / "old_type.xlsx")
    result = fake.write()
    assert not result["ok"] and len(fake.calls) == 1
    assert any("Geen ondersteunde taak" in p for p in result["problems"])
    assert any("standard LLSM report template is not available yet" in p for p in result["problems"])
    assert Path(result["outputs"]["xlsx"]).name == "deck_rapport-01_FAILED.xlsx" and "pdf" not in result["outputs"]


def test_calculate_with_cases_group_writes_the_classes_on_the_copy(tmp_path):
    fake = Fake(tmp_path, idx=index(results=0))
    result = fake.write(calculate=True, cases_group="LLSM-ML TS1")
    assert result["ok"], result["problems"]
    assert result["cases"] == ["LLSM-ML TS1 01", "LLSM-ML TS1 02", "LLSM-ML TS1 03"]
    lin = fake.calls[0]
    assert lin[1] == "LIN" and lin[0] == "deck.esa" and lin[2][0].endswith("classes.xml")
    assert [c[0] for c in fake.calls[1:]] == ["calculated.esa", "calculated.esa"]  # printed from the calculated copy
    assert any("copy only" in w for w in result["warnings"])
    assert fake.model.read_bytes() == b"original model"


@pytest.mark.parametrize(
    "request_args, idx, expected",
    [
        ({"report_name": "Report_9"}, index(), "No report named 'Report_9'"),
        ({}, index(reports=()), "no engineering report"),
        ({}, index(reports=("Report_2", "Report_3")), "say which one"),
        ({}, index(results=0), "no results stored"),
        ({"cases_group": "LLSM-ML TS1"}, index(), "needs calculate"),
        ({"calculate": True, "cases_group": "LLSM-ML TS9"}, index(), "not found"),
        ({"calculate": True}, index(nonlinear=2), "nonlinear combinations"),
        ({"formats": ["docx"]}, index(), "Unknown formats"),
    ],
)
def test_refusals(tmp_path, request_args, idx, expected):
    fake = Fake(tmp_path, idx=idx)
    result = fake.write(**request_args)
    assert not result["ok"] and expected in " ".join(result["problems"]) and not fake.calls


def test_stale_results_are_a_warning(tmp_path):
    result = Fake(tmp_path, idx=index(results=1, cases=4)).write()
    assert result["ok"] and "4 load cases but only 1 stored result sets" in result["warnings"][0]


def test_esa_xml_failure_leaves_no_report(tmp_path):
    fake = Fake(tmp_path, fail_at=0)
    result = fake.write()
    assert not result["ok"] and "standaard document wordt gemist" in result["problems"][0]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["deck.esa"]


def test_cli_report_prints_counts_and_outputs(tmp_path, capsys):
    result = Fake(tmp_path).write()
    assert cli._report(result) == 0
    out = capsys.readouterr().out
    assert "Results tables: 4 of 4 with results" in out and "deck_rapport-01.pdf" in out and out.rstrip().endswith("OK")


def test_cli_parses_formats_and_flags():
    args = cli._parser().parse_args(["write", "m.esa", "--format", "pdf", "--format", "rtf", "--calculate", "--cases-group", "G"])
    assert args.formats == ["pdf", "rtf"] and args.calculate and args.cases_group == "G"


def test_class_without_combinations_is_reported_apart_and_does_not_fail():
    """The template's "Alle UGT" on the moving-load copy: SCIA prints E-C04 and no data."""
    check = check_xlsx(DATA / "empty_class.xlsx")
    assert check.ok, check.problems
    assert check.empty_classes == {"Alle UGT": ["3.2.1.1.1. Reacties", "3.2.1.1.2. Resultante van react"]}
    case = next(t for t in check.tables if t.result_class == "LLSM-ML TS1 01" and t.title.startswith("Resultante"))
    assert case.column("R_{z}") == [600]
    empty = next(t for t in check.tables if t.result_class == "Alle UGT")
    assert empty.messages == ["Fout E-C04: Niet alle items in deze klasse zijn berekend."]


def test_empty_class_becomes_a_warning_in_the_result(tmp_path):
    result = Fake(tmp_path, xlsx=DATA / "empty_class.xlsx").write()
    assert result["ok"] and any("'Alle UGT' has no combinations" in w for w in result["warnings"])


# --- jobs (the MCP route: a print outlives one tool call) ----------------------------------------

import threading  # noqa: E402

from llsm.report import jobs  # noqa: E402


def _drain():
    for job in list(jobs._jobs.values()):
        job.done.wait(5)
    jobs._jobs.clear()


def test_job_runs_in_the_background_and_wait_returns_the_result():
    _drain()
    gate = threading.Event()

    def work(progress):
        progress("printing XLSX")
        gate.wait(5)
        return {"ok": True, "outputs": {"pdf": "x.pdf"}}

    job = jobs.start("m.esa", work)
    first = jobs.wait(job.id, 0.05)
    assert first["status"] == "running" and first["step"] == "printing XLSX" and first["ok"] is None
    with pytest.raises(RuntimeError, match="already being printed"):
        jobs.start("other.esa", work)
    gate.set()
    done = jobs.wait(job.id, 5)
    assert done["status"] == "done" and done["ok"] and done["outputs"] == {"pdf": "x.pdf"}
    _drain()


def test_a_crashing_print_ends_its_job_with_a_problem():
    _drain()

    def work(progress):
        raise ValueError("boom")

    job = jobs.start("m.esa", work)
    result = jobs.wait(job.id, 5)
    assert result["status"] == "done" and not result["ok"] and "boom" in result["problems"][0]
    _drain()


def test_unknown_job_and_wait_is_capped():
    assert jobs.wait("report-999", 0)["status"] == "unknown"
    assert jobs.MAX_WAIT_S < 300  # under the CLI's 300 s per MCP call
