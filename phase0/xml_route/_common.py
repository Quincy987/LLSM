"""Shared plumbing for the XML route spike: guards, paths, running ESA_XML."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import psutil

from dialogs import DialogWatcher

HERE = Path(__file__).resolve().parent
WORK = HERE / "work"
ESA_XML = Path(r"C:\Program Files\SCIA\Engineer24.0\ESA_XML.exe")
SCIA_PROCESSES = {
    "esastartupscreen.exe",
    "esa.exe",
    "sciaengineer.exe",
    "sciatools.admtoadm.hubclient.exe",
    "sciatools.admtoadm.hubserver.exe",
    "esa_xml.exe",
}
EXIT_MEANING = {
    0: "Succeeded",
    1: "Unable to initialize MFC",
    2: "Missing arguments",
    3: "Invalid arguments",
    4: "Unable to open ProjectFile",
    5: "Calculation failed",
    6: "Unable to initialize application environment",
    7: "Error during update ProjectFile by XMLUpdateFile",
    8: "Error during create export outputs",
    9: "Error during create XML outputs",
    10: "Error during update ProjectFile by XLSX Update",
}
TEMPLATE = Path(
    r"C:\Development\Viktor\viktor-apps\amsterdam-kademuren\app\segment\scia_integration\scia\model.esa"
)
SAMPLE_XML = Path(r"C:\Development\Viktor\paaltje.xml")
SAMPLE_DEF = Path(r"C:\Development\Viktor\viktor.xml.def")


class SciaAlreadyRunning(RuntimeError):
    """Someone's SCIA is open. The spike stops; it never closes it."""


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
        raise SciaAlreadyRunning(f"SCIA is already running, stopping the spike: {found}")


def work_copy(source: Path, name: str) -> Path:
    """Copy a model into the gitignored work folder; originals stay untouched."""
    WORK.mkdir(parents=True, exist_ok=True)
    target = WORK / name
    if target.resolve() == source.resolve():
        raise ValueError("source and target are the same file")
    shutil.copy2(source, target)
    return target


def read_xml_text(path: Path) -> str:
    """ESA_XML writes UTF-16 or UTF-8, depending on the document."""
    raw = path.read_bytes()
    return raw.decode("utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8", "replace")


def assert_writable_target(path: Path) -> None:
    resolved = path.resolve()
    if WORK.resolve() not in resolved.parents:
        raise ValueError(f"refusing to write outside {WORK}: {resolved}")


@dataclass
class Run:
    args: list[str]
    exit_code: int | None
    meaning: str
    seconds: float
    timed_out: bool
    stdout: str
    stderr: str
    log: str
    dialogs: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"exit={self.exit_code} ({self.meaning}) in {self.seconds:.1f}s"
            f"{' TIMEOUT' if self.timed_out else ''}; windows={len(self.dialogs)}"
        )


def _kill_owned(root_pid: int) -> None:
    """Stop a process this spike started, with its children. Never anything else."""
    try:
        root = psutil.Process(root_pid)
    except psutil.Error:
        return
    procs = root.children(recursive=True) + [root]
    for proc in procs:
        try:
            proc.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(procs, timeout=10)


def run_esa_xml(
    args: list[str | Path],
    label: str,
    timeout: float = 300,
) -> Run:
    """Run ESA_XML with a timeout and a dialog log; writes work/<label>.run.json."""
    assert_no_scia()
    WORK.mkdir(parents=True, exist_ok=True)
    for arg in args:
        if isinstance(arg, str) and arg[:2] in ("-o", "-x"):
            assert_writable_target(Path(arg[2:]))
    log_path = WORK / f"{label}.esa.log"
    log_path.unlink(missing_ok=True)
    # ESA_XML runs from the install folder, so every path has to be absolute.
    argv = [str(ESA_XML), *(str(a.resolve() if isinstance(a, Path) else a) for a in args), f"-l{log_path}"]
    print(f"[run:{label}] {subprocess.list2cmdline(argv)}", flush=True)
    started = time.time()
    proc = subprocess.Popen(
        argv,
        cwd=ESA_XML.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
    )
    timed_out = False
    with DialogWatcher(proc.pid) as watcher:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_owned(proc.pid)
            stdout, stderr = proc.communicate()
    code = proc.returncode
    log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    run = Run(
        args=argv,
        exit_code=None if timed_out else code,
        meaning="timed out" if timed_out else EXIT_MEANING.get(code, "unknown exit code"),
        seconds=time.time() - started,
        timed_out=timed_out,
        stdout=stdout,
        stderr=stderr,
        log=log,
        dialogs=[d.describe() for d in watcher.dialogs],
    )
    (WORK / f"{label}.run.json").write_text(json.dumps(asdict(run), indent=2), encoding="utf-8")
    print(f"[run:{label}] {run.summary()}", flush=True)
    return run
