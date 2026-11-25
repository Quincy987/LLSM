"""Background model lists for providers that have no fixed list.

An OpenAI-compatible server (Ollama, LM Studio, ...) only knows its models
by being asked. The File menu used to ask only the live backend of the
active chat, which does not exist until a first message is sent, so a
freshly configured local server showed "(no models available)" and could
never be picked. This fetcher asks the server itself, on a worker thread,
and hands the result to the catalog on the Tk thread.

Stale-while-revalidate: callers show what is known and call `refresh()`;
a newly pulled model shows up the next time the menu opens.

Layering: no Tk and no `llm/` import here. The panel injects the probe
(`llsm.llm.providers.probe_models` bound to a spec) and the marshaller
(`root.after(0, fn)`).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class LiveStatus:
    """Last known state of one provider's model list.

    state: "idle" (never asked), "loading" (first answer pending), "ok",
    "empty", "unreachable", "error". `loading` is True whenever a request
    is in flight, also while an older list is still being shown.
    """
    state: str = "idle"
    error: str = ""
    base_url: str = ""
    loading: bool = False


class LiveModelFetcher:
    def __init__(
        self,
        *,
        catalog,
        probe: Callable[[str], object],
        tk_call: Callable[[Callable[[], None]], None],
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
    ) -> None:
        self._catalog = catalog
        self._probe = probe
        self._tk_call = tk_call
        self._thread_factory = thread_factory
        self._status: dict[str, LiveStatus] = {}
        self._inflight: set[str] = set()
        self._generation: dict[str, int] = {}
        self._listeners: list[Callable[[str], None]] = []
        self._lock = threading.Lock()

    def add_listener(self, fn: Callable[[str], None]) -> None:
        """`fn(kind)` runs on the Tk thread after each finished fetch."""
        self._listeners.append(fn)

    def status(self, kind: str) -> LiveStatus:
        st = self._status.get(kind, LiveStatus())
        with self._lock:
            busy = kind in self._inflight
        if busy and not st.loading:
            st = LiveStatus(st.state, st.error, st.base_url, loading=True)
        return st

    def forget(self, kind: str) -> None:
        """Drop what is known (credentials changed). A fetch already in
        flight is ignored when it lands."""
        with self._lock:
            self._generation[kind] = self._generation.get(kind, 0) + 1
            self._inflight.discard(kind)
        self._status.pop(kind, None)
        self._catalog.invalidate(kind)

    def refresh(self, kind: str) -> bool:
        """Start a background fetch unless one is already running."""
        with self._lock:
            if kind in self._inflight:
                return False
            self._inflight.add(kind)
            gen = self._generation.get(kind, 0)
        t = self._thread_factory(target=self._worker, args=(kind, gen),
                                 daemon=True, name=f"llsm-models-{kind}")
        t.start()
        return True

    def _worker(self, kind: str, gen: int) -> None:
        try:
            result = self._probe(kind)
        except Exception as exc:  # the probe should not raise; be safe
            result = _Failed(f"{type(exc).__name__}: {exc}")
        try:
            self._tk_call(lambda: self._apply(kind, gen, result))
        except Exception:
            # Tk is gone (panel closing): nothing to update.
            with self._lock:
                self._inflight.discard(kind)

    def _apply(self, kind: str, gen: int, result) -> None:
        with self._lock:
            if self._generation.get(kind, 0) != gen:
                return  # credentials changed meanwhile; a newer fetch decides
            self._inflight.discard(kind)
        state = getattr(result, "status", "error")
        models = list(getattr(result, "models", ()) or ())
        if state == "ok" and models:
            self._catalog.put(kind, models)
        else:
            # A server that is down or empty must not keep showing an old
            # list as if it could be picked.
            self._catalog.put(kind, [])
        self._status[kind] = LiveStatus(
            state=state,
            error=str(getattr(result, "error", "") or ""),
            base_url=str(getattr(result, "base_url", "") or ""),
        )
        for fn in list(self._listeners):
            try:
                fn(kind)
            except Exception:
                pass


@dataclass(frozen=True)
class _Failed:
    error: str
    status: str = "error"
    models: tuple = ()
    base_url: str = ""
