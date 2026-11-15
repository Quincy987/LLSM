"""One real chat turn, as the panel runs it, against this build's MCP server: which tools does the model call?

  <venv python> chat_turn.py WORKDIR "prompt" [more prompts ...]

Starts the MCP server from this checkout (PYTHONPATH=src) with LLSM_MODEL_DIRS=WORKDIR, then runs the panel's own
chat backend (ClaudeCodeBackend: the claude CLI with only LLSM's tools and the panel's system prompt) for each
prompt in one session, recording every tool call and its result. Writes WORKDIR/chat_turn.json.

Exit 1 unless the traffic route was taken as an engineer's plain request needs: moving_load_preview and then
moving_load_apply, each with rule='toetsmodel' and no path_mm, step_mm or thickness_mm; and unless the model file
the apply wrote is really there, read back with its LLSM-ML groups and wheel patches (the chat's words are not
the evidence, the file is).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from llsm.llm.claude_code_backend import ClaudeCodeBackend  # noqa: E402
from llsm.mcp_guard import TOKEN_HEADER  # noqa: E402


def start_server(work: Path) -> tuple[subprocess.Popen, dict]:
    pid_file, port_file = work / "chat_server_pid.json", work / "chat_server_port.txt"
    for f in (pid_file, port_file):
        f.unlink(missing_ok=True)
    env = {**os.environ, "PYTHONPATH": str(REPO / "src"), "LLSM_MODEL_DIRS": str(work)}
    log = open(work / "chat_server.log", "wb")
    proc = subprocess.Popen([sys.executable, "-m", "llsm.mcp_server", "--http", "--port-file", str(port_file),
                             "--pid-file", str(pid_file)], env=env, stdout=log, stderr=log, cwd=REPO)
    for _ in range(60):
        if pid_file.exists() and pid_file.stat().st_size:
            return proc, json.loads(pid_file.read_text())
        time.sleep(1)
    raise SystemExit("MCP server did not start; see chat_server.log")


def written(work: Path) -> dict | None:
    """What the newest LLSM copy under ``work`` holds, read from the file itself; None when there is none."""
    from llsm.scia.esa_file import read_loads  # noqa: PLC0415
    copies = sorted(work.rglob("*_llsm-*.esa"), key=lambda p: p.stat().st_mtime)
    copies = [p for p in copies if not p.stem.endswith("_FAILED")]
    if not copies:
        return None
    model = read_loads(copies[-1])
    groups = [g.name for g in model.groups if g.name.startswith("LLSM-ML ")]
    patches = [s for s in model.surfaces if s.name.startswith("LLSM-ML ")]
    return {"file": str(copies[-1]), "groups": groups, "patches": len(patches)}


def main() -> int:
    work = Path(sys.argv[1]).resolve()
    prompts = sys.argv[2:]
    proc, info = start_server(work)
    calls: list[dict] = []

    def on_tool_event(phase, name, payload):
        calls.append({"phase": phase, "tool": name, "payload": payload if isinstance(payload, (dict, list, str)) else str(payload)})

    config = json.dumps({"mcpServers": {"llsm": {"type": "http", "url": f"http://127.0.0.1:{info['port']}/mcp",
                                                  "headers": {TOKEN_HEADER: info["token"]}}}}, separators=(",", ":"))
    backend = ClaudeCodeBackend(mcp_config_json=config, cwd=REPO, on_tool_event=on_tool_event)
    turns = []
    try:
        for prompt in prompts:
            started = time.time()
            reply = backend.chat(prompt)
            turns.append({"prompt": prompt, "reply": reply, "seconds": round(time.time() - started)})
            print(f"--- {prompt}\n{reply}\n({round(time.time() - started)} s)", flush=True)
    finally:
        req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/admin/shutdown", method="POST",
                                     headers={TOKEN_HEADER: info["token"], "Host": f"127.0.0.1:{info['port']}"})
        try:
            urllib.request.urlopen(req, timeout=10)
        except OSError as exc:
            print("shutdown failed:", exc)
        proc.wait(60)
    for call in calls:
        if call["phase"] in ("start", "call", "begin"):
            print("CALL", call["tool"], json.dumps(call["payload"], ensure_ascii=False)[:400])
    (work / "chat_turn.json").write_text(json.dumps({"turns": turns, "tool_events": calls}, indent=1, ensure_ascii=False),
                                         encoding="utf-8")
    started = [(c["tool"], c["payload"]) for c in calls if c["phase"] == "start" and isinstance(c["payload"], dict)]
    route = [(tool, args) for tool, args in started if tool in ("moving_load_preview", "moving_load_apply")]
    plain = all(args.get("rule") == "toetsmodel" and not ({"path_mm", "step_mm", "thickness_mm"} & set(args))
                for _, args in route)
    order = [tool for tool, _ in route]
    saved = written(work)
    ok = plain and order[:1] == ["moving_load_preview"] and "moving_load_apply" in order
    ok = ok and saved is not None and bool(saved["groups"]) and saved["patches"] > 0
    print("WRITTEN:", saved)
    print("RESULT:", "PASS" if ok else "FAIL", order)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
