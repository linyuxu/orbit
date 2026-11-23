"""Plugin API 1.2: a plugin may say who a name is ([people]; builddoc/plugins.md §5 "People"). The server's half:
the manifest, the providers' merge, the cache (in memory, on disk), failures, /api/people, the example's provider.

Run: uv run python -m unittest discover -s tests -v
"""

import json
import os
import shutil
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

import test_skeleton
from orbit import API_VERSION, plugins

ROOT = Path(__file__).resolve().parent.parent

FAKE = r'''
import json, os, sys, time
me = os.environ["ORBIT_PLUGIN"]
names = json.load(sys.stdin)["names"]
with open(os.environ["FAKE_PEOPLE_LOG"], "a") as f:
    f.write(json.dumps({"plugin": me, "argv": sys.argv[1:], "names": names}) + "\n")
if me == "junk":
    print("<html>not json</html>")
    sys.exit(0)
if me == "crash":
    sys.exit("directory unreachable")
if me == "slow":
    time.sleep(5)
book = {
    "first": {"Alice Chen": {"avatar": "https://img.example/alice.png", "handle": "alice"},
              "Bob Li": {"avatar": "https://img.example/bob.png"},
              "Dan Http": {"avatar": "http://img.example/dan.png", "handle": "dan"},
              "Eve Html": {"avatar": "data:text/html,<script>alert(1)</script>"},
              "Fay Big": {"avatar": "data:image/png;base64," + "A" * 250000},
              "Gus Js": {"avatar": "javascript:alert(1)"},
              "Not Asked": {"avatar": "https://img.example/x.png"}},
    "second": {"Alice Chen": {"avatar": "https://img.example/alice-2.png", "handle": "achen"},
               "Carol Wu": {"avatar": "data:image/svg+xml;base64,PHN2Zy8+", "handle": "carol"},
               "Dan Http": {"avatar": "https://img.example/dan-2.png"}},
    "slow": {}, "lone": {"Alice Chen": {"handle": "alice"}},
}[me]
print(json.dumps({n: book[n] for n in book if n in names or n == "Not Asked"}))
'''


class Api12Test(test_skeleton.SkeletonTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        t = Path(cls.tmp.name)
        cls.calls = t / "calls.jsonl"
        os.environ["FAKE_PEOPLE_LOG"] = str(cls.calls)
        os.environ["ORBIT_PORT"] = str(cls.port)
        script = t / "fake_people.py"
        script.write_text(FAKE)
        cls.dirs = {}
        for name in ("first", "second", "junk", "crash", "slow", "lone"):
            d = cls.dirs[name] = t / name
            d.mkdir()
            (d / "plugin.toml").write_text(f'[plugin]\nname = "{name}"\nrequires_api = ">=1.2,<2"\n'
                                           f'[cli]\ncommand = [{json.dumps(sys.executable)}, {json.dumps(str(script))}]\n'
                                           f'[people]\nargs = ["people"]\n')
        bad = {"nocli": '[people]\nargs = ["people"]\n',
               "badargs": '[cli]\ncommand = ["x"]\n[people]\nargs = "people"\n',
               "badargs2": '[cli]\ncommand = ["x"]\n[people]\nargs = ["people", 3]\n',
               "notable": '[cli]\ncommand = ["x"]\n'}
        for name, body in bad.items():
            d = cls.dirs[name] = t / name
            d.mkdir()
            top = 'people = "yes"\n' if name == "notable" else ""  # (a top-level key: before any table)
            (d / "plugin.toml").write_text(top + f'[plugin]\nname = "{name}"\n' + body)
        cls.hello = t / "hello"
        shutil.copytree(ROOT / "examples" / "hello", cls.hello)

    @classmethod
    def tearDownClass(cls):
        plugins.unload()
        for k in ("FAKE_PEOPLE_LOG", "ORBIT_PORT"):
            os.environ.pop(k, None)
        super().tearDownClass()

    def setUp(self):
        self.calls.unlink(missing_ok=True)
        (self.state / "cache" / "people.json").unlink(missing_ok=True)

    def load(self, *names):
        return plugins.load(self.state, [str(self.dirs[n]) for n in names])

    def asked(self):
        """Every call the fake providers got: [(plugin, names)]; the arguments were ["people"] each time."""
        if not self.calls.exists():
            return []
        out = [json.loads(x) for x in self.calls.read_text().splitlines()]
        self.assertTrue(all(c["argv"] == ["people"] for c in out), out)  # the names on stdin, never in argv
        return [(c["plugin"], c["names"]) for c in out]

    def people(self, *names):
        code, d = self.req("POST", "/api/people", {"names": list(names)})
        self.assertEqual(code, 200, d)
        return d["people"]

    # ------------------------------------------------------------------ the manifest
    def test_parsing_and_validation(self):
        self.assertGreaterEqual(API_VERSION, (1, 2))  # (1.2 added [people]; later minors keep it)
        by = {p["name"]: p for p in self.load("first", "nocli", "badargs", "badargs2", "notable")}
        self.assertTrue(by["first"]["ok"], by["first"])
        self.assertTrue(by["first"]["people"])
        self.assertEqual(plugins.LOADED["first"]["people"], {"args": ["people"]})
        self.assertIn("[people] runs the plugin's [cli] command, which is missing", by["nocli"]["error"])
        self.assertIn("[people] args: a list of strings", by["badargs"]["error"])
        self.assertIn("[people] args: a list of strings", by["badargs2"]["error"])
        self.assertIn("[people]: a table", by["notable"]["error"])
        _, d = self.req("GET", "/api/plugins")
        self.assertEqual(d["api"], f"{API_VERSION[0]}.{API_VERSION[1]}")
        self.assertEqual({p["name"]: p.get("people") for p in d["plugins"] if p["ok"]}, {"first": True})
        self.assertEqual(plugins.providers(), ["first"])

    def test_check_asks_it_about_one_name(self):
        by = {p["name"]: p for p in plugins.check(self.state, [str(self.dirs["first"]), str(self.dirs["junk"])])}
        self.assertTrue(by["first"]["ok"])
        self.assertIn("not known", by["first"]["people_check"])
        self.assertFalse(by["junk"]["ok"])
        self.assertIn("[people]: its output is not a JSON object", by["junk"]["error"])
        self.assertFalse((self.state / "cache" / "people.json").exists())  # (nothing cached)

    # ------------------------------------------------------------------ answers
    def test_two_providers_merge_in_load_order_and_bad_avatars_go(self):
        self.load("first", "second")
        got = self.people("Alice Chen", "Bob Li", "Carol Wu", "Dan Http", "Eve Html", "Fay Big", "Gus Js", "Nobody")
        self.assertEqual(got["Alice Chen"], {"avatar": "https://img.example/alice.png", "handle": "alice"})  # the first wins
        self.assertEqual(got["Bob Li"], {"avatar": "https://img.example/bob.png"})
        self.assertEqual(got["Carol Wu"], {"avatar": "data:image/svg+xml;base64,PHN2Zy8+", "handle": "carol"})
        self.assertEqual(got["Dan Http"], {"handle": "dan"})  # http: dropped; the first still knew the name
        for n in ("Eve Html", "Fay Big", "Gus Js", "Nobody", "Not Asked"):  # no valid field; unknown; not asked
            self.assertNotIn(n, got)
        calls = self.asked()
        self.assertEqual(calls[0][0], "first")
        self.assertEqual(calls[1], ("second", ["Carol Wu", "Eve Html", "Fay Big", "Gus Js", "Nobody"]))  # only the rest
        # load order: the other way round, the other avatar
        self.load("second", "first")
        self.assertEqual(self.people("Alice Chen")["Alice Chen"]["handle"], "achen")

    def test_answers_are_cached_and_so_is_not_known(self):
        self.load("first")
        self.assertIn("Alice Chen", self.people("Alice Chen", "Nobody"))
        self.assertEqual(len(self.asked()), 1)
        self.assertIn("Alice Chen", self.people("Alice Chen", "Nobody", "  Alice   Chen "))  # spaces collapse
        self.assertEqual(len(self.asked()), 1)  # neither asked again: a known name, a "not known"
        self.people("Bob Li", "Alice Chen")
        self.assertEqual(self.asked()[-1], ("first", ["Bob Li"]))  # only the new one
        # 7 h later: "not known" has expired (6 h), a known name has not (24 h)
        later = time.time() + 7 * 3600
        with mock.patch.object(plugins, "_now", lambda: later):
            self.people("Alice Chen", "Nobody")
        self.assertEqual(self.asked()[-1], ("first", ["Nobody"]))
        with mock.patch.object(plugins, "_now", lambda: later + 18 * 3600):
            self.people("Alice Chen")
        self.assertEqual(self.asked()[-1], ("first", ["Alice Chen"]))

    def test_the_cache_survives_a_reload(self):
        self.load("first")
        self.people("Alice Chen", "Nobody")
        f = self.state / "cache" / "people.json"
        self.assertIn("Alice Chen", json.loads(f.read_text())["people"])
        self.load("first")  # (a restart: the memory is gone)
        self.assertEqual(self.people("Alice Chen", "Nobody")["Alice Chen"]["handle"], "alice")
        self.assertEqual(len(self.asked()), 1)
        self.load("first", "second")  # other providers: the cache does not hold
        self.assertIn("Carol Wu", self.people("Alice Chen", "Carol Wu"))
        self.assertEqual(self.asked()[1], ("first", ["Alice Chen", "Carol Wu"]))

    def test_batches_of_fifty(self):
        self.load("first")
        names = [f"Person {i}" for i in range(120)]
        self.assertEqual(self.people(*names), {})
        self.assertEqual(sorted(len(n) for _, n in self.asked()), [20, 50, 50])
        self.assertEqual(sorted(n for _, ns in self.asked() for n in ns), sorted(names))
        code, d = self.req("POST", "/api/people", {"names": [f"Other {i}" for i in range(300)]})
        self.assertEqual(code, 200)
        self.assertEqual(sum(len(n) for _, n in self.asked()), 120 + plugins.PEOPLE_MAX)  # the rest left out

    # ------------------------------------------------------------------ failures
    def test_junk_crash_and_timeout_never_break_the_caller(self):
        self.load("junk", "crash", "slow", "lone")
        with mock.patch.object(plugins, "PEOPLE_TIMEOUT", 0.5):
            t0 = time.time()
            got = self.people("Alice Chen", "Nobody")
            self.assertLess(time.time() - t0, 4)
        self.assertEqual(got, {"Alice Chen": {"handle": "alice"}})  # the one that answered
        _, d = self.req("GET", "/api/plugins")
        errs = {p["name"]: p.get("people_error") for p in d["plugins"]}
        self.assertIn("not a JSON object", errs["junk"])
        self.assertIn("exited 1: directory unreachable", errs["crash"])
        self.assertIn("no answer within 0.5 s", errs["slow"])
        self.assertIsNone(errs["lone"])
        log = (self.state / "logs" / "plugins" / "crash.people.log").read_text()
        self.assertIn("[people] exited 1: directory unreachable", log)
        # those names count as failed for 10 min: not asked again now, asked again after
        n = len(self.asked())
        self.people("Alice Chen", "Nobody")
        self.assertEqual(len(self.asked()), n)
        with mock.patch.object(plugins, "_now", lambda: time.time() + 11 * 60), \
                mock.patch.object(plugins, "PEOPLE_TIMEOUT", 0.5):
            self.people("Nobody")
        self.assertGreater(len(self.asked()), n)

    def test_a_failed_provider_alone(self):
        self.load("crash")
        self.assertEqual(self.people("Alice Chen"), {})
        entry = json.loads((self.state / "cache" / "people.json").read_text())["people"]["Alice Chen"]
        self.assertLess(entry["until"] - time.time(), plugins.PEOPLE_FAIL_TTL + 5)

    # ------------------------------------------------------------------ the route
    def test_get_with_commas_or_repeated_names(self):
        self.load("first", "second")
        code, d = self.req("GET", "/api/people?names=Alice%20Chen,Bob%20Li&names=Carol%20Wu")
        self.assertEqual(code, 200)
        self.assertEqual(sorted(d["people"]), ["Alice Chen", "Bob Li", "Carol Wu"])
        self.assertEqual(self.req("GET", "/api/people")[1], {"people": {}})
        self.assertEqual(self.req("POST", "/api/people", {"names": "Alice Chen"})[0], 400)

    def test_no_provider_answers_empty_at_once(self):
        self.load("nocli")  # (nothing with [people] loads)
        self.assertEqual(self.req("POST", "/api/people", {"names": ["Alice Chen"]}), (200, {"people": {}}))
        self.assertEqual(self.req("GET", "/api/people?names=Alice%20Chen"), (200, {"people": {}}))
        self.assertEqual(self.asked(), [])
        self.assertFalse((self.state / "cache" / "people.json").exists())

    def test_from_another_machine_people_is_refused(self):
        from orbit.machines import remote
        self.load("first")
        h = type(self).srv.RequestHandlerClass
        tok = {"Authorization": "Bearer " + remote.token(self.state)}
        h.trust_loopback = False
        try:
            self.assertEqual(self.req("GET", "/api/people?names=Alice%20Chen", headers=tok)[0], 403)
            self.assertEqual(self.req("POST", "/api/people", {"names": ["Alice Chen"]}, headers=tok)[0], 403)
        finally:
            h.trust_loopback = True
        self.assertEqual(self.asked(), [])

    # ------------------------------------------------------------------ the example
    def test_the_hello_example_provider(self):
        found = plugins.load(self.state, [str(self.hello)])
        self.assertTrue(found[0]["ok"], found[0])
        self.assertTrue(found[0]["people"])
        got = self.people("Alice Chen", "bob")
        self.assertEqual(got["Alice Chen"]["handle"], "alice")
        self.assertEqual(got["bob"]["handle"], "bob")
        import base64
        prefix = "data:image/svg+xml;base64,"
        self.assertTrue(got["Alice Chen"]["avatar"].startswith(prefix))
        svg = base64.b64decode(got["Alice Chen"]["avatar"][len(prefix):]).decode()
        self.assertIn(">AC</text>", svg)
        self.assertNotEqual(got["Alice Chen"]["avatar"], self.people("Alan Cole")["Alan Cole"]["avatar"])  # its colour


for name in [n for n in dir(test_skeleton.SkeletonTest) if n.startswith("test_")]:
    if name not in Api12Test.__dict__:
        setattr(Api12Test, name, None)

if __name__ == "__main__":
    unittest.main()
