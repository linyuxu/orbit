"""The Fleet: background helper sessions for one-off questions (`orbit-agent ask`, ⌘K)."""

from __future__ import annotations

import os
import uuid

from orbit.desk.session import ACTIVE, DRIVERS, FLEET_GROUP, FLEET_MAX, Session


class Fleet:
    """Desk methods: the Fleet: background helper sessions for one-off questions (`orbit-agent ask`, ⌘K)."""

    def fleet_ask(self, text: str, cwd: str = "") -> dict:
        """A one-off request to a Fleet helper in cwd: an idle one, else a new one (up to FLEET_MAX), else the least
        busy. Returns {"session", "card"}."""
        if not text.strip():
            raise ValueError("empty request")
        cwd = os.path.realpath(os.path.expanduser(cwd or "~"))
        if not os.path.isdir(cwd):
            raise ValueError(f"no such folder: {cwd}")
        harness = str(self.settings.get("fleet_harness") or "") or "claude"  # (Settings → General: Fleet helpers)
        model = str(self.settings.get("fleet_model") or "")
        if harness not in DRIVERS:
            raise ValueError(f"the Fleet harness {harness!r} is not available (Settings → General)")
        with self.lock:  # helpers of this harness and model (a changed setting makes new ones; the old stay idle)
            pool = [s for s in self.sessions.values() if s.fleet and not s.closed and s.cwd == cwd
                    and s.harness == harness and (s.model or "") == model]
        busy = lambda s: sum(1 for c in s.cards if c["state"] == "queued" or c["state"] in ACTIVE)
        idle = [s for s in pool if busy(s) == 0 and s.state != "not ready"]
        if idle:
            s = idle[0]
        elif len(pool) < FLEET_MAX:
            used = {x.name for x in self.sessions.values() if x.fleet}
            n = next(i for i in range(1, 1000) if f"Fleet {i}" not in used)
            s = Session({"id": str(uuid.uuid4()), "name": f"Fleet {n}", "cwd": cwd, "mode": "default",
                         "harness": harness, "model": model, "group": FLEET_GROUP, "fleet": True, "named": True})
            s.loaded = True
            with self.lock:
                self.sessions[s.id] = s
            self.save()
            self._start(s, resume=False)
            self._session_changed(s)
        else:
            s = min(pool, key=busy)
        card = self.send(s.id, text)
        return {"session": s.public(), "card": card}

    def fleet(self) -> list[dict]:
        with self.lock:
            return [s.public() for s in self.sessions.values() if s.fleet and not s.closed]
