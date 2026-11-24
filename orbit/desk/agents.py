"""What agents and the page ask of the desk: the user's context, opening and showing things, finding sessions, search."""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

from orbit.desk.session import mention_tag, Session


class Agents:
    """Desk methods: what agents and the page ask of the desk: the user's context, opening and showing things, finding sessions, search."""

    def set_ctx(self, ctx: dict) -> None:
        with self.lock:
            self.ui_ctx = {**ctx, "at": time.time()}

    def get_ctx(self) -> dict:
        with self.lock:
            return dict(self.ui_ctx)

    def by_name(self, sid: str | None) -> str:
        s = self.sessions.get(sid or "")
        return s.name if s else ""

    def _by_sid(self, sid: str | None) -> str:
        return sid if sid and sid in self.sessions else ""

    def open_for_user(self, path: str, line: int | None = None, side: bool = False, by: str = "") -> dict:
        p = os.path.realpath(os.path.expanduser(path))
        if not os.path.exists(p):
            raise ValueError(f"no such file: {p}")
        ev = {"t": "open", "path": p, "line": line, "side": side, "by": self.by_name(by), "sid": self._by_sid(by)}
        self.broadcast(ev)
        return ev

    def show(self, html: str, title: str = "", by: str = "") -> dict:
        """Store a page an agent made and show it in a side tab (served sandboxed at /show/<id>)."""
        if not html.strip():
            raise ValueError("nothing to show")
        self.show_dir.mkdir(parents=True, exist_ok=True)
        pid = uuid.uuid4().hex[:12]
        (self.show_dir / f"{pid}.html").write_text(html, encoding="utf-8")
        ev = {"t": "show", "id": pid, "url": f"/show/{pid}", "title": title.strip() or "Page", "by": self.by_name(by),
              "sid": self._by_sid(by)}  # (sid: the side tab goes with that session's tab, not whichever is in front)
        self.broadcast(ev)
        return ev

    def show_path(self, pid: str) -> Path | None:
        p = self.show_dir / f"{pid}.html"
        return p if pid.isalnum() and p.is_file() else None

    def find_session(self, ref: str, closed: bool = False) -> Session:
        """An open session (or with closed=True any session) by id, id prefix, or name (case-insensitive)."""
        with self.lock:
            open_ = [s for s in self.sessions.values() if closed or not s.closed]
        for s in open_:
            if s.id == ref:
                return s
        if ref.startswith("@@"):
            ref = ref[2:]
        named = [s for s in open_ if s.name.lower() == ref.lower()] or \
            [s for s in open_ if mention_tag(s.name)[2:].lower() == ref.lower()]
        if len(named) == 1:
            return named[0]
        pref = [s for s in open_ if len(ref) >= 4 and s.id.startswith(ref)]
        if len(pref) == 1:
            return pref[0]
        if len(named) > 1:
            raise ValueError(f"{len(named)} open sessions are called {ref!r}: use the id")
        raise KeyError(ref)

    def search(self, q: str, limit: int = 50) -> list[dict]:
        """Cards of every session (open or closed) whose request, answer or follow-up holds every term."""
        from orbit.workspace.notes import snippet
        terms = [t.lower() for t in q.split()]
        if not terms:
            return []
        out = []
        for s in list(self.sessions.values()):
            self._ensure_loaded(s)  # history comes from the harness's store (parsed once)
            with self.lock:
                cards = list(s.cards)
            for c in reversed(cards):
                parts = [("request", c["text"]), ("answer", c["answer"])] + [("follow-up", f) for f in c["followups"]]
                for where, text in parts:
                    low = (text or "").lower()
                    if all(t in low for t in terms):
                        out.append({"kind": "card", "sid": s.id, "name": s.name, "closed": s.closed, "cid": c["id"],
                                    "where": where, "snippet": snippet(text, terms[0]),
                                    "time": c.get("finished") or c.get("time") or 0})
                        break
        out.sort(key=lambda r: -(r["time"] or 0))
        return out[:limit]
