"""Plugin API 1.1 (left-panel tabs, main-tab kinds, badges, open-tab, notify) and Harness Protocol 1.1 (a terminal per
session, attention, skill folders): the server's half (builddoc/plugins.md §5, harness-protocol.md). The page's half
is checked in a browser.

Run: uv run python -m unittest discover -s tests -v
"""

import base64
import json
import os
import sys
import time
from pathlib import Path

import test_skeleton
from orbit import plugins

HERE = Path(__file__).resolve().parent


class Api11Test(test_skeleton.SkeletonTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        t = Path(cls.tmp.name)
        os.environ["FAKE_PROTOCOL_DIR"] = str(t / "store")
        os.environ["FAKE_TERMINAL"] = "1"
        os.environ["ORBIT_PORT"] = str(cls.port)
        inbox = t / "inbox"
        (inbox / "ui").mkdir(parents=True)
        for f in ("sidebar.html", "thread.html"):
            (inbox / "ui" / f).write_text("<!doctype html><p>" + f)
        (inbox / "plugin.toml").write_text(f'''
[plugin]
name = "inbox"
requires_api = ">=1.1,<2"

[harness.tty]
label = "Terminal agent"
command = [{json.dumps(sys.executable)}, {json.dumps(str(HERE / "fake_protocol_agent.py"))}]

[cli]
command = [{json.dumps(sys.executable)}, "-c", "import sys, json; print('menu got', json.load(sys.stdin)['key'])"]

[[ui.sidebar]]
id = "inbox"
title = "Inbox"
icon = "chat"
view = "doc"
order = 45

[[ui.tab]]
kind = "thread"
title = "Conversation"
icon = "chat"
view = "thread"

[[ui.tab.menu]]
id = "source"
title = "Open at the source"
args = ["source"]
''')
        dup = t / "dup"
        dup.mkdir()
        (dup / "plugin.toml").write_text('[plugin]\nname = "dup"\n[[ui.tab]]\nkind = "a"\ntitle = "A"\nview = "doc"\n'
                                         '[[ui.tab]]\nkind = "a"\ntitle = "B"\nview = "doc"\n')
        cls.found = plugins.load(cls.state, [str(inbox), str(dup)])

    @classmethod
    def tearDownClass(cls):
        plugins.unload()
        for k in ("FAKE_PROTOCOL_DIR", "FAKE_TERMINAL", "ORBIT_PORT"):
            os.environ.pop(k, None)
        super().tearDownClass()

    def until(self, check, timeout=10.0, what="it"):
        end = time.time() + timeout
        while time.time() < end:
            v = check()
            if v:
                return v
            time.sleep(0.05)
        self.fail(f"{what} did not happen in {timeout} s")

    def events(self):
        q = self.desk.subscribe()
        return q, lambda kind: self.until(lambda: next((e for e in iter(lambda: json.loads(q.get(timeout=5)), None)
                                                         if e.get("t") == kind), None), what=kind)

    # ------------------------------------------------------------------ plugin API 1.1
    def test_declared_sidebar_and_tab_kinds(self):
        by = {p["name"]: p for p in self.found}
        self.assertTrue(by["inbox"]["ok"], by["inbox"])
        self.assertTrue(by["dup"]["ok"], by["dup"])  # (2.0: the second of a kind is ignored with an error)
        self.assertIn("[[ui.tab]] a: a second one with that kind: ignored", by["dup"]["error"])
        self.assertEqual([t["title"] for t in plugins.LOADED["dup"]["tabs"]], ["A"])
        _, d = self.req("GET", "/api/plugin-ui")
        self.assertEqual(d["sidebars"], [{"plugin": "inbox", "id": "inbox", "title": "Inbox", "icon": "chat", "order": 45,
                                          "view": "doc", "doc": "inbox"}])
        tab = next(t for t in d["tabs"] if t["plugin"] == "inbox")
        self.assertEqual((tab["kind"], tab["view"]), ("thread", "thread"))
        self.assertEqual(tab["menu"], [{"id": "source", "title": "Open at the source", "args": ["source"]}])
        code, r = self.req("POST", "/api/plugins/inbox/run", {"args": ["source"], "stdin": json.dumps({"kind": "thread", "key": "T-7"})})
        self.assertEqual(r["stdout"].strip(), "menu got T-7")

    def test_badge_open_tab_and_notify_reach_the_pages(self):
        q, wait = self.events()
        try:
            self.assertEqual(self.req("POST", "/api/plugins/inbox/badge", {"id": "inbox", "count": 3})[0], 200)
            self.assertEqual(wait("plugin-badges")["badges"], {"inbox:inbox": {"count": 3, "level": "info"}})
            self.assertEqual(self.req("POST", "/api/plugins/inbox/badge", {"id": "nope", "count": 1})[0], 400)
            self.req("POST", "/api/plugins/inbox/open-tab", {"kind": "thread", "key": "T-7", "title": "Deploy question"})
            ev = wait("plugin-open-tab")
            self.assertEqual((ev["plugin"], ev["kind"], ev["key"], ev["title"]), ("inbox", "thread", "T-7", "Deploy question"))
            self.assertEqual(self.req("POST", "/api/plugins/inbox/open-tab", {"kind": "nope", "key": "x"})[0], 400)
            self.req("POST", "/api/plugins/inbox/notify", {"title": "@you in #ops", "body": "can you look?",
                                                           "open": {"kind": "thread", "key": "T-8"}})
            n = wait("plugin-notify")["note"]
            self.assertEqual((n["title"], n["open"]["key"]), ("@you in #ops", "T-8"))
            self.req("POST", "/api/plugins/inbox/badge", {"id": "inbox", "count": 0})
            self.assertEqual(wait("plugin-badges")["badges"], {})
        finally:
            self.desk.unsubscribe(q)

    # ------------------------------------------------------------------ harness protocol 1.1
    def test_a_terminal_attention_and_skill_folders(self):
        (Path(self.work) / ".fake" / "skills" / "triage").mkdir(parents=True)
        (Path(self.work) / ".fake" / "skills" / "triage" / "SKILL.md").write_text("---\nname: triage\ndescription: sort it\n---\n")
        code, s = self.req("POST", "/api/sessions", {"cwd": self.work, "name": "tty", "harness": "tty"})
        self.assertEqual(code, 200, s)
        me = lambda: next(x for x in self.req("GET", "/api/sessions")[1]["sessions"] if x["id"] == s["id"])
        self.until(lambda: me()["terminal"], what="the terminal reported")
        # ⌘T: the attach command on a pty the server owns; typing echoes; it is not the agent's process
        _, sh = self.req("POST", "/api/shell", {"key": s["id"], "terminal": True})
        self.assertTrue(sh["terminal"] and sh["alive"])
        self.req("POST", f"/api/shell/{sh['id']}/input", {"data": "ping-1\n"})
        shell = self.desk.shells.get(sh["id"])
        self.until(lambda: bytes(shell.buf).count(b"ping-1") >= 2, what="the echo")  # (the pty echoes, cat repeats)
        _, again = self.req("POST", "/api/shell", {"key": s["id"], "terminal": True})
        self.assertEqual(again["id"], sh["id"])  # ⌘T again: the same terminal
        self.req("POST", f"/api/shell/{sh['id']}/kill")
        self.assertEqual(me()["state"], "ready")  # closing the terminal never touches the agent
        # attention: set, then cleared
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "attention please"})
        self.until(lambda: me()["attention"] == "waiting for approval", what="attention")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "attention clear"})
        self.until(lambda: me()["attention"] == "", what="attention cleared")
        # skill folders from --describe join `/` completion
        _, sk = self.req("GET", f"/api/skills?cwd={self.work}&harness=tty")
        self.assertIn("triage", [x["name"] for x in sk["skills"]])

    def test_a_session_whose_plugin_is_gone_says_why(self):
        from orbit.desk.session import DRIVERS
        code, s = self.req("POST", "/api/sessions", {"cwd": self.work, "name": "orphan", "harness": "tty"})
        self.assertEqual(code, 200, s)
        cls = DRIVERS.pop("tty")  # as after a restart without the plugin
        try:
            self.desk.get(s["id"]).driver = None
            self.req("POST", f"/api/sessions/{s['id']}/resume", {})
            me = next(x for x in self.req("GET", "/api/sessions")[1]["sessions"] if x["id"] == s["id"])
            self.assertEqual(me["state"], "not ready")
            self.assertIn("plugin is not loaded", me["error"])
        finally:
            DRIVERS["tty"] = cls

    def test_no_terminal_says_so(self):
        s = self.new_session("plain")  # Claude Code (fake): no terminal
        code, r = self.req("POST", "/api/shell", {"key": s["id"], "terminal": True})
        self.assertEqual(code, 400)
        self.assertIn("no terminal", r["error"])


for name in [n for n in dir(test_skeleton.SkeletonTest) if n.startswith("test_")]:
    if name not in Api11Test.__dict__:
        setattr(Api11Test, name, None)
