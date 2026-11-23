"""Native plugin views (plugin API 2.0, builddoc/plugin-views.md): the threads and docs a plugin keeps in Orbit, which
the page draws with its own code (no iframe), and the `svc:` proxy that serves their files from the plugin's service.

A thread is named by (plugin, kind, key): kind is one of the plugin's `[[ui.tab]] view = "thread"` kinds. A doc by
(plugin, id). The plugin pushes them (PUT, PATCH with its token), or Orbit asks its service in the background when a
view opens with no copy or one older than STALE s (`GET /orbit/thread?kind=&key=`, `GET /orbit/doc?id=`), or without a
service its [cli] (`thread KIND KEY`, `doc ID`, one run per key at a time). The page never waits for either.

Kept in memory (at most LIVE_MAX views; the rest are read back when asked for) and in `<state>/plugin-threads/<plugin>/`
(an index, and a file per view). Caps: a message MSG_MAX bytes, a thread THREAD_MAX messages (the newest), VIEWS_MAX
threads and VIEWS_MAX docs per plugin (the least recently opened go). Every change has a `rev` (+1); the pages hear
the change only ({"t": "plugin-thread", rev, change}), and fetch the whole on a gap.

A reply typed in a thread is a pending message in the store (it shows "Sending…" in every window, and after a reload)
until the plugin pushes the real one with the same `client_id` (or the `id` its /orbit/send answered); a failure marks
it, and Retry sends it again with the same client_id.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

MSG_MAX = 32_768  # bytes of one message (its JSON)
BODY_MAX = 1_048_576  # bytes of a request a plugin pushes
THREAD_MAX = 2000  # messages a thread keeps (the newest)
VIEWS_MAX = 300  # threads (and docs) per plugin: the least recently opened go
LIVE_MAX = 64  # views kept in memory
STALE = 60.0  # s: an older copy is asked for again when a view opens
FETCH_TIMEOUT = 10.0  # s for the service's answer (a thread, a doc, older messages)
CLI_TIMEOUT = 120.0
SEND_TIMEOUT = 30.0
SAVE_DELAY = 1.0
KEY_MAX = 300
DOC_MAX = 1_000_000  # characters of a doc's Markdown
PROXY_MAX = 20 * 1024 * 1024  # bytes the svc: proxy serves
PROXY_TIMEOUT = 10.0  # s, for the whole answer
PROXY_AT_ONCE = 4  # requests in flight per plugin
ID_RE = r"[a-z0-9][a-z0-9_-]*"
CID_RE = r"[A-Za-z0-9_-]{1,100}"

BROADCAST: Callable[[dict], None] | None = None  # Desk.broadcast (set by the server)
_lock = threading.RLock()
_live: dict[tuple, "View"] = {}  # (plugin, what, kind, key) → the view, in memory
_index: dict[str, dict[tuple, dict]] = {}  # plugin → (what, kind, key) → {"file", "opened", "updated"}
_dirty: set[tuple] = set()
_index_dirty: set[str] = set()
_saver: threading.Timer | None = None
_cli_running: set[tuple] = set()  # one [cli] thread/doc run per key at a time
_proxy_sem: dict[str, threading.BoundedSemaphore] = {}


class View:
    """A thread (what "thread": data {title, sub, url, composer, more, unread, messages}) or a doc (what "doc":
    data {title, markdown, version})."""

    def __init__(self, plugin: str, what: str, kind: str, key: str):
        self.plugin, self.what, self.kind, self.key = plugin, what, kind, key
        self.data: dict[str, Any] = {"messages": []} if what == "thread" else {}
        self.rev = 0
        self.updated = 0.0  # when it was last pushed or fetched (0: never)
        self.asked = 0.0  # when Orbit last asked for it in the background
        self.opened = 0.0
        self.fetching = False
        self.error = ""

    @property
    def id(self) -> tuple:
        return (self.plugin, self.what, self.kind, self.key)

    def public(self) -> dict[str, Any]:
        d = {k: v for k, v in self.data.items() if k != "messages"}
        if self.what == "thread":
            d["messages"] = [{k: v for k, v in m.items() if not k.startswith("_")} for m in self.data["messages"]]
        return {**d, "rev": self.rev, "updated": self.updated or None, "loading": self.fetching and not self.updated,
                **({"error": self.error} if self.error else {})}


# ---------------------------------------------------------------------- checking what a plugin sends
def _str(v: Any, what: str, n: int, default: str | None = None) -> str:
    if v is None and default is not None:
        return default
    if not isinstance(v, str) or len(v) > n:
        raise ValueError(f"{what}: a string, {n} characters at most")
    return v


def svc_path_ok(p: str) -> bool:
    """A path of the plugin's service, as the svc: proxy takes it: /…, no control characters or backslashes, no
    encoded . / \\ % or control characters, no . or .. segments, no //."""
    if not isinstance(p, str) or not p.startswith("/") or len(p) > 2000 or p.startswith("//"):
        return False
    path = p.split("?", 1)[0]
    if any(ord(c) < 0x21 or ord(c) == 0x7F or c in '\\#"<>' for c in p):
        return False
    if re.search(r"%(?:[01][0-9a-f]|7f|2e|2f|5c|25)", p, re.I):
        return False
    return "//" not in path and not any(s in (".", "..") for s in path.split("/"))


def url_ok(u: Any, schemes: list[str] = ()) -> bool:
    """A link a plugin gives (a thread's or a message's url): https, http, or one of its declared schemes."""
    if not isinstance(u, str) or len(u) > 4096 or any(c.isspace() or c in '"<>' for c in u):
        return False
    m = re.match(r"([a-z][a-z0-9+.-]*):", u, re.I)
    return bool(m) and (m.group(1).lower() in ("https", "http") and u.lower().startswith(("https://", "http://"))
                        or m.group(1).lower() in schemes)


def message_of(x: Any, n: int, schemes: list[str], kinds: list[str]) -> dict[str, Any]:
    """One message, checked (other fields dropped). ValueError says what is wrong with it."""
    from orbit.plugins import _avatar
    what = f"message {n}"
    if not isinstance(x, dict):
        raise ValueError(f"{what}: an object {{id, author, time, markdown, …}}")
    mid = x.get("id")
    if isinstance(mid, (int, float)) and not isinstance(mid, bool):
        mid = str(mid)
    if not isinstance(mid, str) or not 0 < len(mid) <= KEY_MAX or mid.startswith("pending:"):
        raise ValueError(f"{what}: id: a string, 1–{KEY_MAX} characters")
    what = f"message {n} ({mid[:60]!r})"
    a = x.get("author") or {}
    if not isinstance(a, dict):
        raise ValueError(f"{what}: author: {{name, me?, avatar?}}")
    author: dict[str, Any] = {"name": " ".join(_str(a.get("name"), f"{what}: author.name", 200, "").split())}
    if a.get("me") is True:
        author["me"] = True
    av = _avatar(a.get("avatar"))
    if av:
        author["avatar"] = av
    out: dict[str, Any] = {"id": mid, "author": author, "markdown": _str(x.get("markdown"), f"{what}: markdown", MSG_MAX, "")}
    t = x.get("time")
    if t is not None:
        if isinstance(t, bool) or not isinstance(t, (int, float)):
            raise ValueError(f"{what}: time: a number (epoch seconds)")
        out["time"] = float(t) / 1000 if t > 1e11 else float(t)
    if x.get("url") is not None:
        if not url_ok(x["url"], schemes):
            raise ValueError(f"{what}: url: https://, http:// or one of the plugin's schemes")
        out["url"] = x["url"]
    files = x.get("files") or []
    if not isinstance(files, list) or len(files) > 50:
        raise ValueError(f"{what}: files: a list (50 at most)")
    for k, f in enumerate(files):
        if not isinstance(f, dict):
            raise ValueError(f"{what}: files[{k}]: {{name, url, image?}}")
        u = f.get("url")
        if not (isinstance(u, str) and (u.startswith("https://") and url_ok(u) or u.startswith("svc:") and svc_path_ok(u[4:]))):
            raise ValueError(f"{what}: files[{k}].url: https://… or svc:/path (a path of its service)")
        out.setdefault("files", []).append({"name": _str(f.get("name"), f"{what}: files[{k}].name", 300, ""), "url": u,
                                            **({"image": True} if f.get("image") is True else {})})
    reacts = x.get("reactions") or []
    if not isinstance(reacts, list) or len(reacts) > 50:
        raise ValueError(f"{what}: reactions: a list (50 at most)")
    for k, r in enumerate(reacts):
        if not isinstance(r, dict) or not isinstance(r.get("count", 1), int):
            raise ValueError(f"{what}: reactions[{k}]: {{emoji, count, me?}}")
        out.setdefault("reactions", []).append({"emoji": _str(r.get("emoji"), f"{what}: reactions[{k}].emoji", 64),
                                                "count": max(0, int(r.get("count", 1))),
                                                **({"me": True} if r.get("me") is True else {})})
    if x.get("edited") is True:
        out["edited"] = True
    rp = x.get("replies")
    if rp is not None:
        if not (isinstance(rp, dict) and rp.get("kind") in kinds and isinstance(rp.get("key"), str)
                and 0 < len(rp["key"]) <= KEY_MAX and isinstance(rp.get("count", 0), int)):
            raise ValueError(f"{what}: replies: {{count, kind: one of the plugin's thread kinds, key}}")
        out["replies"] = {"count": max(0, rp.get("count", 0)), "kind": rp["kind"], "key": rp["key"]}
    if x.get("client_id") is not None:
        if not isinstance(x["client_id"], str) or not re.fullmatch(CID_RE, x["client_id"]):
            raise ValueError(f"{what}: client_id: the one Orbit sent")
        out["client_id"] = x["client_id"]
    size = len(json.dumps(out, ensure_ascii=False).encode())
    if size > MSG_MAX:
        raise ValueError(f"{what}: {size} bytes; a message is {MSG_MAX} bytes at most")
    return out


def head_of(x: dict, schemes: list[str]) -> dict[str, Any]:
    """A thread's own fields that are present (title, sub, url, composer, more, unread), checked."""
    out: dict[str, Any] = {}
    for k in ("title", "sub"):
        if k in x:
            out[k] = " ".join(_str(x[k], k, 300, "").split())
    if "url" in x:
        if x["url"] not in (None, "") and not url_ok(x["url"], schemes):
            raise ValueError("url: https://, http:// or one of the plugin's schemes")
        out["url"] = x["url"] or ""
    if "composer" in x:
        c = x["composer"]
        if not isinstance(c, dict):
            raise ValueError("composer: {placeholder?, enabled?}")
        out["composer"] = {"placeholder": _str(c.get("placeholder"), "composer.placeholder", 200, ""),
                           "enabled": c.get("enabled", True) is not False}
    for k in ("more", "unread"):
        if k in x:
            if not isinstance(x[k], bool):
                raise ValueError(f"{k}: true or false")
            out[k] = x[k]
    return out


# ---------------------------------------------------------------------- what a plugin declared
def _manifest(plugin: str) -> dict[str, Any]:
    from orbit import plugins
    m = plugins.LOADED.get(plugin)
    if not m:
        raise KeyError(plugin)
    return m


def thread_kinds(plugin: str) -> list[str]:
    return [t["kind"] for t in _manifest(plugin)["tabs"] if t["view"] == "thread"]


def _thread_kind(plugin: str, kind: str) -> dict[str, Any]:
    m = _manifest(plugin)
    t = next((t for t in m["tabs"] if t["kind"] == kind and t["view"] == "thread"), None)
    if not t:
        have = ", ".join(thread_kinds(plugin)) or "none"
        raise ValueError(f"{plugin} declares no thread kind {kind!r} ([[ui.tab]] view = \"thread\"; it has: {have})")
    return t


def _has_docs(plugin: str) -> bool:
    m = _manifest(plugin)
    return any(x["view"] == "doc" for x in m["tabs"] + m["sidebars"] + m["panels"])


def _doc_service(plugin: str, doc: str) -> str | None:
    """The service a doc is asked of: the panel's or left tab's that shows it, else the first doc tab kind's."""
    m = _manifest(plugin)
    for x in m["panels"] + m["sidebars"]:
        if x["view"] == "doc" and x.get("doc") == doc:
            return x.get("service")
    return next((t["service"] for t in m["tabs"] if t["view"] == "doc"), None)


def _schemes(plugin: str) -> list[str]:
    return list(_manifest(plugin).get("schemes") or [])


def _token(plugin: str) -> str:
    from orbit import plugins
    return next((t for t, n in plugins.TOKENS.items() if n == plugin), "")


def _service(plugin: str, sid: str | None):
    """(the running service, its port) or (None, why not)."""
    from orbit.services import plugin_services
    if not sid:
        return None, ""
    svc = next((s for s in plugin_services.of(plugin) if s.id == sid), None)
    if svc is None or not svc.port or svc.state != "running":
        return None, f"{plugin}.{sid} is not running" + (f" ({svc.state})" if svc else "")
    return svc, ""


# ---------------------------------------------------------------------- the store
def _folder(plugin: str) -> Path | None:
    from orbit import plugins
    return plugins.STATE / "plugin-threads" / plugin if plugins.STATE is not None else None


def _fname(what: str, kind: str, key: str) -> str:
    h = hashlib.sha1((kind + "\0" + key).encode()).hexdigest()[:24]
    return f"{what[0]}-{h}.json"


def load() -> None:
    """(plugins.load) The index of each loaded plugin's views; a view itself is read when asked for."""
    from orbit import plugins
    with _lock:
        _live.clear()
        _index.clear()
        _dirty.clear()
        _index_dirty.clear()
        for name in plugins.LOADED:
            idx: dict[tuple, dict] = {}
            f = _folder(name)
            try:
                d = json.loads((f / "index.json").read_text()) if f and (f / "index.json").exists() else []
            except (OSError, ValueError):
                d = []
            for e in d if isinstance(d, list) else []:
                if isinstance(e, dict) and e.get("what") in ("thread", "doc") and isinstance(e.get("key"), str):
                    idx[(e["what"], str(e.get("kind") or ""), e["key"])] = {
                        "file": str(e.get("file") or ""), "opened": float(e.get("opened") or 0),
                        "updated": float(e.get("updated") or 0)}
            _index[name] = idx


def unload() -> None:
    flush()
    with _lock:
        _live.clear()
        _index.clear()


def flush() -> None:
    """Write the views (and indexes) that changed."""
    global _saver
    with _lock:
        if _saver:
            _saver.cancel()
            _saver = None
        snaps = [(v.plugin, _fname(v.what, v.kind, v.key), {"what": v.what, "kind": v.kind, "key": v.key, "data": v.data,
                                                              "rev": v.rev, "updated": v.updated})
                 for k in _dirty if (v := _live.get(k))]
        idx = {p: [{"what": w, "kind": k, "key": ky, **e} for (w, k, ky), e in _index.get(p, {}).items()]
               for p in _index_dirty}
        _dirty.clear()
        _index_dirty.clear()
    for plugin, name, d in snaps:
        _write(plugin, name, d)
    for plugin, d in idx.items():
        _write(plugin, "index.json", d)


def _write(plugin: str, name: str, d: Any) -> None:
    f = _folder(plugin)
    if f is None or not f.parent.parent.is_dir():  # (its state dir went away: a test's)
        return
    try:
        f.mkdir(parents=True, exist_ok=True)
        tmp = f / (name + ".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False))
        os.replace(tmp, f / name)
    except OSError as e:
        print(f"orbit: {plugin}: {name} not kept ({e})", flush=True)


def _save_soon(v: View) -> None:
    """(under the lock)"""
    global _saver
    _dirty.add(v.id)
    e = _index.setdefault(v.plugin, {}).setdefault((v.what, v.kind, v.key), {"file": _fname(v.what, v.kind, v.key),
                                                                            "opened": 0.0, "updated": 0.0})
    e["updated"], e["opened"] = v.updated, max(e["opened"], v.opened)
    _index_dirty.add(v.plugin)
    if _saver is None:
        _saver = threading.Timer(SAVE_DELAY, flush)
        _saver.daemon = True
        _saver.start()


def _view(plugin: str, what: str, kind: str, key: str, create: bool = False) -> View | None:
    """(under the lock) The view, from memory or its file; a new one when asked to."""
    k = (plugin, what, kind, key)
    v = _live.get(k)
    if v is not None:
        return v
    e = _index.get(plugin, {}).get((what, kind, key))
    if e:
        v = View(plugin, what, kind, key)
        try:
            d = json.loads((_folder(plugin) / e["file"]).read_text())
            v.data, v.rev, v.updated = d["data"], int(d.get("rev") or 0), float(d.get("updated") or 0)
            v.opened = e.get("opened") or 0
            for m in v.data.get("messages") or []:  # (a send cut off by a restart: Retry)
                if m.get("pending") == "sending":
                    m["pending"], m["error"] = "failed", "Orbit restarted before it was sent"
        except (OSError, ValueError, KeyError, TypeError):
            v = View(plugin, what, kind, key)
    elif not create:
        return None
    else:
        v = View(plugin, what, kind, key)
    _live[k] = v
    _keep_in_memory(k)
    return v


def _keep_in_memory(keep: tuple) -> None:
    """(under the lock) At most LIVE_MAX views in memory: the least recently opened that are written go."""
    if len(_live) <= LIVE_MAX:
        return
    for k, v in sorted(_live.items(), key=lambda kv: max(kv[1].opened, kv[1].updated)):
        if len(_live) <= LIVE_MAX:
            break
        if k != keep and k not in _dirty and not v.fetching:
            _live.pop(k)


def _cap(plugin: str, what: str) -> None:
    """(under the lock) VIEWS_MAX threads (docs) per plugin: the least recently opened go, their files too."""
    idx = _index.get(plugin, {})
    mine = [(k, e) for k, e in idx.items() if k[0] == what]
    for k, e in sorted(mine, key=lambda ke: max(ke[1]["opened"], ke[1]["updated"]))[:max(0, len(mine) - VIEWS_MAX)]:
        idx.pop(k, None)
        _live.pop((plugin, *k), None)
        _dirty.discard((plugin, *k))
        f = _folder(plugin)
        if f is not None:
            try:
                (f / e["file"]).unlink()
            except OSError:
                pass
        _index_dirty.add(plugin)


def _tell(ev: dict) -> None:
    b = BROADCAST
    if b is None:
        return
    try:
        b(ev)
    except Exception:  # noqa: BLE001 - a page that went away is not the plugin's problem
        pass


def _changed(v: View, change: dict) -> dict:
    """(under the lock) A new rev, saved soon; the event to tell (after the lock)."""
    v.rev += 1
    _save_soon(v)
    if v.what == "thread":
        return {"t": "plugin-thread", "plugin": v.plugin, "kind": v.kind, "key": v.key, "rev": v.rev, "change": change}
    return {"t": "plugin-doc", "plugin": v.plugin, "id": v.key, "rev": v.rev}


# ---------------------------------------------------------------------- threads: pushed by the plugin
def _matches(m: dict, p: dict) -> bool:
    """Whether a pushed message is the real one of a pending message."""
    return bool(m.get("client_id") and m["client_id"] == p.get("client_id") or p.get("_id") and m["id"] == p["_id"])


def _apply_thread(v: View, head: dict, msgs: list[dict]) -> int:
    """(under the lock) A whole thread replaces v's (pending messages not in it are kept at its end). Dropped count."""
    pend = [p for p in v.data.get("messages", []) if p.get("pending") and not any(_matches(m, p) for m in msgs)]
    dropped = max(0, len(msgs) - THREAD_MAX)
    msgs = msgs[dropped:]
    seen, uniq = set(), []
    for m in msgs:  # (an id given twice: the later one)
        if m["id"] in seen:
            uniq = [x for x in uniq if x["id"] != m["id"]]
        seen.add(m["id"])
        uniq.append(m)
    v.data = {**{k: v.data[k] for k in v.data if k != "messages"}, **head, "messages": uniq + pend}
    v.updated, v.error = time.time(), ""
    return dropped


def put_thread(plugin: str, kind: str, key: str, body: Any) -> dict[str, Any]:
    """PUT …/threads/KIND/KEY: the whole thread (pending messages it does not answer are kept)."""
    _thread_kind(plugin, kind)
    key = _key(key)
    if not isinstance(body, dict) or not isinstance(body.get("messages", []), list):
        raise ValueError("a thread: {title?, sub?, url?, composer?, more?, unread?, messages: [...]}")
    schemes, kinds = _schemes(plugin), thread_kinds(plugin)
    head = head_of(body, schemes)
    msgs = [message_of(x, i, schemes, kinds) for i, x in enumerate(body.get("messages") or [])]
    with _lock:
        v = _view(plugin, "thread", kind, key, create=True)
        dropped = _apply_thread(v, head, msgs)
        ev = _changed(v, {"whole": True})
        _cap(plugin, "thread")
        out = {"count": len(v.data["messages"]), "dropped": dropped, "rev": v.rev}
    _tell(ev)
    return out


def patch_thread(plugin: str, kind: str, key: str, body: Any) -> dict[str, Any]:
    """PATCH …/threads/KIND/KEY: {append, update, remove, title, sub, url, composer, more, unread}: changes only."""
    _thread_kind(plugin, kind)
    key = _key(key)
    if not isinstance(body, dict):
        raise ValueError("changes: {append?, update?, remove?, title?, sub?, url?, composer?, more?, unread?}")
    schemes, kinds = _schemes(plugin), thread_kinds(plugin)
    for k in ("append", "update", "remove"):
        if not isinstance(body.get(k) or [], list):
            raise ValueError(f"{k}: a list")
    head = head_of(body, schemes)
    app = [message_of(x, i, schemes, kinds) for i, x in enumerate(body.get("append") or [])]
    upd = [message_of(x, i, schemes, kinds) for i, x in enumerate(body.get("update") or [])]
    rem = [str(x) for x in body.get("remove") or [] if isinstance(x, (str, int)) and not isinstance(x, bool)]
    with _lock:
        v = _view(plugin, "thread", kind, key, create=True)
        msgs = v.data.setdefault("messages", [])
        change: dict[str, Any] = {**head}
        gone = set(rem) & {m["id"] for m in msgs}
        if gone:
            msgs[:] = [m for m in msgs if m["id"] not in gone]
            change["remove"] = sorted(gone)
        for m in upd:  # (an unknown id: nothing)
            i = next((i for i, x in enumerate(msgs) if x["id"] == m["id"] and not x.get("pending")), None)
            if i is not None and msgs[i] != m:
                msgs[i] = m
                change.setdefault("update", []).append(m)
        for m in app:
            p = next((i for i, x in enumerate(msgs) if x.get("pending") and _matches(m, x)), None)
            i = next((i for i, x in enumerate(msgs) if x["id"] == m["id"] and not x.get("pending")), None)
            if i is not None:  # (appended again: an update in place; a pending one it answers goes)
                if p is not None:
                    change.setdefault("remove", []).append(msgs[p]["id"])
                    msgs.pop(p)
                    i = next(i for i, x in enumerate(msgs) if x["id"] == m["id"] and not x.get("pending"))
                if msgs[i] != m:
                    msgs[i] = m
                    change.setdefault("update", []).append(m)
            elif p is not None:  # the real one of a pending message: in its place
                change.setdefault("replace", {})[msgs[p]["id"]] = m
                msgs[p] = m
            else:
                pend = next((i for i, x in enumerate(msgs) if x.get("pending")), len(msgs))
                msgs.insert(pend, m)  # (before the pending ones: they are the newest)
                change.setdefault("append", []).append(m)
        over = len([m for m in msgs if not m.get("pending")]) - THREAD_MAX
        if over > 0:
            old = [m["id"] for m in msgs if not m.get("pending")][:over]
            msgs[:] = [m for m in msgs if m.get("pending") or m["id"] not in set(old)]
            change.setdefault("remove", []).extend(old)
        v.data.update(head)
        v.updated, v.error = time.time(), ""
        if not change:  # nothing new
            return {"count": len(msgs), "rev": v.rev}
        ev = _changed(v, change)
        _cap(plugin, "thread")
        out = {"count": len(msgs), "rev": v.rev}
    _tell(ev)
    return out


def _key(key: str) -> str:
    if not isinstance(key, str) or not 0 < len(key) <= KEY_MAX:
        raise ValueError(f"key: 1–{KEY_MAX} characters")
    return key


# ---------------------------------------------------------------------- threads: the page's
def get_thread(plugin: str, kind: str, key: str, fresh: bool = False) -> dict[str, Any]:
    """GET …/threads/KIND/KEY: what the store has (at once); asks for it in the background when it has none, or an
    older one than STALE s (or fresh: Reload)."""
    t = _thread_kind(plugin, kind)
    key = _key(key)
    with _lock:
        v = _view(plugin, "thread", kind, key, create=True)
        v.opened = time.time()
        e = _index.setdefault(plugin, {}).get(("thread", kind, key))
        if e:
            e["opened"] = v.opened
            _index_dirty.add(plugin)
        _kick(v, t.get("service"), fresh)
        return v.public()


def _kick(v: View, svc_id: str | None, fresh: bool) -> None:
    """(under the lock) Ask the service (or [cli]) for the view in the background, unless it is fresh enough."""
    now = time.time()
    if v.fetching or (not fresh and now - max(v.updated, v.asked) < STALE):
        return
    svc, why = _service(v.plugin, svc_id)
    m = _manifest(v.plugin)
    if svc is None and (svc_id or not m.get("cli")):  # (a service it should ask is down; or nothing to ask)
        if why and not v.updated:
            v.error = why
        return
    if svc is None and v.id in _cli_running:
        return
    v.fetching, v.asked = True, now
    if svc is None:
        _cli_running.add(v.id)
    threading.Thread(target=_fetch, args=(v, svc), name=f"fetch {v.plugin} {v.kind}", daemon=True).start()


def _fetch(v: View, svc) -> None:
    from orbit import plugins
    err, got = "", None
    try:
        if svc is not None:
            q = {"kind": v.kind, "key": v.key} if v.what == "thread" else {"id": v.key}
            got = _ask(svc, f"/orbit/{v.what}?" + urllib.parse.urlencode(q), v.plugin)
        else:
            args = ["thread", v.kind, v.key] if v.what == "thread" else ["doc", v.key]
            try:
                r = plugins.run_cli(v.plugin, args, timeout=CLI_TIMEOUT)
            except (KeyError, ValueError, OSError) as e:
                r = {"code": 127, "stdout": "", "stderr": str(e)}
            if r["code"] != 0:
                tail = (r["stderr"] or r["stdout"]).strip().splitlines()[-1:] or [""]
                raise ValueError(f"[cli] {' '.join(args)} exited {r['code']}" + (f": {tail[0][:300]}" if tail[0] else ""))
            got = json.loads(r["stdout"] or "null")
        if not isinstance(got, dict):
            raise ValueError(f"its answer is not a {v.what} (a JSON object)")
        if v.what == "thread":
            schemes, kinds = _schemes(v.plugin), thread_kinds(v.plugin)
            head = head_of(got, schemes)
            msgs = [message_of(x, i, schemes, kinds) for i, x in enumerate(got.get("messages") or [])]
        else:
            doc = doc_of(got)
    except (ValueError, OSError, KeyError) as e:
        err = str(e)
    ev = None
    with _lock:
        v.fetching = False
        _cli_running.discard(v.id)
        if _live.get(v.id) is not v:
            return
        if err:
            v.error = err
            ev = _changed(v, {"error": err}) if v.what == "thread" else _changed(v, {})
        elif v.what == "thread":
            _apply_thread(v, head, msgs)
            ev = _changed(v, {"whole": True})
        else:
            v.data, v.updated, v.error = doc, time.time(), ""
            ev = _changed(v, {})
        _cap(v.plugin, v.what)
    if ev:
        _tell(ev)


def _ask(svc, path: str, plugin: str, body: Any = None, timeout: float = FETCH_TIMEOUT) -> Any:
    """A request to the plugin's service (with its token): its JSON answer. ValueError says why not."""
    req = urllib.request.Request(f"http://127.0.0.1:{svc.port}{path}", method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Orbit-Plugin-Token": _token(plugin), "Accept": "application/json",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(BODY_MAX * 4)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = str(json.loads(e.read(4000) or b"{}").get("error") or "")[:300]
        except (ValueError, AttributeError, OSError):
            pass
        raise ValueError(f"{plugin}.{svc.id} answered {e.code}" + (f": {detail}" if detail else "")) from None
    except TimeoutError:
        raise ValueError(f"{plugin}.{svc.id}: no answer within {timeout:g} s") from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise ValueError(f"{plugin}.{svc.id}: no answer within {timeout:g} s") from None
        raise ValueError(f"{plugin}.{svc.id} is not reachable ({e.reason})") from None
    try:
        return json.loads(raw or b"null")
    except ValueError:
        raise ValueError(f"{plugin}.{svc.id}: its answer is not JSON") from None


def older(plugin: str, kind: str, key: str, before: str) -> dict[str, Any]:
    """GET …?before=ID: the messages before that one, from the service (never kept: they stay in the page)."""
    t = _thread_kind(plugin, kind)
    key = _key(key)
    svc, why = _service(plugin, t.get("service"))
    if svc is None:
        raise ValueError(why or f"{plugin}'s {kind} has no service to ask for older messages")
    got = _ask(svc, "/orbit/thread?" + urllib.parse.urlencode({"kind": kind, "key": key, "before": before}), plugin)
    if not isinstance(got, dict) or not isinstance(got.get("messages", []), list):
        raise ValueError(f"{plugin}.{svc.id}: its answer is not a thread")
    schemes, kinds = _schemes(plugin), thread_kinds(plugin)
    out = []
    for i, x in enumerate(got.get("messages") or []):
        try:
            out.append(message_of(x, i, schemes, kinds))
        except ValueError:
            continue
    return {"messages": out, "more": got.get("more") is True}


# ---------------------------------------------------------------------- sending
def send(plugin: str, kind: str, key: str, markdown: Any, files: Any, client_id: Any) -> dict[str, Any]:
    """POST …/send (this machine's page): a pending message at once, then the service's /orbit/send (or [cli] send) in
    the background. The same client_id again (Retry) sends that message again."""
    t = _thread_kind(plugin, kind)
    key = _key(key)
    if not isinstance(client_id, str) or not re.fullmatch(CID_RE, client_id):
        raise ValueError("client_id: 1–100 letters, digits, - and _")
    md = _str(markdown, "markdown", MSG_MAX, "")
    if not isinstance(files, list) or len(files) > 20:
        raise ValueError("files: a list of {name, path} (20 at most)")
    fl = []
    for k, f in enumerate(files):
        p = f.get("path") if isinstance(f, dict) else None
        if not isinstance(p, str) or not os.path.isabs(p) or not os.path.isfile(p):
            raise ValueError(f"files[{k}]: {{name, path}}: a file on this machine (uploaded)")
        fl.append({"name": _str(f.get("name"), f"files[{k}].name", 300, "") or os.path.basename(p), "path": p})
    with _lock:
        v = _view(plugin, "thread", kind, key, create=True)
        msgs = v.data.setdefault("messages", [])
        p = next((x for x in msgs if x.get("pending") and x.get("client_id") == client_id), None)
        if p is not None:  # Retry
            if p["pending"] != "failed":
                return {"message": {k: x for k, x in p.items() if not k.startswith("_")}}
            p.update(pending="sending", error="")  # (the same message again: what it was sent with)
            change = {"update": [p]}
        else:
            if not md.strip() and not fl:
                raise ValueError("nothing to send")
            p = {"id": "pending:" + client_id, "client_id": client_id, "author": {"name": "", "me": True},
                 "time": time.time(), "markdown": md, "pending": "sending",
                 **({"files": [{"name": f["name"]} for f in fl]} if fl else {}), "_send": {"markdown": md, "files": fl}}
            msgs.append(p)
            change = {"append": [p]}
        ev = _changed(v, _public_change(change))
        mine = {k: x for k, x in p.items() if not k.startswith("_")}
    _tell(ev)
    threading.Thread(target=_deliver, args=(plugin, kind, key, client_id, t.get("service")), daemon=True,
                     name=f"send {plugin} {kind}").start()
    return {"message": mine}


def _public_change(change: dict) -> dict:
    strip = lambda m: {k: x for k, x in m.items() if not k.startswith("_")}
    out = dict(change)
    for k in ("append", "update"):
        if k in out:
            out[k] = [strip(m) for m in out[k]]
    if "replace" in out:
        out["replace"] = {k: strip(m) for k, m in out["replace"].items()}
    return out


def _deliver(plugin: str, kind: str, key: str, cid: str, svc_id: str | None) -> None:
    from orbit import plugins
    with _lock:
        v = _view(plugin, "thread", kind, key)
        p = v and next((x for x in v.data.get("messages", []) if x.get("pending") and x.get("client_id") == cid), None)
        if not p:
            return
        payload = {"kind": kind, "key": key, "markdown": p["_send"]["markdown"], "files": p["_send"]["files"],
                   "client_id": cid}
    err, real = "", ""
    try:
        svc, why = _service(plugin, svc_id)
        if svc is not None:
            got = _ask(svc, "/orbit/send", plugin, body=payload, timeout=SEND_TIMEOUT)
        elif svc_id:
            raise ValueError(why)
        elif _manifest(plugin).get("cli"):
            r = plugins.run_cli(plugin, ["send"], stdin=json.dumps(payload), timeout=CLI_TIMEOUT)
            if r["code"] != 0:
                tail = (r["stderr"] or r["stdout"]).strip().splitlines()[-1:] or [""]
                raise ValueError(f"[cli] send exited {r['code']}" + (f": {tail[0][:300]}" if tail[0] else ""))
            line = (r["stdout"].strip().splitlines() or ["null"])[0]
            try:
                got = json.loads(line)
            except ValueError:
                got = None
        else:
            raise ValueError(f"{plugin} has no service or [cli] to send with")
        if isinstance(got, dict) and isinstance(got.get("id"), (str, int)) and not isinstance(got.get("id"), bool):
            real = str(got["id"])
    except (ValueError, OSError, KeyError) as e:
        err = str(e) or "not sent"
    ev = None
    with _lock:
        v = _view(plugin, "thread", kind, key)
        msgs = v.data.get("messages", []) if v else []
        i = next((i for i, x in enumerate(msgs) if x.get("pending") and x.get("client_id") == cid), None)
        if i is None:  # (the real one came first)
            return
        p = msgs[i]
        if err:
            p.update(pending="failed", error=err[:300])
            ev = _changed(v, _public_change({"update": [p]}))
        elif real and any(x["id"] == real and not x.get("pending") for x in msgs):  # (pushed before it answered)
            msgs.pop(i)
            ev = _changed(v, {"remove": [p["id"]]})
        else:
            p["pending"], p["_id"] = "sent", real or None
            ev = _changed(v, _public_change({"update": [p]}))
    if ev:
        _tell(ev)


def discard(plugin: str, kind: str, key: str, client_id: Any) -> dict[str, Any]:
    """POST …/discard (Edit of a message not sent): the pending message goes."""
    _thread_kind(plugin, kind)
    with _lock:
        v = _view(plugin, "thread", kind, _key(key))
        msgs = v.data.get("messages", []) if v else []
        i = next((i for i, x in enumerate(msgs) if x.get("pending") and x.get("client_id") == client_id), None)
        if i is None or msgs[i]["pending"] == "sending":
            return {"ok": False}
        p = msgs.pop(i)
        ev = _changed(v, {"remove": [p["id"]]})
    _tell(ev)
    return {"ok": True}


# ---------------------------------------------------------------------- docs
def doc_of(x: Any) -> dict[str, Any]:
    if not isinstance(x, dict):
        raise ValueError("a doc: {title?, markdown, rev?}")
    out = {"title": " ".join(_str(x.get("title"), "title", 300, "").split()),
           "markdown": _str(x.get("markdown"), "markdown", DOC_MAX, "")}
    if x.get("rev") is not None:
        if isinstance(x["rev"], bool) or not isinstance(x["rev"], int):
            raise ValueError("rev: a whole number (yours: an older one than Orbit has is refused)")
        out["version"] = x["rev"]
    return out


class Stale(ValueError):
    pass


def put_doc(plugin: str, doc: str, body: Any) -> dict[str, Any]:
    """PUT …/docs/ID {title, markdown, rev?}: the doc (a rev older than the one kept is refused: Stale)."""
    if not _has_docs(plugin):
        raise ValueError(f"{plugin} declares no doc view (view = \"doc\" on a [[ui.tab]], [[ui.sidebar]] or [[ui.panel]])")
    doc = _key(doc)
    d = doc_of(body)
    with _lock:
        v = _view(plugin, "doc", "", doc, create=True)
        have = v.data.get("version")
        if d.get("version") is not None and have is not None and d["version"] < have:
            raise Stale(f"rev {d['version']} is older than the doc's ({have})")
        if v.updated and {k: v.data.get(k) for k in d} == d and set(v.data) == set(d):
            return {"rev": v.rev}
        v.data, v.updated, v.error = d, time.time(), ""
        ev = _changed(v, {})
        _cap(plugin, "doc")
        out = {"rev": v.rev}
    _tell(ev)
    return out


def get_doc(plugin: str, doc: str, fresh: bool = False) -> dict[str, Any]:
    if not _has_docs(plugin):
        raise ValueError(f"{plugin} declares no doc view")
    doc = _key(doc)
    with _lock:
        v = _view(plugin, "doc", "", doc, create=True)
        v.opened = time.time()
        _kick(v, _doc_service(plugin, doc), fresh)
        return v.public()


# ---------------------------------------------------------------------- the svc: proxy
class ProxyError(Exception):
    def __init__(self, text: str, code: int):
        super().__init__(text)
        self.code = code


def proxy_target(plugin: str, raw: str, query: str) -> tuple[int, str, str, threading.BoundedSemaphore]:
    """Where /api/plugins/NAME/svc/PATH goes: (port, token, path?query, the plugin's semaphore). ProxyError: why not.
    Only under a `files` prefix one of its services declares (that service), never /orbit/, a plain path."""
    m = _manifest(plugin)
    if not svc_path_ok(raw) or (query and (len(query) > 2000 or not svc_path_ok("/?" + query))):
        raise ProxyError("not a plain path", 400)
    low = raw.lower()
    if low.startswith("/orbit/") or low == "/orbit":
        raise ProxyError("/orbit/ is Orbit's own way to the service", 403)
    sid = next((s for s, spec in sorted(m["service"].items()) if spec.get("files") and raw.startswith(spec["files"])), None)
    if sid is None:
        have = [spec["files"] for spec in m["service"].values() if spec.get("files")]
        raise ProxyError(f"{plugin} serves no files there" + (f" (only under {', '.join(have)})" if have else
                         " (a service declares files = \"/files/\")"), 404)
    svc, why = _service(plugin, sid)
    if svc is None:
        raise ProxyError(why, 503)
    with _lock:
        sem = _proxy_sem.setdefault(plugin, threading.BoundedSemaphore(PROXY_AT_ONCE))
    return svc.port, _token(plugin), raw + ("?" + query if query else ""), sem


IMAGE_MAGIC = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF87a", "image/gif"),
               (b"GIF89a", "image/gif"))


def inert_type(head: bytes | None, upstream: str, dest: str) -> tuple[str, bool]:
    """(the Content-Type to serve, whether as an attachment): png, jpeg, gif, webp by their bytes; SVG only as an
    <img> (Sec-Fetch-Dest: image); anything else application/octet-stream, an attachment. The service's own type is
    never passed through. head None (a HEAD): its declared image type, nothing else."""
    up = (upstream or "").split(";")[0].strip().lower()
    if head is None:
        return (up, False) if up in ("image/png", "image/jpeg", "image/gif", "image/webp") else ("application/octet-stream", True)
    for magic, t in IMAGE_MAGIC:
        if head.startswith(magic):
            return t, False
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp", False
    svg = up == "image/svg+xml" or head.lstrip()[:5].lower() in (b"<svg ", b"<svg>", b"<?xml") and b"<svg" in head.lower()
    if svg and dest == "image":
        return "image/svg+xml", False
    return "application/octet-stream", True
