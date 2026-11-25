"""Can a local model build SCIA models through LLSM? Measure it on your own server.

  python scripts/local_model_trial.py --base-url http://localhost:11434/v1 --model qwen3-coder:30b

Runs LLSM's real OpenAI-compatible backend (the route the panel uses for
Ollama / LM Studio / llama-server) with the SCIA tools as a new local chat
gets them, and the real in-memory model builder. SCIA itself is left out:
starting it, pushing the model and calculating are replaced by a neutral
answer, so this measures the model, not SCIA. Three natural Dutch requests
(a beam, a portal frame, a continuous beam) are each asked `--reps` times;
every run is checked on what was actually built (nodes, members, supports,
loads, span, load sign and size, section size).

Needs: the `openai` extra (`pip install -e .[openai]`) and SCIA Engineer
installed (the model builder loads its libraries; SCIA does not start).
Results go to `--out` (JSON, written after every run) and a table at the end.
See docs/verify/local-models.md for measured results and advice.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))


# What SCIA's tools answer in this check. Worded so the model has nothing to
# react to (an earlier "skipped in trial" made models talk about a "trial
# version" of SCIA).
_NEUTRAL = {
    "launch_scia": {"ok": True},
    "push_model": {"ok": True},
    "run_analysis": {"ok": True, "results_available": False,
                     "note": "Results are not read back in this session; "
                             "report the model that was built."},
    "close_scia": {"ok": True},
}

# Calls that fail only because SCIA is left out: not the model's fault.
_SCIA_LEFT_OUT = ("no analysis results",)


PROMPTS = {
    "ligger": (
        "Maak een betonnen ligger van 6 m op twee steunpunten, doorsnede 300 x 500 mm, "
        "met een gelijkmatige belasting van 10 kN/m naar beneden.",
        dict(nodes=2, beams=1, supports=2, loads=1, span=6000, height=0,
             load=-10, sec=(300, 500), support_rule="not_clamped_both_ends"),
    ),
    "portaal": (
        "Maak een stalen portaal: twee kolommen van 4 m hoog die 8 m uit elkaar staan, "
        "ingeklemd aan de voet, met een ligger erbovenop. Doorsnede 200 x 400 mm. "
        "Zet 15 kN/m naar beneden op de ligger.",
        dict(nodes=4, beams=3, supports=2, loads=1, span=8000, height=4000,
             load=-15, sec=(200, 400), support_rule="all_fixed"),
    ),
    "doorgaand": (
        "Doorgaande stalen ligger over drie steunpunten, overspanningen 5 m en 7 m, "
        "doorsnede 200 x 400 mm, veranderlijke belasting 8 kN/m naar beneden op beide velden.",
        dict(nodes=3, beams=2, supports=3, loads=2, span=12000, height=0,
             load=-8, sec=(200, 400), support_rule=None),
    ),
}


def _extent(nodes: list[dict], axis: str) -> float:
    vals = [float(n.get(axis, 0) or 0) for n in nodes]
    return (max(vals) - min(vals)) if vals else 0.0


def score(snapshot: dict, expected: dict, events: list[dict]) -> dict[str, bool]:
    """Checks on what was built. Every value must be True for a pass."""
    nodes = snapshot.get("nodes", [])
    checks: dict[str, bool] = {}
    for key in ("nodes", "beams", "supports", "loads"):
        checks[key] = len(snapshot.get(key, [])) == expected[key]
    horizontal = max(_extent(nodes, "x_mm"), _extent(nodes, "y_mm"))
    checks["span"] = abs(horizontal - expected["span"]) < 1
    checks["height"] = abs(_extent(nodes, "z_mm") - expected["height"]) < 1
    mags = [ld.get("magnitude_kN_per_m") for ld in snapshot.get("loads", [])]
    checks["load"] = bool(mags) and all(
        m is not None and abs(float(m) - expected["load"]) < 1e-6 for m in mags)
    sections = [e["args"] for e in events
                if e["tool"] == "add_rectangular_section" and e.get("ok")]

    def dims(a: dict) -> set[int]:
        try:
            return {round(float(a.get("width_mm", 0))), round(float(a.get("height_mm", 0)))}
        except Exception:
            return set()
    checks["section"] = any(dims(a) == set(expected["sec"]) for a in sections)
    kinds = [s.get("kind") for s in snapshot.get("supports", [])]
    rule = expected.get("support_rule")
    if rule == "all_fixed":
        checks["support_kind"] = bool(kinds) and all(k == "fixed" for k in kinds)
    elif rule == "not_clamped_both_ends":
        # "op twee steunpunten": two clamped ends is a different beam.
        checks["support_kind"] = not (len(kinds) == 2 and all(k == "fixed" for k in kinds))
    return checks


def _model_errors(events: list[dict]) -> int:
    return sum(
        1 for e in events
        if e.get("ok") is False
        and not any(s in (e.get("error") or "") for s in _SCIA_LEFT_OUT)
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base-url", default="http://localhost:11434/v1",
                    help="the server's OpenAI-compatible URL (default: Ollama)")
    ap.add_argument("--model", required=True, help="model name as the server lists it")
    ap.add_argument("--reps", type=int, default=3, help="runs per request (default 3)")
    ap.add_argument("--only", choices=sorted(PROMPTS), action="append",
                    help="run only this request (repeatable)")
    ap.add_argument("--out", default="local_model_trial.json", help="JSON results file")
    args = ap.parse_args(argv)

    from llsm.scia.loader import load as load_scia
    load_scia()
    from llsm.llm.openai_compatible_backend import OpenAICompatibleBackend
    from llsm.llm.providers import PROVIDERS
    from llsm.scia import tools as scia_tools

    for name, answer in _NEUTRAL.items():
        setattr(scia_tools, name, lambda ctx, _a=answer, **kw: dict(_a))
    # Tools on/off exactly as a new local chat from the File menu gets it.
    tools_on = PROVIDERS["openai-compatible"].default_tools_enabled

    rows: list[dict] = []
    out = Path(args.out)
    for key in (args.only or list(PROMPTS)):
        prompt, expected = PROMPTS[key]
        for rep in range(args.reps):
            events: list[dict] = []

            def on_event(phase, name, payload, _ev=events):
                if phase == "start":
                    _ev.append({"tool": name, "args": payload})
                elif _ev:
                    _ev[-1]["ok"] = payload.get("ok")
                    _ev[-1]["error"] = payload.get("error")

            backend = OpenAICompatibleBackend(
                base_url=args.base_url, model=args.model, verbose=False,
                on_tool_event=on_event, tools_enabled=tools_on,
            )
            t0 = time.monotonic()
            error, reply = None, ""
            try:
                reply = backend.chat(prompt)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"[:400]
            seconds = time.monotonic() - t0
            snapshot = backend.model_snapshot()
            checks = score(snapshot, expected, events)
            row = dict(
                request=key, rep=rep, seconds=round(seconds, 1), error=error,
                tool_calls=len(events), model_errors=_model_errors(events),
                empty_reply=not reply.strip(), stopped=reply.startswith("[ERROR]"),
                checks=checks,
                passed=all(checks.values()) and not error and not reply.startswith("[ERROR]"),
                reply=reply, events=events, snapshot=snapshot,
            )
            rows.append(row)
            failed = [k for k, v in checks.items() if not v]
            print(f"{key:9} #{rep}  {'PASS' if row['passed'] else 'FAIL'}  {seconds:5.0f} s  "
                  f"calls {len(events):2}  "
                  f"{'checks off: ' + ', '.join(failed) if failed else ''}"
                  f"{'  ' + (error or reply[:90]) if (error or row['stopped']) else ''}",
                  flush=True)
            try:
                backend.close()
            except Exception:
                pass
            out.write_text(json.dumps(dict(base_url=args.base_url, model=args.model,
                                           tools_enabled=tools_on, runs=rows),
                                      indent=1, default=str), encoding="utf-8")

    print()
    print(f"{'request':10}{'passed':>8}{'median s':>10}{'stopped':>9}")
    for key in (args.only or list(PROMPTS)):
        mine = [r for r in rows if r["request"] == key]
        secs = sorted(r["seconds"] for r in mine)
        median = secs[len(secs) // 2] if secs else 0
        print(f"{key:10}{sum(r['passed'] for r in mine):>5} / {len(mine):<2}"
              f"{median:>8.0f}{sum(r['stopped'] or r['empty_reply'] for r in mine):>9}")
    total = sum(r["passed"] for r in rows)
    print(f"\n{args.model}: {total} of {len(rows)} models correct. Results: {out}")
    return 0 if total == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
