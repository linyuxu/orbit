"""The left panel's groups: a tree, like folders (a group holds sessions, file tabs and subgroups).

Kept as one ordered list of {"id", "name", "parent"?}: a group's children are the groups whose parent is its id, in
list order (the top: no parent). GROUP_DEPTH levels at most ("Work / Orbit / Release"). A name is unique among its
siblings (case and every space ignored), and Favorites and Fleet are Orbit's own at the top. Deleting a group moves
what it held (its sessions, its subgroups) up to its parent, so nothing is lost.
"""

from __future__ import annotations

import uuid

from orbit.desk.session import FLEET_GROUP

GROUP_DEPTH = 3


def tidy(groups: list[dict]) -> list[dict]:
    """Groups as loaded, made a valid tree: a parent that is missing, itself, or would make a cycle or a tree deeper
    than GROUP_DEPTH is dropped (that group goes to the top)."""
    out = [{k: v for k, v in g.items() if k in ("id", "name", "parent")} for g in groups]
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
        space ignored: "Invest Assistant" is "InvestAssistant"); at the top, Favorites and Fleet are Orbit's own."""
        name = " ".join(name.split())
        if not name:
            raise ValueError("a group needs a name")
        if "/" in name or "›" in name:
            raise ValueError("a group's name has no / or › (they separate a subgroup's path)")
        key = "".join(name.split()).casefold()
        if not parent and key in ("favorites", "fleet"):
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
