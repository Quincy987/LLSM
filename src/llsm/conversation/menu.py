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
        open_wizard_cb: Callable[[], None] | None = None,
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
        self.open_wizard_cb = open_wizard_cb

    def build(self, menubar: tk.Misc) -> tk.Menu:
        """The File menu, parented to `menubar` (a tk.Menu or a Menubutton)."""
        m = tk.Menu(menubar, tearoff=False, postcommand=self._refresh_dynamic)
        self._file_menu = m
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

    def menus(self) -> list[tk.Menu]:
        """The menus this builder owns, for recolouring on a theme switch.

        Cascades rebuilt on every open (per-provider model lists) are created
        fresh with the current option defaults, so they aren't listed here.
        """
        return [m for m in (getattr(self, "_file_menu", None),
                            getattr(self, "_recent_menu", None),
                            getattr(self, "_models_menu", None)) if m is not None]

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
        """Cascade by provider; only show providers whose SDK + creds are present.

        Top-level layout:
            Recent ▸  (across all providers; empty when no usage yet)
            ─────
            <Provider 1> ▸ models
            <Provider 2> ▸ models
            ...
            ─────
            Set up new provider...    <- shortcut to wizard
        """
        m = self._models_menu
        m.delete(0, "end")
        cur = self.session.current()
        active_kind = cur.backend_kind if cur else None
        active_model = cur.model if cur else None

        # --- Recent (across all providers) -------------------------------
        recent_all = self.usage.recent(limit=_RECENT_MODELS_LIMIT)
        if recent_all:
            recent_menu = tk.Menu(m, tearoff=False)
            for r in recent_all:
                check = "● " if (r.backend_kind == active_kind and r.model_id == active_model) else "  "
                label = f"{check}{r.display_name or r.model_id}"
                recent_menu.add_command(
                    label=label,
                    command=lambda kind=r.backend_kind, mid=r.model_id: self.on_switch_model(kind, mid),
                )
            m.add_cascade(label="Recent", menu=recent_menu)
            m.add_separator()

        # --- Per-provider cascades ---------------------------------------
        # Lazy import to avoid pulling providers into history dialogs etc.
        try:
            from ..llm.providers import PROVIDERS, _resolve
            from ..llm.credentials import has_credential
        except Exception:
            PROVIDERS = {}  # type: ignore[assignment]
            _resolve = None  # type: ignore[assignment]
            has_credential = lambda *a, **k: False  # type: ignore[assignment]

        # When a provider has missing SDK we still show its row but with the
        # install hint; the user can click "Set up new provider..." to get
        # a clearer experience.
        any_shown = False
        for kind, spec in (PROVIDERS or {}).items():
            sub = tk.Menu(m, tearoff=False)
            # Probe SDK availability lazily.
            sdk_missing_hint = ""
            if spec.sdk_install_hint:
                try:
                    _resolve(spec.build_backend_path)  # type: ignore[misc]
                except ImportError:
                    sdk_missing_hint = spec.sdk_install_hint
                except Exception:
                    pass

            if sdk_missing_hint:
                sub.add_command(label=f"({sdk_missing_hint} to enable)", state="disabled")
                m.add_cascade(label=spec.display_name, menu=sub)
                any_shown = True
                continue

            # If credentials aren't configured for a provider that needs them,
            # show a hint row and skip the live fetch (CR-2 lazy + cred-gated).
            needs_creds = any(am.value == "api_key" for am in spec.auth_methods) or spec.requires_base_url
            has_any = (
                has_credential(kind, "api_key") or
                (spec.requires_base_url and has_credential(kind, "base_url")) or
                has_credential(kind, "subscription")
            )
            if needs_creds and not has_any:
                # Use static_models() if non-empty, else show empty-state.
                static_list: list[ModelRef] = []
                try:
                    if _resolve is not None:
                        static_list = list(_resolve(spec.static_models_path)())
                except Exception:
                    static_list = []
                if static_list:
                    for r in static_list:
                        sub.add_command(
                            label=r.display_name or r.model_id,
                            command=lambda kk=kind, mid=r.model_id:
                                self.on_switch_model(kk, mid),
                        )
                    sub.add_separator()
                    sub.add_command(label="(set up credentials to use)", state="disabled")
                else:
                    sub.add_command(label="(set up credentials first)", state="disabled")
                m.add_cascade(label=spec.display_name, menu=sub)
                any_shown = True
                continue

            # Otherwise: ask the catalog (which handles live + static fallback).
            backend = None
            if active_kind == kind:
                # Pass the live backend so we get the live model list.
                try:
                    backend = self.backend_provider()
                except Exception:
                    backend = None
            try:
                models = self.catalog.for_backend(kind, backend)
            except Exception:
                models = []
            if not models:
                sub.add_command(label="(no models available)", state="disabled")
            else:
                for r in models:
                    check = "● " if (kind == active_kind and r.model_id == active_model) else "  "
                    label = f"{check}{r.display_name or r.model_id}"
                    sub.add_command(
                        label=label,
                        command=lambda kk=kind, mid=r.model_id:
                            self.on_switch_model(kk, mid),
                    )
            m.add_cascade(label=spec.display_name, menu=sub)
            any_shown = True

        if not any_shown:
            m.add_command(label="(no providers registered)", state="disabled")

        # --- Setup shortcut ----------------------------------------------
        m.add_separator()
        m.add_command(label="Set up new provider…",
                      command=self._on_open_wizard_safe)

    def _on_open_wizard_safe(self):
        """Open the credential wizard via the panel callback if it's wired up."""
        cb = getattr(self, "open_wizard_cb", None)
        if cb is None:
            try:
                from tkinter import messagebox
                messagebox.showinfo(
                    "Set up provider",
                    "Use Tools → Set up credentials… to add or change a provider.",
                    parent=self.root,
                )
            except Exception:
                pass
            return
        try:
            cb()
        except Exception:
            pass


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
