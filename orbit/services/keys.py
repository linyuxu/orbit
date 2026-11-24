"""Orbit's own API keys (Settings → Voice): OpenAI API keys, for GPT Live voice.

Kept like a plugin's secrets: <state>/secrets/_orbit.json, 0600 in a 0700 folder (a plugin's name cannot start with
"_", so it never meets a plugin's file). Set and read on this machine only; the page learns each key's name, its
last four characters and how it last did, never the key. Sent only to the provider it is for.

Several keys per provider, each with a name, in the order they are tried: a call that one is refused (a bad key, no
quota, rate-limited: 401 / 402 / 403 / 429) goes on with the next, and a key refused in the last few minutes is tried
after the others. The file: {"openai": [{"id", "name", "key"}, …]}; an older file's single string is a one-key list.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

KEYS = {"openai": {"label": "OpenAI API key", "prefix": "sk-"}}
OPENAI_BASE = os.environ.get("ORBIT_OPENAI_BASE", "https://api.openai.com")  # (tests point it at a fake)


def _file(state: Path) -> Path:
    return Path(state) / "secrets" / "_orbit.json"


FAILOVER = (401, 402, 403, 429)  # the key was refused: the next one may not be
COOL_S = 600.0  # a key refused this recently is tried after the others
_last: dict[tuple[str, str], dict] = {}  # (state, key id) → {"ok": bool, "t", "error"?}: how it last did (this run)


def _read(state: Path) -> dict[str, list[dict]]:
    try:
        d = json.loads(_file(state).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(d, dict):
        return {}
    out: dict[str, list[dict]] = {}
    for name, v in d.items():
        if isinstance(v, str) and v:
            out[name] = [{"id": "k" + hashlib.sha256(v.encode()).hexdigest()[:8], "name": "", "key": v}]
        elif isinstance(v, list):
            out[name] = [{"id": str(e["id"]), "name": str(e.get("name") or ""), "key": e["key"]} for e in v
                         if isinstance(e, dict) and isinstance(e.get("key"), str) and e["key"] and e.get("id")]
    return out


def _write(state: Path, d: dict[str, list[dict]]) -> None:
    f = _file(state)
    f.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(f.parent, 0o700)
    tmp = f.with_suffix(f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({k: v for k, v in d.items() if v}, fh)
    os.chmod(tmp, 0o600)
    os.replace(tmp, f)


def _hint(v: str) -> str:
    return f"{v[:3]}…{v[-4:]}" if len(v) >= 12 else ""


def label(e: dict) -> str:
    """How a key is named in logs and messages: its name, else its hint (never the key)."""
    return e.get("name") or _hint(e["key"]) or e["id"]


def entries(state: Path, name: str) -> list[dict]:
    """The keys to try, in order ({"id", "name", "key"}): as set, with those refused in the last COOL_S after the rest."""
    es = _read(state).get(name, [])
    now = time.time()

    def cooling(e):
        r = _last.get((str(state), e["id"]))
        return bool(r and not r["ok"] and now - r["t"] < COOL_S)
    return sorted(es, key=cooling)  # (stable: the set order otherwise)


def get(state: Path, name: str) -> str:
    """The key to use first, or "" when none is set."""
    es = entries(state, name)
    return es[0]["key"] if es else ""


def note(state: Path, kid: str, ok: bool, error: str = "") -> None:
    """How a key just did (a call's create, a check): shown in Settings, and a refused one is tried last for a while."""
    _last[(str(state), kid)] = {"ok": ok, "t": time.time(), **({"error": error[:300]} if error else {})}


def status(state: Path) -> dict[str, Any]:
    """{name: {label, set, hint, keys: [{id, name, hint, last?}]}}: hints are "sk-…abcd", never more."""
    have = _read(state)
    out = {}
    for name, k in KEYS.items():
        es = have.get(name, [])
        out[name] = {"label": k["label"], "set": bool(es), "hint": _hint(es[0]["key"]) if es else "",
                     "keys": [{"id": e["id"], "name": e["name"], "hint": _hint(e["key"]),
                               **({"last": _last[(str(state), e["id"])]} if (str(state), e["id"]) in _last else {})}
                              for e in es]}
    return out


def _valid(name: str, value: str) -> str:
    if name not in KEYS:
        raise ValueError(f"no such key: {name} (one of {', '.join(KEYS)})")
    v = str(value or "").strip()
    pre = KEYS[name]["prefix"]
    if any(c.isspace() for c in v) or not 20 <= len(v) <= 400 or (pre and not v.startswith(pre)):
        raise ValueError(f"that does not look like an {KEYS[name]['label']} (it starts with {pre}, no spaces)")
    return v


def _name(n: Any) -> str:
    n = " ".join(str(n or "").split())
    if len(n) > 40:
        raise ValueError("a key's name is at most 40 characters")
    return n


def set_key(state: Path, name: str, value: str | None) -> dict[str, Any]:
    """The one-key way (older clients, tests): a key goes first (kept once); None / "" forgets them all."""
    if name not in KEYS:
        raise ValueError(f"no such key: {name} (one of {', '.join(KEYS)})")
    d = _read(state)
    if value is None or not str(value).strip():
        d.pop(name, None)
    else:
        v = _valid(name, value)
        es = d.get(name, [])
        old = next((e for e in es if e["key"] == v), None)
        d[name] = [old or {"id": _new_id(), "name": "", "key": v}] + [e for e in es if e["key"] != v]
    _write(state, d)
    return status(state)


def _new_id() -> str:
    return "k" + secrets.token_hex(4)


def edit_keys(state: Path, name: str, op: str, kid: str = "", value: str | None = None, label_: Any = None,
              to: int | None = None) -> dict[str, Any]:
    """add (value, name: last in the order), update (id: its name, and/or a new value), remove (id), move (id, to: its
    place in the order, 0 first). ValueError for an unknown op / id or a bad value."""
    if name not in KEYS:
        raise ValueError(f"no such key: {name} (one of {', '.join(KEYS)})")
    d = _read(state)
    es = d.setdefault(name, [])
    if op == "add":
        v = _valid(name, value or "")
        if any(e["key"] == v for e in es):
            raise ValueError("that key is already in the list")
        if len(es) >= 10:
            raise ValueError("at most 10 keys")
        es.append({"id": _new_id(), "name": _name(label_), "key": v})
    else:
        e = next((x for x in es if x["id"] == kid), None)
        if e is None:
            raise ValueError(f"no key {kid!r}")
        if op == "update":
            if label_ is not None:
                e["name"] = _name(label_)
            if value:
                v = _valid(name, value)
                if any(x["key"] == v and x is not e for x in es):
                    raise ValueError("that key is already in the list")
                e["key"] = v
                _last.pop((str(state), e["id"]), None)
        elif op == "remove":
            es.remove(e)
            _last.pop((str(state), e["id"]), None)
        elif op == "move":
            if not isinstance(to, int):
                raise ValueError("move: to (a place in the order) is needed")
            es.remove(e)
            es.insert(max(0, min(to, len(es))), e)
        else:
            raise ValueError(f"no such op: {op} (add, update, remove, move)")
    _write(state, d)
    return status(state)


def check(state: Path, name: str, kid: str = "", timeout: float = 10.0) -> dict[str, Any]:
    """Ask the provider whether a key works (the first, or the one with id kid; OpenAI: GET /v1/models, no model
    call, nothing billed). {"ok": True} or {"ok": False, "error"}."""
    if name not in KEYS:
        raise ValueError(f"no such key: {name}")
    es = _read(state).get(name, [])
    e = next((x for x in es if x["id"] == kid), None) if kid else (es[0] if es else None)
    if e is None:
        return {"ok": False, "error": "no such key" if kid else "no key is set"}
    r = _ask(e["key"], timeout)
    if r["ok"] or r["error"].startswith("OpenAI answered"):  # (not reaching OpenAI says nothing about the key)
        note(state, e["id"], r["ok"], r.get("error", ""))
    return r


def _ask(key: str, timeout: float) -> dict[str, Any]:
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
