"""Remote servers: serving this one to other machines, and reaching theirs.

Served (`orbit serve --remote`): the server also listens beyond 127.0.0.1. A request from this machine needs
nothing new; one from elsewhere must carry the token (`Authorization: Bearer T`, `?token=T`, or the `orbit_token`
cookie a browser gets when it opens the URL with ?token=). The token is <state>/token (0600); `orbit token
--rotate` replaces it. Plain HTTP: meant for a network that is already private and encrypted (Tailscale, a LAN).

Reached (`orbit remote add NAME URL`): <state>/remotes.json keeps {name: {url, token}}; the local server
forwards /api/remote/<name>/<path> to that server with its token, so the page and `orbit-agent` on this machine
work with the remote's sessions (`NAME/session`).
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


COOKIE = "orbit_token"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


# ------------------------------------------------------------------ the token (served side)
def token_path(state: Path) -> Path:
    return state / "token"


def token(state: Path, create: bool = True) -> str:
    p = token_path(state)
    try:
        t = p.read_text().strip()
        if t:
            return t
    except OSError:
        pass
    if not create:
        return ""
    return rotate(state)


def rotate(state: Path) -> str:
    t = secrets.token_urlsafe(24)
    p = token_path(state)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(t + "\n")
    os.chmod(p, 0o600)
    return t


def presented(headers, query: str) -> str:
    """The token a request carries, from the header, the query or the cookie."""
    auth = headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    q = urllib.parse.parse_qs(query).get("token")
    if q:
        return q[0]
    for part in (headers.get("Cookie") or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE:
            return urllib.parse.unquote(v)
    return ""


def valid(state: Path, given: str) -> bool:
    t = token(state, create=False)
    return bool(t and given) and hmac.compare_digest(t, given)


def is_loopback(addr: str) -> bool:
    return addr.startswith("127.") or addr in ("::1", "::ffff:127.0.0.1") or addr.startswith("::ffff:127.")


def addresses() -> list[str]:
    """This machine's addresses worth printing in a URL: Tailscale (100.64/10) first, then the LAN."""
    found: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    try:  # the address the default route uses
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("100.100.100.100", 80))
        found.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    out = [a for a in dict.fromkeys(found) if not a.startswith("127.")]
    return sorted(out, key=lambda a: (not a.startswith("100."), a))


def url(state: Path, host: str, port: int) -> str:
    return f"http://{host}:{port}/?token={token(state)}"


# ------------------------------------------------------------------ remotes (reaching side)
def remotes_path(state: Path) -> Path:
    return state / "remotes.json"


def load(state: Path) -> dict[str, dict[str, str]]:
    try:
        d = json.loads(remotes_path(state).read_text())
        return {k: v for k, v in d.items() if isinstance(v, dict) and v.get("url")}
    except (OSError, ValueError):
        return {}


def _save(state: Path, d: dict) -> None:
    p = remotes_path(state)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # it holds tokens
    with os.fdopen(fd, "w") as f:
        json.dump(d, f, indent=1)


def parse_url(raw: str) -> tuple[str, str]:
    """'http://gpu-box:8770/?token=T' → ('http://gpu-box:8770', 'T')."""
    u = urllib.parse.urlparse(raw.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("a remote is a URL like http://HOST:8770/?token=…")
    tok = (urllib.parse.parse_qs(u.query).get("token") or [""])[0]
    if not tok:
        raise ValueError("the URL has no ?token= (on the remote machine: orbit url)")
    port = f":{u.port}" if u.port else ""
    return f"{u.scheme}://{u.hostname}{port}", tok


def name_from_url(raw: str) -> str:
    """The name a remote gets from its URL's host (http://gpu-box.tail1.ts.net:8770 → gpu-box); '' for an address."""
    host = urllib.parse.urlparse(raw.strip()).hostname or ""
    if not host or re.fullmatch(r"[\d.]+|.*:.*", host):
        return ""
    return re.sub(r"[^a-z0-9_-]+", "-", host.split(".")[0].lower()).strip("-")[:32]


def add(state: Path, name: str, raw_url: str, check: bool = True) -> dict[str, Any]:
    name = name.strip().lower() or name_from_url(raw_url)
    if not name:
        raise ValueError("give the remote a name (its URL is an address, which cannot name it)")
    if not NAME_RE.match(name):
        raise ValueError("a remote's name: lower-case letters, digits, - and _ (it prefixes its sessions: name/session)")
    base, tok = parse_url(raw_url)
    info = {"url": base, "token": tok}
    if check:  # reachable, and the token works
        try:
            call(info, "GET", "/api/sessions", timeout=8)
        except RemoteError as e:
            if e.code == 401:
                raise RemoteError(401, f"{base} refused the token (it must be that machine's current one: orbit url there)") from None
            raise
    d = load(state)
    d[name] = info
    _save(state, d)
    return {"name": name, "url": base}


def remove(state: Path, name: str) -> None:
    d = load(state)
    if name not in d:
        raise KeyError(name)
    del d[name]
    _save(state, d)


def call(info: dict, method: str, path: str, body: dict | None = None, timeout: float = 15) -> Any:
    req = urllib.request.Request(info["url"] + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {info['token']}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", str(e))
        except ValueError:
            msg = str(e)
        raise RemoteError(e.code, msg) from None
    except OSError as e:
        raise RemoteError(502, f"cannot reach {info['url']} ({e.__class__.__name__}: {e})") from None


class RemoteError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


def status(state: Path) -> list[dict[str, Any]]:
    """Each remote, whether it answers, and its open sessions (for the page and `orbit remote ls`)."""
    out = []
    for name, info in sorted(load(state).items()):
        row: dict[str, Any] = {"name": name, "url": info["url"], "ok": False, "sessions": []}
        try:
            d = call(info, "GET", "/api/sessions", timeout=4)
            row["ok"] = True
            row["sessions"] = d.get("sessions", [])
            row["groups"] = d.get("groups", [])
        except RemoteError as e:
            row["error"] = str(e)
        out.append(row)
    return out
