"""Other machines' sessions as this server's own.

For each remote in <state>/remotes.json a thread keeps that server's open sessions and follows its /api/events. Its
sessions join this server's list as `NAME~ID` (with "remote": NAME), in whichever of this server's groups the user put
them (kept here, in <state>/remote-placement.json: its group); their events (cards,
session changes, closes) are rebroadcast here with those ids. A request for `/api/sessions/NAME~ID/...` goes to that
server as `/api/sessions/ID/...` and its answer comes back with the ids rewritten. So a client of this server (the
app, a phone, `orbit-agent`) sees and drives every machine's sessions through one URL.

Their files too: a path on another machine is written `/@NAME/<its path>` here (a session's folder is
`/@gpu-box/home/me/proj`), so the tree, file tabs, review, file links and images work unchanged; a file request that
carries such a path is done by that server (`local_paths` strips the prefix going out, `our_paths` adds it coming
back). Their ⌘J shells likewise: shell `NAME~ID` is shell ID over there.

One hop only: a server lists only its own sessions to another (`/api/sessions?local=1`, and events of `~` ids are not
relayed), so two servers that reach each other do not loop.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from orbit.desk import cardpatch
from orbit.machines import remote


SEP = "~"
GROUP = "r~"  # earlier pages named a machine as a group (r~NAME); still taken when a session is made
PFX = "/@"
PATH_KEYS = {"path", "root", "parent", "cwd", "repo", "dirs", "new", "old", "paths"}


def machine_of(p: str) -> tuple[str, str] | None:
    """'/@gpu-box/home/me' → ('gpu-box', '/home/me'); a path of this machine → None."""
    if not isinstance(p, str) or not p.startswith(PFX):
        return None
    name, _, rest = p[len(PFX):].partition("/")
    return (name, "/" + rest) if remote.NAME_RE.match(name) else None


def machine_in(v) -> str:
    """The other machine a request's values name (query or body), or ""."""
    if isinstance(v, str):
        m = machine_of(v)
        return m[0] if m else ""
    if isinstance(v, dict):
        v = list(v.values())
    if isinstance(v, list):
        for x in v:
            n = machine_in(x)
            if n:
                return n
    return ""


def local_paths(name: str, v):
    """Values as that machine writes them: /@name/x → /x."""
    if isinstance(v, str):
        m = machine_of(v)
        return m[1] if m and m[0] == name else v
    if isinstance(v, list):
        return [local_paths(name, x) for x in v]
    if isinstance(v, dict):
        return {k: local_paths(name, x) for k, x in v.items()}
    return v


def ours(name: str, p: str) -> str:
    return PFX + name + p if isinstance(p, str) and p.startswith("/") and not p.startswith(PFX) else p


def our_paths(name: str, v, key: str = ""):
    """An answer of that machine's file API, its absolute paths written as ours (path-like keys, and dict keys that
    are paths, as /api/stats answers); file contents are left alone."""
    if isinstance(v, str):
        return ours(name, v) if key in PATH_KEYS else v
    if isinstance(v, list):
        return [our_paths(name, x, key) for x in v]
    if isinstance(v, dict):
        return {ours(name, k): our_paths(name, x, k) for k, x in v.items()}
    return v


def origin() -> str:
    """This machine as it marks its own values kept on another machine (a side tab of a session there that shows one
    of this machine's sessions or files): written back as this machine's when they come home."""
    import re
    import socket
    return "o." + (re.sub(r"[^\w.-]", "", socket.gethostname().split(".")[0]) or "here")


def join(name: str, sid: str) -> str:
    return f"{name}{SEP}{sid}"


def split(sid: str) -> tuple[str, str] | None:
    """'gpu-box~abc' → ('gpu-box', 'abc'); a local id → None."""
    name, sep, rest = sid.partition(SEP)
    return (name, rest) if sep and rest and remote.NAME_RE.match(name) else None


class Federation:
    def __init__(self, desk, poll: float = 5.0):
        self.desk = desk
        self.state = desk.state_dir
        self.poll = poll
        self.lock = threading.Lock()
        self.sessions: dict[str, dict[str, dict]] = {}  # remote → {its id: session as shown here}
        self.errors: dict[str, str] = {}  # remote → why it is unreachable ("" when it answers)
        self.watchers: dict[str, threading.Thread] = {}
        self.cards: dict[tuple, dict] = {}  # (remote, its sid, card id) → the card as it last came (its patches apply to it)
        self.stopped = threading.Event()
        self.placement_path = self.state / "remote-placement.json"
        try:  # NAME~ID → {"group"}: where the user put another machine's session, here
            self.placement: dict[str, dict] = json.loads(self.placement_path.read_text())
        except (OSError, ValueError):
            self.placement = {}

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        threading.Thread(target=self._supervise, daemon=True, name="federation").start()

    def stop(self) -> None:
        self.stopped.set()

    def kick(self) -> None:
        """A remote was added or removed: pick it up now, not at the next poll."""
        self._sync()

    def _supervise(self) -> None:
        while not self.stopped.is_set():
            self._sync()
            self.stopped.wait(self.poll)

    def _sync(self) -> None:
        names = set(remote.load(self.state))
        with self.lock:
            for name in names:
                t = self.watchers.get(name)
                if t is None or not t.is_alive():
                    t = threading.Thread(target=self._watch, args=(name,), daemon=True, name=f"remote-{name}")
                    self.watchers[name] = t
                    t.start()
            gone = [n for n in self.sessions if n not in names]
        for name in gone:
            self._drop(name)

    def _info(self, name: str) -> dict | None:
        return remote.load(self.state).get(name)

    # ------------------------------------------------------------------ one remote
    def _watch(self, name: str) -> None:
        backoff = 2.0
        while not self.stopped.is_set():
            info = self._info(name)
            if not info:
                return self._drop(name)
            try:
                d = remote.call(info, "GET", "/api/sessions?local=1", timeout=8)
                self._replace(name, d.get("sessions", []))
                self.errors[name] = ""
                backoff = 2.0
                self._follow(name, info)  # returns when the stream ends
            except (remote.RemoteError, OSError, ValueError) as e:
                self._offline(name, str(e))
            if self.stopped.wait(backoff):
                return
            backoff = min(backoff * 2, 30.0)

    def _follow(self, name: str, info: dict) -> None:
        req = urllib.request.Request(info["url"] + "/api/events?delta=1", headers={"Authorization": f"Bearer {info['token']}"})
        with urllib.request.urlopen(req, timeout=40) as r:  # the server pings every 15 s
            for raw in r:
                if self.stopped.is_set() or name not in remote.load(self.state):
                    return
                if not raw.startswith(b"data: "):
                    continue
                try:
                    ev = json.loads(raw[6:])
                except ValueError:
                    continue
                self._event(name, ev)
        raise OSError("the event stream ended")

    def _event(self, name: str, ev: dict) -> None:
        t = ev.get("t")
        if t == "hello":
            self._replace(name, [s for s in ev.get("sessions", []) if SEP not in s.get("id", "")])
        elif t == "session":
            s = ev.get("session") or {}
            if SEP in s.get("id", "") or s.get("closed"):
                return
            shown = self.session(name, s)
            with self.lock:
                self.sessions.setdefault(name, {})[s["id"]] = shown
            self.desk.broadcast({"t": "session", "session": shown})
        elif t == "card":
            c = ev.get("card") or {}
            if SEP in str(c.get("sid", "")):
                return
            self._keep_card(name, c)
            self.desk.broadcast({"t": "card", "card": self.card(name, c)})
        elif t == "cardp":  # a patch (it follows with ?delta=1): applied to the card as it last came, else fetched
            sid = str(ev.get("sid", ""))
            if SEP in sid:
                return
            key = (name, sid, ev.get("id"))
            with self.lock:
                have = self.cards.get(key)
            if have is not None and have.get("rev") == ev.get("base"):
                c = {**cardpatch.apply(have, ev.get("ops") or []), "rev": ev.get("rev")}
                self._keep_card(name, c)
                self.desk.broadcast({"t": "card", "card": self.card(name, c)})
            elif have is None or (have.get("rev") or 0) < (ev.get("rev") or 0):
                threading.Thread(target=self._fetch_card, args=(name, sid, ev.get("id")), daemon=True).start()
        elif t == "live-hangup":  # its agent was asked to end the voice call: the call is this server's (with that session)
            sid = str(ev.get("sid", ""))
            live = getattr(self.desk, "live", None)
            c = live.call if live is not None else None
            if sid and c is not None and not c.ended and c.sid == join(name, sid):
                threading.Thread(target=c.hang_up, daemon=True).start()
        elif t == "live-op":  # its agent shapes the voice (agentvoice.py): the call is this server's, with that session
            sid = str(ev.get("sid", ""))
            live = getattr(self.desk, "live", None)
            c = live.call if live is not None else None
            if sid and c is not None and not c.ended and c.sid == join(name, sid) and isinstance(ev.get("b"), dict):
                try:
                    c.voice_op(str(ev.get("op", "")), ev["b"])
                except ValueError:
                    pass
        elif t == "side":  # a side panel changed over there: on every screen here too
            sid = str(ev.get("sid", ""))
            if SEP in sid or not sid:
                return
            self.desk.broadcast({"t": "side", "sid": join(name, sid), "side": self.side_ours(name, ev.get("side"))})
        elif t == "closed":
            sid = str(ev.get("id", ""))
            if SEP in sid:
                return
            with self.lock:
                self.sessions.get(name, {}).pop(sid, None)
            self.desk.broadcast({"t": "closed", "id": join(name, sid), "session": self.session(name, ev.get("session") or {"id": sid})})

    # ------------------------------------------------------------------ another machine's session's side panel
    def side_ours(self, name: str, st: Any) -> Any:
        """Its side panel as written here: paths /@name/…, sessions name~id, its agents' pages through this server."""
        if not isinstance(st, dict) or not isinstance(st.get("items"), list):
            return st
        items, mine = [], origin()
        for x in st["items"]:
            y = our_paths(name, dict(x))
            for k in ("path", "root"):  # this machine's own (marked by side_theirs): as they were
                if isinstance(y.get(k), str) and y[k].startswith(f"{PFX}{mine}/"):
                    y[k] = y[k][len(PFX + mine):]
            if isinstance(y.get("sid"), str) and y["sid"].startswith(mine + SEP):
                y["sid"] = y["sid"][len(mine) + 1:]
            elif isinstance(y.get("sid"), str) and y["sid"] and SEP not in y["sid"]:
                y["sid"] = join(name, y["sid"])
            if isinstance(y.get("url"), str) and y["url"].startswith("/show/"):
                y["url"] = f"/api/remote-show/{name}/{y['url'][6:]}"
            items.append(y)
        return {**st, "items": items}

    def side_theirs(self, name: str, spec: Any) -> Any:
        """A side tab named here, as that machine writes it."""
        if not isinstance(spec, dict):
            return spec
        y, mine = dict(spec), origin()
        for k in ("path", "root"):  # that machine's: as it writes them; this machine's own: marked (a third's: as is)
            if isinstance(y.get(k), str) and y[k].startswith("/"):
                m = machine_of(y[k])
                y[k] = m[1] if m and m[0] == name else y[k] if y[k].startswith(PFX) else f"{PFX}{mine}{y[k]}"
        y = local_paths(name, y)
        if isinstance(y.get("sid"), str) and y["sid"].startswith(name + SEP):
            y["sid"] = y["sid"][len(name) + 1:]
        elif isinstance(y.get("sid"), str) and y["sid"] and SEP not in y["sid"]:  # one of this machine's sessions
            y["sid"] = join(mine, y["sid"])
        pre = f"/api/remote-show/{name}/"
        if isinstance(y.get("url"), str) and y["url"].startswith(pre):
            y["url"] = "/show/" + y["url"][len(pre):]
        return y

    def side(self, name: str, rid: str, body: dict | None = None) -> tuple[int, Any]:
        """GET (body None) or change (POST body) that session's side panel over there."""
        if body is not None:
            body = {**body, **({"spec": self.side_theirs(name, body["spec"])} if "spec" in body else {})}
        st, r = self.call(name, "POST" if body is not None else "GET", f"/api/sessions/{rid}/side", body, rewrite=False)
        return st, (self.side_ours(name, r) if st == 200 else r)

    def raw(self, name: str, path: str, timeout: float = 15) -> tuple[int, bytes]:
        """A file of that server as it is (an agent's page), (status, bytes)."""
        info = self._info(name)
        if not info:
            return 404, b""
        req = urllib.request.Request(info["url"] + path, headers={"Authorization": f"Bearer {info['token']}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read(8_000_000)
        except urllib.error.HTTPError as e:
            return e.code, b""
        except OSError:
            return 502, b""

    CARDS_KEPT = 300

    def _keep_card(self, name: str, c: dict) -> None:
        key = (name, str(c.get("sid", "")), c.get("id"))
        with self.lock:
            self.cards.pop(key, None)
            if c.get("state") not in ("completed", "error", "cancelled"):
                self.cards[key] = c
                while len(self.cards) > self.CARDS_KEPT:
                    self.cards.pop(next(iter(self.cards)))

    def _fetch_card(self, name: str, sid: str, cid: Any) -> None:
        """A card whose patch could not be applied (one was missed): fetched whole from that server."""
        try:
            st, r = self.call(name, "GET", f"/api/sessions/{sid}/cards/{cid}?exact=1", rewrite=False, timeout=15)
        except Exception:  # noqa: BLE001 - the next change tries again
            return
        c = (r or {}).get("card") if st == 200 and isinstance(r, dict) else None
        if c:
            self._keep_card(name, c)
            self.desk.broadcast({"t": "card", "card": self.card(name, c)})

    def _replace(self, name: str, sessions: list[dict]) -> None:
        shown = {s["id"]: self.session(name, s) for s in sessions if not s.get("closed")}
        with self.lock:
            old = self.sessions.get(name, {})
            new_group = name not in self.sessions
            self.sessions[name] = shown
        for sid in old.keys() - shown.keys():
            self.desk.broadcast({"t": "closed", "id": join(name, sid), "session": old[sid]})
        if new_group:
            self.desk.broadcast({"t": "machines", "machines": self.machines()})
        for s in shown.values():
            self.desk.broadcast({"t": "session", "session": s})

    def _offline(self, name: str, why: str) -> None:
        self.errors[name] = why
        with self.lock:
            ss = list(self.sessions.setdefault(name, {}).values())
            for s in ss:
                s.update(state="offline", error=f"{name} is unreachable: {why}")
        if ss:
            for s in ss:
                self.desk.broadcast({"t": "session", "session": s})
        else:
            self.desk.broadcast({"t": "machines", "machines": self.machines()})

    def _drop(self, name: str) -> None:
        with self.lock:
            old = self.sessions.pop(name, {})
            self.errors.pop(name, None)
            self.watchers.pop(name, None)
        for sid, s in old.items():
            self.desk.broadcast({"t": "closed", "id": join(name, sid), "session": s})
        self.desk.broadcast({"t": "machines", "machines": self.machines()})

    # ------------------------------------------------------------------ shapes
    def session(self, name: str, s: dict) -> dict:
        out = dict(s)
        out["id"] = join(name, str(s.get("id", "")))
        out["remote"] = name
        place = self.placement.get(out["id"], {})
        groups = [g["id"] for g in self.desk.groups]
        out["group"] = place.get("group") if place.get("group") in groups else (groups[0] if groups else "")
        if isinstance(out.get("cwd"), str):
            out["cwd"] = ours(name, out["cwd"])
        return out

    def place(self, sid: str, group: str | None = None) -> dict | None:
        """Put another machine's session in one of this server's groups: kept here, not there."""
        split_ = split(sid)
        if not split_:
            return None
        name, rid = split_
        if group is not None and group not in [g["id"] for g in self.desk.groups]:
            raise ValueError(f"no group {group}")
        with self.lock:
            p = self.placement.setdefault(sid, {})
            if group is not None:
                p["group"] = group
            snap = dict(self.placement)
            cur = self.sessions.get(name, {}).get(rid)
        tmp = self.placement_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(snap, indent=1))
        tmp.replace(self.placement_path)
        if cur is None:
            return None
        shown = self.session(name, {**cur, "id": rid, "cwd": local_paths(name, cur.get("cwd", ""))})
        with self.lock:
            self.sessions.setdefault(name, {})[rid] = shown
        self.desk.broadcast({"t": "session", "session": shown})
        return shown

    @staticmethod
    def card(name: str, c: dict) -> dict:
        out = dict(c)
        if "sid" in out:
            out["sid"] = join(name, str(out["sid"]))
        return out

    def rewrite(self, name: str, v: Any) -> Any:
        """An answer of that server, its session ids made ours (sessions, cards, lists of them)."""
        if isinstance(v, list):
            return [self.rewrite(name, x) for x in v]
        if isinstance(v, dict):
            if "cwd" in v and "harness" in v and isinstance(v.get("id"), str):
                return self.session(name, v)
            out = {k: self.rewrite(name, x) for k, x in v.items()}
            if isinstance(out.get("sid"), str) and SEP not in out["sid"]:
                out["sid"] = join(name, out["sid"])
            return out
        return v

    # ------------------------------------------------------------------ what this server shows
    def list(self) -> list[dict]:
        with self.lock:
            return [dict(s) for name in sorted(self.sessions) for s in self.sessions[name].values()]

    def refresh(self) -> None:
        """Show every other machine's session again (its group may be gone: it falls back to the first)."""
        with self.lock:
            items = [(n, rid, s) for n, ss in self.sessions.items() for rid, s in ss.items()]
        for n, rid, s in items:
            shown = self.session(n, {**s, "id": rid, "cwd": local_paths(n, s.get("cwd", ""))})
            with self.lock:
                self.sessions.setdefault(n, {})[rid] = shown
            self.desk.broadcast({"t": "session", "session": shown})

    def machines(self) -> list[dict]:
        """The other machines and whether they answer (the page's machine filter and badges)."""
        with self.lock:
            names = sorted(set(self.sessions) | set(self.errors) | set(remote.load(self.state)))
        return [{"name": n, "ok": self.errors.get(n) == "", "error": self.errors.get(n) or ""} for n in names]

    def groups(self) -> list[dict]:
        return []  # another machine's sessions live in this server's own groups (see place)

    def status(self) -> list[dict]:
        out = []
        for name, info in sorted(remote.load(self.state).items()):
            with self.lock:
                ss = [dict(s, id=s["id"].split(SEP, 1)[1]) for s in self.sessions.get(name, {}).values()]
            err = self.errors.get(name)
            out.append({"name": name, "url": info["url"], "ok": err == "", "sessions": ss,
                        **({"error": err} if err else {}), **({"error": "connecting"} if err is None else {})})
        return out

    def call(self, name: str, method: str, path: str, body: dict | None = None, timeout: float = 30,
             rewrite: bool = True) -> tuple[int, Any]:
        """The same request to that server; (status, answer with our ids; as it came when not rewrite)."""
        info = self._info(name)
        if not info:
            return 404, {"error": f"no remote called {name}"}
        req = urllib.request.Request(info["url"] + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {info['token']}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read() or b"{}")
                return r.status, self.rewrite(name, d) if rewrite else d
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read())
            except ValueError:
                return e.code, {"error": str(e)}
        except OSError as e:
            return 502, {"error": f"{name} is unreachable ({info['url']}): {e}"}

    def external(self, limit: int, q: str = "") -> list[dict]:
        """Every other machine's conversations to resume (see Desk.external), at once; one that does not answer is
        left out. Each says its machine; its folder is written /@NAME/… With q, the ones that match it there (a server
        from before 1.3 sends its newest: the caller matches them again)."""
        import concurrent.futures as cf
        from urllib.parse import quote
        names = sorted(remote.load(self.state))
        if not names:
            return []
        qs = f"&q={quote(q)}" if q else ""

        def one(name: str) -> list[dict]:
            code, r = self.call(name, "GET", f"/api/external-sessions?limit={limit}&local=1{qs}", timeout=8, rewrite=False)
            if code != 200 or not isinstance(r, dict):
                return []
            return [{**x, "machine": name, "cwd": ours(name, str(x.get("cwd", "")))} for x in r.get("sessions") or []
                    if isinstance(x, dict)]
        with cf.ThreadPoolExecutor(len(names)) as ex:
            return [x for xs in ex.map(one, names) for x in xs]

    def find_files(self, q: str, limit: int) -> tuple[list[dict], list[str]]:
        """Every other machine's files named so (⌘O → All Files: their sessions' folders and home), at once, their
        paths written /@NAME/…; (files, the machines that did not answer)."""
        import concurrent.futures as cf
        from urllib.parse import quote
        names = sorted(remote.load(self.state))
        if not names:
            return [], []

        def one(name: str) -> list[dict] | None:
            code, r = self.call(name, "GET", f"/api/findfiles?q={quote(q)}&limit={limit}&local=1", timeout=8, rewrite=False)
            if code != 200 or not isinstance(r, dict):
                return None
            return [{**x, "machine": name, "path": ours(name, str(x.get("path", ""))), "root": ours(name, str(x.get("root", "")))}
                    for x in r.get("files") or [] if isinstance(x, dict)]
        with cf.ThreadPoolExecutor(len(names)) as ex:
            got = list(ex.map(one, names))
        return [x for xs in got if xs for x in xs], [n for n, xs in zip(names, got) if xs is None]

    def remember(self, name: str, s: dict) -> None:
        """A session this server just made there (its event follows; the caller should not wait for it)."""
        if isinstance(s, dict) and str(s.get("id", "")).startswith(name + SEP):
            with self.lock:
                self.sessions.setdefault(name, {})[s["id"].split(SEP, 1)[1]] = s
            self.desk.broadcast({"t": "session", "session": s})


def wait_for(cond, timeout: float = 5.0) -> bool:  # tests
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False
