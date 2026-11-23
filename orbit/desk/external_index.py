"""The conversations to resume that are no Orbit session (⌘O → Other sessions, Resume another session): an index
the server keeps per harness, so the dialog opens at once and a typed query searches all of them.

- A harness whose conversations are files (Claude Code, Codex: Driver.external_files) is read once; each open of the
  dialog then only looks at the files' modification times and reads again the ones that changed, newest first, for
  SCAN_BUDGET s; what is left is read in the background (the pages hear `external-sessions` when it is done).
- A plugin harness pushes its conversations to its `conversations` item source (services/plugin_items.py), or else
  its `--list-conversations` runs in the background when the dialog opens and its list is older than LIST_EVERY s.
  The dialog never waits for it: it shows what the index has, and redraws when the list comes in.

A query is matched here, against the whole index, with the page's matcher (fuzzy over the label and the rest, or 4+
characters of the id: finder.js fuzzy / idScore).
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Any, Callable

LIST_EVERY = 30.0  # s: a plugin harness's list is asked for again (in the background) when older
LIST_LIMIT = 1000  # rows asked of `--list-conversations`
SCAN_BUDGET = 0.25  # s an open of the dialog spends reading changed files; the rest is read in the background


def _harness_listed_by_files(cls) -> bool:
    from orbit.drivers.base import Driver
    return getattr(cls.external_files, "__func__", None) is not Driver.external_files.__func__


def _lists_conversations(cls) -> bool:
    from orbit.drivers.base import Driver
    return getattr(cls.external_sessions, "__func__", None) is not Driver.external_sessions.__func__


class ExternalIndex:
    def __init__(self, broadcast: Callable[[dict], None] | None = None):
        self.broadcast = broadcast
        self.lock = threading.Lock()
        self.files: dict[str, dict[str, tuple[float, int, dict | None]]] = {}  # harness → path → (mtime, size, row)
        self.rows: dict[str, list[dict]] = {}  # a listed harness → its last list
        self.listed: dict[str, float] = {}  # a listed harness → when it was last asked
        self.busy: set[str] = set()  # harnesses being read in the background

    # ------------------------------------------------------------------ reading the stores
    def refresh(self, drivers: dict[str, Any], files_only: bool = False) -> None:
        """What an open of the dialog does: the file stores looked at again (changed files read, briefly), the
        plugin harnesses' lists asked for in the background when old. Never waits for a plugin. (files_only: the
        server's start reads the file stores once, in the background, so the first open has them.)"""
        end = time.monotonic() + SCAN_BUDGET  # (for all of them together)
        for h, cls in list(drivers.items()):
            if _harness_listed_by_files(cls):
                self._scan(h, cls, end)
            elif _lists_conversations(cls) and not self._pushed(cls) and not files_only:
                self._ask(h, cls)

    def _take(self, h: str) -> bool:
        with self.lock:
            if h in self.busy:
                return False
            self.busy.add(h)
            return True

    def _done(self, h: str) -> None:
        with self.lock:
            self.busy.discard(h)

    def _scan(self, h: str, cls, end: float) -> None:
        if not self._take(h):
            return  # (read in the background now: the index as it is)
        try:
            listing = cls.external_files() or []
        except Exception as e:  # noqa: BLE001 - one harness's store must not hide the others
            print(f"orbit: {h}: listing conversations: {e}", flush=True)
            self._done(h)
            return
        old = self.files.get(h, {})
        new: dict[str, tuple[float, int, dict | None]] = {}
        todo = []
        for path, mtime, size in listing:
            hit = old.get(path)
            if hit and hit[0] == mtime and hit[1] == size:
                new[path] = hit
            else:
                todo.append((path, mtime, size))
                if hit:
                    new[path] = hit  # (its old row until it is read again)
        while todo and time.monotonic() < end:
            path, mtime, size = todo.pop(0)
            new[path] = (mtime, size, self._row(h, cls, path))
        with self.lock:
            self.files[h] = new
        if not todo:
            self._done(h)
            return

        def rest() -> None:
            try:
                got = {path: (mtime, size, self._row(h, cls, path)) for path, mtime, size in todo}
                with self.lock:
                    self.files.setdefault(h, {}).update(got)
            finally:
                self._done(h)
            self._tell()
        threading.Thread(target=rest, name=f"index {h}", daemon=True).start()

    @staticmethod
    def _row(h: str, cls, path: str) -> dict | None:
        try:
            x = cls.external_row(path)
        except Exception:  # noqa: BLE001 - a file it cannot read is not listed
            return None
        return {**x, "harness": x.get("harness") or h} if x else None

    @staticmethod
    def _pushed(cls) -> bool:
        from orbit.services import plugin_items
        return bool(getattr(cls, "plugin", "")) and cls.plugin in plugin_items.conversations()

    def _ask(self, h: str, cls) -> None:
        with self.lock:
            if h in self.busy or time.time() - self.listed.get(h, 0) < LIST_EVERY:
                return
            self.busy.add(h)
            self.listed[h] = time.time()

        def run() -> None:
            try:
                rows = (cls.list_conversations(LIST_LIMIT) if hasattr(cls, "list_conversations")
                        else cls.external_sessions(LIST_LIMIT, set()))
            except Exception as e:  # noqa: BLE001
                print(f"orbit: {h}: listing conversations: {e}", flush=True)
                rows = None
            with self.lock:
                if rows is not None:  # (a failure keeps the list it had)
                    self.rows[h] = [{**x, "harness": x.get("harness") or h} for x in rows]
                self.listed[h] = time.time()
                self.busy.discard(h)
            if rows is not None:
                self._tell()
        threading.Thread(target=run, name=f"list {h}", daemon=True).start()

    def _tell(self) -> None:
        if self.broadcast:
            try:
                self.broadcast({"t": "external-sessions"})
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ the rows
    def all(self, drivers: dict[str, Any]) -> list[dict]:
        """Every conversation the index has, newest first (before skipping Orbit's own)."""
        from orbit.services import plugin_items
        pushed = plugin_items.conversations()
        out: list[dict] = []
        for h, cls in list(drivers.items()):
            if _harness_listed_by_files(cls):
                with self.lock:
                    rows = [r for _, _, r in self.files.get(h, {}).values() if r]
                try:
                    extra = cls.external_extra() or {}
                except Exception:  # noqa: BLE001
                    extra = {}
                out += [{**r, **extra.get(r["id"], {})} if r["id"] in extra else r for r in rows]
            elif getattr(cls, "plugin", "") in pushed:
                out += [pushed_row(h, cls, x) for x in pushed[cls.plugin] if x.get("harness", h) == h]
            else:
                with self.lock:
                    out += list(self.rows.get(h, []))
        return sorted(out, key=lambda x: -x["time"])

    def pending(self) -> list[str]:
        with self.lock:
            return sorted(self.busy)


def pushed_row(h: str, cls, x: dict) -> dict:
    """A pushed item of a plugin's `conversations` source as an external_sessions row."""
    return {"harness": h, "id": x["key"], "cwd": x.get("cwd", ""), "first": x.get("detail", "")[:300],
            "name": "", "title": x["label"][:200], "time": x.get("time") or 0, "live": None,
            **({"match": x["match"]} if x.get("match") else {})}


# ---------------------------------------------------------------------- the page's matcher (finder.js, core.js)
_BOUNDARY = set(" \t\n/_-.·")


def fuzzy(q: str, text: str) -> float:
    """core.js fuzzy: q's characters in order in text, scored (runs and word starts count more); -1: no match."""
    if not q:
        return 0
    q, t = q.lower(), str(text or "").lower()
    score, last, k = 0.0, -2, -1
    for c in q:
        k = t.find(c, k + 1)
        if k < 0:
            return -1
        score += 1 + (3 if k == last + 1 else 0) + (2 if k == 0 or t[k - 1] in _BOUNDARY else 0)
        last = k
    return score - len(t) * 0.01


def id_score(q: str, ids: list[str]) -> float:
    """finder.js idScore: 4+ characters of an id (its start ranks first), ahead of every match by name."""
    q = re.sub(r"^.*[/~]", "", re.sub(r"#.*$", "", q.lower()))
    if len(q) < 4 or not re.fullmatch(r"[0-9a-z_-]+", q):
        return -1
    best = -1
    for i in ids:
        k = str(i or "").lower().find(q)
        if k >= 0:
            best = max(best, 100000 if k == 0 else 50000)
    return best


def _tilde(p: str) -> str:
    home = os.path.expanduser("~")
    return "~" + p[len(home):] if p == home or p.startswith(home + "/") else p


def label_of(x: dict) -> str:
    return (x.get("name") or x.get("title") or " ".join(str(x.get("first") or "").split()))[:100]


def score(q: str, x: dict, harness_label: str = "") -> float:
    """How well a row matches q, as the page scores it (finder.js OTHER_TAB): -1 when it does not."""
    first = " ".join(str(x.get("first") or "").split())
    match = " ".join(str(v) for v in (x.get("name"), x.get("title"), _tilde(str(x.get("cwd") or "")), x.get("machine"),
                                       harness_label, first, x.get("match")) if v)
    return max(id_score(q, [x.get("id", "")]), fuzzy(q, label_of(x)), fuzzy(q, match) - 1)


def search(rows: list[dict], q: str, labels: dict[str, str], limit: int) -> list[dict]:
    """The rows that match q, best first (then newest), each with its `score`."""
    hits = []
    for x in rows:
        sc = score(q, x, labels.get(x.get("harness", ""), ""))
        if sc >= 0:
            hits.append({**x, "score": round(sc, 3)})
    hits.sort(key=lambda x: (-x["score"], -x["time"]))
    return hits[:limit]
