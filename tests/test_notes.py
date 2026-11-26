"""Build step 4 (Notes): vault, wiki-link resolution, backlinks, the week note, `orbit-agent note`.

Run: uv run python -m unittest discover -s tests -v
"""

import os
import subprocess
import sys
import unittest
from datetime import date
from pathlib import Path

import test_skeleton
from orbit.workspace import notes


class NotesTest(test_skeleton.SkeletonTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vault = Path(cls.tmp.name) / "data" / "notes"
        os.environ.pop("ORBIT_VAULT", None)
        notes.configure(str(Path(cls.tmp.name) / "data"))

    def setUp(self):
        for p in sorted(self.vault.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
        (self.vault / "Projects").mkdir()
        (self.vault / "Projects" / "Orbit.md").write_text("# Orbit\n\nSee [[Ideas]] and [[Ideas|my ideas]].\n")
        (self.vault / "Ideas.md").write_text("# Ideas\n\nBack to [[Orbit#Plan]].\n")
        (self.vault / "Projects" / "Ideas.md").write_text("# Project ideas\n")
        (self.vault / ".obsidian").mkdir()
        (self.vault / ".obsidian" / "app.md").write_text("ignored")

    def test_vault_lists_markdown_only(self):
        _, d = self.req("GET", "/api/notes/list")
        self.assertEqual(d["root"], os.path.realpath(self.vault))
        self.assertEqual(d["notes"], ["Ideas.md", "Projects/Ideas.md", "Projects/Orbit.md"])

    def test_resolve_like_obsidian(self):
        orbit = str(self.vault / "Projects" / "Orbit.md")
        # the same folder wins, else the shortest path
        self.assertEqual(notes.resolve("Ideas", orbit), os.path.realpath(self.vault / "Projects" / "Ideas.md"))
        self.assertEqual(notes.resolve("ideas", str(self.vault / "Ideas.md")), os.path.realpath(self.vault / "Ideas.md"))
        self.assertEqual(notes.resolve("Ideas"), os.path.realpath(self.vault / "Ideas.md"))
        self.assertEqual(notes.resolve("Projects/Orbit"), os.path.realpath(orbit))
        self.assertIsNone(notes.resolve("Nope"))
        _, r = self.req("GET", "/api/notes/resolve?name=Orbit")
        self.assertTrue(r["exists"])

    def test_create(self):
        _, r = self.req("POST", "/api/notes/create", {"name": "Inbox/New idea"})
        self.assertTrue(r["created"])
        self.assertEqual(Path(r["path"]).read_text(), "# New idea\n\n")
        _, again = self.req("POST", "/api/notes/create", {"name": "Inbox/New idea"})
        self.assertFalse(again["created"])  # never overwrites
        self.assertEqual(self.req("POST", "/api/notes/create", {"name": "../escape"})[0], 400)

    # ------------------------------------------------------------------ live preview / drops (builddoc/features/files.md §0)
    def test_attach_into_the_vault_and_beside_a_file(self):
        import base64
        png = b"\x89PNG\r\n\x1a\nfake"
        orbit = self.vault / "Projects" / "Orbit.md"
        code, r = self.req("POST", "/api/attach", {"doc": str(orbit), "name": "shot.png", "data": base64.b64encode(png).decode()})
        self.assertEqual(code, 200)
        self.assertEqual(r["link"], "![[shot.png]]")
        self.assertEqual(Path(r["path"]), Path(os.path.realpath(self.vault / "attachments" / "shot.png")))
        _, again = self.req("POST", "/api/attach", {"doc": str(orbit), "name": "shot.png", "data": base64.b64encode(png).decode()})
        self.assertEqual(again["path"], r["path"])  # the same bytes again: reused, not "shot 1.png"
        src = Path(self.tmp.name) / "report final.pdf"
        src.write_bytes(b"%PDF-1.4")
        _, d = self.req("POST", "/api/attach", {"doc": str(orbit), "src": str(src)})
        self.assertEqual(d["link"], "[[report final.pdf]]")
        outside = Path(self.tmp.name) / "repo" / "README.md"
        outside.parent.mkdir(exist_ok=True)
        outside.write_text("# r\n")
        _, o = self.req("POST", "/api/attach", {"doc": str(outside), "src": str(src)})
        self.assertEqual(o["link"], "[report final.pdf](<assets/report final.pdf>)")
        self.assertTrue((outside.parent / "assets" / "report final.pdf").exists())

    def test_embed_resolves_like_obsidian(self):
        (self.vault / "pic.png").write_bytes(b"\x89PNG")
        (self.vault / "Projects" / "pic.png").write_bytes(b"\x89PNG-near")
        orbit = str(self.vault / "Projects" / "Orbit.md")
        self.assertEqual(notes.resolve_file("pic.png", orbit), os.path.realpath(self.vault / "Projects" / "pic.png"))
        self.assertEqual(notes.resolve_file("pic.png", str(self.vault / "Ideas.md")), os.path.realpath(self.vault / "pic.png"))
        self.assertEqual(notes.resolve_file("old/place/pic.png"), os.path.realpath(self.vault / "pic.png"))  # moved: by name
        self.assertEqual(notes.resolve_file("Orbit"), os.path.realpath(orbit))
        import urllib.parse
        import urllib.request
        q = urllib.parse.urlencode({"name": "pic.png", "from": orbit})
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/embed?{q}") as r:
            self.assertEqual(r.read(), b"\x89PNG-near")  # served, from the linking note's folder
        self.assertEqual(self.req("GET", "/api/embed?name=nope.png")[0], 404)

    def test_rename_updates_links(self):
        (self.vault / "Table.md").write_text("| a | b |\n|---|---|\n| [[Projects/Orbit\\|o]] | ![[Orbit]] |\n")
        old = self.vault / "Projects" / "Orbit.md"
        code, r = self.req("POST", "/api/files/rename", {"path": str(old), "new": str(self.vault / "Projects" / "Orbit IDE.md")})
        self.assertEqual(code, 200)
        self.assertEqual(r["links"], 2)
        self.assertEqual((self.vault / "Ideas.md").read_text(), "# Ideas\n\nBack to [[Orbit IDE#Plan]].\n")
        self.assertIn("[[Projects/Orbit IDE\\|o]]", (self.vault / "Table.md").read_text())
        self.assertIn("![[Orbit IDE]]", (self.vault / "Table.md").read_text())
        # [[Ideas]] in the moved note still means Projects/Ideas.md: renaming that rewrites only links that meant it
        self.req("POST", "/api/files/rename", {"path": str(self.vault / "Projects" / "Ideas.md"), "new": str(self.vault / "Projects" / "Plans.md")})
        self.assertIn("See [[Plans]] and [[Plans|my ideas]]", (self.vault / "Projects" / "Orbit IDE.md").read_text())

    def test_trash_in_the_vault(self):
        code, _ = self.req("POST", "/api/files/trash", {"path": str(self.vault / "Ideas.md")})
        self.assertEqual(code, 200)
        self.assertTrue((self.vault / ".trash" / "Ideas.md").exists())  # Obsidian's place, recoverable
        self.assertNotIn("Ideas.md", notes.all_notes())

    def test_backlinks(self):
        _, d = self.req("GET", f"/api/notes/backlinks?path={self.vault / 'Projects' / 'Orbit.md'}")
        self.assertEqual([(Path(l["path"]).name, l["line"]) for l in d["links"]], [("Ideas.md", 3)])
        # [[Ideas]] from Projects/Orbit.md resolves to Projects/Ideas.md, not the root Ideas.md
        root_ideas = notes.backlinks(str(self.vault / "Ideas.md"))
        self.assertEqual(root_ideas, [])
        proj = notes.backlinks(str(self.vault / "Projects" / "Ideas.md"))
        self.assertEqual(len(proj), 1)

    def test_week_note_and_today(self):
        d = date(2026, 11, 18)  # a Wednesday
        info = notes.week_note(d)
        self.assertTrue(info["path"].endswith("Weekly/Week of 2026-11-16.md"))
        self.assertEqual(info["heading"], "## Wed 11-18")
        self.assertEqual(Path(info["path"]).read_text(), "")  # (empty: its title is its name; no day headings)
        notes.append_today("first thought", d=d)
        notes.append_today("> quoted answer", source="[demo #3](orbit://session/x#3)", d=d)
        text = Path(info["path"]).read_text()
        self.assertTrue(text.startswith("## Wed 11-18\n"), text)  # (today's heading with its first entry)
        wed = text.split("## Wed 11-18\n", 1)[1]
        self.assertEqual(wed.split("\n")[:6], ["", "first thought", "", "> quoted answer", wed.split("\n")[4], ""])
        self.assertTrue(wed.split("\n")[4].startswith("— [demo #3](orbit://session/x#3) · "))
        self.assertNotIn("## Mon 11-16", text)
        self.assertNotIn("\n\n\n", text)
        # its title edited (renamed): still this week's note
        moved = str(Path(info["path"]).with_name("Launch week.md"))
        os.rename(info["path"], moved)
        notes.journal_renamed(info["path"], moved)
        self.assertEqual(notes.week_note(date(2026, 11, 20))["path"], moved)

    def test_week_note_per_the_organization_guide(self):
        (self.vault / "Organization Guide.md").write_text("# How this vault works\n\nWeekly notes live in Journal.\n")
        d = date(2026, 11, 18)  # a Wednesday
        info = notes.week_note(d)
        self.assertTrue(info["path"].endswith("Journal/2026/Week of 20261116.md"))
        self.assertEqual(info["heading"], "### Wed 11/18")
        text = Path(info["path"]).read_text()
        for sec in ["## Focus", "## Daily Work", "### Mon 11/16", "### Fri 11/20", "## Projects in Flight", "## Ideas",
                    "## Carry-overs", "## Reflections"]:
            self.assertIn(sec, text)
        self.assertLess(text.index("### Fri 11/20"), text.index("## Projects in Flight"))  # the days sit in Daily Work
        notes.append_today("guided entry", d=d)
        text = Path(info["path"]).read_text()
        wed = text.split("### Wed 11/18\n", 1)[1].split("### Thu", 1)[0]
        self.assertIn("guided entry", wed)
        # a Saturday entry: no heading yet, it is added at the end of Daily Work (before Projects in Flight)
        notes.append_today("weekend", d=date(2026, 11, 21))
        text = Path(info["path"]).read_text()
        self.assertLess(text.index("### Sat 11/21"), text.index("## Projects in Flight"))
        self.assertLess(text.index("weekend"), text.index("## Projects in Flight"))

    def test_week_note_reuses_a_recent_one_and_the_guide_template(self):
        (self.vault / "Organization Guide.md").write_text(
            "# Guide\n\n## Weekly template\n\n```markdown\n# Week of YYYYMMDD\n\n## Focus\n\n## Daily Work\n\n## Notes\n```\n")
        started = self.vault / "Journal" / "2026" / "Week of 20261117.md"  # started on a Tuesday
        started.parent.mkdir(parents=True)
        started.write_text("# Week of 20261117\n")
        self.assertEqual(Path(notes.week_note(date(2026, 11, 20))["path"]).resolve(), started.resolve())  # within 6 days: reused
        nxt = Path(notes.week_note(date(2026, 11, 25))["path"])  # a week later: a new one, from the guide's template
        self.assertTrue(str(nxt).endswith("Journal/2026/Week of 20261123.md"))
        text = nxt.read_text()
        self.assertTrue(text.startswith("# Week of 20261123\n"))
        self.assertIn("## Notes", text)
        self.assertLess(text.index("### Mon 11/23"), text.index("## Notes"))

    def test_journal_is_the_week_note_where_obsidian_keeps_daily_notes(self):
        """With Obsidian's Daily notes set up, the journal is one note a week (Monday to Sunday) in their folder and
        date format, dated by the week's Monday; from its template, then a heading a day; entries under today."""
        obs = self.vault / ".obsidian"
        obs.mkdir(exist_ok=True)
        (obs / "daily-notes.json").write_text('{"folder": "Journal", "format": "YYYYMM/YYYY-MM-DD", "template": "Templates/Day"}')
        (obs / "templates.json").write_text('{"dateFormat": "YYYY/MM/DD"}')
        (self.vault / "Templates").mkdir(exist_ok=True)
        (self.vault / "Templates" / "Day.md").write_text("# {{title}}\n\nWeek from {{date}} · {{date:dddd}}\n")
        try:
            sun = date(2026, 11, 22)  # a Sunday: the week that began on Monday the 16th
            info = notes.week_note(sun)
            self.assertEqual((info["kind"], info["label"], info["heading"]), ("week", "This week", "## Sun 11-22"))
            self.assertTrue(info["path"].endswith("Journal/202611/Week of 2026-11-16.md"))  # (in its date folder, named as its title)
            self.assertEqual(notes.week_note(date(2026, 11, 16))["path"], info["path"])  # Monday: the same note
            self.assertTrue(notes.week_note(date(2026, 11, 23))["path"].endswith("Journal/202611/Week of 2026-11-23.md"))  # the next Monday: a new one
            text = Path(info["path"]).read_text()
            self.assertEqual(text, "# Week of 2026-11-16\n\nWeek from 2026/11/16 · Monday\n")  # (Obsidian's template: no day headings)
            notes.append_today("first thought", d=sun)
            notes.append_today("> quoted", source="[demo #3](orbit://session/x#3)", d=sun)
            text = Path(info["path"]).read_text()
            self.assertTrue(text.split("## Sun 11-22\n", 1)[1].startswith("\nfirst thought\n\n> quoted\n— [demo #3]"))
            self.assertNotIn("\n\n\n", text)
            self.assertEqual(self.req("GET", "/api/notes/info")[1]["journal"], {"kind": "week", "label": "This week", "folder": "Journal"})
            # this week's note of the old kind (named by the date, a "# Week of" heading): renamed, the heading dropped
            (obs / "daily-notes.json").write_text('{"folder": "Journal", "format": "YYYYMM/YYYY-MM-DD"}')
            old = self.vault / "Journal" / "202611" / "2026-11-30.md"
            old.write_text("# Week of 2026-11-30\n\nkept text\n")
            (self.vault / "Links.md").write_text("see [[2026-11-30]]\n")
            new = Path(notes.week_note(date(2026, 12, 2))["path"])
            self.assertEqual((new.name, new.read_text(), old.exists()), ("Week of 2026-11-30.md", "kept text\n", False))
            self.assertIn("[[Week of 2026-11-30]]", (self.vault / "Links.md").read_text())
            self.assertEqual(notes.moment("[Week] ww, ddd Do MMM YY", date(2026, 1, 3)), "Week 01, Sat 3rd Jan 26")
        finally:
            (obs / "daily-notes.json").unlink()

    def test_today_api_and_empty(self):
        _, r = self.req("POST", "/api/notes/today", {"text": "from the page"})
        self.assertIn("from the page", Path(r["path"]).read_text())
        self.assertEqual(self.req("POST", "/api/notes/today", {"text": "  "})[0], 400)

    def test_aide_note_cli(self):
        env = {**os.environ, "ORBIT_PORT": str(self.port), "ORBIT_SESSION": "sess-1"}
        run = lambda *args, inp=None: subprocess.run([sys.executable, "-m", "orbit.cli.agent", *args], env=env, input=inp,
                                                     capture_output=True, text=True, timeout=30)
        r = run("note", "agent", "wrote", "this")
        self.assertEqual(r.returncode, 0, r.stderr)
        week = Path(r.stdout.strip())
        self.assertIn("agent wrote this", week.read_text())
        self.assertIn("orbit://session/sess-1", week.read_text())
        r = run("note", "-", inp="piped text\n")
        self.assertIn("piped text", week.read_text())
        r = run("note", "--new", "CLI note")
        self.assertTrue(Path(r.stdout.strip()).exists())
        self.assertEqual(run("note", "--week").stdout.strip(), str(week))
        bad = subprocess.run([sys.executable, "-m", "orbit.cli.agent", "note", "x"], env={**env, "ORBIT_PORT": "1", "ORBIT_AGENT_WAIT": "0"},
                             capture_output=True, text=True, timeout=30)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("not running", bad.stderr)


for name in [n for n in dir(test_skeleton.SkeletonTest) if n.startswith("test_")]:
    if name not in NotesTest.__dict__:
        setattr(NotesTest, name, None)

if __name__ == "__main__":
    unittest.main()
