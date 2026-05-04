"""credentials.py is a leaf module — it must NOT import history.* at any point.

CR-2 in the plan: cache invalidation is the wizard's responsibility, not
the storage module's. Importing the catalog from credentials.py would
flip the layering edge in the wrong direction.
"""
from __future__ import annotations

import ast
import os


def test_credentials_does_not_import_history():
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    src = os.path.join(repo, "src", "llsm", "llm", "credentials.py")
    with open(src, encoding="utf-8") as fp:
        tree = ast.parse(fp.read(), filename=src)
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.startswith("..history") or mod.startswith("llsm.history") or mod == "history":
                bad.append(f"from {mod} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("llsm.history"):
                    bad.append(f"import {alias.name}")
    assert not bad, f"credentials.py reaches into history: {bad}"
