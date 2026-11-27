"""Build step 1 (Skeleton): server + Claude Code driver + send → card, against a fake `claude`.

Run: uv run python -m unittest discover -s tests -v
"""

import json
import os
import shlex
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


class SkeletonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        os.environ["ORBIT_CLAUDE_BIN"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(HERE / 'fake_claude.py'))}"
        os.environ["ORBIT_CLAUDE_PROJECTS"] = str(t / "projects")
        os.environ["ORBIT_NO_NOTIFY"] = "1"  # no macOS notifications from tests
        os.environ["ORBIT_CODEX_SESSIONS"] = str(t / "codex-sessions")  # never the user's Codex store
        (t / "work").mkdir()
        cls.work = str((t / "work").resolve())
        cls.state = t / "state"
        from orbit.workspace import notes
        os.environ.pop("ORBIT_VAULT", None)
        notes.configure(str(t / "data"))  # never the user's data root (schedules)
        cls._serve()

    @classmethod
    def _serve(cls):
        from orbit.desk import Desk
        from orbit.server import make_server

        cls.desk = Desk(cls.state)
        cls.srv = make_server(0, cls.desk)
        cls.port = cls.srv.server_address[1]
        cls.srv.RequestHandlerClass.port = cls.port
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def _unserve(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.desk.shutdown()

    @classmethod
    def tearDownClass(cls):
        cls._unserve()
        cls.tmp.cleanup()

    # ------------------------------------------------------------------ helpers
    def req(self, method, path, body=None, headers=None):
        h = {"Content-Type": "application/json", **(headers or {})}
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method, headers=h,
                                   data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def wait_card(self, sid, cid, states, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            _, d = self.req("GET", f"/api/sessions/{sid}/cards")
            c = next((c for c in d["cards"] if c["id"] == cid), None)
            if c and c["state"] in states:
                return c
            time.sleep(0.05)
        self.fail(f"card {cid} never reached {states}: {c}")

    def new_session(self, name="t"):
        code, s = self.req("POST", "/api/sessions", {"cwd": self.work, "name": name})
        self.assertEqual(code, 200, s)
        return s

    # ------------------------------------------------------------------ tests
    def test_new_session_makes_a_missing_folder(self):
        """New session in a folder that does not exist yet: it is made; a file there, or bad settings: refused, nothing made."""
        d = Path(self.work, "fresh", "project")
        code, s = self.req("POST", "/api/sessions", {"cwd": str(d), "name": "fresh"})
        self.assertEqual(code, 200, s)
        self.assertTrue(d.is_dir())
        self.assertEqual(s["cwd"], os.path.realpath(d))
        Path(self.work, "afile").write_text("x")
        code, r = self.req("POST", "/api/sessions", {"cwd": str(Path(self.work, "afile"))})
        self.assertEqual(code, 400)
        self.assertIn("not a folder", r["error"])
        code, r = self.req("POST", "/api/sessions", {"cwd": str(Path(self.work, "never")), "harness": "nope"})
        self.assertEqual(code, 400)
        self.assertFalse(Path(self.work, "never").exists())

    def test_page_served_from_disk(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/") as r:
            self.assertIn(b"<title>Orbit</title>", r.read())
            self.assertEqual(r.headers["Cache-Control"], "no-store")

    def test_foreign_host_and_origin_refused(self):
        code, _ = self.req("GET", "/api/sessions", headers={"Host": "evil.example:8770"})
        self.assertEqual(code, 401)  # another name (a rebound DNS name): another machine's request, with no token
        code, _ = self.req("POST", "/api/sessions", {"cwd": self.work}, headers={"Origin": "http://evil.example"})
        self.assertEqual(code, 403)

    def test_send_to_card(self):
        s = self.new_session()
        code, c = self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "hello"})
        self.assertEqual(code, 200)
        self.assertEqual(c["id"], 1)
        c = self.wait_card(s["id"], 1, ("completed",))
        self.assertEqual(c["answer"], "answer: hello")
        self.assertEqual([(st["name"], st["summary"], st["done"]) for st in c["steps"]], [("Bash", "echo hi", True)])
        self.assertIsNotNone(c["finished"])

    def test_queue_and_ids(self):
        s = self.new_session()
        for t in ("one", "two", "three"):
            self.req("POST", f"/api/sessions/{s['id']}/send", {"text": t})
        c3 = self.wait_card(s["id"], 3, ("completed",))
        self.assertEqual(c3["answer"], "answer: three")
        _, d = self.req("GET", f"/api/sessions/{s['id']}/cards")
        self.assertEqual([c["state"] for c in d["cards"]], ["completed"] * 3)

    def test_stop_cancels(self):
        s = self.new_session()
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "slow"})
        c = self.wait_card(s["id"], 1, ("running",))
        self.assertEqual(c["steps"][0]["kind"], "note")  # narration before the tool became a step
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "queued behind"})
        self.req("POST", f"/api/sessions/{s['id']}/stop")
        self.wait_card(s["id"], 1, ("cancelled",))
        self.assertEqual(self.wait_card(s["id"], 2, ("cancelled",))["started"], None)

    def test_error_and_followup(self):
        s = self.new_session()
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "fail"})
        self.assertEqual(self.wait_card(s["id"], 1, ("error",))["error"], "boom")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "follow"})
        self.wait_card(s["id"], 2, ("completed",))
        end = time.time() + 5
        while time.time() < end:
            _, d = self.req("GET", f"/api/sessions/{s['id']}/cards")
            if d["cards"][1]["followups"]:
                break
            time.sleep(0.05)
        self.assertEqual(d["cards"][1]["followups"], ["a follow-up"])

    def test_bad_requests(self):
        self.assertEqual(self.req("POST", "/api/sessions", {"cwd": "/no/such/dir"})[0], 400)
        s = self.new_session()
        self.assertEqual(self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "  "})[0], 400)
        self.assertEqual(self.req("POST", "/api/sessions/nope/send", {"text": "x"})[0], 404)

    def test_history_survives_restart(self):
        s = self.new_session("keep")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "remember me"})
        self.wait_card(s["id"], 1, ("completed",))
        type(self)._unserve()
        type(self)._serve()
        _, d = self.req("GET", "/api/sessions")
        self.assertIn("keep", [x["name"] for x in d["sessions"]])
        _, d = self.req("GET", f"/api/sessions/{s['id']}/cards")
        self.assertEqual(d["cards"][0]["text"], "remember me")
        self.assertEqual(d["cards"][0]["answer"], "answer: remember me")
        self.assertEqual(d["cards"][0]["steps"][0]["summary"], "echo hi")
        # A new request continues numbering and resumes the same conversation.
        _, c = self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "again"})
        self.assertEqual(c["id"], 2)
        self.wait_card(s["id"], 2, ("completed",))

    def test_sse_streams_cards(self):
        s = self.new_session()
        got = []

        def listen():
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/events", timeout=10) as r:
                for line in r:
                    if line.startswith(b"data: "):
                        ev = json.loads(line[6:])
                        got.append(ev)
                        if ev.get("t") == "card" and ev["card"]["state"] == "completed":
                            return

        th = threading.Thread(target=listen, daemon=True)
        th.start()
        time.sleep(0.2)
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "stream"})
        th.join(10)
        self.assertEqual(got[0]["t"], "hello")
        states = [e["card"]["state"] for e in got if e.get("t") == "card"]
        self.assertEqual(states[0], "queued")
        self.assertIn("running", states)
        self.assertEqual(states[-1], "completed")


if __name__ == "__main__":
    unittest.main()
