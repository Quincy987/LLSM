"""Demo take 6: a moving LM1 tandem over a plate deck, from one chat message.

Reuses the recording tools of the bedding takes in C:\\Development\\LLSM\\demo_out\\video
(gitignored: recordings and their scripts). Run it on the desktop, with nothing else
going on there: it starts SCIA full screen and drives the mouse for the SCIA parts.

  python take6.py prepare     demo model + prompts + make6.py; SCIA screenshot for calibration
  python take6.py chat        record the real panel answering the prompt (no mouse/keyboard)
  python take6.py scia        record SCIA: the original, then the copy cycling cases 01..03
  python take6.py montage     cut take6/LLSM-moving-load-demo.mp4 (and the sequential version)

Run after the moving-load build is merged, so the panel's MCP server serves
moving_load_preview/moving_load_apply (the venv's llsm is an editable install of main).

The SCIA steps (take6_before.json, take6_after.json) need window coordinates of the
load-case box in SCIA's toolbar. They are not known yet: `prepare` saves a screenshot
(take6/calibrate.png) to read them from; then edit LOAD_CASE_BOX below.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

VIDEO = Path(r"C:\Development\LLSM\demo_out\video")
TAKE = VIDEO / "take6"
DEMO = Path(r"C:\Users\klerk015\Documents\LLSM demo")
MODEL = DEMO / "brugdek.esa"
COPY = DEMO / "brugdek_llsm-01.esa"
FIXTURE = Path(__file__).resolve().parents[2] / "xml_route" / "work" / "plate_base.esa"  # make_plate.py
PY = sys.executable

PROMPT = (
    f"Zet een LM1-tandem (TS1) over het dek van {MODEL}: rijlijn van (500, 2000) naar (4900, 2000) mm, "
    "stap 1600 mm. Laat de tabel zien en schrijf het daarna meteen weg."
)
LOAD_CASE_BOX = None  # (x, y) of SCIA's active-load-case box, window-relative; read from calibrate.png
CASES = ["LLSM-ML TS1 01", "LLSM-ML TS1 02", "LLSM-ML TS1 03"]

TEXTS = {
    '"""Take 5: chat + SCIA before/after -> mp4. Usage: make5.py':
        '"""Take 6 (moving load): chat + SCIA before/after -> mp4. Usage: make6.py',
    'HDR_BEFORE = ("SCIA Engineer  -  kademuur_kaal.esa", "(het origineel: palen zonder bedding)")':
        'HDR_BEFORE = ("SCIA Engineer  -  brugdek.esa", "(het origineel: dek zonder verkeer)")',
    'HDR_AFTER = ("SCIA Engineer  -  kademuur_kaal_llsm-01.esa", "(de kopie die LLSM net schreef)")':
        'HDR_AFTER = ("SCIA Engineer  -  brugdek_llsm-01.esa", "(de kopie die LLSM net schreef)")',
    '"32 palen × 3 lagen = 96 lijnsteunpunten invoeren,"': '"3 posities × 4 wielen = 12 puntlasten invoeren,"',
    '"elk met een eigen stijfheid en positie op de paal."': '"elk op zijn eigen x en y, in een eigen belastinggeval."',
    'END = ["96 lijnsteunpunten",': 'END = ["3 belastinggevallen, 12 wiellasten",',
    '"kademuur_kaal_llsm-01.esa  ·  32 palen × 3 lagen  ·  daarna opnieuw doorrekenen in SCIA"':
        '"brugdek_llsm-01.esa  ·  één exclusieve groep LLSM-ML TS1  ·  daarna doorrekenen in SCIA"',
    '"Grondbedding op 32 palen van een kademuur, vanuit één chatbericht"':
        '"Een rijdende LM1-tandem over een brugdek, vanuit één chatbericht"',
}


def prepare() -> None:
    TAKE.mkdir(parents=True, exist_ok=True)
    if not MODEL.exists():
        shutil.copyfile(FIXTURE, MODEL)
    COPY.unlink(missing_ok=True)  # the chat must write _llsm-01, not _llsm-02
    (TAKE / "prompts_take6.json").write_text(
        json.dumps([{"prompt": PROMPT, "settle": 480, "read": 14}], ensure_ascii=False, indent=1), encoding="utf-8"
    )
    source = (VIDEO / "make5.py").read_text(encoding="utf-8")
    for old, new in TEXTS.items():
        if old not in source:
            raise SystemExit(f"make5.py changed; text not found: {old[:60]}")
        source = source.replace(old, new)
    (VIDEO / "make6.py").write_text(source, encoding="utf-8")
    ctl = [PY, str(VIDEO / "scia_ctl.py")]
    subprocess.run([*ctl, "start", str(MODEL)], check=False)
    subprocess.run([*ctl, "shot", str(TAKE / "calibrate.png")], check=False)
    subprocess.run([*ctl, "close"], check=False)
    print(f"Read the load-case box position from {TAKE / 'calibrate.png'} and set LOAD_CASE_BOX.")


def chat() -> None:
    subprocess.run([PY, str(VIDEO / "record_panel.py"), str(TAKE), str(TAKE / "prompts_take6.json")], check=True)
    if not COPY.exists():
        raise SystemExit(f"the chat did not write {COPY}")


def _steps(before: bool) -> list:
    if LOAD_CASE_BOX is None:
        raise SystemExit("set LOAD_CASE_BOX first (python take6.py prepare)")
    x, y = LOAD_CASE_BOX
    if before:
        return [["label", "Het brugdek: een plaat van 6 × 4 m op vier kolommen, nog zonder verkeer", 2.5],
                ["sleep", 3.0], ["shot", "before"], ["sleep", 1.0]]
    steps: list = [["label", "De kopie: één belastinggeval per positie van de tandem", 2.0], ["sleep", 2.0]]
    for case in CASES:
        steps += [["click", x, y], ["sleep", 1.0], ["type", case], ["key", "enter"], ["sleep", 0.8],
                  ["label", f"{case}: de tandem schuift 1600 mm op", 2.0], ["sleep", 2.5], ["shot", case[-2:]]]
    return steps


def scia() -> None:
    for name, model, sub, before in (("before", MODEL, "frames_scia_before", True), ("after", COPY, "frames_scia", False)):
        steps = TAKE / f"take6_{name}.json"
        steps.write_text(json.dumps(_steps(before), ensure_ascii=False, indent=1), encoding="utf-8")
        subprocess.run([PY, str(VIDEO / "rec5.py"), str(TAKE), str(model), sub, str(steps)], check=True)
    shutil.copyfile(TAKE / "frames_scia_before_before.png", TAKE / "still_before.png")


def montage() -> None:
    for mode, name in (("montage", "LLSM-moving-load-demo-montage.mp4"), ("seq", "LLSM-moving-load-demo.mp4")):
        subprocess.run([PY, str(VIDEO / "make6.py"), str(TAKE), mode, str(TAKE / name)], check=True)


if __name__ == "__main__":
    {"prepare": prepare, "chat": chat, "scia": scia, "montage": montage}[sys.argv[1]]()
