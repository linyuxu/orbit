"""Meetings: the 10-minute reminder, a meeting's note, and Prep (a Fleet helper prepares you, into its note).

A meeting's note is where the `meeting_notes` setting says: a path pattern starting with {vault}/ (the notes vault)
or {data}/ (the data root, `--data-root`), with {date} (YYYY-MM-DD), {YYYY} {MM} {DD} {HH} {mm} {YYYYMM} {HHMM} and
{title} (the meeting's title, made safe for a file name), e.g. `{data}/calendar_notes/{YYYYMM}/{DD}_{HHMM}_{title}.md`.
"""

from __future__ import annotations

import os
import re
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


NOTE_ROOTS = ("{vault}/", "{data}/")
NOTE_TOKENS = ("date", "YYYY", "MM", "DD", "HH", "mm", "YYYYMM", "HHMM", "title")


def check_note_pattern(v: str) -> str:
    """The `meeting_notes` setting, checked (ValueError: what is wrong with it)."""
    v = v.strip()
    if not v.startswith(NOTE_ROOTS):
        raise ValueError("meeting_notes starts with {vault}/ or {data}/")
    rest = v.split("/", 1)[1]
    bad = [t for t in re.findall(r"\{([^{}]*)\}", rest) if t not in NOTE_TOKENS]
    if bad:
        raise ValueError(f"meeting_notes: no {{{bad[0]}}} (it knows {', '.join('{' + t + '}' for t in NOTE_TOKENS)})")
    if not rest or any(part in ("", ".", "..") for part in rest.split("/")) or rest.startswith("."):
        raise ValueError("meeting_notes: a path of names, no empty, . or .. parts")
    if not rest.lower().endswith(".md"):
        raise ValueError("meeting_notes ends with .md")
    return v


def note_title(e: dict) -> str:
    t = re.sub(r'[\/\\:*?"<>|#^\[\]\x00-\x1f]+', " ", str(e.get("title") or "Meeting"))
    return " ".join(t.split())[:120].strip(" .") or "Meeting"


def note_path(pattern: str, e: dict) -> Path:
    """Where meeting e's note is, by the pattern (checked; a bad one falls back to the default)."""
    from orbit.workspace import notes
    try:
        pattern = check_note_pattern(pattern)
    except ValueError:
        pattern = "{vault}/Meetings/{date} {title}.md"
    a = datetime.fromtimestamp(float(e.get("start") or 0))
    vals = {"date": f"{a:%Y-%m-%d}", "YYYY": f"{a:%Y}", "MM": f"{a:%m}", "DD": f"{a:%d}", "HH": f"{a:%H}",
            "mm": f"{a:%M}", "YYYYMM": f"{a:%Y%m}", "HHMM": f"{a:%H%M}", "title": note_title(e)}
    root_tok, rest = pattern.split("/", 1)
    base = Path(os.path.realpath(notes.root() if root_tok == "{vault}" else notes.data_root()))
    p = Path(os.path.realpath(base / re.sub(r"\{(\w+)\}", lambda m: vals[m.group(1)], rest)))
    if base not in p.parents:
        raise ValueError(f"{p} is outside {base}")
    return p


def note_head(e: dict) -> str:
    """A new meeting note: when, where, who, the link; then ## Notes."""
    where = " · ".join(x for x in (e.get("calendar"), e.get("location")) if x)
    lines = [f"# {e.get('title') or 'Meeting'}", "", f"- **When** {_when(e)}" + (f" · {where}" if where else "")]
    if e.get("organizer"):
        lines.append(f"- **Organizer** {e['organizer']}")
    people = e.get("people") or []
    if people:
        lines.append("- **With** " + ", ".join(map(str, people)))
    elif e.get("attendees"):
        lines.append(f"- **With** {e['attendees']} people")
    if e.get("link"):
        lines.append(f"- **Join** {e['link']}")
    return "\n".join(lines + ["", "## Notes", "", ""])


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

    # ---------- a meeting's note (where `meeting_notes` says)
    def meeting_note(self, event: dict, create: bool = False) -> dict:
        """{"path", "exists", "created", "text"?}: meeting `event`'s note; made (its header) when create is set."""
        from orbit.workspace import files
        if not isinstance(event, dict) or not event.get("start"):
            raise ValueError("event: a meeting (with its start)")
        p = note_path(str(self.settings.get("meeting_notes") or ""), event)
        created = False
        if create and not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            files.save(str(p), note_head(event), None)
            created = True
        out = {"path": str(p), "exists": p.is_file(), "created": created}
        if p.is_file():
            out["text"] = files.read(str(p)).get("text", "")
        return out

    # ---------- Prep: a Fleet helper prepares you for a meeting; its brief is appended to the meeting's note
    def meeting_prep(self, event: dict, path: str) -> dict:
        from orbit import plugins
        from orbit.workspace import files, notes
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
                f"{notes.root()}, recent files and git history in the projects it names.\n\n"
                "Answer with a short brief in Markdown (no title): the likely topic; who is likely to ask what (a table: "
                "person · what they will ask or want); open items and decisions to make; links to the sessions, notes and "
                "files that matter (as paths). Your answer is "
                "appended to the meeting note as it is; do not edit the note yourself.")
        extra = plugins.meeting_context(event)  # (API 2.1) what the plugins' [meeting] prep adds
        if extra:
            text += "\n\nWhat my tools add about it:\n" + "\n".join(f"\n### From {x['plugin']}\n{x['text']}" for x in extra)
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
