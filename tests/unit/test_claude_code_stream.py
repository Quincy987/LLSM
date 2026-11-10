"""Tool steps read from `claude -p --output-format stream-json` for the activity block."""
import io
import json

from llsm.llm.claude_code_backend import ClaudeCodeBackend, parse_stream_line, tool_events


def line(obj):
    return json.dumps(obj) + "\n"


def test_tool_use_starts_and_tool_result_ends_a_step():
    pending = {}
    start = parse_stream_line(line({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "even kijken"},
        {"type": "tool_use", "id": "t1", "name": "mcp__llsm__bedding_preview", "input": {"path": "a.esa"}}]}}))
    assert tool_events(start, pending) == [("start", "bedding_preview", {"path": "a.esa"})]
    end = parse_stream_line(line({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": '{"ok": true, "rows": []}'}]}]}}))
    assert tool_events(end, pending) == [("end", "bedding_preview", {"ok": True, "error": ""})]
    assert pending == {}


def test_a_tools_own_ok_false_and_an_mcp_error_both_fail_the_step():
    pending = {"a": "bedding_apply", "b": "run_analysis"}
    event = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "a", "content": '{"ok": false, "error": "file is open"}'},
        {"type": "tool_result", "tool_use_id": "b", "is_error": True, "content": [{"type": "text", "text": "Error: timeout"}]}]}}
    assert tool_events(event, pending) == [
        ("end", "bedding_apply", {"ok": False, "error": "file is open"}),
        ("end", "run_analysis", {"ok": False, "error": "Error: timeout"})]


def test_other_lines_are_ignored():
    assert parse_stream_line("not json") is None
    assert tool_events({"type": "system", "subtype": "init"}, {}) == []
    assert tool_events({"type": "user", "message": {"content": "plain text"}}, {}) == []


def test_the_backend_streams_reports_steps_and_returns_the_result(monkeypatch):
    events = []
    backend = ClaudeCodeBackend(on_tool_event=lambda *e: events.append(e))
    cmd = backend._build_cmd("hoi")
    assert cmd[cmd.index("--output-format") + 1] == "stream-json" and "--verbose" in cmd
    out = "".join([
        line({"type": "system", "subtype": "init"}),
        line({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "x", "name": "mcp__llsm__model_summary", "input": {}}]}}),
        line({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "x", "content": "{}"}]}}),
        line({"type": "result", "result": "Klaar.", "session_id": "s"}),
    ])

    class FakeProc:
        def __init__(self, *a, **k):
            self.stdout = io.StringIO(out)
            self.stderr = io.StringIO("")
            self.returncode = None

        def wait(self):
            self.returncode = 0
            return 0

        def poll(self):
            return self.returncode

        def kill(self):
            pass

    monkeypatch.setattr("llsm.llm.claude_code_backend.subprocess.Popen", FakeProc)
    assert backend.chat("hoi") == "Klaar."
    assert events == [("start", "model_summary", {}), ("end", "model_summary", {"ok": True, "error": ""})]
