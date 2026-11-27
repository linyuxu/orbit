"""Build step 6 (Agent API): the [orbit] line, orbit-agent ctx / open / show / sessions / send.

Run: uv run python -m unittest discover -s tests -v
"""

import json
import os
import subprocess
import sys
import threading
import time
import unittest
import urllib.request
from pathlib import Path

import test_skeleton


class AgentApiTest(test_skeleton.SkeletonTest):
    def agent(self, *args, inp=None, session=""):
        env = {**os.environ, "ORBIT_PORT": str(self.port), "ORBIT_SESSION": session}
        return subprocess.run([sys.executable, "-m", "orbit.cli.agent", *args], env=env, input=inp,
                              capture_output=True, text=True, timeout=60)

    def inputs(self, sid):
        """The raw texts the (fake) agent of session sid received, in order."""
        log = Path(self.tmp.name) / "projects" / "inputs.log"
        return [d["text"] for d in map(json.loads, log.read_text().splitlines()) if d["sid"] == sid]

    def listen(self, kinds, n=1):
        """Collect the first n SSE events of the given kinds (in a thread)."""
        got = []

        def run():
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/events", timeout=10) as r:
                for line in r:
                    if line.startswith(b"data: "):
                        ev = json.loads(line[6:])
                        if ev.get("t") in kinds:
                            got.append(ev)
                            if len(got) >= n:
                                return

        th = threading.Thread(target=run, daemon=True)
        th.start()
        time.sleep(0.2)
        return got, th

    # ------------------------------------------------------------------ [orbit] line
    def test_orbit_line_once_per_process_and_hidden(self):
        s = self.new_session("agent")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "first"})
        c1 = self.wait_card(s["id"], 1, ("completed",))
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "second"})
        c2 = self.wait_card(s["id"], 2, ("completed",))
        self.assertEqual(c1["text"], "first")  # the card shows what the user wrote
        got = self.inputs(s["id"])
        self.assertTrue(got[0].startswith("[orbit] You run inside Orbit"))  # the agent got the line
        self.assertIn(f'session "agent", id {s["id"]}', got[0])
        self.assertTrue(got[0].endswith("\n\nfirst"))
        self.assertEqual(got[1], "second")  # only the first request of the process
        # after a restart the history shows the user's text, and the new process hears the line again
        type(self)._unserve()
        type(self)._serve()
        _, d = self.req("GET", f"/api/sessions/{s['id']}/cards")
        self.assertEqual([c["text"] for c in d["cards"]], ["first", "second"])
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "third"})
        self.wait_card(s["id"], 3, ("completed",))
        self.assertTrue(self.inputs(s["id"])[2].startswith("[orbit] "))

    # ------------------------------------------------------------------ ctx
    def test_voice_cli(self):
        """orbit-agent voice: instruct from a file or stdin, show, ack; over the budget: non-zero, with the tokens left."""
        s = self.new_session("Voice cli")
        r = self.agent("voice", "instruct", "-", "--remind", "no answers", inp="Never give the answer away.", session=s["id"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("left); at the next call", r.stdout)
        out = self.agent("voice", "show", session=s["id"]).stdout
        self.assertIn("instructions: this session's", out)
        self.assertIn("reminder: no answers", out)
        self.assertIn("Never give the answer away.", out)
        r = self.agent("voice", "instruct", "-", inp="word " * 5000, session=s["id"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("at most 4000", r.stderr)
        self.assertIn("0 left", r.stderr)
        self.assertEqual(self.agent("voice", "ack", "off", session=s["id"]).returncode, 0)
        self.assertIn("ack: off", self.agent("voice", "show", session=s["id"]).stdout)
        self.assertNotEqual(self.agent("voice", "ack", "maybe", session=s["id"]).returncode, 0)
        self.assertNotEqual(self.agent("voice", "show").returncode, 0)  # (outside a session)

    def test_ctx(self):
        self.assertIn("not reported", self.agent("ctx").stdout)
        ctx = {"session": {"id": "abc", "name": "demo", "cwd": "/w"}, "tab": {"kind": "file", "path": "/w/a.py", "line": 7},
               "selection": "def f():\n    pass", "files": ["/w/a.py"], "group": "Main"}
        self.assertEqual(self.req("POST", "/api/ctx", ctx)[0], 200)
        out = self.agent("ctx").stdout
        self.assertIn("tab: file /w/a.py:7", out)
        self.assertIn("session: demo · /w", out)
        self.assertIn("    def f():", out)
        data = json.loads(self.agent("ctx", "--json").stdout)
        self.assertEqual(data["tab"]["line"], 7)

    # ------------------------------------------------------------------ open / show
    def test_open_broadcasts_without_taking_focus(self):
        f = Path(self.work) / "x.py"
        f.write_text("print(1)\n")
        s = self.new_session("opener")
        got, th = self.listen({"open"})
        r = self.agent("open", f"{f}:3", "--side", session=s["id"])
        self.assertEqual(r.returncode, 0, r.stderr)
        th.join(5)
        self.assertEqual(got[0], {"t": "open", "path": os.path.realpath(f), "line": 3, "side": True, "by": "opener",
                                  "sid": s["id"]})
        self.assertNotEqual(self.agent("open", "/no/such/file").returncode, 0)

    def test_show_serves_a_sandboxed_page(self):
        got, th = self.listen({"show"})
        r = self.agent("show", "-", "--title", "Chart", inp="<h1>hello</h1>")
        self.assertEqual(r.returncode, 0, r.stderr)
        th.join(5)
        ev = got[0]
        self.assertEqual((ev["title"], ev["sid"]), ("Chart", ""))  # (from a terminal: no session; the page then uses the tab in front)
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{ev['url']}") as resp:
            got = resp.read()
            self.assertTrue(got.endswith(b"<h1>hello</h1>") and b"web-key" in got, got[:60])  # (the key bridge first: no <head> to go after)
            self.assertIn("sandbox", resp.headers["Content-Security-Policy"])
        self.assertEqual(self.req("GET", "/show/nope")[0], 404)
        # Orbit's keys from the page (⌘⇧O back to the main tab…): a small script after <head>, the doctype first
        ev2 = self.req("POST", "/api/show", {"html": "<!doctype html><html><head><title>t</title></head><body>x</body></html>"})[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{ev2['url']}") as resp:
            body = resp.read().decode()
        self.assertTrue(body.startswith("<!doctype html><html><head><script>"), body[:80])
        self.assertIn("web-key", body)
        self.assertIn("MS:KeyO", body)
        # a non-HTML file goes to the side tab as a file
        md = Path(self.work) / "r.md"
        md.write_text("# r\n")
        got, th = self.listen({"open"})
        self.agent("show", str(md))
        th.join(5)
        self.assertTrue(got[0]["side"])

    def test_show_a_url_beside(self):
        """orbit-agent show URL: the site in the session's side tab (--browser: the browser, as before); only http(s)
        addresses are web side tabs (never javascript: or file:)."""
        s = self.new_session("browser")
        got, th = self.listen({"show"})
        r = self.agent("show", "https://example.com/docs?x=1", session=s["id"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("in the side tab", r.stdout)
        th.join(5)
        self.assertEqual((got[0]["url"], got[0]["title"], got[0]["sid"]), ("https://example.com/docs?x=1", "example.com", s["id"]))
        items = self.req("GET", f"/api/sessions/{s['id']}/side")[1]["items"]
        self.assertEqual([(x["kind"], x["url"]) for x in items], [("web", "https://example.com/docs?x=1")])
        self.assertEqual(self.req("POST", "/api/show", {"url": "javascript:alert(1)"})[0], 400)
        for bad in ("javascript:alert(1)", "file:///etc/passwd", "/etc/passwd"):
            code, e = self.req("POST", f"/api/sessions/{s['id']}/side", {"op": "open", "spec": {"kind": "web", "url": bad}})
            self.assertEqual(code, 400, bad)

    def test_a_sessions_side_panel_is_the_servers(self):
        """A session's side panel is kept by the server for every screen: an agent's page and a file it opens beside go
        in it (shown); a screen opens, selects and closes tabs there; each change goes to all as a side event; the same
        tab again is selected, not added; another session's panel is its own."""
        s, other = self.new_session("sider"), self.new_session("other")
        side = lambda sid=s["id"]: self.req("GET", f"/api/sessions/{sid}/side")[1]
        self.assertEqual(side(), {"items": [], "i": 0, "rev": 0})
        got, th = self.listen({"side"}, n=2)
        self.agent("show", "-", "--title", "Bells", inp="<h1>b</h1>", session=s["id"])
        f = Path(self.work) / "notes.py"
        f.write_text("x = 1\n")
        self.agent("open", f"{f}:1", "--side", session=s["id"])
        th.join(5)
        self.assertEqual([e["sid"] for e in got], [s["id"], s["id"]])
        st = side()
        self.assertEqual([(x["kind"], x.get("title") or x.get("path")) for x in st["items"]], [("web", "Bells"), ("file", os.path.realpath(f))])
        self.assertEqual((st["i"], st["items"][1]["line"]), (1, 1))  # (the newest shown)
        self.assertEqual(got[-1]["side"], st)
        self.agent("show", "-", "--title", "Elsewhere", inp="<h1>o</h1>", session=other["id"])
        self.assertEqual([x["title"] for x in side(other["id"])["items"]], ["Elsewhere"])
        # a screen: select, open (after the one shown), the same again (selected, not added), close
        bells = st["items"][0]["id"]
        post = lambda body: self.req("POST", f"/api/sessions/{s['id']}/side", body)
        self.assertEqual(post({"op": "select", "id": bells})[1]["i"], 0)
        code, st = post({"op": "open", "spec": {"kind": "review", "root": self.work, "sid": s["id"], "id": "rv1"}})
        self.assertEqual((code, [x["kind"] for x in st["items"]], st["i"]), (200, ["web", "review", "file"], 1))
        st = post({"op": "open", "spec": {"kind": "file", "path": os.path.realpath(f), "line": 7}})[1]
        self.assertEqual((len(st["items"]), st["i"], st["items"][2]["line"]), (3, 2, 7))
        st = post({"op": "close", "id": "rv1"})[1]
        self.assertEqual(([x["kind"] for x in st["items"]], st["i"]), (["web", "file"], 1))
        # a preview (a file clicked in the Files panel): the next one takes its place; one already there is selected
        g = Path(self.work) / "other.py"; g.write_text("y\n")
        prev = post({"op": "open", "spec": {"kind": "file", "path": str(g), "preview": True, "id": "pv1"}})[1]
        self.assertEqual(([x["kind"] for x in prev["items"]], prev["i"]), (["web", "file", "file"], 2))
        st = post({"op": "replace", "id": "pv1", "spec": {"kind": "file", "path": str(f.with_name("third.py")), "preview": True, "id": "pv2"}})[1]
        self.assertEqual(([x.get("path", "").rsplit("/", 1)[-1] for x in st["items"]], st["i"], st["items"][2]["id"]), (["", "notes.py", "third.py"], 2, "pv2"))
        st = post({"op": "replace", "id": "pv2", "spec": {"kind": "file", "path": os.path.realpath(f), "preview": True}})[1]
        self.assertEqual((len(st["items"]), st["i"]), (3, 1))  # (notes.py is there: shown, the preview stays)
        st = post({"op": "close", "id": "pv2"})[1]
        self.assertEqual(post({"op": "open", "spec": {"kind": "nope"}})[0], 400)
        self.assertEqual(post({"op": "close", "id": "gone"})[0], 400)
        self.assertEqual(self.req("GET", "/api/sessions/nope/side")[0], 404)
        # kept across a restart of the desk's reading (a file), and a page that is gone is left out
        (self.desk.show_dir / f"{bells}.html").unlink()
        st = side()
        self.assertEqual(([x["kind"] for x in st["items"]], st["i"]), (["file"], 0))
        # every tab closed, then a new one: its rev goes on growing (a page that saw the old ones takes it)
        before = side()["rev"]
        post({"op": "clear"})
        self.assertEqual(side()["items"], [])
        again = post({"op": "open", "spec": {"kind": "web", "url": "https://example.com/", "title": "Again"}})[1]
        self.assertGreater(again["rev"], before)

    def test_the_favorites_tabs_go_to_the_first_group(self):
        """The ★ Favorites view is gone: a layout kept with its file tabs gives them to the first group (once, not twice
        a tab that group has), its tab order goes."""
        first = next(g["id"] for g in self.desk.groups if not g.get("parent"))
        cur = self.desk.layout()
        lay = {"ftabs": {"fav": [{"path": "orbit:calendar", "pinned": True}, {"path": "/tmp/x.md", "pinned": True}],
                         first: [{"path": "/tmp/x.md", "pinned": True}]},
               "order": {"fav": ["s:1", "f:orbit:calendar"], first: ["f:/tmp/x.md"]}, "sides": {}}
        self.desk.set_layout(lay, cur["rev"])
        got = self.desk.layout()
        self.assertNotIn("fav", got["layout"]["ftabs"])
        self.assertNotIn("fav", got["layout"]["order"])
        self.assertEqual([t["path"] for t in got["layout"]["ftabs"][first]], ["/tmp/x.md", "orbit:calendar"])
        self.assertEqual(got["rev"], cur["rev"] + 2)  # (written once, as a change pages pick up)
        self.assertEqual(self.desk.layout()["rev"], got["rev"])
        self.desk._layout_file().unlink()  # (the next test starts with none kept)

    def test_the_tab_layout_is_the_servers(self):
        """The file tabs, the tab row's order and file tabs' side tabs: one layout every page shares, written whole from
        the revision a page had (a page behind gets 409 and the current one), told to all as a layout event."""
        self.assertEqual(self.req("GET", "/api/layout")[1], {"layout": {"ftabs": {}, "order": {}, "sides": {}}, "rev": 0})
        lay = {"ftabs": {"main": [{"path": "/tmp/a.py", "pinned": True}, {"path": "orbit:schedules", "pinned": True}]},
               "order": {"main": ["f:/tmp/a.py", "s:abc"]},
               "sides": {"f:/tmp/a.py": {"list": [{"kind": "review", "root": "/tmp", "id": "r1"}], "i": 0}}}
        got, th = self.listen({"layout"})
        code, r = self.req("POST", "/api/layout", {"layout": lay, "base": 0})
        th.join(5)
        self.assertEqual((code, r["rev"]), (200, 1))
        self.assertEqual(got[0], {"t": "layout", "layout": lay, "rev": 1})
        self.assertEqual(self.req("GET", "/api/layout")[1], {"layout": lay, "rev": 1})
        code, r = self.req("POST", "/api/layout", {"layout": {**lay, "order": {}}, "base": 0})  # (a page behind)
        self.assertEqual((code, r["rev"], r["layout"]), (409, 1, lay))
        self.assertEqual(self.req("POST", "/api/layout", {"layout": {"ftabs": []}, "base": 1})[0], 400)
        self.assertEqual(self.req("POST", "/api/layout", {"layout": {"ftabs": {}, "order": {}, "sides": {}}, "base": 1})[1]["rev"], 2)

    # ------------------------------------------------------------------ sessions / send
    def test_sessions_and_send(self):
        a = self.new_session("asker")
        b = self.new_session("helper")
        out = self.agent("sessions", session=a["id"]).stdout
        self.assertIn("asker (you)", out)
        self.assertIn("helper", out)
        r = self.agent("send", "HELPER", "what", "is", "2+2", "--wait", session=a["id"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('(from session "asker") what is 2+2', r.stdout)  # the fake echoes the request
        _, d = self.req("GET", f"/api/sessions/{b['id']}/cards")
        self.assertEqual(d["cards"][-1]["text"], '(from session "asker") what is 2+2')
        self.assertIn("that is this session", self.agent("send", "asker", "hi", session=a["id"]).stderr)
        self.assertIn("no such session", self.agent("send", "nobody", "hi").stderr)
        r = self.agent("send", b["id"][:8], "-", inp="piped")
        self.assertIn("sent to helper", r.stdout)


    def test_steer_another_sessions_running_request(self):
        a = self.new_session("lead")
        b = self.new_session("worker2")
        self.req("POST", f"/api/sessions/{b['id']}/send", {"text": "slow build"})
        end = time.time() + 5
        while time.time() < end:  # until its tool step runs
            _, d = self.req("GET", f"/api/sessions/{b['id']}/cards")
            if d["cards"] and d["cards"][0]["steps"]:
                break
            time.sleep(0.05)
        r = self.agent("steer", "worker2", "skip", "the", "tests", "--wait", session=a["id"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("joined worker2 #1", r.stderr)
        self.assertIn('steered: (from session "lead") skip the tests', r.stdout)  # one turn, its answer
        _, d = self.req("GET", f"/api/sessions/{b['id']}/cards")
        self.assertEqual(len(d["cards"]), 1)
        r = self.agent("steer", "worker2", "now this", session=a["id"])  # idle: an ordinary request
        self.assertIn("sent to worker2 as #2", r.stdout)
        self.assertIn("that is this session", self.agent("steer", "lead", "x", session=a["id"]).stderr)

    def test_new_groups_wait_history_search_continue(self):
        out = self.agent("groups").stdout
        self.assertTrue(out.strip())  # at least the default group
        group = out.split("\t")[0]
        r = self.agent("new", self.work, "--name", "worker", "--group", group, "--mode", "plan", "count", "the", "files")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("started worker", r.stdout)
        sid = r.stdout.split("(")[1].split(")")[0]
        w = self.agent("wait", "worker")
        self.assertEqual(w.returncode, 0, w.stderr)
        self.assertIn("count the files", w.stdout)  # the fake echoes the request
        _, s = self.req("POST", "/api/resolve-session", {"ref": "worker"})
        self.assertEqual(s["mode"], "plan")
        self.assertIn("no paused request", self.agent("continue", "worker").stderr)
        found = self.agent("search", "count", "files").stdout
        self.assertIn(f"orbit://session/{sid}#1", found)
        self.req("POST", f"/api/sessions/{sid}/close")
        self.assertIn("no such session", self.agent("read", "worker").stderr)  # read: open sessions only
        h = self.agent("history", sid)
        self.assertEqual(h.returncode, 0, h.stderr)
        self.assertIn("count the files", h.stdout)  # history reaches a closed one
        self.assertIn("no such folder", self.agent("new", "/nope/nowhere").stderr)

    # ------------------------------------------------------------------ @@ mentions, orbit-agent read
    def test_mention_from_user_and_agent(self):
        a = self.new_session("Data Pipeline")
        b = self.new_session("reviewer")
        self.req("POST", f"/api/sessions/{a['id']}/send", {"text": "load the csv"})
        self.wait_card(a["id"], 1, ("completed",))
        # The user mentions @@reviewer from Data Pipeline: the source, the words, its last turns, how to answer back.
        code, c = self.req("POST", f"/api/sessions/{b['id']}/mention", {"from": a["id"], "text": "@@reviewer check this"})
        self.assertEqual(code, 200, c)
        t = self.wait_card(b["id"], c["id"], ("completed",))["text"]
        self.assertIn(f"from @@Data-Pipeline (session {a['id']})", t)
        self.assertIn("> @@reviewer check this", t)
        self.assertIn("#1 request: load the csv", t)
        self.assertIn(f"orbit-agent read {a['id']} --last 10", t)
        self.assertIn(f'orbit-agent mention {a["id"]}', t)
        # An agent's mention back is a note, not a task, and says not to ping-pong.
        r = self.agent("mention", "@@Data-Pipeline", "done:", "3", "rows", session=b["id"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("mentioned Data Pipeline", r.stdout)
        _, d = self.req("GET", f"/api/sessions/{a['id']}/cards")
        t = d["cards"][-1]["text"]
        self.assertTrue(t.startswith(f"Note from @@reviewer (session {b['id']}, another agent): done: 3 rows"))
        self.assertIn("Do not mention @@reviewer back unless it asks you to", t)
        self.assertIn("that is this session", self.agent("mention", "reviewer", "x", session=b["id"]).stderr)
        self.assertEqual(self.req("POST", f"/api/sessions/{a['id']}/mention", {"from": a["id"], "text": "x"})[0], 400)
        # orbit-agent read: the other session's recent turns, without the [orbit] line.
        out = self.agent("read", "Data-Pipeline", "--last", "5").stdout
        self.assertIn("## #1 (completed)\n\nload the csv", out)
        self.assertNotIn("[orbit]", out)

    def test_edit_resend_withdraw(self):
        s = self.new_session("ed")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "slow one"})
        self.wait_card(s["id"], 1, ("running",))
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "queued two"})
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "queued three"})
        self.assertEqual(self.req("POST", f"/api/sessions/{s['id']}/edit", {"cid": 1, "text": "x"})[0], 400)  # running
        self.assertEqual(self.req("POST", f"/api/sessions/{s['id']}/withdraw", {"cid": 1})[0], 400)
        # A queued request changes in place, or is withdrawn.
        _, c = self.req("POST", f"/api/sessions/{s['id']}/edit", {"cid": 2, "text": "queued two (edited)"})
        self.assertEqual((c["id"], c["text"], c["state"]), (2, "queued two (edited)", "queued"))
        self.assertEqual(self.req("POST", f"/api/sessions/{s['id']}/withdraw", {"cid": 3})[0], 200)
        self.req("POST", f"/api/sessions/{s['id']}/stop")
        self.wait_card(s["id"], 1, ("cancelled",))
        _, d = self.req("GET", f"/api/sessions/{s['id']}/cards")
        by = {c["id"]: c for c in d["cards"]}
        self.assertTrue(by[3].get("hidden"))
        # Stop cancels what is queued too; edit a finished request: it is replaced by a new one.
        _, n = self.req("POST", f"/api/sessions/{s['id']}/edit", {"cid": 1, "text": "fast one"})
        self.assertEqual(n["text"], "fast one")
        self.wait_card(s["id"], n["id"], ("completed",))
        _, d = self.req("GET", f"/api/sessions/{s['id']}/cards")
        self.assertTrue(next(c for c in d["cards"] if c["id"] == 1).get("hidden"))


for name in [n for n in dir(test_skeleton.SkeletonTest) if n.startswith("test_")]:
    if name not in AgentApiTest.__dict__:
        setattr(AgentApiTest, name, None)

if __name__ == "__main__":
    unittest.main()
