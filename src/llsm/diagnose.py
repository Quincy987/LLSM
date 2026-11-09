"""``llsm diagnose``: one report on whether this computer can run LLSM.

Starts nothing and needs no credentials. Licence tokens are only read for their
expiry date; their contents are never printed.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import importlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

OK, WARN, FAIL = "OK", "WARN", "FAIL"

_PACKAGES = (("pythonnet", "pythonnet"), ("pywin32", "win32api"), ("psutil", "psutil"), ("mcp", "mcp"))
_LLSM_HOME = Path.home() / ".llsm"
_PROTECTION = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SCIA" / "Protection"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def _python() -> list[Check]:
    version = ".".join(str(part) for part in sys.version_info[:3])
    status = OK if sys.version_info[:2] >= (3, 11) else FAIL
    return [Check("Python", status, f"{version} at {sys.prefix}")]


def _packages() -> list[Check]:
    checks = []
    for distribution, module in _PACKAGES:
        try:
            importlib.import_module(module)
            version = importlib.metadata.version(distribution)
        except Exception as exc:
            checks.append(Check(f"Package {distribution}", FAIL, f"{type(exc).__name__}: {exc}"))
        else:
            checks.append(Check(f"Package {distribution}", OK, version))
    return checks


def _scia() -> list[Check]:
    try:
        from .scia import loader

        install = loader.discover().install_folder
    except Exception as exc:
        return [Check("SCIA Engineer", FAIL, f"not found in the registry ({type(exc).__name__})")]
    from .scia.loader import DEFAULT_SCIA_VERSION

    exe = install / "ESA_XML.exe"
    return [
        Check("SCIA Engineer", OK, f"{DEFAULT_SCIA_VERSION} at {install}"),
        Check("ESA_XML.exe", OK if exe.is_file() else FAIL, str(exe)),
    ]


def _token_expiry(path: Path) -> dt.datetime | None:
    """The expiry of a cached licence token; everything else in it is ignored."""
    try:
        payload = path.read_text(encoding="ascii", errors="ignore").split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return dt.datetime.fromtimestamp(json.loads(base64.urlsafe_b64decode(payload))["exp"], dt.timezone.utc)
    except (OSError, IndexError, KeyError, ValueError, TypeError):
        return None


def _licence(folder: Path | None = None, now: dt.datetime | None = None) -> list[Check]:
    folder = folder or _PROTECTION
    expiries = [e for e in (_token_expiry(p) for p in folder.glob("*.jwt")) if e is not None]
    if not expiries:
        return [Check("SCIA licence", WARN, "no cached licence token; open SCIA once")]
    latest = max(expiries)
    stamp = latest.strftime("%Y-%m-%d %H:%M UTC")
    if latest < (now or dt.datetime.now(dt.timezone.utc)):
        return [Check("SCIA licence", WARN, f"cached token expired {stamp}; on a licence error, open SCIA once to renew it")]
    return [Check("SCIA licence", OK, f"cached token valid until {stamp}")]


def _claude() -> list[Check]:
    path = shutil.which("claude")
    if path is None:
        return [Check("Claude Code CLI", WARN, "not on PATH; needed for the chat, not for `llsm bedding`")]
    try:
        done = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=30, errors="replace")
        return [Check("Claude Code CLI", OK, done.stdout.strip() or path)]
    except (OSError, subprocess.SubprocessError) as exc:
        return [Check("Claude Code CLI", WARN, f"found but did not answer ({type(exc).__name__})")]


def _running() -> list[Check]:
    try:
        from .scia.esa_xml import running_scia

        found = running_scia()
    except Exception as exc:
        return [Check("SCIA running now", WARN, f"could not check ({type(exc).__name__})")]
    if found:
        return [Check("SCIA running now", WARN, f"{', '.join(n for _, n in found)}; close it before `llsm bedding apply`")]
    return [Check("SCIA running now", OK, "none")]


def _writable(name: str, folder: Path) -> Check:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=folder):
            pass
    except OSError as exc:
        return Check(name, FAIL, f"{folder}: {exc}")
    return Check(name, OK, str(folder))


def run_checks(folder: Path | None = None) -> list[Check]:
    checks = [*_python(), *_packages(), *_scia(), *_licence(), *_claude(), *_running(), _writable("LLSM state folder", _LLSM_HOME)]
    if folder is not None:
        checks.append(_writable("Model folder", folder))
    return checks


def format_report(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    return "\n".join(f"{c.status:<4}  {c.name:<{width}}  {c.detail}" for c in checks)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="llsm diagnose", description="Check whether this computer can run LLSM.")
    parser.add_argument("--folder", type=Path, help="Also test that this folder is writable.")
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    args = parser.parse_args(argv)
    checks = run_checks(args.folder)
    print(json.dumps([asdict(c) for c in checks], indent=1) if args.json else format_report(checks))
    return 1 if any(c.status == FAIL for c in checks) else 0
