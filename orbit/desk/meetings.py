"""Meetings: the 10-minute reminder, and Prep (a Fleet helper prepares you for a meeting, into its note)."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from orbit.services.calendar import REMIND_S, due_reminders


def _when(e: dict) -> str:
    a, b = datetime.fromtimestamp(e["start"]), datetime.fromtimestamp(e["end"])
    return "all day " + a.strftime("%a %Y-%m-%d") if e.get("all_day") else f"{a:%a %Y-%m-%d %H:%M}–{b:%H:%M}"


def macos_notify(title: str, text: str) -> None:
    if sys.platform != "darwin" or os.environ.get("ORBIT_NO_NOTIFY"):
        return
    q = lambda s: '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    try:
        subprocess.Popen(["osascript", "-e", f"display notification {q(text)} with title {q(title)}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


class Meetings:
    """Desk methods: meeting reminders and Prep."""

    # ---------- the reminder: 10 minutes before (once per meeting; not all-day, declined or your own blocks)
    def start_reminders(self, every: float = 30) -> None:
        self._told: set[str] = set()

        def loop() -> None:
            while not self.stopping_all:
                try:
                    self.remind_now()
                except Exception as e:  # noqa: BLE001 - a calendar hiccup must not end the reminders
                    print(f"orbit: reminders: {e}", flush=True)
                time.sleep(every)
        threading.Thread(target=loop, name="orbit-reminders", daemon=True).start()

    def remind_now(self, now: float | None = None) -> list[dict]:
        if not self.settings.get("calendar"):
            return []
        now = time.time() if now is None else now
        d = self.calendar.events(now - 60, now + REMIND_S + 60)
        due = due_reminders(d.get("events") or [], now, self.__dict__.setdefault("_told", set()))
        for e in due:
            mins = max(1, round((e["start"] - now) / 60))
            self.broadcast({"t": "remind", "event": e, "minutes": mins})
            macos_notify(f"In {mins} min: {e.get('title') or 'a meeting'}",
                         datetime.fromtimestamp(e["start"]).strftime("%H:%M") + (" · join link in Orbit" if e.get("link") else ""))
        return due

    # ---------- Prep: a Fleet helper prepares you for a meeting; its brief is appended to the meeting's note
    def meeting_prep(self, event: dict, path: str) -> dict:
        from orbit.workspace import files
        note = files._abs(path)
        if not note.is_file():
            raise ValueError("make the meeting note first")
        people = ", ".join(event.get("people") or []) or (f"{event['attendees']} people" if event.get("attendees") else "")
        facts = "\n".join(f"- {k}: {v}" for k, v in [
            ("Title", event.get("title") or "(no title)"), ("When", _when(event) if event.get("start") else ""),
            ("Organizer", event.get("organizer") or ""), ("Attendees", people), ("Calendar", event.get("calendar") or ""),
            ("Location", event.get("location") or ""), ("Link", event.get("link") or "")] if v)
        desc = (event.get("notes") or "").strip()
        text = ("Prepare me for this meeting. Read-only: look things up, change nothing, send or post nothing.\n\n"
                f"{facts}\n" + (f"- Description:\n{desc[:3000]}\n" if desc else "") +
                f"\nThe meeting note is {note} (read it: my own notes may say what I want from it). Look for what "
                "relates to it on this machine: Orbit sessions (`orbit-agent sessions`, `orbit-agent read`), notes in "
                f"{note.parent.parent}, recent files and git history in the projects it names.\n\n"
                "Answer with a short brief in Markdown (no title): the likely topic; who is likely to ask what (a table: "
                "person · what they will ask or want); open items and decisions to make; links to the sessions, notes and "
                "files that matter (as paths). Your answer is "
                "appended to the meeting note as it is; do not edit the note yourself.")
        r = self.fleet_ask(text, str(note.parent))
        sid, cid = r["session"]["id"], r["card"]["id"]
        self.card_hooks[(sid, cid)] = lambda c: self._prep_done(c, note, r["session"]["name"])
        return {"session": r["session"], "card": r["card"]}

    def _prep_done(self, c: dict, note: Path, who: str) -> None:
        from orbit.workspace import files
        if c["state"] != "completed" or not (c.get("answer") or "").strip():
            self.broadcast({"t": "prep", "path": str(note), "error": c.get("error") or c["state"]})
            return
        block = f"\n\n## Prep · {who}, {datetime.now():%H:%M}\n\n{c['answer'].strip()}\n"
        with open(note, "a", encoding="utf-8") as f:
            f.write(block)
        f = files.read(str(note))  # the page's editor takes the new text and this as its base (no conflict)
        self.broadcast({"t": "note_append", "path": str(note), "text": block, "mtime": f.get("mtime"),
                        "size": f.get("size"), "hash": f.get("hash", "")})
