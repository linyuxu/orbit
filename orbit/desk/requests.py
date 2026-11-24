"""Requests to a session: send, steer, edit, withdraw, continue, stop, and mail between sessions."""

from __future__ import annotations

import copy
import time

from orbit.desk.session import ACTIVE, clip, mention_tag, Session, Stopping
from orbit.drivers.base import strip_orbit_line


class Requests:
    """Desk methods: requests to a session: send, steer, edit, withdraw, continue, stop, and mail between sessions."""

    def _card(self, s: Session, cid: int) -> dict:
        c = next((c for c in s.cards if c["id"] == cid), None)
        if not c:
            raise KeyError(f"{s.id}#{cid}")
        return c

    def send(self, sid: str, text: str, origin: dict | None = None, client_id: str | None = None, first: bool = False) -> dict:
        """A request (queued behind any running one). origin: where it came from (a plugin: {"plugin", "label",
        "url"?, "data"?}), kept with the card and shown on it, so the plugin can take the answer back there.
        client_id: the sender's own id for this request: the same id again returns the card it made (with
        "duplicate": true) instead of a second one, so a retried or repeated send runs once (kept across restarts).
        first: ahead of the requests waiting (the next to run), not behind them (the voice's "do this first")."""
        if not text.strip():
            raise ValueError("empty request")
        if client_id is not None and (not isinstance(client_id, str) or not 1 <= len(client_id) <= 200):
            raise ValueError("client_id: a string of 1 to 200 characters")
        if self.stopping_all:
            raise Stopping("Orbit's server is stopping (a restart): send it again in a moment")
        s = self.get(sid)
        self._ensure_loaded(s)
        with self.lock:
            had = s.client_ids.get(client_id) if client_id else None
            old = next((x for x in s.cards if x["id"] == had), None) if had is not None else None
            if old is not None:
                return {**copy.deepcopy(old), "duplicate": True}
            c = self._new_card(s, s.next_id(), text)
            if origin:
                c["origin"] = s.origins[str(c["id"])] = origin
            if client_id:
                c["client_id"] = client_id
                s.client_ids[client_id] = c["id"]
            at = next((i for i, x in enumerate(s.cards) if x["state"] == "queued"), None) if first else None
            if at is None:
                s.cards.append(c)
            else:
                s.cards.insert(at, c)  # (the queue is the cards' order: it runs before those waiting)
            self._card_changed(c)
            snap = copy.deepcopy(c)
        self.save()
        if s.catching_up:
            pass  # its handed-off process is being read up to now: the queue moves once it is (ready)
        elif self.hs_restarting:
            pass  # the Harness Server is restarting: it starts once that is back (restart_harness_server)
        elif not s.driver or not s.driver.alive():
            self._start(s, resume=True)  # starting → ready pumps the queue
        else:
            self._pump(s)
        self._session_changed(s)
        return snap

    def steer(self, sid: str, text: str, origin: dict | None = None, client_id: str | None = None) -> dict:
        """A message for the request that is running: it joins that turn (harnesses that can), shown on its card.
        Nothing running (or a harness that cannot): an ordinary request (with origin and client_id, as send's).
        Returns the card it went to."""
        if not text.strip():
            raise ValueError("empty message")
        s = self.get(sid)
        with self.lock:
            if self.stopping_all:  # (under the lock: none slips into a hand-off)
                raise Stopping("Orbit's server is stopping (a restart): send it again in a moment")
            c = s.current()
            if (c is None or c["state"] == "queued" or not s.driver or not s.driver.steers or not s.driver.alive()
                    or s.catching_up):
                pass
            else:
                try:
                    s.driver.steer(text)
                except (RuntimeError, OSError):  # the turn ended (or its process went) just now: a request instead
                    pass
                else:
                    c.setdefault("steers", []).append({"text": text, "time": time.time()})
                    self._card_changed(c)
                    return {**c, "steered": True}
        return {**self.send(sid, text, origin=origin, client_id=client_id), "steered": False}

    def edit(self, sid: str, cid: int, text: str) -> dict:
        """An edited request: a queued one changes in place; a finished one is replaced by a new request."""
        if not text.strip():
            raise ValueError("empty request")
        s = self.get(sid)
        self._ensure_loaded(s)
        with self.lock:
            c = self._card(s, cid)
            if c["state"] in ACTIVE:
                raise ValueError("that request is running: stop it first")
            if c["state"] == "queued":
                c["text"] = text
                self._card_changed(c)
                snap = copy.deepcopy(c)
            else:
                c["hidden"] = True
                if cid not in s.hidden:
                    s.hidden.append(cid)
                self._card_changed(c)
                snap = None
        self.save()
        return snap or self.send(sid, text)

    def add_note(self, sid: str, title: str, text: str, origin: dict | None = None) -> dict:
        """A note card: something to keep in the conversation that is not a request (a voice call's transcript). Nothing
        runs; it sits after the last request (id 4.001, 4.002 …, so request ids stay as the harness's history numbers
        them) and is kept with the session."""
        s = self.get(sid)
        self._ensure_loaded(s)
        with self.lock:
            after = s.next_id() - 1
            k = sum(1 for x in s.cards if x.get("kind") == "note" and int(x["id"]) == after)
            now = time.time()
            c = {**self._new_card(s, round(after + (k + 1) / 1000, 3), title, now), "kind": "note", "state": "note",
                 "answer": text, "started": now, "finished": now, "rev": 1}
            if origin:
                c["origin"] = origin
            s.cards.append(c)
            s.cards.sort(key=lambda x: x["id"])
            s.notes.append({k2: c[k2] for k2 in ("id", "text", "answer", "time", "origin") if k2 in c})
            self._card_changed(c)
            snap = copy.deepcopy(c)
        self.save()
        return snap

    def withdraw(self, sid: str, cid: int) -> None:
        """Take back a request still waiting in the queue."""
        s = self.get(sid)
        self._ensure_loaded(s)
        with self.lock:
            c = self._card(s, cid)
            if c["state"] != "queued":
                raise ValueError("only a request still waiting in the queue can be withdrawn")
            c["state"], c["hidden"] = "cancelled", True
            self._card_changed(c)
            self._session_changed(s)
        self.save()

    def continue_(self, sid: str, cid: int) -> dict:
        """A paused request (its agent went away, or ran out of turns): resume the agent and ask it to go on."""
        s = self.get(sid)
        c = next((x for x in s.cards if x["id"] == cid), None)
        if not c:
            raise ValueError(f"no request #{cid}")
        if c["state"] != "paused":
            raise ValueError(f"#{cid} is not paused")
        if not s.catching_up and not self.hs_restarting and (not s.driver or not s.driver.alive()):
            self._start(s, resume=True)
        with self.lock:
            c["continued"] = True
            self._card_changed(c)
        nxt = self.send(sid, f"Continue request #{cid} where you stopped.", origin=c.get("origin"))
        with self.lock:
            c["continued_by"] = nxt["id"]  # (a plugin waiting for #cid follows it there)
            self._card_changed(c)
        return nxt

    def wait_card(self, sid: str, cid: int, timeout: float) -> dict:
        """The card once it is final, following a paused request to the request that continues it (retries);
        or, after timeout seconds, as it is then. {"card", "final"}."""
        s = self.get(sid)
        self._ensure_loaded(s)
        end = time.time() + max(0.0, min(timeout, 3600.0))
        while True:
            with self.lock:
                c = next((x for x in s.cards if x["id"] == cid), None)
                if c is None:
                    raise KeyError(f"{sid}#{cid}")
                if c["state"] == "paused" and c.get("continued_by"):
                    cid = c["continued_by"]
                    continue
                retrying = c["state"] == "paused" and "retrying in" in (c.get("error") or "")
                if c["state"] in ("completed", "error", "cancelled", "paused") and not retrying:
                    return {"card": copy.deepcopy(c), "final": True}
                snap = copy.deepcopy(c)
            if time.time() >= end:
                return {"card": snap, "final": False}
            time.sleep(0.2)

    def stop(self, sid: str, keep_queue: bool = False) -> None:
        """Stop: the running request, and the queued ones with it; keep_queue: the running one only (the voice's
        "stop the running task": the requests waiting behind it go on)."""
        s = self.get(sid)
        with self.lock:
            if self.stopping_all:  # a Stop must not slip into a hand-off (the next server takes it)
                raise Stopping("Orbit's server is stopping (a restart): stop it again in a moment")
            # Drop queued requests first so Stop never lets the next one start.
            for c in ([] if keep_queue else s.cards):
                if c["state"] == "queued":
                    c["state"] = "cancelled"
                    self._card_changed(c)
            if s.driver:
                s.driver.stop()
        self.save()

    def mention(self, sid: str, source: str, text: str, agent: bool = False) -> dict:
        """Send `text` to session `sid` as a mention from session `source`.

        From the user (@@ in the input): the source, the user's words, the source's last 3 turns, and how to answer
        back (`orbit-agent mention`) only if the source needs the result. From an agent (`orbit-agent mention`): a note, not a
        task, that says not to mention back unless asked (no ping-pong)."""
        if not text.strip():
            raise ValueError("empty mention")
        src = self.get(source)
        if src.id == sid:
            raise ValueError("a session cannot mention itself")
        tag = mention_tag(src.name)
        if agent:
            body = (f"Note from {tag} (session {src.id}, another agent): {text.strip()}\n\n"
                    f"This is a note, not a task. Do not mention {tag} back unless it asks you to. "
                    f"Its recent turns: `orbit-agent read {src.id} --last 10`.")
        else:
            self._ensure_loaded(src)
            with self.lock:
                turns = [c for c in src.cards if c["state"] != "queued" and not c.get("hidden")][-3:]
                lines = []
                for c in turns:
                    lines.append(f"- #{c['id']} request: {clip(strip_orbit_line(c['text']), 300)}")
                    if c.get("answer"):
                        lines.append(f"  answer: {clip(c['answer'], 400)}")
            quoted = "\n".join("> " + line for line in text.strip().splitlines())
            body = (f"The user mentioned you from {tag} (session {src.id}) and wrote there:\n\n{quoted}\n\n"
                    f"Recent turns in {tag}:\n" + ("\n".join(lines) or "(none yet)") +
                    f"\nMore: `orbit-agent read {src.id} --last 10`.\n\n"
                    f"Answer here. Only if {tag} needs your result, tell it with `orbit-agent mention {src.id} \"…\"`.")
        return self.send(sid, body)
