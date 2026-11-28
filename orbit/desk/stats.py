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

Built from the cards Orbit reads from each harness's transcript: no counting of its own, so the history is the
harness's, as far back as it keeps it.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

SOURCES = ("you", "voice", "agents", "schedule", "plugin", "auto")
DAYS_MAX = 400
FINAL = ("completed", "error", "cancelled", "paused")


def source(c: dict) -> str:
    o = c.get("origin") or {}
    text = c.get("text") or ""
    if c.get("kind") == "auto":
        return "auto"
    if o.get("label") == "Voice":
        return "voice"
    if o.get("kind") == "user-relay" or text.startswith("The user mentioned you from"):
        return "you"
    if o.get("plugin"):
        return "plugin"
    if text.startswith(('(schedule "', "(wake")):
        return "schedule"
    if text.startswith(("(from session ", "(from a terminal session", "Note from @@")):
        return "agents"
    return "you"


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
        out.append([round(float(t), 1), round(float(c.get("started") or t), 1),
                    round(float(end), 1) if end else None, st, source(c)])
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
                recs += [[len(sessions) - 1, *r] for r in rs if isinstance(r, list) and len(r) == 5]
    return {"now": now, "since": since, "days": days, "sources": SOURCES, "sessions": sessions, "records": recs,
            "partial": partial}
