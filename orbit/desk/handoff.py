"""Agents that outlive the Orbit Server (builddoc/harness-server.md §3, §4): the Harness Server runs their processes;
a restart hands each one off (its cards, the driver's state, the byte offset read) and the next server reads on.

    host()           at start (`orbit serve`): connect to the Harness Server (starting it), take the hand-offs, end
                     every process nobody attached, before any session may start one
    _hand_off(s)     at a graceful stop: one session's process carried over to the next server
"""

from __future__ import annotations

import copy
import json
import os
import threading
import time

from orbit.desk.session import DRIVERS, Session
from orbit.drivers.base import Driver
from orbit.drivers.process import DIRECT, ProcessDriver


SESSION_FIELDS = ("state", "model_seen", "commands", "terminal", "context", "attention", "compactions",
                  "auto_turn")  # (a turn of its own not yet a card: read on by the next server)


class Handoff:
    """Desk methods: the Harness Server, the hand-off at a restart and taking it at start-up."""

    def host(self, server_args: list[str] | tuple = ()) -> None:
        """Run the agents in the Harness Server of this state dir (`orbit serve` turns it on; tests stay direct unless
        they ask). Direct when it cannot be used, or with ORBIT_HARNESS_SERVER=0."""
        from orbit.harness_server.client import HostedLauncher, Mismatch
        if os.environ.get("ORBIT_HARNESS_SERVER") == "0":
            self.hosting = {"mode": "direct", "why": "ORBIT_HARNESS_SERVER=0"}
            self._retire_harness_server()
            self._take_handoffs([])
            return
        launcher = HostedLauncher(self.state_dir, args=server_args, fallback=DIRECT)
        try:
            # This server holds <state>/server.lock: an owner still connected is an earlier server of this state dir
            # that is gone or hung, so it is taken over.
            hello = launcher.connect(takeover=True)
        except Mismatch as e:
            # Another major protocol: the old one goes (its agents end; their requests resume as after a crash).
            # Never two Harness Servers, never live agents left behind. (A waiting update: phase 5.)
            print(f"orbit: {e}: stopping it", flush=True)
            self._retire_harness_server()
            try:
                hello = launcher.connect(takeover=True)
            except OSError as e2:
                return self._direct(f"the Harness Server could not start: {e2}")
        except OSError as e:
            return self._direct(f"the Harness Server could not be used: {e}")
        self.launcher = launcher
        self.hosting = {"mode": "hosted", "pid": hello.get("pid"), "version": hello.get("version"),
                        "protocol": hello.get("protocol"), "started": hello.get("started")}
        try:
            procs = launcher.list()
        except OSError:
            procs = []
        attached = self._take_handoffs(procs)
        self._end_unattached(procs, attached)

    def _direct(self, why: str) -> None:
        print(f"orbit: {why}: agents run directly", flush=True)
        self.hosting = {"mode": "direct", "why": why}
        self._take_handoffs([])

    def _retire_harness_server(self) -> None:
        """A Harness Server of this state dir that will not be used: it and its processes end (none may write to a
        conversation the next request resumes)."""
        from orbit.harness_server.client import Client
        c = Client(self.state_dir)
        try:
            c.connect(start=False)
            c.request("shutdown")
        except OSError:
            return
        finally:
            c.close()
        sock = os.path.join(self.state_dir, "harness", "server.sock")
        end = time.time() + 8
        while os.path.exists(sock) and time.time() < end:
            time.sleep(0.1)

    def restart_harness_server(self) -> dict:
        """Restart Harness Server (the ⋯ menu, View, `orbit harness-server restart`): every agent process ends (its
        session stays: a request at work is cut off and goes on by itself, as when an agent dies; requests sent
        meanwhile wait in the queue), the Harness Server ends, and a new one starts from the code on disk."""
        if self.launcher is None:
            raise ValueError("the agents run in the server itself (" + (self.hosting.get("why") or "direct") +
                             "): there is no Harness Server to restart")
        with self.lock:
            if self.hs_restarting or self.stopping_all:
                raise ValueError("the Harness Server is restarting already, or the server is stopping")
            self.hs_restarting = True
        old_pid = self.hosting.get("pid")
        try:
            live = [s for s in self.sessions.values() if s.driver is not None and s.driver.alive()]
            with self.lock:  # a request at work is cut off: it goes on once the new one is up (as after a restart)
                for s in live:
                    if s.current_request() is not None:
                        s.interrupted = True
            self.save()
            closing = [threading.Thread(target=s.driver.close, daemon=True) for s in live]
            for t in closing:  # (each waits for its process: TERM, then KILL)
                t.start()
            for t in closing:
                t.join(15)
            self.launcher.shutdown_server()  # (whatever is left ends with it)
            sock = os.path.join(self.state_dir, "harness", "server.sock")
            end = time.time() + 8
            while os.path.exists(sock) and time.time() < end:
                time.sleep(0.1)
            self.launcher = None
            self.host()
            self.resume_interrupted("Orbit's Harness Server restarted")  # (queued: they start below)
        finally:
            with self.lock:
                self.hs_restarting = False
        for s in list(self.sessions.values()):  # requests that came meanwhile: their agent starts now
            retrying = any(c["state"] == "paused" and "retrying in" in (c.get("error") or "") for c in s.cards)
            if (not s.closed and not retrying and s.current() is None  # (a retry starts its own)
                    and any(c["state"] == "queued" for c in s.cards) and not (s.driver and s.driver.alive())):
                try:
                    self._start(s, resume=True)
                except Exception as e:  # noqa: BLE001 - one session must not stop the others
                    print(f"orbit: {s.name}: {type(e).__name__}: {e}", flush=True)
        print(f"orbit: the Harness Server restarted ({old_pid} → {self.hosting.get('pid')}); "
              f"{len(live)} agent process(es) ended", flush=True)
        return {**self.harness_status(), "ended": len(live), "old_pid": old_pid}

    def harness_status(self) -> dict:
        st = dict(self.hosting)
        if self.launcher is not None:
            try:
                st["procs"] = self.launcher.list()
            except OSError as e:
                st["error"] = str(e)
        return st

    # ------------------------------------------------------------------ start: taking the hand-off
    def _take_handoffs(self, procs: list[dict]) -> set[str]:
        """Each <state>/handoff/<session>.json (then deleted): the session's cards as they were, and its process
        attached when it is still listed. The processes attached."""
        d = self.state_dir / "handoff"
        listed = {p["proc"]: p for p in procs}
        attached: set[str] = set()
        for f in sorted(d.glob("*.json")) if d.is_dir() else []:
            try:
                h = json.loads(f.read_text())
            except (OSError, ValueError):
                h = None
            f.unlink(missing_ok=True)
            s = self.sessions.get(f.stem)
            if not isinstance(h, dict) or s is None or s.closed:
                continue  # (its process, if any, is ended with the unattached ones)
            with self.lock:
                s.cards = [c for c in h.get("cards") or [] if isinstance(c, dict)]
                s.loaded, s.pending = True, []
                for k in SESSION_FIELDS:
                    if k in (h.get("session") or {}):
                        setattr(s, k, h["session"][k])
            p = listed.get(h.get("proc"))
            drv = self._attachable(s, h) if p is not None and self.launcher is not None else None
            hp = None
            if drv is not None:
                with self.lock:
                    s.catching_up = True  # until its stream is live: no new request goes to it
                    s.intro_pending = False  # this process had its [orbit] line
                    s.driver = drv
                try:
                    hp = self.launcher.attach(p["proc"], int(h.get("offset") or 0), int(p["pid"]))
                    drv.attach(hp, h.get("driver") or {}, bool(h.get("busy")))
                except Exception as e:  # noqa: BLE001 - e.g. its journal was truncated (too long unread), a bad checkpoint
                    print(f"orbit: {s.name}: its process cannot be read on ({type(e).__name__}: {e}): resumed instead",
                          flush=True)
                    with self.lock:
                        s.driver, s.catching_up = None, False
                    if hp is not None:
                        hp.detach(0)
                    hp = None
            if hp is None:
                self._cut_off(s)  # (its process, if any, is ended with the unattached ones)
                continue
            attached.add(p["proc"])
            self._session_changed(s)
        return attached

    def _attachable(self, s: Session, h: dict) -> Driver | None:
        """The driver that reads on a handed-off process: only a reattachable ProcessDriver of the harness that
        wrote the hand-off (its plugin gone, another harness: None, the session is cut off). Phase 1 wrote no
        harness: only Claude Code was handed off then, under its session's own."""
        cls = DRIVERS.get(s.harness)
        if (cls is None or h.get("harness", s.harness) != s.harness or not issubclass(cls, ProcessDriver)
                or not cls.reattachable):
            return None
        try:
            cls.primed(h.get("driver") or {})
            return self._make_driver(s, resume=True)
        except Exception as e:  # noqa: BLE001 - a driver that cannot be made: cut off, as when gone
            print(f"orbit: {s.name}: {type(e).__name__}: {e}", flush=True)
            return None

    def _cut_off(self, s: Session) -> None:
        """A handed-off session whose process is gone: the request it ran is resumed (resume_interrupted), its
        background subagents, and a turn it began itself, ended with it."""
        with self.lock:
            self._end_auto(s)
            c = s.current()
            if c is not None:
                c["state"], c["finished"], c["continued"] = "paused", time.time(), True  # (resumed for the user)
                c["error"] = "its agent's process ended while Orbit's server restarted"
                c["partial"] = c["thinking"] = ""
                for st in c["steps"]:
                    if not st.get("background"):
                        st["done"] = True
                s.interrupted = True
            s.state = "stopped"
            self._end_background(s)

    def _end_unattached(self, procs: list[dict], attached: set[str]) -> None:
        """§4.4: every listed process nobody attached ends (TERM, KILL, waited for, forgotten) before any session may
        start a new one: `--resume` must not start on a conversation a live process still writes. After a crash
        nobody marked its request interrupted: that is done here."""
        rest = [p for p in procs if p["proc"] not in attached]
        if not rest or self.launcher is None:
            return
        live = [p for p in rest if p.get("alive")]
        if live:
            print(f"orbit: ending {len(live)} agent process(es) no session took over", flush=True)
        try:
            self.launcher.end(rest)
        except OSError as e:
            print(f"orbit: ending them: {e}", flush=True)
        for p in live:
            s = self.sessions.get(p.get("tag") or "")
            if s is not None and not s.closed and (s.current_request() is not None if s.loaded else s.was_running):
                s.interrupted = True
        self.save()

    # ------------------------------------------------------------------ stop: the hand-off
    def _hand_off(self, s: Session) -> bool:
        """Carry this session's process over to the next server: its readers stop at a line boundary, then its cards,
        the driver's state and the offset read are written. False: closed as before."""
        drv = s.driver
        if not isinstance(drv, ProcessDriver):
            return False
        # The reader finishes the line in hand before it stops, so everything up to the offset reached the cards.
        got = drv.detach_for_hand_off()
        if got is None:
            return False
        proc, off = got
        with self.lock:
            s.driver = None  # whatever the old driver still says is dropped
            h = {"harness": drv.harness, "proc": proc.proc, "offset": off, "driver": drv.checkpoint(),
                 "session": {k: getattr(s, k) for k in SESSION_FIELDS},
                 "cards": copy.deepcopy(s.cards), "busy": drv.busy, "time": time.time()}
        d = self.state_dir / "handoff"
        d.mkdir(exist_ok=True)
        tmp = d / f"{s.id}.tmp"
        tmp.write_text(json.dumps(h, ensure_ascii=False))
        os.replace(tmp, d / f"{s.id}.json")
        proc.trim()
        return True
