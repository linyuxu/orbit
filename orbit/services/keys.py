"""Orbit's own API keys (Settings → Voice): the OpenAI API key, for GPT Live voice.

Kept like a plugin's secrets: <state>/secrets/_orbit.json, 0600 in a 0700 folder (a plugin's name cannot start with
"_", so it never meets a plugin's file). Set and read on this machine only; the page learns whether one is set and
its last four characters, never the key. Sent only to the provider it is for.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

KEYS = {"openai": {"label": "OpenAI API key", "prefix": "sk-"}}
OPENAI_BASE = os.environ.get("ORBIT_OPENAI_BASE", "https://api.openai.com")  # (tests point it at a fake)


def _file(state: Path) -> Path:
    return Path(state) / "secrets" / "_orbit.json"


def _read(state: Path) -> dict[str, str]:
    try:
        d = json.loads(_file(state).read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in d.items() if isinstance(v, str)} if isinstance(d, dict) else {}


def get(state: Path, name: str) -> str:
    """The key, or "" when none is set."""
    return _read(state).get(name, "")


def status(state: Path) -> dict[str, Any]:
    """{name: {label, set, hint}}: hint is "sk-…abcd", never more."""
    have = _read(state)
    out = {}
    for name, k in KEYS.items():
        v = have.get(name, "")
        out[name] = {"label": k["label"], "set": bool(v), "hint": f"{v[:3]}…{v[-4:]}" if len(v) >= 12 else ""}
    return out


def set_key(state: Path, name: str, value: str | None) -> dict[str, Any]:
    """Keep (or with None / "" forget) a key. ValueError: an unknown name or a value that is not a key."""
    if name not in KEYS:
        raise ValueError(f"no such key: {name} (one of {', '.join(KEYS)})")
    d = _read(state)
    if value is None or not str(value).strip():
        d.pop(name, None)
    else:
        v = str(value).strip()
        pre = KEYS[name]["prefix"]
        if any(c.isspace() for c in v) or not 20 <= len(v) <= 400 or (pre and not v.startswith(pre)):
            raise ValueError(f"that does not look like an {KEYS[name]['label']} (it starts with {pre}, no spaces)")
        d[name] = v
    f = _file(state)
    f.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(f.parent, 0o700)
    tmp = f.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(d, fh)
    os.chmod(tmp, 0o600)
    os.replace(tmp, f)
    return status(state)


def check(state: Path, name: str, timeout: float = 10.0) -> dict[str, Any]:
    """Ask the provider whether the key works (OpenAI: GET /v1/models, no model call, nothing billed).
    {"ok": True} or {"ok": False, "error"}."""
    if name not in KEYS:
        raise ValueError(f"no such key: {name}")
    key = get(state, name)
    if not key:
        return {"ok": False, "error": "no key is set"}
    req = urllib.request.Request(f"{OPENAI_BASE}/v1/models?limit=1", headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(4096)
        return {"ok": True}
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read(65536)).get("error", {}).get("message") or ""
        except (ValueError, AttributeError):
            msg = ""
        return {"ok": False, "error": f"OpenAI answered {e.code}" + (f": {msg}" if msg else "")}
    except OSError as e:
        return {"ok": False, "error": f"could not reach OpenAI: {e}"}
