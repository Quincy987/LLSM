"""LLSM CLI — drives SCIA via the Anthropic tool-use loop.

Usage:
    llsm "build a 3m simply supported beam with a 10 kN point load at midspan"
    llsm --visible "..."          # show the SCIA window
    llsm --model opus-4.6 "..."   # override the model

In v0.3.0 the CLI's `--provider` flag is scoped to `api` only. The new
multi-provider factory in `llsm.llm.providers` is panel-coupled (reads
transcripts, MCP handle, callbacks off the panel object); headless
construction will land in v0.3.2 alongside `build_backend_headless` factories.

Default install requires ANTHROPIC_API_KEY (env var) or a stored credential
via the panel's Tools -> Set up credentials wizard.
"""
from __future__ import annotations
import argparse
import sys


def main() -> int:
    # Before the parser: its `prompt` positional would swallow a subcommand.
    if len(sys.argv) > 1 and sys.argv[1] == "bedding":
        from llsm.bedding.cli import main as bedding_main
        return bedding_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "diagnose":
        from llsm.diagnose import main as diagnose_main
        return diagnose_main(sys.argv[2:])

    # Not at module level: `llsm diagnose` has to run when these cannot be imported.
    from llsm.llm.anthropic_backend import AnthropicBackend
    from llsm.scia.loader import load as load_scia

    ap = argparse.ArgumentParser(prog="llsm", description="LLM-driven SCIA Engineer automation.")
    ap.add_argument("prompt", nargs="+", help="Natural-language request for the LLM.")
    ap.add_argument("--model", default=None, help="Override Anthropic model.")
    ap.add_argument("--provider", default="api",
                    help="Provider kind (v0.3.0: only 'api' is supported headless; "
                         "use the panel for openai/gemini/etc.).")
    ap.add_argument("--visible", action="store_true", help="Show SCIA window normally (default: minimized).")
    ap.add_argument("--verbose", action="store_true", help="Print every tool call + result.")
    args = ap.parse_args()

    if args.provider != "api":
        print(
            f"[llsm] error: --provider {args.provider!r} is not supported "
            "headless in v0.3.0. Use the panel (`llsm-panel`) for "
            "openai/gemini/openai-compatible/claude-code; headless "
            "multi-provider lands in v0.3.2.",
            file=sys.stderr,
        )
        return 2

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
