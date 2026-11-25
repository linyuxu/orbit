"""Extension points a customized build uses instead of patching core files (req room #29): Driver.end() on a close,
$ORBIT_HOME and the old folder moved only when asked, `orbit-agent` / `orbit` finding the server of their state folder,
the Fleet's harness and model, the week note's template.

Run: uv run python -m unittest discover -s tests -v
"""

import json
import os
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import test_skeleton
from orbit import home
from orbit.server.run import take_server_lock
from orbit.workspace import notes


class HomeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.t)}, clear=False)
        self.env.start()
        for k in ("ORBIT_HOME", "ORBIT_PORT", "ORBIT_STATE_DIR"):
            os.environ.pop(k, None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_orbit_home_and_the_old_folder_moved_only_when_asked(self):
        self.assertEqual(home.home(), self.t / ".orbit")
        old = home.legacy_home()
        old.mkdir()
        (old / "settings.json").write_text("{}")
        self.assertEqual(home.home(), self.t / ".orbit")
        self.assertFalse((self.t / ".orbit").exists())  # (home() moves nothing)
        self.assertTrue(home.needs_migration())
        os.environ["ORBIT_HOME"] = str(self.t / "second")
        self.assertEqual(home.home(), self.t / "second")
        self.assertFalse(home.needs_migration())
        self.assertIn("ORBIT_HOME is set", home.migrate())
        self.assertTrue(old.is_dir() and not old.is_symlink())  # (another instance never touches it)
        del os.environ["ORBIT_HOME"]
        self.assertIn("moved", home.migrate())
        self.assertTrue(old.is_symlink() and (self.t / ".orbit" / "settings.json").is_file())
        self.assertIn("nothing to move", home.migrate())

    def test_the_server_port_of_a_state_folder(self):
        st = self.t / "state"
        st.mkdir()
        self.assertEqual(home.server_port(st), 8770)  # (nothing there)
        (st / "server.json").write_text(json.dumps({"pid": 1, "port": 8791, "started": 0}))
        self.assertEqual(home.server_port(st), 8770)  # (no server holds the lock: an old file)
        fd = take_server_lock(st)
        try:
            self.assertEqual(home.server_port(st), 8791)
            os.environ["ORBIT_STATE_DIR"] = str(st)
            self.assertEqual(home.server_port(), 8791)  # (orbit-agent in a terminal: its state folder's server)
            os.environ["ORBIT_PORT"] = "8792"
            self.assertEqual(home.server_port(st), 8792)  # ($ORBIT_PORT first)
        finally:
            os.close(fd)
        del os.environ["ORBIT_PORT"]
        self.assertEqual(home.server_port(st), 8770)
        os.environ["ORBIT_HOME"] = str(st)
        del os.environ["ORBIT_STATE_DIR"]
        self.assertEqual(home.state_dir(), st)


class DeskPointsTest(test_skeleton.SkeletonTest):
    def setUp(self):
        self.req("POST", "/api/settings", {"fleet_harness": "", "fleet_model": "", "week_template": ""})

    def test_close_calls_end_restarts_call_close(self):
        s = self.new_session("Ends")
        sess = self.desk.get(s["id"])
        calls = []

        class Fake:
            def close(self):
                calls.append("close")

            def end(self):
                calls.append("end")
        sess.driver = Fake()
        self.req("POST", f"/api/sessions/{s['id']}/close", {})
        for _ in range(50):
            if calls:
                break
            time.sleep(0.02)
        self.assertEqual(calls, ["end"])
        from orbit.drivers.base import Driver
        d = mock.Mock(spec=Driver)
        Driver.end(d)  # the default: close()
        d.close.assert_called_once_with()

    def test_agents_that_outlive_the_server(self):
        """(#31) link_alive: at start, a session whose agent still runs is followed (started with resume, no new
        agent); a stopped one stays stopped. end_link: closing a session with no driver still ends its agent."""
        from orbit.desk.session import DRIVERS
        from orbit.drivers.base import Driver
        running, asked, ended = set(), [], []

        class Mux(DRIVERS["claude"]):
            @classmethod
            def link_alive(cls, link, session_id="", state_dir=""):
                asked.append((link, session_id))
                return session_id in running

            @classmethod
            def end_link(cls, link, session_id="", state_dir=""):
                ended.append((link, session_id))
        a, b = self.new_session("runs on"), self.new_session("stopped")
        for x in (a, b):
            self.desk.get(x["id"]).driver = None
        running.add(a["id"])
        (self.state / "harness-ids.json").write_text(json.dumps({"claude": {a["id"]: "conv-a"}}))
        with mock.patch.dict(DRIVERS, {"claude": Mux}), mock.patch.object(self.desk, "_start") as start:
            followed = self.desk.follow_living()
            self.assertEqual(followed, [a["id"]])
            start.assert_called_once_with(self.desk.get(a["id"]), resume=True)
            self.assertIn(("conv-a", a["id"]), asked)  # (the linked conversation id; else the session's own)
            self.assertIn((b["id"], b["id"]), asked)
            self.req("POST", f"/api/sessions/{b['id']}/close", {})
            for _ in range(50):
                if ended:
                    break
                time.sleep(0.02)
        self.assertEqual(ended, [(b["id"], b["id"])])
        self.assertFalse(Driver.link_alive("x"))  # (the defaults: not followed, nothing ended)
        self.assertIsNone(Driver.end_link("x"))
        with mock.patch.object(self.desk, "_start") as start:
            self.desk.follow_living()  # (a driver without link_alive is not asked)
            start.assert_not_called()
        (self.state / "harness-ids.json").unlink()

    def test_the_fleet_harness_and_model(self):
        code, e = self.req("POST", "/api/settings", {"fleet_harness": "nope"})
        self.assertEqual(code, 400)
        self.assertIn("fleet_harness is one of", e["error"])
        _, st = self.req("POST", "/api/settings", {"fleet_harness": "codex", "fleet_model": " gpt-x "})
        self.assertEqual((st["fleet_harness"], st["fleet_model"]), ("codex", "gpt-x"))
        with mock.patch.object(self.desk, "_start"), mock.patch.object(self.desk, "send", return_value={"id": 1}):
            r = self.desk.fleet_ask("hello", self.work)
            again = self.desk.fleet_ask("again", self.work)
            self.req("POST", "/api/settings", {"fleet_harness": "", "fleet_model": ""})
            other = self.desk.fleet_ask("third", self.work)
        h = self.desk.get(r["session"]["id"])
        self.assertEqual((h.harness, h.model, h.fleet), ("codex", "gpt-x", True))
        self.assertEqual(again["session"]["id"], h.id)  # (an idle helper of that harness is reused)
        o = self.desk.get(other["session"]["id"])
        self.assertNotEqual(o.id, h.id)  # (the setting changed: a helper of the new one)
        self.assertEqual((o.harness, o.model), ("claude", ""))

    def test_the_week_note_template(self):
        vault = Path(notes.root())
        (vault / "Templates").mkdir(exist_ok=True)
        (vault / "Templates" / "Week.md").write_text("# Week {week}\n\nGoals for {monday}\n\n## Days\n\n{days}\n## Notes\n")
        self.req("POST", "/api/settings", {"week_template": "Templates/Week"})
        info = notes.week_note(date(2026, 3, 4))  # a Wednesday
        text = Path(info["path"]).read_text()
        self.assertTrue(text.startswith("# Week 20260302\n\nGoals for 2026-03-02\n\n## Days\n\n## Mon 03-02\n\n## Tue 03-03\n"), text)
        self.assertIn("## Fri 03-06\n\n## Notes\n", text)
        self.assertNotIn("## Sat", text)
        notes.append_today("done", d=date(2026, 3, 4))
        self.assertIn("## Wed 03-04\n\ndone", Path(info["path"]).read_text())
        self.req("POST", "/api/settings", {"week_template": "Templates/Missing"})
        later = notes.week_note(date(2026, 3, 11))
        self.assertIn("## Sun 03-15", Path(later["path"]).read_text())  # (unreadable: the built-in one)
        self.assertEqual(self.req("POST", "/api/settings", {"week_template": "a\nb"})[0], 400)


if __name__ == "__main__":
    unittest.main()
