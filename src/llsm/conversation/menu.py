"""File menu cascade: New / Recent / Open History / Switch Model.

Talks only to the session, store, and model catalog/usage. Never touches
backends directly.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from typing import Callable

from ..history.models import Conversation, ModelRef


_RECENT_LIMIT = 10
_RECENT_MODELS_LIMIT = 5


def format_recent_label(c: Conversation, *, active: bool) -> str:
    """One row label for Recent Chats / History dialog.

    Module-level so HistoryDialog and FileMenuBuilder share one formatter
    without one importing the other's class.
    """
    title = c.title or "Untitled"
    if len(title) > 50:
        title = title[:49] + "…"
    bullet = "● " if active else "  "
    pin = "📌 " if c.pinned else ""
    ts = (c.last_used_at or "").split("T", 1)[0]
    return f"{bullet}{pin}{title}    [{c.model}]  {ts}"


class FileMenuBuilder:
    def __init__(
        self,
        *,
        root: tk.Tk,
        session,
        catalog,
        usage,
        backend_provider: Callable[[], object],
        on_new: Callable[[str | None], None],
        on_resume: Callable[[str], None],
        on_switch_model: Callable[[str, str], None],  # (backend_kind, model_id)
        on_open_history: Callable[[], None],
    ) -> None:
        self.root = root
        self.session = session
        self.catalog = catalog
        self.usage = usage
        self.backend_provider = backend_provider
        self.on_new = on_new
        self.on_resume = on_resume
        self.on_switch_model = on_switch_model
        self.on_open_history = on_open_history

    def build(self, menubar: tk.Menu) -> tk.Menu:
        m = tk.Menu(menubar, tearoff=False, postcommand=self._refresh_dynamic)
        m.add_command(label="New Chat  Ctrl+N", command=lambda: self.on_new(None))

        self._recent_menu = tk.Menu(m, tearoff=False)
        m.add_cascade(label="Recent Chats", menu=self._recent_menu)

        m.add_command(label="Open Chat History…  Ctrl+H", command=self.on_open_history)

        m.add_separator()

        self._models_menu = tk.Menu(m, tearoff=False)
        m.add_cascade(label="Switch Model", menu=self._models_menu)

        # Bind shortcuts (top-level so they fire regardless of focus widget).
        self.root.bind_all("<Control-n>", lambda e: (self.on_new(None), "break"))
        self.root.bind_all("<Control-N>", lambda e: (self.on_new(None), "break"))
        self.root.bind_all("<Control-h>", lambda e: (self.on_open_history(), "break"))
        self.root.bind_all("<Control-H>", lambda e: (self.on_open_history(), "break"))

        # Initial population so the cascades aren't empty before first hover.
        self._refresh_dynamic()
        return m

    def _refresh_dynamic(self) -> None:
        self._populate_recent()
        self._populate_models()

    def _populate_recent(self) -> None:
        m = self._recent_menu
        m.delete(0, "end")
        convs = self.session.store.list_conversations()[:_RECENT_LIMIT]
        if not convs:
            m.add_command(label="(no past chats yet)", state="disabled")
            return
        active_id = self.session.current_id()
        for c in convs:
            label = format_recent_label(c, active=(c.id == active_id))
            m.add_command(label=label, command=lambda cid=c.id: self.on_resume(cid))

    def _populate_models(self) -> None:
        m = self._models_menu
        m.delete(0, "end")
        cur = self.session.current()
        backend_kind = cur.backend_kind if cur else "claude-code"
        backend = None
        try:
            backend = self.backend_provider()
        except Exception:
            backend = None
        catalog_models = self.catalog.for_backend(backend_kind, backend)
        recent = self.usage.recent(backend_kind, limit=_RECENT_MODELS_LIMIT)
        # Show recent first (those that are still in the catalog), then the
        # rest of the catalog. Models in usage but no longer in the catalog
        # are dropped — `available_models()` is the source of truth for what
        # the active backend can serve.
        catalog_ids = [r.model_id for r in catalog_models]
        ordered: list[ModelRef] = []
        seen: set[str] = set()
        for r in recent:
            if r.model_id in catalog_ids and r.model_id not in seen:
                ordered.append(r)
                seen.add(r.model_id)
        for r in catalog_models:
            if r.model_id not in seen:
                ordered.append(r)
                seen.add(r.model_id)
        if not ordered:
            m.add_command(label="(no models available)", state="disabled")
            return
        active_model = cur.model if cur else None
        for r in ordered:
            check = "● " if r.model_id == active_model else "  "
            label = f"{check}{r.display_name or r.model_id}"
            m.add_command(
                label=label,
                command=lambda kind=r.backend_kind, mid=r.model_id: self.on_switch_model(kind, mid),
            )


class HistoryDialog(tk.Toplevel):
    """Searchable chat history list. Read/rename/delete/pin via right-click."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        session,
        on_resume: Callable[[str], None],
        on_state_changed: Callable[[], None] = lambda: None,
    ) -> None:
        super().__init__(master)
        self.title("Chat History")
        self.geometry("520x420")
        self.session = session
        self._on_resume = on_resume
        self._on_state_changed = on_state_changed

        topbar = ttk.Frame(self, padding=(8, 6))
        topbar.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(topbar, text="Search").pack(side=tk.LEFT, padx=(0, 6))
        self._query_var = tk.StringVar()
        self._query_var.trace_add("write", lambda *_: self._refresh())
        entry = ttk.Entry(topbar, textvariable=self._query_var)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        entry.focus_set()

        body = ttk.Frame(self, padding=(8, 0))
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self._listbox = tk.Listbox(body, activestyle="dotbox")
        self._listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(body, orient="vertical", command=self._listbox.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._listbox.configure(yscrollcommand=sb.set)
        self._listbox.bind("<Double-Button-1>", lambda e: self._resume_selected())
        self._listbox.bind("<Return>", lambda e: self._resume_selected())
        self._listbox.bind("<Delete>", lambda e: self._delete_selected())
        self._listbox.bind("<F2>", lambda e: self._rename_selected())
        self._listbox.bind("<Button-3>", self._show_ctx_menu)

        bottom = ttk.Frame(self, padding=(8, 6))
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(bottom, text="Resume", command=self._resume_selected).pack(side=tk.LEFT)
        ttk.Button(bottom, text="Rename", command=self._rename_selected).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(bottom, text="Pin / Unpin", command=self._toggle_pin_selected).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(bottom, text="Delete", command=self._delete_selected).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(bottom, text="Close", command=self.destroy).pack(side=tk.RIGHT)

        self._all: list[Conversation] = []
        self._visible: list[Conversation] = []
        self._refresh()

    def _refresh(self) -> None:
        self._all = self.session.store.list_conversations()
        q = (self._query_var.get() or "").strip().lower()
        self._visible = [
            c for c in self._all
            if not q or q in c.title.lower() or q in (c.preview or "").lower() or q in c.model.lower()
        ]
        self._listbox.delete(0, "end")
        active_id = self.session.current_id()
        for c in self._visible:
            self._listbox.insert("end", format_recent_label(c, active=(c.id == active_id)))

    def _selected_conv(self) -> Conversation | None:
        sel = self._listbox.curselection()
        if not sel:
            return None
        idx = sel[0]
        if idx < 0 or idx >= len(self._visible):
            return None
        return self._visible[idx]

    def _resume_selected(self) -> None:
        c = self._selected_conv()
        if c is None:
            return
        self._on_resume(c.id)
        self.destroy()

    def _rename_selected(self) -> None:
        c = self._selected_conv()
        if c is None:
            return
        new_title = simpledialog.askstring("Rename chat", "New title:", initialvalue=c.title, parent=self)
        if new_title is None:
            return
        self.session.store.rename(c.id, new_title)
        self._refresh()
        self._notify_changed()

    def _toggle_pin_selected(self) -> None:
        c = self._selected_conv()
        if c is None:
            return
        self.session.store.set_pinned(c.id, not c.pinned)
        self._refresh()
        self._notify_changed()

    def _delete_selected(self) -> None:
        c = self._selected_conv()
        if c is None:
            return
        if not messagebox.askyesno("Delete chat",
                                   f"Delete '{c.title}'? This cannot be undone.",
                                   parent=self):
            return
        self.session.store.delete(c.id)
        self._refresh()
        self._notify_changed()

    def _notify_changed(self) -> None:
        try:
            self._on_state_changed()
        except Exception:
            pass

    def _show_ctx_menu(self, event) -> None:
        idx = self._listbox.nearest(event.y)
        if idx >= 0:
            self._listbox.selection_clear(0, "end")
            self._listbox.selection_set(idx)
            self._listbox.activate(idx)
        m = tk.Menu(self, tearoff=False)
        m.add_command(label="Resume", command=self._resume_selected)
        m.add_command(label="Rename…", command=self._rename_selected)
        m.add_command(label="Pin / Unpin", command=self._toggle_pin_selected)
        m.add_separator()
        m.add_command(label="Delete", command=self._delete_selected)
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()
