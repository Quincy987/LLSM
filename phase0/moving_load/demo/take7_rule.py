"""Demo take 7: traffic on a plate deck from one plain chat message, by the plaatbruggen toetsmodel's rules.

Prepared, not yet recorded. Recording needs the merged main (the panel's MCP server runs the venv's llsm, an
editable install of main) and a free desktop: it starts SCIA full screen and drives the mouse. Run by hand:

  python take7_rule.py prepare     fresh demo model, prompt, make7.py; SCIA screenshot for calibration
  python take7_rule.py chat        record the real panel answering the prompt (no mouse/keyboard)
  python take7_rule.py scia        record SCIA: the original, then the copy cycling cases L1A 01..03, then the groups
  python take7_rule.py montage     cut take7/LLSM-traffic-demo.mp4 (and the sequential version)

Unlike take 6 the prompt is what an engineer types: the model's file name and what they want, no coordinates,
step or thickness. LLSM finds brugdek.esa under Documents, takes the deck's long axis and its 0.3 m thickness from
the model, and writes 3 groups of 14 positions (42 cases, 138 wheel patches; docs/verify/phase4-traffic-rules.md).

The SCIA clicks reuse take 6's coordinates (measured on SCIA 24.0.3015.64, 1929 x 1149 desktop). The active-case
list shows BG1 and then the cases in order, so the three measured rows are L1A 01..03: the tandem driving onto the
deck (front axle at 70, 570 and 1070 mm, at first only the front wheels on the deck). A middle position (L1A 08)
needs a scrolled list: measure it on calibrate.png first if wanted.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from take6 import (  # noqa: F401  (same desktop, same SCIA: the measured positions)
    CASE_ROW_X, CASE_ROWS_Y, COMMAND_HIT, COMMAND_SEARCH, DEMO, FIXTURE, GROUP_ROW, GROUPS_CLOSE, LOAD_CASE_BOX,
    MODEL, PY, VIDEO, _view,
)

TAKE = VIDEO / "take7"
COPY = DEMO / "brugdek_llsm-01.esa"
PROMPT = "Zet de verkeersbelasting LM1 op het brugdek (brugdek.esa) en schrijf het weg."
CASES = [
    ("LLSM-ML TS L1A 01", "Positie 1: de tandem rijdt het dek op, alleen de voorwielen staan erop"),
    ("LLSM-ML TS L1A 02", "Positie 2: 0,5 m verder; vlak bij het steunpunt kleine stappen, zoals het toetsmodel"),
    ("LLSM-ML TS L1A 03", "Positie 3: weer 0,5 m; nu staan ook de achterwielen deels op het dek"),
]

TEXTS = {
    '"""Take 5: chat + SCIA before/after -> mp4. Usage: make5.py':
        '"""Take 7 (traffic by the toetsmodel rules): chat + SCIA before/after -> mp4. Usage: make7.py',
    'HDR_BEFORE = ("SCIA Engineer  -  kademuur_kaal.esa", "(het origineel: palen zonder bedding)")':
        'HDR_BEFORE = ("SCIA Engineer  -  brugdek.esa", "(het origineel: dek zonder verkeer)")',
    'HDR_AFTER = ("SCIA Engineer  -  kademuur_kaal_llsm-01.esa", "(de kopie die LLSM net schreef)")':
        'HDR_AFTER = ("SCIA Engineer  -  brugdek_llsm-01.esa", "(de kopie die LLSM net schreef)")',
    '"32 palen × 3 lagen = 96 lijnsteunpunten invoeren,"': '"3 configuraties × 14 posities = 42 belastinggevallen,"',
    '"elk met een eigen stijfheid en positie op de paal."': '"138 wielvlakken, elk op de plek die het toetsmodel kiest."',
    'END = ["96 lijnsteunpunten",': 'END = ["42 belastinggevallen, 138 wielvlakken",',
    '"kademuur_kaal_llsm-01.esa  ·  32 palen × 3 lagen  ·  daarna opnieuw doorrekenen in SCIA"':
        '"brugdek_llsm-01.esa  ·  posities volgens het toetsmodel plaatbruggen  ·  daarna doorrekenen in SCIA"',
    '"Grondbedding op 32 palen van een kademuur, vanuit één chatbericht"':
        '"Verkeersbelasting LM1 op een brugdek, vanuit één gewone zin"',
    '"kademuur_kaal.esa in SCIA Engineer: 32 palen, nog zonder bedding."':
        '"brugdek.esa in SCIA Engineer: een plaat van 6 × 4 m, 300 mm dik, nog zonder verkeer."',
    '"kademuur_kaal_llsm-01.esa geopend in SCIA Engineer."': '"brugdek_llsm-01.esa geopend in SCIA Engineer."',
    '"Zelfde paal S45: nu drie lijnsteunpunten, één per grondlaag."':
        '"Eén belastinggeval per positie, in drie groepen: rijstrook links, rechts en midden."',
}


def prepare() -> None:
    TAKE.mkdir(parents=True, exist_ok=True)
    if not MODEL.exists():
        shutil.copyfile(FIXTURE, MODEL)
    COPY.unlink(missing_ok=True)  # the chat must write _llsm-01, not _llsm-02
    (TAKE / "prompts_take7.json").write_text(
        json.dumps([{"prompt": PROMPT, "settle": 480, "read": 16}], ensure_ascii=False, indent=1), encoding="utf-8"
    )
    source = (VIDEO / "make5.py").read_text(encoding="utf-8")
    for old, new in TEXTS.items():
        if old not in source:
            raise SystemExit(f"make5.py changed; text not found: {old[:60]}")
        source = source.replace(old, new)
    (VIDEO / "make7.py").write_text(source, encoding="utf-8")
    ctl = [PY, str(VIDEO / "scia_ctl.py")]
    subprocess.run([*ctl, "start", str(MODEL)], check=False)
    subprocess.run([*ctl, "shot", str(TAKE / "calibrate.png")], check=False)
    subprocess.run([*ctl, "close"], check=False)
    print(f"Check the positions in {TAKE / 'calibrate.png'} against take 6's LOAD_CASE_BOX and its neighbours.")


def chat() -> None:
    subprocess.run([PY, str(VIDEO / "record_panel.py"), str(TAKE), str(TAKE / "prompts_take7.json")], check=True)
    if not COPY.exists():
        raise SystemExit(f"the chat did not write {COPY}")


def _steps(before: bool) -> list:
    x, y = LOAD_CASE_BOX
    if before:
        return [*_view(), ["label", "Het brugdek: een plaat van 6 × 4 m, 300 mm dik, nog zonder verkeer", 2.5],
                ["sleep", 3.0], ["shot", "before"], ["sleep", 1.0]]
    steps: list = [*_view(), ["label", "De kopie: de tandem op elke positie die het toetsmodel gebruikt", 2.0],
                   ["sleep", 2.0]]
    for (case, text), row_y in zip(CASES, CASE_ROWS_Y):
        steps += [["click", x, y], ["sleep", 1.2], ["click", CASE_ROW_X, row_y], ["sleep", 0.3], ["move", 1000, 900],
                  ["label", text, 2.2], ["sleep", 2.8], ["shot", case[-6:].replace(" ", "_")]]
    gx, gy = GROUP_ROW
    steps += [["click", *COMMAND_SEARCH], ["sleep", 0.8], ["type", "Belastingsgroepen"], ["sleep", 1.5],
              ["click", *COMMAND_HIT], ["sleep", 2.5], ["click", gx, gy], ["sleep", 0.5],
              ["label", "Drie groepen, één per rijstrookindeling; exclusief, dus steeds één positie per combinatie", 3.0],
              ["sleep", 3.5], ["shot", "groups"], ["click", *GROUPS_CLOSE], ["sleep", 1.0]]
    return steps


def scia() -> None:
    for name, model, sub, before in (("before", MODEL, "frames_scia_before", True), ("after", COPY, "frames_scia", False)):
        steps = TAKE / f"take7_{name}.json"
        steps.write_text(json.dumps(_steps(before), ensure_ascii=False, indent=1), encoding="utf-8")
        subprocess.run([PY, str(VIDEO / "rec5.py"), str(TAKE), str(model), sub, str(steps)], check=True)
    shutil.copyfile(TAKE / "frames_scia_before_before.png", TAKE / "still_before.png")


def montage() -> None:
    for mode, name in (("montage", "LLSM-traffic-demo-montage.mp4"), ("seq", "LLSM-traffic-demo.mp4")):
        subprocess.run([PY, str(VIDEO / "make7.py"), str(TAKE), mode, str(TAKE / name)], check=True)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    {"prepare": prepare, "chat": chat, "scia": scia, "montage": montage}[sys.argv[1]]()
