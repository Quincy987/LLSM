"""Verify rule='toetsmodel' through the app: the real MCP server, called the way the panel's chat calls it.

  python verify_app_rule.py WORKDIR

Needs SCIA closed. Copies the demo deck (brugdek.esa: plate D1 6 x 4 m on four columns) into WORKDIR and
never touches the original. Runs moving_load_preview and moving_load_apply with rule='toetsmodel', then the
same apply on its own output without replace (must be refused), and shuts the server down through
/admin/shutdown. Writes WORKDIR/verify_app_rule.json.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

REPO = Path(__file__).resolve().parents[2]
SOURCE = Path(r"C:\Users\klerk015\Documents\LLSM demo\brugdek.esa")
ARGS = {"path_mm": [[0, 2000], [6000, 2000]], "rule": "toetsmodel", "thickness_mm": 300}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def start_server(work: Path) -> tuple[subprocess.Popen, dict]:
    pid_file, port_file = work / "server_pid.json", work / "server_port.txt"
    for f in (pid_file, port_file):
        f.unlink(missing_ok=True)
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    log = open(work / "server.log", "wb")
    proc = subprocess.Popen([sys.executable, "-m", "llsm.mcp_server", "--http", "--port-file", str(port_file),
                             "--pid-file", str(pid_file)], env=env, stdout=log, stderr=log, cwd=work)
    for _ in range(60):
        if pid_file.exists() and pid_file.stat().st_size:
            return proc, json.loads(pid_file.read_text())
        time.sleep(1)
    raise SystemExit("MCP server did not start; see server.log")


def summary(data: dict) -> dict:
    keep = ("ok", "planned_cases", "planned_loads", "written", "expected", "cases_confirmed", "output", "seconds",
            "groups", "contact", "problems", "mismatches", "other_changes", "notes")
    return {k: data.get(k) for k in keep}


async def runs(info: dict, model: Path) -> dict:
    out = {}
    url = f"http://127.0.0.1:{info['port']}/mcp"
    async with streamablehttp_client(url, headers={"X-LLSM-Token": info["token"]}, sse_read_timeout=3600) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            schema = next(t for t in tools if t.name == "moving_load_apply").inputSchema["properties"]
            out["apply_arguments"] = sorted(schema)

            async def call(name, args):
                started = time.time()
                res = await session.call_tool(name, args, read_timeout_seconds=timedelta(minutes=30))
                data = json.loads(res.content[0].text)
                print(f"{name}: ok={data['ok']} {round(time.time() - started)} s; cases={data['planned_cases']} "
                      f"written={data.get('written')}/{data.get('expected')} problems={[p[:140] for p in data['problems']]}",
                      flush=True)
                return data

            out["preview"] = await call("moving_load_preview", {"model_path": str(model), **ARGS})
            out["apply"] = await call("moving_load_apply", {"model_path": str(model), **ARGS})
            if out["apply"]["ok"]:
                out["again_without_replace"] = await call("moving_load_apply", {"model_path": out["apply"]["output"], **ARGS})
    return out


def main() -> int:
    work = Path(sys.argv[1]).resolve()
    work.mkdir(parents=True, exist_ok=True)
    model = work / SOURCE.name
    shutil.copyfile(SOURCE, model)
    source_hash, copy_hash = digest(SOURCE), digest(model)
    proc, info = start_server(work)
    try:
        results = asyncio.run(runs(info, model))
    finally:
        req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/admin/shutdown", method="POST",
                                     headers={"X-LLSM-Token": info["token"], "Host": f"127.0.0.1:{info['port']}"})
        try:
            print("shutdown:", urllib.request.urlopen(req, timeout=10).status)
        except OSError as exc:
            print("shutdown failed:", exc)
        proc.wait(60)
    results = {k: summary(v) if isinstance(v, dict) else v for k, v in results.items()}
    results["server_exit"] = proc.returncode
    results["original_unchanged"] = digest(SOURCE) == source_hash and digest(model) == copy_hash
    log = (work / "server.log").read_text(encoding="utf-8", errors="replace")
    results["server_log_errors"] = [line for line in log.splitlines() if "Traceback" in line or "ERROR" in line]
    print("server exit:", proc.returncode, "| original and copy unchanged:", results["original_unchanged"],
          "| log errors:", results["server_log_errors"])
    (work / "verify_app_rule.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
