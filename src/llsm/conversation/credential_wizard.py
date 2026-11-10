"""Three-step Tk wizard for configuring provider credentials.

Step 1: pick provider (radio list, with a green check next to providers
        that already have credentials configured).
Step 2: pick auth method (radio list filtered to spec.auth_methods —
        skipped automatically when only one method applies).
Step 3: enter credentials (API key / subscription login / base_url).

UX threading discipline: anything that hits the network (the "Test" button
calling /v1/models, or the SUBSCRIPTION_LOGIN subprocess) MUST run on a
worker thread; results are marshaled back via `root.after(0, lambda: ...)`.
The wizard window stays responsive throughout.

This module is imported lazily from panel.py so a default-install user with
no `keyring` package can still launch the panel and get a clean error
message when they open the wizard, instead of an ImportError at startup.
"""
from __future__ import annotations

import subprocess
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk
from typing import Callable, Optional

from ..llm.providers import (
    AuthMethod,
    PROVIDERS,
    ProviderSpec,
    _resolve,
)
from ..llm import credentials


_DEFAULT_PROFILE = "default"


# Convenience: get_credential / has_credential / set_credential / delete_credential
get_credential = credentials.get_credential
has_credential = credentials.has_credential
set_credential = credentials.set_credential
delete_credential = credentials.delete_credential


def _spec_already_configured(spec: ProviderSpec) -> bool:
    """True if the spec has at least one stored credential we recognise."""
    if AuthMethod.API_KEY in spec.auth_methods and has_credential(spec.kind, "api_key"):
        return True
    if spec.requires_base_url and has_credential(spec.kind, "base_url"):
        return True
    if AuthMethod.SUBSCRIPTION_LOGIN in spec.auth_methods and has_credential(spec.kind, "subscription"):
        return True
    return False


class CredentialWizard(tk.Toplevel):
    """3-step modal wizard. Caller passes a callback fired on Save."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        on_saved: Callable[[str], None] = lambda kind: None,
    ) -> None:
        super().__init__(master)
        self.title("Set up credentials")
        self.geometry("520x380")
        self.transient(master)
        self.grab_set()
        self._on_saved = on_saved
        self._spec: Optional[ProviderSpec] = None
        self._auth: Optional[AuthMethod] = None

        # Step 1 state.
        self._kind_var = tk.StringVar()

        # Step 3 state (API_KEY path).
        self._api_key_var = tk.StringVar()
        self._show_var = tk.BooleanVar(value=False)
        self._base_url_var = tk.StringVar()
        # Step 3 state (test status + last error).
        self._test_status_var = tk.StringVar(value="")
        self._busy = False  # True while a worker thread is running

        # Build frames stacked; show_step toggles which is packed.
        self._body = ttk.Frame(self, padding=(12, 12))
        self._body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self._buttons = ttk.Frame(self, padding=(12, 8))
        self._buttons.pack(side=tk.BOTTOM, fill=tk.X)

        self._back_btn = ttk.Button(self._buttons, text="Back", command=self._go_back)
        self._back_btn.pack(side=tk.LEFT)
        self._cancel_btn = ttk.Button(self._buttons, text="Cancel", command=self.destroy)
        self._cancel_btn.pack(side=tk.RIGHT)
        self._next_btn = ttk.Button(self._buttons, text="Next", command=self._go_next)
        self._next_btn.pack(side=tk.RIGHT, padx=(0, 6))

        self._step = 0
        self._show_step1()

    # --- Generic frame helpers ---

    def _clear_body(self):
        for w in self._body.winfo_children():
            w.destroy()

    def _set_buttons(self, *, back: bool, next_label: str | None, next_enabled: bool) -> None:
        self._back_btn.configure(state="normal" if back else "disabled")
        if next_label is None:
            self._next_btn.pack_forget()
        else:
            self._next_btn.pack(side=tk.RIGHT, padx=(0, 6))
            self._next_btn.configure(text=next_label, state="normal" if next_enabled else "disabled")

    # --- Step 1: provider picker ---

    def _show_step1(self):
        self._step = 1
        self._clear_body()
        ttk.Label(self._body, text="Which provider would you like to set up?",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 8))

        for kind, spec in PROVIDERS.items():
            row = ttk.Frame(self._body)
            row.pack(side=tk.TOP, fill=tk.X, pady=2)

            check = "✓ " if _spec_already_configured(spec) else "  "
            label_text = f"{check}{spec.display_name}"

            rb = ttk.Radiobutton(
                row,
                text=label_text,
                value=kind,
                variable=self._kind_var,
                command=self._on_kind_changed,
            )
            rb.pack(side=tk.LEFT)

            # If the SDK is missing, surface the install hint inline so the
            # user gets a clear remedy. We probe lazily via _resolve() which
            # may raise ImportError.
            hint = self._sdk_install_hint_if_missing(spec)
            if hint:
                ttk.Label(row, text=f"  ({hint} to enable)", style="Muted.TLabel").pack(side=tk.LEFT)

        # Description label (refreshed when selection changes).
        self._desc_var = tk.StringVar(value="Pick a provider to see its description.")
        ttk.Separator(self._body, orient="horizontal").pack(fill=tk.X, pady=(10, 6))
        ttk.Label(self._body, textvariable=self._desc_var, wraplength=480,
                  style="Muted.TLabel").pack(anchor="w")

        self._set_buttons(back=False, next_label="Next", next_enabled=False)

    def _sdk_install_hint_if_missing(self, spec: ProviderSpec) -> str:
        """Return the install hint string when the SDK can't be imported."""
        if not spec.sdk_install_hint:
            return ""
        try:
            _resolve(spec.build_backend_path)
        except ImportError:
            return spec.sdk_install_hint
        except Exception:
            return ""
        return ""

    def _on_kind_changed(self):
        kind = self._kind_var.get()
        spec = PROVIDERS.get(kind)
        if spec is None:
            return
        self._desc_var.set(spec.description)
        self._set_buttons(back=False, next_label="Next", next_enabled=True)

    def _go_back(self):
        if self._step == 3:
            # If there's only one auth method, Back from step 3 jumps to step 1.
            if self._spec is not None and len(self._spec.auth_methods) == 1:
                self._show_step1()
            else:
                self._show_step2()
        elif self._step == 2:
            self._show_step1()

    def _go_next(self):
        if self._step == 1:
            spec = PROVIDERS.get(self._kind_var.get())
            if spec is None:
                return
            self._spec = spec
            # Auto-skip step 2 when there's only one auth method.
            if len(spec.auth_methods) == 1:
                self._auth = spec.auth_methods[0]
                self._show_step3()
            else:
                self._show_step2()
        elif self._step == 2:
            if self._auth is None:
                return
            self._show_step3()

    # --- Step 2: auth method ---

    def _show_step2(self):
        self._step = 2
        self._clear_body()
        spec = self._spec
        assert spec is not None
        ttk.Label(self._body, text=f"How would you like to authenticate with {spec.display_name}?",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 8))

        method_var = tk.StringVar(value=self._auth.value if self._auth else "")
        for m in spec.auth_methods:
            label = {
                AuthMethod.API_KEY: "API key",
                AuthMethod.SUBSCRIPTION_LOGIN: "Subscription login (browser)",
                AuthMethod.NONE: "Local server, no key needed",
            }[m]
            ttk.Radiobutton(
                self._body,
                text=label,
                value=m.value,
                variable=method_var,
                command=lambda v=m: self._on_auth_method_picked(v),
            ).pack(anchor="w", pady=2)

        self._set_buttons(back=True, next_label="Next", next_enabled=self._auth is not None)

    def _on_auth_method_picked(self, m: AuthMethod):
        self._auth = m
        self._set_buttons(back=True, next_label="Next", next_enabled=True)

    # --- Step 3: enter credentials ---

    def _show_step3(self):
        self._step = 3
        self._clear_body()
        spec = self._spec
        auth = self._auth
        assert spec is not None and auth is not None

        ttk.Label(self._body, text=f"{spec.display_name} — credentials",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 8))

        # Help link if we have one.
        if spec.help_url:
            link = ttk.Label(self._body, text="Where to get this →", style="Link.TLabel", cursor="hand2")
            link.pack(anchor="w", pady=(0, 6))
            link.bind("<Button-1>", lambda e: webbrowser.open(spec.help_url))

        if auth == AuthMethod.API_KEY:
            self._build_api_key_frame(spec)
        elif auth == AuthMethod.SUBSCRIPTION_LOGIN:
            self._build_subscription_frame(spec)
        else:
            self._build_none_frame(spec)

    def _build_api_key_frame(self, spec: ProviderSpec):
        # Pre-fill from existing stored key (masked).
        existing = get_credential(spec.kind, "api_key") or ""
        self._api_key_var.set(existing)

        # base_url (only shown for openai-compatible)
        if spec.requires_base_url:
            ttk.Label(self._body, text="Base URL (e.g. http://localhost:11434/v1):").pack(anchor="w")
            ttk.Entry(self._body, textvariable=self._base_url_var, width=60).pack(anchor="w", pady=(0, 8), fill=tk.X)
            existing_url = get_credential(spec.kind, "base_url") or ""
            self._base_url_var.set(existing_url)

        ttk.Label(self._body, text="API key:").pack(anchor="w")
        row = ttk.Frame(self._body)
        row.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
        self._key_entry = ttk.Entry(row, textvariable=self._api_key_var, show="•")
        self._key_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Checkbutton(row, text="Show", variable=self._show_var,
                        command=self._toggle_show_key).pack(side=tk.LEFT, padx=(6, 0))

        # Test row.
        test_row = ttk.Frame(self._body)
        test_row.pack(side=tk.TOP, fill=tk.X, pady=(4, 0))
        self._test_btn = ttk.Button(test_row, text="Test", command=self._on_test)
        self._test_btn.pack(side=tk.LEFT)
        self._test_status_var.set("")
        ttk.Label(test_row, textvariable=self._test_status_var,
                  wraplength=380).pack(side=tk.LEFT, padx=(8, 0))

        self._set_buttons(back=True, next_label="Save", next_enabled=True)
        self._next_btn.configure(command=self._on_save_api_key)

    def _toggle_show_key(self):
        self._key_entry.configure(show="" if self._show_var.get() else "•")

    def _build_subscription_frame(self, spec: ProviderSpec):
        ttk.Label(self._body, text="A new browser window will open for you to log in.",
                  wraplength=480).pack(anchor="w")

        row = ttk.Frame(self._body)
        row.pack(side=tk.TOP, fill=tk.X, pady=(8, 4))
        self._login_btn = ttk.Button(row, text="Start login", command=self._on_subscription_login)
        self._login_btn.pack(side=tk.LEFT)
        ttk.Label(row, textvariable=self._test_status_var, wraplength=380).pack(side=tk.LEFT, padx=(8, 0))

        # Output box for subprocess stream.
        self._login_output = tk.Text(self._body, height=8, wrap="word", font=("Consolas", 9))
        self._login_output.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(8, 0))
        self._login_output.configure(state="disabled")

        # Save is enabled once login signals success.
        self._set_buttons(back=True, next_label="Save", next_enabled=False)
        self._next_btn.configure(command=self._on_save_subscription)

    def _build_none_frame(self, spec: ProviderSpec):
        ttk.Label(self._body, text="Base URL (e.g. http://localhost:11434/v1):").pack(anchor="w")
        ttk.Entry(self._body, textvariable=self._base_url_var, width=60).pack(anchor="w", pady=(0, 8), fill=tk.X)
        existing_url = get_credential(spec.kind, "base_url") or ""
        self._base_url_var.set(existing_url)

        # Test connection button (pings /v1/models).
        row = ttk.Frame(self._body)
        row.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
        self._test_btn = ttk.Button(row, text="Test connection", command=self._on_test)
        self._test_btn.pack(side=tk.LEFT)
        ttk.Label(row, textvariable=self._test_status_var, wraplength=380).pack(side=tk.LEFT, padx=(8, 0))

        self._set_buttons(back=True, next_label="Save", next_enabled=True)
        self._next_btn.configure(command=self._on_save_none)

    # --- Worker-thread helpers ---

    def _set_busy(self, busy: bool):
        self._busy = busy
        try:
            state = "disabled" if busy else "normal"
            if hasattr(self, "_test_btn"):
                self._test_btn.configure(state=state)
            self._next_btn.configure(state=state)
            self._back_btn.configure(state=state)
        except Exception:
            pass

    def _post_status(self, text: str, ok: bool | None = None):
        """Marshaled back to the Tk thread via after()."""
        prefix = "✓ " if ok else ("✗ " if ok is False else "")
        self._test_status_var.set(prefix + text)

    def _on_test(self):
        spec = self._spec
        if spec is None or self._busy:
            return
        api_key = self._api_key_var.get().strip()
        base_url = self._base_url_var.get().strip() if spec.requires_base_url else ""
        if spec.requires_base_url and not base_url:
            self._post_status("Base URL is required.", ok=False)
            return
        if AuthMethod.API_KEY in spec.auth_methods and not api_key:
            if self._auth == AuthMethod.API_KEY:
                self._post_status("Enter an API key first.", ok=False)
                return

        self._set_busy(True)
        self._post_status("Testing…")
        threading.Thread(
            target=self._test_worker,
            args=(spec, api_key, base_url),
            daemon=True,
        ).start()

    def _test_worker(self, spec: ProviderSpec, api_key: str, base_url: str):
        """Construct the backend with the entered creds, call available_models()."""
        ok = False
        msg = ""
        try:
            build_fn = _resolve(spec.build_backend_path)
            # The factory is panel-coupled; we can't reuse it. Construct the
            # backend class directly with kwargs. For the purpose of testing
            # creds we just need a backend that can call available_models().
            # Each backend module exports a class named like its kind; rather
            # than maintaining a reverse map, use the build_backend module's
            # __module__ to import the class.
            mod_name = build_fn.__module__
            backend_class = self._backend_class_from_module(mod_name)
            kwargs = {"verbose": False}
            if AuthMethod.API_KEY in spec.auth_methods and api_key:
                kwargs["api_key"] = api_key
            if spec.requires_base_url and base_url:
                kwargs["base_url"] = base_url
            backend = backend_class(**kwargs)
            try:
                models = backend.available_models()
            finally:
                try:
                    backend.close()
                except Exception:
                    pass
            n = len(list(models))
            ok = n > 0
            msg = f"Connected ({n} model{'s' if n != 1 else ''} available)" if ok else "Connected, but no models returned."
        except ImportError as exc:
            msg = f"SDK missing: {spec.sdk_install_hint or exc}"
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"

        # Marshal back to Tk thread.
        try:
            self.after(0, lambda: self._post_status(msg, ok=ok))
            self.after(0, lambda: self._set_busy(False))
        except Exception:
            pass

    @staticmethod
    def _backend_class_from_module(mod_name: str):
        """Locate the Backend class by module — convention: each backend
        module exports a single concrete `*Backend` class."""
        import importlib
        m = importlib.import_module(mod_name)
        for attr in dir(m):
            if attr.endswith("Backend") and not attr.startswith("_"):
                obj = getattr(m, attr)
                if isinstance(obj, type):
                    return obj
        raise RuntimeError(f"no Backend class found in {mod_name}")

    # --- Subscription login (claude setup-token) ---

    def _on_subscription_login(self):
        spec = self._spec
        if spec is None or self._busy:
            return
        if spec.kind != "claude-code":
            self._post_status("Subscription login is only wired for claude-code in v0.3.0.", ok=False)
            return
        self._set_busy(True)
        self._append_login_output("[wizard] Spawning `claude setup-token`...\n")
        threading.Thread(target=self._login_worker, daemon=True).start()

    def _append_login_output(self, text: str):
        try:
            self._login_output.configure(state="normal")
            self._login_output.insert("end", text)
            self._login_output.see("end")
            self._login_output.configure(state="disabled")
        except Exception:
            pass

    def _login_worker(self):
        rc: int = -1
        try:
            proc = subprocess.Popen(
                ["claude", "setup-token"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for line in proc.stdout:  # type: ignore[union-attr]
                self.after(0, lambda l=line: self._append_login_output(l))
            proc.wait()
            rc = proc.returncode
        except FileNotFoundError:
            self.after(0, lambda: self._append_login_output(
                "[wizard] `claude` CLI not on PATH. Install Claude Code first.\n"
            ))
            self.after(0, lambda: self._set_busy(False))
            return
        except Exception as exc:
            # Bind now: Python clears `exc` when the except block ends, before Tk runs this.
            self.after(0, lambda msg=f"[wizard] error: {exc}\n": self._append_login_output(msg))
            self.after(0, lambda: self._set_busy(False))
            return

        if rc == 0:
            # Mark "configured" so a re-open shows the green check. The
            # actual token lives in claude's own config.
            try:
                set_credential("claude-code", "subscription", "ok")
            except Exception:
                pass
            self.after(0, lambda: self._post_status("Login successful.", ok=True))
            self.after(0, lambda: self._next_btn.configure(state="normal"))
        else:
            self.after(0, lambda: self._post_status(f"Login failed (exit {rc}).", ok=False))
        self.after(0, lambda: self._set_busy(False))

    # --- Save handlers ---

    def _on_save_api_key(self):
        spec = self._spec
        assert spec is not None
        api_key = self._api_key_var.get().strip()
        if not api_key:
            self._post_status("Enter an API key first.", ok=False)
            return
        try:
            set_credential(spec.kind, "api_key", api_key)
            if spec.requires_base_url:
                base_url = self._base_url_var.get().strip()
                if base_url:
                    set_credential(spec.kind, "base_url", base_url)
        except Exception as exc:
            self._post_status(f"Save failed: {exc}", ok=False)
            return
        self._on_saved(spec.kind)
        self.destroy()

    def _on_save_subscription(self):
        spec = self._spec
        assert spec is not None
        # Token already stored in claude's own config; we just notify caller.
        self._on_saved(spec.kind)
        self.destroy()

    def _on_save_none(self):
        spec = self._spec
        assert spec is not None
        base_url = self._base_url_var.get().strip()
        if not base_url:
            self._post_status("Base URL is required.", ok=False)
            return
        try:
            set_credential(spec.kind, "base_url", base_url)
            # api_key may also have been entered — store it if so.
            api_key = self._api_key_var.get().strip()
            if api_key:
                set_credential(spec.kind, "api_key", api_key)
        except Exception as exc:
            self._post_status(f"Save failed: {exc}", ok=False)
            return
        self._on_saved(spec.kind)
        self.destroy()


# ---------- Manage providers dialog ----------

class ManageProvidersDialog(tk.Toplevel):
    """Lists configured providers; user can Edit (re-run wizard) or Remove."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        on_changed: Callable[[str | None], None] = lambda kind: None,
    ) -> None:
        super().__init__(master)
        self.title("Manage providers")
        self.geometry("440x320")
        self.transient(master)
        self.grab_set()
        self._on_changed = on_changed

        ttk.Label(self, text="Configured providers", font=("Segoe UI", 10, "bold"),
                  padding=(12, 8)).pack(anchor="w")

        self._body = ttk.Frame(self, padding=(12, 0))
        self._body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        bottom = ttk.Frame(self, padding=(12, 8))
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(bottom, text="Close", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(bottom, text="Add new provider…",
                   command=self._add_new).pack(side=tk.LEFT)

        self._refresh()

    def _refresh(self):
        for w in self._body.winfo_children():
            w.destroy()
        any_configured = False
        for kind, spec in PROVIDERS.items():
            if not _spec_already_configured(spec):
                continue
            any_configured = True
            row = ttk.Frame(self._body)
            row.pack(side=tk.TOP, fill=tk.X, pady=4)
            ttk.Label(row, text=f"✓ {spec.display_name}").pack(side=tk.LEFT)
            ttk.Button(row, text="Remove",
                       command=lambda s=spec: self._remove(s)).pack(side=tk.RIGHT)
            ttk.Button(row, text="Edit…",
                       command=lambda s=spec: self._edit(s)).pack(side=tk.RIGHT, padx=(0, 6))
        if not any_configured:
            ttk.Label(self._body, text="(none configured yet — click 'Add new provider…')",
                      style="Muted.TLabel").pack(anchor="w")

    def _add_new(self):
        wiz = CredentialWizard(self, on_saved=self._after_save)
        self.wait_window(wiz)

    def _edit(self, spec: ProviderSpec):
        wiz = CredentialWizard(self, on_saved=self._after_save)
        # Pre-select that provider so the user doesn't have to reclick.
        wiz._kind_var.set(spec.kind)
        wiz._on_kind_changed()
        self.wait_window(wiz)

    def _remove(self, spec: ProviderSpec):
        for field in ("api_key", "base_url", "subscription"):
            try:
                delete_credential(spec.kind, field)
            except Exception:
                pass
        self._on_changed(spec.kind)
        self._refresh()

    def _after_save(self, kind: str):
        self._on_changed(kind)
        self._refresh()
