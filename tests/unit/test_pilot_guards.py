"""The MCP server's door, the claude command line, and the diagnose report."""
import base64
import datetime as dt
import json
from pathlib import Path

import pytest

from llsm import diagnose
from llsm.bedding.apply import PlanRequest, apply_bedding, preview_bedding
from llsm.llm.claude_code_backend import ClaudeCodeBackend
from llsm.mcp_guard import refusal
from llsm.mcp_supervisor import McpHandle
from llsm.scia.esa_file import EsaFormatError, EsaModel, LineSupport, Member
from llsm.scia.esa_xml import RunResult

GOOD = {"host": "127.0.0.1:8765", "x-llsm-token": "secret"}


def test_good_request_passes():
    assert refusal(GOOD, "secret", 8765) is None


@pytest.mark.parametrize("headers", [{"host": "127.0.0.1:8765"}, {**GOOD, "x-llsm-token": "wrong"}])
def test_no_token_is_401(headers):
    assert refusal(headers, "secret", 8765)[0] == 401


@pytest.mark.parametrize("extra", [{"origin": "http://evil"}, {"sec-fetch-site": "cross-site"}, {"sec-fetch-mode": "no-cors"}])
def test_browser_requests_are_403_even_with_the_token(extra):
    assert refusal({**GOOD, **extra}, "secret", 8765)[0] == 403


def test_wrong_host_is_403():
    assert refusal({**GOOD, "host": "evil.example:8765"}, "secret", 8765)[0] == 403


def test_mcp_config_carries_the_token():
    handle = McpHandle(None, 8765, "secret", Path("a"), Path("b"))
    server = json.loads(handle.mcp_config_json)["mcpServers"]["llsm"]
    assert server["headers"] == {"X-LLSM-Token": "secret"}


def test_claude_only_gets_the_llsm_tools():
    command = ClaudeCodeBackend(mcp_config_json=None)._build_cmd("hello")
    assert "bypassPermissions" not in command
    assert command[command.index("--permission-mode") + 1] == "dontAsk"
    assert command[command.index("--allowedTools") + 1] == "mcp__llsm__*"


def test_the_prompt_is_not_swallowed_by_the_tool_list():
    command = ClaudeCodeBackend(mcp_config_json=None)._build_cmd("hello")
    following = command[command.index("--allowedTools") + 2]
    assert following.startswith("--") and command[-1] == "hello"


def token(tmp_path, name, expires):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": expires}).encode()).decode().rstrip("=")
    (tmp_path / name).write_text(f"header.{payload}.SECRETSIGNATURE", encoding="ascii")


NOW = dt.datetime(2026, 11, 9, tzinfo=dt.timezone.utc)


def test_valid_licence(tmp_path):
    token(tmp_path, "a.jwt", NOW.timestamp() + 3600)
    check = diagnose._licence(tmp_path, NOW)[0]
    assert check.status == diagnose.OK and "SECRET" not in check.detail


def test_expired_licence_says_what_to_do(tmp_path):
    token(tmp_path, "a.jwt", NOW.timestamp() - 3600)
    check = diagnose._licence(tmp_path, NOW)[0]
    assert check.status == diagnose.WARN and "open SCIA once" in check.detail


def test_no_token_warns(tmp_path):
    assert diagnose._licence(tmp_path, NOW)[0].status == diagnose.WARN


def test_unreadable_token_is_skipped(tmp_path):
    (tmp_path / "bad.jwt").write_text("nonsense", encoding="ascii")
    assert diagnose._licence(tmp_path, NOW)[0].status == diagnose.WARN


def test_writable_folder(tmp_path):
    assert diagnose._writable("x", tmp_path).status == diagnose.OK


def test_unwritable_folder_fails(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    assert diagnose._writable("x", blocker / "sub").status == diagnose.FAIL


def test_report_has_a_line_per_check():
    checks = [diagnose.Check("A", diagnose.OK, "fine"), diagnose.Check("Longer", diagnose.FAIL, "broken")]
    assert diagnose.format_report(checks).splitlines() == ["OK    A       fine", "FAIL  Longer  broken"]


def test_exit_code_follows_the_failures(monkeypatch, capsys):
    monkeypatch.setattr(diagnose, "run_checks", lambda folder: [diagnose.Check("A", diagnose.FAIL, "x")])
    assert diagnose.main([]) == 1
    monkeypatch.setattr(diagnose, "run_checks", lambda folder: [diagnose.Check("A", diagnose.WARN, "x")])
    assert diagnose.main(["--json"]) == 0
    assert '"status": "WARN"' in capsys.readouterr().out


EXPORT = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://www.scia.cz">
  <container t="n"><table t="EP_DSG_Elements.EP_StructNode.1">
    <h><h0 t="Name"/><h1 t="Coord X"/><h2 t="Coord Y"/><h3 t="Coord Z"/></h>
    <obj id="1" nm="T"><p1 v="0"/><p2 v="0"/><p3 v="-1"/></obj>
    <obj id="2" nm="E"><p1 v="0"/><p2 v="0"/><p3 v="-21"/></obj>
  </table></container>
  <container t="b"><table t="EP_DSG_Elements.EP_Beam.1">
    <h><h0 t="Name"/><h1 t="Beg. node"/><h2 t="End node"/></h>
    <obj id="7" nm="P1"><p1 n="T"/><p2 n="E"/></obj>
  </table></container>
</project>
"""
ROWS = [{"top": -2, "bottom": -8, "value": 5000, "unit": "kh_kN_m3", "description": "klei"}]
REQUEST = PlanRequest(diameter_m=0.45)
SAVED = LineSupport("LLSM:P1:1", 7, 0.05, 0.35, 2.25e6, 2.25e6)
OK = RunResult(0, "Succeeded", 1.0, "", "")


@pytest.fixture
def export(tmp_path):
    path = tmp_path / "export.xml"
    path.write_text(EXPORT, encoding="utf-8")
    return path


def refuses_source(after):
    def reader(path):
        if path.stem == "m":
            raise EsaFormatError("counts differ")
        if isinstance(after, Exception):
            raise after
        return after

    return reader


def write_output(source, output, xml):
    output.write_bytes(b"saved")
    return OK


def test_preview_falls_back_on_the_export(tmp_path, export):
    result = preview_bedding(tmp_path / "m.esa", ROWS, REQUEST, reader=refuses_source(None), geometry_from=export)
    assert result["ok"] and result["preview"][0]["pile"] == "P1"
    assert "Piles taken from export.xml" in " ".join(result["warnings"])


def test_without_an_export_the_refusal_stands(tmp_path):
    result = preview_bedding(tmp_path / "m.esa", ROWS, REQUEST, reader=refuses_source(None))
    assert not result["ok"] and "could not be read reliably" in result["problems"][0]


def test_apply_compares_by_name_after_an_export(tmp_path, export):
    (tmp_path / "m.esa").write_bytes(b"x")
    after = EsaModel([Member("P1", 7, (0, 0, -1), (0, 0, -21))], [SAVED], "+D_344_24.0.3015.64")
    result = apply_bedding(tmp_path / "m.esa", ROWS, REQUEST, reader=refuses_source(after), writer=write_output, geometry_from=export)
    assert result["ok"] and not result["other_changes"]
    assert "by name only" in " ".join(result["warnings"])


def test_a_saved_file_the_reader_refuses_is_not_ok(tmp_path, export):
    (tmp_path / "m.esa").write_bytes(b"x")
    result = apply_bedding(
        tmp_path / "m.esa", ROWS, REQUEST, reader=refuses_source(EsaFormatError("no")), writer=write_output, geometry_from=export
    )
    assert not result["ok"] and result["output"].endswith("_FAILED.esa")


def test_a_broken_export_is_a_problem(tmp_path):
    bad = tmp_path / "bad.xml"
    bad.write_text("<not xml", encoding="utf-8")
    result = preview_bedding(tmp_path / "m.esa", ROWS, REQUEST, reader=refuses_source(None), geometry_from=bad)
    assert not result["ok"] and "export could not be read" in result["problems"][0]
