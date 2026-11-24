"""Remote servers: the token rules of a served server, and reaching one (`orbit remote add`, NAME/session)."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import test_skeleton
from orbit.machines import remote

ROOT = Path(__file__).resolve().parent.parent


class RemoteTest(test_skeleton.SkeletonTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from orbit.desk import Desk
        from orbit.server import make_server
        cls.rtmp = tempfile.TemporaryDirectory()
        cls.rdesk = Desk(Path(cls.rtmp.name) / "state")
        cls.rsrv = make_server(0, cls.rdesk, schedules=Path(cls.rtmp.name) / "schedules.json")
        cls.rsrv.RequestHandlerClass.trust_loopback = False  # every client counts as another machine
        cls.rport = cls.rsrv.server_address[1]
        cls.rsrv.RequestHandlerClass.port = cls.rport
        threading.Thread(target=cls.rsrv.serve_forever, daemon=True).start()
        cls.token = remote.token(cls.rdesk.state_dir)
        (Path(cls.rtmp.name) / "work").mkdir()
        cls.rwork = str((Path(cls.rtmp.name) / "work").resolve())

    @classmethod
    def tearDownClass(cls):
        cls.rsrv.shutdown()
        cls.rsrv.server_close()
        cls.rdesk.shutdown()
        cls.rtmp.cleanup()
        super().tearDownClass()

    def raw(self, path, method="GET", headers=None, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.rport}{path}", method=method, headers=headers or {},
                                     data=json.dumps(body).encode() if body is not None else None)

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=10) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def agent(self, *args, session=""):
        env = {**os.environ, "ORBIT_PORT": str(self.port), "ORBIT_SESSION": session}
        return subprocess.run([sys.executable, "-m", "orbit.cli.agent", *args], env=env, capture_output=True, text=True, timeout=60)

    def test_sharing_switch(self):
        """Settings → Machines → This server: on listens on the machine's network address (token required), off
        closes it; the choice is remembered; another machine cannot switch it."""
        import socket
        from orbit.server import Sharing
        if not Sharing.default_addresses():
            self.skipTest("no network address")
        code, st = self.req("POST", "/api/sharing", {"on": True})
        self.assertEqual(code, 200, st)
        try:
            self.assertTrue(st["on"])
            addr = st["addresses"][0]
            self.assertTrue(st["urls"][0].startswith(f"http://{addr}:{self.port}/?token="))
            self.assertTrue(self.desk.settings["serve_remote"])
            req = urllib.request.Request(f"http://{addr}:{self.port}/api/sessions")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(cm.exception.code, 401)  # reached over the network: the token is needed
            tok = st["urls"][0].split("token=")[1]
            req = urllib.request.Request(f"http://{addr}:{self.port}/api/sessions", headers={"Authorization": f"Bearer {tok}"})
            with urllib.request.urlopen(req, timeout=5) as r:
                self.assertEqual(r.status, 200)
            # another machine cannot read the URLs or switch it
            self.assertEqual(self.raw("/api/sharing", headers={"Authorization": f"Bearer {self.token}"})[0], 403)
        finally:
            code, st = self.req("POST", "/api/sharing", {"on": False})
        self.assertFalse(st["on"])
        self.assertFalse(self.desk.settings["serve_remote"])
        time.sleep(0.5)
        with self.assertRaises(OSError):
            socket.create_connection((addr, self.port), 1).close()

    def test_a_remote_named_by_its_url(self):
        self.assertEqual(remote.name_from_url("http://build-box:8770/?token=x"), "build-box")
        self.assertEqual(remote.name_from_url("http://gpu-box.tail0000.ts.net:8770/?token=x"), "gpu-box")
        self.assertEqual(remote.name_from_url("http://Big_Box:8770/?token=x"), "big_box")
        self.assertEqual(remote.name_from_url("http://100.67.1.2:8770/?token=x"), "")  # an address names nothing
        with tempfile.TemporaryDirectory() as d:
            r = remote.add(Path(d), "", "http://build-box:8770/?token=abc", check=False)
            self.assertEqual(r, {"name": "build-box", "url": "http://build-box:8770"})
            with self.assertRaises(ValueError):
                remote.add(Path(d), "", "http://100.67.1.2:8770/?token=abc", check=False)

    def test_a_proxy_on_this_machine_counts_as_another_machine(self):
        # Tailscale in userspace mode (or an ssh tunnel) hands a request over from 127.0.0.1, with the name it was sent to
        tok = remote.token(self.state)
        def get(host, auth=None):
            h = {"Host": host, **({"Authorization": f"Bearer {auth}"} if auth else {})}
            req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/version", headers=h)
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.status
            except urllib.error.HTTPError as e:
                return e.code
        self.assertEqual(get(f"100.64.1.2:{self.port}"), 401)  # another machine's name: the token is needed
        self.assertEqual(get(f"100.64.1.2:{self.port}", "nope"), 401)
        self.assertEqual(get(f"100.64.1.2:{self.port}", tok), 200)
        self.assertEqual(get(f"127.0.0.1:{self.port}"), 200)  # this machine itself: nothing new

    def test_token_rules(self):
        self.assertEqual(self.raw("/api/sessions")[0], 401)  # another machine, no token
        self.assertEqual(self.raw("/api/sessions", headers={"Authorization": "Bearer nope"})[0], 401)
        self.assertEqual(self.raw("/", headers={})[0], 401)  # not even the page
        code, _, body = self.raw("/api/sessions", headers={"Authorization": f"Bearer {self.token}"})
        self.assertEqual(code, 200)
        # a browser opening the URL: a cookie, the token out of the address bar
        code, h, _ = self.raw(f"/?token={self.token}")
        self.assertEqual(code, 302)
        self.assertIn("HttpOnly", h["Set-Cookie"])
        self.assertIn("SameSite=Strict", h["Set-Cookie"])
        cookie = h["Set-Cookie"].split(";")[0]
        code, h, _ = self.raw(f"/?token={self.token}&session=abc")
        self.assertEqual(h["Location"], "/?session=abc")  # the rest of the URL stays, the token goes
        # opening a browser is for the server's own machine, not for whoever holds the token
        code, _, _ = self.raw("/api/remotes/x/open", "POST", {"Cookie": cookie, "Content-Type": "application/json"}, {})
        self.assertEqual(code, 403)
        self.assertEqual(self.raw("/api/sessions", headers={"Cookie": cookie})[0], 200)
        # a write from a page of another origin is refused even with the token
        code, _, _ = self.raw("/api/groups", "POST", {"Cookie": cookie, "Origin": "http://evil.example",
                                                        "Content-Type": "application/json"}, {"name": "x"})
        self.assertEqual(code, 403)
        mode = (self.rdesk.state_dir / "token").stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_federation_other_machine_sessions_are_ours(self):
        """The local server lists the remote's sessions as box~ID in a group "box", relays their events, and does
        what its clients ask of them over there."""
        from orbit.machines.federation import wait_for
        url = f"http://127.0.0.1:{self.rport}/?token={self.token}"
        self.assertEqual(self.req("POST", "/api/remotes", {"name": "box", "url": url})[0], 200)
        up = lambda n: any(m["name"] == n and m["ok"] for m in self.req("GET", "/api/sessions")[1]["machines"])
        self.assertTrue(wait_for(lambda: up("box")))
        self.assertFalse(any(g["id"].startswith("r~") for g in self.req("GET", "/api/sessions")[1]["groups"]))  # no machine groups
        events = self.desk.subscribe()
        try:
            # a new session in box's group is made on box
            code, s = self.req("POST", "/api/sessions", {"cwd": self.rwork, "name": "builder", "machine": "box", "group": "main"})
            self.assertEqual(code, 200, s)
            self.assertTrue(s["id"].startswith("box~"))
            self.assertEqual((s["remote"], s["group"]), ("box", "main"))  # made on box, in a group of this server
            rid = s["id"].split("~", 1)[1]
            self.assertIn(rid, [x["id"] for x in self.rdesk.list()])  # it lives over there
            self.assertNotIn(rid, [x["id"] for x in self.desk.list()])
            # sending, and the answer coming back as our card events
            code, c = self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "build it"})
            self.assertEqual(code, 200, c)
            self.assertEqual(c["sid"], s["id"])
            seen = []

            def answered():
                while True:
                    try:
                        ev = json.loads(events.get(timeout=0.2))
                    except Exception:
                        return False
                    seen.append(ev)
                    if ev.get("t") == "card" and ev["card"]["sid"] == s["id"] and ev["card"]["state"] == "completed":
                        return True
            self.assertTrue(wait_for(answered, 15), [e.get("t") for e in seen])
            code, cards = self.req("GET", f"/api/sessions/{s['id']}/cards")
            self.assertEqual(code, 200)
            self.assertEqual(cards["cards"][-1]["answer"], "answer: build it")
            self.assertTrue(all(x["sid"] == s["id"] for x in cards["cards"]))
            code, one = self.req("GET", f"/api/sessions/{s['id']}/cards/{cards['cards'][-1]['id']}")  # one card (the phone)
            self.assertEqual((code, one["card"]["answer"], one["card"]["sid"]), (200, "answer: build it", s["id"]))
            listed = self.req("GET", "/api/sessions")[1]["sessions"]
            self.assertIn(s["id"], [x["id"] for x in listed])
            # another server asking this one gets only this machine's (no loops)
            self.assertNotIn(s["id"], [x["id"] for x in self.req("GET", "/api/sessions?local=1")[1]["sessions"]])
            # a session stays in its machine's group; renaming works there
            # tab order across machines: a local tab moves past box's, and box's moves too (this server's order)
            local = self.req("POST", "/api/sessions", {"cwd": str(self.work), "name": "here", "group": "main"})[1]
            order = lambda: [x["id"] for x in self.req("GET", "/api/sessions")[1]["sessions"] if x["id"] in (local["id"], s["id"])]
            self.assertEqual(self.req("POST", f"/api/sessions/{local['id']}/move", {"before": None})[0], 200)
            self.assertEqual(order(), [s["id"], local["id"]])
            self.assertEqual(self.req("POST", f"/api/sessions/{local['id']}/move", {"before": s["id"]})[0], 200)  # before a remote one
            self.assertEqual(order(), [local["id"], s["id"]])
            self.assertEqual(self.req("POST", f"/api/sessions/{s['id']}/move", {"before": local["id"]})[0], 200)  # the remote one moves
            self.assertEqual(order(), [s["id"], local["id"]])
            self.assertIn(s["id"], json.loads((self.state / "sessions.json").read_text())["order"])  # kept
            self.req("POST", f"/api/sessions/{local['id']}/close", {})
            # any of this server's groups (kept here, not on box), and starred
            code, moved = self.req("POST", f"/api/sessions/{s['id']}/update", {"group": "projects", "favorite": True})
            self.assertEqual((code, moved["group"], moved["favorite"]), (200, "projects", True))
            listed = {x["id"]: x for x in self.req("GET", "/api/sessions")[1]["sessions"]}
            self.assertEqual(listed[s["id"]]["group"], "projects")
            self.assertEqual(json.loads((self.state / "remote-placement.json").read_text())[s["id"]]["group"], "projects")
            self.assertEqual(self.req("POST", f"/api/sessions/{s['id']}/update", {"group": "nope"})[0], 400)
            # its group deleted: it falls back to the first group, like this machine's sessions
            g = self.req("POST", "/api/groups", {"name": "Temp"})[1]
            self.req("POST", f"/api/sessions/{s['id']}/update", {"group": g["id"]})
            self.req("POST", f"/api/groups/{g['id']}/delete", {})
            listed = {x["id"]: x for x in self.req("GET", "/api/sessions")[1]["sessions"]}
            self.assertEqual(listed[s["id"]]["group"], self.req("GET", "/api/sessions")[1]["groups"][0]["id"])
            code, r = self.req("POST", f"/api/sessions/{s['id']}/rename", {"name": "builder2"})
            self.assertEqual((code, r["name"], r["id"]), (200, "builder2", s["id"]))
            # orbit-agent sees it too, by NAME/session or by its id here
            self.assertIn("box/builder2", self.agent("sessions").stdout)
            self.assertIn("build it", self.agent("read", s["id"], "--last", "1").stdout)
            # closing it over there removes it here
            self.assertEqual(self.req("POST", f"/api/sessions/{s['id']}/close", {})[0], 200)
            self.assertTrue(wait_for(lambda: s["id"] not in [x["id"] for x in self.req("GET", "/api/sessions")[1]["sessions"]]))
        finally:
            self.desk.unsubscribe(events)
            self.req("POST", "/api/remotes/box/delete", {})
        self.assertTrue(wait_for(lambda: not any(m["name"] == "box" for m in self.req("GET", "/api/sessions")[1]["machines"])))

    def test_federation_follows_card_patches(self):
        """This server follows another's events with patches (?delta=1): a streaming card there comes here step by step
        (patches applied to the card as it last came), and ends as the card over there."""
        from orbit.machines.federation import wait_for
        url = f"http://127.0.0.1:{self.rport}/?token={self.token}"
        self.assertEqual(self.req("POST", "/api/remotes", {"name": "pbox", "url": url})[0], 200)
        try:
            self.assertTrue(wait_for(lambda: any(m["name"] == "pbox" and m["ok"] for m in self.req("GET", "/api/sessions")[1]["machines"])))
            code, s = self.req("POST", "/api/sessions", {"cwd": self.rwork, "name": "streamer", "machine": "pbox", "group": "main"})
            self.assertEqual(code, 200, s)
            events = self.desk.subscribe()
            os.environ["FAKE_FLOOD"] = "8"
            try:
                self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "flood it"})
                got = []

                def done():
                    while True:
                        try:
                            ev = json.loads(events.get(timeout=0.2))
                        except Exception:
                            return False
                        if ev.get("t") == "card" and ev["card"]["sid"] == s["id"]:
                            got.append(ev["card"])
                            if ev["card"]["state"] == "completed":
                                return True
                self.assertTrue(wait_for(done, 30), [c["state"] for c in got])
            finally:
                os.environ.pop("FAKE_FLOOD", None)
                self.desk.unsubscribe(events)
            at_work = [c for c in got if c["state"] in ("thinking", "running")]
            self.assertGreater(len(at_work), 8)  # (the steps between: they come as patches over the link)
            rid = s["id"].split("~", 1)[1]
            there = self.rdesk.card(rid, got[-1]["id"])
            self.assertEqual(got[-1], {**there, "sid": s["id"]})
        finally:
            self.req("POST", "/api/remotes/pbox/delete", {})

    def test_federation_side_panel_of_another_machines_session(self):
        """Another machine's session's side panel, here: its tabs as written here (/@name paths, its pages through this
        server, sandboxed), changed from here over there, and its changes over there coming here as side events."""
        from orbit.machines.federation import wait_for
        url = f"http://127.0.0.1:{self.rport}/?token={self.token}"
        self.assertEqual(self.req("POST", "/api/remotes", {"name": "sbox", "url": url})[0], 200)
        try:
            self.assertTrue(wait_for(lambda: any(m["name"] == "sbox" and m["ok"] for m in self.req("GET", "/api/sessions")[1]["machines"])))
            code, s = self.req("POST", "/api/sessions", {"cwd": self.rwork, "name": "sider", "machine": "sbox", "group": "main"})
            self.assertEqual(code, 200, s)
            rid = s["id"].split("~", 1)[1]
            events = self.desk.subscribe()
            try:
                ev = self.rdesk.show("<h1>far</h1>", "Far page", rid)  # (an agent over there shows a page)
                self.rdesk.open_for_user(self.rwork, side=True, by=rid)

                seen = []

                def told():
                    while True:
                        try:
                            x = json.loads(events.get(timeout=0.2))
                        except Exception:
                            return False
                        if x.get("t") == "side" and x["sid"] == s["id"] and len(x["side"]["items"]) == 2:
                            seen.append(x)
                            return True
                self.assertTrue(wait_for(told, 10))
                e = seen[-1]
            finally:
                self.desk.unsubscribe(events)
            st = self.req("GET", f"/api/sessions/{s['id']}/side")[1]
            self.assertEqual(st, e["side"])
            page, folder = st["items"]
            self.assertEqual(page["url"], f"/api/remote-show/sbox/{ev['id']}")
            self.assertEqual(folder["path"], f"/@sbox{os.path.realpath(self.rwork)}")
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{page['url']}", timeout=10) as r:
                self.assertEqual((r.status, r.read()), (200, b"<h1>far</h1>"))
                self.assertIn("sandbox", r.headers["Content-Security-Policy"])
            # changed from here, over there
            code, st2 = self.req("POST", f"/api/sessions/{s['id']}/side", {"op": "select", "id": page["id"]})
            self.assertEqual((code, st2["i"]), (200, 0))
            self.assertEqual(self.rdesk.side_state(rid)["i"], 0)
            code, st3 = self.req("POST", f"/api/sessions/{s['id']}/side", {"op": "open", "spec": {"kind": "review", "root": f"/@sbox{self.rwork}", "sid": s["id"]}})
            self.assertEqual(code, 200, st3)
            there = self.rdesk.side_state(rid)["items"][1]
            self.assertEqual((there["root"], there["sid"]), (self.rwork, rid))  # (as that machine writes them)
            self.assertEqual((st3["items"][1]["root"], st3["items"][1]["sid"]), (f"/@sbox{self.rwork}", s["id"]))
        finally:
            self.req("POST", "/api/remotes/sbox/delete", {})

    def test_federation_other_machines_conversations_to_resume(self):
        """⌘O → Other sessions lists every machine's conversations; one of box's is resumed on box."""
        import uuid
        from orbit.machines.federation import wait_for
        url = f"http://127.0.0.1:{self.rport}/?token={self.token}"
        self.assertEqual(self.req("POST", "/api/remotes", {"name": "box2", "url": url})[0], 200)
        try:
            self.assertTrue(wait_for(lambda: any(m["name"] == "box2" and m["ok"]
                                                 for m in self.req("GET", "/api/sessions")[1]["machines"])))
            conv = str(uuid.uuid4())  # both servers here read the same transcripts: each lists it as its own
            d = Path(os.environ["ORBIT_CLAUDE_PROJECTS"]) / "-elsewhere"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{conv}.jsonl").write_text(json.dumps({"type": "user", "cwd": self.rwork,
                                                         "message": {"role": "user", "content": "tidy the logs"}}) + "\n")
            found = [x for x in self.req("GET", "/api/external-sessions?limit=200")[1]["sessions"] if x["id"] == conv]
            self.assertEqual(sorted((x.get("machine", ""), x["cwd"]) for x in found),
                             [("", self.rwork), ("box2", "/@box2" + self.rwork)])
            self.assertEqual([x["id"] for x in self.req("GET", "/api/external-sessions?limit=200&local=1")[1]["sessions"]
                              if x["id"] == conv], [conv])  # another server asking: this machine's only
            there = next(x for x in found if x.get("machine"))
            code, s = self.req("POST", "/api/attach-session", {"harness": "claude", "id": conv, "cwd": there["cwd"],
                                                               "machine": "box2", "group": "main"})
            self.assertEqual(code, 200, s)
            self.assertEqual((s["id"], s["remote"], s["group"]), (f"box2~{conv}", "box2", "main"))
            self.assertIn(conv, self.rdesk.sessions)  # resumed over there, in its own folder
            self.assertEqual(self.rdesk.sessions[conv].cwd, self.rwork)
            left = [x.get("machine", "") for x in self.req("GET", "/api/external-sessions?limit=200")[1]["sessions"]
                    if x["id"] == conv]
            self.assertEqual(left, [""])  # box2's is a session now: only this machine's copy is offered
            self.req("POST", f"/api/sessions/box2~{conv}/close", {})
        finally:
            self.req("POST", "/api/remotes/box2/delete", {})

    def test_federation_files_and_shells(self):
        """Another machine's files are /@NAME/<path> here: tree, read, save, raw, stats, resolve go to that server;
        its ⌘J shell is shell NAME~ID; requests carrying /@NAME/ paths reach its agent as its own paths."""
        from orbit.machines.federation import wait_for
        url = f"http://127.0.0.1:{self.rport}/?token={self.token}"
        self.assertEqual(self.req("POST", "/api/remotes", {"name": "far", "url": url})[0], 200)
        try:
            self.assertTrue(wait_for(lambda: any(m["name"] == "far" and m["ok"] for m in self.req("GET", "/api/sessions")[1]["machines"])))
            code, s = self.req("POST", "/api/sessions", {"cwd": self.rwork, "name": "files", "machine": "far"})
            self.assertEqual(code, 200, s)
            root = "/@far" + self.rwork
            self.assertEqual(s["cwd"], root)
            # save and read a file over there
            code, r = self.req("POST", "/api/file", {"path": root + "/notes.md", "text": "# hi\n"})
            self.assertEqual(code, 200, r)
            self.assertEqual(r["path"], root + "/notes.md")
            self.assertEqual((Path(self.rwork) / "notes.md").read_text(), "# hi\n")
            code, r = self.req("GET", "/api/file?path=" + urllib.parse.quote(root + "/notes.md"))
            self.assertEqual((code, r["text"], r["path"]), (200, "# hi\n", root + "/notes.md"))
            # its tree
            code, t = self.req("GET", "/api/tree?root=" + urllib.parse.quote(root))
            self.assertEqual(code, 200, t)
            self.assertEqual(t["root"], root)
            self.assertIn("notes.md", t["files"])
            # stats keyed by our paths; raw bytes come through
            code, st = self.req("POST", "/api/stats", {"paths": [root + "/notes.md"]})
            self.assertIn(root + "/notes.md", st)
            (Path(self.rwork) / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\nxyz")
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/raw?path=" + urllib.parse.quote(root + "/pic.png"), timeout=10) as resp:
                self.assertEqual(resp.read(), b"\x89PNG\r\n\x1a\nxyz")
            # a file named in an answer, resolved in the session's folder over there
            code, r = self.req("GET", "/api/resolve?ref=notes.md&cwd=" + urllib.parse.quote(root))
            self.assertEqual(r["path"], root + "/notes.md")
            # a request naming /@far/ paths reaches the agent with its own paths
            code, c = self.req("POST", f"/api/sessions/{s['id']}/send", {"text": f"read {root}/notes.md"})
            self.assertEqual(code, 200, c)
            self.assertEqual(c["text"], f"read {self.rwork}/notes.md")
            # ⌘J: the session's shell over there
            code, sh = self.req("POST", "/api/shell", {"key": s["id"]})
            self.assertEqual(code, 200, sh)
            self.assertTrue(sh["id"].startswith("far~"))
            self.assertEqual(sh["cwd"], root)
            self.assertEqual(self.req("POST", f"/api/shell/{sh['id']}/input", {"data": "echo orbit-4''2\n"})[0], 200)
            seen = b""
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/shell/{sh['id']}/stream", timeout=10) as st:
                end = time.time() + 8
                import base64
                while time.time() < end and b"orbit-42" not in seen:
                    line = st.readline()
                    if line.startswith(b"data: "):
                        ev = json.loads(line[6:])
                        if ev.get("d"):
                            seen += base64.b64decode(ev["d"])
            self.assertIn(b"orbit-42", seen)
            self.assertEqual(self.req("POST", f"/api/shell/{sh['id']}/kill", {})[0], 200)
            self.req("POST", f"/api/sessions/{s['id']}/close", {})
        finally:
            self.req("POST", "/api/remotes/far/delete", {})

    def test_reach_a_remote_and_its_sessions(self):
        url = f"http://127.0.0.1:{self.rport}/?token={self.token}"
        code, r = self.req("POST", "/api/remotes", {"name": "gpu-box", "url": url.replace(self.token, "wrong")})
        self.assertNotEqual(code, 200)  # the token is checked when added
        code, r = self.req("POST", "/api/remotes", {"name": "gpu-box", "url": url})
        self.assertEqual(code, 200)
        self.assertEqual(r["name"], "gpu-box")
        self.assertEqual((self.state / "remotes.json").stat().st_mode & 0o777, 0o600)  # it holds tokens
        # start a session over there, ask it, steer it, read it: all from this machine
        out = self.agent("new", self.rwork, "--on", "gpu-box", "--name", "trainer", "hello")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("started gpu-box/trainer", out.stdout)
        w = self.agent("wait", "gpu-box/trainer")
        self.assertIn("answer: hello", w.stdout)
        s = self.agent("send", "gpu-box/trainer", "status?", "--wait")
        self.assertEqual(s.returncode, 0, s.stderr)
        self.assertIn("status?", s.stdout)
        ls = self.agent("sessions").stdout
        self.assertIn("gpu-box/trainer", ls)
        self.assertIn("gpu-box/trainer", self.agent("read", "gpu-box/trainer").stdout + "gpu-box/trainer")
        self.assertIn("status?", self.agent("read", "gpu-box/trainer", "--last", "1").stdout)
        # the page's events come through too (the first event of the stream)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/remote/gpu-box/api/events")
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertIn("event-stream", r.headers.get("Content-Type", ""))
            first = r.readline()
        self.assertTrue(first.startswith((b"data:", b":", b"event:", b"retry:")))
        self.assertIn("no remote called nowhere", self.agent("send", "nowhere/x", "hi").stderr)
        code, _ = self.req("POST", "/api/remotes/gpu-box/delete", {})
        self.assertEqual(code, 200)
        self.assertEqual(self.req("POST", "/api/remotes/gpu-box/delete", {})[0], 404)
        self.assertEqual(self.req("POST", "/api/remotes/gpu-box/open", {})[0], 404)


for name in [n for n in dir(test_skeleton.SkeletonTest) if n.startswith("test_")]:
    if name not in RemoteTest.__dict__:
        setattr(RemoteTest, name, None)


class RemoteVoiceTest(RemoteTest):
    def test_voice_call_with_another_machines_session(self):
        """A voice call held by this server with a session on another machine: its requests go there (origin Voice,
        idempotent), the context comes from there, the answer comes back on this server's events and is spoken, the
        session's state (a request typed over there too) is told silently, and the call's transcript becomes a note
        card over there."""
        import fake_live
        from orbit.machines.federation import wait_for
        from orbit.services import keys
        from test_live import delegation, t_in
        fake = fake_live.FakeLive().start()
        base, keys.OPENAI_BASE = keys.OPENAI_BASE, fake.base
        keys.set_key(self.state, "openai", fake_live.KEY)
        try:
            url = f"http://127.0.0.1:{self.rport}/?token={self.token}"
            self.assertEqual(self.req("POST", "/api/remotes", {"name": "vbox", "url": url})[0], 200)
            self.assertTrue(wait_for(lambda: any(m["name"] == "vbox" and m["ok"] for m in self.req("GET", "/api/sessions")[1]["machines"])))
            s = self.req("POST", "/api/sessions", {"cwd": self.rwork, "name": "far", "machine": "vbox", "group": "main"})[1]
            rid = s["id"].split("~", 1)[1]
            self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "earlier"})
            self.assertTrue(wait_for(lambda: any(c["state"] == "completed" for c in self.rdesk.cards(rid)), 15))
            code, r = self.req("POST", "/api/live/start", {"sid": s["id"], "sdp": "v=0 offer"})
            self.assertEqual(code, 200, r)
            self.assertTrue(fake.wait_attached(1))
            se = fake.bodies[-1]["session"]
            self.assertIn('session named "far"', se["instructions"])
            self.assertIn(f"folder {self.rwork}", se["instructions"])  # (that machine's path, not /@vbox/…)
            self.assertIn("answer: earlier", json.dumps(se["input"]))  # its recent work, read over there
            fake.emit(t_in("run the build", 500, 1500, "r1"), delegation("del_r", 1600, "dr"))
            self.assertTrue(wait_for(lambda: len(self.rdesk.cards(rid)) >= 2, 10))
            c = self.rdesk.cards(rid)[1]
            self.assertEqual((c["text"], c["origin"]["label"]), ("run the build", "Voice"))
            com = fake.wait_got("session.commentary.append")
            self.assertIn("answer: run the build", com[0]["content"])
            # a request typed over there (not by the call): its state reaches the voice through this server's events
            self.rdesk.send(rid, "typed over there")
            snap = lambda: [e["content"] for e in fake.got("session.thinking.append") if e["delegation_id"] is None
                            and 'Last finished: request #3 ("typed over there")' in e["content"]]
            self.assertTrue(wait_for(snap, 10), [e["content"] for e in fake.got("session.thinking.append")])
            self.assertIn('"far" is idle', snap()[-1])
            self.req("POST", "/api/live/stop", {})
            ok = wait_for(lambda: any(x.get("kind") == "note" for x in self.rdesk.cards(rid)), 10)
            self.assertTrue(ok, [l for f in Path(self.state, "live").glob("*.jsonl") for l in f.read_text().splitlines() if "note" in l])
            note = next(x for x in self.rdesk.cards(rid) if x.get("kind") == "note")
            self.assertIn("run the build", note["answer"])
            self.assertEqual(note["origin"]["label"], "Voice")
        finally:
            keys.OPENAI_BASE = base
            keys.set_key(self.state, "openai", None)
            fake.stop()
