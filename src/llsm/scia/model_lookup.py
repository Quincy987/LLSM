"""Find the engineer's saved model from what they call it: a full path, or just its file name.

The panel's chat has only LLSM's tools and runs in the repo folder, so "brugdek.esa" would be looked for there.
An engineer names the model they have open; this finds it:

* a path (absolute, or with a folder in it) is used as is, and refused when it does not exist;
* a bare file name (".esa" added when missing, case does not matter) is searched in the model folders, never in
  the server's own working folder: ``LLSM_MODEL_DIRS`` (folders separated by ``os.pathsep``) when set, else the
  user's Documents folder as Windows knows it (also when it is redirected, e.g. to OneDrive) and the local
  ~/Documents when that is another folder, to a depth of four
  folders, skipping folders whose name starts with ``.``; and the folder of the last model a tool used in this
  session, first.

One match is the model. Several: the caller lists them and asks which. None: the caller says where it looked.
A name that matched one model, or that was given once as a full path, keeps meaning that file for the rest of the
session while it exists, so a preview and the apply after it act on the same model (and the folders are not
searched again).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

MAX_DEPTH = 4
_last_folder: Path | None = None  # the folder of the last model found, this session
_known: dict[str, Path] = {}  # bare name (lower case, with .esa) -> the one model it matched, this session


@dataclass
class Lookup:
    path: Path | None = None
    candidates: list[dict] = field(default_factory=list)  # {"path", "saved"} when several match
    problem: str | None = None


def documents_folder() -> Path:
    """The user's Documents folder as Windows knows it (FOLDERID_Documents); else ~/Documents."""
    if os.name == "nt":
        try:
            import ctypes  # noqa: PLC0415
            from ctypes import wintypes  # noqa: PLC0415

            class _Guid(ctypes.Structure):
                _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

            documents = _Guid(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
            found = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(documents), 0, None, ctypes.byref(found)) == 0:
                try:
                    return Path(found.value)
                finally:
                    ctypes.windll.ole32.CoTaskMemFree(found)
        except (OSError, AttributeError, ValueError):
            pass
    return Path.home() / "Documents"


def model_dirs() -> list[Path]:
    """Where a bare file name is searched, in order."""
    given = os.environ.get("LLSM_MODEL_DIRS", "").strip()
    if given:
        folders = [Path(p) for p in given.split(os.pathsep) if p.strip()]
    else:  # Windows' Documents (often on OneDrive) and the local one, when they differ: models live in both
        folders = list(dict.fromkeys(f for f in (documents_folder(), Path.home() / "Documents") if f.is_dir()))
    if _last_folder is not None and _last_folder not in folders:
        folders.insert(0, _last_folder)
    return folders


def _walk(folder: Path, wanted: str, depth: int, found: list[Path]) -> None:
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_file() and entry.name.lower() == wanted:
                found.append(Path(entry.path).resolve())
            elif entry.is_dir(follow_symlinks=False) and not entry.name.startswith(".") and depth < MAX_DEPTH:
                _walk(Path(entry.path), wanted, depth + 1, found)
        except OSError:
            continue


def resolve_model(text: str) -> Lookup:
    """The model the engineer means by ``text``."""
    global _last_folder
    raw = str(text).strip().strip('"')
    if not raw:
        return Lookup(problem="No model given: name the .esa file.")
    given = Path(raw).expanduser()
    if given.is_absolute() or given.parent != Path("."):
        if not given.is_file():
            return Lookup(problem=f"Model not found: {given}")
        chosen = given.resolve()
        _last_folder = chosen.parent
        _known[chosen.name.lower()] = chosen  # after "which one?", the bare name means the one chosen
        return Lookup(path=chosen)
    wanted = (raw if raw.lower().endswith(".esa") else raw + ".esa").lower()
    known = _known.get(wanted)
    if known is not None and known.is_file():
        _last_folder = known.parent
        return Lookup(path=known)
    folders = model_dirs()
    found: list[Path] = []
    for folder in folders:
        _walk(folder, wanted, 0, found)
    found = list(dict.fromkeys(found))
    if len(found) == 1:
        _last_folder = found[0].parent
        _known[wanted] = found[0]
        return Lookup(path=found[0])
    where = ", ".join(str(f) for f in folders)
    if not found:
        return Lookup(problem=f"No model called {wanted} under {where}; give the path of the file.")
    candidates = [
        {"path": str(p), "saved": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")}
        for p in sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)
    ]
    return Lookup(candidates=candidates, problem=f"Several models called {wanted}; say which (newest first).")


def forget() -> None:
    """Drop the remembered folder and names (tests)."""
    global _last_folder
    _last_folder = None
    _known.clear()
