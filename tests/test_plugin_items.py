"""Plugin API 1.3: item sources for ⌘O ([[ui.palette]]: pushed by a service, or refreshed by a command in the
background), the live search through a plugin's running service, and Resume another session from the server's index
(never waiting on a plugin harness; a typed query matched against the whole store).

Run: uv run python -m unittest discover -s tests -v
"""

import json
import os
import shutil
import sys
import time
import urllib.parse
import uuid
from pathlib import Path

import test_skeleton
from orbit import plugins
from orbit.desk import external_index
from orbit.machines.federation import wait_for
from orbit.services import plugin_items, plugin_services

HERE = Path(__file__).resolve().parent
PY = json.dumps(sys.executable)

# The plugin's command: logs each run (spawns.log), `feed` prints feed.jsonl after feed.sleep s, exits feed.code.
CLI = r'''
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
def read(name, default=""):
    try:
        return open(os.path.join(here, name)).read()
    except OSError:
        return default
with open(os.path.join(here, "spawns.log"), "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
if sys.argv[1:2] == ["feed"]:
    time.sleep(float(read("feed.sleep", "0") or 0))
    sys.stdout.write(read("feed.jsonl"))
    sys.exit(int(read("feed.code", "0") or 0))
if sys.argv[1:2] == ["do"]:
    print("did " + json.load(sys.stdin)["item"]["key"])
'''

# The plugin's service: answers Orbit's live search on $ORBIT_SERVICE_PORT (logs each request to search.log).
SERVICE = r'''
import json, os, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
here = os.path.dirname(os.path.abspath(__file__))
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        with open(os.path.join(here, "search.log"), "a") as f:
            f.write(json.dumps({"path": u.path, "q": q, "token": self.headers.get("X-Orbit-Plugin-Token")}) + "\n")
        if self.headers.get("X-Orbit-Plugin-Token") != os.environ["ORBIT_PLUGIN_TOKEN"]:
            code, d = 403, {"error": "not Orbit"}
        else:
            if q.get("q", "").startswith("slow"):
                time.sleep(3)
            code, d = 200, {"items": [{"key": "t1", "label": "already pushed"}, {"key": "found-" + q.get("q", ""),
                            "label": "found " + q.get("q", ""), "time": 5}, {"key": "bad"}]}
        b = json.dumps(d).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def log_message(self, *a):
        pass
ThreadingHTTPServer(("127.0.0.1", int(os.environ["ORBIT_SERVICE_PORT"])), H).serve_forever()
'''

INBOX = f'''[plugin]
name = "inbox"
requires_api = ">=1.3,<2"
[cli]
command = [{PY}, "cli.py"]
[service.bridge]
command = [{PY}, "svc.py"]
[[ui.tab]]
kind = "thread"
title = "Thread"
view = "thread"
[[ui.palette]]
id = "threads"
title = "Threads"
search = true
[[ui.palette]]
id = "cmds"
title = "Commands"
open = "action"
args = ["do"]
[[ui.palette]]
id = "feed"
title = "Feed"
open = "url"
refresh = {{ args = ["feed"], every = "1m" }}
'''

BAD = f'''[plugin]
name = "bad"
[cli]
command = [{PY}, "cli.py"]
[service.one]
command = ["x"]
[service.two]
command = ["y"]
[[ui.palette]]
id = "nokind"
title = "No tab kind"
[[ui.palette]]
id = "Bad Id"
title = "x"
[[ui.palette]]
id = "conversations"
title = "Mine"
open = "url"
[[ui.palette]]
id = "weird"
title = "Weird"
open = "popup"
[[ui.palette]]
id = "which"
title = "Which service"
open = "url"
search = true
[[ui.palette]]
id = "named"
title = "Named service"
open = "url"
search = "two"
[[ui.palette]]
id = "often"
title = "Too often"
open = "url"
refresh = {{ args = ["feed"], every = "5s" }}
[[ui.palette]]
id = "often"
title = "Twice"
open = "url"
'''


def plugin_dir(root: Path, name: str, toml: str, files: dict | None = None) -> Path:
    d = root / name
    d.mkdir(exist_ok=True)
    (d / "plugin.toml").write_text(toml)
    for k, v in (files or {}).items():
        (d / k).write_text(v)
    return d


class ItemsTest(test_skeleton.SkeletonTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        t = Path(cls.tmp.name)
        os.environ["ORBIT_PORT"] = str(cls.port)  # (its processes reach this server, never the user's)
        cls.inbox = plugin_dir(t, "inbox", INBOX, {"cli.py": CLI, "svc.py": SERVICE})
        cls.other = plugin_dir(t, "other", '[plugin]\nname = "other"\n[[ui.tab]]\nkind = "x"\ntitle = "X"\nview = "doc"\n'
                                           '[[ui.palette]]\nid = "threads"\ntitle = "Theirs"\n')
        cls.bad = plugin_dir(t, "bad", BAD, {"cli.py": CLI})
        cls.min_every, cls.tick = plugin_items.MIN_EVERY, plugin_items.TICK

    @classmethod
    def tearDownClass(cls):
        plugins.unload()
        plugin_items.MIN_EVERY, plugin_items.TICK = cls.min_every, cls.tick
        os.environ.pop("ORBIT_PORT", None)
        super().tearDownClass()

    def setUp(self):
        for f in ("spawns.log", "search.log", "feed.jsonl", "feed.sleep", "feed.code"):
            (self.inbox / f).unlink(missing_ok=True)
        plugins.unload()
        shutil.rmtree(self.state / "plugin-items", ignore_errors=True)  # (each test starts with empty sources)
        plugins.load(self.state, [str(self.inbox), str(self.other), str(self.bad)])

    def token(self, name):
        return next(t for t, n in plugins.TOKENS.items() if n == name)

    def put(self, items, name="inbox", source="threads", token=None):
        return self.req("PUT", f"/api/plugins/{name}/items/{source}", {"items": items},
                        headers={"X-Orbit-Plugin-Token": token if token is not None else self.token(name)})

    def post(self, body, name="inbox", source="threads", token=None):
        return self.req("POST", f"/api/plugins/{name}/items/{source}", body,
                        headers={"X-Orbit-Plugin-Token": token if token is not None else self.token(name)})

    def items(self, source="threads", plugin="inbox"):
        code, d = self.req("GET", f"/api/palette?plugin={plugin}&source={source}")
        self.assertEqual(code, 200, d)
        return d["sources"][0]

    def spawns(self):
        try:
            return [json.loads(x) for x in (self.inbox / "spawns.log").read_text().splitlines()]
        except OSError:
            return []

    # ------------------------------------------------------------------ the manifest
    def test_the_declarations(self):
        m = plugins.LOADED["inbox"]
        by = {p["id"]: p for p in m["palette"]}
        self.assertEqual(by["threads"]["kind"], "thread")  # (its only [[ui.tab]] kind)
        self.assertEqual(by["threads"]["search"], "bridge")  # (its only service)
        self.assertEqual(by["cmds"]["args"], ["do"])
        self.assertEqual(by["feed"]["refresh"], {"args": ["feed"], "every": 60.0})
        ui = {(x["plugin"], x["id"]): x for x in self.req("GET", "/api/plugin-ui")[1]["palette"]}
        self.assertEqual(ui[("inbox", "threads")], {"plugin": "inbox", "id": "threads", "title": "Threads", "open": "tab",
                                                    "search": True, "kind": "thread"})
        self.assertFalse(ui[("inbox", "feed")]["search"])

    def test_a_bad_source_is_ignored_with_an_error_and_the_plugin_loads(self):
        p = next(x for x in plugins.PLUGINS if x["name"] == "bad")
        self.assertTrue(p["ok"], p)
        got = {x["id"]: x for x in plugins.LOADED["bad"]["palette"]}
        self.assertEqual(sorted(got), ["named", "often", "which"])
        err = p["error"]
        self.assertIn("[[ui.palette]] nokind: open = \"tab\" opens one of the plugin's [[ui.tab]] kinds; it has none: ignored", err)
        self.assertIn("needs id and title: ignored", err)
        self.assertIn("`conversations` is the source a harness plugin pushes", err)
        self.assertIn("[[ui.palette]] weird: open is tab, action or url: ignored", err)
        self.assertIn('which: search ignored: it has several services, say which: search = "one"', err)
        self.assertEqual((got["which"]["search"], got["named"]["search"]), (None, "two"))
        self.assertIn("refresh every '5s': at least 60 s, so every 60 s", err)
        self.assertEqual(got["often"]["refresh"]["every"], 60.0)
        self.assertIn("[[ui.palette]] often: a second one with that id: ignored", err)
        self.assertEqual(got["often"]["title"], "Too often")

    # ------------------------------------------------------------------ pushed items
    def test_push_upsert_remove_and_the_order(self):
        code, r = self.put([{"key": "t1", "label": "Old", "time": 100}, {"key": "t2", "label": "New", "time": 300,
                            "detail": "#ops", "match": "alice", "junk": "dropped"}, {"key": 3, "label": "No time"}])
        self.assertEqual((code, r), (200, {"count": 3, "dropped": 0}))
        src = self.items()
        self.assertEqual([x["key"] for x in src["items"]], ["t2", "t1", "3"])  # newest first, no time last
        self.assertEqual(src["items"][0], {"key": "t2", "label": "New", "time": 300.0, "detail": "#ops", "match": "alice"})
        self.assertEqual((src["title"], src["open"], src["search"], src["kind"], src["count"]), ("Threads", "tab", True, "thread", 3))
        code, r = self.post({"upsert": [{"key": "t1", "label": "Old, edited", "time": 400}, {"key": "t4", "label": "Four"}],
                             "remove": ["3", "nope"]})
        self.assertEqual((code, r["count"]), (200, 3))
        self.assertEqual([(x["key"], x["label"]) for x in self.items()["items"]],
                         [("t1", "Old, edited"), ("t2", "New"), ("t4", "Four")])
        self.assertEqual(self.put([])[1]["count"], 0)  # a resync to nothing
        self.assertEqual(self.items()["items"], [])

    def test_a_change_is_told_to_the_pages(self):
        q = self.desk.subscribe()
        try:
            self.put([{"key": "a", "label": "A"}])
            end, got = time.time() + 5, None
            while time.time() < end and not got:
                ev = json.loads(q.get(timeout=5))
                got = ev if ev.get("t") == "plugin-items" else None
            updated = self.items()["updated"]
            self.put([{"key": "a", "label": "A"}])  # the same list again: nothing is told, nothing changes
            self.post({"upsert": [{"key": "a", "label": "A"}], "remove": ["gone"]})
            time.sleep(0.3)
            told = [json.loads(q.get_nowait()) for _ in range(q.qsize())]
            self.assertEqual([e for e in told if e.get("t") == "plugin-items"], [])
            self.assertEqual(self.items()["updated"], updated)
        finally:
            self.desk.unsubscribe(q)
        self.assertEqual((got["plugin"], got["source"]), ("inbox", "threads"))

    def test_kept_across_a_restart(self):
        self.put([{"key": "k1", "label": "Kept", "time": 1}])
        self.post({"upsert": [{"key": "k2", "label": "Also", "time": 2}]})
        plugins.load(self.state, [str(self.inbox), str(self.other), str(self.bad)])  # (written first, read back)
        self.assertTrue((self.state / "plugin-items" / "inbox.threads.json").exists())
        type(self)._unserve()
        type(self)._serve()
        self.assertEqual([x["key"] for x in self.items()["items"]], ["k2", "k1"])

    def test_the_caps(self):
        many = [{"key": f"k{i}", "label": "x", "time": i} for i in range(plugin_items.SOURCE_MAX + 5)]
        code, r = self.put(many)
        self.assertEqual((code, r), (200, {"count": plugin_items.SOURCE_MAX, "dropped": 5}))
        keys = [x["key"] for x in self.items()["items"]]
        self.assertEqual(len(keys), plugin_items.SOURCE_MAX)
        self.assertEqual((keys[0], keys[-1]), (f"k{plugin_items.SOURCE_MAX + 4}", "k5"))  # the newest kept
        code, r = self.post({"upsert": [{"key": "new", "label": "x", "time": 10**9}]})
        self.assertEqual(r, {"count": plugin_items.SOURCE_MAX, "dropped": 1})
        code, r = self.put([{"key": "ok", "label": "fine"}, {"key": "big", "label": "x" * 3000}])
        self.assertEqual(code, 400)
        self.assertIn("item 1 ('big'):", r["error"])
        self.assertIn("an item is 2048 bytes at most", r["error"])
        self.assertEqual(len(self.items()["items"]), plugin_items.SOURCE_MAX)  # (refused as a whole: nothing changed)
        for bad, what in (({"label": "no key"}, "key: a string"), ({"key": "k"}, "label: a string"),
                          ({"key": "k", "label": "l", "time": "soon"}, "time: a number"),
                          ({"key": "k", "label": "l", "url": "javascript:x"}, "url: https:// or http://")):
            code, r = self.post({"upsert": [bad]})
            self.assertEqual(code, 400, bad)
            self.assertIn(what, r["error"])

    def test_only_the_plugin_writes_its_own_declared_sources(self):
        code, r = self.put([{"key": "a", "label": "A"}], token="")
        self.assertEqual(code, 403)
        self.assertIn("only inbox writes its items", r["error"])
        self.assertEqual(self.put([{"key": "a", "label": "A"}], token=self.token("other"))[0], 403)  # another's token
        self.assertEqual(self.put([{"key": "a", "label": "A"}], token="orbitp_made_up")[0], 403)
        code, r = self.put([{"key": "a", "label": "A"}], source="secret")
        self.assertEqual(code, 400)
        self.assertIn("inbox declares no item source 'secret'", r["error"])
        code, r = self.put([{"key": "a", "label": "A"}], source="conversations")  # (only a harness plugin has it)
        self.assertEqual(code, 400)
        self.assertEqual(self.put([], name="nope", token="x")[0], 404)
        self.assertEqual(self.items()["items"], [])
        self.assertEqual(self.put([{"key": "b", "label": "B"}], name="other")[0], 200)  # its own of the same name
        self.assertEqual([x["key"] for x in self.items(plugin="other")["items"]], ["b"])
        self.assertEqual(self.items()["items"], [])

    # ------------------------------------------------------------------ refreshed by a command
    def test_refresh_in_the_background_never_per_request(self):
        plugin_items.MIN_EVERY = 0.5
        try:
            plugins.load(self.state, [str(self.inbox)])
        finally:
            plugin_items.MIN_EVERY = self.min_every
        src = plugin_items.SOURCES[("inbox", "feed")]
        self.assertEqual(src.decl["refresh"]["every"], 60.0)  # ("1m": not raised)
        src.decl["refresh"]["every"] = 1.0  # (a test cannot wait a minute)
        (self.inbox / "feed.jsonl").write_text(json.dumps({"key": "u1", "label": "One", "url": "https://example.com/1"})
                                               + "\nnot json\n" + json.dumps({"key": "u2", "label": "Two", "time": 9}) + "\n")
        (self.inbox / "feed.sleep").write_text("0.8")
        t0 = time.time()
        first = self.items("feed")  # older than its every (never ran): a run starts; the page does not wait
        self.assertLess(time.time() - t0, 0.5)
        self.assertEqual(first["items"], [])
        for _ in range(20):  # every request while it runs, and after: no second process
            self.items("feed")
        self.assertTrue(wait_for(lambda: len(self.items("feed")["items"]) == 2, timeout=10))
        self.assertEqual(len(self.spawns()), 1, self.spawns())
        src = self.items("feed")
        self.assertEqual([x["key"] for x in src["items"]], ["u2", "u1"])
        self.assertIn("1 lines skipped", src["error"])  # (a bad line is left out and said)
        for _ in range(20):
            self.items("feed")
        self.assertEqual(len(self.spawns()), 1)  # within its every: not again
        # the list is replaced; a failure keeps the one it had, and says why
        (self.inbox / "feed.sleep").write_text("0")
        (self.inbox / "feed.jsonl").write_text(json.dumps({"key": "u3", "label": "Three"}) + "\n")
        plugin_items.TICK = 0.1
        plugin_items.start()  # (the loop: in the server, from its start)
        self.assertTrue(wait_for(lambda: [x["key"] for x in self.items("feed")["items"]] == ["u3"], timeout=10))
        self.assertNotIn("error", self.items("feed"))
        (self.inbox / "feed.code").write_text("3")
        self.assertTrue(wait_for(lambda: "error" in self.items("feed"), timeout=10))
        self.assertTrue(self.items("feed")["error"].startswith("exited 3"))
        self.assertEqual([x["key"] for x in self.items("feed")["items"]], ["u3"])
        p = next(x for x in self.req("GET", "/api/plugins")[1]["plugins"] if x["name"] == "inbox")
        self.assertEqual(list(p["palette_errors"]), ["feed"])
        self.assertIn("[feed] refresh feed: exited 3", (self.state / "logs" / "plugins" / "inbox.palette.log").read_text())
        n = len(self.spawns())
        time.sleep(0.5)
        self.assertLessEqual(len(self.spawns()) - n, 1)  # (once a second at most: its every)
        self.assertTrue(all(a == ["feed"] for a in self.spawns()))

    # ------------------------------------------------------------------ live search through the service
    def test_search_through_the_running_service(self):
        plugin_services.start_all()
        svc = plugin_services.of("inbox")[0]
        self.assertTrue(wait_for(lambda: svc.state == "running", timeout=10))
        port = svc.status()["port"]
        self.assertTrue(port)
        mine = next(x for x in self.req("GET", "/api/plugins")[1]["plugins"] if x["name"] == "inbox")
        self.assertEqual(mine["services"][0]["port"], port)  # (recorded: $ORBIT_SERVICE_PORT)

        def search(q, source="threads"):
            return self.req("GET", f"/api/palette/search?plugin=inbox&source={source}&q={q}")
        self.assertTrue(wait_for(lambda: search("hello")[0] == 200, timeout=10))  # (once it listens)
        code, d = search("hello")
        self.assertEqual([x["key"] for x in d["items"]], ["t1", "found-hello"])  # (the bad one dropped)
        log = [json.loads(x) for x in (self.inbox / "search.log").read_text().splitlines()]
        self.assertEqual(log[-1], {"path": "/orbit/search", "q": {"source": "threads", "q": "hello", "limit": "30"},
                                   "token": self.token("inbox")})
        n = len(log)
        self.assertEqual(search("h"), (200, {"items": []}))  # fewer than 2 characters: not asked
        self.assertEqual(len((self.inbox / "search.log").read_text().splitlines()), n)
        t0 = time.time()
        code, d = search("slowly")
        self.assertEqual(code, 504)
        self.assertLess(time.time() - t0, 2.5)
        self.assertIn("no answer within 1.5 s", d["error"])
        self.assertEqual(d["items"], [])
        code, d = search("hello", source="feed")
        self.assertEqual(code, 404)
        self.assertIn("has no live search", d["error"])
        svc.stop()  # down: the source still has its items; a search says so
        self.put([{"key": "t1", "label": "Mine"}])
        code, d = search("hello")
        self.assertEqual(code, 503)
        self.assertIn("inbox.bridge is not running", d["error"])
        self.assertEqual(self.items()["items"][0]["key"], "t1")
        self.assertFalse(any(x.get("token") is None for x in
                             map(json.loads, (self.inbox / "search.log").read_text().splitlines())))

    def test_a_search_with_a_wrong_token_is_refused_by_the_service(self):
        plugin_services.start_all()
        svc = plugin_services.of("inbox")[0]
        self.assertTrue(wait_for(lambda: svc.state == "running", timeout=10))
        import urllib.request, urllib.error
        url = f"http://127.0.0.1:{svc.port}/orbit/search?source=threads&q=x"
        self.assertTrue(wait_for(lambda: self.req("GET", "/api/palette/search?plugin=inbox&source=threads&q=xx")[0] == 200))
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(urllib.request.Request(url, headers={"X-Orbit-Plugin-Token": "orbitp_nope"}), timeout=5)
        self.assertEqual(e.exception.code, 403)  # (the service tells Orbit from anyone else by the token)
        svc.stop()

    def test_its_port_is_kept_across_a_restart_of_the_service(self):
        plugin_services.start_all()
        svc = plugin_services.of("inbox")[0]
        self.assertTrue(wait_for(lambda: svc.state == "running", timeout=10))
        port = svc.port
        svc.stop()
        svc.start()
        self.assertTrue(wait_for(lambda: svc.state == "running", timeout=10))
        self.assertEqual(svc.port, port)
        svc.stop()


class ResumeIndexTest(test_skeleton.SkeletonTest):
    """Resume another session: from the server's index; a plugin harness never makes the dialog wait."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        t = Path(cls.tmp.name)
        os.environ["ORBIT_PORT"] = str(cls.port)
        cls.store = t / "slow-store"
        os.environ["FAKE_PROTOCOL_DIR"] = str(cls.store)
        agent = f'command = [{PY}, {json.dumps(str(HERE / "fake_protocol_agent.py"))}]\n'
        cls.slow = plugin_dir(t, "slowplug", '[plugin]\nname = "slowplug"\n[harness.slowco]\nlabel = "Slow Co"\n' + agent)
        cls.push = plugin_dir(t, "pushplug", '[plugin]\nname = "pushplug"\n[harness.pushy]\nlabel = "Pushy"\n'
                                             + agent.replace("]\n", ', "--as-pushy"]\n'))
        cls.budget = external_index.SCAN_BUDGET

    @classmethod
    def tearDownClass(cls):
        plugins.unload()
        external_index.SCAN_BUDGET = cls.budget
        for k in ("ORBIT_PORT", "FAKE_PROTOCOL_DIR", "FAKE_LIST_SLEEP"):
            os.environ.pop(k, None)
        super().tearDownClass()

    def setUp(self):
        plugins.load(self.state, [str(self.slow), str(self.push)])

    def conv_in_store(self, text):
        conv = str(uuid.uuid4())
        self.store.mkdir(exist_ok=True)
        (self.store / f"{conv}.json").write_text(json.dumps({"cwd": self.work, "turns": [{"text": text, "answer": "ok"}]}))
        return conv

    def lists(self, pushy=False):
        """The --list-conversations runs of slowco (or pushy)."""
        try:
            rows = (self.store / "argv.log").read_text().splitlines()
        except OSError:
            return []
        return [x for x in rows if "--list-conversations" in x and ("--as-pushy" in x) == pushy]

    def ext(self, q="", limit=200):
        t0 = time.time()
        code, d = self.req("GET", f"/api/external-sessions?limit={limit}" + (f"&q={urllib.parse.quote(q)}" if q else ""))
        self.assertEqual(code, 200, d)
        return d["sessions"], time.time() - t0

    def test_a_slow_plugin_harness_never_makes_it_wait(self):
        os.environ["FAKE_LIST_SLEEP"] = "3"
        self.desk.external_index.listed.clear()
        try:
            conv = self.conv_in_store("from the slow agent")
            before = len(self.lists())
            got, took = self.ext()
            self.assertLess(took, 1.0)  # (it would have taken 3 s and more)
            self.assertNotIn(conv, [x["id"] for x in got])
            for _ in range(5):  # opened again while it is listing: no second process
                self.assertLess(self.ext()[1], 1.0)
            self.assertTrue(wait_for(lambda: conv in [x["id"] for x in self.ext()[0]], timeout=15))
            self.assertEqual(len(self.lists()) - before, 1)  # once
            hit = next(x for x in self.ext()[0] if x["id"] == conv)
            self.assertEqual((hit["harness"], hit["first"]), ("slowco", "from the slow agent"))
            self.ext()
            self.assertEqual(len(self.lists()) - before, 1)  # younger than 30 s: not asked again
        finally:
            os.environ.pop("FAKE_LIST_SLEEP", None)

    def test_pushed_conversations_feed_the_index(self):
        self.desk.external_index.listed.clear()
        tok = next(t for t, n in plugins.TOKENS.items() if n == "pushplug")
        code, r = self.req("PUT", "/api/plugins/pushplug/items/conversations",
                           {"items": [{"key": "c-1", "label": "Fix the flaky test", "detail": "the test is flaky", "time": 50,
                                       "cwd": self.work, "match": "TICKET-77"}]},
                           headers={"X-Orbit-Plugin-Token": tok})
        self.assertEqual(code, 200, r)
        n = len(self.lists(pushy=True))
        got = [x for x in self.ext()[0] if x["harness"] == "pushy"]
        self.assertEqual([(x["id"], x["title"], x["first"], x["cwd"]) for x in got],
                         [("c-1", "Fix the flaky test", "the test is flaky", self.work)])
        hit = [x for x in self.ext("ticket-77")[0] if x["harness"] == "pushy"]
        self.assertEqual([x["id"] for x in hit], ["c-1"])  # (its `match` is searched)
        self.assertNotIn("conversations", [x["id"] for x in self.req("GET", "/api/palette")[1]["sources"]])
        time.sleep(0.5)
        self.assertEqual(len(self.lists(pushy=True)), n)  # it pushes: its --list-conversations is not run

    def test_the_whole_store_is_searched(self):
        root = Path(os.environ["ORBIT_CLAUDE_PROJECTS"]) / "-many"
        root.mkdir(parents=True, exist_ok=True)
        ids = []
        for i in range(260):  # the oldest one is not among the 200 newest
            conv = str(uuid.uuid4())
            f = root / f"{conv}.jsonl"
            word = "zanzibar migration" if i == 0 else f"routine chore {i}"
            f.write_text(json.dumps({"type": "user", "cwd": self.work, "message": {"role": "user", "content": word}}) + "\n")
            os.utime(f, (1_000_000 + i, 1_000_000 + i))
            ids.append(conv)
        newest, _ = self.ext(limit=200)
        self.assertNotIn(ids[0], [x["id"] for x in newest])
        self.assertIn(ids[-1], [x["id"] for x in newest])
        self.assertEqual([x["time"] for x in newest], sorted((x["time"] for x in newest), reverse=True))
        hits, took = self.ext("zanzibar", limit=200)
        self.assertEqual(hits[0]["id"], ids[0])
        self.assertGreater(hits[0]["score"], 0)
        hits, _ = self.ext(ids[0][:8])  # 4+ characters of its id
        self.assertEqual(hits[0]["id"], ids[0])
        # an open of the dialog reads only what changed (here: one file), within its budget; the rest in the background
        external_index.SCAN_BUDGET = 0
        try:
            f = root / f"{ids[5]}.jsonl"
            f.write_text(json.dumps({"type": "user", "cwd": self.work, "message": {"role": "user", "content": "renamed task"}}) + "\n")
            self.ext()
            self.assertTrue(wait_for(lambda: [x["id"] for x in self.ext("renamed task")[0]][:1] == [ids[5]], timeout=10))
        finally:
            external_index.SCAN_BUDGET = self.budget
        ours = self.req("POST", "/api/attach-session", {"harness": "claude", "id": ids[0], "cwd": self.work})[1]
        self.assertEqual(ours["id"], ids[0])
        self.assertNotIn(ids[0], [x["id"] for x in self.ext("zanzibar")[0]])  # an Orbit session now: not offered
        self.req("POST", f"/api/sessions/{ids[0]}/close", {})


for _c in (ItemsTest, ResumeIndexTest):  # (the skeleton's own tests are not run again)
    for _n in [n for n in dir(test_skeleton.SkeletonTest) if n.startswith("test_")]:
        if _n not in _c.__dict__:
            setattr(_c, _n, None)
del _c, _n
