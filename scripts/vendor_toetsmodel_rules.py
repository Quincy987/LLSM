"""Copy the traffic rules of the plaatbruggen toetsmodel into ``src/llsm/traffic_rules/_toetsmodel/``.

  python scripts/vendor_toetsmodel_rules.py <checkout> [--commit e8e44e7] [--check]

Nobody edits the copy by hand; changes go upstream and this script is run again. Two kinds of copy:

* whole files that import nothing outside the copy: equal to the original after one import rewrite
  (line endings aside: git checks them out as configured)
  (``from src.integrations.scia_integration.`` -> ``from llsm.traffic_rules._toetsmodel.``);
* single functions from modules that also import VIKTOR, plotly or the app's params (``lanes.py``): each
  function's source is copied verbatim (its exact lines in the original), with a generated import of the
  vendored constants it uses. A name the copy does not provide stops the script.

``--check`` writes nothing: it rebuilds the copy in memory and exits 1 when it differs from what is in
the repo (the unit test does the same).
"""
from __future__ import annotations

import argparse
import ast
import builtins
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / "src" / "llsm" / "traffic_rules" / "_toetsmodel"
UPSTREAM_URL = "https://github.com/Amsterdam/automatisch-toetsmodel-plaatbruggen"
PINNED = "e8e44e7"
PACKAGE = "src/integrations/scia_integration"
OLD_IMPORT = "from src.integrations.scia_integration."
NEW_IMPORT = "from llsm.traffic_rules._toetsmodel."

WHOLE_FILES = (
    "constants/geometry.py",
    "constants/loads.py",
    "constants/vehicles.py",
    "load_system/tandem_sequencer.py",
)
# (module in the package, function): pure functions whose modules pull in VIKTOR / plotly / params.
EXTRACTS = (
    ("load_system/lane_calculations.py", "amount_of_notional_lanes"),
    ("load_system/theoretical_tandem_generators.py", "generate_theoretical_lane_positions_bg8000"),
    ("load_system/theoretical_tandem_generators.py", "generate_theoretical_lane_positions_bg9000"),
    ("load_system/theoretical_tandem_generators.py", "generate_theoretical_lane_positions_bg10000"),
)
EXTRACT_FILE = "lanes.py"
# The vendored modules an extract may take names from.
NAME_SOURCES = ("constants/geometry.py", "constants/loads.py")
_BUILTINS = set(dir(builtins))


class VendorError(RuntimeError):
    """The copy cannot be made as specified."""


@dataclass(frozen=True)
class Copy:
    files: dict[str, str]  # path below TARGET -> text
    extracts: list[tuple[str, str]]


def rewrite(text: str) -> str:
    """The only change a whole file gets."""
    return text.replace(OLD_IMPORT, NEW_IMPORT)


def module_names(text: str) -> set[str]:
    """Names a module defines at its top level."""
    tree = ast.parse(text)
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, (ast.AnnAssign,)) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def function_source(text: str, name: str) -> tuple[str, set[str]]:
    """The exact lines of a top-level function, and the free names it reads."""
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            lines = text.splitlines(keepends=True)
            source = "".join(lines[node.lineno - 1:node.end_lineno])
            local = {a.arg for a in [*node.args.args, *node.args.kwonlyargs, *node.args.posonlyargs]}
            local |= {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
            read = {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
            return source, read - local
    raise VendorError(f"function {name} not found")


def build(checkout: Path) -> Copy:
    package = checkout / PACKAGE
    files: dict[str, str] = {}
    for relative in WHOLE_FILES:
        text = (package / relative).read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")) and not stripped.startswith(OLD_IMPORT):
                if not stripped.startswith(("from __future__", "from typing", "import math")):
                    raise VendorError(f"{relative} imports {stripped!r}: not a self-contained file")
            if stripped.startswith(OLD_IMPORT):
                module = stripped[len(OLD_IMPORT):].split(" import ")[0].replace(".", "/") + ".py"
                if module not in WHOLE_FILES:
                    raise VendorError(f"{relative} imports {module}, which is not copied whole")
        files[relative] = rewrite(text)

    available: dict[str, str] = {}
    for relative in NAME_SOURCES:
        for name in module_names((package / relative).read_text(encoding="utf-8")):
            available.setdefault(name, relative)
    blocks: list[str] = []
    needed: dict[str, set[str]] = {}
    defined = {name for _, name in EXTRACTS}
    for relative, name in EXTRACTS:
        source, free = function_source((package / relative).read_text(encoding="utf-8"), name)
        for used in sorted(free - defined - _BUILTINS):
            if used not in available:
                raise VendorError(f"{relative}:{name} uses {used}, which the copy does not provide")
            needed.setdefault(available[used], set()).add(used)
        blocks.append(f"# --- {PACKAGE}/{relative} :: {name} ---\n{source}")
    header = [
        '"""Pure lane rules of the plaatbruggen toetsmodel, copied function by function.',
        "",
        "Generated by scripts/vendor_toetsmodel_rules.py: do not edit. Each function below is the exact",
        "source of the function named in its marker line; the modules they come from also import VIKTOR,",
        "plotly or the app's params, so they are not copied whole (UPSTREAM.md).",
        '"""',
        "",
    ]
    for relative in sorted(needed):
        module = relative[:-3].replace("/", ".")
        names = ",\n".join(f"    {n}" for n in sorted(needed[relative]))
        header.append(f"{NEW_IMPORT}{module} import (  # noqa: F401\n{names},\n)")
    files[EXTRACT_FILE] = "\n".join(header) + "\n\n\n" + "\n\n".join(blocks)
    for package_dir in ("", "constants", "load_system"):
        files[f"{package_dir}/__init__.py".lstrip("/")] = '"""Vendored from the plaatbruggen toetsmodel; see UPSTREAM.md."""\n'
    return Copy(files, list(EXTRACTS))


def upstream_md(commit: str, date: str) -> str:
    whole = "\n".join(f"- `{PACKAGE}/{f}` → `{f}`" for f in WHOLE_FILES)
    extracts = "\n".join(f"- `{PACKAGE}/{m}` :: `{n}` → `{EXTRACT_FILE}`" for m, n in EXTRACTS)
    return f"""# Vendored: traffic rules of the plaatbruggen toetsmodel

- Repository: {UPSTREAM_URL}
- Commit: `{commit}` ({date})
- Made by: `python scripts/vendor_toetsmodel_rules.py <checkout> --commit {commit[:7]}`. Nobody edits these
  files by hand: a rule change goes upstream, then this script runs again and the tests show what moved.
- Upstream requires Python 3.14. The copy was imported and run under 3.11.0 (same positions and lanes);
  LLSM's tests run it on 3.12.

## Whole files (equal to the original after one import rewrite; line endings as git checks them out)

`{OLD_IMPORT}` → `{NEW_IMPORT}`

{whole}

## Single functions

Their modules also import VIKTOR, plotly (through `src.common.constants.parametrization`), `src.common.materials`
or the app's params, so only the functions are copied, each as its exact source lines. `{EXTRACT_FILE}` starts with
a generated import of the vendored constants they use.

{extracts}

Not copied: `real_tandem_generators._generate_lanes_*_strategy*` (only used for two road zones, which LLSM
v0.4.0 does not offer) and the `generate_real_lane_positions_*` wrappers (they read the road zone from the app's
params). For one carriageway those wrappers compute the theoretical lanes over the road strip, shifted to its
edge; LLSM does exactly that with the functions above (compared in docs/verify/phase4-traffic-rules.md).
"""


def write(copy: Copy, commit: str, date: str) -> None:
    for relative, text in copy.files.items():
        path = TARGET / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    (TARGET / "UPSTREAM.md").write_bytes(upstream_md(commit, date).encode("utf-8"))


def differences(copy: Copy) -> list[str]:
    out = []
    for relative, text in copy.files.items():
        path = TARGET / relative
        if not path.is_file():
            out.append(f"{relative}: missing")
        elif path.read_text(encoding="utf-8") != text:  # line endings follow git's checkout setting, so not compared
            out.append(f"{relative}: differs from the original")
    return out


def _git(checkout: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, text=True, check=True).stdout.strip()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--commit", default=PINNED)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    head = _git(args.checkout, "rev-parse", "HEAD")
    if not head.startswith(args.commit):
        print(f"The checkout is at {head[:7]}, not {args.commit}: check out the pinned commit first.")
        return 1
    copy = build(args.checkout)
    if args.check:
        problems = differences(copy)
        print("\n".join(problems) or "The copy matches the original.")
        return 1 if problems else 0
    write(copy, head, _git(args.checkout, "log", "-1", "--format=%ad", "--date=short"))
    print(f"Copied {len(WHOLE_FILES)} files and {len(EXTRACTS)} functions from {head[:7]} into {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
