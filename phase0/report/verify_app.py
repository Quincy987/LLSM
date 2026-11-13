"""Verify the report tool through the app: the real MCP server (as the panel's chat calls it) and the CLI.

  python verify_app.py WORKDIR

Needs SCIA closed. Copies the models into WORKDIR first and never touches the originals:
- plate:   the spike's calculated plate (Report_1 of the kademuur template, results stored);
- brugdek: the moving-load copy from the demo folder (three cases LLSM-ML TS1 01..03, no results);
- real:    a v19.1 kademuur copy whose report items are of the old type.
Writes WORKDIR/verify_app.json with every result, and prints a summary.
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
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

REPO = Path(__file__).resolve().parents[2]
MODELS = {
    "plate": Path(r"C:\Development\LLSM\.worktrees\report-spike\phase0\xml_route\work\report\plate_calc.esa"),
    "brugdek": Path(r"C:\Users\klerk015\Documents\LLSM demo\brugdek_llsm-01.esa"),
    "real": Path(r"C:\Development\LLSM\.worktrees\t-405\phase0\xml_route\work\resave_real_out.esa"),
}
PY = sys.executable


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def start_server(work: Path) -> tuple[subprocess.Popen, dict]:
    pid_file, port_file = work / "server_pid.json", work / "server_port.txt"
    for f in (pid_file, port_file):
        f.unlink(missing_ok=True)
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    log = open(work / "server.log", "wb")
    proc = subprocess.Popen([PY, "-m", "llsm.mcp_server", "--http", "--port-file", str(port_file), "--pid-file", str(pid_file)],
                            env=env, stdout=log, stderr=log, cwd=work)
    for _ in range(60):
        if pid_file.exists() and pid_file.stat().st_size:
            return proc, json.loads(pid_file.read_text())
        time.sleep(1)
    raise SystemExit("MCP server did not start; see server.log")


async def mcp_runs(info: dict, copies: dict[str, Path]) -> dict:
    url = f"http://127.0.0.1:{info['port']}/mcp"
    out = {}
    async with streamablehttp_client(url, headers={"X-LLSM-Token": info["token"]}, sse_read_timeout=3600) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = [t.name for t in (await session.list_tools()).tools]
            out["tools"] = [t for t in tools if t.startswith("report_")] + [f"of {len(tools)}"]
            print("tools:", out["tools"], flush=True)

            async def call(name, args):
                started = time.time()
                res = await session.call_tool(name, args, read_timeout_seconds=timedelta(hours=1))
                data = json.loads(res.content[0].text)
                print(f"{name} {args.get('model_path', '')[-40:]} {json.dumps({k: args[k] for k in args if k != 'model_path'})}: "
                      f"ok={data['ok']} {round(time.time() - started)} s; chapters={data.get('chapters')}; "
                      f"problems={[p[:120] for p in data['problems']]}", flush=True)
                return data

            plate, brug = str(copies["plate"]), str(copies["brugdek"])
            out["preview_plate"] = await call("report_preview", {"model_path": plate})
            out["refuse_wrong_name"] = await call("report_write", {"model_path": plate, "report_name": "Report_9"})
            out["refuse_uncalculated"] = await call("report_write", {"model_path": brug})
            out["refuse_group_without_calc"] = await call("report_write", {"model_path": brug, "cases_group": "LLSM-ML TS1"})
            out["refuse_unknown_group"] = await call("report_write", {"model_path": brug, "calculate": True, "cases_group": "LLSM-ML TS9"})
            out["write_plate"] = await call("report_write", {"model_path": plate})
            out["preview_brugdek"] = await call("report_preview", {"model_path": brug, "calculate": True, "cases_group": "LLSM-ML TS1"})
            out["write_brugdek"] = await call("report_write", {"model_path": brug, "calculate": True, "cases_group": "LLSM-ML TS1"})
    return out


def main() -> int:
    work = Path(sys.argv[1]).resolve()
    work.mkdir(parents=True, exist_ok=True)
    copies = {}
    for key, src in MODELS.items():
        dst = work / src.name
        shutil.copyfile(src, dst)
        copies[key] = dst
    hashes = {k: digest(p) for k, p in copies.items()}

    proc, info = start_server(work)
    try:
        results = asyncio.run(mcp_runs(info, copies))
    finally:
        proc.terminate()
        proc.wait(30)

    env = {**os.environ, "PYTHONPATH": str(REPO / "src"), "PYTHONIOENCODING": "utf-8"}
    cli = subprocess.run([PY, "-m", "llsm.cli", "report", "write", str(copies["real"])], env=env, capture_output=True,
                         text=True, encoding="utf-8", errors="replace", cwd=work)
    results["cli_real"] = {"exit": cli.returncode, "stdout": cli.stdout[-3000:], "stderr": cli.stderr[-1500:]}
    print("CLI real:", cli.returncode, cli.stdout[-1500:], flush=True)

    results["sources_unchanged"] = {k: digest(p) == hashes[k] for k, p in copies.items()}
    print("sources unchanged:", results["sources_unchanged"])
    (work / "verify_app.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
