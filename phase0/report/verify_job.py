"""The job route through the real MCP server: report_write returns at once, report_status until done.

  python verify_job.py MODEL.esa WORKDIR
"""
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

REPO = Path(__file__).resolve().parents[2]


async def run(info, model):
    async with streamablehttp_client(f"http://127.0.0.1:{info['port']}/mcp", headers={"X-LLSM-Token": info["token"]},
                                     sse_read_timeout=600) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            t0 = time.time()
            res = json.loads((await session.call_tool("report_write", {"model_path": str(model), "formats": ["xlsx"]})).content[0].text)
            print(f"report_write: {res['status']} job={res['job']} ok={res['ok']} saved_by={res['saved_by']} in {time.time() - t0:.1f} s", flush=True)
            while True:
                t1 = time.time()
                st = json.loads((await session.call_tool("report_status", {"job": res["job"]},
                                                         read_timeout_seconds=timedelta(seconds=290))).content[0].text)
                print(f"report_status: {st['status']} step={st.get('step')} after {time.time() - t1:.0f} s (total {st['elapsed_s']} s)", flush=True)
                if st["status"] != "running":
                    print("ok:", st["ok"], "chapters:", st.get("chapters"), "outputs:", st.get("outputs"), "problems:", st.get("problems"))
                    return st


def main():
    model, work = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    work.mkdir(parents=True, exist_ok=True)
    pid_file, port_file = work / "job_pid.json", work / "job_port.txt"
    for f in (pid_file, port_file):
        f.unlink(missing_ok=True)
    server = subprocess.Popen([sys.executable, "-m", "llsm.mcp_server", "--http", "--port-file", str(port_file), "--pid-file", str(pid_file)],
                              env={**os.environ, "PYTHONPATH": str(REPO / "src")}, cwd=work,
                              stdout=open(work / "job_server.log", "wb"), stderr=subprocess.STDOUT)
    try:
        for _ in range(60):
            if pid_file.exists() and pid_file.stat().st_size:
                break
            time.sleep(1)
        st = asyncio.run(run(json.loads(pid_file.read_text()), model))
    finally:
        server.terminate()
        server.wait(30)
    print("RESULT:", "PASS" if st.get("ok") else "FAIL")


if __name__ == "__main__":
    main()
