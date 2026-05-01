"""End-to-end CLI harness for LLSM dev work.

Lets a developer (or an automated agent) send a prompt through the same
ClaudeCodeBackend the panel uses, but without the Tk window and the UX
layers around it. Reuses any running MCP supervisor; spawns one if none is
running; persists session_id like the panel does so a follow-up call
continues the conversation.

Usage:
    python tests/run_e2e.py "Build a 3m beam, S235, 200x200, 10 kN/m, run analysis."
    python tests/run_e2e.py --check-node n2          # call get_node_position via MCP directly
    python tests/run_e2e.py --shutdown              # graceful supervisor shutdown
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

# Make sure we use the in-tree source.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from llsm.scia.loader import load as load_scia
from llsm import mcp_supervisor
from llsm.llm.claude_code_backend import ClaudeCodeBackend


def get_or_spawn_supervisor(verbose: bool):
    handle = mcp_supervisor.connect_to_existing(verbose=verbose)
    if handle is not None:
        print(f"[harness] reattached to running supervisor at {handle.url}", file=sys.stderr)
        return handle
    print("[harness] no running supervisor — spawning fresh", file=sys.stderr)
    return mcp_supervisor.spawn(verbose=verbose)


def cmd_chat(prompt: str, verbose: bool, timeout_s: int) -> int:
    handle = get_or_spawn_supervisor(verbose=verbose)
    persisted = mcp_supervisor.read_session_id()
    backend = ClaudeCodeBackend(
        mcp_config_json=handle.mcp_config_json,
        session_id=persisted,
        session_started=bool(persisted),
        verbose=verbose,
        timeout_s=timeout_s,
        on_session_rotated=lambda new_id: mcp_supervisor.write_session_id(new_id),
        on_orphan_cleanup=lambda sid: mcp_supervisor.kill_orphan_claudes(sid, verbose=verbose),
    )
    # Persist whatever id we used so a follow-up resumes.
    try:
        mcp_supervisor.write_session_id(backend.session_id)
    except Exception:
        pass

    print(f"\n>>> USER: {prompt}\n", flush=True)
    t0 = time.monotonic()
    try:
        reply = backend.chat(prompt)
    except Exception as exc:
        print(f"\n[harness] chat failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    dt = time.monotonic() - t0
    print(f"<<< LLSM ({dt:.1f}s):\n{reply}\n", flush=True)
    return 0


async def _mcp_call(url: str, tool: str, arguments: dict) -> dict:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(url) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments=arguments)
            for c in result.content:
                if hasattr(c, "text"):
                    try:
                        return json.loads(c.text)
                    except json.JSONDecodeError:
                        return {"_raw": c.text}
            return {"_empty": True}


def cmd_check_node(name: str, verbose: bool) -> int:
    handle = mcp_supervisor.connect_to_existing(verbose=verbose)
    if handle is None:
        print("[harness] no running supervisor — nothing to check.", file=sys.stderr)
        return 1
    out = asyncio.run(_mcp_call(handle.url, "get_node_position", {"name": name}))
    print(json.dumps(out, indent=2), flush=True)
    return 0


def cmd_summary(verbose: bool) -> int:
    handle = mcp_supervisor.connect_to_existing(verbose=verbose)
    if handle is None:
        print("[harness] no running supervisor", file=sys.stderr)
        return 1
    out = asyncio.run(_mcp_call(handle.url, "model_summary", {}))
    print(json.dumps(out, indent=2), flush=True)
    return 0


def cmd_shutdown(verbose: bool) -> int:
    handle = mcp_supervisor.connect_to_existing(verbose=verbose)
    if handle is None:
        print("[harness] no running supervisor", file=sys.stderr)
        return 0
    mcp_supervisor.shutdown(handle, verbose=verbose)
    print("[harness] supervisor shut down", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="run_e2e",
        description="End-to-end LLSM harness: send chat prompts + verify state without the panel UI.")
    ap.add_argument("prompt", nargs="*", help="Prompt to send to the LLM (default cmd is `chat`)")
    ap.add_argument("--verbose", "-v", action="store_true", help="Verbose subprocess + supervisor output")
    ap.add_argument("--timeout", type=int, default=600, help="Per-chat timeout in seconds (default 600)")
    ap.add_argument("--check-node", metavar="NAME", help="Call get_node_position(NAME) via MCP and print the result")
    ap.add_argument("--summary", action="store_true", help="Call model_summary via MCP and print the result")
    ap.add_argument("--shutdown", action="store_true", help="Gracefully shut down the running supervisor")
    args = ap.parse_args()

    load_scia()

    if args.shutdown:
        return cmd_shutdown(args.verbose)
    if args.check_node:
        return cmd_check_node(args.check_node, args.verbose)
    if args.summary:
        return cmd_summary(args.verbose)
    if args.prompt:
        return cmd_chat(" ".join(args.prompt), args.verbose, args.timeout)

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
