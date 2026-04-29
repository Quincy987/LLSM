"""LLSM CLI — drives SCIA via the Anthropic tool-use loop.

Usage:
    llsm "build a 3m simply supported beam with a 10 kN point load at midspan"
    llsm --visible "..."   # show the SCIA window instead of minimized
    llsm --model opus-4.6 "..."  # override the model

Requires ANTHROPIC_API_KEY env var.
"""
from __future__ import annotations
import argparse
import sys

from llsm.scia.loader import load as load_scia
from llsm.llm.anthropic_backend import AnthropicBackend


def main() -> int:
    ap = argparse.ArgumentParser(prog="llsm", description="LLM-driven SCIA Engineer automation.")
    ap.add_argument("prompt", nargs="+", help="Natural-language request for the LLM.")
    ap.add_argument("--model", default=None, help="Override Anthropic model.")
    ap.add_argument("--visible", action="store_true", help="Show SCIA window normally (default: minimized).")
    ap.add_argument("--verbose", action="store_true", help="Print every tool call + result.")
    args = ap.parse_args()

    prompt = " ".join(args.prompt)

    print(f"[llsm] {prompt!r}")
    print(f"[llsm] loading SCIA DLLs...")
    load_scia()

    kwargs = {"verbose": args.verbose}
    if args.model:
        kwargs["model"] = args.model
    backend = AnthropicBackend(**kwargs)

    # Nudge the LLM about visibility if user asked
    if args.visible:
        prompt += "\n\n(Please call launch_scia with visible=true so the window is shown normally.)"

    try:
        reply = backend.chat(prompt)
        print()
        print("=" * 60)
        print(reply)
        print("=" * 60)
        return 0
    except KeyboardInterrupt:
        print("\n[llsm] interrupted")
        return 130
    except Exception as e:
        print(f"[llsm] ERROR: {type(e).__name__}: {e}")
        return 1
    finally:
        backend.close()


if __name__ == "__main__":
    sys.exit(main())
