"""The Desk: the harness-independent layer above the drivers.

It owns sessions, the per-session request queue and the cards, and broadcasts every change to the page over SSE.
Conversations are never copied here: on load each session's cards are rebuilt from its driver's own store.
This file holds its state, persistence, events and the sessions' lifecycle; the rest is in mixins beside it.
"""

from __future__ import annotations

import copy
import json
import os
import queue
import threading
import time
import uuid
from pathlib import Path

from orbit.desk import cardpatch
from orbit.desk.agents import Agents
from orbit.desk.driving import Driving
from orbit.desk.fleet import Fleet
from orbit.desk.groups import Groups, tidy
from orbit.desk.handoff import Handoff
from orbit.desk.meetings import Meetings
from orbit.desk.requests import Requests
from orbit.desk.layout import Layout
from orbit.desk.sides import Sides
from orbit.desk.session import ACTIVE, DEFAULT_GROUPS, DRIVERS, FLEET_GROUP, MODES, OPEN_IN, SETTINGS, Session
from orbit.drivers.process import ProcessDriver
from orbit.services.calendar import Calendar
from orbit.services.live import config as live_config
from orbit.services.shell import Shells
from orbit.workspace import notes


class Desk(Groups, Fleet, Driving, Requests, Agents, Sides, Layout, Meetings, Handoff):
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        try:  # this user's only: sessions, settings and logs are in it (on a machine shared with others too)
            if self.state_dir.stat().st_uid == os.getuid():
                os.chmod(self.state_dir, 0o700)
        except OSError:
            pass
        self.path = state_dir / "sessions.json"
        self.lock = threading.RLock()
        self.stopping_all = False  # the server is stopping: agents going away now are not retried
        self.hs_restarting = False  # Restart Harness Server at work: no process starts (requests queue) until it is back
        self.stop_agents = False  # ... and its agents stop with it (Quit Orbit and Stop Agents), not handed off
        self.launcher = None  # the Harness Server's HostedLauncher once host() turned it on (None: direct)
        self.hosting: dict = {"mode": "direct", "why": "not turned on (orbit serve turns it on)"}
        self.card_hooks: dict[tuple[str, int], object] = {}  # (session, card) → called with the card when it ends
        self.sessions: dict[str, Session] = {}  # insertion order = tab order
        self.groups: list[dict] = []  # [{"id", "name", "parent"?}]: a tree in list order (desk/groups.py; ⌘2…⌘9: the top ones)
        self.clients: list[queue.Queue] = []
        self._cards_told: dict[tuple, dict] = {}  # (sid, card id) → the card as last broadcast (patches start from it)
        self.badge = 0  # the page's unread count (the app shell's Dock badge)
        self.ui_ctx: dict = {}  # what the user is looking at, reported by the page (orbit-agent ctx)
        self.show_dir = state_dir / "show"  # pages agents show with `orbit-agent show`
        self.scheduler = None  # orbit.services.schedule.Scheduler, attached by the server
        self.live = None  # orbit.services.live.Live: the GPT Live voice call, attached by the server
        self.federation = None  # orbit.machines.federation.Federation: other machines' sessions, attached by the server
        from orbit.desk.external_index import ExternalIndex
        self.external_index = ExternalIndex(self.broadcast)  # conversations to resume (Resume another session)
        self.calendar = Calendar(state_dir)
        self.shells = Shells()  # the ⌘J shells, one per session
        self.settings_path = state_dir / "settings.json"
        try:
            self.settings = {**SETTINGS, **json.loads(self.settings_path.read_text())}
        except (OSError, ValueError):
            self.settings = dict(SETTINGS)
        notes.WEEK_TEMPLATE = str(self.settings.get("week_template") or "")  # (a new week note's text, if set)
        # The model each choice ran last ({"claude:": "claude-opus-5-5", "claude:opus": …}): the ⌘N dialog shows
        # what "Default" or an alias resolves to, learned from each process's start-up.
        self._soon: dict[tuple, threading.Timer] = {}  # cards with a streaming broadcast pending
        self._sent_at: dict[tuple, float] = {}
        self.seen_path = state_dir / "models-seen.json"
        try:
            self.seen_models: dict[str, str] = json.loads(self.seen_path.read_text())
        except (OSError, ValueError):
            self.seen_models = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            data = {"sessions": []}
        except json.JSONDecodeError:
            bad = self.path.with_suffix(f".bad-{int(time.time())}.json")
            self.path.rename(bad)  # keep the broken file for recovery, never overwrite it
            data = {"sessions": []}
        self.groups = tidy([g for g in data.get("groups", []) if g.get("id") and g.get("name")]) or \
            [dict(g) for g in DEFAULT_GROUPS]  # a fresh Orbit starts with a few groups (rename or delete them)
        ids = {g["id"] for g in self.groups}
        for m in data.get("sessions", []):
            s = Session(m)
            if s.fleet:
                s.group = FLEET_GROUP
            elif s.group not in ids:
                s.group = self.groups[0]["id"]
            self.sessions[s.id] = s
        # Tab order across machines: this machine's sessions and other machines' (NAME~ID), as the user arranged them.
        self.tab_order = [str(x) for x in data.get("order", []) if isinstance(x, str)]

    def save(self) -> None:
        with self.lock:
            text = json.dumps({"groups": self.groups, "sessions": [s.meta() for s in self.sessions.values()],
                               "order": self.tab_order}, indent=1, ensure_ascii=False)
            # (a temp file of its own: during a restart's hand-off two servers may save into the same folder at once)
            tmp = self.path.with_name(f"{self.path.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(text)
            os.replace(tmp, self.path)  # atomic: a crash never leaves a half-written file

    def subscribe(self, delta: bool = False) -> queue.Queue:
        """A queue of the live events (JSON text). delta: a card's change may come as a patch (desk/cardpatch.py)."""
        q: queue.Queue = queue.Queue(maxsize=5000)
        q.delta = delta  # type: ignore[attr-defined]
        with self.lock:
            self.clients.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)

    def broadcast(self, ev: dict) -> None:
        # Serialize now: cards are mutated in place, and a client must see each state as it was.
        with self.lock:  # (queued under the lock too: each client gets the changes in the order they were made)
            data = json.dumps(ev, ensure_ascii=False)
            short = self._card_patch(data) if ev.get("t") == "card" else None
            for q in self.clients:
                try:
                    q.put_nowait(short if short is not None and getattr(q, "delta", False) else data)
                except queue.Full:
                    pass

    CARDS_KEPT = 300  # the last version told of this many cards (at work): what their patches start from

    def _card_patch(self, data: str) -> str | None:
        """A card's change as a patch from the version told before (desk/cardpatch.py), when it is that much smaller;
        else None (it goes whole). The version told is kept for the next one (cards at work only)."""
        new = json.loads(data).get("card") or {}
        key = (new.get("sid"), new.get("id"))
        old = self._cards_told.pop(key, None)
        if new.get("state") not in ("completed", "error", "cancelled"):
            self._cards_told[key] = new
            while len(self._cards_told) > self.CARDS_KEPT:
                self._cards_told.pop(next(iter(self._cards_told)))
        p = cardpatch.patch(old, new, ACTIVE)
        if p is None:
            return None
        text = json.dumps(p, ensure_ascii=False)
        return text if len(text) < len(data) * 0.7 else None

    def _card_changed(self, c: dict) -> None:
        s = self.sessions.get(c["sid"])
        if s is not None and s.catching_up:  # reading what its agent did meanwhile: the last state is what counts
            return self._card_changed_soon(c)
        c["rev"] = c.get("rev", 0) + 1
        self._soon.pop((c["sid"], c["id"]), None)
        self.broadcast({"t": "card", "card": c})
        s = self.sessions.get(c["sid"])
        if s is not None:  # its subagents at work: in the session's marks (a background one outlives its request)
            n = s.running_agents()
            if n != getattr(s, "_agents_told", 0):
                s._agents_told = n
                self._session_changed(s)

    def _card_changed_soon(self, c: dict) -> None:
        """Streaming deltas: at most ~8 broadcasts a second per card (the last state always goes out)."""
        key = (c["sid"], c["id"])
        if key in self._soon:
            return
        last = self._sent_at.get(key, 0.0)
        wait = max(0.0, 0.12 - (time.time() - last))

        def go() -> None:
            with self.lock:
                if self._soon.pop(key, None) is None:
                    return  # a full change went out meanwhile
                self._sent_at[key] = time.time()
                c["rev"] = c.get("rev", 0) + 1
                self.broadcast({"t": "card", "card": c})

        tm = threading.Timer(wait, go)
        tm.daemon = True
        self._soon[key] = tm
        tm.start()

    def _session_changed(self, s: Session) -> None:
        self.broadcast({"t": "session", "session": s.public()})

    def list(self, closed: bool = False) -> list[dict]:
        """Open sessions in tab order; with closed=True also the closed ones (finder, ⌘R, ⌘⇧T)."""
        with self.lock:
            return [s.public() for s in self.sessions.values() if closed or not s.closed]

    def get(self, sid: str) -> Session:
        s = self.sessions.get(sid)
        if not s:
            raise KeyError(sid)
        return s

    def create(self, cwd: str, model: str = "", mode: str = "default", name: str = "",
               harness: str = "claude", group: str = "", effort: str = "") -> dict:
        cwd = os.path.realpath(os.path.expanduser(cwd or "~"))
        if not os.path.isdir(cwd):
            raise ValueError(f"no such folder: {cwd}")
        if harness not in DRIVERS:
            raise ValueError(f"unknown harness: {harness}")
        if mode not in MODES:
            raise ValueError(f"unknown mode: {mode}")
        if mode not in DRIVERS[harness].modes:
            raise ValueError(f"{DRIVERS[harness].label} has no {mode} mode (it has {', '.join(DRIVERS[harness].modes)})")
        if effort and effort not in DRIVERS[harness].efforts:
            raise ValueError(f"unknown effort for {harness}: {effort}")
        s = Session({"id": str(uuid.uuid4()), "name": name.strip(), "cwd": cwd, "model": model.strip(),
                     "mode": mode, "harness": harness, "group": self._group_id(group), "effort": effort})
        s.loaded = True
        with self.lock:
            self.sessions[s.id] = s
        self.save()
        self._start(s, resume=False)
        self._session_changed(s)
        return s.public()

    def harnesses(self) -> list[dict]:
        """The harnesses a session can run (the ⌘N dialog): id, display name, the models each offers."""
        out = []
        for k, cls in DRIVERS.items():
            seen = {key.split(":", 1)[1]: v for key, v in self.seen_models.items() if key.startswith(k + ":")}
            out.append({"id": k, "label": cls.label, "models": cls.models(), "efforts": list(cls.efforts),
                        "modes": list(cls.modes), "default_seen": seen.get("", ""), "seen": seen,
                        "steers": bool(cls.steers), "local_only": bool(cls.local_only), "icon": cls.icon_url})
        return out

    def rename(self, sid: str, name: str) -> dict:
        s = self.get(sid)
        s.name = name.strip() or Path(s.cwd).name
        s.named = bool(name.strip())
        self.save()
        self._session_changed(s)
        return s.public()

    def close(self, sid: str) -> None:
        s = self.get(sid)
        s.closed, s.closed_at = True, time.time()
        with self.lock:
            for c in s.cards:  # closing stops the work: nothing queued may start on reopen
                if c["state"] == "queued" or c["state"] in ACTIVE:
                    if c["state"] in ACTIVE:
                        c["finished"] = time.time()
                    c["state"] = "cancelled"
                    self._card_changed(c)
        drv, s.driver = s.driver, None
        if drv:  # the user closed it: end(), not close() (restarts and hand-offs call close())
            threading.Thread(target=getattr(drv, "end", drv.close), daemon=True).start()
        threading.Thread(target=self.shells.close_key, args=(sid,), daemon=True).start()
        threading.Thread(target=self.shells.close_key, args=("term:" + sid,), daemon=True).start()  # (detaches only)
        s.state = "stopped"
        self.save()
        self.broadcast({"t": "closed", "id": sid, "session": s.public()})

    def reopen(self, sid: str) -> dict:
        """Bring a closed session back with its name; its process starts on the next send (or Resume)."""
        s = self.get(sid)
        s.closed, s.closed_at = False, None
        self.save()
        self._session_changed(s)
        return s.public()

    def resume(self, sid: str) -> dict:
        s = self.get(sid)
        if not s.catching_up and (not s.driver or not s.driver.alive()):
            self._start(s, resume=True)
        return s.public()

    def update(self, sid: str, group: str | None = None, favorite: bool | None = None) -> dict:
        s = self.get(sid)
        if group is not None:
            if group not in {g["id"] for g in self.groups}:
                raise ValueError(f"no such group: {group}")
            s.group, s.fleet = group, False  # a Fleet helper moved to a group becomes an ordinary session
        if favorite is not None:
            s.favorite = bool(favorite)
        self.save()
        self._session_changed(s)
        return s.public()

    def _ordered(self, ids: list[str]) -> list[str]:
        """ids in the user's tab order; ones never arranged keep their place after those that were."""
        present = set(ids)
        known = [i for i in self.tab_order if i in present]
        seen = set(known)
        return known + [i for i in ids if i not in seen]

    def _all_ids(self) -> list[str]:
        return list(self.sessions) + ([s["id"] for s in self.federation.list()] if self.federation else [])

    def move(self, sid: str, before: str | None) -> None:
        """Put a session (of this machine or another) before another one in tab order (None: at the end)."""
        with self.lock:
            order = self._ordered(self._all_ids())
            if sid not in order:
                raise KeyError(sid)
            if before is not None and before not in order:
                raise KeyError(before)
            order.remove(sid)
            order.insert(order.index(before) if before is not None else len(order), sid)
            self.tab_order = order
            self.sessions = {i: self.sessions[i] for i in order if i in self.sessions}
        self.save()
        self.broadcast({"t": "order", "ids": order})

    def fork(self, sid: str, name: str = "") -> dict:
        """A new session in the same group that continues from this one's conversation, with all its settings
        (folder, harness, model, effort, mode). The harness forks the conversation itself (Claude Code --fork-session,
        Codex thread/fork, a protocol agent's --fork); one that cannot (agy, `fork: false`) gets the conversation so far
        with its first request."""
        src = self.get(sid)
        self._ensure_loaded(src)
        from orbit.desk.session import DRIVERS
        native = getattr(DRIVERS.get(src.harness), "native_fork", True)
        with self.lock:
            s = Session({"id": str(uuid.uuid4()), "name": name.strip() or f"{src.name} (fork)", "named": bool(name.strip()) or src.named,
                         "cwd": src.cwd, "model": src.model, "mode": src.mode, "effort": src.effort, "harness": src.harness,
                         "group": src.group, "fork_from": src.id, "last": src.last})
            s.loaded = True
            for c in src.cards:
                if c["state"] in ("queued",) + ACTIVE:
                    continue
                cc = copy.deepcopy(c)
                cc["sid"] = s.id
                s.cards.append(cc)
            s.compactions = src.compactions
            if not native:
                s.fork_carry = len(s.cards)
            order = list(self.sessions.values())
            i = order.index(src) + 1
            order.insert(i, s)
            self.sessions = {x.id: x for x in order}
            if src.id in self.tab_order:  # right after the session it continues, there too
                self.tab_order.insert(self.tab_order.index(src.id) + 1, s.id)
            ids = self._ordered(self._all_ids())
        self.save()
        self._session_changed(s)
        self.broadcast({"t": "order", "ids": ids})
        return s.public()

    clone = fork  # (its earlier name)

    def set_settings(self, b: dict) -> dict:
        for k, v in b.items():
            if k not in SETTINGS:
                raise ValueError(f"no such setting: {k}")
            if type(v) is int and type(SETTINGS[k]) is float:
                b[k] = v = float(v)  # (JSON writes 1.0 as 1)
            if type(v) is not type(SETTINGS[k]):
                raise ValueError(f"{k} takes a {type(SETTINGS[k]).__name__}")
            if k == "open_in" and v not in OPEN_IN:
                raise ValueError(f"open_in is one of {', '.join(OPEN_IN)}")
            if k.startswith("live_"):
                live_config.check(k, v)
            if k == "fleet_harness" and v and v not in DRIVERS:
                raise ValueError(f"fleet_harness is one of {', '.join(sorted(DRIVERS))} (or \"\": Claude Code)")
            if k in ("fleet_model", "week_template"):
                b[k] = v = v.strip()
                if len(v) > 300 or "\n" in v:
                    raise ValueError(f"{k}: one line")
            if k == "meeting_notes":
                from orbit.desk.meetings import check_note_pattern
                b[k] = check_note_pattern(v)
        if "live_budget" in b:
            b["live_budget_at"] = time.time()  # (the balance as of now: calls from here on count down from it)
        with self.lock:
            self.settings.update(b)
            snap = dict(self.settings)
        notes.WEEK_TEMPLATE = str(snap.get("week_template") or "")
        tmp = self.settings_path.with_name(f"{self.settings_path.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(snap, indent=1))
        os.replace(tmp, self.settings_path)
        self.broadcast({"t": "settings", "settings": snap})
        return snap

    def card(self, sid: str, cid: float) -> dict:
        """One card, as it is now (a client that missed a patch fetches it). KeyError: no such session or card."""
        s = self.get(sid)
        self._ensure_loaded(s)
        with self.lock:
            c = next((x for x in s.cards if float(x["id"]) == float(cid)), None)
            if c is None:
                raise KeyError(f"{sid}#{cid}")
            return self._as_told(c)

    def _as_told(self, c: dict) -> dict:
        """A card for a client to hold (a copy): a card at work as it was last broadcast, not as it is now. Its
        streaming text grows in place before its revision moves (the next broadcast, ≤ 0.12 s later): read now, a
        client would get text its next patch adds again."""
        told = self._cards_told.get((c.get("sid"), c.get("id")))
        return copy.deepcopy(told if told is not None and told.get("rev") == c.get("rev") else c)

    def cards(self, sid: str) -> list[dict]:
        s = self.get(sid)
        self._ensure_loaded(s)
        with self.lock:  # a snapshot (drivers mutate cards while the response is written); cards at work as last told
            return [self._as_told(c) for c in s.cards]

    def resume_command(self, sid: str) -> dict:
        """The shell command that continues this session's conversation in a terminal: cd to its folder, then the
        harness's own resume ({"command", "alive"}; ValueError when the harness has none, or nothing was asked yet).
        alive: its agent's process runs here (hosted or not), so the terminal would be a second writer."""
        import shlex
        s = self.get(sid)
        argv = self._make_driver(s, resume=True).terminal_command()
        if not argv:
            raise ValueError(f"{s.name}: nothing to resume in a terminal yet" if s.harness in ("claude", "codex", "agy", "opencode")
                             else f"{s.name}: its agent has no terminal resume")
        drv = s.driver
        alive = s.catching_up or (isinstance(drv, ProcessDriver) and drv.running())
        return {"command": f"cd {shlex.quote(s.cwd)} && {shlex.join(argv)}", "alive": bool(alive)}

    def _ensure_loaded(self, s: Session) -> None:
        with self.lock:
            if s.loaded:
                return
            s.loaded = True
            drv = self._make_driver(s, resume=True)
            n, k = 0, 0
            for h in drv.history():
                if h.kind == "auto":  # a turn of its own: after the request before it (5.501 …), as live (_auto_card)
                    k += 1
                    s.cards.append({**self._new_card(s, round(n + 0.5 + k / 1000, 3), h.text, h.time), "kind": "auto",
                                    "auto": dict(h.auto or {}), "state": h.state, "steps": h.steps, "answer": h.answer,
                                    "followups": h.followups, "started": h.time, "finished": h.finished,
                                    "compactions": h.compactions, "steers": h.steers, "rev": 1})
                    s.compactions += h.compactions
                    continue
                n, k = n + 1, 0
                s.cards.append({"id": n, "sid": s.id, "text": h.text, "state": h.state, "steps": h.steps, "steers": h.steers,
                                "answer": h.answer, "live": "", "followups": h.followups, "time": h.time,
                                "started": h.time, "finished": h.finished, "compactions": h.compactions,
                                "error": "", "activity": "", "rev": 1, **({"hidden": True} if n in s.hidden else {}),
                                **({"origin": s.origins[str(n)]} if str(n) in s.origins else {})})
                s.compactions += h.compactions
            for p in s.pending:
                n += 1
                c = self._new_card(s, n, p["text"], p.get("time"))
                if p.get("origin"):
                    c["origin"] = s.origins[str(n)] = p["origin"]
                s.cards.append(c)
            s.pending = []
            sent = {v: k for k, v in s.client_ids.items()}  # card id → the sender's client id
            for c in s.cards:
                if c["id"] in sent:
                    c["client_id"] = sent[c["id"]]
            for x in s.notes:  # note cards between the requests they followed
                s.cards.append({**self._new_card(s, x["id"], x.get("text", ""), x.get("time")), "kind": "note",
                                "state": "note", "answer": x.get("answer", ""), "started": x.get("time"),
                                "finished": x.get("time"), "rev": 1, **({"origin": x["origin"]} if x.get("origin") else {})})
            s.cards.sort(key=lambda c: c["id"])

    def external(self, limit: int = 40, q: str = "") -> list[dict]:
        """Conversations started outside Orbit (a terminal, anywhere) that a harness can resume: newest first, or with
        q the ones that match it, best first (from the server's index: desk/external_index.py; never waits on a
        plugin harness, whose list comes in the background)."""
        from orbit.desk import external_index
        try:  # {harness: {Orbit session: its conversation}} (see Driver.link)
            ids = json.loads((self.state_dir / "harness-ids.json").read_text())
        except (OSError, ValueError):
            ids = {}
        drivers = dict(DRIVERS)
        if not q or not self.external_index.files:  # (the dialog opens: the stores looked at again; a typed query
            self.external_index.refresh(drivers)       # searches the index as it is, once there is one)
        skip: dict[str, set[str]] = {}
        for name, cls in drivers.items():
            if cls.identity == "orbit-id":  # its conversation has the Orbit session's id
                skip[name] = set(self.sessions)
            else:
                linked = ids.get(name) if isinstance(ids, dict) else None
                skip[name] = {v for v in linked.values() if isinstance(v, str)} if isinstance(linked, dict) else set()
        found = [x for x in self.external_index.all(drivers) if x["id"] not in skip.get(x["harness"], set())]
        if q:
            labels = {h: getattr(cls, "label", h) for h, cls in drivers.items()}
            return external_index.search(found, q, labels, limit)
        return found[:limit]

    def attach(self, harness: str, conversation: str, cwd: str, name: str = "", group: str = "",
               mode: str = "default") -> dict:
        """An Orbit session that resumes a conversation started elsewhere. Its history shows as cards; the next
        request continues it (a terminal still running it should be closed first: both would write to it)."""
        cls = DRIVERS.get(harness)
        if cls is None:
            raise ValueError(f"cannot attach a {harness} conversation")
        own = cls.identity == "orbit-id"  # the conversation id becomes the session's
        cwd = os.path.realpath(os.path.expanduser(cwd or "~"))
        if not os.path.isdir(cwd):
            raise ValueError(f"no such folder: {cwd}")
        if own and conversation in self.sessions:  # already here (maybe closed): bring it back
            s = self.sessions[conversation]
            if s.closed:
                self.reopen(s.id)
            return s.public()
        s = Session({"id": conversation if own else str(uuid.uuid4()), "name": name.strip(), "cwd": cwd,
                     "mode": mode, "harness": harness, "group": self._group_id(group), "named": bool(name.strip())})
        with self.lock:
            self.sessions[s.id] = s
        self.save()
        s.intro_pending = True
        s.driver = self._make_driver(s, resume=True)
        if not own:
            s.driver.link(conversation)  # Orbit session → the harness's conversation (it resumes that one)
        self._ensure_loaded(s)  # the conversation so far, as cards
        s.driver.start()
        self._session_changed(s)
        return s.public()

    def all_sessions(self, closed: bool = False) -> list[dict]:
        """Every machine's sessions, in the user's tab order."""
        ss = self.list(closed=closed) + (self.federation.list() if self.federation else [])
        pos = {i: k for k, i in enumerate(self._ordered([s["id"] for s in ss]))}
        return sorted(ss, key=lambda s: pos[s["id"]])

    def shutdown(self) -> None:
        """The server stops. A restart hands each hosted, reattachable agent off to the next server (it runs on);
        the others are closed, and a request cut off goes on after the restart (resume_interrupted). With
        stop_agents (Quit Orbit and Stop Agents) every agent is closed, then the Harness Server ends."""
        with self.lock:  # (a send, steer or Stop holding the lock goes out before; none after)
            self.stopping_all = True  # no new work: no _pump, no retries, sends, steers and Stops refused
        handed: set[str] = set()
        if self.launcher is not None and not self.stop_agents:
            for s in list(self.sessions.values()):
                try:
                    if self._hand_off(s):
                        handed.add(s.id)
                except Exception as e:  # noqa: BLE001 - one session's hand-off must not stop the others'
                    print(f"orbit: {s.name}: hand-off failed ({type(e).__name__}: {e}): closed instead", flush=True)
        with self.lock:
            for s in self.sessions.values():
                if s.id not in handed and s.current_request() is not None and s.driver and s.driver.alive():
                    s.interrupted = True
        if self.live is not None:  # a voice call ends with the server (its transcript kept as a note card)
            try:
                self.live.shutdown()
            except Exception as e:  # noqa: BLE001 - the server stops anyway
                print(f"orbit: voice call: {type(e).__name__}: {e}", flush=True)
        if self.federation:
            self.federation.stop()
        if self.scheduler:
            self.scheduler.stop()
        self.shells.shutdown()
        self.save()
        for s in self.sessions.values():
            if s.id not in handed and s.driver and s.driver.alive():
                s.driver.close()
        if self.launcher is not None:
            if self.stop_agents:
                self.launcher.shutdown_server()
            else:
                self.launcher.close()
