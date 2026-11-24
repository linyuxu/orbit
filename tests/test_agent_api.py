"""Build step 6 (Agent API): the [orbit] line, orbit-agent ctx / open / show / sessions / send.

Run: uv run python -m unittest discover -s tests -v
"""

import json
import os
import subprocess
import sys
import threading
import time
import unittest
import urllib.request
from pathlib import Path

import test_skeleton


class AgentApiTest(test_skeleton.SkeletonTest):
    def agent(self, *args, inp=None, session=""):
        env = {**os.environ, "ORBIT_PORT": str(self.port), "ORBIT_SESSION": session}
        return subprocess.run([sys.executable, "-m", "orbit.cli.agent", *args], env=env, input=inp,
                              capture_output=True, text=True, timeout=60)

    def inputs(self, sid):
        """The raw texts the (fake) agent of session sid received, in order."""
        log = Path(self.tmp.name) / "projects" / "inputs.log"
        return [d["text"] for d in map(json.loads, log.read_text().splitlines()) if d["sid"] == sid]

    def listen(self, kinds, n=1):
        """Collect the first n SSE events of the given kinds (in a thread)."""
        got = []

        def run():
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/events", timeout=10) as r:
                for line in r:
                    if line.startswith(b"data: "):
                        ev = json.loads(line[6:])
                        if ev.get("t") in kinds:
                            got.append(ev)
                            if len(got) >= n:
                                return

        th = threading.Thread(target=run, daemon=True)
        th.start()
        time.sleep(0.2)
        return got, th

    # ------------------------------------------------------------------ [orbit] line
    def test_orbit_line_once_per_process_and_hidden(self):
        s = self.new_session("agent")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "first"})
        c1 = self.wait_card(s["id"], 1, ("completed",))
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "second"})
        c2 = self.wait_card(s["id"], 2, ("completed",))
        self.assertEqual(c1["text"], "first")  # the card shows what the user wrote
        got = self.inputs(s["id"])
        self.assertTrue(got[0].startswith("[orbit] You run inside Orbit"))  # the agent got the line
        self.assertIn(f'session "agent", id {s["id"]}', got[0])
        self.assertTrue(got[0].endswith("\n\nfirst"))
        self.assertEqual(got[1], "second")  # only the first request of the process
        # after a restart the history shows the user's text, and the new process hears the line again
        type(self)._unserve()
        type(self)._serve()
        _, d = self.req("GET", f"/api/sessions/{s['id']}/cards")
        self.assertEqual([c["text"] for c in d["cards"]], ["first", "second"])
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "third"})
        self.wait_card(s["id"], 3, ("completed",))
        self.assertTrue(self.inputs(s["id"])[2].startswith("[orbit] "))

    # ------------------------------------------------------------------ ctx
    def test_ctx(self):
        self.assertIn("not reported", self.agent("ctx").stdout)
        ctx = {"session": {"id": "abc", "name": "demo", "cwd": "/w"}, "tab": {"kind": "file", "path": "/w/a.py", "line": 7},
               "selection": "def f():\n    pass", "files": ["/w/a.py"], "group": "Main"}
        self.assertEqual(self.req("POST", "/api/ctx", ctx)[0], 200)
        out = self.agent("ctx").stdout
        self.assertIn("tab: file /w/a.py:7", out)
        self.assertIn("session: demo · /w", out)
        self.assertIn("    def f():", out)
        data = json.loads(self.agent("ctx", "--json").stdout)
        self.assertEqual(data["tab"]["line"], 7)

    # ------------------------------------------------------------------ open / show
    def test_open_broadcasts_without_taking_focus(self):
        f = Path(self.work) / "x.py"
        f.write_text("print(1)\n")
        s = self.new_session("opener")
        got, th = self.listen({"open"})
        r = self.agent("open", f"{f}:3", "--side", session=s["id"])
        self.assertEqual(r.returncode, 0, r.stderr)
        th.join(5)
        self.assertEqual(got[0], {"t": "open", "path": os.path.realpath(f), "line": 3, "side": True, "by": "opener",
                                  "sid": s["id"]})
        self.assertNotEqual(self.agent("open", "/no/such/file").returncode, 0)

    def test_show_serves_a_sandboxed_page(self):
        got, th = self.listen({"show"})
        r = self.agent("show", "-", "--title", "Chart", inp="<h1>hello</h1>")
        self.assertEqual(r.returncode, 0, r.stderr)
        th.join(5)
        ev = got[0]
        self.assertEqual((ev["title"], ev["sid"]), ("Chart", ""))  # (from a terminal: no session; the page then uses the tab in front)
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{ev['url']}") as resp:
            self.assertEqual(resp.read(), b"<h1>hello</h1>")
            self.assertIn("sandbox", resp.headers["Content-Security-Policy"])
        self.assertEqual(self.req("GET", "/show/nope")[0], 404)
        # a non-HTML file goes to the side tab as a file
        md = Path(self.work) / "r.md"
        md.write_text("# r\n")
        got, th = self.listen({"open"})
        self.agent("show", str(md))
        th.join(5)
        self.assertTrue(got[0]["side"])

    # ------------------------------------------------------------------ sessions / send
    def test_sessions_and_send(self):
        a = self.new_session("asker")
        b = self.new_session("helper")
        out = self.agent("sessions", session=a["id"]).stdout
        self.assertIn("asker (you)", out)
        self.assertIn("helper", out)
        r = self.agent("send", "HELPER", "what", "is", "2+2", "--wait", session=a["id"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('(from session "asker") what is 2+2', r.stdout)  # the fake echoes the request
        _, d = self.req("GET", f"/api/sessions/{b['id']}/cards")
        self.assertEqual(d["cards"][-1]["text"], '(from session "asker") what is 2+2')
        self.assertIn("that is this session", self.agent("send", "asker", "hi", session=a["id"]).stderr)
        self.assertIn("no such session", self.agent("send", "nobody", "hi").stderr)
        r = self.agent("send", b["id"][:8], "-", inp="piped")
        self.assertIn("sent to helper", r.stdout)


    def test_steer_another_sessions_running_request(self):
        a = self.new_session("lead")
        b = self.new_session("worker2")
        self.req("POST", f"/api/sessions/{b['id']}/send", {"text": "slow build"})
        end = time.time() + 5
        while time.time() < end:  # until its tool step runs
            _, d = self.req("GET", f"/api/sessions/{b['id']}/cards")
            if d["cards"] and d["cards"][0]["steps"]:
                break
            time.sleep(0.05)
        r = self.agent("steer", "worker2", "skip", "the", "tests", "--wait", session=a["id"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("joined worker2 #1", r.stderr)
        self.assertIn('steered: (from session "lead") skip the tests', r.stdout)  # one turn, its answer
        _, d = self.req("GET", f"/api/sessions/{b['id']}/cards")
        self.assertEqual(len(d["cards"]), 1)
        r = self.agent("steer", "worker2", "now this", session=a["id"])  # idle: an ordinary request
        self.assertIn("sent to worker2 as #2", r.stdout)
        self.assertIn("that is this session", self.agent("steer", "lead", "x", session=a["id"]).stderr)

    def test_new_groups_wait_history_search_continue(self):
        out = self.agent("groups").stdout
        self.assertTrue(out.strip())  # at least the default group
        group = out.split("\t")[0]
        r = self.agent("new", self.work, "--name", "worker", "--group", group, "--mode", "plan", "count", "the", "files")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("started worker", r.stdout)
        sid = r.stdout.split("(")[1].split(")")[0]
        w = self.agent("wait", "worker")
        self.assertEqual(w.returncode, 0, w.stderr)
        self.assertIn("count the files", w.stdout)  # the fake echoes the request
        _, s = self.req("POST", "/api/resolve-session", {"ref": "worker"})
        self.assertEqual(s["mode"], "plan")
        self.assertIn("no paused request", self.agent("continue", "worker").stderr)
        found = self.agent("search", "count", "files").stdout
        self.assertIn(f"orbit://session/{sid}#1", found)
        self.req("POST", f"/api/sessions/{sid}/close")
        self.assertIn("no such session", self.agent("read", "worker").stderr)  # read: open sessions only
        h = self.agent("history", sid)
        self.assertEqual(h.returncode, 0, h.stderr)
        self.assertIn("count the files", h.stdout)  # history reaches a closed one
        self.assertIn("no such folder", self.agent("new", "/nope/nowhere").stderr)

    # ------------------------------------------------------------------ @@ mentions, orbit-agent read
    def test_mention_from_user_and_agent(self):
        a = self.new_session("Data Pipeline")
        b = self.new_session("reviewer")
        self.req("POST", f"/api/sessions/{a['id']}/send", {"text": "load the csv"})
        self.wait_card(a["id"], 1, ("completed",))
        # The user mentions @@reviewer from Data Pipeline: the source, the words, its last turns, how to answer back.
        code, c = self.req("POST", f"/api/sessions/{b['id']}/mention", {"from": a["id"], "text": "@@reviewer check this"})
        self.assertEqual(code, 200, c)
        t = self.wait_card(b["id"], c["id"], ("completed",))["text"]
        self.assertIn(f"from @@Data-Pipeline (session {a['id']})", t)
        self.assertIn("> @@reviewer check this", t)
        self.assertIn("#1 request: load the csv", t)
        self.assertIn(f"orbit-agent read {a['id']} --last 10", t)
        self.assertIn(f'orbit-agent mention {a["id"]}', t)
        # An agent's mention back is a note, not a task, and says not to ping-pong.
        r = self.agent("mention", "@@Data-Pipeline", "done:", "3", "rows", session=b["id"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("mentioned Data Pipeline", r.stdout)
        _, d = self.req("GET", f"/api/sessions/{a['id']}/cards")
        t = d["cards"][-1]["text"]
        self.assertTrue(t.startswith(f"Note from @@reviewer (session {b['id']}, another agent): done: 3 rows"))
        self.assertIn("Do not mention @@reviewer back unless it asks you to", t)
        self.assertIn("that is this session", self.agent("mention", "reviewer", "x", session=b["id"]).stderr)
        self.assertEqual(self.req("POST", f"/api/sessions/{a['id']}/mention", {"from": a["id"], "text": "x"})[0], 400)
        # orbit-agent read: the other session's recent turns, without the [orbit] line.
        out = self.agent("read", "Data-Pipeline", "--last", "5").stdout
        self.assertIn("## #1 (completed)\n\nload the csv", out)
        self.assertNotIn("[orbit]", out)

    def test_edit_resend_withdraw(self):
        s = self.new_session("ed")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "slow one"})
        self.wait_card(s["id"], 1, ("running",))
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "queued two"})
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "queued three"})
        self.assertEqual(self.req("POST", f"/api/sessions/{s['id']}/edit", {"cid": 1, "text": "x"})[0], 400)  # running
        self.assertEqual(self.req("POST", f"/api/sessions/{s['id']}/withdraw", {"cid": 1})[0], 400)
        # A queued request changes in place, or is withdrawn.
        _, c = self.req("POST", f"/api/sessions/{s['id']}/edit", {"cid": 2, "text": "queued two (edited)"})
        self.assertEqual((c["id"], c["text"], c["state"]), (2, "queued two (edited)", "queued"))
        self.assertEqual(self.req("POST", f"/api/sessions/{s['id']}/withdraw", {"cid": 3})[0], 200)
        self.req("POST", f"/api/sessions/{s['id']}/stop")
        self.wait_card(s["id"], 1, ("cancelled",))
        _, d = self.req("GET", f"/api/sessions/{s['id']}/cards")
        by = {c["id"]: c for c in d["cards"]}
        self.assertTrue(by[3].get("hidden"))
        # Stop cancels what is queued too; edit a finished request: it is replaced by a new one.
        _, n = self.req("POST", f"/api/sessions/{s['id']}/edit", {"cid": 1, "text": "fast one"})
        self.assertEqual(n["text"], "fast one")
        self.wait_card(s["id"], n["id"], ("completed",))
        _, d = self.req("GET", f"/api/sessions/{s['id']}/cards")
        self.assertTrue(next(c for c in d["cards"] if c["id"] == 1).get("hidden"))


for name in [n for n in dir(test_skeleton.SkeletonTest) if n.startswith("test_")]:
    if name not in AgentApiTest.__dict__:
        setattr(AgentApiTest, name, None)

if __name__ == "__main__":
    unittest.main()
