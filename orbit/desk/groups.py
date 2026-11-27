"""The left panel's groups: a tree, like folders (a group holds sessions, file tabs and subgroups).

Kept as one ordered list of {"id", "name", "parent"?}: a group's children are the groups whose parent is its id, in
list order (the top: no parent). GROUP_DEPTH levels at most ("Work / Orbit / Release"). A name is unique among its
siblings (case and every space ignored), and Fleet is Orbit's own at the top. Deleting a group moves
what it held (its sessions, its subgroups) up to its parent, so nothing is lost.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from orbit.desk.session import FLEET_GROUP

GROUP_DEPTH = 3
KEPT = ("id", "name", "parent", "icon", "color", "cover", "home", "pins")
# A group's home page (its first tab): widgets on a grid, in order. A widget is {"id", "kind", "span", "hidden"?, "path"?}:
# kind one of WIDGETS; span its width in sixths (2, 3, 4, 6: a third, a half, two thirds, the row); "path" a Markdown
# note's (kind "md"). Without a layout of its own a group's home shows DEFAULT_HOME.
WIDGETS = ("sessions", "groups", "tabs", "schedules", "activity", "pins", "md")
SPANS = (2, 3, 4, 6)
DEFAULT_HOME = [{"id": "sessions", "kind": "sessions", "span": 6}, {"id": "groups", "kind": "groups", "span": 6},
                {"id": "tabs", "kind": "tabs", "span": 3}, {"id": "schedules", "kind": "schedules", "span": 3},
                {"id": "activity", "kind": "activity", "span": 6}]
HOME_MAX = 40  # widgets
WIDGET_H = (60, 2000)  # a widget's body height, when set (px)
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def check_style(key: str, v: object) -> str:
    """A group's icon (an emoji or a short text, or img:<path> of an uploaded picture), color (#rrggbb) or cover (a
    picture's path); "" clears it. ValueError when it is not one."""
    if not isinstance(v, str):
        raise ValueError(f"{key}: a string")
    v = v.strip()
    if not v:
        return ""
    if key == "color":
        if not COLOR.match(v):
            raise ValueError("color: #rrggbb")
        return v.lower()
    if key == "icon" and not v.startswith("img:"):
        if len(v) > 8 or any(c in v for c in "<>\"\'&"):
            raise ValueError("icon: an emoji or a few letters (or a picture)")
        return v
    path = v[4:] if key == "icon" else v
    if not path.startswith("/") or len(path) > 1000 or "\n" in path:
        raise ValueError(f"{key}: a picture's absolute path")
    return v


def check_home(widgets: object) -> list[dict]:
    """A home page's widgets, checked (ValueError: what is wrong)."""
    if not isinstance(widgets, list) or len(widgets) > HOME_MAX:
        raise ValueError(f"widgets: a list of {HOME_MAX} at most")
    out, ids = [], set()
    for w in widgets:
        if not isinstance(w, dict) or w.get("kind") not in WIDGETS:
            raise ValueError(f"a widget is {{kind: {', '.join(WIDGETS)}, span, …}}")
        wid = str(w.get("id") or "")
        if not re.fullmatch(r"[\w-]{1,40}", wid) or wid in ids:
            raise ValueError("a widget needs its own id")
        ids.add(wid)
        x = {"id": wid, "kind": w["kind"], "span": w.get("span") if w.get("span") in SPANS else 6}
        if w.get("hidden"):
            x["hidden"] = True
        if w["kind"] == "md":
            path = w.get("path")
            if not isinstance(path, str) or not path.startswith("/") or len(path) > 1000:
                raise ValueError("a Markdown widget names its note (an absolute path)")
            x["path"] = path
        h = w.get("h")  # its body's height in px (resized with the mouse); none: as tall as what it shows
        if h not in (None, 0, ""):
            if not isinstance(h, (int, float)) or isinstance(h, bool) or not WIDGET_H[0] <= h <= WIDGET_H[1]:
                raise ValueError(f"widget {wid}: h is {WIDGET_H[0]}–{WIDGET_H[1]} (px)")
            x["h"] = int(round(h))
        if isinstance(w.get("title"), str) and w["title"].strip():  # (its own title, any kind)
            x["title"] = " ".join(w["title"].split())[:80]
        # Its own look: an icon (as a group's), a colour that tints the card, a background picture.
        for key, as_ in (("icon", "icon"), ("color", "color"), ("bg", "cover")):
            if w.get(key):
                try:
                    v = check_style(as_, w[key])
                except ValueError as e:
                    raise ValueError(f"widget {wid}: {str(e).replace('cover', 'bg', 1)}") from None
                if v:
                    x[key] = v
        out.append(x)
    return out


def tidy(groups: list[dict]) -> list[dict]:
    """Groups as loaded, made a valid tree: a parent that is missing, itself, or would make a cycle or a tree deeper
    than GROUP_DEPTH is dropped (that group goes to the top)."""
    out = [{k: v for k, v in g.items() if k in KEPT} for g in groups]
    ids = {g["id"] for g in out}
    by = {g["id"]: g for g in out}
    for g in out:
        p = g.get("parent")
        if not p or p not in ids or p == g["id"]:
            g.pop("parent", None)
    for g in out:  # (cycles: walk up; a walk that comes back, or runs too deep, cuts at this group)
        seen, x, n = {g["id"]}, g, 1
        while x.get("parent"):
            x = by[x["parent"]]
            n += 1
            if x["id"] in seen or n > GROUP_DEPTH:
                g.pop("parent", None)
                break
            seen.add(x["id"])
    return out


class Groups:
    """Desk methods: the left panel's groups (see the module)."""

    def _group_id(self, gid: str) -> str:
        if gid == FLEET_GROUP:
            return gid
        return gid if gid in {g["id"] for g in self.groups} else self.groups[0]["id"]

    def _groups_changed(self) -> None:
        self.save()
        self.broadcast({"t": "groups", "groups": self.all_groups()})

    # ---------- the tree
    def _children(self, parent: str | None) -> list[dict]:
        return [g for g in self.groups if (g.get("parent") or None) == (parent or None)]

    def _depth(self, gid: str) -> int:
        """1 for a top group, 2 for its child …"""
        n, g = 1, self._group(gid)
        while g.get("parent"):
            g, n = self._group(g["parent"]), n + 1
        return n

    def _height(self, gid: str) -> int:
        """1 for a group without subgroups, 2 with children only …"""
        return 1 + max((self._height(c["id"]) for c in self._children(gid)), default=0)

    def _descendants(self, gid: str) -> set[str]:
        out: set[str] = set()
        for c in self._children(gid):
            out |= {c["id"]} | self._descendants(c["id"])
        return out

    def _check_name(self, name: str, gid: str = "", parent: str | None = None) -> str:
        """The name, trimmed (inner runs of spaces as one): ValueError when empty or a sibling has it (case and every
        space ignored: "Data Pipeline" is "DataPipeline"); at the top, Fleet is Orbit's own."""
        name = " ".join(name.split())
        if not name:
            raise ValueError("a group needs a name")
        if "/" in name or "›" in name:
            raise ValueError("a group's name has no / or › (they separate a subgroup's path)")
        key = "".join(name.split()).casefold()
        if not parent and key == "fleet":
            raise ValueError(f"“{name}” is Orbit's own: there is a group named that already")
        same = next((g for g in self._children(parent) if g["id"] != gid and "".join(g["name"].split()).casefold() == key), None)
        if same:
            raise ValueError(f"there is a group named “{same['name']}” there already")
        return name

    def group_create(self, name: str, parent: str = "") -> dict:
        with self.lock:
            parent = parent or ""
            if parent:
                self._group(parent)
                if self._depth(parent) >= GROUP_DEPTH:
                    raise ValueError(f"groups nest {GROUP_DEPTH} levels deep at most")
            g = {"id": "g-" + uuid.uuid4().hex[:8], "name": self._check_name(name, parent=parent or None)}
            if parent:
                g["parent"] = parent
                kids = self._children(parent)  # (the new child goes after its siblings, as a new top group does)
                at = max((self.groups.index(x) for x in kids), default=self.groups.index(self._group(parent))) + 1
                self.groups.insert(at, g)
            else:
                self.groups.append(g)
        self._groups_changed()
        return g

    def _group(self, gid: str) -> dict:
        g = next((g for g in self.groups if g["id"] == gid), None)
        if not g:
            raise KeyError(gid)
        return g

    def group_rename(self, gid: str, name: str) -> dict:
        with self.lock:
            g = self._group(gid)
            g["name"] = self._check_name(name, gid, g.get("parent"))
        self._groups_changed()
        return g

    def group_move(self, gid: str, delta: int) -> None:
        """Up (delta < 0) or down among its siblings."""
        with self.lock:
            g = self._group(gid)
            sib = self._children(g.get("parent"))
            i = sib.index(g)
            j = max(0, min(len(sib) - 1, i + delta))
            if j != i:  # (to place j among its siblings: before the one there now, or after the last)
                rest = [x for x in sib if x is not g]
                self.groups.remove(g)
                at = self.groups.index(rest[j]) if j < len(rest) else self.groups.index(rest[-1]) + 1
                self.groups.insert(at, g)
        self._groups_changed()

    def group_nest(self, gid: str, parent: str, after: str = "") -> dict:
        """Put group gid under parent ("" = the top), after sibling `after` there (else last). ValueError: into itself
        or one of its subgroups, too deep, or a sibling has its name."""
        with self.lock:
            g = self._group(gid)
            parent = parent or ""
            if parent:
                self._group(parent)
                if parent == gid or parent in self._descendants(gid):
                    raise ValueError("a group cannot go into itself or one of its subgroups")
                if self._depth(parent) + self._height(gid) > GROUP_DEPTH:
                    raise ValueError(f"groups nest {GROUP_DEPTH} levels deep at most")
            self._check_name(g["name"], gid, parent or None)
            self.groups.remove(g)
            if parent:
                g["parent"] = parent
            else:
                g.pop("parent", None)
            sib = self._children(parent or None)
            ref = next((x for x in sib if x["id"] == after), None) or (sib[-1] if sib else (self._group(parent) if parent else None))
            self.groups.insert(self.groups.index(ref) + 1 if ref else len(self.groups), g)
        self._groups_changed()
        return g

    def group_delete(self, gid: str) -> None:
        """Delete a group: its sessions (open or closed) and its subgroups move up to its parent (a top group's
        sessions: to the first remaining group; its subgroups: to the top, where it was)."""
        with self.lock:
            g = self._group(gid)
            if len(self.groups) == 1:
                raise ValueError("the last group cannot be deleted")
            parent = g.get("parent") or ""
            at = self.groups.index(g)
            kids = self._children(gid)
            for c in kids:  # (its subgroups take its place, in their order; names that clash get a suffix)
                if parent:
                    c["parent"] = parent
                else:
                    c.pop("parent", None)
                try:
                    self._check_name(c["name"], c["id"], parent or None)
                except ValueError:
                    c["name"] = f"{c['name']} ({g['name']})"
            self.groups.remove(g)
            for c in kids:
                self.groups.remove(c)
            for k, c in enumerate(kids):
                self.groups.insert(min(at + k, len(self.groups)), c)
            to = parent or self.groups[0]["id"]
            moved = [s for s in self.sessions.values() if s.group == gid]
            for s in moved:
                s.group = to
        self._groups_changed()
        for s in moved:
            self._session_changed(s)
        if self.federation:  # another machine's sessions in it fall back too
            self.federation.refresh()

    def all_groups(self) -> list[dict]:
        """This machine's groups, then one per other machine (its sessions)."""
        return self.groups + (self.federation.groups() if self.federation else [])

    # ---------- a group's look and its home page (its first tab)
    def group_style(self, gid: str, patch: dict) -> dict:
        """{icon?, color?, cover?}: set ("" clears) what is given."""
        with self.lock:
            g = self._group(gid)
            for k in ("icon", "color", "cover"):
                if k in patch:
                    v = check_style(k, patch[k])
                    if v:
                        g[k] = v
                    else:
                        g.pop(k, None)
        self._groups_changed()
        return g

    def group_home(self, gid: str, widgets: object) -> dict:
        """The home page's widgets, as the page arranged them (None: back to the default)."""
        with self.lock:
            g = self._group(gid)
            if widgets is None:
                g.pop("home", None)
            else:
                g["home"] = {"widgets": check_home(widgets)}
        self._groups_changed()
        return g

    # ---------- pins: what the user or an agent pinned to a group's home page (a Markdown file in the vault)
    def group_folder(self, gid: str) -> Path:
        """Where a group's notes go: <vault>/Groups/<its path> (made when first needed)."""
        from orbit.workspace import notes
        names, g = [], self._group(gid)
        while g:
            names.insert(0, re.sub(r'[\\/:*?"<>|#^\[\]]+', " ", g["name"]).strip() or g["id"])
            g = self._group(g["parent"]) if g.get("parent") else None
        return notes.root() / "Groups" / Path(*names)

    def pins_file(self, gid: str) -> Path:
        """The group's pins file (kept once made, so a renamed group keeps its pins)."""
        g = self._group(gid)
        p = g.get("pins")
        return Path(p) if p else self.group_folder(gid) / "Pins.md"

    def group_pin(self, gid: str, text: str, by: str = "", sid: str = "", cid: str = "") -> dict:
        """Pin text to the group's home: a block at the top of its Pins.md. {"id", "path"}."""
        from orbit.workspace import files
        text = (text or "").strip()
        if not text:
            raise ValueError("nothing to pin")
        if len(text) > 20000:
            raise ValueError("too long to pin (20 000 characters at most)")
        with self.lock:
            f = self.pins_file(gid)
            self._group(gid)["pins"] = str(f)
        pid = uuid.uuid4().hex[:10]
        src = f" · [source](orbit://session/{sid}{'#' + str(cid) if cid else ''})" if sid else ""
        block = f"<!-- pin:{pid} -->\n## {time.strftime('%Y-%m-%d %H:%M')} · {by or 'You'}{src}\n\n{text}\n\n"
        f.parent.mkdir(parents=True, exist_ok=True)
        old = f.read_text(encoding="utf-8") if f.exists() else "# Pins\n\n"
        head, _, rest = old.partition("\n\n") if old.startswith("# ") else ("", "", old)
        new = (head + "\n\n" if head else "") + block + rest
        files.save(str(f), new, None, force=True)
        self._groups_changed()
        self.broadcast({"t": "pin", "gid": gid, "id": pid})
        return {"id": pid, "path": str(f)}

    def group_unpin(self, gid: str, pid: str) -> dict:
        """Take a pin off (its block leaves Pins.md)."""
        from orbit.workspace import files
        f = self.pins_file(gid)
        if not re.fullmatch(r"[0-9a-f]{10}", pid or "") or not f.exists():
            raise ValueError("no such pin")
        text = f.read_text(encoding="utf-8")
        m = re.search(r"<!-- pin:" + pid + r" -->\n.*?(?=<!-- pin:[0-9a-f]{10} -->\n|\Z)", text, re.S)
        if not m:
            raise ValueError("no such pin")
        files.save(str(f), text[:m.start()] + text[m.end():], None, force=True)
        self.broadcast({"t": "pin", "gid": gid, "id": pid, "removed": True})
        return {"ok": True}
