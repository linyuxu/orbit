"""The tab layout, kept by the server like the sessions: every screen has the same tabs.

What the page arranges beside the sessions: its file tabs (files, notes, a plugin's views, Schedules, Calendar) per
group, the order of the tab row per group (sessions and files mixed), and a file tab's side tabs (a session's side
tabs are desk/sides.py's). One JSON document, {"ftabs", "order", "sides"}, written whole by a page (with the revision
it started from: a page behind the others gets 409 and the current layout instead) and sent to every page as
{"t": "layout", "layout", "rev"}. Which tab a screen shows, its scroll, a side panel put away: that screen's own.
Kept in <state>/layout.json.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

LAYOUT_MAX = 400_000  # characters of JSON
KEYS = ("ftabs", "order", "sides")


class Stale(Exception):
    """A layout written from a revision that is not the current one."""

    def __init__(self, current: dict):
        super().__init__("the tabs changed meanwhile")
        self.current = current


class Layout:
    """Desk methods: the tab layout (see the module)."""

    def _layout_file(self) -> Path:
        return self.state_dir / "layout.json"

    def layout(self) -> dict:
        """{"layout": {"ftabs", "order", "sides"}, "rev"}; rev 0: none kept yet."""
        try:
            d = json.loads(self._layout_file().read_text())
            if isinstance(d, dict) and isinstance(d.get("layout"), dict):
                return {"layout": d["layout"], "rev": int(d.get("rev") or 0)}
        except (OSError, ValueError):
            pass
        return {"layout": {k: {} for k in KEYS}, "rev": 0}

    def set_layout(self, layout: object, base: object) -> dict:
        """The layout a page arranged, from revision `base`. ValueError: not one; Stale: the tabs changed meanwhile."""
        if not isinstance(layout, dict) or any(not isinstance(layout.get(k, {}), dict) for k in KEYS):
            raise ValueError('a layout is {"ftabs": {}, "order": {}, "sides": {}}')
        clean = {k: layout.get(k, {}) for k in KEYS}
        text = json.dumps(clean)
        if len(text) > LAYOUT_MAX:
            raise ValueError("that layout is too large")
        with self.lock:
            cur = self.layout()
            if not isinstance(base, int) or base != cur["rev"]:
                raise Stale(cur)
            new = {"layout": clean, "rev": cur["rev"] + 1}
            f = self._layout_file()
            tmp = f.with_name(f"layout.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(new))
            os.replace(tmp, f)
        self.broadcast({"t": "layout", **new})
        return new
