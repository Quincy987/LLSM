from __future__ import annotations
import queue, threading
import tkinter as tk
from tkinter import scrolledtext, ttk
import psutil, win32api, win32con, win32gui, win32process
from .llm.anthropic_backend import AnthropicBackend
from .llm.claude_code_backend import ClaudeCodeBackend
from .scia.loader import load as load_scia
from . import mcp_supervisor

GWLP_HWNDPARENT = -8
SCIA_PROC_NAMES = {"sciaengineer.exe"}
POLL_INTERVAL_MS = 250
PANEL_WIDTH = 460


def _find_scia_main_hwnd():
    pids = {p.info["pid"] for p in psutil.process_iter(["pid", "name"])
            if p.info["name"] and p.info["name"].lower() in SCIA_PROC_NAMES}
    if not pids:
        return None
    candidates = []
    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid not in pids:
            return True
        title = win32gui.GetWindowText(hwnd) or ""
        if "SCIA Engineer" not in title:
            return True
        if "Startup Screen" in title or "Splash" in title:
            return True
        rect = win32gui.GetWindowRect(hwnd)
        area = (rect[2] - rect[0]) * (rect[3] - rect[1])
        candidates.append((area, hwnd))
        return True
    win32gui.EnumWindows(cb, None)
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


class LlsmPanel:
    def __init__(self, *, model=None, backend_kind='claude-code'):
        self.scia_hwnd = None
        self.outbox = queue.Queue()
        self.busy = False
        self._backend = None
        self._model = model
        self._backend_kind = backend_kind
        self._mcp_handle = None  # mcp_supervisor.McpHandle | None
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._start_mcp_server()
        self._poll_scia()
        self._drain_outbox()

    def _start_mcp_server(self):
        """Spawn the persistent MCP server child. Logs to the panel on success/failure.

        Only used when the LLM backend talks via Claude Code (which speaks MCP).
        The direct-API backend has no MCP layer, so we skip the child for it.
        """
        if self._backend_kind != "claude-code":
            self._sysline("backend=api: persistent MCP server not started")
            return
        try:
            self._mcp_handle = mcp_supervisor.spawn(verbose=True)
            self._sysline(f"MCP server up at {self._mcp_handle.url}")
        except Exception as exc:
            self._sysline(f"MCP server failed to start: {type(exc).__name__}: {exc}")
            self._mcp_handle = None

    def _on_close(self):
        """Window close handler — graceful MCP shutdown then destroy."""
        if self._mcp_handle is not None:
            try:
                mcp_supervisor.shutdown(self._mcp_handle, verbose=True)
            except Exception as exc:
                print(f"[panel] MCP shutdown failed: {exc}")
            self._mcp_handle = None
        try:
            self.root.destroy()
        except Exception:
            pass

    def _build_ui(self):
        self.root = tk.Tk()
        self.root.title("LLSM")
        self.root.geometry(f"{PANEL_WIDTH}x800+0+0")
        self.root.minsize(360, 500)
        self.status_var = tk.StringVar(value="searching for SCIA...")
        ttk.Label(self.root, textvariable=self.status_var, anchor="w", padding=(8, 4)).pack(side=tk.TOP, fill=tk.X)
        ttk.Separator(self.root, orient="horizontal").pack(fill=tk.X)
        self.history = scrolledtext.ScrolledText(self.root, wrap="word", state="disabled", font=("Segoe UI", 10), padx=10, pady=10)
        self.history.tag_configure("user", foreground="#1e6091", font=("Segoe UI Semibold", 10))
        self.history.tag_configure("assistant", foreground="#1d3557", font=("Segoe UI", 10))
        self.history.tag_configure("error", foreground="#d62828", font=("Segoe UI", 10))
        self.history.tag_configure("system", foreground="#888", font=("Segoe UI", 9, "italic"))
        self.history.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=4)
        bottom = ttk.Frame(self.root, padding=(4, 4))
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        self.input_text = tk.Text(bottom, height=4, wrap="word", font=("Segoe UI", 10))
        self.input_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))
        self.input_text.bind("<Control-Return>", lambda e: (self._send(), "break"))
        self.send_btn = ttk.Button(bottom, text="Send (Ctrl+Enter)", command=self._send)
        self.send_btn.pack(side=tk.RIGHT, fill=tk.Y)
        self._sysline(f"Welcome to LLSM. Backend: {self._backend_kind}. Describe what you want to build in SCIA.")

    def _own_to_scia(self, scia_hwnd):
        try:
            our_hwnd = int(self.root.winfo_id())
            top = self.root.wm_frame()
            if top:
                try:
                    our_hwnd = int(top, 16)
                except ValueError:
                    pass
        except Exception:
            our_hwnd = int(self.root.winfo_id())
        try:
            win32api.SetWindowLong(our_hwnd, GWLP_HWNDPARENT, scia_hwnd)
        except Exception as exc:
            self._sysline(f"could not set owner window: {exc}")

    def _position_next_to_scia(self):
        if not self.scia_hwnd or not win32gui.IsWindow(self.scia_hwnd):
            return
        try:
            rect = win32gui.GetWindowRect(self.scia_hwnd)
        except Exception:
            return
        scia_left, scia_top, scia_right, scia_bottom = rect
        height = max(500, scia_bottom - scia_top)
        x = scia_right
        y = scia_top
        cur = f"{PANEL_WIDTH}x{height}+{x}+{y}"
        if self.root.geometry() != cur:
            self.root.geometry(cur)

    def _poll_scia(self):
        try:
            hwnd = _find_scia_main_hwnd()
            if hwnd != self.scia_hwnd:
                if hwnd:
                    self.scia_hwnd = hwnd
                    self.status_var.set(f"docked to SCIA  (hwnd 0x{hwnd:x})")
                    self._own_to_scia(hwnd)
                else:
                    self.scia_hwnd = None
                    self.status_var.set("waiting for SCIA Engineer to start...")
            self._position_next_to_scia()
        except Exception as exc:
            self.status_var.set(f"poll error: {exc}")
        self.root.after(POLL_INTERVAL_MS, self._poll_scia)

    def _ensure_backend(self):
        if self._backend is None:
            kwargs = {"verbose": False}
            if self._model:
                kwargs["model"] = self._model
            if self._backend_kind == "api":
                self._backend = AnthropicBackend(**kwargs)
            else:
                # default: claude code subprocess (uses Max subscription, no API key)
                # Hand it the persistent-MCP-server config so all turns hit the
                # SAME server (and thus the same SCIA + in-memory ADM model).
                if self._mcp_handle is not None:
                    kwargs["mcp_config_json"] = self._mcp_handle.mcp_config_json
                self._backend = ClaudeCodeBackend(**kwargs)
        return self._backend

    def _send(self):
        if self.busy:
            return
        text = self.input_text.get("1.0", "end").strip()
        if not text:
            return
        self.input_text.delete("1.0", "end")
        self._append("you", text, "user")
        self.busy = True
        self.send_btn.config(state="disabled", text="...")
        threading.Thread(target=self._chat_thread, args=(text,), daemon=True).start()

    def _chat_thread(self, prompt):
        try:
            backend = self._ensure_backend()
            reply = backend.chat(prompt)
            self.outbox.put(("assistant", reply))
        except Exception as exc:
            self.outbox.put(("error", f"{type(exc).__name__}: {exc}"))
        finally:
            self.outbox.put(("__done__", ""))

    def _drain_outbox(self):
        try:
            while True:
                kind, msg = self.outbox.get_nowait()
                if kind == "__done__":
                    self.busy = False
                    self.send_btn.config(state="normal", text="Send (Ctrl+Enter)")
                elif kind == "error":
                    self._append("error", msg, "error")
                else:
                    self._append("LLSM", msg, "assistant")
        except queue.Empty:
            pass
        self.root.after(80, self._drain_outbox)

    def _append(self, who, text, tag):
        self.history.config(state="normal")
        self.history.insert("end", f"{who}: ", tag)
        self.history.insert("end", text + "\n\n", tag)
        self.history.see("end")
        self.history.config(state="disabled")

    def _sysline(self, text):
        self.history.config(state="normal")
        self.history.insert("end", text + "\n\n", "system")
        self.history.see("end")
        self.history.config(state="disabled")

    def run(self):
        self.root.mainloop()


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="llsm-panel", description="LLSM chat panel docked to SCIA Engineer")
    ap.add_argument("--model", default=None, help="Override LLM model (e.g. opus, sonnet, claude-opus-4-7)")
    ap.add_argument("--backend", choices=["claude-code", "api"], default="claude-code",
                    help="LLM backend: claude-code (subprocess via your Max subscription, default) or api (direct Anthropic API, needs ANTHROPIC_API_KEY)")
    args = ap.parse_args()
    load_scia()
    LlsmPanel(model=args.model, backend_kind=args.backend).run()


if __name__ == "__main__":
    main()
