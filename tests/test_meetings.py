"""The Calendar's meetings: where a meeting's note is (the `meeting_notes` setting, POST /api/calendar/note), and the
plugins' [meeting] hooks (API 2.1): what relates to a meeting (POST /api/calendar/related) and what Prep should know.

Run: uv run python -m unittest discover -s tests -v
"""

import json
import os
import sys
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import test_skeleton
from orbit import API_VERSION, plugins
from orbit.desk import meetings
from orbit.workspace import notes

FAKE = r'''
import json, os, sys
me, hook = os.environ["ORBIT_PLUGIN"], sys.argv[1]
ev = json.load(sys.stdin)["event"]
with open(os.environ["FAKE_MEETING_LOG"], "a") as f:
    f.write(json.dumps({"plugin": me, "hook": hook, "event": ev}) + "\n")
if me == "broken":
    sys.exit("no directory")
if hook == "related":
    print(json.dumps({"items": [
        {"title": "Ticket ORB-1 " + ev["title"], "url": "https://tickets.example/ORB-1", "detail": "open"},
        {"title": "Spec", "path": "/tmp/spec.md"},
        {"title": "Bad link", "url": "javascript:alert(1)", "path": "relative/x.md"},
        {"url": "https://no-title.example"},
    ] + [{"title": f"more {i}"} for i in range(20)]}))
else:
    print(json.dumps({"context": "Last time: " + ev["title"] + " slipped a week."}))
'''


class MeetingsTest(test_skeleton.SkeletonTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        t = Path(cls.tmp.name)
        cls.log = t / "meeting.jsonl"
        os.environ["FAKE_MEETING_LOG"] = str(cls.log)
        script = t / "fake_meeting.py"
        script.write_text(FAKE)
        cls.dirs = {}
        for name, table in (("acme", '[meeting]\nrelated = ["related"]\nprep = ["prep"]\n'),
                            ("broken", '[meeting]\nrelated = ["related"]\n'),
                            ("badkey", '[meeting]\nsoon = ["x"]\n'),
                            ("nocli", None)):
            d = cls.dirs[name] = t / name
            d.mkdir()
            cli = "" if name == "nocli" else f'[cli]\ncommand = [{json.dumps(sys.executable)}, {json.dumps(str(script))}]\n'
            (d / "plugin.toml").write_text(f'[plugin]\nname = "{name}"\nrequires_api = ">=2.1,<3"\n' + cli
                                           + (table or '[meeting]\nprep = ["prep"]\n'))

    @classmethod
    def tearDownClass(cls):
        plugins.unload()
        os.environ.pop("FAKE_MEETING_LOG", None)
        super().tearDownClass()

    def setUp(self):
        self.log.unlink(missing_ok=True)
        plugins.unload()
        self.req("POST", "/api/settings", {"meeting_notes": "{vault}/Meetings/{date} {title}.md"})

    def event(self, title="Design review: v2/Plan", **kw):
        start = datetime(2026, 11, 24, 14, 30).timestamp()
        return {"id": "e1", "title": title, "start": start, "end": start + 3600, "organizer": "Alice Chen",
                "people": ["Bob Li"], "guests": [{"name": "Bob Li", "status": "accepted"}], "link": "https://zoom.us/j/1",
                "notes": "x" * 9000, **kw}

    # ------------------------------------------------------------------ the note's place
    def test_default_note_is_in_the_vault_and_made_once(self):
        code, d = self.req("POST", "/api/calendar/note", {"event": self.event()})
        self.assertEqual(code, 200, d)
        want = Path(os.path.realpath(notes.root())) / "Meetings" / "2026-11-24 Design review v2 Plan.md"
        self.assertEqual((d["path"], d["exists"], d["created"]), (str(want), False, False))
        self.assertNotIn("text", d)
        self.assertFalse(want.exists())  # (asking does not make it)
        d = self.req("POST", "/api/calendar/note", {"event": self.event(), "create": True})[1]
        self.assertTrue(d["created"] and d["exists"])
        text = want.read_text()
        self.assertTrue(text.startswith("# Design review: v2/Plan\n"), text)
        for line in ("- **When** Tue 2026-11-24 14:30–15:30", "- **Organizer** Alice Chen", "- **With** Bob Li",
                     "- **Join** https://zoom.us/j/1", "## Notes"):
            self.assertIn(line, text)
        self.assertEqual(d["text"], text)
        want.write_text(text + "mine\n")
        d = self.req("POST", "/api/calendar/note", {"event": self.event(), "create": True})[1]
        self.assertEqual((d["created"], d["text"].endswith("mine\n")), (False, True))  # (an existing note is kept)

    def test_the_setting_moves_it_under_the_data_root(self):
        code, st = self.req("POST", "/api/settings", {"meeting_notes": " {data}/calendar_notes/{YYYYMM}/{DD}_{HHMM}_{title}.md "})
        self.assertEqual((code, st["meeting_notes"]), (200, "{data}/calendar_notes/{YYYYMM}/{DD}_{HHMM}_{title}.md"))
        d = self.req("POST", "/api/calendar/note", {"event": self.event(title="1:1"), "create": True})[1]
        want = Path(os.path.realpath(notes.data_root())) / "calendar_notes" / "202611" / "24_1430_1 1.md"
        self.assertEqual(d["path"], str(want))
        self.assertTrue(want.is_file())
        for bad, says in (("/etc/x.md", "starts with"), ("{vault}/../x.md", ". or .."), ("{vault}/{who}.md", "no {who}"),
                          ("{vault}/x.txt", "ends with .md"), ("{vault}/", "a path"), ("{data}/a//b.md", ". or ..")):
            code, e = self.req("POST", "/api/settings", {"meeting_notes": bad})
            self.assertEqual(code, 400, bad)
            self.assertIn(says, e["error"], bad)
        self.assertEqual(self.req("GET", "/api/settings")[1]["meeting_notes"], st["meeting_notes"])  # (unchanged)

    def test_a_title_cannot_leave_the_folder(self):
        for title in ("../../etc/passwd", "..", "/abs/olute", "a\\b", "\x00\n"):
            p = meetings.note_path("{vault}/Meetings/{title}.md", self.event(title=title))
            self.assertEqual(p.parent, Path(os.path.realpath(notes.root())) / "Meetings", title)
        self.assertEqual(meetings.note_path("{vault}/{title}.md", self.event(title="..")).name, "Meeting.md")
        self.assertEqual(self.req("POST", "/api/calendar/note", {"event": {"title": "x"}})[0], 400)  # (no start)

    # ------------------------------------------------------------------ the plugins' [meeting] (API 2.1)
    def test_manifest(self):
        self.assertGreaterEqual(API_VERSION, (2, 1))
        by = {p["name"]: p for p in plugins.load(self.state, [str(d) for d in self.dirs.values()])}
        self.assertTrue(by["acme"]["ok"], by["acme"])
        self.assertEqual(by["acme"]["meeting"], ["prep", "related"])
        self.assertEqual(plugins.LOADED["acme"]["meeting"], {"related": ["related"], "prep": ["prep"]})
        self.assertIn("[meeting]: a table", by["badkey"]["error"])
        self.assertIn("[meeting] runs the plugin's [cli] command, which is missing", by["nocli"]["error"])

    def test_related_items_checked_and_a_broken_plugin_left_out(self):
        self.assertEqual(self.req("POST", "/api/calendar/related", {"event": self.event()})[1], {"items": [], "local": True})
        plugins.load(self.state, [str(self.dirs["acme"]), str(self.dirs["broken"])])
        code, d = self.req("POST", "/api/calendar/related", {"event": self.event()})
        self.assertEqual(code, 200, d)
        items = d["items"]
        self.assertEqual(items[0], {"plugin": "acme", "title": "Ticket ORB-1 Design review: v2/Plan", "detail": "open",
                                    "url": "https://tickets.example/ORB-1"})
        self.assertEqual(items[1], {"plugin": "acme", "title": "Spec", "path": "/tmp/spec.md"})
        self.assertEqual(items[2], {"plugin": "acme", "title": "Bad link"})  # (a javascript: URL, a relative path: dropped)
        self.assertEqual(len(items), 7)  # (8 asked for per plugin: the one without a title is left out)
        calls = [json.loads(x) for x in self.log.read_text().splitlines()]
        self.assertEqual(sorted(c["plugin"] for c in calls), ["acme", "broken"])
        self.assertEqual(len(calls[0]["event"]["notes"]), 4000)  # (the invitation cut)
        self.assertIn("no directory", (self.state / "logs" / "plugins" / "broken.meeting.log").read_text())

    def test_prep_gets_the_plugins_context(self):
        plugins.load(self.state, [str(self.dirs["acme"])])
        path = self.req("POST", "/api/calendar/note", {"event": self.event(), "create": True})[1]["path"]
        asked = []
        fake = lambda text, cwd: asked.append(text) or {"session": {"id": "s", "name": "Helper"}, "card": {"id": 1}}
        with mock.patch.object(self.desk, "fleet_ask", side_effect=fake):
            code, d = self.req("POST", "/api/calendar/prep", {"event": self.event(), "path": path})
        self.assertEqual(code, 200, d)
        self.desk.card_hooks.pop(("s", 1), None)
        self.assertIn("### From acme\nLast time: Design review: v2/Plan slipped a week.", asked[0])
        self.assertIn(f"notes in {os.path.realpath(notes.root())}", asked[0])


if __name__ == "__main__":
    unittest.main()
