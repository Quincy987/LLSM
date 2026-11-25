"""The LLSM start model: the SCIA project a chat-built model is pushed into, so it can be calculated.

SCIA's own calculation command does nothing on the gemeente's licence, and its results API returns
nothing (docs/verify/calc-run.md). What works is the route the VIKTOR apps use: a saved copy of the
project calculated by SCIA's batch program (ESA_XML LIN) with the results exported as XML (-x). That
export needs an XML IO document in the project, and only SCIA's GUI can make one. So a chat model is
pushed into a copy of a model that already has one: the start model.

It is not in the repository (36 MB, an internal app's file). ``llsm start-model <template.esa>``
stores a SCIA-24 copy of a template at ``~/.llsm/start_model.esa`` (or ``LLSM_START_MODEL``);
the tested template is the kademuren app's ``app/segment/scia_integration/scia/model.esa``.

The start model holds SCIA's material library (487 names), 31 cross-sections, load group LG1 and
load case BG1. A chat model must not reuse those names: SCIA refuses LG1 and BG1, and a material
named C30/37 made the calculation give no results at all (try 16). They are read from the file once.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from .esa_file import _table_of, iter_tables, names_in, saved_by_version

ENV = "LLSM_START_MODEL"
DEFAULT = Path.home() / ".llsm" / "start_model.esa"
EXPORT_TABLE = "Interne 1D-krachten"   # the export must hold this table, per result class
MATERIALS_TABLE = "EP_Material.EP_Material.1"
# Tables whose names a chat model must not reuse (each continues in the blocks after its header).
OWNED_TABLES = (
    MATERIALS_TABLE,
    "EP_CrossSection.EP_CssGener.1",
    "DataSetScia.EP_LoadGroup.1",
    "DataSetScia.EP_LoadCase.1",
    "EP_DSG_Elements.EP_DataLayer.1",
)
_VERSION_STAMP = re.compile(r"^\+D_\d+_")
_names_cache: dict[tuple[str, float], dict[str, tuple[str, ...]]] = {}


def find() -> Path | None:
    """The start model this computer uses, or None when there is none."""
    configured = os.environ.get(ENV)
    path = Path(configured) if configured else DEFAULT
    return path if path.is_file() else None


def _names_by_table(path: Path) -> dict[str, tuple[str, ...]]:
    """The names in each OWNED_TABLES table of the start model, in file order, read once per file version."""
    path = Path(path)
    key = (str(path.resolve()), path.stat().st_mtime)
    if key in _names_cache:
        return _names_cache[key]
    names: dict[str, list[str]] = {t: [] for t in OWNED_TABLES}
    current = None
    for chunk in iter_tables(path.read_bytes()):
        table = _table_of(chunk)
        if table:
            current = table
        if current not in OWNED_TABLES:
            continue
        for _, text in names_in(chunk)[(1 if table else 0):]:  # the first string of a header block is the header
            text = text.strip()
            if len(text) > 1 and "<" not in text and ">" not in text and not _VERSION_STAMP.match(text):
                if text not in names[current]:
                    names[current].append(text)
    _names_cache[key] = {t: tuple(v) for t, v in names.items()}
    return _names_cache[key]


def owned_names(path: Path) -> frozenset[str]:
    """Every name the start model's materials, sections, load groups, load cases and layers use."""
    return frozenset(n for names in _names_by_table(path).values() for n in names)


def material_grades(path: Path) -> tuple[str, ...]:
    """The names of SCIA's material library in the start model ("C30/37", "S 235", ...): the grades."""
    return _names_by_table(path)[MATERIALS_TABLE]


def export_has_results_table(xml_text: str) -> bool:
    return f'name="{EXPORT_TABLE}"' in xml_text


def check(path: Path, runner=None) -> dict:
    """Does a batch export of ``path`` define the results table LLSM reads? (ESA_XML NONE -x, about 20 s.)"""
    from . import esa_xml
    from .xmlio import read_text
    runner = runner or esa_xml.run_on_copy
    with tempfile.TemporaryDirectory(prefix="llsm-start-", ignore_cleanup_errors=True) as temp:
        out = Path(temp) / "export.xml"
        run = runner(Path(path), Path(temp), "NONE", [f"-x{out}"], 600)
        has = out.exists() and export_has_results_table(read_text(out))
    return {"ok": run.succeeded and has, "esa_xml": run.message, "seconds": round(run.seconds, 1),
            "results_table": has}


def prepare(template: Path, target: Path | None = None) -> dict:
    """Store a SCIA-24 copy of ``template`` as the start model, and check that its export works.

    Opens SCIA (nothing else of SCIA may run), opens a copy of the template, saves it with SCIA 24
    (answering "Gegevens converteren?" for that save only) and closes SCIA again.
    """
    from . import esa_xml
    from .autoclick import CONVERT_QUESTION, OLD_SECTIONS_QUESTION, ScopedAnswer
    from .loader import load
    from .session import SciaSession

    template = Path(template).resolve()
    target = Path(target or os.environ.get(ENV) or DEFAULT)
    result: dict = {"ok": False, "template": str(template), "start_model": str(target), "problems": []}
    if not template.is_file():
        result["problems"].append(f"Template not found: {template}")
        return result
    try:
        esa_xml.assert_no_scia()
    except esa_xml.SciaRunning as exc:
        result["problems"].append(str(exc))
        return result
    load()
    target.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="llsm-start-", ignore_cleanup_errors=True) as temp:
        copy = Path(temp) / "template.esa"
        shutil.copyfile(template, copy)
        saved = Path(temp) / "start_model.esa"
        session = SciaSession(visible=False, verbose=False)
        try:
            session.start()
            with ScopedAnswer(lambda: set(session.own_pids), OLD_SECTIONS_QUESTION, CONVERT_QUESTION):
                session.set_project(session.submit(lambda: session.env.OpenProject(str(copy)), timeout_s=300))
            if not session.save_project_as(saved):
                result["problems"].append("SCIA did not save the copy.")
        except Exception as exc:  # a licence question, a start over SCIA's 3-minute limit, a hang
            result["problems"].append(f"SCIA could not open and save the template: {exc}. If SCIA asks about "
                                      "licences, answer it (or set LLSM_ANSWER_LICENCE_NOTICES=1) and run this again.")
        finally:
            session.close()
        if saved.is_file():
            shutil.copyfile(saved, target)
    result["saved_by"] = saved_by_version(target) if target.is_file() else None
    if result["problems"] or not target.is_file():
        return result
    for _ in range(30):  # SCIA takes a few seconds to quit; the export check needs it gone
        if not esa_xml.running_scia():
            break
        time.sleep(1)
    try:
        result["check"] = check(target)
    except esa_xml.SciaRunning as exc:
        result["problems"].append(f"The start model is saved, but its export could not be checked: {exc}")
        return result
    result["names_reserved"] = len(owned_names(target))
    result["seconds"] = round(time.time() - started, 1)
    result["ok"] = result["check"]["ok"]
    if not result["ok"]:
        result["problems"].append(f"The start model's XML export has no {EXPORT_TABLE!r} table: "
                                  "a template needs an XML IO document with 1D internal forces per result class.")
    return result
