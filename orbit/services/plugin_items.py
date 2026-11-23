"""Item sources (plugin API 1.3): lists a plugin keeps in Orbit, for ⌘O and Resume another session.

A plugin declares `[[ui.palette]]` sources (plugins.palette_of). Their items are pushed by its service
(PUT / POST /api/plugins/NAME/items/SOURCE) or come from a command Orbit runs in the background (`refresh`); they are
kept in memory and in `<state>/plugin-items/<plugin>.<source>.json`. The page fetches a source once when its tab
opens and filters it itself: nothing runs per keystroke. A live search (`search`) goes to the plugin's running
service over HTTP (search(), below), never to a new process.

`conversations` is a source every plugin with a harness has without declaring it: the conversations of its harness
to resume (desk/external_index.py reads it instead of running `--list-conversations`).
"""

from __future__ import annotations

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

ITEM_MAX = 2048  # bytes of one item (its JSON)
SOURCE_MAX = 10_000  # items in a source (the newest by time are kept)
KEY_MAX = 300
SEARCH_TIMEOUT = 1.5  # s for a service's answer
SEARCH_LIMIT = 30
REFRESH_TIMEOUT = 120.0  # s for a refresh command
MIN_EVERY = 60.0  # s: a refresh runs at most this often
TICK = 5.0  # s between two looks at the refresh sources
SAVE_DELAY = 1.0  # s after the last change a source is written
CONVERSATIONS = "conversations"
FIELDS = ("key", "label", "kind", "detail", "time", "match", "icon", "tag", "url", "cwd", "harness", "data", "unread",
          "group", "open")
ID_RE = r"[a-z0-9][a-z0-9_-]*"

BROADCAST: Callable[[dict], None] | None = None  # Desk.broadcast, set by the server (an open ⌘O redraws)
_lock = threading.Lock()
SOURCES: dict[tuple[str, str], "Source"] = {}
_dirty: set[tuple[str, str]] = set()
_saver: threading.Timer | None = None
_loop: threading.Thread | None = None


class Source:
    def __init__(self, plugin: str, sid: str, decl: dict[str, Any]):
        self.plugin, self.id, self.decl = plugin, sid, decl
        self.items: dict[str, dict[str, Any]] = {}
        self.updated = 0.0  # when its list last changed (0: never pushed nor refreshed)
        self.refreshed = 0.0  # when its refresh command last ran (refresh sources)
        self.error = ""  # its refresh command's last failure, until it succeeds again
        self.running = False  # its refresh command runs (one at a time)

    def sorted(self) -> list[dict[str, Any]]:
        """Newest first (items without a time last, in the order they came)."""
        return sorted(self.items.values(), key=lambda x: -(x.get("time") or 0))

    def public(self, items: bool = True) -> dict[str, Any]:
        d = self.decl
        out = {"plugin": self.plugin, "id": self.id, "title": d.get("title", self.id), "open": d.get("open", "tab"),
               "search": bool(d.get("search")), "refresh": bool(d.get("refresh")), "updated": self.updated or None,
               "count": len(self.items), **({"kind": d["kind"]} if d.get("kind") else {}),
               **({"icon": d["icon"]} if d.get("icon") else {}), **({"error": self.error} if self.error else {})}
        if items:
            out["items"] = self.sorted()
        return out


# ---------------------------------------------------------------------- items
def item_of(x: Any, n: int = 0) -> dict[str, Any]:
    """One item, checked: {key, label, kind?, detail?, time?, match?, icon?, tag?, url?, cwd?, harness?, data?, and
    (2.0, a list view) unread?, group?, open?} (other fields are dropped). ValueError says what is wrong with it."""
    what = f"item {n}"
    if not isinstance(x, dict):
        raise ValueError(f"{what}: an object {{key, label, …}}")
    key = x.get("key")
    if isinstance(key, (int, float)) and not isinstance(key, bool):
        key = str(key)
    if not isinstance(key, str) or not key or len(key) > KEY_MAX:
        raise ValueError(f"{what}: key: a string, 1–{KEY_MAX} characters")
    what = f"item {n} ({key[:60]!r})"
    label = x.get("label")
    if not isinstance(label, str) or not label.strip():
        raise ValueError(f"{what}: label: a string")
    out: dict[str, Any] = {"key": key, "label": label}
    for k in ("kind", "detail", "match", "icon", "tag", "url", "cwd", "harness", "group"):
        v = x.get(k)
        if v is None or v == "":
            continue
        if not isinstance(v, str):
            raise ValueError(f"{what}: {k}: a string")
        out[k] = v
    if "kind" in out and not re.fullmatch(ID_RE, out["kind"]):
        raise ValueError(f"{what}: kind: one of the plugin's [[ui.tab]] kinds")
    if "url" in out and not out["url"].startswith(("https://", "http://")):
        raise ValueError(f"{what}: url: https:// or http://")
    t = x.get("time")
    if t is not None:
        if isinstance(t, bool) or not isinstance(t, (int, float)):
            raise ValueError(f"{what}: time: a number (epoch seconds)")
        out["time"] = float(t) / 1000 if t > 1e11 else float(t)  # (milliseconds taken too)
    if "data" in x and x["data"] is not None:
        out["data"] = x["data"]
    if x.get("unread") is True:  # (2.0) the list's unread dot
        out["unread"] = True
    o = x.get("open")  # (2.0) what a click opens: {"kind", "key"} (one of the plugin's tabs) or {"url"}
    if o is not None:
        if not isinstance(o, dict):
            raise ValueError(f"{what}: open: {{\"kind\", \"key\"}} or {{\"url\"}}")
        if o.get("url") is not None:
            if not isinstance(o["url"], str) or not o["url"].startswith(("https://", "http://")):
                raise ValueError(f"{what}: open.url: https:// or http://")
            out["open"] = {"url": o["url"]}
        else:
            k, ky = o.get("kind"), o.get("key")
            if not isinstance(k, str) or not re.fullmatch(ID_RE, k) or not isinstance(ky, str) or not 0 < len(ky) <= KEY_MAX:
                raise ValueError(f"{what}: open: {{\"kind\": one of the plugin's [[ui.tab]] kinds, \"key\"}}")
            out["open"] = {"kind": k, "key": ky}
    size = len(json.dumps(out, ensure_ascii=False).encode())
    if size > ITEM_MAX:
        raise ValueError(f"{what}: {size} bytes; an item is {ITEM_MAX} bytes at most")
    return out


def _cap(items: dict[str, dict[str, Any]]) -> int:
    """Keep the SOURCE_MAX newest (by time); how many were dropped."""
    over = len(items) - SOURCE_MAX
    if over <= 0:
        return 0
    for x in sorted(items.values(), key=lambda x: x.get("time") or 0)[:over]:
        items.pop(x["key"], None)
    return over


# ---------------------------------------------------------------------- the store
def _file(plugin: str, sid: str) -> Path | None:
    from orbit import plugins
    return plugins.STATE / "plugin-items" / f"{plugin}.{sid}.json" if plugins.STATE is not None else None


def declared(plugin: str) -> dict[str, dict[str, Any]]:
    """The sources a loaded plugin may write: its [[ui.palette]] ids, and `conversations` when it has a harness."""
    from orbit import plugins
    m = plugins.LOADED.get(plugin)
    if not m:
        return {}
    out = {p["id"]: p for p in m.get("palette") or []}
    if m.get("harness"):
        out.setdefault(CONVERSATIONS, {"id": CONVERSATIONS, "title": "Conversations", "conversations": True})
    return out


def load() -> None:
    """(plugins.load) A source per declared one, its items read back from disk."""
    from orbit import plugins
    with _lock:
        SOURCES.clear()
        _dirty.clear()
    for name in list(plugins.LOADED):
        for sid, decl in declared(name).items():
            src = Source(name, sid, decl)
            f = _file(name, sid)
            try:
                d = json.loads(f.read_text()) if f and f.exists() else {}
            except (OSError, ValueError):
                d = {}
            if isinstance(d, dict):
                for i, x in enumerate(d.get("items") or []):
                    try:
                        it = item_of(x, i)
                    except ValueError:
                        continue
                    src.items[it["key"]] = it
                _cap(src.items)
                src.updated = float(d.get("updated") or 0)
                src.refreshed = float(d.get("refreshed") or 0)
            with _lock:
                SOURCES[(name, sid)] = src


def unload() -> None:
    flush()
    with _lock:
        SOURCES.clear()


def flush() -> None:
    """Write the sources that changed (also before a reload, and when the server stops)."""
    global _saver
    with _lock:
        todo = [SOURCES[k] for k in _dirty if k in SOURCES]
        _dirty.clear()
        if _saver:
            _saver.cancel()
            _saver = None
        snaps = [(s.plugin, s.id, {"items": list(s.items.values()), "updated": s.updated, "refreshed": s.refreshed})
                 for s in todo]
    for plugin, sid, d in snaps:
        f = _file(plugin, sid)
        if f is None or not f.parent.parent.is_dir():  # (its state dir went away: a test's)
            continue
        try:
            f.parent.mkdir(exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False))
            os.replace(tmp, f)
        except OSError as e:
            print(f"orbit: {plugin}: items of {sid} not kept ({e})", flush=True)


def _changed(src: Source) -> None:
    """(under the lock) Write it soon (then _tell, after the lock)."""
    global _saver
    _dirty.add((src.plugin, src.id))
    if _saver is None:
        _saver = threading.Timer(SAVE_DELAY, flush)
        _saver.daemon = True
        _saver.start()


def _tell(src: Source) -> None:
    """The pages hear of it: an open ⌘O on that source redraws (and Resume another session, for conversations)."""
    b = BROADCAST
    if b is None:
        return
    try:
        b({"t": "plugin-items", "plugin": src.plugin, "source": src.id, "updated": src.updated})
        if src.id == CONVERSATIONS:
            b({"t": "external-sessions"})
    except Exception:  # noqa: BLE001 - a page that went away is not the plugin's problem
        pass


def source(plugin: str, sid: str) -> Source:
    """A source the plugin may write (KeyError: no such plugin; ValueError: it did not declare it)."""
    from orbit import plugins
    if plugin not in plugins.LOADED:
        raise KeyError(plugin)
    with _lock:
        src = SOURCES.get((plugin, sid))
    if src is None:
        have = ", ".join(sorted(declared(plugin))) or "none"
        raise ValueError(f"{plugin} declares no item source {sid!r} ([[ui.palette]] id; it has: {have})")
    return src


def put(plugin: str, sid: str, items: Any) -> dict[str, Any]:
    """The whole list (a resync)."""
    src = source(plugin, sid)
    if not isinstance(items, list):
        raise ValueError("items: a list")
    new: dict[str, dict[str, Any]] = {}
    for i, x in enumerate(items):
        it = item_of(x, i)
        new[it["key"]] = it
    dropped = _cap(new)
    with _lock:
        same = src.updated and list(new.items()) == list(src.items.items())
        if not same:  # (a resync that changes nothing is not told: an open ⌘O does not fetch it again)
            src.items = new
            src.updated = time.time()
            _changed(src)
        out = {"count": len(src.items), "dropped": dropped}
    if not same:
        _tell(src)
    return out


def change(plugin: str, sid: str, upsert: Any = None, remove: Any = None) -> dict[str, Any]:
    """Changes only: items added or replaced (by key), keys removed."""
    src = source(plugin, sid)
    upsert = [] if upsert is None else upsert
    remove = [] if remove is None else remove
    if not isinstance(upsert, list) or not isinstance(remove, list):
        raise ValueError("upsert: a list of items; remove: a list of keys")
    its = [item_of(x, i) for i, x in enumerate(upsert)]
    keys = [str(k) for k in remove if isinstance(k, (str, int)) and not isinstance(k, bool)]
    with _lock:
        same = bool(src.updated) and all(k not in src.items for k in keys) and all(src.items.get(it["key"]) == it for it in its)
        if not same:
            for k in keys:
                src.items.pop(k, None)
            for it in its:
                src.items[it["key"]] = it
            dropped = _cap(src.items)
            src.updated = time.time()
            _changed(src)
        else:
            dropped = 0
        out = {"count": len(src.items), "dropped": dropped}
    if not same:
        _tell(src)
    return out


def palette(plugin: str = "", sid: str = "", fresh: bool = False) -> list[dict[str, Any]]:
    """GET /api/palette: the [[ui.palette]] sources (not `conversations`), with their items; a refresh source whose
    list is older than its `every` (or, fresh: the user asked, a pull on the phone's list) is refreshed in the
    background (the caller never waits; one run at a time)."""
    with _lock:
        srcs = [s for (p, i), s in sorted(SOURCES.items()) if not s.decl.get("conversations")
                and (not plugin or p == plugin) and (not sid or i == sid)]
    for s in srcs:
        kick(s, force=fresh)
    with _lock:
        return [s.public() for s in srcs]


def conversations() -> dict[str, list[dict[str, Any]]]:
    """Pushed conversations by plugin (only the plugins that pushed some list: the others' harnesses are listed by
    `--list-conversations`)."""
    with _lock:
        return {p: s.sorted() for (p, i), s in SOURCES.items() if i == CONVERSATIONS and s.updated}


def errors(plugin: str) -> dict[str, str]:
    """Its sources' refresh failures (until they run again without one): /api/plugins palette_errors."""
    with _lock:
        return {i: s.error for (p, i), s in sorted(SOURCES.items()) if p == plugin and s.error}


# ---------------------------------------------------------------------- refresh by command
def _log(plugin: str, text: str) -> None:
    from orbit import plugins
    if plugins.STATE is None:
        return
    try:
        f = plugins.STATE / "logs" / "plugins" / f"{plugin}.palette.log"
        f.parent.mkdir(parents=True, exist_ok=True)
        if f.exists() and f.stat().st_size > 1_000_000:
            os.replace(f, f.with_suffix(".log.1"))
        with open(f, "a") as fh:
            fh.write(f"--- orbit: {time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
    except OSError:
        pass


def kick(src: Source, force: bool = False) -> bool:
    """Run a refresh source's command in the background when its list is older than its `every` (or force); one run
    at a time. Whether one started."""
    r = src.decl.get("refresh")
    if not r:
        return False
    with _lock:
        if src.running or (not force and time.time() - src.refreshed < r["every"]):
            return False
        if SOURCES.get((src.plugin, src.id)) is not src:  # (a reload replaced it)
            return False
        src.running = True
    threading.Thread(target=_refresh, args=(src,), name=f"refresh {src.plugin}.{src.id}", daemon=True).start()
    return True


def _refresh(src: Source) -> None:
    from orbit import plugins
    r = src.decl["refresh"]
    try:
        try:
            out = plugins.run_cli(src.plugin, r["args"], timeout=REFRESH_TIMEOUT)
        except (KeyError, ValueError, OSError) as e:
            out = {"code": 127, "stdout": "", "stderr": str(e)}
        err, new, bad, fresh = "", {}, [], False
        if out["code"] != 0:
            tail = (out["stderr"] or out["stdout"]).strip().splitlines()[-1:] or [""]
            err = f"exited {out['code']}" + (f": {tail[0][:300]}" if tail[0] else "")
        else:
            for n, line in enumerate(out["stdout"].splitlines()):
                if not line.strip():
                    continue
                try:
                    it = item_of(json.loads(line), n)
                except ValueError as e:
                    bad.append(str(e))
                    continue
                new[it["key"]] = it
            _cap(new)
        with _lock:
            src.refreshed = time.time()
            if err:  # (the list it had stays)
                src.error = err
                _changed(src)  # (its refreshed time is kept: not again before its `every`)
            else:
                src.error = f"{len(bad)} lines skipped (the first: {bad[0][:200]})" if bad else ""
                fresh = list(new.items()) != list(src.items.items()) or not src.updated
                if fresh:
                    src.items, src.updated = new, time.time()
                _changed(src)  # (its refreshed time is kept)
        if not err and fresh:
            _tell(src)
        if err or bad:
            _log(src.plugin, f"[{src.id}] refresh {' '.join(r['args'])}: {src.error}")
    finally:
        with _lock:
            src.running = False


def start() -> None:
    """The refresh loop (once per process): every refresh source runs when its list is older than its `every`."""
    global _loop
    with _lock:
        if _loop and _loop.is_alive():
            return
        _loop = threading.Thread(target=_tick, name="plugin item refresh", daemon=True)
        _loop.start()


def _tick() -> None:
    while True:
        with _lock:
            srcs = [s for s in SOURCES.values() if s.decl.get("refresh")]
        for s in srcs:
            kick(s)
        time.sleep(TICK)


# ---------------------------------------------------------------------- live search through the service
class SearchError(Exception):
    def __init__(self, text: str, code: int):
        super().__init__(text)
        self.code = code


def search(plugin: str, sid: str, q: str, limit: int = SEARCH_LIMIT) -> list[dict[str, Any]]:
    """Ask the plugin's running service: GET http://127.0.0.1:$ORBIT_SERVICE_PORT/orbit/search?source=&q=&limit=,
    with its token, SEARCH_TIMEOUT s. Its {"items": [...]} checked (bad ones dropped). SearchError says why not."""
    from orbit import plugins
    from orbit.services import plugin_services
    src = source(plugin, sid)
    svc_id = src.decl.get("search")
    if not svc_id:
        raise SearchError(f"{plugin}'s {sid} has no live search", 404)
    svc = next((s for s in plugin_services.of(plugin) if s.id == svc_id), None)
    if svc is None or not svc.port or svc.state != "running":
        raise SearchError(f"{plugin}.{svc_id} is not running" + (f" ({svc.state})" if svc else ""), 503)
    token = next((t for t, n in plugins.TOKENS.items() if n == plugin), "")
    qs = urllib.parse.urlencode({"source": sid, "q": q, "limit": str(limit)})
    req = urllib.request.Request(f"http://127.0.0.1:{svc.port}/orbit/search?{qs}",
                                 headers={"X-Orbit-Plugin-Token": token, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT) as r:
            d = json.loads(r.read(4_000_000) or b"{}")
    except urllib.error.HTTPError as e:
        raise SearchError(f"{plugin}.{svc_id} answered {e.code}", 502) from None
    except TimeoutError:
        raise SearchError(f"{plugin}.{svc_id}: no answer within {SEARCH_TIMEOUT:g} s", 504) from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise SearchError(f"{plugin}.{svc_id}: no answer within {SEARCH_TIMEOUT:g} s", 504) from None
        raise SearchError(f"{plugin}.{svc_id} is not reachable ({e.reason})", 503) from None
    except (OSError, ValueError) as e:
        raise SearchError(f"{plugin}.{svc_id}: {e}", 502) from None
    raw = d.get("items") if isinstance(d, dict) else None
    if not isinstance(raw, list):
        raise SearchError(f"{plugin}.{svc_id}: its answer is not {{\"items\": [...]}}", 502)
    out = []
    for i, x in enumerate(raw[:limit]):
        try:
            out.append(item_of(x, i))
        except ValueError:
            continue
    return out
