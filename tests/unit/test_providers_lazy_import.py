"""CR-1: importing providers.py must NOT pull in optional SDKs.

We run the assertion in a subprocess so a pytest plugin (or another test
in this same session) that pre-imported `openai` / `google.genai` can't
produce a false-positive failure.
"""
from __future__ import annotations

import os
import subprocess
import sys


_PROBE = """
import sys
import llsm.llm.providers  # noqa: F401
bad = [m for m in ("openai", "google.genai", "google.generativeai") if m in sys.modules]
print("BAD:" + ",".join(bad))
"""


def test_providers_does_not_import_sdks():
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    src = os.path.join(repo, "src")
    env = dict(os.environ)
    # Prepend src/ so the subprocess can `import llsm` without an editable install.
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert proc.returncode == 0, f"probe failed: stderr={proc.stderr}"
    out = proc.stdout.strip()
    assert out.startswith("BAD:"), f"unexpected probe output: {out!r}"
    bad_list = [m for m in out.removeprefix("BAD:").split(",") if m]
    assert bad_list == [], f"providers.py eagerly imported SDKs: {bad_list}"
