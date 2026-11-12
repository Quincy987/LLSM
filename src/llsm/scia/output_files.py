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


def mark_failed(output: Path) -> Path:
    """Keep a bad file for inspection under a name nobody will mistake for a result."""
    failed = output.with_name(f"{output.stem}_FAILED{output.suffix}")
    try:
        failed.unlink(missing_ok=True)
        output.rename(failed)
    except OSError:  # still held by something; the result is not ok either way
        return output
    return failed
