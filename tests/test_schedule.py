"""Build step 7 (Schedule): parse WHEN, next runs, firing into sessions, catch-up, `orbit-agent schedule` / `orbit-agent wake`.

Run: uv run python -m unittest discover -s tests -v
"""

import json
import os
import subprocess
import sys
import time
import unittest
from datetime import datetime

import test_skeleton
from orbit.services import schedule


def at(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi).timestamp()


class ParseTest(unittest.TestCase):
    NOW = at(2026, 11, 18, 10)  # a Wednesday

    def nxt(self, when, after=None):
        spec = schedule.parse(when, self.NOW)
        return schedule.next_run(spec, after or self.NOW)

    def test_forms(self):
        self.assertEqual(self.nxt("every 30m"), self.NOW + 1800)
        self.assertEqual(self.nxt("in 2h"), self.NOW + 7200)
        self.assertEqual(self.nxt("daily 09:00"), at(2026, 11, 19, 9))
        self.assertEqual(self.nxt("daily 11:00"), at(2026, 11, 18, 11))
        self.assertEqual(self.nxt("weekdays 9am", at(2026, 11, 20, 10)), at(2026, 11, 23, 9))  # Fri → Mon
        self.assertEqual(self.nxt("mon,wed 18:00"), at(2026, 11, 18, 18))
        self.assertEqual(self.nxt("fri-mon 8:30pm"), at(2026, 11, 20, 20, 30))
        self.assertEqual(self.nxt("tomorrow 9am"), at(2026, 11, 19, 9))
        self.assertEqual(self.nxt("2026-12-01 14:00"), at(2026, 12, 1, 14))
        self.assertEqual(self.nxt("at 7pm"), at(2026, 11, 18, 19))
        self.assertEqual(schedule.describe(schedule.parse("weekdays 9am")), "weekdays at 09:00")
        self.assertEqual(schedule.describe(schedule.parse("every 2h")), "every 2 hours")
        self.assertIsNone(self.nxt("today 9am"))  # passed

    def test_cron(self):
        self.assertEqual(self.nxt("cron 0 9 * * 1-5", at(2026, 11, 20, 10)), at(2026, 11, 23, 9))  # Fri → Mon
        self.assertEqual(self.nxt("*/15 * * * *"), at(2026, 11, 18, 10, 15))
        self.assertEqual(self.nxt("0 9 1 * *"), at(2026, 12, 1, 9))
        self.assertEqual(self.nxt("30 18 * * sat,sun"), at(2026, 11, 21, 18, 30))
        self.assertEqual(self.nxt("0 0 * * 7"), at(2026, 11, 22))  # 7 is Sunday too
        self.assertEqual(schedule.describe(schedule.parse("0 9 * * 1-5")), "cron 0 9 * * 1-5")

    def test_errors_say_what(self):
        for bad, why in [("", "say when"), ("every 0m", "interval"), ("daily 25:00", "time of day"),
                         ("someday 9am", "not a day: 'someday'"), ("2026-02-30 9:00", "date"), ("cron 61 * * * *", "out of range"),
                         ("cron 0 9 * *", "cannot read")]:
            with self.assertRaises(ValueError) as e:
                schedule.parse(bad)
            self.assertIn(why, str(e.exception))


class ScheduleTest(test_skeleton.SkeletonTest):
    def sched(self):
        return self.desk.scheduler

    def agent(self, *args, inp=None, session=""):
        env = {**os.environ, "ORBIT_PORT": str(self.port), "ORBIT_SESSION": session}
        return subprocess.run([sys.executable, "-m", "orbit.cli.agent", *args], env=env, input=inp,
                              capture_output=True, text=True, timeout=60)

    def tearDown(self):
        for it in list(self.sched().items):
            self.sched().delete(it["id"])

    def test_fires_into_a_session_on_time(self):
        s = self.new_session("nightly")
        code, x = self.req("POST", "/api/schedules", {"when": "every 1h", "text": "check the build", "sid": s["id"]})
        self.assertEqual(code, 200, x)
        self.assertEqual(x["describe"], "every hour")
        self.assertEqual(x["target_name"], "nightly")
        self.assertEqual(self.sched().tick(x["next"] - 1), [])  # not yet
        self.assertEqual(self.sched().tick(x["next"] + 1), [x["id"]])
        c = self.wait_card(s["id"], 1, ("completed",))
        self.assertTrue(c["text"].startswith('(schedule "check the build") check the build'), c["text"])
        self.assertIn("journal", c["text"])  # a repeating job: read the journal first, append to it
        _, d = self.req("GET", "/api/schedules")
        y = d["schedules"][0]
        self.assertEqual((y["last"]["sid"], y["last"]["cid"], y["runs"]), (s["id"], 1, 1))
        self.assertAlmostEqual(y["next"], x["next"] + 1 + 3600, delta=1)

    def test_once_disables_itself_and_closed_sessions_reopen(self):
        s = self.new_session("later")
        self.req("POST", f"/api/sessions/{s['id']}/close")
        _, x = self.req("POST", "/api/schedules", {"when": "in 5m", "text": "wake up", "sid": s["id"], "name": "w"})
        self.sched().tick(time.time() + 400)
        self.wait_card(s["id"], 1, ("completed",))
        _, d = self.req("GET", "/api/sessions")
        self.assertIn(s["id"], [z["id"] for z in d["sessions"]])  # reopened
        y = self.req("GET", "/api/schedules")[1]["schedules"][0]
        self.assertFalse(y["enabled"])
        self.assertIsNone(y["next"])

    def test_folder_target_starts_a_new_session_each_run(self):
        _, x = self.req("POST", "/api/schedules", {"when": "daily 07:00", "text": "summarize", "cwd": self.work,
                                                   "name": "digest"})
        self.req("POST", f"/api/schedules/{x['id']}/run")
        y = self.req("GET", "/api/schedules")[1]["schedules"][0]
        self.assertEqual(y["next"], x["next"])  # run now leaves the plan alone
        sid = y["last"]["sid"]
        self.assertTrue(self.desk.get(sid).name.startswith("digest · "))
        self.wait_card(sid, 1, ("completed",))

    def test_validation_and_update(self):
        s = self.new_session()
        self.assertEqual(self.req("POST", "/api/schedules", {"when": "daily 9", "text": "x", "sid": s["id"]})[0], 400)
        self.assertEqual(self.req("POST", "/api/schedules", {"when": "daily 9am", "text": " ", "sid": s["id"]})[0], 400)
        self.assertEqual(self.req("POST", "/api/schedules", {"when": "daily 9am", "text": "x"})[0], 400)
        self.assertEqual(self.req("POST", "/api/schedules", {"when": "daily 9am", "text": "x", "sid": "nope"})[0], 404)
        self.assertEqual(self.req("POST", "/api/schedules", {"when": "today 00:00", "text": "x", "sid": s["id"]})[0], 400)
        _, x = self.req("POST", "/api/schedules", {"when": "daily 9am", "text": "x", "sid": s["id"]})
        _, y = self.req("POST", f"/api/schedules/{x['id']}/update", {"enabled": False})
        self.assertIsNone(y["next"])
        _, y = self.req("POST", f"/api/schedules/{x['id']}/update", {"enabled": True, "when": "every 2h"})
        self.assertAlmostEqual(y["next"], time.time() + 7200, delta=5)
        self.assertEqual(self.req("POST", "/api/schedules/zzzz/run")[1]["error"], "no such schedule")
        _, p = self.req("POST", "/api/schedules/parse", {"when": "mon 9am"})
        self.assertEqual(p["describe"], "Mon at 09:00")

    def test_missed_runs_survive_restart(self):
        s = self.new_session("cron")
        _, recent = self.req("POST", "/api/schedules", {"when": "every 1h", "text": "a", "sid": s["id"]})
        _, old = self.req("POST", "/api/schedules", {"when": "every 1h", "text": "b", "sid": s["id"]})
        path = self.sched().path
        data = json.loads(path.read_text())
        for it in data["schedules"]:  # as if Orbit was off: one is 1 h late, one is 2 days late
            it["next"] = time.time() - (3600 if it["id"] == recent["id"] else 2 * 86400)
        type(self)._unserve()
        path.write_text(json.dumps(data))
        type(self)._serve()
        fired = self.sched().tick()
        self.assertEqual(fired, [recent["id"]])
        y = {z["id"]: z for z in self.req("GET", "/api/schedules")[1]["schedules"]}
        self.assertTrue(y[recent["id"]]["last"]["late"])
        self.assertGreater(y[old["id"]]["next"], time.time())  # skipped to the next run
        self.assertEqual(y[old["id"]]["skipped"], 1)

    def test_aide_schedule_and_wake(self):
        a = self.new_session("agent")
        r = self.agent("wake", "30m", "check", "CI", session=a["id"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("→ agent", r.stdout)
        r = self.agent("schedule", "add", "weekdays 9am", "standup notes", "--name", "standup", "--folder", self.work)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = self.agent("schedule").stdout
        self.assertIn("wake: check CI", out)
        self.assertIn("standup\n    weekdays at 09:00 → new session in work", out)
        self.assertIn("(off)", self.agent("schedule", "off", "standup").stdout)
        self.assertIn("ran standup", self.agent("schedule", "run", "standup").stdout)
        self.assertEqual(self.agent("schedule", "rm", "standup").stdout.strip(), "removed")
        self.assertIn("no schedule matches", self.agent("schedule", "rm", "standup").stderr)
        self.assertIn("outside Orbit", self.agent("schedule", "add", "in 5m", "x").stderr)
        self.assertIn("cannot read", self.agent("wake", "whenever", "x", session=a["id"]).stderr)


    def test_watch_guards_fleet_and_journal(self):
        """A watch runs its check for free and wakes the agent only on new events; guards; the Fleet; the journal."""
        from pathlib import Path
        a = self.new_session("watcher")
        ev = Path(self.work) / "events.txt"
        ev.write_text('{"id": 1, "pr": "first comment"}\n')
        r = self.agent("schedule", "watch", "--every", "10s", "--check", f"cat {ev}", "--max", "1",
                       "answer the new PR comments", session=a["id"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("every 10 seconds → watcher", r.stdout)
        w = self.sched().items[-1]
        self.sched()._fire(w, False)  # the first run learns what is there
        self.assertEqual(len(self.req("GET", f"/api/sessions/{a['id']}/cards")[1]["cards"]), 0)
        ev.write_text('{"id": 1}\n{"id": 2, "pr": "please rename"}\nthe build is red\n')
        out = self.agent("schedule", "check", w["id"]).stdout
        self.assertIn("3 events, 2 new", out)  # nothing sent by a check
        self.assertEqual(len(self.req("GET", f"/api/sessions/{a['id']}/cards")[1]["cards"]), 0)
        self.sched()._fire(w, False)
        cards = self.req("GET", f"/api/sessions/{a['id']}/cards")[1]["cards"]
        self.assertEqual(len(cards), 1)
        self.assertIn("please rename", cards[0]["text"])
        self.assertIn("the build is red", cards[0]["text"])
        self.assertIn(f"jobs/{w['id']}.md", cards[0]["text"])  # read the journal first, append to it
        self.assertIn("Journal: watch: answer the new PR", self.agent("schedule", "journal", w["id"]).stdout)
        ev.write_text('{"id": 3}\n')
        self.sched()._fire(w, False)  # max 1 a day: held back, still new for a later run
        self.assertIn("max", w["last"]["skipped"])
        self.assertEqual([e["id"] for e in self.sched().check(w["id"])["new"]], ["3"])
        self.assertIn("every minute at most", self.agent("schedule", "add", "every 30s", "x", session=a["id"]).stderr)
        r = self.agent("schedule", "add", "cron 0 9 * * 1-5", "summarize the overnight runs", "--fleet",
                       "--folder", self.work)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("cron 0 9 * * 1-5 → Fleet in work", r.stdout)
        f = self.sched().items[-1]
        self.sched()._fire(f, False)
        fleet = [x for x in self.req("GET", "/api/sessions")[1]["sessions"] if x["id"] == f["last"].get("sid")]
        self.assertTrue(fleet and fleet[0]["fleet"], f["last"])
        self.assertIn("(off)", self.agent("schedule", "pause", w["id"]).stdout)
        self.assertFalse(self.sched().get(w["id"])["enabled"])
        u = self.sched().create({"text": "late", "when": "every 1m", "sid": a["id"], "until": "00:00"})
        self.sched()._fire(self.sched().get(u["id"]), False)
        self.assertIn("until", self.sched().get(u["id"])["last"]["skipped"])  # every time of day is past 00:00


for name in [n for n in dir(test_skeleton.SkeletonTest) if n.startswith("test_")]:
    if name not in ScheduleTest.__dict__:
        setattr(ScheduleTest, name, None)

if __name__ == "__main__":
    unittest.main()
