"""Try 7: SCIA's normal "Calculate" command, with LLSM pressing its button.

Try 5 showed ExecuteCommandString("CMD.TOOLS.CALCULATION") opens SCIA's Win32 dialog "EE berekening"
(Lineaire analyse, Belastingsgevallen: 2, button "Bereken") and waits for it. This try presses
"Bereken" with BM_CLICK on that button, records every later dialog (title, texts, buttons, picture)
and measures files and the read-back.

Run: python try07_gui_calculate.py [visible]
"""
import queue
import sys
import threading
import time

from common import VARIANTS, build_variant, diff, forces, launch, log, log_mark, log_since, run, snap, solver_link_sizes, t, verdict

VISIBLE = "visible" in sys.argv[1:]
VARIANT = next((a for a in sys.argv[1:] if a in VARIANTS), "base")
BM_CLICK = 0x00F5


def com_app(sess):
    import clr
    import EnvESA80
    from System.Reflection import BindingFlags as BF
    fl = BF.Public | BF.NonPublic | BF.Instance
    proj = sess.project
    sw = clr.GetClrType(type(proj)).GetField("ServiceWrapper", fl).GetValue(proj)
    env = sw.GetType().GetField("MyEnvEsa", fl).GetValue(sw)
    return EnvESA80.IEnvESAApp(EnvESA80.IEnvESA(env).ESAApp)


def uia_read(hwnd: int) -> dict:
    """Texts and buttons of a window through UI Automation (Qt draws its own widgets, Win32 sees none)."""
    import clr
    clr.AddReference("UIAutomationClient, Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35")
    clr.AddReference("UIAutomationTypes, Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35")
    from System.Windows.Automation import AutomationElement, Condition, ControlType, TreeScope
    import System
    root = AutomationElement.FromHandle(System.IntPtr(hwnd))
    texts, buttons = [], []
    for el in root.FindAll(TreeScope.Descendants, Condition.TrueCondition):
        name = str(el.Current.Name or "").strip()
        kind = el.Current.ControlType
        if kind == ControlType.Button:
            buttons.append(name)
        elif name:
            texts.append(name)
    return {"texts": texts, "buttons": buttons}


def uia_press(hwnd: int, label: str) -> bool:
    import clr
    clr.AddReference("UIAutomationClient, Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35")
    from System.Windows.Automation import AutomationElement, Condition, ControlType, InvokePattern, TreeScope
    import System
    root = AutomationElement.FromHandle(System.IntPtr(hwnd))
    for el in root.FindAll(TreeScope.Descendants, Condition.TrueCondition):
        if el.Current.ControlType == ControlType.Button and str(el.Current.Name).strip().lower() == label.lower():
            el.GetCurrentPattern(InvokePattern.Pattern).Invoke()
            return True
    return False


def press(dialog_hwnd: int, button_hwnd: int, how: str) -> None:
    import win32api
    import win32con
    import win32gui
    if how == "bm_click":
        win32gui.PostMessage(button_hwnd, BM_CLICK, 0, 0)
    elif how == "wm_command":
        ctrl = win32gui.GetDlgCtrlID(button_hwnd)
        win32gui.PostMessage(dialog_hwnd, win32con.WM_COMMAND, (win32con.BN_CLICKED << 16) | ctrl, button_hwnd)
    elif how == "space":
        win32gui.PostMessage(button_hwnd, win32con.WM_KEYDOWN, win32con.VK_SPACE, 0x00390001)
        win32gui.PostMessage(button_hwnd, win32con.WM_KEYUP, win32con.VK_SPACE, 0xC0390001)
    elif how == "mouse":  # last resort: a real click, which needs the window in front
        l, tp, r, b = win32gui.GetWindowRect(button_hwnd)
        try:
            win32gui.SetForegroundWindow(dialog_hwnd)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
        old = win32api.GetCursorPos()
        win32api.SetCursorPos(((l + r) // 2, (tp + b) // 2))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
        time.sleep(0.2)
        win32api.SetCursorPos(old)


def dialog_hand(stop: threading.Event, seen: list) -> None:
    """Press "Bereken" in "EE berekening" (escalating), record every window of our SCIA as it changes."""
    import win32gui
    from llsm.scia.autoclick import _capture, visible_windows
    from llsm.scia.session import our_scia_processes
    recorded, pressed, last_state = set(), {}, None
    qt_seen, qt_shot, qt_answered = {}, set(), set()
    ways = ["bm_click", "wm_command", "space", "mouse"]
    while not stop.is_set():
        pids = {p.pid for p in our_scia_processes()}
        wins = [w for w in (visible_windows(pids) if pids else []) if w.width < 1800 and w.rect[0] > -1000]
        state = sorted((w.title, w.cls) for w in wins)
        if state != last_state:
            log("windows now", state)
            last_state = state
        for w in wins:
            # a Qt message box after "Bereken": photograph it again once drawn, then answer it with Return
            if w.cls.startswith("Qt") and "OwnDC" not in w.cls and w.width < 1000 and w.title == "SCIA Engineer":
                first = qt_seen.setdefault(w.hwnd, time.time())
                age = time.time() - first
                for mark in (2, 6):
                    if age > mark and (w.hwnd, mark) not in qt_shot:
                        qt_shot.add((w.hwnd, mark))
                        entry = {"at": time.strftime("%H:%M:%S"), "title": w.title, "cls": w.cls, "rect": w.rect,
                                 "age_s": mark, "picture": str(_capture(w))}
                        seen.append(entry)
                        log("qt message", entry)
                if age > 3 and w.hwnd not in qt_answered:
                    import win32api
                    import win32con
                    qt_answered.add(w.hwnd)
                    try:
                        content = uia_read(w.hwnd)
                    except Exception as e:  # noqa: BLE001
                        content = {"error": f"{type(e).__name__}: {e}"}
                    seen.append({"at": time.strftime("%H:%M:%S"), "qt_content": content})
                    log("qt message content", content)
                    how = "Return"
                    for label in ("OK", "Ja", "Yes", "Doorgaan"):
                        try:
                            if uia_press(w.hwnd, label):
                                how = f"UIA invoke {label}"
                                break
                        except Exception as e:  # noqa: BLE001
                            log("uia press failed", e)
                    if how == "Return":
                        win32api.PostMessage(w.hwnd, win32con.WM_KEYDOWN, win32con.VK_RETURN, 1)
                        win32api.PostMessage(w.hwnd, win32con.WM_KEYUP, win32con.VK_RETURN, 3 << 30 | 1)
                    seen.append({"at": time.strftime("%H:%M:%S"), "answered_qt": how})
                    log("answered the Qt message:", how)
            key = (w.hwnd, w.title, tuple(w.texts))
            if key not in recorded:
                recorded.add(key)
                entry = {"at": time.strftime("%H:%M:%S"), "title": w.title, "cls": w.cls, "texts": list(w.texts),
                         "buttons": w.labels, "picture": str(_capture(w))}
                seen.append(entry)
                log("window", entry)
            if w.title == "EE berekening" and w.cls == "#32770":
                button = next((h for h, label in w.buttons if label.replace("&", "").strip().lower() == "bereken"), None)
                n, at = pressed.get(w.hwnd, (0, 0.0))
                if button and n < len(ways) and time.time() - at > 6:
                    press(w.hwnd, button, ways[n])
                    pressed[w.hwnd] = (n + 1, time.time())
                    log("pressed Bereken by", ways[n])
                    seen.append({"at": time.strftime("%H:%M:%S"), "pressed": ways[n]})
        time.sleep(0.3)


def body(ctx, result):
    result["variant"] = VARIANT
    build_variant(ctx, VARIANT)
    launch(ctx, visible=VISIBLE)
    log("push", t.push_model(ctx))
    sess = ctx.session
    s0 = snap()
    log("mesh", sess.submit(lambda: sess.project.CreateMesh(), timeout_s=300))
    s1 = snap()
    log("mesh files", len(diff(s0, s1)), "SolverLinkData", solver_link_sizes(s1))

    import System

    def calculate():
        out = System.String("")
        return str(com_app(sess).ExecuteCommandString("CMD.TOOLS.CALCULATION", "", out))

    stop, seen = threading.Event(), []
    threading.Thread(target=dialog_hand, args=(stop, seen), daemon=True).start()
    m1 = log_mark()
    t1 = time.time()
    reply: queue.Queue = queue.Queue()
    sess._call_q.put((calculate, reply))  # the dialog is expected: bypass submit's dialog guard
    try:
        status, payload = reply.get(timeout=300)
    except queue.Empty:
        status, payload = "timeout", "no return within 300 s"
    secs = round(time.time() - t1, 1)
    time.sleep(3)
    s2 = snap()
    files = diff(s1, s2)
    result["calc"] = {"status": status, "returned": str(payload), "seconds": secs, "files": files,
                      "solver_link": solver_link_sizes(s2), "logs": log_since(m1, 30)}
    log("calculate ->", status, payload, f"({secs} s),", len(files), "files changed; SolverLinkData", solver_link_sizes(s2))
    time.sleep(2)
    stop.set()
    result["dialogs"] = seen
    result["solver_message"] = [d["qt_content"] for d in seen if "qt_content" in d]
    log("solver message", result["solver_message"])
    if status != "ok":
        return
    # reach the results through LLSM's own read path
    sess._clicker.pending = None
    ctx.rapi = sess.submit(lambda: sess.project.Model.InitializeResultsAPI(), timeout_s=120)
    ctx.last_calc_ok = ctx.rapi is not None
    cases = VARIANTS[VARIANT][0]
    result["read"] = {f"{b}/{c}": forces(ctx, b, c) for b in ("Veld1", "Veld2") for c in cases}
    for k, v in result["read"].items():
        log("read", k, v)
    result["verdict"] = verdict(files, result["read"][f"Veld1/{cases[-1]}"])
    log("verdict", result["verdict"])


if __name__ == "__main__":
    run(f"try07_gui_calculate_{VARIANT}", body)
