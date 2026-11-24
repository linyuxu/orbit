"""The Codex, agy and OpenCode drivers behind the same Desk: send → card, stop, errors, paused, restart, clone — against fakes.

Run: uv run python -m unittest discover -s tests -v
"""

import json
import os
import shlex
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


class HarnessCases:
    HARNESS = ""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        py = shlex.quote(sys.executable)
        os.environ["ORBIT_CODEX_BIN"] = f"{py} {shlex.quote(str(HERE / 'fake_codex.py'))}"
        os.environ["ORBIT_AGY_BIN"] = f"{py} {shlex.quote(str(HERE / 'fake_agy.py'))}"
        os.environ["ORBIT_CODEX_SESSIONS"] = str(t / "codex" / "sessions")
        os.environ["CODEX_HOME"] = str(t / "codex")
        os.environ["ORBIT_AGY_CONVERSATIONS"] = str(t / "agy")
        os.environ["ORBIT_OPENCODE_BIN"] = f"{py} {shlex.quote(str(HERE / 'fake_opencode.py'))}"
        os.environ["ORBIT_OPENCODE_DB"] = str(t / "opencode" / "opencode.db")
        (t / "codex").mkdir()
        (t / "codex" / "models_cache.json").write_text(json.dumps({"models": [
            {"slug": "fake-5", "display_name": "Fake 5", "visibility": "list"},
            {"slug": "hidden", "display_name": "Hidden", "visibility": "hide"}]}))
        (t / "work").mkdir()
        cls.work = str((t / "work").resolve())
        cls.state = t / "state"
        cls.store = Path({"codex": os.environ["ORBIT_CODEX_SESSIONS"], "agy": os.environ["ORBIT_AGY_CONVERSATIONS"],
                          "opencode": str(t / "opencode")}[cls.HARNESS])
        from orbit.workspace import notes
        os.environ.pop("ORBIT_VAULT", None)
        notes.configure(str(t / "data"))
        cls._serve()

    @classmethod
    def _serve(cls):
        from orbit.desk import Desk
        from orbit.server import make_server

        cls.desk = Desk(cls.state)
        cls.srv = make_server(0, cls.desk)
        cls.port = cls.srv.server_address[1]
        cls.srv.RequestHandlerClass.port = cls.port
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def _unserve(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.desk.shutdown()

    @classmethod
    def tearDownClass(cls):
        cls._unserve()
        cls.tmp.cleanup()

    # ------------------------------------------------------------------ helpers
    def req(self, method, path, body=None):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                   headers={"Content-Type": "application/json"},
                                   data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def wait_card(self, sid, cid, states, timeout=10):
        end, c = time.time() + timeout, None
        while time.time() < end:
            _, d = self.req("GET", f"/api/sessions/{sid}/cards")
            c = next((c for c in d["cards"] if c["id"] == cid), None)
            if c and c["state"] in states:
                return c
            time.sleep(0.05)
        self.fail(f"card {cid} never reached {states}: {c}")

    def new_session(self, name="t", mode="default", model=""):
        code, s = self.req("POST", "/api/sessions", {"cwd": self.work, "name": name, "harness": self.HARNESS,
                                                      "mode": mode, "model": model})
        self.assertEqual(code, 200, s)
        self.assertEqual(s["harness"], self.HARNESS)
        return s

    def send(self, sid, text):
        code, c = self.req("POST", f"/api/sessions/{sid}/send", {"text": text})
        self.assertEqual(code, 200, c)
        return c

    def argv(self):
        return [json.loads(l) for l in (self.store / "argv.log").read_text().splitlines()]

    # ------------------------------------------------------------------ tests
    def test_listed_with_models(self):
        _, d = self.req("GET", "/api/harnesses")
        h = {x["id"]: x for x in d["harnesses"]}
        self.assertEqual(set(h), {"claude", "codex", "agy", "opencode"})
        self.assertEqual(h["codex"]["label"], "Codex")
        self.assertEqual([m["value"] for m in h["codex"]["models"]], ["fake-5"])
        self.assertEqual([m["value"] for m in h["agy"]["models"]], ["fake-pro", "fake-flash"])
        self.assertEqual([m["value"] for m in h["opencode"]["models"]], ["fake/one", "fake/two"])
        self.assertEqual(h["opencode"]["modes"], ["default", "acceptEdits", "plan", "bypassPermissions"])

    def test_send_to_card(self):
        s = self.new_session()
        c = self.send(s["id"], "hello")
        self.assertEqual(c["id"], 1)
        c = self.wait_card(s["id"], 1, ("completed",))
        self.assertEqual(c["answer"], "answer: hello")
        self.assertEqual([(st["kind"], st["summary"], st["done"]) for st in c["steps"]], [("tool", "echo hi", True)])
        self.assertIsNotNone(c["finished"])
        _, d = self.req("GET", "/api/sessions")
        me = next(x for x in d["sessions"] if x["id"] == s["id"])
        self.assertGreater(me["context"], 0)

    def test_queue_resumes_one_conversation(self):
        s = self.new_session()
        for t in ("one", "two", "three"):
            self.send(s["id"], t)
        self.assertEqual(self.wait_card(s["id"], 3, ("completed",))["answer"], "answer: three")
        _, d = self.req("GET", f"/api/sessions/{s['id']}/cards")
        self.assertEqual([c["state"] for c in d["cards"]], ["completed"] * 3)
        convs = self.conversations()
        self.assertEqual(len(convs), 1, convs)  # every request went to the same harness conversation

    def test_stop_cancels_then_continues(self):
        s = self.new_session()
        self.send(s["id"], "slow")
        c = self.wait_card(s["id"], 1, ("running",))
        self.assertEqual(c["steps"][0]["kind"], "note")  # narration before the tool became a step
        self.send(s["id"], "queued behind")
        self.req("POST", f"/api/sessions/{s['id']}/stop")
        self.wait_card(s["id"], 1, ("cancelled",))
        self.assertIsNone(self.wait_card(s["id"], 2, ("cancelled",))["started"])
        # The session stays usable and keeps its conversation.
        self.send(s["id"], "after stop")
        self.assertEqual(self.wait_card(s["id"], 3, ("completed",))["answer"], "answer: after stop")
        _, d = self.req("GET", "/api/sessions")
        self.assertEqual(next(x for x in d["sessions"] if x["id"] == s["id"])["state"], "ready")
        self.assertEqual(len(self.conversations()), 1)

    def test_error(self):
        s = self.new_session()
        self.send(s["id"], "fail")
        self.assertEqual(self.wait_card(s["id"], 1, ("error",))["error"], "boom")
        self.send(s["id"], "fine")
        self.wait_card(s["id"], 2, ("completed",))

    def test_paused_then_continue(self):
        s = self.new_session()
        self.send(s["id"], "die")
        c = self.wait_card(s["id"], 1, ("paused",))
        self.assertTrue(c["error"])
        code, _ = self.req("POST", f"/api/sessions/{s['id']}/continue", {"cid": 1})
        self.assertEqual(code, 200)
        self.assertEqual(self.wait_card(s["id"], 2, ("completed",))["text"], "Continue request #1 where you stopped.")
        self.assertEqual(len(self.conversations()), 1)

    def test_died_request_retries_by_itself(self):
        """Its agent died mid-request: after a short wait the request goes on by itself (Continue, automatically)."""
        from unittest import mock
        from orbit.desk import driving
        s = self.new_session("retry")
        with mock.patch.object(driving, "RETRY_WAIT", 0.2):
            self.send(s["id"], "die")
            c = self.wait_card(s["id"], 1, ("paused",))
            self.assertIn("retrying in", c["error"])
            nxt = self.wait_card(s["id"], 2, ("completed",))
        self.assertEqual((nxt["text"], nxt.get("retry")), ("Continue request #1 where you stopped.", 1))

    def test_request_cut_off_by_a_restart_goes_on(self):
        """The server stops while a request runs: after the restart the agent is asked to go on with it."""
        s = self.new_session("cut")
        self.send(s["id"], "slow")
        self.wait_card(s["id"], 1, ("running",))
        type(self)._unserve()
        saved = {x["id"]: x for x in json.loads((self.state / "sessions.json").read_text())["sessions"]}
        self.assertTrue(saved[s["id"]].get("interrupted"))  # remembered across the restart
        type(self)._serve()
        self.desk.resume_interrupted()
        _, cards = self.req("GET", f"/api/sessions/{s['id']}/cards")
        last = cards["cards"][-1]
        self.assertIn("server restarted", last["text"])
        self.wait_card(s["id"], last["id"], ("completed",))
        self.assertFalse(self.desk.sessions[s["id"]].interrupted)

    def test_history_survives_restart(self):
        s = self.new_session("keep")
        self.send(s["id"], "remember me")
        self.wait_card(s["id"], 1, ("completed",))
        type(self)._unserve()
        type(self)._serve()
        _, d = self.req("GET", f"/api/sessions/{s['id']}/cards")
        self.assertEqual(d["cards"][0]["text"], "remember me")  # the [orbit] line is not part of the card
        self.assertEqual(d["cards"][0]["answer"], "answer: remember me")
        self.assertEqual(d["cards"][0]["steps"][0]["summary"], "echo hi")
        c = self.send(s["id"], "again")
        self.assertEqual(c["id"], 2)
        self.wait_card(s["id"], 2, ("completed",))
        _, d = self.req("GET", f"/api/sessions/{s['id']}/cards")
        self.assertEqual([c["text"] for c in d["cards"]], ["remember me", "again"])
        self.assertEqual(len(self.conversations()), 1)

    def test_clone_keeps_cards(self):
        s = self.new_session("src")
        self.send(s["id"], "before clone")
        self.wait_card(s["id"], 1, ("completed",))
        _, c = self.req("POST", f"/api/sessions/{s['id']}/clone")
        self.send(c["id"], "in the clone")
        self.wait_card(c["id"], 2, ("completed",))
        type(self)._unserve()
        type(self)._serve()
        _, d = self.req("GET", f"/api/sessions/{c['id']}/cards")
        self.assertEqual([x["text"] for x in d["cards"]], ["before clone", "in the clone"])

    def test_mode_and_model_flags(self):
        s = self.new_session(mode="plan", model="m-1")
        self.send(s["id"], "flags")
        self.wait_card(s["id"], 1, ("completed",))
        a = self.argv()[-1]
        self.assertIn("m-1", a)
        self.assertIn("read-only" if self.HARNESS == "codex" else "plan", a)

    def inputs(self):
        f = self.store / "inputs.log"
        return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []

    def setUp(self):
        self.mark = len(self.inputs())

    def conversations(self):
        """The harness conversations this test's requests went to (as the fake recorded them)."""
        return {x.get("thread") or x.get("conv") for x in self.inputs()[self.mark:]}


class CodexTest(HarnessCases, unittest.TestCase):
    """Codex on `codex app-server`: one long-lived process per session, requests as turns (JSON-RPC)."""
    HARNESS = "codex"

    def rpc(self, method=None):
        f = self.store / "rpc.log"
        rows = [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []
        return [r for r in rows if method is None or r["method"] == method]

    def pids(self, thread):
        return {x["pid"] for x in self.inputs()[self.mark:] if x.get("thread") == thread and "text" in x}

    def thread_of(self, sid):
        return self.desk.get(sid).driver.linked()

    def wait_for(self, check, timeout=10, what="it"):
        end = time.time() + timeout
        while time.time() < end:
            v = check()
            if v:
                return v
            time.sleep(0.05)
        self.fail(f"{what} never happened")

    def test_command_shape(self):
        """One `codex app-server` per session: both requests are turns on its one thread, in one process."""
        s = self.new_session(mode="bypassPermissions")
        self.send(s["id"], "one")
        self.wait_card(s["id"], 1, ("completed",))
        self.send(s["id"], "two")
        self.assertEqual(self.wait_card(s["id"], 2, ("completed",))["answer"], "answer: two")
        self.assertEqual(self.argv()[-1], ["app-server", "--listen", "stdio://"])
        th = self.thread_of(s["id"])
        self.assertTrue(th)
        self.assertEqual(len(self.pids(th)), 1)  # one spawn, two requests
        start = next(r for r in self.rpc("thread/start") if r["pid"] in self.pids(th))
        self.assertEqual((start["params"]["sandbox"], start["params"]["approvalPolicy"]), ("danger-full-access", "never"))
        self.assertEqual(len([r for r in self.rpc("turn/start") if r["params"]["threadId"] == th]), 2)

    def test_mode_and_model_flags(self):
        code, s = self.req("POST", "/api/sessions", {"cwd": self.work, "name": "flags", "harness": "codex", "mode": "plan",
                                                      "model": "m-1", "effort": "high"})
        self.assertEqual(code, 200, s)
        self.send(s["id"], "flags")
        self.wait_card(s["id"], 1, ("completed",))
        th = self.thread_of(s["id"])
        start = [r for r in self.rpc("thread/start") if r["pid"] in self.pids(th)][-1]["params"]
        self.assertEqual((start["model"], start["sandbox"], start["approvalPolicy"], start["cwd"]),
                         ("m-1", "read-only", "never", self.work))
        turn = [r for r in self.rpc("turn/start") if r["params"]["threadId"] == th][-1]["params"]
        self.assertEqual(turn["effort"], "high")
        _, d = self.req("GET", "/api/sessions")
        self.assertEqual(next(x for x in d["sessions"] if x["id"] == s["id"])["model_seen"], "m-1")  # info: the thread's model

    def test_stop_interrupts_the_turn(self):
        """Stop is turn/interrupt for the running turn; the process stays and takes the next request."""
        s = self.new_session()
        self.send(s["id"], "slow")
        self.wait_card(s["id"], 1, ("running",))
        self.req("POST", f"/api/sessions/{s['id']}/stop")
        self.wait_card(s["id"], 1, ("cancelled",))
        th = self.thread_of(s["id"])
        self.assertEqual([r["params"]["threadId"] for r in self.rpc("turn/interrupt")][-1], th)
        self.send(s["id"], "after")
        self.wait_card(s["id"], 2, ("completed",))
        self.assertEqual(len(self.pids(th)), 1)

    def test_steer_joins_the_running_turn(self):
        """A message sent mid-turn is turn/steer: the same card, no new request; history keeps it on that card."""
        s = self.new_session("steer")
        self.send(s["id"], "slow")
        self.wait_card(s["id"], 1, ("running",))
        code, r = self.req("POST", f"/api/sessions/{s['id']}/steer", {"text": "also this"})
        self.assertEqual(code, 200, r)
        self.assertTrue(r["steered"])
        c = self.wait_card(s["id"], 1, ("completed",))
        self.assertEqual(c["answer"], "steered: also this")
        self.assertEqual([x["text"] for x in c["steers"]], ["also this"])
        steer = self.rpc("turn/steer")[-1]["params"]
        self.assertEqual(steer["threadId"], self.thread_of(s["id"]))
        type(self)._unserve()
        type(self)._serve()
        _, d = self.req("GET", f"/api/sessions/{s['id']}/cards")
        self.assertEqual([x["text"] for x in d["cards"]], ["slow"])
        self.assertEqual([x["text"] for x in d["cards"][0]["steers"]], ["also this"])

    def test_subagent_outlives_its_turn(self):
        """The app-server reports subagents. One still at work when the turn ends is a background step; it reports
        later on the same process (agent_done with its last words). Rebuilt from the rollouts, the same."""
        s = self.new_session("subs")
        os.environ["FAKE_SUB_PAUSE"] = "1.5"
        try:
            self.send(s["id"], "subagents please")
            c = self.wait_card(s["id"], 1, ("completed",))
            ag = {st["agent"]: st for st in c["steps"] if st.get("agent")}
            self.assertEqual(set(ag), {"checker", "straggler"})
            self.assertEqual((ag["checker"]["done"], ag["checker"]["error"], ag["checker"]["result"]),
                             (True, False, "checker: all good"))
            self.assertEqual([x["name"] for x in ag["checker"]["sub"] if x["kind"] == "tool"], ["Bash"])
            self.assertEqual((ag["straggler"]["done"], ag["straggler"].get("background")), (False, True))  # it runs on
            self.assertEqual(ag["straggler"]["sub"][0]["summary"], "sleep 600")

            def reported():
                cc = self.req("GET", f"/api/sessions/{s['id']}/cards")[1]["cards"][0]
                st = next(x for x in cc["steps"] if x.get("agent") == "straggler")
                return st if st["done"] else None
            st = self.wait_for(reported, what="the straggler's report")
        finally:
            os.environ.pop("FAKE_SUB_PAUSE", None)
        self.assertEqual((st["error"], st.get("lost"), st["result"]), (False, None, "straggler: done at last"))
        self.assertTrue(all(x["done"] for x in st["sub"]))
        self.assertIn("keep running after your turn ends", (self.store / "inputs.log").read_text())
        drv = self.desk.get(s["id"]).driver
        hist, _ = drv._cards(drv.rollout())
        hg = {x["agent"]: x for x in hist[-1].steps if x.get("agent")}
        self.assertEqual((hg["straggler"]["result"], hg["straggler"].get("lost")), ("straggler: done at last", None))
        self.assertEqual(hg["checker"]["result"], "checker: all good")

    def test_subagent_lost_when_its_process_goes(self):
        """A background subagent still at work when the session's process ends (a restart) is marked lost."""
        s = self.new_session("lost")
        os.environ["FAKE_SUB_PAUSE"] = "60"
        try:
            self.send(s["id"], "subagents please")
            self.wait_card(s["id"], 1, ("completed",))
            self.req("POST", f"/api/sessions/{s['id']}/restart")
        finally:
            os.environ.pop("FAKE_SUB_PAUSE", None)
        c = self.req("GET", f"/api/sessions/{s['id']}/cards")[1]["cards"][0]
        st = next(x for x in c["steps"] if x.get("agent") == "straggler")
        self.assertEqual((st["done"], st["error"], st.get("lost")), (True, True, True))

    def test_resume_after_restart(self):
        """A restarted session's new process resumes its thread (thread/resume), and so does a thread the exec mode
        created (the same store)."""
        s = self.new_session("resume")
        self.send(s["id"], "first")
        self.wait_card(s["id"], 1, ("completed",))
        th = self.thread_of(s["id"])
        self.req("POST", f"/api/sessions/{s['id']}/restart")
        self.send(s["id"], "second")
        self.assertEqual(self.wait_card(s["id"], 2, ("completed",))["answer"], "answer: second")
        self.assertEqual(self.rpc("thread/resume")[-1]["params"]["threadId"], th)
        self.assertEqual(len(self.pids(th)), 2)
        self.assertEqual(self.conversations(), {th})
        os.environ["ORBIT_CODEX_EXEC"] = "1"  # a session whose thread `codex exec` started
        try:
            e = self.new_session("from exec")
            self.send(e["id"], "by exec")
            self.wait_card(e["id"], 1, ("completed",))
            self.assertEqual(self.argv()[-1][:2], ["exec", "--json"])
        finally:
            os.environ.pop("ORBIT_CODEX_EXEC")
        eth = self.thread_of(e["id"])
        self.req("POST", f"/api/sessions/{e['id']}/restart")
        self.send(e["id"], "by app-server")
        self.wait_card(e["id"], 2, ("completed",))
        self.assertEqual(self.rpc("thread/resume")[-1]["params"]["threadId"], eth)
        type(self)._unserve()
        type(self)._serve()
        _, d = self.req("GET", f"/api/sessions/{e['id']}/cards")
        self.assertEqual([x["text"] for x in d["cards"]], ["by exec", "by app-server"])

    def test_approvals_answered_by_mode(self):
        """No approval UI for Codex: the driver answers each approval at once, by the session's mode."""
        want = {"default": "cmd=decline file=decline", "acceptEdits": "cmd=decline file=accept",
                "bypassPermissions": "cmd=accept file=accept"}
        for mode, expect in want.items():
            s = self.new_session(f"approve {mode}", mode=mode)
            self.send(s["id"], "approve")
            c = self.wait_card(s["id"], 1, ("completed",))
            self.assertEqual(c["answer"], f"answer: {expect}", mode)
            self.assertEqual(c["steps"][0]["error"], mode != "bypassPermissions")  # a declined command fails
        answers = [json.loads(l) for l in (self.store / "answers.log").read_text().splitlines()]
        self.assertEqual(answers[-1]["ask"], {"answers": {}})

    def test_falls_back_to_exec(self):
        """A codex without a working `app-server` (it exits at once): the session runs on `codex exec`."""
        from orbit.drivers.codex import CodexDriver
        os.environ["FAKE_CODEX_NO_APP"] = "1"
        try:
            s = self.new_session("old codex")
            self.send(s["id"], "hello")
            self.assertEqual(self.wait_card(s["id"], 1, ("completed",))["answer"], "answer: hello")
            self.assertEqual(self.argv()[-1][:2], ["exec", "--json"])
            self.assertFalse(self.desk.get(s["id"]).driver.steers)
        finally:
            os.environ.pop("FAKE_CODEX_NO_APP")
            CodexDriver._no_app.clear()


class CodexExecTest(HarnessCases, unittest.TestCase):
    """Codex on `codex exec --json`, one process per request (ORBIT_CODEX_EXEC=1, and the fallback)."""
    HARNESS = "codex"

    @classmethod
    def setUpClass(cls):
        os.environ["ORBIT_CODEX_EXEC"] = "1"
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        os.environ.pop("ORBIT_CODEX_EXEC", None)

    def test_command_shape(self):
        s = self.new_session(mode="bypassPermissions")
        self.send(s["id"], "one")
        self.wait_card(s["id"], 1, ("completed",))
        self.send(s["id"], "two")
        self.wait_card(s["id"], 2, ("completed",))
        first, second = self.argv()[-2:]
        self.assertEqual(first[:5], ["exec", "--json", "--skip-git-repo-check", "-C", self.work])
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", first)
        self.assertEqual(first[-1], "-")
        self.assertEqual(second[-3], "resume")

    def test_subagents_from_the_rollouts(self):
        """Codex's subagents (only in its rollouts, not in `exec --json`): live, each is an Agent step with its steps;
        one that reported has its report; one still at work when the turn ends is marked ended with it. The [orbit]
        line tells Codex to wait for them. Rebuilt from the rollouts, the same."""
        s = self.new_session()
        os.environ["FAKE_SUB_PAUSE"] = "1.2"
        try:
            self.send(s["id"], "subagents please")
            end, seen = time.time() + 8, None
            while time.time() < end and not seen:  # live: the straggler at work while the request runs
                c = self.req("GET", f"/api/sessions/{s['id']}/cards")[1]["cards"][0]
                seen = next((st for st in c["steps"] if st.get("agent") == "straggler" and st.get("sub") and not st["done"]), None)
                time.sleep(0.05)
            self.assertIsNotNone(seen, "the straggler was never shown at work")
            self.assertEqual(seen["sub"][0]["name"], "Bash")
            c = self.wait_card(s["id"], 1, ("completed",))
        finally:
            os.environ.pop("FAKE_SUB_PAUSE", None)
        ag = {st["agent"]: st for st in c["steps"] if st.get("agent")}
        self.assertEqual(set(ag), {"checker", "straggler"})
        self.assertEqual((ag["checker"]["done"], ag["checker"]["error"], ag["checker"]["result"]), (True, False, "checker: all good"))
        self.assertEqual([x["name"] for x in ag["checker"]["sub"] if x["kind"] == "tool"], ["Bash"])
        self.assertEqual((ag["straggler"]["done"], ag["straggler"]["error"], ag["straggler"].get("lost")), (True, True, True))
        self.assertIn("one process per request", ag["straggler"]["result"])
        self.assertIn("wait for them (wait_agent)", (self.store / "inputs.log").read_text())
        drv = self.desk.get(s["id"]).driver
        hist, _ = drv._cards(drv.rollout())
        hg = {st["agent"]: st for st in hist[-1].steps if st.get("agent")}
        self.assertEqual(hg["checker"]["result"], "checker: all good")
        self.assertTrue(hg["straggler"].get("lost"))


class AgyTest(HarnessCases, unittest.TestCase):
    HARNESS = "agy"

    def test_a_fork_carries_the_conversation(self):
        """agy cannot fork: the fork's first request carries the conversation so far (once), which the card and the
        rebuilt history never show."""
        s = self.new_session("src")
        self.send(s["id"], "remember the word kumquat")
        self.wait_card(s["id"], 1, ("completed",))
        _, f = self.req("POST", f"/api/sessions/{s['id']}/fork")
        self.assertEqual((f["name"], f["harness"]), ("src (fork)", "agy"))
        self.send(f["id"], "what was the word?")
        self.wait_card(f["id"], 2, ("completed",))
        self.send(f["id"], "and again")
        self.wait_card(f["id"], 3, ("completed",))
        sent = [x["text"] for x in self.inputs()[self.mark:]]
        first = next(t for t in sent if "what was the word?" in t)
        self.assertIn("[orbit-fork]", first)
        self.assertIn("remember the word kumquat", first)
        self.assertIn("answer: remember the word kumquat", first)
        self.assertNotIn("[orbit-fork]", next(t for t in sent if "and again" in t))  # once only
        type(self)._unserve()
        type(self)._serve()
        _, d = self.req("GET", f"/api/sessions/{f['id']}/cards")
        self.assertEqual([x["text"] for x in d["cards"]], ["remember the word kumquat", "what was the word?", "and again"])


class OpenCodeTest(HarnessCases, unittest.TestCase):
    """OpenCode on `opencode serve`: one HTTP server per session (its own port and password), events over SSE."""
    HARNESS = "opencode"

    def http(self):
        f = self.store / "http.log"
        return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []

    def drv(self, sid):
        return self.desk.get(sid).driver

    def oc(self, sid, method, path, body=None):
        """A call to the session's OpenCode server, as its own TUI (⌘T) makes them."""
        d = self.drv(sid)
        r = urllib.request.Request(d.base + path, method=method, headers=d._headers(),
                                   data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(r, timeout=10) as resp:
            return json.loads(resp.read() or b"null")

    def session(self, sid):
        return next(x for x in self.req("GET", "/api/sessions")[1]["sessions"] if x["id"] == sid)

    def wait_for(self, check, timeout=10, what="it"):
        end = time.time() + timeout
        while time.time() < end:
            v = check()
            if v:
                return v
            time.sleep(0.05)
        self.fail(f"{what} never happened")

    def test_command_shape(self):
        """One `opencode serve` per session, password-protected: both requests go to its one OpenCode session."""
        s = self.new_session()
        self.send(s["id"], "one")
        self.wait_card(s["id"], 1, ("completed",))
        self.send(s["id"], "two")
        self.assertEqual(self.wait_card(s["id"], 2, ("completed",))["answer"], "answer: two")
        conv = self.drv(s["id"]).linked()
        self.assertTrue(conv.startswith("ses_"))
        got = [x for x in self.inputs()[self.mark:] if x["conv"] == conv]
        self.assertEqual(len({x["pid"] for x in got}), 1)  # one process, two requests
        argv = [json.loads(l) for l in (self.store / "argv.log").read_text().splitlines()]
        self.assertEqual(next(a["argv"] for a in argv if a["pid"] == got[0]["pid"]),
                         ["serve", "--hostname", "127.0.0.1", "--port", "0"])
        self.assertEqual([x["path"] for x in self.http() if x["pid"] == got[0]["pid"] and x["path"].endswith("prompt_async")],
                         [f"/session/{conv}/prompt_async"] * 2)
        self.assertTrue(self.drv(s["id"]).password)
        d = self.drv(s["id"])
        with self.assertRaises(urllib.error.HTTPError) as e:  # (no password, no entry)
            urllib.request.urlopen(d.base + "/session/status", timeout=5)
        self.assertEqual(e.exception.code, 401)

    def test_mode_and_model_flags(self):
        """The model (provider/model) and plan mode's agent go with each request; a model without a provider is refused."""
        s = self.new_session(mode="plan", model="fake/two")
        self.send(s["id"], "flags")
        self.wait_card(s["id"], 1, ("completed",))
        last = self.inputs()[-1]
        self.assertEqual((last["model"], last["agent"]), ({"providerID": "fake", "modelID": "two"}, "plan"))
        self.assertEqual(self.session(s["id"])["model_seen"], "fake/two")
        b = self.new_session(model="two")
        self.send(b["id"], "flags")
        self.assertIn("provider/model", self.wait_card(b["id"], 1, ("error",))["error"])

    def test_stop_aborts_the_session(self):
        """Stop is POST /session/:id/abort; the server stays and takes the next request."""
        s = self.new_session()
        self.send(s["id"], "slow")
        self.wait_card(s["id"], 1, ("running",))
        self.req("POST", f"/api/sessions/{s['id']}/stop")
        c = self.wait_card(s["id"], 1, ("cancelled",))
        self.assertEqual([(x["name"], x["error"]) for x in c["steps"] if x.get("kind") == "tool"], [("Bash", True)])  # (cut short)
        conv = self.drv(s["id"]).linked()
        self.assertIn(f"/session/{conv}/abort", [x["path"] for x in self.http()])
        self.send(s["id"], "after")
        self.wait_card(s["id"], 2, ("completed",))
        self.assertEqual(len({x["pid"] for x in self.inputs()[self.mark:] if x["conv"] == conv}), 1)

    def test_permissions_by_mode(self):
        """bypassPermissions answers every ask, acceptEdits the edit ones; any other waits for the user in the
        terminal (⌘T: `opencode attach`), with the session's attention mark saying what it asks."""
        for mode, text in (("bypassPermissions", "ask"), ("acceptEdits", "edit")):
            s = self.new_session(f"perm {mode}", mode=mode)
            self.send(s["id"], text)
            self.assertEqual(self.wait_card(s["id"], 1, ("completed",))["answer"], "answer: allowed", mode)
        s = self.new_session("perm ask", mode="acceptEdits")
        self.send(s["id"], "ask")
        att = self.wait_for(lambda: self.session(s["id"])["attention"], what="the attention mark")
        self.assertEqual(att, "OpenCode asks permission: bash rm -rf build")
        me = self.session(s["id"])
        self.assertTrue(me["terminal"])
        d = self.drv(s["id"])
        self.assertEqual(d.terminal[-4:], ["attach", d.base, "--session", d.conv])
        self.assertNotIn(d.password, " ".join(d.terminal))  # (ps shows argv: the password is in ⌘T's environment)
        self.assertEqual(d.terminal_env, {"OPENCODE_SERVER_PASSWORD": d.password})
        ask = self.oc(s["id"], "GET", "/permission")[0]
        self.oc(s["id"], "POST", f"/permission/{ask['id']}/reply", {"reply": "reject"})  # (answered in its TUI)
        self.assertEqual(self.wait_card(s["id"], 1, ("completed",))["answer"], "answer: refused")
        self.assertEqual(self.session(s["id"])["attention"], "")

    def test_question_waits_in_the_terminal(self):
        s = self.new_session("question", mode="bypassPermissions")
        self.send(s["id"], "question")
        self.assertEqual(self.wait_for(lambda: self.session(s["id"])["attention"], what="the attention mark"),
                         "OpenCode asks: Which color?")
        qid = list(self.drv(s["id"])._asks)[0]  # (the question's id, as its TUI shows it)
        self.oc(s["id"], "POST", f"/question/{qid}/reply", {"answers": [["blue"]]})
        self.assertEqual(self.wait_card(s["id"], 1, ("completed",))["answer"], "answer: blue")
        self.assertEqual(self.session(s["id"])["attention"], "")

    def test_subagent_under_its_step(self):
        """A task is an Agent step; its child session's steps and words go under it, its report is the result.
        Rebuilt from OpenCode's store, the same."""
        s = self.new_session("task")
        self.send(s["id"], "task")
        c = self.wait_card(s["id"], 1, ("completed",))
        self.assertEqual(c["answer"], "answer: task")
        ag = next(st for st in c["steps"] if st.get("agent"))
        self.assertEqual((ag["agent"], ag["name"], ag["summary"], ag["result"], ag["done"], ag["error"]),
                         ("checker", "Task", "check things", "checker: all good", True, False))
        self.assertEqual([(x["kind"], x.get("name") or x.get("text")) for x in ag["sub"]],
                         [("tool", "Bash"), ("note", "checker: all good")])
        h = self.drv(s["id"]).history()[-1]
        hg = next(st for st in h.steps if st.get("agent"))
        self.assertEqual((hg["result"], [x["summary"] for x in hg["sub"]]), ("checker: all good", ["make test", "checker: all good"]))

    def test_commands_and_compact(self):
        """A request naming one of OpenCode's commands runs it (POST /session/:id/command); /compact summarizes."""
        s = self.new_session("cmds")
        self.send(s["id"], "hello")
        self.wait_card(s["id"], 1, ("completed",))
        self.assertIn("review", self.session(s["id"])["commands"])
        self.send(s["id"], "/review src/app.py")
        self.assertEqual(self.wait_card(s["id"], 2, ("completed",))["answer"], "ran review: src/app.py")
        self.send(s["id"], "/compact")
        self.wait_card(s["id"], 3, ("completed",))
        self.assertEqual(self.session(s["id"])["compactions"], 1)
        conv = self.drv(s["id"]).linked()
        self.assertEqual([x["path"] for x in self.http() if x["path"].startswith(f"/session/{conv}/") and
                          x["method"] == "POST"][-2:], [f"/session/{conv}/command", f"/session/{conv}/summarize"])

    def test_resume_an_opencode_session(self):
        """OpenCode's own sessions are offered to resume (read-only from its store); attached, the history shows
        and the next request continues that session."""
        from orbit.drivers.opencode import conversations
        s = self.new_session("elsewhere")
        self.send(s["id"], "started elsewhere")
        self.wait_card(s["id"], 1, ("completed",))
        conv = self.drv(s["id"]).linked()
        row = next(x for x in conversations(50) if x["id"] == conv)
        self.assertEqual((row["harness"], row["first"], row["cwd"]), ("opencode", "started elsewhere", self.work))
        self.desk.external(50)  # (OpenCode's list is asked for in the background)
        self.wait_for(lambda: "opencode" in self.desk.external_index.rows, what="OpenCode's list")
        self.assertNotIn(conv, [x["id"] for x in self.desk.external(50)])  # (Orbit's own: linked)
        self.req("POST", f"/api/sessions/{s['id']}/close")
        a = self.desk.attach("opencode", conv, self.work, name="resumed")
        _, d = self.req("GET", f"/api/sessions/{a['id']}/cards")
        self.assertEqual([(x["text"], x["answer"]) for x in d["cards"]], [("started elsewhere", "answer: started elsewhere")])
        c = self.send(a["id"], "and on")
        self.wait_card(a["id"], c["id"], ("completed",))
        self.assertEqual(self.inputs()[-1]["conv"], conv)


if __name__ == "__main__":
    unittest.main()


class CodexNamesTest(unittest.TestCase):
    def test_codex_thread_names(self):
        """Codex's conversations to resume carry the thread's name (session_index.jsonl: the last line counts)."""
        import tempfile
        from unittest import mock
        from orbit.drivers import external
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "sessions" / "2026" / "11").mkdir(parents=True)
            rows = [{"type": "session_meta", "payload": {"id": "t-1", "cwd": "/w"}},
                    {"type": "event_msg", "payload": {"type": "user_message", "message": "tune the model"}}]
            (home / "sessions" / "2026" / "11" / "rollout-a.jsonl").write_text("\n".join(map(json.dumps, rows)) + "\n")
            (home / "session_index.jsonl").write_text("\n".join(map(json.dumps, [
                {"id": "t-1", "thread_name": "Tune"}, {"id": "t-2", "thread_name": "other"},
                {"id": "t-1", "thread_name": "nightly report"}])) + "\n")
            with mock.patch.dict(os.environ, {"CODEX_HOME": tmp, "ORBIT_CODEX_SESSIONS": str(home / "sessions")}):
                got = external.codex_sessions(10)
        self.assertEqual([(x["id"], x["first"], x["name"]) for x in got], [("t-1", "tune the model", "nightly report")])


class CodexUnrecordedRequestsTest(unittest.TestCase):
    def test_turns_whose_request_is_not_recorded(self):
        """A rollout whose requests reached only the model's input (Codex Desktop's VS Code extension: no UserMessage
        item) still shows its work: a card per turn the user's input started; a turn that goes on (a tool's output)
        stays on the card before; recorded requests are as before."""
        import tempfile
        from orbit.drivers.codex import CodexDriver, NO_REQUEST

        def ev(t, **p):
            return {"type": "event_msg", "timestamp": "2026-10-06T22:05:42Z", "payload": {"type": t, **p}}

        def item(t, **kw):
            return ev("item_completed", item={"type": t, **kw})

        def user_input(text):
            return {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}}

        rows = [{"type": "session_meta", "payload": {"id": "t-9", "cwd": "/w"}},
                ev("task_started"), user_input("<environment_context>…</environment_context>"),
                item("AgentMessage", content=[{"type": "text", "text": "first answer"}]), ev("task_complete"),
                ev("task_started"), item("FunctionCallOutput"),  # goes on: the same card
                item("AgentMessage", content=[{"type": "text", "text": "more of it"}]), ev("task_complete"),
                ev("task_started"), user_input("<environment_context>…</environment_context>"), item("FunctionCallOutput"),
                item("AgentMessage", content=[{"type": "text", "text": "second answer"}]), ev("task_complete"),
                ev("task_started"), item("UserMessage", content=[{"type": "text", "text": "a real request"}]),
                item("AgentMessage", content=[{"type": "text", "text": "third answer"}]), ev("task_complete")]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout-x-t-9.jsonl"
            path.write_text("\n".join(map(json.dumps, rows)) + "\n")
            drv = CodexDriver(session_id="s", cwd=tmp, model="", mode="default", emit=lambda e: None, resume=True)
            cards, _ = drv._cards(str(path))
        self.assertEqual([(c.text, c.answer) for c in cards], [(NO_REQUEST, "first answer\n\nmore of it"),
                                                              (NO_REQUEST, "second answer"),
                                                              ("a real request", "third answer")])
