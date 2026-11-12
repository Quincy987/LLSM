"""Update a saved SCIA model through ``ESA_XML.exe``, without opening SCIA.

An exit code of 0 proves nothing here: SCIA also saves a support on a member that does
not exist. Callers must read the saved file back.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import quoteattr

import psutil
import pywintypes
import win32con
import win32file

from . import loader

DEF_FILE = Path(__file__).resolve().parent / "resources" / "line_supports.xml.def"
LOADS_DEF_FILE = Path(__file__).resolve().parent / "resources" / "loads.xml.def"

EXIT_MEANING = {
    0: "Succeeded",
    1: "Unable to initialize MFC",
    2: "Missing arguments",
    3: "Invalid arguments",
    4: "Unable to open the project file",
    5: "Calculation failed",
    6: "Unable to start SCIA's environment; if this is a licence error, open SCIA once to renew the token",
    7: "SCIA rejected the XML update",
    8: "Error while creating export outputs",
    9: "Error while creating XML outputs",
    10: "SCIA rejected the XLSX update",
}

SCIA_PROCESSES = {
    "esastartupscreen.exe",
    "esa.exe",
    "sciaengineer.exe",
    "sciatools.admtoadm.hubclient.exe",
    "sciatools.admtoadm.hubserver.exe",
    "esa_xml.exe",
}

_HEADERS = (
    "Reference Table", "Constraint", "Type", "X", "Stiffness X", "Y", "Stiffness Y", "Z", "Stiffness Z",
    "Rx", "Stiffness Rx", "Ry", "Stiffness Ry", "Rz", "Stiffness Rz", "Function X", "Function Y",
    "Function Z", "Function Rx", "Function Ry", "Function Rz", "System", "Coord. definition", "Extent",
    "Position x1", "Position x2", "Origin",
)
_BEAM_CLSID = "{ECB5D684-7357-11D4-9F6C-00104BC3B443}"
_BEAM_PROGID = "EP_DSG_Elements.EP_Beam.1"
_FIRST_OBJECT_ID = 9001


class EsaXmlError(RuntimeError):
    """The update could not be run, or SCIA refused it."""


class SciaRunning(EsaXmlError):
    """An SCIA is open on this machine. We stop; we never close someone's SCIA."""


class ModelLocked(EsaXmlError):
    """The model file is open elsewhere, most likely in SCIA."""


@dataclass(frozen=True)
class UpdateSupport:
    """One flexible line support: Y and Z sprung, X and rotations free."""

    name: str
    member_name: str  # what SCIA resolves the member by
    member_id: int | None
    x1: float
    x2: float
    stiffness: float  # N/m2
    torsion: float = 0.0  # spring about the member axis; 0 leaves it free


@dataclass(frozen=True)
class RunResult:
    exit_code: int | None
    message: str
    seconds: float
    stderr: str
    log: str

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


def running_scia() -> list[tuple[int, str]]:
    found = []
    for proc in psutil.process_iter(["pid", "name"]):
        name = (proc.info.get("name") or "").lower()
        if name in SCIA_PROCESSES:
            found.append((proc.info["pid"], name))
    return found


def assert_no_scia() -> None:
    found = running_scia()
    if found:
        raise SciaRunning(f"SCIA is running ({', '.join(name for _, name in found)}). Close it and try again.")


def assert_unlocked(path: Path) -> None:
    """Refuse a file that another process has open."""
    try:
        handle = win32file.CreateFile(
            str(path),
            win32con.GENERIC_READ,  # read only, so a read-only model is not mistaken for a locked one
            0,  # no sharing: fails when anything else holds the file
            None,
            win32con.OPEN_EXISTING,
            win32con.FILE_ATTRIBUTE_NORMAL,
            None,
        )
    except pywintypes.error as exc:
        raise ModelLocked(f"{Path(path).name} is open elsewhere, probably in SCIA: {exc.strerror}") from exc
    win32file.CloseHandle(handle)


def build_update(supports: list[UpdateSupport], def_name: str = DEF_FILE.name) -> str:
    """The XML that adds or overwrites these line supports, keyed by support name."""
    header = "\n".join(f'            <h{i} t="{text}"/>' for i, text in enumerate(_HEADERS))
    objects = "\n".join(_support_object(_FIRST_OBJECT_ID + i, s) for i, s in enumerate(supports))
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<project xmlns="http://www.scia.cz">
   <def uri={quoteattr(def_name)}/>
   <container id="{{61FC64DE-5B75-4074-BF61-DA1AAA0A194C}}" t="DataAddSupport.EP_LineSupportLine.1">
      <table id="F1872059-F8EE-4009-8094-18C23FDC874D" t="DataAddSupport.EP_LineSupportLine.1" name="Line supports on beam">
         <h>
{header}
         </h>
{objects}
      </table>
   </container>
</project>
"""


def _support_object(object_id: int, support: UpdateSupport) -> str:
    rotation_x = f'<p9 v="2"/>\n           <p10 v="{support.torsion}"/>' if support.torsion else '<p9 v="0"/>'
    return f"""         <obj id="{object_id}" nm={quoteattr(support.name)}>
           <p0 t="">
             <h>
               <h0 t="Member Type"/>
               <h1 t="Member Type Name"/>
               <h2 t="Member Id"/>
               <h3 t="Member Name"/>
             </h>
             <row id="0">
               <p0 v="{_BEAM_CLSID}"/>
               <p1 v="{_BEAM_PROGID}"/>
               <p2 v="{support.member_id or 0}"/>
               <p3 v={quoteattr(support.member_name)}/>
             </row>
           </p0>
           <p1 v="3"/>
           <p2 v="0"/>
           <p3 v="0"/>
           <p5 v="2"/>
           <p6 v="{support.stiffness}"/>
           <p7 v="2"/>
           <p8 v="{support.stiffness}"/>
           {rotation_x}
           <p11 v="0"/>
           <p13 v="0"/>
           <p24 v="{support.x1}"/>
           <p25 v="{support.x2}"/>
         </obj>
"""


def _esa_xml_exe() -> Path:
    try:
        exe = loader.discover().install_folder / "ESA_XML.exe"
    except OSError as exc:
        raise EsaXmlError("SCIA Engineer 24.0 was not found on this computer.") from exc
    if not exe.is_file():
        raise EsaXmlError(f"ESA_XML.exe not found at {exe}")
    return exe


def _stop(proc: subprocess.Popen) -> None:
    """Stop the ESA_XML this call started, with its children. Never anything else."""
    try:
        root = psutil.Process(proc.pid)
    except psutil.Error:
        return
    family = root.children(recursive=True) + [root]
    for member in family:
        try:
            member.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(family, timeout=10)


def run_update(
    source: Path, output: Path, xml_text: str, timeout: float = 600, def_file: Path = DEF_FILE
) -> RunResult:
    """Apply an update to a temporary copy of ``source`` and save the result as ``output``.

    ``source`` is only ever read. On any failure no ``output`` is left behind. ``def_file``
    is the table definition the XML names in its ``<def uri>``.
    """
    source, output = Path(source).resolve(), Path(output).resolve()  # ESA_XML runs from its install folder
    assert_no_scia()
    assert_unlocked(source)
    exe = _esa_xml_exe()
    # A stopped ESA_XML can hold its files for a moment; that must not hide the result.
    with tempfile.TemporaryDirectory(prefix="llsm-update-", ignore_cleanup_errors=True) as temp:
        folder = Path(temp)
        working_copy = folder / "input.esa"
        shutil.copyfile(source, working_copy)  # no attributes: a read-only model must give a writable copy
        update = folder / "update.xml"
        update.write_text(xml_text, encoding="utf-8")
        shutil.copyfile(def_file, folder / Path(def_file).name)
        log_path = folder / "esa_xml.log"
        argv = [str(exe), "NONE", str(working_copy), str(update), "-tESA", f"-o{output}", f"-l{log_path}"]

        started = time.time()
        proc = subprocess.Popen(
            argv, cwd=exe.parent, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace"
        )
        try:
            _, stderr = proc.communicate(timeout=timeout)
            code: int | None = proc.returncode
        except subprocess.TimeoutExpired:
            _stop(proc)
            _, stderr = proc.communicate()
            code = None
        log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""

    message = f"Timed out after {timeout:.0f} s" if code is None else EXIT_MEANING.get(code, f"Unknown exit code {code}")
    result = RunResult(code, message, time.time() - started, stderr, log)
    if not result.succeeded:
        output.unlink(missing_ok=True)
    return result
