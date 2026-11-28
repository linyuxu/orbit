"""A group's statistics for its home page (the Stats, Activity and Agent time widgets, web/src/homestats.js).

Every request of the group's sessions (its subgroups' too, closed ones included; another machine's from that server)
as a compact record [session index, asked at, started, finished or None, state, source]; the page buckets them by its
own local day, so a day is the user's wherever a server runs. A source says who asked:

  you       typed here (or the user's words relayed by another session, or an @@ mention of the user's)
  voice     a voice call's request
  agents    another session or a terminal agent (orbit-agent send)
  schedule  a schedule or a wake (orbit-agent schedule / wake)
  plugin    a plugin (a chat, a webhook)
  auto      the agent's own turn (a background task's report)

A request from another agent carries its sender as a sixth field [sid, name, machine] (the card's origin when
orbit-agent sent it with one, else the name in its "(from session …)" prefix): the group's collaboration ("edges", the
most frequent sender → receiver pairs of the last EDGE_DAYS days where either end is in the group).

Built from the cards Orbit reads from each harness's transcript: no counting of its own, so the history is the
harness's, as far back as it keeps it.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

SOURCES = ("you", "voice", "agents", "schedule", "plugin", "auto")
DAYS_MAX = 400
FINAL = ("completed", "error", "cancelled", "paused")
EDGE_DAYS, EDGES_MAX = 30, 8
PREFIX = re.compile(r'\(from session "([^"]*)"(?: on ([\w.-]+))?\)')


def source(c: dict) -> str:
    o = c.get("origin") or {}
    text = c.get("text") or ""
    if c.get("kind") == "auto":
        return "auto"
    if o.get("label") == "Voice":
        return "voice"
    if o.get("kind") == "agent":
        return "agents"
    if o.get("kind") == "user-relay" or text.startswith("The user mentioned you from"):
        return "you"
    if o.get("plugin"):
        return "plugin"
    if text.startswith(('(schedule "', "(wake")):
        return "schedule"
    if text.startswith(("(from session ", "(from a terminal session", "Note from @@")):
        return "agents"
    return "you"


def sender(c: dict) -> list | None:
    """Another agent's request: who sent it, [sid ("" when only its name is known), name, machine]."""
    o = c.get("origin") or {}
    if o.get("kind") == "agent":
        f = o.get("from") or {}
        return [str(f.get("sid") or ""), str(f.get("name") or ""), str(f.get("machine") or "")]
    m = PREFIX.match(c.get("text") or "")
    return ["", m.group(1), m.group(2) or ""] if m else None


def records(desk, sid: str, since: float) -> list[list]:
    """This server's session: its requests asked since `since` (epoch s)."""
    s = desk.get(sid)
    desk._ensure_loaded(s)
    with desk.lock:
        cards = [dict(c) for c in s.cards]
    out = []
    for c in cards:
        if c.get("kind") == "note":
            continue
        t = c.get("time") or c.get("started")
        if not t or t < since:
            continue
        st = str(c.get("state") or "")
        end = c.get("finished") if st in FINAL else None
        src = source(c)
        r = [round(float(t), 1), round(float(c.get("started") or t), 1), round(float(end), 1) if end else None, st, src]
        who = sender(c) if src == "agents" else None
        out.append(r + [who] if who else r)
    return out


def subtree(desk, gid: str) -> set[str]:
    ids, grew = {gid}, True
    while grew:
        grew = False
        for g in desk.groups:
            if g.get("parent") in ids and g["id"] not in ids:
                ids.add(g["id"])
                grew = True
    return ids


def group(desk, gid: str, days: int) -> dict:
    """The group's sessions ({id, name, harness, remote?}) and their requests of the last `days` days."""
    days = max(1, min(int(days), DAYS_MAX))
    now = time.time()
    since = now - days * 86400
    ids = subtree(desk, gid)
    sessions, recs, partial = [], [], []
    with desk.lock:
        local = [s for s in desk.sessions.values() if s.group in ids and not s.fleet]
    for s in local:
        try:
            rs = records(desk, s.id, since)
        except Exception:  # noqa: BLE001 - a transcript that cannot be read: the others still count
            continue
        sessions.append({"id": s.id, "name": s.name, "harness": s.harness})
        recs += [[len(sessions) - 1, *r] for r in rs]
    fed = desk.federation
    if fed is not None:
        from orbit.machines.federation import split
        far = [x for x in fed.list() if x.get("group") in ids]

        def get(x: dict):
            name, rsid = split(x["id"])
            code, d = fed.call(name, "GET", f"/api/sessions/{rsid}/stats?since={since:.0f}", rewrite=False, timeout=10)
            return x, name, (d or {}).get("records") if code == 200 and isinstance(d, dict) else None

        with ThreadPoolExecutor(max_workers=8) as pool:
            for x, name, rs in pool.map(get, far):
                if rs is None:
                    if name not in partial:
                        partial.append(name)
                    continue
                sessions.append({"id": x["id"], "name": x.get("name", ""), "harness": x.get("harness", ""), "remote": name})
                recs += [[len(sessions) - 1, *r] for r in rs if isinstance(r, list) and len(r) in (5, 6)]  # (an older server: 5)
    edges = collab(desk, ids, sessions, recs, now - EDGE_DAYS * 86400)
    for r in recs:  # (the sender went into the edges; the page gets the five fields)
        del r[6:]
    return {"now": now, "since": since, "days": days, "sources": SOURCES, "sessions": sessions, "records": recs,
            "partial": partial, "edges": edges}


def collab(desk, ids: set[str], sessions: list[dict], recs: list[list], cut: float) -> list[dict]:
    """The most frequent sender → receiver pairs since `cut`: requests to the group's sessions (its records), and from
    them to this server's open sessions elsewhere. A sender is named by its session where one is known (here, or another
    machine's as NAME~ID), else by the name its prefix gave."""
    known: dict[str, tuple[str, str]] = {}  # an id as its own server writes it → (the id here, its name)
    with desk.lock:
        for s in desk.sessions.values():
            known[s.id] = (s.id, s.name)
    for x in (desk.federation.list() if desk.federation is not None else []):
        known[x["id"].split("~", 1)[-1]] = (x["id"], x.get("name", ""))

    by_name: dict[str, list] = {}
    for sid_, nm in known.values():
        by_name.setdefault(nm, []).append(sid_)

    def who(snd: list) -> tuple[str, str]:
        hit = known.get(snd[0]) if snd[0] else None
        if hit:
            return hit
        same = by_name.get(snd[1]) or []
        return (same[0], snd[1]) if len(same) == 1 else ("", snd[1])  # (by its name, when only one session has it)

    n: Counter = Counter()
    for r in recs:
        if len(r) > 6 and r[6] and r[1] >= cut:
            to = sessions[r[0]]
            n[(who(r[6]), (to["id"], to["name"]))] += 1
    mine = {s["id"] for s in sessions}
    with desk.lock:
        elsewhere = [s for s in desk.sessions.values() if s.group not in ids and not s.fleet and not s.closed]
    for s in elsewhere:
        try:
            desk._ensure_loaded(s)
        except Exception:  # noqa: BLE001
            continue
        with desk.lock:
            cards = [dict(c) for c in s.cards if (c.get("time") or 0) >= cut]
        for c in cards:
            snd = sender(c) if source(c) == "agents" else None
            if not snd:
                continue
            frm = who(snd)
            if frm[0] in mine:  # (only a sender known to be the group's: a bare name can be another session's)
                n[(frm, (s.id, s.name))] += 1
    return [{"from": {"sid": f[0], "name": f[1]}, "to": {"sid": t[0], "name": t[1]}, "n": k}
            for (f, t), k in n.most_common(EDGES_MAX)]
