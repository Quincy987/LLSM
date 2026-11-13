"""Demo take 7: a results report of the moving-load copy, from one chat message.

  python take7.py prepare     checks, demo folder clean-up, a patched panel recorder, a dry check
                              that a server started with this build serves report_write
  python take7.py chat        record the real panel answering the prompt (no mouse/keyboard driving)
  python take7.py montage     cut the mp4: title, chat (the print's wait sped up), the PDF's pages, end card

Run with the venv's python for `prepare` and `chat` (the panel needs mcp, pythonnet), and with the
system python for `montage` (PyMuPDF, imageio-ffmpeg). Nothing here drives the mouse: the panel is
filled in-process (as take 6's chat), and the PDF is rendered from the file.

The venv's llsm is an editable install of the main checkout, so the panel would serve main's
tools. `chat` therefore runs the recorder with PYTHONPATH = this checkout's src, and the recorder
copy written by `prepare` puts the same src first; the MCP server the panel spawns
(`python -m llsm.mcp_server`) inherits it. `prepare` proves that with a server of its own.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SRC = REPO / "src"
VIDEO = Path(r"C:\Development\LLSM\demo_out\video")
TAKE = VIDEO / "take7"
DEMO = Path(r"C:\Users\klerk015\Documents\LLSM demo")
MODEL = DEMO / "brugdek_llsm-01.esa"
OUT_MP4 = DEMO / "LLSM-report-demo.mp4"
PROMPT = f"Maak een rapport van {MODEL}, per belastinggeval, met doorrekenen."
SETTLE_S = 2400  # calculate + XLSX + PDF of a 36 MB model: 10-15 min


def _env() -> dict:
    return {**os.environ, "PYTHONPATH": str(SRC), "PYTHONIOENCODING": "utf-8"}


def _scia_running() -> list[str]:
    import psutil
    names = {"esa.exe", "sciaengineer.exe", "esa_xml.exe", "esastartupscreen.exe"}
    return [p.info["name"] for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() in names]


def _teams_call() -> list[str]:
    """Visible Teams windows titled with a person or meeting, not the main 'Chat | ...' window."""
    import win32gui
    found = []

    def visit(hwnd, _):
        title = win32gui.GetWindowText(hwnd)
        if win32gui.IsWindowVisible(hwnd) and "Microsoft Teams" in title and not title.startswith(("Chat |", "Activiteit |", "Activity |")):
            found.append(title)
        return True

    win32gui.EnumWindows(visit, None)
    return found


async def _served_tools(info: dict) -> list[str]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    async with streamablehttp_client(f"http://127.0.0.1:{info['port']}/mcp", headers={"X-LLSM-Token": info["token"]}) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            return [t.name for t in (await session.list_tools()).tools]


def prepare() -> None:
    TAKE.mkdir(parents=True, exist_ok=True)
    if _scia_running():
        raise SystemExit(f"SCIA is running ({_scia_running()}); close it first.")
    if not MODEL.exists():
        raise SystemExit(f"{MODEL} is missing: record take 6 first (it writes the moving-load copy).")
    for old in DEMO.glob(f"{MODEL.stem}_rapport-*"):
        old.unlink()  # the chat must write _rapport-01
    source = (VIDEO / "record_panel.py").read_text(encoding="utf-8")
    line = 'sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))'
    if line not in source:
        raise SystemExit("record_panel.py changed; the line that sets the src path was not found")
    (TAKE / "record_panel7.py").write_text(source.replace(line, f"sys.path.insert(0, {str(SRC)!r})"), encoding="utf-8")
    shutil.copyfile(VIDEO / "recorder.py", TAKE / "recorder.py") if (VIDEO / "recorder.py").exists() else None
    (TAKE / "prompts_take7.json").write_text(
        json.dumps([{"prompt": PROMPT, "settle": SETTLE_S, "read": 16}], ensure_ascii=False, indent=1), encoding="utf-8")

    pid_file, port_file = TAKE / "check_pid.json", TAKE / "check_port.txt"
    for f in (pid_file, port_file):
        f.unlink(missing_ok=True)
    proc = subprocess.Popen([sys.executable, "-m", "llsm.mcp_server", "--http", "--port-file", str(port_file),
                             "--pid-file", str(pid_file)], env=_env(), cwd=TAKE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            if pid_file.exists() and pid_file.stat().st_size:
                break
            time.sleep(1)
        tools = asyncio.run(_served_tools(json.loads(pid_file.read_text())))
    finally:
        proc.terminate()
        proc.wait(30)
    if "report_write" not in tools:
        raise SystemExit(f"a server started with PYTHONPATH={SRC} does not serve report_write: {tools}")
    print(f"ok: {len(tools)} tools served by this build, report_write among them")


def chat() -> None:
    if _scia_running():
        raise SystemExit("SCIA is running; stop.")
    calls = _teams_call()
    if calls:
        raise SystemExit(f"A Teams call window is open ({calls[0][:60]}); not recording.")
    subprocess.run([sys.executable, str(TAKE / "record_panel7.py"), str(TAKE), str(TAKE / "prompts_take7.json")],
                   env=_env(), check=True, cwd=VIDEO)
    timeline = json.loads((TAKE / "timeline.json").read_text(encoding="utf-8"))
    tools = [e.get("tool") for e in timeline if e["kind"] == "tool"]
    print("tools called:", tools)
    pdfs = sorted(DEMO.glob(f"{MODEL.stem}_rapport-*.pdf"))
    if not any("report_write" in (t or "") for t in tools) or not pdfs:
        raise SystemExit(f"the chat did not print a report (tools {tools}, PDFs {pdfs})")


# --- montage (system python) ---------------------------------------------------------------------

W, H, FPS = 1600, 900, 12
BG, FG, ACCENT, MUTED = (246, 247, 248), (30, 34, 40), (0, 128, 128), (110, 116, 124)


def _font(size, bold=False):
    from PIL import ImageFont
    for name in (("segoeuib.ttf" if bold else "segoeui.ttf"), "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _card(lines, sub=None):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    y = H // 2 - 40 * len(lines)
    for i, text in enumerate(lines):
        f = _font(54 if i == 0 else 34, bold=i == 0)
        d.text(((W - d.textlength(text, font=f)) / 2, y), text, fill=ACCENT if i == 0 else FG, font=f)
        y += 80 if i == 0 else 56
    if sub:
        f = _font(24)
        d.text(((W - d.textlength(sub, font=f)) / 2, H - 90), sub, fill=MUTED, font=f)
    return img


def _fit(img, caption=None):
    from PIL import Image, ImageDraw
    canvas = Image.new("RGB", (W, H), BG)
    top = 70 if caption else 20
    scale = min((W - 40) / img.width, (H - top - 20) / img.height)
    small = img.resize((int(img.width * scale), int(img.height * scale)))
    canvas.paste(small, ((W - small.width) // 2, top))
    if caption:
        d = ImageDraw.Draw(canvas)
        d.text((30, 18), caption, fill=FG, font=_font(28, True))
    return canvas


def _chat_frames(writer):
    from PIL import Image, ImageDraw
    frames = TAKE / "frames"
    index = [line.split() for line in (frames / "index.txt").read_text().splitlines() if line.strip()]
    index = [(int(n), float(t)) for n, t, *_ in index]
    events = json.loads((TAKE / "timeline.json").read_text(encoding="utf-8"))
    t = {e["kind"]: e["t"] for e in events}
    sent, settled = t["sent"], t["settled"]
    end = settled + 16

    def frame_at(when):
        n = min(index, key=lambda item: abs(item[1] - when))[0]
        return Image.open(frames / f"{n:06d}.jpg").convert("RGB")

    def emit(img, caption, count=1):
        out = _fit(img, caption)
        for _ in range(count):
            writer.send(_np(out))

    step = 1.0 / FPS
    when = t["type_start"] - 1
    while when < sent + 4:  # typing and the first seconds, in real time
        emit(frame_at(when), "Eén chatbericht in het LLSM-paneel")
        when += step
    wait = settled - sent
    for i in range(10 * FPS):  # the print, sped up to 10 s
        moment = sent + 4 + (wait - 4) * i / (10 * FPS)
        minutes = int((moment - sent) // 60)
        emit(frame_at(moment), f"SCIA rekent een kopie door en drukt het rapport af ({minutes} min, versneld)")
    when = settled
    while when < end:
        emit(frame_at(when), "LLSM controleert het rapport: elk resultaathoofdstuk gevuld")
        when += step
    return wait


def _np(img):
    import numpy as np
    return np.asarray(img, dtype=np.uint8)


def _pdf_pages(pdf: Path) -> list[tuple[str, "object"]]:
    """(caption, image) for three pages: input, reactions per case, a 2D chapter."""
    import fitz
    from PIL import Image
    doc = fitz.open(pdf)
    wanted = [
        ("Invoer: de drie belastinggevallen van de rijdende belasting", ("Belastingsgevallen", "LLSM-ML TS1 02")),
        ("Reacties per geval: de vier kolommen dragen samen 600 kN", ("Resultante van reacties", "LLSM-ML TS1 02")),
        ("Interne 2D-krachten in het dek, per geval", ("Interne 2D-krachten", "LLSM-ML TS1 02")),
    ]
    pages = []
    for caption, needles in wanted:
        hit = next((p for p in doc if all(n in p.get_text() for n in needles)), None)
        if hit is None:
            continue
        pix = hit.get_pixmap(dpi=110)
        pages.append((caption, Image.frombytes("RGB", (pix.width, pix.height), pix.samples), hit.number + 1))
    return pages


def montage() -> None:
    import imageio_ffmpeg
    pdf = sorted(DEMO.glob(f"{MODEL.stem}_rapport-*.pdf"))[-1]
    writer = imageio_ffmpeg.write_frames(str(OUT_MP4), (W, H), fps=FPS, quality=8, macro_block_size=8)
    writer.send(None)
    title = _card(["LLSM", "Een rapport van een SCIA-model, vanuit één chatbericht"], "brugdek met een rijdende LM1-tandem, drie posities")
    for _ in range(3 * FPS):
        writer.send(_np(title))
    wait = _chat_frames(writer)
    pages = _pdf_pages(pdf)
    for caption, page, number in pages:
        from PIL import Image
        crop_h = int(page.width * (H - 90) / (W - 40))
        travel = max(0, page.height - crop_h)
        for i in range(5 * FPS):  # scroll down the page in 5 s
            y = int(travel * i / (5 * FPS - 1)) if travel else 0
            writer.send(_np(_fit(page.crop((0, y, page.width, y + min(crop_h, page.height))), f"{caption}  (p. {number})")))
    end = _card(["Rapport afgedrukt en gecontroleerd", "model ongewijzigd"],
                f"{pdf.name}  ·  SCIA's eigen engineering report  ·  {round(wait / 60)} min, op een kopie")
    for _ in range(4 * FPS):
        writer.send(_np(end))
    writer.close()
    for i, (caption, page, number) in enumerate(pages):
        page.save(DEMO / f"LLSM-report-page{i + 1}.jpg", quality=88)
    print(f"{OUT_MP4} written; pages used: {[n for _, _, n in pages]}")


if __name__ == "__main__":
    {"prepare": prepare, "chat": chat, "montage": montage}[sys.argv[1]]()
