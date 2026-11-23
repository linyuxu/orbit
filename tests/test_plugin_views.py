"""Plugin API 2.0: native views (builddoc/plugin-views.md). A plugin's threads and docs kept in Orbit (pushed with its
token, or asked of its service / [cli] in the background, never per request), replies sent through its service with a
pending message in the store, older messages paged in, the hardened `svc:` proxy to its service's files, the manifest
(views, schemes, files, message menus; iframe pages gone), and plugin-mode Markdown (a node check of the page's code).

Run: uv run python -m unittest discover -s tests -v
"""

import http.client
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import unittest
import urllib.parse
from pathlib import Path

import test_skeleton
from orbit import plugins
from orbit.machines.federation import wait_for
from orbit.services import plugin_services, plugin_views

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PY = json.dumps(sys.executable)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

# The view service: threads from threads.json, its files, replies (svc.log: every request it got, with its headers).
SERVICE = r'''
import json, os, sys, threading, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
here = os.path.dirname(os.path.abspath(__file__))
TOKEN = os.environ["ORBIT_PLUGIN_TOKEN"]
lock, busy = threading.Lock(), [0, 0]
def log(d):
    with lock, open(os.path.join(here, "svc.log"), "a") as f:
        f.write(json.dumps(d) + "\n")
def orbit(method, path, body):
    r = urllib.request.Request(os.environ["ORBIT_URL"] + path, method=method, data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json", "X-Orbit-Plugin-Token": TOKEN})
    urllib.request.urlopen(r, timeout=10).read()
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def out(self, code, body, ctype="application/json", extra=()):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
    def do_HEAD(self):
        self.do_GET()
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        log({"method": self.command, "path": self.path, "headers": dict(self.headers)})
        if self.headers.get("X-Orbit-Plugin-Token") != TOKEN:
            return self.out(403, {"error": "not Orbit"})
        p = u.path
        if p == "/orbit/thread":
            if q.get("key", "").startswith("slow"):
                time.sleep(3)
            if q.get("key") == "bad":
                return self.out(500, {"error": "boom"})
            if q.get("before"):
                return self.out(200, {"messages": [{"id": "old2", "markdown": "older 2", "author": {"name": "Ana"}},
                                                   {"id": "bad", "files": "x"}, {"id": "old1", "markdown": "older 1"}][::-1],
                                      "more": q["before"] != "old1"})
            th = json.load(open(os.path.join(here, "threads.json"))).get(q.get("key"), {"messages": []})
            return self.out(200, th)
        if p == "/orbit/doc":
            return self.out(200, {"title": "Fetched " + q.get("id", ""), "markdown": "# From the service"})
        if p == "/files/a.png":
            return self.out(200, PNG, "text/html", [("ETag", '"e1"'), ("Cache-Control", "max-age=60"), ("Set-Cookie", "x=1"),
                                                    ("X-Evil", "1"), ("Content-Security-Policy", "none")])
        if p == "/files/close.png":  # (an answer that closes its connection: as an HTTP/1.0 service's)
            return self.out(200, PNG, "image/png", [("Connection", "close")])
        if p == "/files/page.html":
            return self.out(200, b"<html><script>alert(1)</script></html>", "text/html")
        if p == "/files/page.xhtml":
            return self.out(200, b"<?xml version='1.0'?><html xmlns='http://www.w3.org/1999/xhtml'><script>x</script></html>", "application/xhtml+xml")
        if p == "/files/pic.svg":
            return self.out(200, b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", "image/svg+xml")
        if p == "/files/fake.png":  # (says png, is html)
            return self.out(200, b"<html><script>alert(1)</script>", "image/png")
        if p == "/files/redirect":
            return self.out(302, b"", "text/plain", [("Location", "http://127.0.0.1:1/elsewhere")])
        if p == "/files/big":
            return self.out(200, b"x" * 3000, "application/octet-stream")
        if p == "/files/chunked":
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for _ in range(3):
                self.wfile.write(b"3e8\r\n" + b"y" * 1000 + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            return
        if p == "/files/slow":
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(PNG[:20])
            self.wfile.flush()
            time.sleep(4)
            self.wfile.write(b"z" * 80)
            return
        if p == "/files/range":
            if self.headers.get("Range"):
                return self.out(206, b"abcd", "application/pdf", [("Content-Range", "bytes 0-3/10")])
            return self.out(200, b"abcdefghij", "application/pdf")
        if p == "/files/hold":
            with lock:
                busy[0] += 1
                busy[1] = max(busy[1], busy[0])
                open(os.path.join(here, "busy.max"), "w").write(str(busy[1]))
            time.sleep(0.6)
            with lock:
                busy[0] -= 1
            return self.out(200, PNG, "image/png")
        if p.startswith("/orbit/") or p.startswith("/secret"):
            return self.out(200, b"SECRET", "text/plain")
        return self.out(404, {"error": "not found"})
    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        log({"method": "POST", "path": self.path, "body": b, "token": self.headers.get("X-Orbit-Plugin-Token")})
        if self.headers.get("X-Orbit-Plugin-Token") != TOKEN or self.path != "/orbit/send":
            return self.out(403, {"error": "no"})
        md, cid, key = b["markdown"], b["client_id"], b["key"]
        path = f"/api/plugins/chat/threads/{b['kind']}/{urllib.parse.quote(key, safe='')}"
        if md.startswith("fail"):
            return self.out(500, {"error": "the channel is archived"})
        if md.startswith("pushfirst"):
            orbit("PATCH", path, {"append": [{"id": "r1", "markdown": md, "author": {"name": "Me", "me": True}, "client_id": cid}]})
            return self.out(200, {"id": "r1"})
        if md.startswith("idonly"):
            orbit("PATCH", path, {"append": [{"id": "r2", "markdown": md, "author": {"name": "Me", "me": True}}]})
            return self.out(200, {"id": "r2"})
        if md.startswith("slowsend"):
            time.sleep(1)
        return self.out(200, {"id": "m-" + cid})
    def log_message(self, *a):
        pass
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
ThreadingHTTPServer(("127.0.0.1", int(os.environ["ORBIT_SERVICE_PORT"])), H).serve_forever()
'''

# The plugin without a service: its [cli] answers `thread KIND KEY`, `doc ID` and `send` (stdin), each run logged.
CLI = r'''
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "spawns.log"), "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
a = sys.argv[1:]
if a[:1] == ["thread"]:
    time.sleep(float(open(os.path.join(here, "sleep")).read()) if os.path.exists(os.path.join(here, "sleep")) else 0)
    print(json.dumps({"title": "From the command " + a[2], "messages": [{"id": "c1", "markdown": "hi from cli", "author": {"name": "Bo"}}]}))
elif a[:1] == ["doc"]:
    print(json.dumps({"title": "Cli doc", "markdown": "*cli*"}))
elif a[:1] == ["send"]:
    b = json.load(sys.stdin)
    with open(os.path.join(here, "sent.log"), "a") as f:
        f.write(json.dumps(b) + "\n")
    if b["markdown"].startswith("fail"):
        sys.exit("no way")
    print(json.dumps({"id": "q-" + b["client_id"]}))
elif a[:1] == ["words"]:
    print(len(json.load(sys.stdin)["message"]["markdown"].split()), "words")
'''

CHAT = f'''[plugin]
name = "chat"
requires_api = ">=2,<3"
schemes = ["slack", "javascript", "Bad!"]
[cli]
command = [{PY}, "cli.py"]
[service.bridge]
command = [{PY}, "svc.py"]
files = "/files/"
[[ui.tab]]
kind = "thread"
title = "Thread"
view = "thread"
[[ui.tab]]
kind = "dm"
title = "DM"
view = "thread"
[[ui.tab]]
kind = "page"
title = "Page"
view = "doc"
[[ui.tab]]
kind = "old"
title = "Old"
path = "ui/old.html"
[[ui.tab]]
kind = "weird"
title = "Weird"
view = "board"
[[ui.message.menu]]
id = "words"
title = "Words"
args = ["words"]
[[ui.palette]]
id = "inbox"
title = "Inbox"
kind = "thread"
[[ui.sidebar]]
id = "inbox"
title = "Inbox"
view = "list"
source = "inbox"
[[ui.sidebar]]
id = "nosrc"
title = "No source"
view = "list"
source = "nope"
[[ui.panel]]
id = "status"
title = "Status"
view = "doc"
[[ui.panel]]
id = "one"
title = "One thread"
view = "thread"
kind = "thread"
key = "T1"
'''

QUIET = f'''[plugin]
name = "quiet"
requires_api = ">=2"
[cli]
command = [{PY}, "cli.py"]
[[ui.tab]]
kind = "thread"
title = "Thread"
view = "thread"
[[ui.panel]]
id = "notes"
title = "Notes"
view = "doc"
'''

OTHER = '''[plugin]
name = "other"
[service.a]
command = ["true"]
files = "/orbit/x/"
[service.b]
command = ["true"]
files = "../up/"
[[ui.tab]]
kind = "thread"
title = "Thread"
view = "thread"
'''


def plugin_dir(root: Path, name: str, toml: str, files: dict | None = None) -> Path:
    d = root / name
    d.mkdir(exist_ok=True)
    (d / "plugin.toml").write_text(toml)
    for k, v in (files or {}).items():
        (d / k).write_text(v)
    return d


class ViewsTest(test_skeleton.SkeletonTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        t = Path(cls.tmp.name)
        os.environ["ORBIT_PORT"] = str(cls.port)
        cls.chat = plugin_dir(t, "chat", CHAT, {"svc.py": SERVICE, "cli.py": CLI, "threads.json": "{}"})
        cls.quiet = plugin_dir(t, "quiet", QUIET, {"cli.py": CLI})
        cls.other = plugin_dir(t, "other", OTHER)
        cls.saved = {k: getattr(plugin_views, k) for k in ("STALE", "PROXY_MAX", "PROXY_TIMEOUT", "VIEWS_MAX", "THREAD_MAX")}

    @classmethod
    def tearDownClass(cls):
        plugins.unload()
        os.environ.pop("ORBIT_PORT", None)
        super().tearDownClass()

    def setUp(self):
        for k, v in self.saved.items():
            setattr(plugin_views, k, v)
        for d in (self.chat, self.quiet):
            for f in ("svc.log", "spawns.log", "sent.log", "busy.max", "sleep"):
                (d / f).unlink(missing_ok=True)
        (self.chat / "threads.json").write_text("{}")
        plugins.unload()
        shutil.rmtree(self.state / "plugin-threads", ignore_errors=True)
        self.found = {p["name"]: p for p in plugins.load(self.state, [str(self.chat), str(self.quiet), str(self.other)])}

    def tearDown(self):
        plugin_services.stop_all()

    # ------------------------------------------------------------------ helpers
    def token(self, name="chat"):
        return next(t for t, n in plugins.TOKENS.items() if n == name)

    def tpath(self, key, kind="thread", name="chat"):
        return f"/api/plugins/{name}/threads/{kind}/{urllib.parse.quote(key, safe='')}"

    def push(self, method, key, body, kind="thread", name="chat", token=None):
        return self.req(method, self.tpath(key, kind, name), body,
                        headers={"X-Orbit-Plugin-Token": self.token(name) if token is None else token})

    def get(self, key, kind="thread", name="chat", q=""):
        return self.req("GET", self.tpath(key, kind, name) + q)

    def msgs(self, key, **kw):
        code, d = self.get(key, **kw)
        self.assertEqual(code, 200, d)
        return d["messages"]

    def m(self, i, text="", **kw):
        return {"id": str(i), "markdown": text or f"message {i}", "author": {"name": "Ana Li"},
                "time": 1000 + (i if isinstance(i, int) else 0), **kw}

    def events(self):
        q = self.desk.subscribe()
        got = []

        def take(kind, timeout=5.0):
            end = time.time() + timeout
            while time.time() < end:
                try:
                    ev = json.loads(q.get(timeout=max(0.01, end - time.time())))
                except Exception:  # noqa: BLE001 - queue.Empty
                    break
                got.append(ev)
                if ev.get("t") == kind:
                    return ev
            self.fail(f"no {kind} event (got {[e.get('t') for e in got]})")
        return q, take

    def service(self):
        plugin_services.start_all()
        svc = next(s for s in plugin_services.of("chat") if s.id == "bridge")
        self.assertTrue(wait_for(lambda: svc.state == "running", timeout=10))
        self.assertTrue(wait_for(lambda: self.svc_up(svc.port), timeout=10))
        return svc

    @staticmethod
    def svc_up(port):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            c.request("GET", "/ping")
            c.getresponse().read()
            return True
        except OSError:
            return False
        finally:
            c.close()

    def raw(self, method, path, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        c.request(method, path, headers={"Host": f"127.0.0.1:{self.port}", **(headers or {})})
        r = c.getresponse()
        body = r.read()
        c.close()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, body

    def svc_log(self):
        try:
            return [json.loads(x) for x in (self.chat / "svc.log").read_text().splitlines()]
        except OSError:
            return []

    # ------------------------------------------------------------------ the manifest
    def test_the_manifest(self):
        p = self.found["chat"]
        self.assertTrue(p["ok"], p)
        m = plugins.LOADED["chat"]
        self.assertEqual([(t["kind"], t["view"], t["service"]) for t in m["tabs"]],
                         [("thread", "thread", "bridge"), ("dm", "thread", "bridge"), ("page", "doc", "bridge")])
        self.assertIn("[[ui.tab]] old: path 'ui/old.html': iframe pages are gone in plugin API 2.0: use view = \"thread\" or \"doc\"", p["error"])
        self.assertIn('[[ui.tab]] weird: view is "thread" or "doc": ignored', p["error"])
        self.assertIn("[[ui.sidebar]] nosrc: source: one of its [[ui.palette]] ids (inbox): ignored", p["error"])
        self.assertIn("schemes: 'javascript' ignored", p["error"])
        self.assertIn("schemes: 'Bad!' ignored", p["error"])
        self.assertEqual(m["schemes"], ["slack"])
        self.assertEqual(m["service"]["bridge"]["files"], "/files/")
        self.assertEqual(m["messages"], [{"id": "words", "title": "Words", "args": ["words"]}])
        ui = self.req("GET", "/api/plugin-ui")[1]
        self.assertIn({"plugin": "chat", "id": "inbox", "title": "Inbox", "icon": "", "order": 50, "view": "list", "source": "inbox"}, ui["sidebars"])
        self.assertIn({"plugin": "chat", "id": "one", "title": "One thread", "view": "thread", "kind": "thread", "key": "T1"}, ui["panels"])
        self.assertIn({"plugin": "chat", "id": "words", "title": "Words", "args": ["words"]}, ui["messages"])
        self.assertEqual(ui["schemes"], {"chat": ["slack"]})
        o = self.found["other"]
        self.assertTrue(o["ok"], o)  # (no requires_api: loads; a bad `files` is ignored with an error)
        self.assertIn("[service.a] files '/orbit/x/' ignored: /orbit/ is where Orbit asks the service", o["error"])
        self.assertIn("[service.b] files '../up/' ignored", o["error"])
        self.assertIn("the plugin has several services, say which one it asks", o["error"])
        self.assertNotIn("files", plugins.LOADED["other"]["service"]["a"])
        self.assertEqual(self.req("GET", "/api/version")[1]["api"], "2.0")

    def test_a_1x_plugin_still_loads_without_its_pages(self):
        d = plugin_dir(Path(self.tmp.name), "old1", '[plugin]\nname = "old1"\nrequires_api = ">=1.1,<2"\n'
                                                     '[[ui.panel]]\nid = "p"\ntitle = "P"\npath = "ui/p.html"\n')
        p = plugins.load(self.state, [str(d)])[0]
        self.assertTrue(p["ok"], p)
        self.assertIn("[[ui.panel]] p: path 'ui/p.html': iframe pages are gone in plugin API 2.0", p["error"])
        self.assertFalse(plugins.fits(">=1.1,<2"))
        d2 = plugin_dir(Path(self.tmp.name), "new3", '[plugin]\nname = "new3"\nrequires_api = ">=3"\n')
        self.assertIn("needs Orbit's plugin API >=3", plugins.load(self.state, [str(d2)])[0]["error"])

    # ------------------------------------------------------------------ the store
    def test_push_patch_remove(self):
        q, take = self.events()
        try:
            code, r = self.push("PUT", "C1/17.5", {"title": "#infra · deploy", "sub": "Slack", "url": "slack://channel?id=C1",
                                                   "composer": {"placeholder": "Reply in #infra"}, "more": True,
                                                   "messages": [self.m(1), self.m(2, author={"name": "Bo", "avatar": "javascript:x"},
                                                                files=[{"name": "log.png", "url": "svc:/files/a.png", "image": True}],
                                                                reactions=[{"emoji": "👍", "count": 2, "me": True}],
                                                                replies={"count": 3, "kind": "dm", "key": "x"}, junk=1)]})
            self.assertEqual((code, r["count"], r["dropped"]), (200, 2, 0))
            ev = take("plugin-thread")
            self.assertEqual((ev["plugin"], ev["kind"], ev["key"], ev["change"]), ("chat", "thread", "C1/17.5", {"whole": True}))
            d = self.get("C1/17.5")[1]
            self.assertEqual((d["title"], d["sub"], d["url"], d["more"], d["composer"]),
                             ("#infra · deploy", "Slack", "slack://channel?id=C1", True, {"placeholder": "Reply in #infra", "enabled": True}))
            m2 = d["messages"][1]
            self.assertNotIn("junk", m2)
            self.assertEqual(m2["author"], {"name": "Bo"})  # (a bad avatar dropped)
            self.assertEqual(m2["files"], [{"name": "log.png", "url": "svc:/files/a.png", "image": True}])
            rev = d["rev"]
            code, r = self.push("PATCH", "C1/17.5", {"append": [self.m(3)], "update": [self.m(1, "edited", edited=True), self.m(99)],
                                                     "remove": ["2", "nope"], "title": "renamed", "unread": True})
            self.assertEqual((code, r["count"], r["rev"]), (200, 2, rev + 1))
            ev = take("plugin-thread")
            c = ev["change"]
            self.assertEqual((ev["rev"], c["title"], c["unread"], c["remove"], [x["id"] for x in c["update"]], [x["id"] for x in c["append"]]),
                             (rev + 1, "renamed", True, ["2"], ["1"], ["3"]))
            self.assertEqual([(x["id"], x["markdown"]) for x in self.msgs("C1/17.5")], [("1", "edited"), ("3", "message 3")])
            self.push("PATCH", "C1/17.5", {"append": [self.m(3, "again")]})  # (an id appended again: an update in place)
            self.assertEqual([(x["id"], x["markdown"]) for x in self.msgs("C1/17.5")], [("1", "edited"), ("3", "again")])
            r = self.push("PATCH", "C1/17.5", {"remove": ["gone"]})[1]  # (nothing changes: no event, no rev)
            self.assertEqual(r["rev"], rev + 2)
        finally:
            self.desk.unsubscribe(q)
        for bad, what in (({"messages": [{"markdown": "no id"}]}, "id: a string"),
                          ({"messages": [self.m(1, url="javascript:alert(1)")]}, "url: https://"),
                          ({"messages": [self.m(1, files=[{"name": "x", "url": "file:///etc/passwd"}])]}, "files[0].url"),
                          ({"messages": [self.m(1, files=[{"name": "x", "url": "svc:/files/../secret"}])]}, "files[0].url"),
                          ({"messages": [self.m(1, files=[{"name": "x", "url": "svc:files/a"}])]}, "files[0].url"),
                          ({"messages": [self.m(1, replies={"count": 1, "kind": "page", "key": "x"})]}, "replies"),
                          ({"messages": [self.m(1, text="x" * 40000)]}, "markdown: a string, 32768"),
                          ({"url": "file:///x"}, "url: https://"),
                          ({"messages": [self.m("pending:x")]}, "id: a string")):
            code, r = self.push("PUT", "C1/17.5", bad)
            self.assertEqual(code, 400, bad)
            self.assertIn(what, r["error"])
        self.assertEqual(len(self.msgs("C1/17.5")), 2)  # (refused as a whole: nothing changed)

    def test_the_caps(self):
        plugin_views.THREAD_MAX = 5
        code, r = self.push("PUT", "k", {"messages": [self.m(i) for i in range(8)]})
        self.assertEqual((code, r["count"], r["dropped"]), (200, 5, 3))
        self.assertEqual([x["id"] for x in self.msgs("k")], ["3", "4", "5", "6", "7"])  # the newest kept
        self.push("PATCH", "k", {"append": [self.m(8), self.m(9)]})
        self.assertEqual([x["id"] for x in self.msgs("k")], ["5", "6", "7", "8", "9"])
        big = self.req("PUT", self.tpath("k"), {"messages": [], "pad": "x" * (plugin_views.BODY_MAX + 10)},
                       headers={"X-Orbit-Plugin-Token": self.token()})
        self.assertEqual(big[0], 413)
        plugin_views.VIEWS_MAX = 3
        for i in range(3):
            self.push("PUT", f"t{i}", {"messages": [self.m(1)]})
            time.sleep(0.01)
        self.get("t0")  # (opened: the least recently opened goes next)
        self.push("PUT", "t3", {"messages": [self.m(1)]})
        plugin_views.flush()
        idx = json.loads((self.state / "plugin-threads" / "chat" / "index.json").read_text())
        keys = sorted(e["key"] for e in idx if e["what"] == "thread")
        self.assertEqual(len(keys), 3)
        self.assertIn("t0", keys)
        self.assertEqual(len(list((self.state / "plugin-threads" / "chat").glob("t-*.json"))), 3)

    def test_kept_across_a_restart(self):
        self.push("PUT", "keep", {"title": "Kept", "messages": [self.m(1), self.m(2)]})
        self.push("PATCH", "keep", {"append": [self.m(3)]})
        rev = self.get("keep")[1]["rev"]
        plugins.load(self.state, [str(self.chat), str(self.quiet), str(self.other)])  # (written first, read back)
        type(self)._unserve()
        type(self)._serve()
        os.environ["ORBIT_PORT"] = str(type(self).port)  # (its services call the new one)
        d = self.get("keep")[1]
        self.assertEqual((d["title"], [x["id"] for x in d["messages"]], d["rev"]), ("Kept", ["1", "2", "3"], rev))

    # ------------------------------------------------------------------ who may push
    def test_only_the_plugin_pushes_its_declared_kinds(self):
        self.assertEqual(self.push("PUT", "k", {"messages": []}, token="")[0], 403)
        code, r = self.push("PUT", "k", {"messages": []}, token=self.token("quiet"))  # another plugin's token
        self.assertEqual(code, 403)
        self.assertIn("only chat writes its threads", r["error"])
        self.assertEqual(self.push("PATCH", "k", {"append": []}, token="orbitp_made_up")[0], 403)
        code, r = self.push("PUT", "k", {"messages": []}, kind="page")  # (a doc kind, not a thread)
        self.assertEqual(code, 400)
        self.assertIn("declares no thread kind 'page'", r["error"])
        code, r = self.push("PUT", "k", {"messages": []}, kind="nope")
        self.assertEqual(code, 400)
        self.assertEqual(self.push("PUT", "k", {"messages": []}, name="ghost", token="x")[0], 404)
        self.assertEqual(self.get("k", kind="nope")[0], 404)
        h = type(self).srv.RequestHandlerClass
        from orbit.machines import remote
        tok = {"Authorization": "Bearer " + remote.token(self.state)}
        self.push("PUT", "k", {"messages": [self.m(1)]})
        h.trust_loopback = False  # another machine, with the server's token
        try:
            self.assertEqual(self.req("PUT", self.tpath("k"), {"messages": []}, headers={**tok, "X-Orbit-Plugin-Token": self.token()})[0], 403)
            code, r = self.req("POST", self.tpath("k") + "/send", {"markdown": "hi", "files": [], "client_id": "c1"}, headers=tok)
            self.assertEqual(code, 403)  # sending: this machine only
            self.assertEqual(self.req("GET", self.tpath("k"), headers=tok)[1]["messages"][0]["id"], "1")  # reading: fine
        finally:
            h.trust_loopback = True

    # ------------------------------------------------------------------ asked for in the background
    def test_fetched_from_the_service_never_blocking(self):
        self.service()
        (self.chat / "threads.json").write_text(json.dumps({"T1": {"title": "From the service", "messages": [self.m(1), {"id": "x"}]},
                                                            "slow-1": {"title": "Slow", "messages": [self.m(5)]}}))
        code, d = self.get("T1")
        self.assertEqual((code, d["messages"], d["loading"]), (200, [], True))  # nothing yet: at once
        self.assertTrue(wait_for(lambda: self.get("T1")[1]["messages"], timeout=10))
        d = self.get("T1")[1]
        self.assertEqual((d["title"], [x["id"] for x in d["messages"]]), ("From the service", ["1", "x"]))
        asks = [x for x in self.svc_log() if x["path"].startswith("/orbit/thread")]
        self.assertEqual(len(asks), 1)
        self.assertEqual(asks[0]["headers"]["X-Orbit-Plugin-Token"], self.token())
        self.get("T1")
        self.get("T1")
        time.sleep(0.3)
        self.assertEqual(len([x for x in self.svc_log() if x["path"].startswith("/orbit/thread")]), 1)  # fresh: not again
        self.get("T1", q="?fresh=1")  # (Reload)
        self.assertTrue(wait_for(lambda: len([x for x in self.svc_log() if x["path"].startswith("/orbit/thread")]) == 2, timeout=5))
        t0 = time.time()
        code, d = self.get("slow-1")  # the service takes 3 s: the page does not wait
        self.assertLess(time.time() - t0, 1.0)
        self.assertEqual(d["messages"], [])
        self.assertTrue(wait_for(lambda: self.get("slow-1")[1]["messages"], timeout=10))
        self.get("bad")
        self.assertTrue(wait_for(lambda: "answered 500" in (self.get("bad")[1].get("error") or ""), timeout=10))

    def test_fetched_with_the_command_once_per_key(self):
        (self.quiet / "sleep").write_text("1")
        for _ in range(4):
            code, d = self.get("K", name="quiet")
            self.assertEqual(code, 200)
        self.assertTrue(wait_for(lambda: self.get("K", name="quiet")[1]["messages"], timeout=10))
        d = self.get("K", name="quiet")[1]
        self.assertEqual((d["title"], d["messages"][0]["markdown"]), ("From the command K", "hi from cli"))
        runs = [json.loads(x) for x in (self.quiet / "spawns.log").read_text().splitlines()]
        self.assertEqual(runs, [["thread", "thread", "K"]])  # one run, not one per request
        self.assertEqual(self.req("GET", self.tpath("K", name="quiet") + "?before=c1")[0], 502)  # (older: a service's only)

    def test_older_messages_through_the_service(self):
        self.service()
        self.push("PUT", "T2", {"more": True, "messages": [self.m(5), self.m(6)]})
        code, d = self.req("GET", self.tpath("T2") + "?before=5")
        self.assertEqual(code, 200, d)
        self.assertEqual(([x["id"] for x in d["messages"]], d["more"]), (["old1", "old2"], True))  # (the bad one dropped)
        self.assertEqual([x["id"] for x in self.msgs("T2")], ["5", "6"])  # never kept: they stay in the page
        self.assertEqual(self.req("GET", self.tpath("T2") + "?before=old1")[1]["more"], False)
        ask = [x for x in self.svc_log() if "before=" in x["path"]][0]
        self.assertIn("kind=thread&key=T2&before=5", ask["path"])

    # ------------------------------------------------------------------ sending
    def send(self, key, text, cid, files=None, name="chat"):
        return self.req("POST", self.tpath(key, name=name) + "/send", {"markdown": text, "files": files or [], "client_id": cid})

    def test_send_pending_then_replaced(self):
        self.service()
        self.push("PUT", "S", {"messages": [self.m(1)]})
        up = self.req("POST", "/api/upload", {"name": "shot.png", "data": __import__("base64").b64encode(PNG).decode()})[1]
        code, r = self.send("S", "hello **there**", "c1", [{"name": "shot.png", "path": up["path"]}])
        self.assertEqual(code, 200, r)
        self.assertEqual((r["message"]["id"], r["message"]["pending"], r["message"]["files"]), ("pending:c1", "sending", [{"name": "shot.png"}]))
        self.assertNotIn("_send", r["message"])
        self.assertTrue(wait_for(lambda: any(x["path"] == "/orbit/send" for x in self.svc_log()), timeout=10))
        sent = next(x for x in self.svc_log() if x["path"] == "/orbit/send")
        self.assertEqual(sent["body"], {"kind": "thread", "key": "S", "markdown": "hello **there**",
                                        "files": [{"name": "shot.png", "path": up["path"]}], "client_id": "c1"})
        self.assertEqual(sent["token"], self.token())
        self.assertTrue(wait_for(lambda: self.msgs("S")[-1].get("pending") == "sent", timeout=5))  # (taken: m-c1)
        self.push("PUT", "S", {"messages": [self.m(1)]})  # a whole PUT without it: the pending one is kept
        self.assertEqual([x["id"] for x in self.msgs("S")], ["1", "pending:c1"])
        self.push("PATCH", "S", {"append": [{"id": "m-c1", "markdown": "hello **there**", "author": {"name": "Me", "me": True}}]})
        self.assertEqual([x["id"] for x in self.msgs("S")], ["1", "m-c1"])  # (matched by the id it answered)
        self.assertEqual(self.send("S", "", "c9")[0], 400)  # nothing to send
        self.assertEqual(self.send("S", "x", "bad id!")[0], 400)
        self.assertEqual(self.send("S", "x", "c8", [{"name": "x", "path": "/no/such/file"}])[0], 400)

    def test_the_real_one_pushed_before_send_answers(self):
        self.service()
        q, take = self.events()
        try:
            self.send("R", "pushfirst hi", "c2")
            ok = wait_for(lambda: [x["id"] for x in self.msgs("R")] == ["r1"], timeout=10)
            self.assertTrue(ok, (self.msgs("R"), self.svc_log()[-3:]))
            ev = take("plugin-thread")
            while "replace" not in ev["change"]:
                ev = take("plugin-thread")
            self.assertEqual(list(ev["change"]["replace"]), ["pending:c2"])  # in its place
        finally:
            self.desk.unsubscribe(q)
        self.send("R", "idonly hi", "c3")  # pushed without client_id, before the answer named its id
        self.assertTrue(wait_for(lambda: [x["id"] for x in self.msgs("R")] == ["r1", "r2"], timeout=10))
        time.sleep(0.3)
        self.assertEqual([x["id"] for x in self.msgs("R")], ["r1", "r2"])

    def test_a_failure_then_retry_with_the_same_client_id(self):
        self.service()
        self.send("F", "fail once", "c4")
        self.assertTrue(wait_for(lambda: self.msgs("F") and self.msgs("F")[0].get("pending") == "failed", timeout=10))
        m = self.msgs("F")[0]
        self.assertIn("answered 500: the channel is archived", m["error"])
        code, r = self.send("F", "fail once", "c4")  # Retry: the same message again, the same client_id
        self.assertEqual(r["message"]["pending"], "sending")
        self.assertTrue(wait_for(lambda: len([x for x in self.svc_log() if x["path"] == "/orbit/send"]) == 2, timeout=10))
        self.assertEqual({x["body"]["client_id"] for x in self.svc_log() if x["path"] == "/orbit/send"}, {"c4"})
        self.assertTrue(wait_for(lambda: self.msgs("F")[0].get("pending") == "failed", timeout=10))
        self.assertEqual(self.req("POST", self.tpath("F") + "/discard", {"client_id": "c4"})[1], {"ok": True})  # Edit
        self.assertEqual(self.msgs("F"), [])

    def test_send_through_the_command(self):
        self.send("Q", "hi there", "c5", name="quiet")
        self.assertTrue(wait_for(lambda: (self.quiet / "sent.log").exists(), timeout=10))
        self.assertEqual(json.loads((self.quiet / "sent.log").read_text())["client_id"], "c5")
        self.assertTrue(wait_for(lambda: self.msgs("Q", name="quiet")[0].get("pending") == "sent", timeout=10))
        self.send("Q", "fail now", "c6", name="quiet")
        self.assertTrue(wait_for(lambda: self.msgs("Q", name="quiet")[-1].get("pending") == "failed", timeout=10))
        self.assertIn("no way", self.msgs("Q", name="quiet")[-1]["error"])

    def test_a_sending_one_survives_a_restart_as_not_sent(self):
        self.push("PUT", "P", {"messages": []})
        with plugin_views._lock:  # (a send on its way when the server stopped)
            v = plugin_views._view("chat", "thread", "thread", "P")
            v.data["messages"].append({"id": "pending:c7", "client_id": "c7", "author": {"name": "", "me": True}, "markdown": "x",
                                       "pending": "sending", "_send": {"markdown": "x", "files": []}})
            plugin_views._save_soon(v)
        plugin_views.flush()
        plugins.load(self.state, [str(self.chat), str(self.quiet), str(self.other)])
        m = self.msgs("P")[0]
        self.assertEqual((m["id"], m["pending"]), ("pending:c7", "failed"))
        self.assertIn("restarted", m["error"])

    # ------------------------------------------------------------------ docs
    def test_docs(self):
        dp = "/api/plugins/chat/docs/status"
        hdr = {"X-Orbit-Plugin-Token": self.token()}
        self.assertEqual(self.req("PUT", dp, {"title": "Status", "markdown": "# ok", "rev": 3}, headers={"X-Orbit-Plugin-Token": self.token("quiet")})[0], 403)
        q, take = self.events()
        try:
            code, r = self.req("PUT", dp, {"title": "Status", "markdown": "# ok", "rev": 3}, headers=hdr)
            self.assertEqual(code, 200, r)
            self.assertEqual((take("plugin-doc")["id"]), "status")
        finally:
            self.desk.unsubscribe(q)
        d = self.req("GET", dp)[1]
        self.assertEqual((d["title"], d["markdown"], d["version"]), ("Status", "# ok", 3))
        code, r = self.req("PUT", dp, {"title": "Status", "markdown": "# older", "rev": 2}, headers=hdr)
        self.assertEqual(code, 409)
        self.assertEqual(self.req("PATCH", dp, {"markdown": "x"}, headers=hdr)[0], 405)
        self.assertEqual(self.req("PUT", "/api/plugins/other/docs/x", {"markdown": "x"}, headers={"X-Orbit-Plugin-Token": self.token("other")})[0], 400)
        self.service()
        self.req("GET", "/api/plugins/chat/docs/new-one")
        self.assertTrue(wait_for(lambda: self.req("GET", "/api/plugins/chat/docs/new-one")[1].get("title") == "Fetched new-one", timeout=10))
        self.req("GET", "/api/plugins/quiet/docs/notes")
        self.assertTrue(wait_for(lambda: self.req("GET", "/api/plugins/quiet/docs/notes")[1].get("markdown") == "*cli*", timeout=10))

    # ------------------------------------------------------------------ the svc: proxy
    def test_the_proxy_serves_inert_content_only(self):
        self.service()
        base = "/api/plugins/chat/svc"
        code, h, body = self.raw("GET", base + "/files/a.png")
        self.assertEqual((code, h["content-type"], body), (200, "image/png", PNG))  # by its bytes, not its text/html
        self.assertEqual((h["x-content-type-options"], h["content-security-policy"]), ("nosniff", "sandbox"))
        self.assertEqual((h["etag"], h["cache-control"]), ('"e1"', "max-age=60"))  # passed: these only
        self.assertNotIn("set-cookie", h)
        self.assertNotIn("x-evil", h)
        got = [x for x in self.svc_log() if x["path"] == "/files/a.png"][-1]["headers"]
        self.assertEqual(got["X-Orbit-Proxy"], "1")
        self.assertEqual(got["X-Orbit-Plugin-Token"], self.token())
        for path in ("/files/page.html", "/files/page.xhtml", "/files/fake.png", "/files/pic.svg"):
            code, h, body = self.raw("GET", base + path)
            self.assertEqual(code, 200, path)
            self.assertEqual(h["content-type"], "application/octet-stream", path)  # never active content
            self.assertTrue(h["content-disposition"].startswith("attachment"), path)
            self.assertEqual((h["x-content-type-options"], h["content-security-policy"]), ("nosniff", "sandbox"))
        code, h, _ = self.raw("GET", base + "/files/pic.svg", {"Sec-Fetch-Dest": "image"})  # an <img>: SVG, sandboxed
        self.assertEqual(h["content-type"], "image/svg+xml")
        self.assertIn("sandbox", h["content-security-policy"])
        self.assertIn("default-src 'none'", h["content-security-policy"])
        code, h, _ = self.raw("GET", base + "/files/page.html", {"Sec-Fetch-Dest": "image"})
        self.assertEqual(h["content-type"], "application/octet-stream")
        self.assertEqual(self.raw("GET", base + "/files/close.png")[2], PNG)
        code, h, body = self.raw("HEAD", base + "/files/a.png")
        self.assertEqual((code, body), (200, b""))

    def test_the_proxy_is_not_a_gateway(self):
        self.service()
        base = "/api/plugins/chat/svc"
        n = len(self.svc_log())
        for path, want in (("/files/../secret", 400), ("/files/%2e%2e/secret", 400), ("/files/%2E%2E%2Fsecret", 400),
                           ("/files/a%2fb", 400), ("/files/a%5cb", 400), ("/files/a\\b", 400), ("/files/a%0d%0aX-Evil:%201", 400),
                           ("/files/a%0aX", 400), ("/files/a%00", 400), ("/files/a%252e", 400), ("//files/a.png", 400),
                           ("/files//a.png", 400), ("/orbit/thread?kind=thread&key=x", 403), ("/orbit/send", 403),
                           ("/ORBIT/thread", 403), ("/secret", 404), ("/files", 404), ("/file/a.png", 404)):
            code, h, body = self.raw("GET", base + path)
            self.assertEqual(code, want, path)
            self.assertNotIn(b"SECRET", body)
        self.assertEqual(len(self.svc_log()), n)  # none of those reached the service
        for m in ("POST", "PUT", "PATCH", "DELETE"):
            self.assertIn(self.raw(m, base + "/files/a.png")[0], (404, 405, 501), m)
        code, h, body = self.raw("GET", base + "/files/redirect")
        self.assertEqual(code, 502)
        self.assertIn(b"not followed", body)
        code, h, body = self.raw("GET", base + "/files/range", {"Range": "bytes=0-3", "Cookie": "orbit=1", "Authorization": "Bearer x",
                                                                 "X-Orbit-Plugin-Token": "forged"})
        self.assertEqual((code, body, h["content-range"]), (206, b"abcd", "bytes 0-3/10"))
        got = [x for x in self.svc_log() if x["path"] == "/files/range"][-1]["headers"]
        self.assertEqual(got.get("Range"), "bytes=0-3")
        for k in ("Cookie", "Authorization"):
            self.assertNotIn(k, got)
        self.assertEqual(got["X-Orbit-Plugin-Token"], self.token())  # (its own, never the caller's)
        self.assertEqual(self.raw("GET", "/api/plugins/other/svc/files/a.png")[0], 404)  # (it declares no files)
        self.assertEqual(self.raw("GET", "/api/plugins/quiet/svc/files/a.png")[0], 404)  # (no service)

    def test_the_proxy_limits(self):
        self.service()
        base = "/api/plugins/chat/svc"
        plugin_views.PROXY_MAX = 2000
        code, h, body = self.raw("GET", base + "/files/big")  # Content-Length past the cap
        self.assertEqual(code, 502)
        self.assertIn(b"larger than", body)
        code, h, body = self.raw("GET", base + "/files/chunked")  # no length: counted as it comes
        self.assertEqual(code, 502)
        plugin_views.PROXY_MAX = self.saved["PROXY_MAX"]
        code, h, body = self.raw("GET", base + "/files/chunked")
        self.assertEqual((code, len(body), h["content-length"]), (200, 3000, "3000"))
        plugin_views.PROXY_TIMEOUT = 1.5
        t0 = time.time()
        try:
            code, h, body = self.raw("GET", base + "/files/slow")  # 4 s for its bytes: cut at 1.5 s
        except (http.client.IncompleteRead, http.client.RemoteDisconnected, ConnectionError):
            code, body = 0, b""
        self.assertLess(time.time() - t0, 3.5)
        self.assertLess(len(body), 100)
        plugin_views.PROXY_TIMEOUT = self.saved["PROXY_TIMEOUT"]
        out = []
        ts = [threading.Thread(target=lambda: out.append(self.raw("GET", base + "/files/hold")[0])) for _ in range(7)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(20)
        self.assertEqual(out, [200] * 7)  # (the others waited their turn)
        self.assertLessEqual(int((self.chat / "busy.max").read_text()), plugin_views.PROXY_AT_ONCE)

    def test_the_proxy_works_for_another_machine_with_the_token(self):
        self.service()
        from orbit.machines import remote
        h = type(self).srv.RequestHandlerClass
        h.trust_loopback = False
        try:
            self.assertEqual(self.raw("GET", "/api/plugins/chat/svc/files/a.png")[0], 401)
            code, hd, body = self.raw("GET", "/api/plugins/chat/svc/files/a.png", {"Authorization": "Bearer " + remote.token(self.state)})
            self.assertEqual((code, body), (200, PNG))
        finally:
            h.trust_loopback = True

    def test_raw_files_are_nosniff(self):
        f = Path(self.work) / "x.txt"
        f.write_text("hi")
        code, h, _ = self.raw("GET", "/api/raw?path=" + urllib.parse.quote(str(f)))
        self.assertEqual((code, h["x-content-type-options"]), (200, "nosniff"))

    # ------------------------------------------------------------------ the page's Markdown, in plugin mode
    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_plugin_mode_markdown(self):
        js = r'''
const vm = require("vm"), fs = require("fs");
global.window = global; global.S = { sessions: new Map() }; global.exports = undefined; global.module = undefined;
global.MD_MACHINE = ""; global.MD_CWD = "/w"; global.machinePath = p => p;
global.esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
for (const f of ["vendor/markdown-it.min.js", "src/markdown-base.js", "src/markdown.js"])
  vm.runInThisContext(fs.readFileSync(process.argv[1] + "/web/" + f, "utf8"), { filename: f });
const r = s => renderPluginMd(s, "chat", ["slack"]);
const out = {
  local: r("![x](/etc/passwd) ![y](~/a.png) ![z](file:///etc/passwd) ![w](plot.png) [f](/Users/me/x.txt) `src/app.py`"),
  imgs: r("![a](https://e.com/a.png) ![b](svc:/files/F1) ![c](http://e.com/c.png) ![d](data:image/png;base64,AA)"),
  links: r("[a](https://e.com) [b](mailto:x@e.com) [c](slack://channel?id=1) [d](javascript:alert(1)) [e](zoommtg://x) [f](http:x) <script>x</script> https://bare.example"),
  answer: md.render("`src/app.py` ![x](/tmp/a.png)"),
};
console.log(JSON.stringify(out));
'''
        r = subprocess.run(["node", "-e", js, str(ROOT)], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        o = json.loads(r.stdout)
        for bad in ("<img", "<a ", "/api/raw", "data-fref", 'href="', 'src="'):  # (what is left is text)
            self.assertNotIn(bad, o["local"], bad)
        self.assertIn('src="https://e.com/a.png"', o["imgs"])
        self.assertIn('src="/api/plugins/chat/svc/files/F1"', o["imgs"])
        self.assertEqual(o["imgs"].count("<img"), 2)  # (not http:, not data:)
        for ok in ('href="https://e.com"', 'href="mailto:x@e.com"', 'href="slack://channel?id=1"', 'href="https://bare.example"'):
            self.assertIn(ok, o["links"])
        for bad in ('href="javascript:', 'href="zoommtg:', 'href="http:x"', "<script>"):
            self.assertNotIn(bad, o["links"])
        self.assertIn("data-fref", o["answer"])  # (an answer keeps its file links and local images)
        self.assertIn("/api/raw?path=", o["answer"])


for name in [n for n in dir(test_skeleton.SkeletonTest) if n.startswith("test_")]:
    if name not in ViewsTest.__dict__:
        setattr(ViewsTest, name, None)

if __name__ == "__main__":
    unittest.main()
