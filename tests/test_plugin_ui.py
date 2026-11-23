"""What plugins add to the page (builddoc/plugins.md §5): status items, panels (2.0: views, no pages), actions, with
examples/hello.

Run: uv run python -m unittest discover -s tests -v
"""

import json
import os
import shutil
import time
from pathlib import Path

import test_skeleton
from orbit import plugins
from orbit.services import plugin_services

ROOT = Path(__file__).resolve().parent.parent


class PluginUiTest(test_skeleton.SkeletonTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        t = Path(cls.tmp.name)
        shutil.copytree(ROOT / "examples" / "hello", t / "hello")
        bad = t / "badui"
        bad.mkdir()
        (bad / "plugin.toml").write_text('[plugin]\nname = "badui"\n[[ui.panel]]\nid = "x"\ntitle = "X"\npath = "ui/x.html"\n'
                                         '[[ui.sidebar]]\nid = "y"\ntitle = "Y"\npath = "ui/y.html"\n'
                                         '[[ui.tab]]\nkind = "z"\ntitle = "Z"\npath = "ui/z.html"\n')
        (bad / "ui").mkdir()
        (bad / "ui" / "x.html").write_text("<script>secret</script>")
        os.environ["ORBIT_PORT"] = str(cls.port)
        os.environ["HELLO_EVERY"] = "0.2"
        cls.found = plugins.load(cls.state, [str(t / "hello"), str(bad)])

    @classmethod
    def tearDownClass(cls):
        plugins.unload()
        for k in ("ORBIT_PORT", "HELLO_EVERY"):
            os.environ.pop(k, None)
        super().tearDownClass()

    def test_declared_ui(self):
        by = {p["name"]: p for p in self.found}
        self.assertTrue(by["hello"]["ok"], by["hello"])
        self.assertTrue(by["badui"]["ok"], by["badui"])  # (2.0: its pages are ignored with an error; it loads)
        for what in ("[[ui.panel]] x", "[[ui.sidebar]] y", "[[ui.tab]] z"):
            self.assertIn(f"{what}: path 'ui/", by["badui"]["error"])
        self.assertIn("iframe pages are gone in plugin API 2.0: use view =", by["badui"]["error"])
        self.assertEqual((plugins.LOADED["badui"]["panels"], plugins.LOADED["badui"]["sidebars"], plugins.LOADED["badui"]["tabs"]), ([], [], []))
        _, d = self.req("GET", "/api/plugin-ui")
        self.assertEqual(d["panels"], [{"plugin": "hello", "id": "sessions", "title": "Hello: sessions", "view": "doc",
                                        "doc": "sessions"}])
        self.assertEqual([(a["id"], a["where"], a["args"]) for a in d["actions"]],
                         [("words", "answer", ["words"]), ("where", "menu", ["where"])])

    def test_no_plugin_page_is_served(self):  # (2.0: no iframes; only a harness's declared icon is a plugin's file)
        for path in ("/plugins/badui/ui/x.html", "/plugins/hello/plugin.toml", "/plugins/hello/hello-orbit",
                     "/plugins/hello/ui/../plugin.toml", "/plugins/hello/ui/%2e%2e/plugin.toml", "/plugins/nope/ui/index.html",
                     "/src/plugin-bridge.js"):
            self.assertEqual(self.req("GET", path)[0], 404, path)

    def test_status_items_reach_the_page(self):
        q = self.desk.subscribe()
        try:
            code, d = self.req("POST", "/api/plugins/hello/status", {"id": "t", "text": "3 open", "panel": "sessions", "level": "warn"})
            self.assertEqual(code, 200, d)
            ev = json.loads(q.get(timeout=5))  # (subscribers get each event serialized)
            while ev.get("t") != "plugin-status":
                ev = json.loads(q.get(timeout=5))
        finally:
            self.desk.unsubscribe(q)
        self.assertIn({"plugin": "hello", "id": "t", "text": "3 open", "title": "", "level": "warn", "panel": "sessions"}, ev["status"])
        self.assertEqual(self.req("POST", "/api/plugins/hello/status", {"id": "t", "text": "x", "panel": "nope"})[0], 400)
        self.req("POST", "/api/plugins/hello/status", {"id": "t", "text": ""})  # removed
        self.assertNotIn("t", [x["id"] for x in self.req("GET", "/api/plugin-ui")[1]["status"]])

    def test_the_service_keeps_its_item(self):
        plugin_services.start_all()
        self.new_session("busy")
        got = None
        end = time.time() + 15
        while time.time() < end and not got:
            got = [x for x in self.req("GET", "/api/plugin-ui")[1]["status"] if x["id"] == "pulse"]
            time.sleep(0.1)
        self.assertTrue(got, plugin_services.of("hello")[0].tail())
        self.assertEqual((got[0]["text"], got[0]["panel"]), ("0 working", "sessions"))

    def test_actions_run_the_command_with_what_they_act_on(self):
        card = {"id": 4, "text": "q", "answer": "one two three", "state": "completed", "origin": None}
        code, r = self.req("POST", "/api/plugins/hello/run", {"args": ["words"], "stdin": json.dumps({"session": "s", "card": card})})
        self.assertEqual((code, r["code"], r["stdout"].strip()), (200, 0, "#4's answer has 3 words"))


for name in [n for n in dir(test_skeleton.SkeletonTest) if n.startswith("test_")]:
    if name not in PluginUiTest.__dict__:
        setattr(PluginUiTest, name, None)
