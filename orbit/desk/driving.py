"""Driving the harness: its process, its events into cards, the queue, model, mode, restart."""

from __future__ import annotations

import json
import os
import threading
import time

from orbit.desk.session import ACTIVE, DRIVERS, MODES, ORBIT_PREFIX, orbit_line, orbit_skill_hint, Session
from orbit.drivers.base import auto_title, Driver, DriverEvent, session_link, unavailable
from orbit.drivers.process import ProcessDriver

AUTO_RETRIES = 2  # a request whose agent died goes on by itself this many times before it waits for Continue
RETRY_WAIT = float(os.environ.get("ORBIT_RETRY_WAIT", "3"))  # seconds before the first retry (then 2×, …)


class Driving:
    """Desk methods: driving the harness: its process, its events into cards, the queue, model, mode, restart."""

    def _make_driver(self, s: Session, resume: bool) -> Driver:
        cls = DRIVERS.get(s.harness) or unavailable(s.harness)  # (its plugin gone: the session says why)
        holder: dict[str, Driver] = {}

        def emit(ev: DriverEvent) -> None:
            # Events from a replaced driver (after restart) must not touch the session.
            if holder.get("d") is s.driver:
                self._on_event(s, ev)

        holder["d"] = cls(session_id=s.id, cwd=s.cwd, model=s.model, mode=s.mode, resume=resume, emit=emit,
                          fork_from="" if s.fork_harness else s.fork_from,  # (another harness's conversation: nothing to fork)
                          state_dir=str(self.state_dir), effort=s.effort)
        if self.launcher is not None and isinstance(holder["d"], ProcessDriver):
            holder["d"].launcher = self.launcher  # its processes run in the Harness Server
        return holder["d"]

    def _start(self, s: Session, resume: bool) -> None:
        s.intro_pending = True
        s.driver = self._make_driver(s, resume=resume)
        s.driver.start()

    def _on_event(self, s: Session, ev: DriverEvent) -> None:
        with self.lock:
            d = ev.data
            c = s.current()
            if ev.kind == "state":
                s.state = d["state"]
                s.error = d.get("error", "")
                if d.get("live") or s.state != "ready":  # a handed-off process: read up to now (`live`), or gone
                    s.catching_up = False  # (a protocol agent's error is a "ready" too: not the end of catching up)
                if s.state in ("stopped", "not ready", "starting"):  # its process is gone: so are its background subagents
                    self._end_background(s)
                    self._end_auto(s)  # (and a turn it began itself)
                self._session_changed(s)
                if s.state == "ready":
                    self._pump(s)
                return
            if ev.kind == "info":  # the process's start-up: the model in use, its slash commands
                if d.get("model"):
                    s.model_seen = d["model"]
                    key = f"{s.harness}:{s.model}"
                    if self.seen_models.get(key) != d["model"]:
                        self.seen_models[key] = d["model"]
                        try:
                            self.seen_path.write_text(json.dumps(self.seen_models, indent=1))
                        except OSError:
                            pass
                s.commands = list(d.get("commands") or [])
                s.terminal = bool(d.get("terminal"))
                self._session_changed(s)
                return
            if ev.kind == "attention":  # the agent needs the user in its terminal ("" when no longer)
                s.attention = d.get("detail", "")
                self._session_changed(s)
                return
            if ev.kind == "usage":
                s.context = d["context"]
                s.model_seen = d.get("model") or s.model_seen
                self._session_changed(s)
                return
            if ev.kind == "compaction":
                s.compactions += 1
                if c:
                    c["compactions"] += 1
                    self._card_changed(c)
                self._session_changed(s)
                return
            if ev.kind == "activity":
                if c:
                    c["activity"] = d["detail"]
                    self._card_changed(c)
                return
            if ev.kind == "agent_done":  # a background subagent finished (maybe after its request did): its report
                card, agent = self._agent_step(s, d["id"])
                if agent is not None:
                    agent["done"], agent["error"] = True, d.get("status") not in ("completed", "")
                    if d.get("lost"):
                        agent["lost"] = True
                    said = [x for x in agent.get("sub", []) if x["kind"] == "note"]
                    agent["result"] = d.get("result") or (said[-1]["text"] if said else "")
                    for x in agent.get("sub", []):
                        x["done"] = True
                    self._card_changed(card)
                return
            if ev.kind in ("sub_text", "sub_tool_start", "sub_tool_end"):  # a subagent at work, under its Agent step
                card, agent = self._agent_step(s, d.get("parent", ""))
                if agent is None:
                    return
                sub = agent.setdefault("sub", [])
                if ev.kind == "sub_text":
                    sub.append({"id": "", "kind": "note", "name": "", "summary": d["text"][:300], "text": d["text"].strip(),
                                "done": True, "error": False})
                elif ev.kind == "sub_tool_start":
                    sub.append({k: v for k, v in d.items() if k != "parent"})
                else:
                    for st in sub:
                        if st["id"] == d["id"]:
                            st["done"], st["error"] = True, d["error"]
                self._card_changed_soon(card)
                return
            if ev.kind == "auto":  # the harness began a turn by itself (no request): what woke it; its card comes with
                if c is None:      # its first step, and a turn that only talks stays follow-ups on the last card
                    last = self._last_card(s)
                    s.auto_turn = {"why": str(d.get("why") or ""), "what": str(d.get("what") or ""),
                                   "card": last["id"] if last else None, "n": len(last["followups"]) if last else 0}
                return
            if c is None and ev.kind == "tool_start":  # a turn of its own that works: a card of its own (its steps,
                c = self._auto_card(s)                 # subagents and answer show as a request's do)
            if c is None:
                # Text after the answer, before the next request: a follow-up on the last finished card.
                last = self._last_card(s)
                if ev.kind == "text" and last is not None:
                    last["followups"].append(d["text"])
                    self._card_changed(last)
                elif ev.kind == "result":
                    s.auto_turn = None
                return
            if ev.kind in ("delta", "thinking"):  # streaming: the text (or thinking) being written right now
                if ev.kind == "delta":
                    c["partial"] = c.get("partial", "") + d["text"]
                    c["thinking"] = ""
                else:
                    c["thinking"] = "" if d.get("start") else (c.get("thinking", "") + d["text"])[-1200:]
                if c["state"] == "queued":
                    c["state"] = "thinking"
                self._card_changed_soon(c)
                return
            if ev.kind == "text":
                c["partial"] = ""  # the whole block arrived: it replaces its deltas
                c["thinking"] = ""
                c["live"] = (c["live"] + "\n\n" + d["text"]).strip()
            elif ev.kind == "tool_start":
                c["partial"] = c["thinking"] = ""
                if c["live"]:
                    c["steps"].append({"id": "", "kind": "note", "name": "", "summary": c["live"][:300], "text": c["live"],
                                       "done": True, "error": False})
                    c["live"] = ""
                c["steps"].append({**d, "kind": "tool", "done": False, "error": False, "t0": d.get("t0") or time.time()})
                c["state"] = "running"
            elif ev.kind == "tool_end":
                for st in c["steps"]:
                    if st["id"] == d["id"]:
                        st["done"], st["error"] = True, d["error"]
                        if d.get("background"):  # launched in the background: it runs on, reports later
                            st["done"], st["background"] = False, True
                            continue
                        if "result" in d:  # a subagent's report
                            st["result"] = d["result"]
                        for x in st.get("sub", []):
                            x["done"] = True
                if not any(st["kind"] == "tool" and not st["done"] and not st.get("background") for st in c["steps"]):
                    c["state"] = "thinking"
            elif ev.kind == "result":
                c["partial"] = c["thinking"] = ""
                c["finished"] = time.time()
                c["answer"] = d.get("text") or c["live"]
                c["live"] = ""
                c["activity"] = ""
                for st in c["steps"]:
                    if not st.get("background"):  # a background subagent runs past its request
                        st["done"] = True
                if d.get("ok"):
                    c["state"] = "completed"
                elif d.get("cancelled"):
                    c["state"] = "cancelled"
                elif c.get("kind") == "auto":  # (no request to continue: a turn of its own that broke off ends there)
                    c["state"] = "error"
                    c["error"] = d.get("error", "") or "the agent stopped before it finished"
                elif d.get("paused"):
                    c["state"] = "paused"
                    c["error"] = d.get("error", "") or "the agent stopped before it finished"
                    n = c.get("retry", 0)
                    if d.get("died") and n < AUTO_RETRIES and not self.stopping_all and s.driver is not None:
                        wait = RETRY_WAIT * (n + 1)
                        c["error"] += f" · retrying in {wait:g}s ({n + 1}/{AUTO_RETRIES})"
                        t = threading.Timer(wait, self._auto_continue, (s.id, c["id"], n + 1))
                        t.daemon = True
                        t.start()
                else:
                    c["state"] = "error"
                    c["error"] = d.get("error", "error")
                s.last = {"id": c["id"], "state": c["state"], "time": c["finished"],
                          "text": (c["answer"] or c["error"] or "").strip()[:240]}
                retrying = c["state"] == "paused" and "retrying in" in (c.get("error") or "")
                hook = None if retrying else self.card_hooks.pop((s.id, c["id"]), None)  # a retry takes it along
                if hook:
                    threading.Thread(target=hook, args=(dict(c),), daemon=True).start()
                self._card_changed(c)
                self._session_changed(s)
                self._pump(s)
                self.save()
                return
            self._card_changed(c)

    def _auto_continue(self, sid: str, cid: int, n: int) -> None:
        """Its agent died mid-request: go on where it stopped (unless the user did something else meanwhile)."""
        end = time.time() + 60
        while self.hs_restarting and time.time() < end:  # (Restart Harness Server: go on once it is back)
            time.sleep(0.2)
        s = self.sessions.get(sid)
        if not s or s.closed or self.stopping_all:
            return
        with self.lock:
            c = next((x for x in s.cards if x["id"] == cid), None)
            later = any(x["id"] > cid and x["state"] not in ("cancelled",) for x in s.cards)
        if not c or c["state"] != "paused" or c.get("continued") or later:
            return
        try:
            nxt = self.continue_(sid, cid)
        except (ValueError, KeyError, RuntimeError, OSError):
            return
        with self.lock:
            hook = self.card_hooks.pop((sid, cid), None)
            if hook:
                self.card_hooks[(sid, nxt["id"])] = hook
            for x in s.cards:
                if x["id"] == nxt["id"]:
                    x["retry"] = n
                    self._card_changed(x)

    def follow_living(self) -> list[str]:
        """At the server's start: each open session without a driver whose agent still runs outside Orbit (its
        driver's link_alive) is followed at once: its driver started with resume=True takes the running agent on
        (no new one). A stopped one stays stopped. The sessions followed."""
        out = []
        for s in list(self.sessions.values()):
            cls = DRIVERS.get(s.harness)
            if s.closed or s.driver is not None or cls is None or cls.link_alive.__func__ is Driver.link_alive.__func__:
                continue
            try:
                if not cls.link_alive(session_link(cls, s.id, str(self.state_dir)), session_id=s.id,
                                      state_dir=str(self.state_dir)):
                    continue
                self._start(s, resume=True)
                out.append(s.id)
            except Exception as e:  # noqa: BLE001 - a driver's own code: the other sessions go on
                print(f"orbit: {s.name}: following its running agent: {type(e).__name__}: {e}", flush=True)
        return out

    def resume_interrupted(self, why: str = "Orbit's server restarted") -> None:
        """After a restart: each session whose request the stopping server cut off goes on with it."""
        if self.stopping_all:  # stopped again at once: they stay marked for the next server
            return
        for s in list(self.sessions.values()):
            if not s.interrupted:
                continue
            s.interrupted = False
            self.save()
            if s.closed:
                continue
            try:
                self.send(s.id, f"{why} while you were working on my previous request, which cut you off. "
                                "Continue it where you stopped.")
            except (ValueError, KeyError, RuntimeError, OSError):
                pass

    def _end_background(self, s: Session) -> None:
        """Background subagents still marked running when their agent's process ended: they ended with it."""
        for card in s.cards:
            hit = False
            for st in card["steps"]:
                if st.get("background") and not st["done"]:
                    st["done"], st["error"], st["lost"] = True, True, True
                    st["result"] = st.get("result") or "It ended when its agent's process did (stopped or restarted), before it reported."
                    for x in st.get("sub", []):
                        x["done"] = True
                    hit = True
            if hit:
                self._card_changed(card)

    @staticmethod
    def _last_card(s: Session) -> dict | None:
        """The card a follow-up goes on: the last request or turn of its own (not a note, not one still queued)."""
        return next((x for x in reversed(s.cards) if x.get("kind") != "note" and x["state"] != "queued"), None)

    def _auto_card(self, s: Session) -> dict:
        """A card for a turn the harness began itself: not a request (kind "auto", drawn as "On its own", no edit or
        resend), titled by what woke it. Its id sits after the last request's (5.501, 5.502 …, as notes' 5.001 do), so
        requests keep the numbers the harness's history gives them, whether or not history finds the same turns. The
        text it said before its first step (follow-ups on the last card until then) moves into it."""
        a = s.auto_turn or {}
        s.auto_turn = None
        why, what = a.get("why", ""), a.get("what", "")
        after = max((int(x["id"]) for x in s.cards if x.get("kind") not in ("note", "auto") and x["state"] != "queued"),
                    default=0)
        k = sum(1 for x in s.cards if x.get("kind") == "auto" and int(x["id"]) == after)
        now = time.time()
        c = {**self._new_card(s, round(after + 0.5 + (k + 1) / 1000, 3), auto_title(why, what), now), "kind": "auto",
             "auto": {"why": why, "what": what}, "state": "thinking", "started": now}
        prev = next((x for x in s.cards if x["id"] == a.get("card")), None)
        if prev is not None and len(prev["followups"]) > a.get("n", 0):
            said, prev["followups"] = prev["followups"][a["n"]:], prev["followups"][:a["n"]]
            c["live"] = "\n\n".join(said)  # (the next step makes it a note, as a request's narration)
            self._card_changed(prev)
        s.cards.append(c)
        s.cards.sort(key=lambda x: x["id"])
        self._session_changed(s)
        return c

    def _end_auto(self, s: Session) -> None:
        """A turn of its own still marked running when its agent's process went: it ended with it."""
        s.auto_turn = None
        for c in s.cards:
            if c.get("kind") == "auto" and c["state"] in ACTIVE:
                c["state"], c["finished"] = "cancelled", time.time()
                c["partial"] = c["thinking"] = c["activity"] = ""
                for st in c["steps"]:
                    if not st.get("background"):
                        st["done"] = True
                self._card_changed(c)

    @staticmethod
    def _agent_step(s: Session, tool_id: str) -> tuple[dict | None, dict | None]:
        """The card and step of an Agent call: newest first (a background subagent outlives its request)."""
        for card in reversed(s.cards):
            for st in card["steps"]:
                if st["id"] == tool_id and "agent" in st:
                    return card, st
        return None, None

    def _pump(self, s: Session) -> None:
        """Send the next queued request if the session is idle and its process is up."""
        with self.lock:
            if (s.current() is not None or not s.driver or not s.driver.alive() or s.catching_up or self.stopping_all
                    or self.hs_restarting):
                return
            nxt = next((c for c in s.cards if c["state"] == "queued"), None)
            if not nxt:
                return
            nxt["state"] = "thinking"
            nxt["started"] = time.time()
            try:
                text = nxt["text"]
                hint = orbit_skill_hint(text)  # (one [orbit] line in all: history strips exactly one)
                o = nxt.get("origin") or {}
                if o.get("label") == "Voice" and not o.get("plugin") and "live" in (o.get("data") or {}):  # GPT Live
                    hint = (hint + " This request came by voice (the user's transcribed speech: it may mishear words); "
                            "the start of your answer is spoken back to them, so lead with the takeaway in a sentence "
                            "or two; code, tables and details stay in Orbit.").strip()
                elif o.get("label") or o.get("plugin"):  # a plugin sent it: the agent knows where its answer goes
                    via = " ".join(f"This request came via {o.get('label') or o.get('plugin')}"
                                   f"{' (the ' + o['plugin'] + ' plugin)' if o.get('plugin') else ''}; your answer is "
                                   "sent back there, so write it for that reader.".split())
                    hint = (hint + " " + via).strip()
                if s.fork_carry:  # a fork its harness could not make: the conversation so far goes with the first request
                    text = fork_carry_block(s) + "\n\n" + text
                    s.fork_carry = 0
                if s.intro_pending:
                    text = orbit_line(s) + (" " + hint if hint else "") + "\n\n" + text
                elif hint:
                    text = ORBIT_PREFIX + hint + "\n\n" + text
                s.driver.send(text)
                s.intro_pending = False
            except (RuntimeError, OSError, BrokenPipeError) as e:
                nxt["state"] = "error"
                nxt["error"] = str(e)
            self._card_changed(nxt)
            self._session_changed(s)
            self.save()  # "running" on disk: after a crash, this request is known to be cut off

    def _new_card(self, s: Session, n: int, text: str, t: float | None = None) -> dict:
        return {"id": n, "sid": s.id, "text": text, "state": "queued", "steps": [], "answer": "", "live": "",
                "followups": [], "time": t or time.time(), "started": None, "finished": None, "compactions": 0,
                "error": "", "activity": "", "rev": 0}

    def set_model(self, sid: str, model: str, effort: str | None = None) -> dict:
        """`/model …`: another model (or effort) for this session; the agent restarts on the same conversation."""
        s = self.get(sid)
        efforts = getattr(DRIVERS.get(s.harness), "efforts", ())
        if effort is not None and effort and effort not in efforts:
            raise ValueError(f"unknown effort: {effort} (one of {', '.join(efforts) or 'none'})")
        if s.current() is not None:
            raise ValueError("a request is running: change the model when it has finished")
        new_model = s.model if model is None else model.strip()
        new_effort = s.effort if effort is None else effort
        if (new_model, new_effort) == (s.model, s.effort):
            return s.public()
        s.model, s.effort = new_model, new_effort
        s.model_seen = ""  # until the new process reports the model it runs
        self.save()
        return self.restart(sid)

    def set_mode(self, sid: str, mode: str) -> dict:
        """`/mode …` in the input: a new permission mode. The agent restarts on the same conversation (a mode is fixed
        per process); refused while a request runs, so no work is cut off."""
        if mode not in MODES:
            raise ValueError(f"no such mode: {mode} (one of {', '.join(MODES)})")
        s = self.get(sid)
        cls = DRIVERS.get(s.harness)
        if cls is not None and mode not in cls.modes:
            raise ValueError(f"{cls.label} has no {mode} mode (it has {', '.join(cls.modes)})")
        if s.current() is not None:
            raise ValueError("a request is running: change the mode when it has finished")
        if s.mode == mode:
            return s.public()
        s.mode = mode
        self.save()
        return self.restart(sid)

    def restart(self, sid: str) -> dict:
        s = self.get(sid)
        old, s.driver = s.driver, None  # detach first: the old process's last events are ignored
        if old:
            old.close()  # before the new process resumes the same conversation
        with self.lock:
            for c in s.cards:
                if c["state"] in ACTIVE:
                    c["state"], c["finished"] = "cancelled", time.time()
                    self._card_changed(c)
        self._start(s, resume=True)
        return s.public()


def fork_carry_block(s, limit: int = 60_000) -> str:
    """The conversation a fork starts from, for a harness that cannot fork: each request and its answer, the latest
    kept when it is long. History parsing strips it again (strip_orbit_line), so the card shows the request alone."""
    from orbit.drivers.base import FORK_CLOSE, FORK_OPEN, strip_orbit_line
    turns = []
    for c in s.cards[: s.fork_carry]:
        q, a = strip_orbit_line(c.get("text") or "").strip(), (c.get("answer") or "").strip()
        if q:
            turns.append(f"## Request #{c['id']}\n{q}" + (f"\n\n## Answer\n{a}" if a else ""))
    body = "\n\n".join(turns)
    if len(body) > limit:
        body = "…(earlier turns left out)\n\n" + body[-limit:]
    return (f"{FORK_OPEN} This session is a fork of another: here is that conversation so far, for context (you did not "
            f"see it). Continue from it.\n\n{body}\n{FORK_CLOSE}")
