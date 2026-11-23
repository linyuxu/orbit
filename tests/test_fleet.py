"""⌘K quick ask: Fleet helpers are background sessions (no group, no tab), reused when idle, up to FLEET_MAX per folder.

Run: uv run python -m unittest discover -s tests -p test_fleet.py -v
"""

import os
import subprocess
import sys

import test_skeleton
from orbit.desk import FLEET_GROUP, FLEET_MAX


class FleetTest(test_skeleton.SkeletonTest):
    def agent(self, *args, cwd=None):
        env = {**os.environ, "ORBIT_PORT": str(self.port), "ORBIT_SESSION": ""}
        return subprocess.run([sys.executable, "-m", "orbit.cli.agent", *args], env=env, capture_output=True, text=True,
                              timeout=60, cwd=cwd)

    def test_fleet_pool(self):
        code, r = self.req("POST", "/api/fleet/ask", {"text": "hello", "cwd": self.work})
        self.assertEqual(code, 200, r)
        h1 = r["session"]
        self.assertTrue(h1["fleet"])
        self.assertEqual(h1["group"], FLEET_GROUP)
        self.wait_card(h1["id"], r["card"]["id"], ("completed",))
        # Idle: the same helper answers the next ask.
        _, r2 = self.req("POST", "/api/fleet/ask", {"text": "again", "cwd": self.work})
        self.assertEqual(r2["session"]["id"], h1["id"])
        self.wait_card(h1["id"], r2["card"]["id"], ("completed",))
        # Busy: a new helper, up to FLEET_MAX; then they queue.
        ids = set()
        for i in range(FLEET_MAX + 1):
            _, r3 = self.req("POST", "/api/fleet/ask", {"text": f"slow {i}", "cwd": self.work})
            ids.add(r3["session"]["id"])
        self.assertEqual(len(ids), FLEET_MAX)
        _, d = self.req("GET", "/api/fleet")
        self.assertEqual(len([s for s in d["fleet"] if s["cwd"] == self.work]), FLEET_MAX)
        for s in d["fleet"]:
            self.req("POST", f"/api/sessions/{s['id']}/stop")
        # Moved to a group, a helper becomes an ordinary session.
        _, g = self.req("GET", "/api/sessions")
        _, kept = self.req("POST", f"/api/sessions/{h1['id']}/update", {"group": g["groups"][0]["id"]})
        self.assertFalse(kept["fleet"])

    def test_errors_and_aide_ask(self):
        code, _ = self.req("POST", "/api/fleet/ask", {"text": " ", "cwd": self.work})
        self.assertEqual(code, 400)
        code, _ = self.req("POST", "/api/fleet/ask", {"text": "x", "cwd": "/no/such/dir"})
        self.assertEqual(code, 400)
        r = self.agent("ask", "what", "is", "up", "--folder", self.work)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("answer: what is up", r.stdout)


class PausedTest(test_skeleton.SkeletonTest):
    def test_paused_and_continue(self):
        s = self.new_session("p")
        _, c = self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "die now"})
        card = self.wait_card(s["id"], c["id"], ("paused", "error", "completed"))
        self.assertEqual(card["state"], "paused", card)
        code, nxt = self.req("POST", f"/api/sessions/{s['id']}/continue", {"cid": c["id"]})
        self.assertEqual(code, 200, nxt)
        done = self.wait_card(s["id"], nxt["id"], ("completed",))
        self.assertIn(f"Continue request #{c['id']}", done["text"])
        _, cards = self.req("GET", f"/api/sessions/{s['id']}/cards")
        self.assertTrue(next(x for x in cards["cards"] if x["id"] == c["id"]).get("continued"))
        code, _ = self.req("POST", f"/api/sessions/{s['id']}/continue", {"cid": nxt["id"]})
        self.assertEqual(code, 400)  # not paused
        _, m = self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "maxturns please"})
        self.assertEqual(self.wait_card(s["id"], m["id"], ("paused", "error"))["state"], "paused")


class UploadTest(test_skeleton.SkeletonTest):
    def test_upload_gives_a_path(self):
        import base64
        from pathlib import Path
        code, r = self.req("POST", "/api/upload", {"name": "my shot?.png", "data": base64.b64encode(b"\x89PNG..").decode()})
        self.assertEqual(code, 200, r)
        p = Path(r["path"])
        self.assertTrue(p.is_file())
        self.assertEqual(p.read_bytes(), b"\x89PNG..")
        self.assertTrue(str(p).startswith(str(self.state / "uploads")))
        self.assertTrue(p.name.endswith("my-shot-.png") or p.name.endswith("my-shot.png"), p.name)
        code, _ = self.req("POST", "/api/upload", {"name": "x", "data": "not base64!"})
        self.assertEqual(code, 400)
