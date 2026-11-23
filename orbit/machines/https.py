"""HTTPS for everything that crosses the network, by Tailscale (`tailscale serve`).

The server itself keeps listening on 127.0.0.1 over plain HTTP: this machine's own page and app use that, so they work
even with Tailscale down. Other machines, browsers elsewhere and the phone reach it at
`https://<machine>.<tailnet>.ts.net:<port>` (8443 by default): Tailscale terminates TLS there with a real certificate it
issues and renews for the machine's name, and hands the request to 127.0.0.1:<server port>, adding
`X-Forwarded-Proto: https` (and keeping the Host). HTTPS is what a phone's browser asks before it allows the microphone,
notifications, a service worker or the clipboard; the traffic itself is already encrypted by Tailscale.

When it is on, a request from the network that did not come through it (Tailscale in userspace mode hands every port of
the machine's tailnet address to 127.0.0.1, so plain HTTP still arrives) is sent to the HTTPS address (handler.py).

The Tailscale command is `tailscale` (or the macOS app's), or `ORBIT_TAILSCALE` / `tailscale = "…"` in config.toml,
e.g. `tailscale --socket ~/.local/state/tailscale/tailscaled.sock` for a userspace tailscaled.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

DEFAULT_PORT = 8443
_cache: dict[str, Any] = {"at": 0.0, "status": None}
_lock = threading.Lock()


def command(state_dir: Path | None = None) -> list[str] | None:
    """The tailscale CLI, as argv (None: not found)."""
    raw = os.environ.get("ORBIT_TAILSCALE", "")
    if not raw and state_dir is not None:
        try:
            import tomllib
            cfg = tomllib.loads((Path(state_dir) / "config.toml").read_text())
            raw = str(cfg.get("tailscale") or "")
        except (OSError, ValueError):
            raw = ""
    if raw:
        return [os.path.expanduser(x) for x in shlex.split(raw)]
    for c in ("tailscale", "/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        found = shutil.which(c) or (c if os.path.isfile(c) else None)
        if found:
            return [found]
    return None


def _run(argv: list[str], timeout: float = 15) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def _json(ts: list[str], *args: str) -> dict:
    code, out = _run(ts + list(args) + ["--json"])
    if code != 0:
        raise RuntimeError(out.strip().splitlines()[-1] if out.strip() else "tailscale failed")
    try:
        return json.loads(out[out.index("{"):]) if "{" in out else {}
    except ValueError as e:
        raise RuntimeError(f"tailscale: unreadable answer ({e})") from e


def status(state_dir: Path, server_port: int, fresh: bool = False) -> dict[str, Any]:
    """{available, on, url, host, port, error}: whether HTTPS reaches this server (cached for 30 s)."""
    with _lock:
        if not fresh and _cache["status"] is not None and time.time() - _cache["at"] < 30:
            return dict(_cache["status"])
    ts = command(state_dir)
    st: dict[str, Any] = {"available": bool(ts), "on": False, "url": "", "host": "", "port": DEFAULT_PORT, "error": ""}
    if not ts:
        st["error"] = "Tailscale is not installed here (or set tailscale = \"…\" in config.toml)"
    else:
        try:
            me = _json(ts, "status")
            host = str(((me.get("Self") or {}).get("DNSName") or "")).rstrip(".")
            st["host"] = host
            if not me.get("CertDomains"):
                st["error"] = "HTTPS certificates are off for this tailnet (Tailscale admin console → DNS → HTTPS Certificates)"
            serve = _json(ts, "serve", "status")
            target = f"127.0.0.1:{server_port}"
            for hostport, web in (serve.get("Web") or {}).items():
                for _path, h in ((web or {}).get("Handlers") or {}).items():
                    if target in str((h or {}).get("Proxy") or ""):
                        name, _, port = hostport.rpartition(":")
                        st.update(on=True, port=int(port or 443), url=f"https://{name}" + ("" if port in ("", "443") else f":{port}"))
        except RuntimeError as e:
            st["error"] = str(e)
    with _lock:
        _cache.update(at=time.time(), status=st)
    if ts and not st["error"]:
        save(state_dir, st)
    return dict(st)


def enable(state_dir: Path, server_port: int, port: int = DEFAULT_PORT) -> dict[str, Any]:
    ts = command(state_dir)
    if not ts:
        raise RuntimeError("Tailscale is not installed here (or set tailscale = \"…\" in config.toml)")
    code, out = _run(ts + ["serve", "--bg", f"--https={port}", f"http://127.0.0.1:{server_port}"], timeout=60)
    if code != 0:
        raise RuntimeError(out.strip() or "tailscale serve failed")
    return status(state_dir, server_port, fresh=True)


def disable(state_dir: Path, server_port: int) -> dict[str, Any]:
    ts = command(state_dir)
    st = status(state_dir, server_port, fresh=True)
    if ts and st["on"]:
        code, out = _run(ts + ["serve", f"--https={st['port']}", "off"], timeout=30)
        if code != 0:
            raise RuntimeError(out.strip() or "tailscale serve off failed")
    return status(state_dir, server_port, fresh=True)


def via_https(headers) -> bool:
    """The request came through Tailscale's HTTPS (or another TLS-terminating proxy on this machine)."""
    return (headers.get("X-Forwarded-Proto") or "").lower() == "https"


# What the guard needs on every request, without running tailscale: the HTTPS address when it is on (<state>/https.json,
# written when it is switched on or off here, or when a status check finds it).
_saved: dict[str, Any] = {"path": None, "mtime": None, "url": ""}


def save(state_dir: Path, st: dict[str, Any]) -> None:
    p = Path(state_dir) / "https.json"
    p.write_text(json.dumps({"url": st["url"] if st.get("on") else ""}))


def saved_url(state_dir: Path) -> str:
    p = Path(state_dir) / "https.json"
    try:
        m = p.stat().st_mtime
    except OSError:
        return ""
    if _saved["path"] != p or _saved["mtime"] != m:
        try:
            _saved.update(path=p, mtime=m, url=str(json.loads(p.read_text()).get("url") or ""))
        except (OSError, ValueError):
            _saved.update(path=p, mtime=m, url="")
    return _saved["url"]
