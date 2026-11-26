"""Fork to another machine or folder (orbit/desk/forkto.py, the Fork to… dialog): a hand-over there, its latest turns
written to a file on that machine, the first request pointing at it; on its own machine and folder, the fork as before.

Run: uv run python -m unittest discover -s tests -p test_forkto.py -v
"""

import glob
import os
import tempfile
import time
import unittest
from pathlib import Path

import test_remote
from orbit.machines.federation import wait_for


class ForkToTest(test_remote.RemoteTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        url = f"http://127.0.0.1:{cls.rport}/?token={cls.token}"
        assert cls.req(cls, "POST", "/api/remotes", {"name": "rbox", "url": url})[0] == 200

    def setUp(self):
        super().setUp()
        self.assertTrue(wait_for(lambda: any(m["name"] == "rbox" and m["ok"] for m in self.req("GET", "/api/sessions")[1]["machines"])))
        self._tmp_before = set(glob.glob("/tmp/orbit-handover-*.md"))

    def tearDown(self):
        for f in set(glob.glob("/tmp/orbit-handover-*.md")) - self._tmp_before:  # (the files this test wrote "there")
            os.unlink(f)
        super().tearDown()

    def source(self, name):
        s = self.new_session(name)
        c = self.req("POST", f"/api/sessions/{s['id']}/send", {"text": f"build the {name} parser"})[1]
        self.wait_card(s["id"], c["id"], ("completed",))
        return s

    def remote_cards(self, rid, n=1, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            cs = self.rdesk.cards(rid)
            if len(cs) >= n:
                return cs
            time.sleep(0.1)
        self.fail(f"no card in rbox's {rid}")

    def test_to_another_machine(self):
        s = self.source("alpha")
        code, r = self.req("POST", f"/api/sessions/{s['id']}/fork", {"machine": "rbox", "cwd": self.rwork, "name": "alpha there", "harness": "claude"})
        self.assertEqual(code, 200, r)
        self.assertTrue(r["id"].startswith("rbox~") and r["fork_across"], r)
        self.assertEqual(r["name"], "alpha there")
        rid = r["id"].split("~", 1)[1]
        first = self.remote_cards(rid)[0]["text"]
        self.assertIn('Orbit session "alpha"', first)
        self.assertIn("/tmp/orbit-handover-", first)
        f = r["handover"]
        self.assertTrue(f.startswith("/tmp/orbit-handover-") and Path(f).exists(), f)
        md = Path(f).read_text()
        self.assertIn("build the alpha parser", md)  # (its request, and its answer)
        self.assertIn("answer:", md)
        self.assertIn(s["id"][:8], f)

    def test_to_another_folder_here_and_the_plain_fork(self):
        s = self.source("beta")
        other = tempfile.mkdtemp(dir=self.work)
        code, r = self.req("POST", f"/api/sessions/{s['id']}/fork", {"machine": "", "cwd": other})
        self.assertEqual(code, 200, r)
        self.assertTrue(r["fork_across"])
        self.assertEqual(os.path.realpath(r["cwd"]), os.path.realpath(other))
        self.assertTrue(r["handover"].startswith(str(self.state / "handover")))
        self.assertIn("build the beta parser", Path(r["handover"]).read_text())
        c = self.wait_card(r["id"], 1, ("completed",))
        self.assertIn(r["handover"], c["text"])
        # the same machine and folder: the fork as before (the conversation continues: its cards come with it)
        code, f = self.req("POST", f"/api/sessions/{s['id']}/fork", {"machine": "", "cwd": self.work, "name": "beta again"})
        self.assertEqual(code, 200, f)
        self.assertFalse(f.get("fork_across"))
        self.assertEqual(self.req("GET", f"/api/sessions/{f['id']}/cards")[1]["cards"][0]["text"], "build the beta parser")

    def test_from_another_machine_to_here(self):
        code, far = self.req("POST", "/api/sessions", {"cwd": self.rwork, "name": "gamma far", "machine": "rbox", "group": "main"})
        self.assertEqual(code, 200, far)
        rid = far["id"].split("~", 1)[1]
        self.rdesk.send(rid, "tune the gamma model")
        end = time.time() + 10
        while time.time() < end and not (self.rdesk.cards(rid) and self.rdesk.cards(rid)[-1]["state"] == "completed"):
            time.sleep(0.1)
        self.assertTrue(wait_for(lambda: any(x["id"] == far["id"] for x in self.req("GET", "/api/sessions")[1]["sessions"])))
        code, r = self.req("POST", f"/api/sessions/{far['id']}/fork", {"machine": "", "cwd": self.work})
        self.assertEqual(code, 200, r)
        self.assertTrue(r["fork_across"] and not r["id"].startswith("rbox~"))
        self.assertIn("tune the gamma model", Path(r["handover"]).read_text())
        self.assertIn("rbox", self.wait_card(r["id"], 1, ("completed",))["text"])


def load_tests(loader, tests, pattern):  # (its own tests only: not the remote test's again)
    suite = unittest.TestSuite()
    suite.addTests(ForkToTest(n) for n in loader.getTestCaseNames(ForkToTest) if n in vars(ForkToTest))
    return suite


if __name__ == "__main__":
    unittest.main()
