"""Who may talk to the local MCP server.

A server on 127.0.0.1 is reachable from any web page in the user's browser, and its
tools write files. Every request needs the per-server token, and anything that looks
like a browser is refused.
"""
from __future__ import annotations

import json
import secrets

TOKEN_HEADER = "X-LLSM-Token"

_BROWSER_HEADERS = ("origin", "sec-fetch-site", "sec-fetch-mode")


def refusal(headers: dict[str, str], token: str, port: int) -> tuple[int, str] | None:
    """Why this request may not pass, as (status, reason); None if it may. Header names in lower case."""
    if any(name in headers for name in _BROWSER_HEADERS):
        return 403, "requests from a browser are not allowed"
    if headers.get("host") != f"127.0.0.1:{port}":
        return 403, "wrong Host header"
    if not secrets.compare_digest(headers.get(TOKEN_HEADER.lower(), ""), token):
        return 401, "missing or wrong token"
    return None


class GuardMiddleware:
    """ASGI middleware applying ``refusal`` to every HTTP request."""

    def __init__(self, app, token: str, port: int) -> None:
        self.app = app
        self.token = token
        self.port = port

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        refused = refusal(headers, self.token, self.port)
        if refused is None:
            await self.app(scope, receive, send)
            return
        status, reason = refused
        body = json.dumps({"error": reason}).encode("utf-8")
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})
