"""Build step 2 (Sessions): groups, favorites, tab order, close / reopen with names, clone, resume.

Run: uv run python -m unittest discover -s tests -v
"""

import json
import time
import unittest
from pathlib import Path

import test_skeleton


class SessionsTest(test_skeleton.SkeletonTest):
    # Reuse the server fixture; skip the skeleton tests themselves.
    def _all(self, closed=False):
        _, d = self.req("GET", "/api/sessions" + ("?closed=1" if closed else ""))
        return d

    def test_subagents_show_under_their_agent_step(self):
        """A subagent's text and tool calls go under the Agent step that started it, with its type and its report:
        live, and again from <session>/subagents/ when the cards are rebuilt."""
        sid = self.req("POST", "/api/sessions", {"cwd": self.work})[1]["id"]
        self.req("POST", f"/api/sessions/{sid}/send", {"text": "subagent please"})
        c = self.wait_card(sid, 1, "completed")
        agent = next(st for st in c["steps"] if st.get("agent"))
        self.assertEqual((agent["name"], agent["agent"], agent["summary"]), ("Agent", "Explore", "Find the parser"))
        self.assertEqual([(x["kind"], x.get("name") or x.get("text")) for x in agent["sub"]],
                         [("note", "Looking for the parser."), ("tool", "Grep"), ("note", "Found it in a.py.")])
        self.assertTrue(all(x["done"] for x in agent["sub"]))
        self.assertIn("returns early on empty input", agent["result"])
        self.assertNotIn("Looking for the parser.", c["answer"])  # the subagent's words are not the main answer
        from orbit.drivers.claude import ClaudeDriver
        hist = ClaudeDriver(session_id=sid, cwd=self.work, model="", mode="default", emit=lambda e: None,
                            resume=True).history()
        h = next(st for st in hist[-1].steps if st.get("agent"))
        self.assertEqual(h["agent"], "Explore")
        self.assertEqual([x.get("name") or x.get("text") for x in h["sub"]], ["Looking for the parser.", "Grep", "Found it in a.py."])
        self.assertIn("returns early on empty input", h["result"])

    def test_resume_command(self):
        """Copy resume command: cd to the folder, then the harness's own resume; none before anything was asked."""
        import shlex
        sid = self.req("POST", "/api/sessions", {"cwd": self.work})[1]["id"]
        code, d = self.req("GET", f"/api/sessions/{sid}/resume-command")
        self.assertEqual(code, 400)
        self.assertIn("nothing to resume", d["error"])
        self.req("POST", f"/api/sessions/{sid}/send", {"text": "hello"})
        self.wait_card(sid, 1, "completed")
        code, d = self.req("GET", f"/api/sessions/{sid}/resume-command")
        self.assertEqual(code, 200, d)
        self.assertTrue(d["command"].startswith(f"cd {shlex.quote(self.work)} && "))
        self.assertTrue(d["command"].endswith(f" --resume {sid}"))
        self.assertEqual(self.req("GET", "/api/sessions/nope/resume-command")[0], 404)

    def test_background_subagent_reports_later(self):
        """A subagent run in the background: its launch is no report; it stays running past its request, and its
        work and report (a task notification) land on its Agent step; rebuilt, the report is its last words."""
        sid = self.req("POST", "/api/sessions", {"cwd": self.work})[1]["id"]
        self.req("POST", f"/api/sessions/{sid}/send", {"text": "bgagent please"})
        c = self.wait_card(sid, 1, "completed")
        agent = next(st for st in c["steps"] if st.get("agent"))
        self.assertTrue(agent["background"])
        self.assertNotIn("Async agent launched", agent.get("result", ""))
        end = time.time() + 10
        while time.time() < end:
            c = self.req("GET", f"/api/sessions/{sid}/cards")[1]["cards"][0]
            agent = next(st for st in c["steps"] if st.get("agent"))
            if agent["done"]:
                break
            time.sleep(0.1)
        self.assertTrue(agent["done"])
        self.assertFalse(agent["error"])
        self.assertIn("returns early on empty input", agent["result"])
        self.assertEqual([x.get("name") or x.get("text") for x in agent["sub"]][:3], ["Looking for the parser.", "Grep", "Found it in a.py."])
        from orbit.drivers.claude import ClaudeDriver
        hist = ClaudeDriver(session_id=sid, cwd=self.work, model="", mode="default", emit=lambda e: None,
                            resume=True).history()
        h = next(st for st in hist[-1].steps if st.get("agent"))
        self.assertIn("returns early on empty input", h["result"])
        self.assertNotIn("Async agent launched", h["result"])

    def test_a_background_subagent_ends_with_its_process(self):
        """A background subagent still at work when its agent's process goes (a restart): it is marked ended (lost),
        never left running forever."""
        sid = self.req("POST", "/api/sessions", {"cwd": self.work})[1]["id"]
        self.req("POST", f"/api/sessions/{sid}/send", {"text": "bgslow please"})
        self.wait_card(sid, 1, "completed")
        agent = lambda: next(st for st in self.req("GET", f"/api/sessions/{sid}/cards")[1]["cards"][0]["steps"] if st.get("agent"))
        end = time.time() + 10
        while time.time() < end and not any(x.get("name") == "Read" for x in agent().get("sub", [])):
            time.sleep(0.1)
        a = agent()
        self.assertEqual((a["done"], a["background"]), (False, True))  # at work, past its request
        info = lambda: next(x for x in self.req("GET", "/api/sessions")[1]["sessions"] if x["id"] == sid)
        self.assertEqual((info()["state"], info()["agents"]), ("ready", 1))  # not "working" (a request runs at once), but at work
        self.req("POST", f"/api/sessions/{sid}/restart", {})
        self.assertEqual(info()["agents"], 0)
        a = agent()
        self.assertEqual((a["done"], a["error"], a.get("lost")), (True, True, True))
        self.assertIn("ended when its agent's process did", a["result"])
        self.assertTrue(all(x["done"] for x in a["sub"]))

    def test_what_the_agent_says_between_tools_is_kept_whole(self):
        """Every text the agent writes between tool calls stays in the card, whole (not cut to a summary), in order:
        live, and again when the cards are rebuilt from the transcript."""
        sid = self.req("POST", "/api/sessions", {"cwd": self.work})[1]["id"]
        self.req("POST", f"/api/sessions/{sid}/send", {"text": "narrate please"})
        c = self.wait_card(sid, 1, "completed")
        notes = [st for st in c["steps"] if st["kind"] == "note"]
        said = [n["text"] for n in notes]  # tests/fake_claude.py NARRATE
        self.assertEqual(len(said), 2)
        self.assertTrue(said[0].startswith("**First** I'll read `a.py`") and len(said[0]) > 300, said[0][:80])
        self.assertEqual(said[1], "Found it: the key is built before the locale is set.\n\n- fix the key\n- add a test")
        kinds = [st["kind"] for st in c["steps"]]
        self.assertEqual(kinds, ["note", "tool", "note", "tool", "tool", "tool"])
        self.assertEqual(c["answer"], "answer: narrate please")
        from orbit.drivers.claude import ClaudeDriver  # rebuilt from the transcript (after a restart): the same
        hist = ClaudeDriver(session_id=sid, cwd=self.work, model="", mode="default", emit=lambda e: None,
                            resume=True).history()
        self.assertEqual([st["text"] for st in hist[-1].steps if st["kind"] == "note"], said)

    def test_default_group_and_create_in_group(self):
        d = self._all()
        self.assertTrue(d["groups"])
        _, g = self.req("POST", "/api/groups", {"name": "Work"})
        code, s = self.req("POST", "/api/sessions", {"cwd": self.work, "group": g["id"]})
        self.assertEqual(code, 200)
        self.assertEqual(s["group"], g["id"])
        _, s2 = self.req("POST", "/api/sessions", {"cwd": self.work, "group": "nope"})
        self.assertEqual(s2["group"], self._all()["groups"][0]["id"])  # unknown group → first group

    def test_group_rename_move_delete(self):
        _, a = self.req("POST", "/api/groups", {"name": "A"})
        _, b = self.req("POST", "/api/groups", {"name": "B"})
        self.assertEqual(self.req("POST", "/api/groups", {"name": " "})[0], 400)
        self.req("POST", f"/api/groups/{a['id']}/rename", {"name": "A2"})
        self.req("POST", f"/api/groups/{b['id']}/move", {"delta": -1})
        names = [g["name"] for g in self._all()["groups"]]
        self.assertLess(names.index("B"), names.index("A2"))
        _, s = self.req("POST", "/api/sessions", {"cwd": self.work, "group": b["id"]})
        self.req("POST", f"/api/groups/{b['id']}/delete")
        d = self._all(closed=True)
        self.assertNotIn("B", [g["name"] for g in d["groups"]])
        moved = next(x for x in d["sessions"] if x["id"] == s["id"])
        self.assertEqual(moved["group"], d["groups"][0]["id"])

    def test_group_names_are_unique(self):
        _, a = self.req("POST", "/api/groups", {"name": "Research  Notes"})
        self.assertEqual(a["name"], "Research Notes")  # (inner spaces as one)
        for taken in ("research notes", " RESEARCH   NOTES ", "Favorites", "fleet"):
            code, r = self.req("POST", "/api/groups", {"name": taken})
            self.assertEqual(code, 400, taken)
            self.assertIn("already", r["error"])
        _, b = self.req("POST", "/api/groups", {"name": "Drafts"})
        self.assertEqual(self.req("POST", f"/api/groups/{b['id']}/rename", {"name": "research NOTES"})[0], 400)
        self.assertEqual(self.req("POST", f"/api/groups/{a['id']}/rename", {"name": "Research notes"})[0], 200)  # itself
        self.assertEqual([g["name"] for g in self._all()["groups"]].count("Research notes"), 1)

    def test_last_group_cannot_be_deleted(self):
        for g in self._all()["groups"][1:]:
            self.req("POST", f"/api/groups/{g['id']}/delete")
        only = self._all()["groups"]
        self.assertEqual(len(only), 1)
        self.assertEqual(self.req("POST", f"/api/groups/{only[0]['id']}/delete")[0], 400)

    def test_favorite_and_move_group(self):
        s = self.new_session()
        _, g = self.req("POST", "/api/groups", {"name": "Fav"})
        _, s = self.req("POST", f"/api/sessions/{s['id']}/update", {"favorite": True, "group": g["id"]})
        self.assertTrue(s["favorite"])
        self.assertEqual(s["group"], g["id"])
        self.assertEqual(self.req("POST", f"/api/sessions/{s['id']}/update", {"group": "nope"})[0], 400)

    def test_tab_order(self):
        a, b, c = self.new_session("a"), self.new_session("b"), self.new_session("c")
        self.req("POST", f"/api/sessions/{c['id']}/move", {"before": a["id"]})
        ids = [x["id"] for x in self._all()["sessions"]]
        self.assertLess(ids.index(c["id"]), ids.index(a["id"]))
        self.req("POST", f"/api/sessions/{c['id']}/move", {"before": None})
        self.assertEqual([x["id"] for x in self._all()["sessions"]][-1], c["id"])

    def test_close_keeps_name_and_reopen(self):
        s = self.new_session("keeper")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "hello"})
        self.wait_card(s["id"], 1, ("completed",))
        self.req("POST", f"/api/sessions/{s['id']}/close")
        self.assertNotIn(s["id"], [x["id"] for x in self._all()["sessions"]])
        closed = next(x for x in self._all(closed=True)["sessions"] if x["id"] == s["id"])
        self.assertTrue(closed["closed"] and closed["named"])
        self.assertEqual(closed["name"], "keeper")
        self.assertEqual(closed["last"]["text"], "answer: hello")
        self.assertIsNotNone(closed["closed_at"])
        _, r = self.req("POST", f"/api/sessions/{s['id']}/reopen")
        self.assertFalse(r["closed"])
        self.assertEqual(r["name"], "keeper")
        _, c = self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "again"})
        self.assertEqual(c["id"], 2)
        self.assertEqual(self.wait_card(s["id"], 2, ("completed",))["answer"], "answer: again")

    def test_close_while_working_cancels(self):
        s = self.new_session()
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "slow"})
        self.wait_card(s["id"], 1, ("running",))
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "queued"})
        self.req("POST", f"/api/sessions/{s['id']}/close")
        _, d = self.req("GET", f"/api/sessions/{s['id']}/cards")
        self.assertEqual([c["state"] for c in d["cards"]], ["cancelled", "cancelled"])

    def test_rename_sets_and_clears_named(self):
        s = self.new_session("")
        self.assertFalse(s["named"])
        _, r = self.req("POST", f"/api/sessions/{s['id']}/rename", {"name": "Named"})
        self.assertTrue(r["named"])
        _, r = self.req("POST", f"/api/sessions/{s['id']}/rename", {"name": ""})
        self.assertFalse(r["named"])
        self.assertEqual(r["name"], Path(self.work).name)

    def test_clone_forks_conversation(self):
        s = self.new_session("orig")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "first"})
        self.wait_card(s["id"], 1, ("completed",))
        self.req("POST", f"/api/sessions/{s['id']}/model", {"model": "opus", "effort": "high"})
        self.req("POST", f"/api/sessions/{s['id']}/mode", {"mode": "plan"})
        _, c = self.req("POST", f"/api/sessions/{s['id']}/fork")
        self.assertEqual(c["name"], "orig (fork)")
        src = next(x for x in self._all()["sessions"] if x["id"] == s["id"])
        for k in ("cwd", "harness", "model", "effort", "mode", "group"):  # every setting goes with it
            self.assertEqual(c[k], src[k], k)
        _, named = self.req("POST", f"/api/sessions/{s['id']}/fork", {"name": "try B"})
        self.assertEqual((named["name"], named["named"]), ("try B", True))
        self.req("POST", f"/api/sessions/{named['id']}/close")
        ids = [x["id"] for x in self._all()["sessions"]]
        self.assertEqual(ids.index(c["id"]), ids.index(s["id"]) + 1)  # right after the original
        _, d = self.req("GET", f"/api/sessions/{c['id']}/cards")
        self.assertEqual([x["text"] for x in d["cards"]], ["first"])
        self.req("POST", f"/api/sessions/{c['id']}/send", {"text": "second"})
        self.wait_card(c["id"], 2, ("completed",))
        argv = [json.loads(l) for l in (Path(self.tmp.name) / "projects" / "argv.log").read_text().splitlines()]
        fork = [a for a in argv if "--fork-session" in a]
        self.assertTrue(any(a[a.index("--resume") + 1] == s["id"] and a[a.index("--session-id") + 1] == c["id"]
                            for a in fork))
        _, d = self.req("GET", f"/api/sessions/{s['id']}/cards")
        self.assertEqual(len(d["cards"]), 1)  # the original is untouched

    def test_resume_starts_process(self):
        s = self.new_session()
        self.req("POST", f"/api/sessions/{s['id']}/close")
        self.req("POST", f"/api/sessions/{s['id']}/reopen")
        _, r = self.req("POST", f"/api/sessions/{s['id']}/resume")
        end = time.time() + 5
        while time.time() < end:
            st = next(x for x in self._all()["sessions"] if x["id"] == s["id"])["state"]
            if st == "ready":
                break
            time.sleep(0.05)
        self.assertEqual(st, "ready")


    def test_mode_change_restarts_on_the_same_conversation(self):
        s = self.new_session("moded")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "first"})
        self.wait_card(s["id"], 1, ("completed",))
        code, r = self.req("POST", f"/api/sessions/{s['id']}/mode", {"mode": "plan"})
        self.assertEqual(code, 200)
        self.assertEqual(r["mode"], "plan")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "second"})
        self.wait_card(s["id"], 2, ("completed",))
        argv = [json.loads(l) for l in (Path(self.tmp.name) / "projects" / "argv.log").read_text().splitlines()]
        mine = [a for a in argv if s["id"] in a]
        last = mine[-1]
        self.assertEqual(last[last.index("--permission-mode") + 1], "plan")
        self.assertEqual(last[last.index("--resume") + 1], s["id"])  # the same conversation
        # Orbit's own skills as a Claude Code plugin, unless `orbit tools install` put the skill in ~/.claude/skills
        self.assertEqual("--plugin-dir" in last, not (Path.home() / ".claude" / "skills" / "orbit-agents").exists())
        self.assertEqual(self.req("POST", f"/api/sessions/{s['id']}/mode", {"mode": "nope"})[0], 400)

    def test_steer_that_crosses_the_end_of_the_turn_becomes_a_request(self):
        """The turn ends between the check and the steer (the driver says nothing runs): a request, not an error."""
        s = self.new_session("crossing")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "slow job"})
        end = time.time() + 5
        while time.time() < end:
            _, d = self.req("GET", f"/api/sessions/{s['id']}/cards")
            if d["cards"] and d["cards"][0]["steps"]:
                break
            time.sleep(0.05)
        drv = self.desk.sessions[s["id"]].driver

        def ended(text):
            raise RuntimeError("no request is running")
        drv.steer = ended
        code, c = self.req("POST", f"/api/sessions/{s['id']}/steer", {"text": "and then this"})
        self.assertEqual(code, 200, c)
        self.assertFalse(c["steered"])
        self.assertEqual((c["id"], c["text"], c["state"]), (2, "and then this", "queued"))  # its own request, next

    def test_steer_joins_the_running_request(self):
        s = self.new_session("steered")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "slow job"})
        end = time.time() + 5
        while time.time() < end:  # until the tool step runs
            _, d = self.req("GET", f"/api/sessions/{s['id']}/cards")
            if d["cards"] and d["cards"][0]["steps"]:
                break
            time.sleep(0.05)
        code, c = self.req("POST", f"/api/sessions/{s['id']}/steer", {"text": "use the cache"})
        self.assertEqual(code, 200)
        self.assertTrue(c["steered"])
        self.assertEqual(c["id"], 1)
        done = self.wait_card(s["id"], 1, ("completed",))
        self.assertIn("steered: use the cache", done["answer"])
        self.assertEqual([x["text"] for x in done["steers"]], ["use the cache"])
        _, d = self.req("GET", f"/api/sessions/{s['id']}/cards")
        self.assertEqual(len(d["cards"]), 1)  # no second request
        from orbit.drivers.claude import ClaudeDriver  # rebuilt from the transcript (after a restart): still one card
        hist = ClaudeDriver(session_id=s["id"], cwd=self.work, model="", mode="default", emit=lambda e: None,
                            resume=True).history()
        self.assertEqual(len(hist), 1)
        self.assertEqual([x["text"] for x in hist[0].steers], ["use the cache"])
        # nothing running: a steer is an ordinary request
        _, c2 = self.req("POST", f"/api/sessions/{s['id']}/steer", {"text": "and now this"})
        self.assertFalse(c2["steered"])
        self.assertEqual(c2["id"], 2)

    def test_init_reports_model_and_commands(self):
        s = self.new_session("info")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "hi"})
        self.wait_card(s["id"], 1, ("completed",))
        _, r = self.req("POST", "/api/resolve-session", {"ref": s["id"]})
        self.assertEqual(r["model_seen"], "fake-1")
        self.assertEqual(r["commands"], ["compact", "context", "model", "review"])  # doctor is terminal-only
        _, h = self.req("GET", "/api/harnesses")
        claude = next(x for x in h["harnesses"] if x["id"] == "claude")
        self.assertEqual(claude["default_seen"], "fake-1")  # "Default" resolved, for the ⌘N dialog
        self.assertIn("claude-opus-5-5", [m["value"] for m in claude["models"]])
        self.assertIn("high", claude["efforts"])
        code, r = self.req("POST", f"/api/sessions/{s['id']}/model", {"model": "claude-haiku-5-5", "effort": "low"})
        self.assertEqual(code, 200)
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "again"})
        self.wait_card(s["id"], 2, ("completed",))
        argv = [json.loads(l) for l in (Path(self.tmp.name) / "projects" / "argv.log").read_text().splitlines()]
        last = [a for a in argv if s["id"] in a][-1]
        self.assertEqual(last[last.index("--model") + 1], "claude-haiku-5-5")
        self.assertEqual(last[last.index("--effort") + 1], "low")

    def test_attach_a_terminal_conversation(self):
        import uuid
        conv = str(uuid.uuid4())
        d = Path(self.tmp.name) / "projects" / "-term"
        d.mkdir(parents=True, exist_ok=True)
        lines = [{"type": "user", "cwd": self.work, "message": {"role": "user", "content": "explain the build"},
                  "timestamp": "2026-11-21T10:00:00Z"},
                 {"type": "custom-title", "customTitle": "old name", "sessionId": conv},
                 {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "it builds"}]},
                  "timestamp": "2026-11-21T10:00:05Z"},
                 {"type": "ai-title", "aiTitle": "Build explained", "sessionId": conv},
                 {"type": "custom-title", "customTitle": "nightly report", "sessionId": conv}]  # /rename there: the last counts
        (d / f"{conv}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        ids = self.state / "harness-ids.json"  # a Codex session here: its thread is Orbit's, not offered
        ids.write_text(json.dumps({"codex": {"some-session": "some-thread"}}))
        try:
            code, ext = self.req("GET", "/api/external-sessions")
        finally:
            ids.unlink()
        self.assertEqual(code, 200, ext)
        hit = next(x for x in ext["sessions"] if x["id"] == conv)
        self.assertEqual((hit["harness"], hit["first"], hit["name"], hit["title"]),
                         ("claude", "explain the build", "nightly report", "Build explained"))
        code, s = self.req("POST", "/api/attach-session", {"harness": "claude", "id": conv, "cwd": self.work, "name": hit["name"]})
        self.assertEqual(code, 200)
        self.assertEqual((s["id"], s["name"], s["named"]), (conv, "nightly report", True))  # its name comes along
        _, cards = self.req("GET", f"/api/sessions/{conv}/cards")
        self.assertEqual([(c["text"], c["answer"]) for c in cards["cards"]], [("explain the build", "it builds")])
        self.req("POST", f"/api/sessions/{conv}/send", {"text": "and test it"})
        self.wait_card(conv, 2, ("completed",))
        with open(Path(self.tmp.name) / "projects" / "argv.log") as f:
            last = [json.loads(l) for l in f if conv in l][-1]
        self.assertEqual(last[last.index("--resume") + 1], conv)  # continues the terminal's conversation
        _, ext = self.req("GET", "/api/external-sessions")
        self.assertNotIn(conv, [x["id"] for x in ext["sessions"]])  # attached: no longer offered

    def test_create_only_save(self):
        p = Path(self.work) / "mine.md"
        code, _ = self.req("POST", "/api/file", {"path": str(p), "text": "x", "base_hash": None, "create_only": True})
        self.assertEqual(code, 200)
        code, d = self.req("POST", "/api/file", {"path": str(p), "text": "y", "base_hash": None, "create_only": True})
        self.assertEqual(code, 409)  # Save mine as a copy picks the next free name
        self.assertEqual(p.read_text(), "x")

# Do not run the inherited skeleton tests twice.
for name in [n for n in dir(test_skeleton.SkeletonTest) if n.startswith("test_")]:
    if name not in SessionsTest.__dict__:
        setattr(SessionsTest, name, None)

if __name__ == "__main__":
    unittest.main()
