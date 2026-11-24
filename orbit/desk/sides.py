"""A session's side panel, kept by the server like its conversation: the tabs beside it and the one shown.

Every client (the page, the phone, a page on another machine) shows the same tabs and changes them here; each change
goes to all of them as {"t": "side", "sid", "side": {"items", "i", "rev"}}. What a screen does with them (the panel
hidden, its width) stays that screen's own.

An item is the spec of what a side tab shows (the page's side.js draws them): {"id", "kind", …} with kind
  web       an agent's page (orbit-agent show): url, title
  file      a file: path, line?
  review    ± Review of a folder: root, sid
  session   another session's conversation: sid
  schedules, calendar   Orbit's own views
  pview     a plugin's view: path, title
Kept in <state>/side.json ({sid: state}); SIDE_KEPT items each.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

KINDS = {"web", "file", "review", "session", "schedules", "calendar", "pview"}
SIDE_KEPT = 20
ID_RE = re.compile(r"^[\w-]{1,40}$")
SPEC_MAX = 4000  # characters of JSON


def same(a: dict, b: dict) -> bool:
    """The same side tab (as the page's sameSide): an agent's page by its URL, anything else by kind and target."""
    if a.get("kind") != b.get("kind"):
        return False
    if a.get("kind") == "web":
        return a.get("url") == b.get("url")
    return all((a.get(k) or "") == (b.get(k) or "") for k in ("sid", "path", "root"))


def clean(spec: Any) -> dict:
    """A side tab's spec as kept: ValueError when it is not one."""
    if not isinstance(spec, dict) or spec.get("kind") not in KINDS:
        raise ValueError(f"a side tab is {{kind: {', '.join(sorted(KINDS))}, …}}")
    out = {k: v for k, v in spec.items() if isinstance(k, str) and (v is None or isinstance(v, (str, int, float, bool)))}
    if len(json.dumps(out)) > SPEC_MAX:
        raise ValueError("that side tab says too much")
    if not (isinstance(out.get("id"), str) and ID_RE.match(out["id"])):
        out["id"] = uuid.uuid4().hex[:10]
    return out


class Sides:
    """Desk methods: a session's side panel (see the module)."""

    def _sides_file(self) -> Path:
        return self.state_dir / "side.json"

    def _sides_read(self) -> dict:
        try:
            d = json.loads(self._sides_file().read_text())
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _sides_write(self, d: dict) -> None:
        f = self._sides_file()
        tmp = f.with_name(f"side.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(d))
        os.replace(tmp, f)

    def _side_of(self, d: dict, sid: str) -> dict:
        st = d.get(sid)
        if not isinstance(st, dict) or not isinstance(st.get("items"), list):
            st = {"items": [], "i": 0, "rev": 0}
        return st

    def side_state(self, sid: str) -> dict:
        """{"items", "i", "rev"}: its side tabs and the one shown. An agent's page whose file is gone is left out."""
        self.get(sid)  # (KeyError: no such session)
        with self.lock:
            st = self._side_of(self._sides_read(), sid)
        shown = st["items"][st["i"]]["id"] if 0 <= int(st.get("i") or 0) < len(st["items"]) else None
        items = [x for x in st["items"] if x.get("kind") != "web" or not str(x.get("url", "")).startswith("/show/")
                 or self.show_path(str(x["url"])[6:])]
        i = next((n for n, x in enumerate(items) if x.get("id") == shown), 0)
        return {"items": items, "i": i, "rev": st.get("rev", 0)}

    def side_op(self, sid: str, op: str, spec: Any = None, item_id: str = "") -> dict:
        """open (spec: shown, added after the one shown unless it is there already: then that one, its line or title
        updated), close (id), select (id), clear. The new state, told to every client."""
        self.get(sid)
        with self.lock:
            d = self._sides_read()
            st = self._side_of(d, sid)
            items, i = list(st["items"]), int(st.get("i") or 0)
            if op == "open":
                sp = clean(spec)
                k = next((n for n, x in enumerate(items) if same(x, sp)), -1)
                if k >= 0:
                    items[k] = {**items[k], **{key: v for key, v in sp.items() if key != "id"}}
                    i = k
                else:
                    i = i + 1 if items else 0
                    items.insert(i, {**sp, "t": time.time()})
                    while len(items) > SIDE_KEPT:  # the oldest of the others goes
                        drop = min((n for n in range(len(items)) if n != i), key=lambda n: items[n].get("t", 0))
                        items.pop(drop)
                        i -= drop < i
            elif op in ("close", "select"):
                k = next((n for n, x in enumerate(items) if x.get("id") == item_id), -1)
                if k < 0:
                    raise ValueError(f"no side tab {item_id!r}")
                if op == "select":
                    i = k
                else:
                    items.pop(k)
                    i = i - 1 if k < i else min(i, len(items) - 1)
            elif op == "clear":
                items, i = [], 0
            else:
                raise ValueError(f"no such op: {op} (open, close, select, clear)")
            new = {"items": items, "i": max(0, i), "rev": int(st.get("rev") or 0) + 1}
            if items:
                d[sid] = new
            else:
                d.pop(sid, None)
            self._sides_write(d)
        self.broadcast({"t": "side", "sid": sid, "side": new})
        return new
