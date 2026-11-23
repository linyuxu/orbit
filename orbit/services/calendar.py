"""Calendar (D4): read-only events from macOS Calendar (EventKit), behind the `calendar` setting.

The events come from a small Swift helper (helpers/calendar.swift), compiled on first use into
<state dir>/bin/orbit-calendar. `$ORBIT_CALENDAR_BIN` replaces it (tests use a fake). The first read asks macOS for
Calendar access; the answer belongs to the app that started Orbit.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path


SRC = Path(__file__).resolve().parent / "helpers" / "calendar.swift"
LINK = re.compile(r"https://[^\s<>\"')]*(?:zoom\.us/[jw]/|meet\.google\.com/|teams\.microsoft\.com/l/meetup-join/|"
                  r"teams\.live\.com/meet/|webex\.com/(?:meet|join)|whereby\.com/|meet\.jit\.si/|"
                  r"chime\.aws/)[^\s<>\"')]*", re.I)
CACHE_S = 60


REMIND_S = 600  # a meeting's reminder: 10 minutes before it starts


PLACEHOLDER = re.compile(r"^[A-Z0-9_-]+:[A-Z0-9_-]+$")  # org placeholders like TEAM:TEAM


def noise(e: dict) -> bool:
    """A block, not a meeting to list, count or be reminded of: all day, declined, canceled, free, no title, an org
    placeholder, or your own block (no one else invited)."""
    title = (e.get("title") or "").strip()
    return bool(e.get("all_day") or e.get("response") == "declined" or e.get("canceled") or e.get("free")
                or not title or title.lower() in ("(no title)", "busy", "free") or PLACEHOLDER.match(title)
                or re.match(r"(?i)cancel(l)?ed\s*:", title)
                or (e.get("mine", True) and not e.get("attendees") and not e.get("people")))


def due_reminders(events: list[dict], now: float, told: set[str]) -> list[dict]:
    """The meetings starting within REMIND_S that were not reminded of yet (each once); marks them told."""
    out = []
    for e in events:
        k = f"{e.get('id')}:{e.get('start')}"
        if k in told or noise(e) or not (now < e.get("start", 0) <= now + REMIND_S):
            continue
        told.add(k)
        out.append(e)
    return out


def meeting_link(e: dict) -> str:
    for field in ("url", "location", "notes"):
        m = LINK.search(e.get(field) or "")
        if m:
            return m.group(0).rstrip(".,;")
    return ""


class Calendar:
    def __init__(self, state_dir: Path):
        self.bin = state_dir / "bin" / "orbit-calendar"
        self.lock = threading.Lock()
        self.cache: dict[tuple[int, int], tuple[float, dict]] = {}

    def _command(self) -> list[str]:
        fake = os.environ.get("ORBIT_CALENDAR_BIN")
        if fake:
            return shlex.split(fake)
        if not self.bin.exists() or self.bin.stat().st_mtime < SRC.stat().st_mtime:
            self.bin.parent.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(["swiftc", "-O", "-o", str(self.bin), str(SRC)], capture_output=True, text=True,
                               timeout=300)
            if r.returncode != 0:
                raise RuntimeError("could not build the Calendar helper (swiftc): " + (r.stderr or r.stdout)[-400:])
        return [str(self.bin)]

    def events(self, frm: float, to: float, fresh: bool = False) -> dict:
        key = (int(frm), int(to))
        with self.lock:  # one helper at a time (the first one may be waiting for the access prompt)
            hit = self.cache.get(key)
            if hit and not fresh and time.time() - hit[0] < CACHE_S:
                return hit[1]
            try:
                r = subprocess.run([*self._command(), str(int(frm)), str(int(to))], capture_output=True, text=True,
                                   timeout=180)
                d = json.loads(r.stdout or "{}") if r.stdout.strip() else {"error": (r.stderr or "no output")[-400:]}
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as e:
                d = {"error": str(e)}
            for e in d.get("events", []):
                e["link"] = meeting_link(e)
                e["noise"] = noise(e)
            if "events" in d:
                self.cache[key] = (time.time(), d)
            return d
