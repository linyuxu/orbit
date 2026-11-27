"""The user's own words, passed on to another session (`orbit-agent relay SESSION TEXT`).

The user asks an agent ("send 'restart now' to trainer"); the agent relays those words. The receiving session gets
the words alone (no "(from session …)" line), and its card says where they came from, outside the text:
origin {"kind": "user-relay", "from": {...}, "proof": {...}}. Only this module writes that origin: the API's
generic origin (handler._origin: label, url, data, plugin) can never carry a "kind".

What is checked: the words must be the user's own, found in what this server itself holds:
  voice  a voice call's request, from the user's transcript as GPT Live sent it to this server (Live.heard): no API
         writes it, so an agent cannot make words up and pass them off as the user's
  typed  a request the user sent in the relaying session (no origin, not an agent's "(from …)" mail): as trusted as
         any message typed in Orbit's input (a local process could post one through the raw API, as it always could)
The trust boundary, plainly: one Mac user's processes share Orbit's local API and its files, so a process that
writes Orbit's state files or drives the UI can do anything the user can; this check keeps an agent's own wording
(or a typo of the user's) from passing as the user's through Orbit's API and tools, not a hostile local program.

Another machine's session: this server asks that one (POST /api/relay/forward, with its token). Words this server
found (proof) go with the request, marked verified_by this machine; otherwise that server looks in what it holds
(a voice call there whose session is this one's, seen there as NAME~ID). The same words, source, proof and target
make the same client id: a retried relay delivers once.
"""

from __future__ import annotations

import hashlib
import re
import socket
import time

WINDOW_S = 3600   # the user's words count for an hour
CARDS_LOOKED = 40  # the relaying session's latest requests looked at
MAX_TEXT = 8000


def norm(text: str) -> str:
    """Spaces as one, ends trimmed (a transcript and a quote differ there, never in words)."""
    return " ".join(str(text or "").split())


def _same_session(a: str, b: str) -> bool:
    """The same session as two servers name it: ID here, NAME~ID on the server that reaches this one."""
    return a == b or a.endswith("~" + b) or b.endswith("~" + a)


def find_proof(desk, src: str, text: str, now: float | None = None) -> dict | None:
    """Where `text` is the user's own words, said or typed in session `src` within the hour; None if nowhere."""
    want, now = norm(text), now or time.time()
    if not want:
        return None
    live = getattr(desk, "live", None)
    for h in reversed(list(getattr(live, "heard", []) or [])):  # the voice first: the strongest
        if now - h["at"] <= WINDOW_S and _same_session(h["sid"], src) and want in norm(h["words"]):
            return {"kind": "voice", "call": h["call"], "delegation": h["delegation"], "at": h["at"]}
    s = desk.sessions.get(src)
    if s is None:
        return None
    from orbit.drivers.base import strip_orbit_line
    with desk.lock:
        cards = [dict(c) for c in s.cards[-CARDS_LOOKED:]]
    for c in reversed(cards):
        if now - (c.get("time") or 0) > WINDOW_S or c.get("origin") or c.get("hidden") or c.get("kind") in ("auto", "note"):
            continue
        body = strip_orbit_line(c.get("text") or "")
        if body.lstrip().startswith("(from "):  # (another session's or a terminal's mail: not the user's)
            continue
        if want in norm(body):
            return {"kind": "typed", "card": c["id"], "at": c.get("time")}
    return None


def client_id(src: str, target: str, text: str, proof: dict) -> str:
    key = f"{src}|{target}|{norm(text)}|{proof.get('kind')}|{proof.get('card') or proof.get('delegation') or ''}"
    return "relay:" + hashlib.sha1(key.encode()).hexdigest()[:24]


def deliver(desk, target: str, text: str, src: dict, proof: dict, verified_by: str = "", cid: str = "") -> dict:
    """The words, alone, as a request in `target` (this server's session); where they came from on the card."""
    who = src.get("name") or "another session"
    origin = {"kind": "user-relay", "label": f"You, via {who}" + (f" on {src['machine']}" if src.get("machine") else ""),
              "from": {k: v for k, v in src.items() if v}, "proof": proof}
    if verified_by:
        origin["verified_by"] = verified_by
    return desk.send(target, text, origin=origin, client_id=cid or client_id(src.get("sid", ""), target, text, proof))


def check_text(text) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("nothing to relay")
    if len(text) > MAX_TEXT:
        raise ValueError(f"the words are {MAX_TEXT} characters at most")
    return text


NOT_THEIRS = ("not the user's own words: relay only what the user said (in a voice call to this session) or typed here, "
              "word for word; for your own message use orbit-agent send")


def host() -> str:
    return re.sub(r"\..*$", "", socket.gethostname())
