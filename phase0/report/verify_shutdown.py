"""Does a running report print stop when the server shuts down the way the panel stops it?

  python verify_shutdown.py MODEL.esa WORKDIR
Starts the MCP server from this build, calls report_write (a job), waits until ESA_XML runs,
POSTs /admin/shutdown with the token (the panel's first shutdown layer), then checks that no
ESA_XML of that print is left.
"""
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import psutil
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

REPO = Path(__file__).resolve().parents[2]


def esa_xml_runs():
    return [p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "esa_xml.exe"]


async def start_print(info, model):
    async with streamablehttp_client(f"http://127.0.0.1:{info['port']}/mcp", headers={"X-LLSM-Token": info["token"]}) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            res = await session.call_tool("report_write", {"model_path": str(model), "formats": ["xlsx"]})
            return json.loads(res.content[0].text)


def main():
    model, work = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    work.mkdir(parents=True, exist_ok=True)
    if esa_xml_runs():
        raise SystemExit("an ESA_XML is already running; not testing")
    pid_file, port_file = work / "sd_pid.json", work / "sd_port.txt"
    for f in (pid_file, port_file):
        f.unlink(missing_ok=True)
    server = subprocess.Popen([sys.executable, "-m", "llsm.mcp_server", "--http", "--port-file", str(port_file), "--pid-file", str(pid_file)],
                              env={**os.environ, "PYTHONPATH": str(REPO / "src")}, cwd=work,
                              stdout=open(work / "sd_server.log", "wb"), stderr=subprocess.STDOUT)
    for _ in range(60):
        if pid_file.exists() and pid_file.stat().st_size:
            break
        time.sleep(1)
    info = json.loads(pid_file.read_text())
    started = asyncio.run(start_print(info, model))
    print("report_write:", started.get("status"), started.get("job"), started.get("ok"))
    for _ in range(60):
        if esa_xml_runs():
            break
        time.sleep(1)
    runs = esa_xml_runs()
    print("ESA_XML running before shutdown:", runs)
    req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/admin/shutdown", method="POST",
                                 headers={"X-LLSM-Token": info["token"], "Host": f"127.0.0.1:{info['port']}"})
    print("shutdown:", urllib.request.urlopen(req, timeout=10).status)
    server.wait(30)
    time.sleep(3)
    left = esa_xml_runs()
    print("server exit:", server.returncode, "| ESA_XML left:", left)
    print("RESULT:", "PASS" if runs and not left else "FAIL")


if __name__ == "__main__":
    main()
