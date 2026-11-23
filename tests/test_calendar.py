"""Build step 7 (Calendar, D4): read-only, behind the `calendar` setting, through the helper (a fake here).

Run: uv run python -m unittest discover -s tests -v
"""

import os
import shlex
import sys
import time
import unittest
from pathlib import Path

import test_skeleton
from orbit.services.calendar import meeting_link

HERE = Path(__file__).resolve().parent


class CalendarTest(test_skeleton.SkeletonTest):
    @classmethod
    def setUpClass(cls):
        os.environ["ORBIT_CALENDAR_BIN"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(HERE / 'fake_calendar.py'))}"
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        os.environ.pop("ORBIT_CALENDAR_BIN", None)
        os.environ.pop("FAKE_CAL", None)

    def day(self):
        t = time.localtime()
        start = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
        return f"/api/calendar?from={start}&to={start + 86400}"

    def test_off_until_turned_on(self):
        self.req("POST", "/api/settings", {"calendar": False})
        _, d = self.req("GET", self.day())
        self.assertEqual(d, {"enabled": False, "events": []})
        _, st = self.req("POST", "/api/settings", {"calendar": True})
        self.assertTrue(st["calendar"])
        _, d = self.req("GET", self.day())
        self.assertTrue(d["enabled"])
        self.assertEqual([(e["title"], e["noise"]) for e in d["events"]],
                         [("Standup", False), ("Release sync", False), ("Focus block", True), ("Design review", False)])  # your own block: noise
        self.assertEqual([g["status"] for g in d["events"][0]["guests"]], ["accepted", "accepted", "declined", "accepted"])
        self.assertEqual(d["events"][0]["link"], "https://meet.google.com/abc-defg-hij")
        self.assertEqual(d["events"][3]["link"], "https://acme.zoom.us/j/123456?pwd=x")
        # the setting survives a restart
        type(self)._unserve()
        type(self)._serve()
        self.assertTrue(self.req("GET", "/api/settings")[1]["calendar"])

    def test_errors(self):
        self.req("POST", "/api/settings", {"calendar": True})
        os.environ["FAKE_CAL"] = "denied"
        try:
            _, d = self.req("GET", self.day() + "&fresh=1")
        finally:
            os.environ.pop("FAKE_CAL")
        self.assertTrue(d["denied"])
        self.assertIn("may not read", d["error"])
        self.assertEqual(self.req("GET", "/api/calendar")[0], 400)
        self.assertEqual(self.req("POST", "/api/settings", {"nope": 1})[0], 400)
        self.assertEqual(self.req("POST", "/api/settings", {"calendar": "yes"})[0], 400)
        self.assertEqual(self.req("POST", "/api/settings", {"open_in": "emacs"})[0], 400)
        self.assertEqual(self.req("POST", "/api/settings", {"open_in": "vscode"})[1]["open_in"], "vscode")
        self.req("POST", "/api/settings", {"open_in": "default"})

    def test_aide_calendar(self):
        import subprocess
        env = {**os.environ, "ORBIT_PORT": str(self.port)}
        run = lambda *a: subprocess.run([sys.executable, "-m", "orbit.cli.agent", "calendar", *a], env=env,
                                        capture_output=True, text=True, timeout=30)
        self.req("POST", "/api/settings", {"calendar": False})
        self.assertIn("Calendar is off", run().stderr)
        self.req("POST", "/api/settings", {"calendar": True})
        out = run("--days", "2").stdout
        self.assertEqual(out.count("Standup"), 2)
        self.assertIn("09:00–09:30  Standup  (https://meet.google.com/abc-defg-hij)", out)
        self.assertIn("Design review  (Room 4 · https://acme.zoom.us/j/123456?pwd=x)", out)

    def test_reminder_once_ten_minutes_before(self):
        self.req("POST", "/api/settings", {"calendar": True})
        t = time.localtime()
        nine = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 9, 0, 0, 0, 0, -1))
        events = self.desk.subscribe()
        try:
            self.assertEqual([e["title"] for e in self.desk.remind_now(nine - 300)], ["Standup"])  # 09:15 is still 20 min off
            self.assertEqual(self.desk.remind_now(nine - 240), [])  # once
            self.assertEqual(self.desk.remind_now(nine - 1200), [])  # too early
            noon = nine + 3 * 3600
            self.assertEqual(self.desk.remind_now(noon - 300), [])  # your own block: no reminder
            import json as _j
            seen = []
            while not events.empty():
                seen.append(_j.loads(events.get()))
            self.assertIn(("remind", "Standup", 5), [(m["t"], m.get("event", {}).get("title"), m.get("minutes")) for m in seen])
        finally:
            self.desk.unsubscribe(events)

    def test_prep_appends_to_the_meeting_note(self):
        import json as _j
        from orbit.desk.session import FLEET_GROUP  # noqa: F401
        note = Path(self.tmp.name) / "data" / "notes" / "Meetings" / "2026-11-21 Standup.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("# Standup\n\n## Notes\n\nask about the deploy\n")
        ev = {"title": "Standup", "start": time.time() + 3600, "end": time.time() + 5400, "organizer": "Ann",
              "people": ["Ann", "Bo"], "link": "https://meet.google.com/x"}
        events = self.desk.subscribe()
        try:
            code, r = self.req("POST", "/api/calendar/prep", {"event": ev, "path": str(note)})
            self.assertEqual(code, 200, r)
            self.assertTrue(r["session"]["fleet"])
            self.assertIn("Organizer: Ann", r["card"]["text"])
            self.assertIn("Attendees: Ann, Bo", r["card"]["text"])
            self.wait_card(r["session"]["id"], r["card"]["id"], ("completed",))
            got = None
            for _ in range(100):
                try:
                    m = _j.loads(events.get(timeout=0.1))
                except Exception:
                    continue
                if m["t"] == "note_append":
                    got = m
                    break
            self.assertTrue(got, "no note_append event")
            text = note.read_text()
            self.assertIn("ask about the deploy", text)  # the user's own notes stay
            self.assertIn(f"## Prep · {r['session']['name']}", text)
            self.assertIn("answer: Prepare me for this meeting", text)  # the fake agent's answer, appended
            self.assertEqual(got["text"], text[len("# Standup\n\n## Notes\n\nask about the deploy\n"):])
            self.assertTrue(got["hash"])
        finally:
            self.desk.unsubscribe(events)
        self.assertEqual(self.req("POST", "/api/calendar/prep", {"event": ev, "path": str(note) + ".nope"})[0], 400)

    def test_blocks(self):
        from orbit.services.calendar import noise
        for title, extra, want in [("Busy", {}, True), ("", {"attendees": 3}, True), ("TEAM:TEAM", {"attendees": 3}, True),
                                   ("Canceled: Sync", {"attendees": 3}, True), ("Sync", {"attendees": 3, "canceled": True}, True),
                                   ("Sync", {"attendees": 3, "free": True}, True), ("Sync", {"attendees": 3, "mine": False}, False),
                                   ("Lunch", {"mine": True}, True)]:
            self.assertEqual(noise({"title": title, **extra}), want, (title, extra))

    def test_meeting_link(self):
        self.assertEqual(meeting_link({"notes": "dial https://teams.microsoft.com/l/meetup-join/19%3a abc"}),
                         "https://teams.microsoft.com/l/meetup-join/19%3a")
        self.assertEqual(meeting_link({"url": "https://example.com/doc"}), "")


for name in [n for n in dir(test_skeleton.SkeletonTest) if n.startswith("test_")]:
    if name not in CalendarTest.__dict__:
        setattr(CalendarTest, name, None)

if __name__ == "__main__":
    unittest.main()
