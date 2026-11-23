"""The ⌘J shell: one login shell per session on a pty, streamed over SSE (base64), killed when the session closes.

Run: uv run python -m unittest discover -s tests -p test_shell.py -v
"""

import base64
import json
import os
import queue
import threading
import time
import urllib.request

import test_skeleton


class ShellTest(test_skeleton.SkeletonTest):
    @classmethod
    def setUpClass(cls):
        os.environ["ORBIT_SHELL"] = "/bin/sh"  # quick and the same everywhere
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        os.environ.pop("ORBIT_SHELL", None)

    def stream(self, shid):
        """Events of the shell's SSE stream, in a queue (read by a thread)."""
        q = queue.Queue()

        def read():
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/shell/{shid}/stream", timeout=20) as r:
                for line in r:
                    if line.startswith(b"data: "):
                        q.put(json.loads(line[6:]))

        threading.Thread(target=read, daemon=True).start()
        return q

    def until(self, q, pred, timeout=8):
        out, end = b"", time.time() + timeout
        while time.time() < end:
            try:
                ev = q.get(timeout=0.2)
            except queue.Empty:
                continue
            if ev["t"] in ("screen", "data"):
                out += base64.b64decode(ev["d"])
            if pred(ev, out):
                return ev, out
        self.fail(f"never happened; output so far: {out[-400:]!r}")

    def test_shell_in_the_session_folder(self):
        s = self.new_session("sh")
        code, sh = self.req("POST", "/api/shell", {"key": s["id"]})
        self.assertEqual(code, 200, sh)
        self.assertTrue(sh["alive"])
        self.assertEqual(sh["cwd"], self.work)
        q = self.stream(sh["id"])
        self.until(q, lambda ev, out: ev["t"] == "screen")
        self.req("POST", f"/api/shell/{sh['id']}/resize", {"cols": 91, "rows": 33})
        self.req("POST", f"/api/shell/{sh['id']}/input", {"data": 'echo "mark-$((6*7))"; pwd; stty size; echo "s=$ORBIT_SESSION"\n'})
        _, out = self.until(q, lambda ev, out: b"s=" + s["id"].encode() in out)
        self.assertIn(b"mark-42", out)
        self.assertIn(self.work.encode(), out)
        self.assertIn(b"33 91", out)
        # The same key gets the same shell; a reconnect replays the screen.
        _, again = self.req("POST", "/api/shell", {"key": s["id"]})
        self.assertEqual(again["id"], sh["id"])
        q2 = self.stream(sh["id"])
        ev, out2 = self.until(q2, lambda ev, out: ev["t"] == "screen")
        self.assertIn(b"mark-42", out2)
        # Kill → exit; restart → alive again.
        self.req("POST", f"/api/shell/{sh['id']}/kill")
        self.until(q, lambda ev, out: ev["t"] == "exit")
        code, _ = self.req("POST", f"/api/shell/{sh['id']}/input", {"data": "x"})
        self.assertEqual(code, 400)
        code, r = self.req("POST", f"/api/shell/{sh['id']}/restart")
        self.assertTrue(r["alive"])
        # Closing the session ends its shell.
        self.req("POST", f"/api/sessions/{s['id']}/close")
        end = time.time() + 5
        while time.time() < end and any(x["id"] == sh["id"] for x in self.req("GET", "/api/shells")[1]["shells"]):
            time.sleep(0.05)
        self.assertFalse(any(x["id"] == sh["id"] for x in self.req("GET", "/api/shells")[1]["shells"]))

    def test_errors(self):
        code, _ = self.req("POST", "/api/shell", {})
        self.assertEqual(code, 400)
        code, _ = self.req("POST", "/api/shell/nope/input", {"data": "x"})
        self.assertEqual(code, 404)

    def test_fonts(self):
        code, d = self.req("GET", "/api/fonts")
        self.assertEqual(code, 200)
        self.assertIsInstance(d["families"], list)
