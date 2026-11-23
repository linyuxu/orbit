"""The Orbit Harness Protocol (builddoc/harness-protocol.md) and plugins that declare harnesses: a plugin folder with
tests/fake_protocol_agent.py as its harness, driven through the server like any other session.

Run: uv run python -m unittest discover -s tests -v
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import test_skeleton
from orbit import plugins

HERE = Path(__file__).resolve().parent


def make_plugin(root: Path, name: str, body: str) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.toml").write_text(body)
    return d


class FitsTest(unittest.TestCase):
    def test_requires_api(self):
        self.assertTrue(plugins.fits(">=1,<2", (1, 0)))
        self.assertTrue(plugins.fits(">=1.0", (1, 3)))
        self.assertFalse(plugins.fits(">=1.4", (1, 3)))
        self.assertFalse(plugins.fits(">=2", (1, 9)))
        self.assertTrue(plugins.fits("", (1, 0)))
        with self.assertRaises(ValueError):
            plugins.fits("about 1", (1, 0))


class ProtocolTest(test_skeleton.SkeletonTest):
    @classmethod
    def setUpClass(cls):
        cls.ptmp = tempfile.TemporaryDirectory()
        root = Path(cls.ptmp.name)
        os.environ["FAKE_PROTOCOL_DIR"] = str(root / "store")
        cls.plugdir = make_plugin(root / "plugins", "fakeco", f'''
[plugin]
name = "fakeco"
version = "0.1.0"
requires_api = ">=1,<2"

[harness.fake]
label = "Fake"
command = [{json.dumps(sys.executable)}, {json.dumps(str(HERE / "fake_protocol_agent.py"))}]
local_only = true
''')
        bad = [make_plugin(root / "plugins", "future", '[plugin]\nname = "future"\nrequires_api = ">=3"\n'),
               make_plugin(root / "plugins", "clash", '[plugin]\nname = "clash"\n[harness.claude]\ncommand = ["x"]\n'),
               make_plugin(root / "plugins", "broken", '[plugin\nname = ')]
        cls.found = plugins.load(root / "nostate", [str(cls.plugdir), *map(str, bad)])
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        plugins.unload()
        os.environ.pop("FAKE_PROTOCOL_DIR", None)
        cls.ptmp.cleanup()

    def store(self) -> Path:
        return Path(os.environ["FAKE_PROTOCOL_DIR"])

    def new_fake(self, name="p"):
        code, s = self.req("POST", "/api/sessions", {"cwd": self.work, "name": name, "harness": "fake"})
        self.assertEqual(code, 200, s)
        return s

    def wait_steps(self, sid):
        end = time.time() + 5
        while time.time() < end:
            _, d = self.req("GET", f"/api/sessions/{sid}/cards")
            if d["cards"] and d["cards"][-1]["steps"]:
                return
            time.sleep(0.05)

    def test_plugins_load_and_bad_ones_are_reported(self):
        by = {p["name"]: p for p in self.found}
        self.assertTrue(by["fakeco"]["ok"], by["fakeco"])
        self.assertEqual(by["fakeco"]["harnesses"], ["fake"])
        self.assertIn("needs Orbit's plugin API >=3", by["future"]["error"])
        self.assertIn("'claude' exists already", by["clash"]["error"])
        self.assertIn("plugin.toml", by["broken"]["error"])
        _, d = self.req("GET", "/api/plugins")
        from orbit import API_VERSION
        self.assertEqual(d["api"], f"{API_VERSION[0]}.{API_VERSION[1]}")
        self.assertEqual(len(d["plugins"]), 4)
        h = {x["id"]: x for x in self.req("GET", "/api/harnesses")[1]["harnesses"]}
        self.assertEqual(h["fake"]["label"], "Fake")
        self.assertEqual([m["value"] for m in h["fake"]["models"]], ["fast", "deep"])  # from --describe
        self.assertEqual((h["fake"]["efforts"], h["fake"]["steers"], h["fake"]["local_only"]), (["low", "high"], True, True))

    def test_a_request_streams_and_finishes(self):
        s = self.new_fake("streams")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "hello there"})
        c = self.wait_card(s["id"], 1, ("completed",))
        self.assertEqual(c["answer"], "answer: hello there")
        self.assertEqual([(x["name"], x["summary"]) for x in c["steps"] if x["kind"] == "tool"], [("Bash", "ls -la")])
        _, ss = self.req("GET", "/api/sessions")
        me = next(x for x in ss["sessions"] if x["id"] == s["id"])
        self.assertEqual((me["context"], me["model_seen"], me["commands"]), (1234, "fast", ["/review"]))
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "again"})
        self.assertEqual(self.wait_card(s["id"], 2, ("completed",))["answer"], "answer: again")

    def test_steer_stop_fail_and_a_subagent(self):
        s = self.new_fake("steer")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "slow job"})
        self.wait_steps(s["id"])
        code, c = self.req("POST", f"/api/sessions/{s['id']}/steer", {"text": "use the cache"})
        self.assertEqual((code, c["steered"]), (200, True))
        self.assertIn("steered: use the cache", self.wait_card(s["id"], 1, ("completed",))["answer"])
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "slow again"})
        self.wait_steps(s["id"])
        self.req("POST", f"/api/sessions/{s['id']}/stop", {})
        self.assertEqual(self.wait_card(s["id"], 2, ("cancelled",))["state"], "cancelled")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "fail please"})
        self.assertEqual(self.wait_card(s["id"], 3, ("error",))["error"], "boom")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "agent go"})
        c = self.wait_card(s["id"], 4, ("completed",))
        agent = next(x for x in c["steps"] if x.get("agent"))
        self.assertEqual((agent["agent"], agent["result"]), ("explorer", "the README says hello"))
        self.assertEqual([(x["name"], x["summary"]) for x in agent["sub"] if x["kind"] == "tool"], [("Read", "README.md")])

    def test_a_process_that_dies_is_continued_and_the_conversation_kept(self):
        from unittest import mock
        from orbit.desk import driving
        s = self.new_fake("dies")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "first"})
        self.wait_card(s["id"], 1, ("completed",))
        with mock.patch.object(driving, "RETRY_WAIT", 0.2):
            self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "die now"})
            self.assertTrue(self.wait_card(s["id"], 2, ("paused",))["error"])
            nxt = self.wait_card(s["id"], 3, ("completed",))
        self.assertTrue(nxt["text"].startswith("Continue request #2"))
        runs = [json.loads(x) for x in (self.store() / "argv.log").read_text().splitlines()]
        conv = [r[r.index("--conversation") + 1] for r in runs if "--conversation" in r]
        self.assertTrue(conv, runs)  # the new process continued the same conversation
        self.assertEqual(len(set(conv)), 1)

    def test_history_after_a_restart_and_attach(self):
        s = self.new_fake("keep")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "remember me"})
        self.wait_card(s["id"], 1, ("completed",))
        type(self)._unserve()
        type(self)._serve()
        _, d = self.req("GET", f"/api/sessions/{s['id']}/cards")
        self.assertEqual([(c["text"], c["answer"]) for c in d["cards"]], [("remember me", "answer: remember me")])
        # a conversation started outside Orbit (a terminal): listed, and attached
        p = subprocess.run([sys.executable, str(HERE / "fake_protocol_agent.py")], cwd=self.work, text=True, timeout=30,
                           input=json.dumps({"type": "request", "id": "r1", "text": "from the terminal"}) + "\n",
                           capture_output=True)
        conv = json.loads(p.stdout.splitlines()[0])["conversation"]
        # (the list comes in the background: the first open does not wait for it, a later one has it)
        from orbit.machines.federation import wait_for
        fake = lambda: [x for x in self.req("GET", "/api/external-sessions?limit=50")[1]["sessions"] if x["harness"] == "fake"]
        self.assertTrue(wait_for(lambda: conv in [x["id"] for x in fake()], timeout=10))
        ext = fake()
        self.assertNotIn(s["id"], [x["id"] for x in ext])
        code, a = self.req("POST", "/api/attach-session", {"harness": "fake", "id": conv, "cwd": self.work})
        self.assertEqual(code, 200, a)
        _, d = self.req("GET", f"/api/sessions/{a['id']}/cards")
        self.assertEqual([c["text"] for c in d["cards"]], ["from the terminal"])
        self.assertNotIn(conv, [x["id"] for x in self.req("GET", "/api/external-sessions?limit=50")[1]["sessions"]])
        usage = {x["id"]: x for x in self.desk.harnesses()}  # (the usage figures come from --usage)
        self.assertIn("fake", usage)
        from orbit.desk.session import DRIVERS
        self.assertEqual(DRIVERS["fake"].usage()["plan"], "Team")
        from orbit.services.usage import Usage
        fake = next(h for h in Usage().refresh()["harnesses"] if h["id"] == "fake")
        self.assertEqual(fake["limits"][0]["used"], 25.0)  # 0.25 from --usage: shown as 25%, like Claude's and Codex's


for name in [n for n in dir(test_skeleton.SkeletonTest) if n.startswith("test_")]:
    if name not in ProtocolTest.__dict__:
        setattr(ProtocolTest, name, None)

if __name__ == "__main__":
    unittest.main()
