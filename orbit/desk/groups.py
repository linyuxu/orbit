"""The left panel's groups (and one per other machine), in order."""

from __future__ import annotations

import uuid

from orbit.desk.session import FLEET_GROUP


class Groups:
    """Desk methods: the left panel's groups (and one per other machine), in order."""

    def _group_id(self, gid: str) -> str:
        if gid == FLEET_GROUP:
            return gid
        return gid if gid in {g["id"] for g in self.groups} else self.groups[0]["id"]

    def _groups_changed(self) -> None:
        self.save()
        self.broadcast({"t": "groups", "groups": self.all_groups()})

    def _check_name(self, name: str, gid: str = "") -> str:
        """The name, trimmed (inner runs of spaces as one): ValueError when empty or another group has it (case and
        spaces ignored), Favorites and Fleet included."""
        name = " ".join(name.split())
        if not name:
            raise ValueError("a group needs a name")
        key = name.casefold()
        if key in ("favorites", "fleet"):
            raise ValueError(f"“{name}” is Orbit's own: there is a group named that already")
        same = next((g for g in self.groups if g["id"] != gid and " ".join(g["name"].split()).casefold() == key), None)
        if same:
            raise ValueError(f"there is a group named “{same['name']}” already")
        return name

    def group_create(self, name: str) -> dict:
        with self.lock:
            g = {"id": "g-" + uuid.uuid4().hex[:8], "name": self._check_name(name)}
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
            g["name"] = self._check_name(name, gid)
        self._groups_changed()
        return g

    def group_move(self, gid: str, delta: int) -> None:
        with self.lock:
            g = self._group(gid)
            i = self.groups.index(g)
            j = max(0, min(len(self.groups) - 1, i + delta))
            self.groups.insert(j, self.groups.pop(i))
        self._groups_changed()

    def group_delete(self, gid: str) -> None:
        """Delete a group; its sessions (open or closed) move to the first remaining group."""
        with self.lock:
            g = self._group(gid)
            if len(self.groups) == 1:
                raise ValueError("the last group cannot be deleted")
            self.groups.remove(g)
            to = self.groups[0]["id"]
            moved = [s for s in self.sessions.values() if s.group == gid]
            for s in moved:
                s.group = to
        self._groups_changed()
        for s in moved:
            self._session_changed(s)
        if self.federation:  # another machine's sessions in it fall back to the first group too
            self.federation.refresh()

    def all_groups(self) -> list[dict]:
        """This machine's groups, then one per other machine (its sessions)."""
        return self.groups + (self.federation.groups() if self.federation else [])
