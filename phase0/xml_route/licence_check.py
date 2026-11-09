"""Why does SCIA not get a licence? Prints expiry dates and endpoints, never tokens.

Reads the claims of the cached licence tokens (no signature, no token text is
printed), lists the sign-in hosts found in SCIA's own binaries and tests whether
those hosts answer from this machine.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import re
import socket
import ssl
from pathlib import Path

PROTECTION = Path.home() / "AppData" / "Local" / "SCIA" / "Protection"
INSTALL = Path(r"C:\Program Files\SCIA\Engineer24.0")
BINARIES = ["defender.dll", "DukeLogin.exe"]
HOST = re.compile(rb"https?://([a-z0-9.-]+\.[a-z]{2,})", re.I)
HOST_WIDE = re.compile(rb"h\x00t\x00t\x00p\x00s?\x00?:\x00/\x00/\x00((?:[a-zA-Z0-9.-]\x00){4,})")
NOISE = re.compile(r"(w3\.org|microsoft\.com|digicert|verisign|symantec|globalsign|sectigo|openssl|usertrust|comodo|thawte|entrust|qt\.io|xmlsoap|purl\.org|schemas)", re.I)


def claims(path: Path) -> dict:
    payload = path.read_text(encoding="ascii", errors="ignore").split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def stamp(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "-"
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def hosts() -> list[str]:
    found: set[str] = set()
    for name in BINARIES:
        data = (INSTALL / name).read_bytes()
        found |= {m.group(1).decode().lower() for m in HOST.finditer(data)}
        found |= {m.group(1).decode("utf-16le").lower() for m in HOST_WIDE.finditer(data)}
    return sorted(h for h in found if not NOISE.search(h))


def reach(host: str) -> str:
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=8) as sock:
            with context.wrap_socket(sock, server_hostname=host) as tls:
                return f"ok ({tls.version()})"
    except Exception as exc:  # the failure text is the diagnostic
        return f"FAILED: {type(exc).__name__}: {exc}"


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    print("now:", now.strftime("%Y-%m-%d %H:%M UTC"))
    for path in sorted(PROTECTION.glob("*.jwt")):
        try:
            data = claims(path)
            print(f"{path.name}: issued {stamp(data.get('iat'))}, expires {stamp(data.get('exp'))}")
        except Exception as exc:
            print(f"{path.name}: unreadable ({type(exc).__name__})")
    for host in hosts():
        print(f"{host}: {reach(host)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
