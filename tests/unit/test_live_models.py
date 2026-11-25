"""The File menu lists the models of a server-only provider (Ollama, ...).

Before: the menu asked only the live backend of the active chat (none until
a first message), cached the empty answer for the whole session, and the
provider's fixed list is empty, so "(no models available)" never went away.
"""
from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from llsm.conversation.live_models import LiveModelFetcher, LiveStatus
from llsm.history.model_catalog import ModelCatalog
from llsm.history.models import ModelRef

pytest.importorskip("openai")

from llsm.llm.providers import PROVIDERS, has_static_models, probe_models  # noqa: E402

SPEC = PROVIDERS["openai-compatible"]


# ---------- a fake /v1/models server ----------

class _Server:
    def __init__(self, ids):
        self.ids = ids
        self.hits = 0
        outer = self

        class _H(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits += 1
                if self.path.rstrip("/").endswith("/models"):
                    body = json.dumps({"object": "list", "data": [
                        {"id": i, "object": "model", "created": 0, "owned_by": "x"}
                        for i in outer.ids]}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    s = _Server(["qwen3:latest", "qwen3-coder:30b"])
    yield s
    s.close()


def _closed_port_url() -> str:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}/v1"


# ---------- probe ----------

def test_probe_lists_the_servers_models(server):
    r = probe_models(SPEC, base_url=server.url)
    assert r.status == "ok"
    assert [m.model_id for m in r.models] == ["qwen3:latest", "qwen3-coder:30b"]
    assert r.base_url == server.url


def test_probe_tells_an_empty_server_apart(server):
    server.ids = []
    assert probe_models(SPEC, base_url=server.url).status == "empty"


def test_probe_tells_a_closed_port_apart_quickly():
    import time
    url = _closed_port_url()
    t0 = time.monotonic()
    r = probe_models(SPEC, base_url=url, timeout_s=2.0)
    assert r.status == "unreachable"
    assert r.base_url == url and r.error
    assert time.monotonic() - t0 < 5.0  # no retries, no minutes-long default


def test_only_providers_without_a_fixed_list_are_live_only():
    live = {k for k, s in PROVIDERS.items() if not has_static_models(s)}
    assert live == {"openai-compatible"}


def test_wizard_status_line_is_unchanged_for_a_working_server(server):
    from llsm.conversation.credential_wizard import describe_probe
    assert describe_probe(probe_models(SPEC, base_url=server.url)) == \
        (True, "Connected (2 models available)")
    server.ids = []
    assert describe_probe(probe_models(SPEC, base_url=server.url)) == \
        (False, "Connected, but no models returned.")
    ok, msg = describe_probe(probe_models(SPEC, base_url=_closed_port_url()))
    assert not ok and msg.startswith("Server not reachable at http://127.0.0.1:")


# ---------- catalog ----------

def test_empty_result_is_not_cached():
    calls = {"n": 0}
    answers = [[], [ModelRef("x", "m1")]]

    def provider():
        calls["n"] += 1
        return answers[calls["n"] - 1]

    cat = ModelCatalog(providers={"x": provider})
    assert cat.for_backend("x") == []
    assert [m.model_id for m in cat.for_backend("x")] == ["m1"]
    assert calls["n"] == 2


def test_live_only_kind_never_asks_the_backend_and_reads_what_was_put():
    class _Backend:
        def available_models(self):
            raise AssertionError("must not hit the network on the Tk thread")

    cat = ModelCatalog(providers={"oc": lambda: []})
    cat.mark_live_only("oc")
    assert cat.for_backend("oc", _Backend()) == []
    cat.put("oc", [ModelRef("oc", "qwen3")])
    assert [m.model_id for m in cat.for_backend("oc", _Backend())] == ["qwen3"]
    cat.put("oc", [])
    assert cat.peek("oc") is None


def test_static_list_providers_keep_their_behaviour():
    calls = {"n": 0}

    def provider():
        calls["n"] += 1
        return [ModelRef("gemini", "gemini-2.5-pro")]

    cat = ModelCatalog(providers={"gemini": provider})
    cat.for_backend("gemini")
    cat.for_backend("gemini")
    assert calls["n"] == 1 and not cat.is_live_only("gemini")


def test_active_local_chat_is_not_unavailable_when_no_list_is_known(tmp_path):
    from llsm.conversation.session import ConversationSession
    from llsm.history.model_usage import ModelUsage
    from llsm.history.store import ConversationStore

    store = ConversationStore(tmp_path / "c")
    cat = ModelCatalog(providers={"openai-compatible": lambda: []})
    cat.mark_live_only("openai-compatible")
    session = ConversationSession(store=store, usage=ModelUsage(tmp_path / "u.json"),
                                  catalog=cat, cancel_in_flight=lambda: None)
    session.new_conversation(backend_kind="openai-compatible", model="qwen3:latest")
    assert session.is_active_model_available(None) is True
    cat.put("openai-compatible", [ModelRef("openai-compatible", "llama3:latest")])
    assert session.is_active_model_available(None) is False
    cat.put("openai-compatible", [ModelRef("openai-compatible", "qwen3:latest")])
    assert session.is_active_model_available(None) is True


# ---------- fetcher ----------

class _Sync:
    """Runs the worker inline and queues Tk callbacks for an explicit pump."""

    def __init__(self):
        self.pending = []

    def thread(self, target, args, **kw):
        sync = self

        class _T:
            def start(self_):
                target(*args)
        return _T()

    def tk_call(self, fn):
        self.pending.append(fn)

    def pump(self):
        while self.pending:
            self.pending.pop(0)()


def _fetcher(url_holder, sync=None):
    sync = sync or _Sync()
    cat = ModelCatalog(providers={"openai-compatible": lambda: []})
    cat.mark_live_only("openai-compatible")
    f = LiveModelFetcher(
        catalog=cat,
        probe=lambda kind: probe_models(SPEC, base_url=url_holder["url"], timeout_s=2.0),
        tk_call=sync.tk_call,
        thread_factory=sync.thread,
    )
    return f, cat, sync


def test_fetcher_fills_the_catalog_and_tells_listeners(server):
    holder = {"url": server.url}
    f, cat, sync = _fetcher(holder)
    seen = []
    f.add_listener(seen.append)
    assert f.refresh("openai-compatible") is True
    assert f.status("openai-compatible").loading  # result not applied yet
    sync.pump()
    assert [m.model_id for m in cat.for_backend("openai-compatible")] == \
        ["qwen3:latest", "qwen3-coder:30b"]
    assert f.status("openai-compatible").state == "ok"
    assert seen == ["openai-compatible"]


def test_fetcher_sees_a_newly_pulled_model_on_the_next_refresh(server):
    f, cat, sync = _fetcher({"url": server.url})
    f.refresh("openai-compatible"); sync.pump()
    server.ids = server.ids + ["gpt-oss:20b"]
    f.refresh("openai-compatible"); sync.pump()
    assert "gpt-oss:20b" in [m.model_id for m in cat.for_backend("openai-compatible")]


def test_fetcher_reports_a_server_that_went_down(server):
    holder = {"url": server.url}
    f, cat, sync = _fetcher(holder)
    f.refresh("openai-compatible"); sync.pump()
    holder["url"] = _closed_port_url()
    f.refresh("openai-compatible"); sync.pump()
    st = f.status("openai-compatible")
    assert st.state == "unreachable" and st.base_url == holder["url"]
    assert cat.for_backend("openai-compatible") == []


def test_one_fetch_at_a_time():
    sync = _Sync()
    started = []
    sync.thread = lambda target, args, **kw: type("T", (), {"start": lambda s: started.append(args)})()
    f, _, _ = _fetcher({"url": "http://x/v1"}, sync)
    assert f.refresh("openai-compatible") is True
    assert f.refresh("openai-compatible") is False
    assert len(started) == 1


def test_forget_drops_an_answer_still_in_flight(server):
    f, cat, sync = _fetcher({"url": server.url})
    f.refresh("openai-compatible")      # worker ran, result queued for Tk
    f.forget("openai-compatible")       # credentials changed meanwhile
    sync.pump()
    assert cat.for_backend("openai-compatible") == []
    assert f.status("openai-compatible") == LiveStatus()


# ---------- menu ----------

def test_menu_rows_for_a_server_only_provider(server, monkeypatch):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    try:
        from llsm.conversation import menu as menu_mod
        from llsm.llm import credentials

        # The colleague's state: a Gemini chat active, a local server saved.
        monkeypatch.setattr(credentials, "has_credential",
                            lambda kind, field, *a, **k: kind == "openai-compatible" and field == "base_url")
        monkeypatch.setattr(menu_mod, "_stored_base_url", lambda kind: server.url)

        class _Conv:
            backend_kind, model = "gemini", "gemini-2.5-flash"

        class _Session:
            store = type("S", (), {"list_conversations": lambda self: []})()
            def current(self): return _Conv()
            def current_id(self): return None

        class _Usage:
            def recent(self, limit): return []

        holder = {"url": server.url}
        f, cat, sync = _fetcher(holder)
        picked = []
        builder = menu_mod.FileMenuBuilder(
            root=root, session=_Session(), catalog=cat, usage=_Usage(),
            backend_provider=lambda: None, on_new=lambda m: None,
            on_resume=lambda c: None, on_switch_model=lambda k, m: picked.append((k, m)),
            on_open_history=lambda: None, live_models=f,
        )
        builder.build(tk.Menu(root))

        def rows():
            m = builder._models_menu
            for i in range(m.index("end") + 1):
                if m.type(i) == "cascade" and m.entrycget(i, "label").startswith("OpenAI-compatible"):
                    sub = root.nametowidget(m.entrycget(i, "menu"))
                    return [(sub.entrycget(j, "label").strip(), sub.entrycget(j, "state"))
                            for j in range(sub.index("end") + 1)]
            raise AssertionError("no OpenAI-compatible cascade")

        assert rows() == [("(loading models…)", "disabled")]
        sync.pump()  # the fetch lands: the listener rebuilds the cascade
        assert [r[0] for r in rows()] == [
            "[local:%d] qwen3:latest" % int(server.url.split(":")[2].split("/")[0]),
            "[local:%d] qwen3-coder:30b" % int(server.url.split(":")[2].split("/")[0]),
        ]

        server.ids = []
        builder._populate_models(); sync.pump()
        assert rows() == [("(server has no models)", "disabled")]

        holder["url"] = _closed_port_url()
        builder._populate_models(); sync.pump()
        assert rows() == [(f"(server not reachable at {holder['url']})", "disabled")]
    finally:
        root.destroy()
