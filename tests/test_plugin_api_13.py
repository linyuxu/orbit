"""Plugin API 1.3: a shortcut for a left-panel tab or a panel (`shortcut` on [[ui.sidebar]] / [[ui.panel]]), and a
harness's quota line in the status bar (`quota` in `--usage`). The server's half: the manifest's checks (a bad one, one
of Orbit's, one another plugin has: ignored with an error, the plugin still loads), /api/plugin-ui, the reserved list
against Orbit's own bindings, the quota passing through.

Run: uv run python -m unittest discover -s tests -v
"""

import json
import os
import re
import sys
import unittest
from pathlib import Path

import test_skeleton
from orbit import API_VERSION, ROOT, plugins

HERE = Path(__file__).resolve().parent


def manifest(name: str, sidebars=(), panels=(), extra: str = "") -> str:
    out = f'[plugin]\nname = "{name}"\nrequires_api = ">=1.3,<2"\n' + extra
    for sid, sc in sidebars:  # (2.0: a doc each; the key is what is checked here)
        out += f'[[ui.sidebar]]\nid = "{sid}"\ntitle = "{sid.title()}"\nview = "doc"\n' + (f"shortcut = {sc}\n" if sc else "")
    for pid, sc in panels:
        out += f'[[ui.panel]]\nid = "{pid}"\ntitle = "{pid.title()}"\nview = "doc"\n' + (f"shortcut = {sc}\n" if sc else "")
    return out


class Api13Test(test_skeleton.SkeletonTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        t = Path(cls.tmp.name)
        cls.dirs = {}
        specs = {
            "good": manifest("good", [("inbox", '"Mod-Shift-S"')], [("threads", '"ctrl-alt-7"'), ("plain", "")]),
            "badform": manifest("badform", [("a", '"Cmd-S"'), ("b", '"Mod-Shift-SS"'), ("c", '"Alt-S"'), ("d", "5"),
                                            ("e", '"Mod-Alt-S"'), ("f", '"Ctrl-Alt-/"')]),
            "orbits": manifest("orbits", [("find", '"Mod-Shift-F"')], [("edge", '"Ctrl-Alt-H"'), ("shot", '"Mod-Shift-3"')]),
            "second": manifest("second", [("inbox", '"Mod-Shift-S"'), ("other", '"Mod-Shift-Y"')], [("again", '"Mod-Shift-Y"')]),
            "broken": manifest("broken", [("inbox", '"Mod-Shift-B"')], extra='[harness.claude]\ncommand = ["x"]\n'),
            "late": manifest("late", [("inbox", '"Mod-Shift-B"')]),
        }
        for name, body in specs.items():
            d = cls.dirs[name] = t / name
            d.mkdir()
            (d / "plugin.toml").write_text(body)
        cls.agent = t / "agent"
        cls.agent.mkdir()
        (cls.agent / "plugin.toml").write_text(
            '[plugin]\nname = "agent"\nrequires_api = ">=1.3,<2"\n[harness.quotaco]\nlabel = "Quota Co"\n'
            f'command = [{json.dumps(sys.executable)}, {json.dumps(str(HERE / "fake_protocol_agent.py"))}]\n')

    @classmethod
    def tearDownClass(cls):
        plugins.unload()
        os.environ.pop("FAKE_QUOTA", None)
        super().tearDownClass()

    def load(self, *names):
        return {p["name"]: p for p in plugins.load(self.state, [str(self.dirs[n]) for n in names])}

    # ------------------------------------------------------------------ the version
    def test_api_has_1_3(self):
        self.assertGreaterEqual(API_VERSION, (1, 3))  # (2.0 kept the shortcuts and the quota)
        self.assertEqual(self.req("GET", "/api/version")[1]["api"], f"{API_VERSION[0]}.{API_VERSION[1]}")
        self.assertTrue(plugins.fits(">=1.3"))

    # ------------------------------------------------------------------ the manifest
    def test_a_good_shortcut_is_kept_in_its_canonical_form(self):
        by = self.load("good")
        self.assertTrue(by["good"]["ok"], by["good"])
        self.assertEqual(by["good"]["error"], "")
        m = plugins.LOADED["good"]
        self.assertEqual(m["sidebars"][0]["shortcut"], "Mod-Shift-S")
        self.assertEqual([p["shortcut"] for p in m["panels"]], ["Ctrl-Alt-7", None])

    def test_a_bad_form_is_ignored_with_an_error_and_the_plugin_loads(self):
        by = self.load("badform")
        p = by["badform"]
        self.assertTrue(p["ok"], p)
        self.assertEqual(len(p["warnings"]), 6, p["warnings"])
        for sid in "abcdef":
            self.assertIn(f"[[ui.sidebar]] {sid}: shortcut", p["error"])
        self.assertIn('"Mod-Shift-<letter or digit>" or "Ctrl-Alt-<letter or digit>"', p["error"])
        self.assertEqual([s["shortcut"] for s in plugins.LOADED["badform"]["sidebars"]], [None] * 6)
        self.assertEqual(len(plugins.LOADED["badform"]["sidebars"]), 6)  # (the tabs are there, without a key)

    def test_one_of_orbits_is_ignored_with_an_error(self):
        p = self.load("orbits")["orbits"]
        self.assertTrue(p["ok"], p)
        self.assertIn("[[ui.sidebar]] find: shortcut Mod-Shift-F ignored: it is reserved (Orbit: search history", p["error"])
        self.assertIn("[[ui.panel]] edge: shortcut Ctrl-Alt-H ignored: it is reserved", p["error"])
        self.assertIn("[[ui.panel]] shot: shortcut Mod-Shift-3 ignored: it is reserved (macOS: screenshot)", p["error"])
        ui = plugins.ui()
        self.assertEqual([x.get("shortcut") for x in ui["sidebars"] + ui["panels"]], [None, None, None])

    def test_between_plugins_the_first_wins(self):
        by = self.load("good", "second")
        self.assertTrue(by["second"]["ok"], by["second"])
        self.assertEqual(by["good"]["error"], "")
        self.assertIn("[[ui.sidebar]] inbox: shortcut Mod-Shift-S ignored: good's [[ui.sidebar]] inbox has it",
                      by["second"]["error"])
        # within one plugin too: its sidebar got Mod-Shift-Y first, its panel's is ignored
        self.assertIn("[[ui.panel]] again: shortcut Mod-Shift-Y ignored: second's [[ui.sidebar]] other has it",
                      by["second"]["error"])
        sc = {(x["plugin"], x["id"]): x.get("shortcut") for x in plugins.ui()["sidebars"] + plugins.ui()["panels"]}
        self.assertEqual(sc[("good", "inbox")], "Mod-Shift-S")
        self.assertEqual(sc[("second", "inbox")], None)
        self.assertEqual(sc[("second", "other")], "Mod-Shift-Y")
        self.assertEqual(sc[("second", "again")], None)
        # the other order: the other one wins
        by = self.load("second", "good")
        self.assertIn("Mod-Shift-S ignored: second's [[ui.sidebar]] inbox has it", by["good"]["error"])

    def test_a_plugin_that_does_not_load_claims_nothing(self):
        by = self.load("broken", "late")
        self.assertFalse(by["broken"]["ok"])
        self.assertTrue(by["late"]["ok"])
        self.assertEqual(by["late"]["error"], "")
        self.assertEqual(plugins.LOADED["late"]["sidebars"][0]["shortcut"], "Mod-Shift-B")

    def test_the_errors_show_where_plugin_problems_show(self):
        self.load("good", "second", "orbits")
        _, d = self.req("GET", "/api/plugins")
        by = {p["name"]: p for p in d["plugins"]}
        self.assertTrue(by["second"]["ok"])
        self.assertIn("Mod-Shift-S ignored", by["second"]["error"])
        self.assertIn("it is reserved", by["orbits"]["error"])
        self.assertEqual(by["good"]["error"], "")
        found = {p["name"]: p for p in plugins.check(self.state, [str(self.dirs[n]) for n in ("good", "second")])}
        self.assertTrue(found["second"]["ok"])  # (`orbit plugin check` prints the error; the plugin is fine)
        self.assertIn("Mod-Shift-S ignored", found["second"]["error"])

    def test_plugin_ui_carries_the_shortcuts(self):
        self.load("good", "second")
        code, d = self.req("GET", "/api/plugin-ui")
        self.assertEqual(code, 200)
        sb = {(x["plugin"], x["id"]): x for x in d["sidebars"]}
        pn = {(x["plugin"], x["id"]): x for x in d["panels"]}
        self.assertEqual(sb[("good", "inbox")]["shortcut"], "Mod-Shift-S")
        self.assertNotIn("shortcut", sb[("second", "inbox")])  # (none: the field is left out, as before 1.3)
        self.assertEqual(pn[("good", "threads")]["shortcut"], "Ctrl-Alt-7")
        self.assertNotIn("shortcut", pn[("good", "plain")])

    # ------------------------------------------------------------------ the reserved list is Orbit's bindings
    def test_every_orbit_binding_a_plugin_could_take_is_reserved(self):
        """A key Orbit binds (the page, the app's menu, the editors) in a form a plugin may use is in
        plugins.RESERVED_SHORTCUTS: add a new Orbit shortcut there too, and plugins are checked against it."""
        found: dict[str, str] = {}
        to_char = lambda k: k[3:] if k.startswith("Key") else k[5:]  # KeyS → S, Digit3 → 3
        for f in sorted((ROOT / "web" / "src").glob("*.js")):
            for mods, key in re.findall(r'"(MS|CA):(Key[A-Z]|Digit[0-9])"', f.read_text()):
                found[("Mod-Shift-" if mods == "MS" else "Ctrl-Alt-") + to_char(key)] = f.name
            for x in re.findall(r"\^CA:Key\[([A-Z]+)\]", f.read_text()):  # /^CA:Key[HJKL]$/
                for c in x:
                    found[f"Ctrl-Alt-{c}"] = f.name
        rs = (ROOT / "app" / "src" / "main.rs").read_text()
        for key in re.findall(r'"CmdOrCtrl\+Shift\+([A-Za-z0-9])"', rs):
            found[f"Mod-Shift-{key.upper()}"] = "main.rs"
        self.assertNotIn("Ctrl+Alt", rs.replace("CmdOrCtrl+Alt", ""))  # (none today: Ctrl-Alt would need adding)
        cm = (ROOT / "web" / "cm" / "entry.js").read_text()
        for key in re.findall(r'"(?:Mod-Shift|Shift-Mod)-([a-zA-Z0-9])"', cm):
            found[f"Mod-Shift-{key.upper()}"] = "entry.js"
        self.assertIn("Mod-Shift-F", found)  # (the scan works)
        self.assertIn("Ctrl-Alt-L", found)
        missing = {k: v for k, v in found.items() if k not in plugins.RESERVED_SHORTCUTS}
        self.assertEqual(missing, {}, "Orbit binds these: add them to plugins.RESERVED_SHORTCUTS")
        for k in plugins.RESERVED_SHORTCUTS:
            self.assertEqual(plugins.shortcut_form(k), k)  # (each in the form a plugin would write it)

    # ------------------------------------------------------------------ the quota
    def test_usage_quota_passes_through(self):
        from orbit.desk.session import DRIVERS
        from orbit.services.usage import Usage
        found = plugins.load(self.state, [str(self.agent)])
        self.assertTrue(found[0]["ok"], found[0])
        try:
            os.environ["FAKE_QUOTA"] = "8 USD left"
            self.assertEqual(DRIVERS["quotaco"].usage()["quota"], "8 USD left")
            h = next(x for x in Usage().refresh()["harnesses"] if x["id"] == "quotaco")
            self.assertEqual((h["quota"], h["limits"][0]["used"], h["label"]), ("8 USD left", 25.0, "Quota Co"))
            os.environ["FAKE_QUOTA"] = "  lots   of\tspace " + "x" * 60
            q = DRIVERS["quotaco"].usage()["quota"]
            self.assertEqual(len(q), 40)
            self.assertTrue(q.startswith("lots of space xxx"))
            os.environ["FAKE_QUOTA"] = ""  # (none: nothing in the answer, as from an older agent)
            self.assertNotIn("quota", DRIVERS["quotaco"].usage())
        finally:
            os.environ.pop("FAKE_QUOTA", None)

    def test_a_quota_alone_or_not_a_string(self):
        from orbit.drivers.protocol import ProtocolDriver
        answers = {"alone": '{"quota": "120 credits"}', "number": '{"limits": [], "quota": 8}',
                   "old": '{"plan": "Team", "limits": [{"label": "Day", "used": 0.5}]}'}
        got = {}
        for k, out in answers.items():
            cls = type("Q", (ProtocolDriver,), {"query": classmethod(lambda c, *a, timeout=0, out=out: out)})
            got[k] = cls.usage()
        self.assertEqual(got["alone"], {"quota": "120 credits", "limits": []})
        self.assertEqual(got["number"], {"limits": []})
        self.assertEqual(got["old"], {"plan": "Team", "limits": [{"label": "Day", "used": 0.5}]})


for name in [n for n in dir(test_skeleton.SkeletonTest) if n.startswith("test_")]:
    if name not in Api13Test.__dict__:
        setattr(Api13Test, name, None)

if __name__ == "__main__":
    unittest.main()
