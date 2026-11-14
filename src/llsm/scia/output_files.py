"""Where an update of a saved model goes, and what happens to a result that did not check out."""
from __future__ import annotations

import re
from pathlib import Path

_OUTPUT_SUFFIX = re.compile(r"_llsm-\d+$")


def next_output_path(model_path: Path, out_dir: Path | None = None) -> Path:
    """``<stem>_llsm-NN.esa`` with the first free number, numbering on from an earlier output."""
    folder = Path(out_dir) if out_dir else model_path.parent
    stem = _OUTPUT_SUFFIX.sub("", model_path.stem)
    number = 1
    while (candidate := folder / f"{stem}_llsm-{number:02d}{model_path.suffix}").exists():
        number += 1
    return candidate


def next_output_set(model_path: Path, label: str, suffixes: list[str], out_dir: Path | None = None) -> dict[str, Path]:
    """``<full stem>_<label>-NN.<suffix>`` for each suffix, with one number free for all of them.

    The model's whole stem is kept (``brugdek_llsm-01_rapport-01.pdf``): a copy's report must not
    take the name of the original's. A number counts as taken when any of the files, or its
    _FAILED twin, exists.
    """
    folder = Path(out_dir) if out_dir else model_path.parent
    number = 1
    while True:
        base = f"{model_path.stem}_{label}-{number:02d}"
        names = {s: folder / f"{base}.{s}" for s in suffixes}
        if not any(p.exists() or p.with_name(f"{p.stem}_FAILED{p.suffix}").exists() for p in names.values()):
            return names
        number += 1


def mark_failed(output: Path) -> Path:
    """Keep a bad file for inspection under a name nobody will mistake for a result."""
    failed = output.with_name(f"{output.stem}_FAILED{output.suffix}")
    try:
        failed.unlink(missing_ok=True)
        output.rename(failed)
    except OSError:  # still held by something; the result is not ok either way
        return output
    return failed
