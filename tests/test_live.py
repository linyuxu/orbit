"""GPT Live voice (builddoc/gpt-live.md), against a fake GPT Live (tests/fake_live.py) and the fake `claude`: the
session body, the sideband (replay dedupe, reattach), transcript assembly, idempotent send, corrections, results,
standby, hang-up, and the websocket client's frames. Never calls OpenAI.
"""

import json
import os
import re
import socket
import struct
import threading
import time
import unittest
import urllib.request
from pathlib import Path

import fake_live
import test_skeleton
from orbit.services import keys
from orbit.services.live import session as live
from orbit.services.live import ws as wsock


def t_in(text, a, b, eid):
    return {"type": "session.input_transcript.delta", "event_id": eid, "delta": text, "start_ms": a, "end_ms": b}


def t_out(text, a, b, eid):
    return {"type": "session.output_transcript.delta", "event_id": eid, "delta": text, "start_ms": a, "end_ms": b}


def delegation(did, offset, eid):
    return {"type": "session.delegation.created", "event_id": eid, "offset_ms": offset,
            "delegation": {"id": did, "type": "delegation", "target": "client"}}


def wait(fn, timeout=10.0, step=0.03):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return fn()


class LiveTest(test_skeleton.SkeletonTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fake = fake_live.FakeLive().start()
        cls._base, keys.OPENAI_BASE = keys.OPENAI_BASE, cls.fake.base
        keys.set_key(cls.state, "openai", fake_live.KEY)

    @classmethod
    def tearDownClass(cls):
        keys.OPENAI_BASE = cls._base
        cls.fake.stop()
        super().tearDownClass()

    def setUp(self):
        f = self.fake
        f.drop()
        with f.lock:
            f.bodies.clear(), f.received.clear(), f.history.clear()
            f.attached, f.attach_status, f.closed_reason = 0, 0, "close_requested"

    def tearDown(self):
        c = self.desk.live.call
        if c is not None and not c.ended:
            c.hang_up()

    # ------------------------------------------------------------------ helpers
    def start_call(self, name="voice"):
        s = self.new_session(name)
        code, r = self.req("POST", "/api/live/start", {"sid": s["id"], "sdp": "v=0 offer"})
        self.assertEqual(code, 200, r)
        self.assertTrue(self.fake.wait_attached(1), "the sideband never attached")
        return s, r

    def cards(self, sid):
        return self.req("GET", f"/api/sessions/{sid}/cards")[1]["cards"]

    def live_state(self):
        return self.req("GET", "/api/live")[1]

    def commentary(self, did=None):
        return [e for e in self.fake.got("session.commentary.append") if did is None or e["delegation_id"] == did]

    # ------------------------------------------------------------------ the session
    def test_session_body(self):
        s, r = self.start_call("Body")
        self.assertEqual(r["sdp"], fake_live.CANNED_SDP)
        self.assertEqual(r["live"], "live_1")
        b = self.fake.bodies[0]
        self.assertEqual(b["transport"], {"type": "webrtc", "sdp": "v=0 offer"})
        se = b["session"]
        self.assertEqual(se["model"], "gpt-live-1.1")
        self.assertEqual(se["audio"], {"output": {"voice": "marin"}})
        self.assertEqual(se["delegation"], {"type": "client", "acknowledgement_delay_ms": 800})
        self.assertEqual(se["wake_policy"], {"type": "speech", "vad_threshold": 0.5})
        self.assertEqual(se["idle_timeout_s"], 30)
        self.assertIs(se["store"], False)
        dc = se["client"]["data_channel"]
        self.assertEqual(dc["allowed_client_events"], [])  # the page can neither delegate nor add context
        self.assertEqual({x["type"] for x in dc["allowed_server_events"]},
                         {"session.started", "session.input_transcript.delta", "session.output_transcript.delta",
                          "session.paused", "session.resumed", "session.closed"})
        self.assertIn('session named "Body"', se["instructions"])
        self.assertIn("Delegation policy:", se["instructions"])
        self.assertIn("throat-clears", se["instructions"])
        self.assertEqual(se["input"][0]["role"], "developer")
        self.assertEqual(se["input"][0]["content"][0]["type"], "input_text")
        self.assertIn("observed_at", se["input"][0]["content"][0]["text"])
        self.assertNotIn("audio.format", json.dumps(se))
        self.assertNotIn(fake_live.KEY, json.dumps(self.live_state()))
        log = Path(self.state, "live", r["call"] + ".jsonl").read_text()
        self.assertIn('"stage": "created"', log)
        self.assertNotIn(fake_live.KEY, log)  # never the key

    def test_startup_context_lists_recent_and_running(self):
        s = self.new_session("Ctx")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "hello there"})
        self.wait_card(s["id"], 1, ("completed",))
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "slow job"})
        self.wait_card(s["id"], 2, ("running",))
        items = live.startup_input(self.desk, self.desk.get(s["id"]))
        text = [i["content"][0]["text"] for i in items]
        self.assertTrue(any("working on request #2" in t for t in text), text)
        self.assertIn({"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "answer: hello there"}]}, items)
        self.assertTrue(any(t.startswith("Requests of this session still running") and "#2 (running)" in t for t in text))
        self.req("POST", f"/api/sessions/{s['id']}/stop")
        self.assertIn("Generate <|SpawnThinking|>", live.instructions("x", "/tmp", "gpt-live-1"))
        self.assertNotIn("SpawnThinking", live.instructions("x", "/tmp", "gpt-live-1.1"))

    def test_voice_settings(self):
        """Settings → Voice: validated by the desk; read when a call starts (voice, language, results, standby, wake,
        context, model, the user's instructions, the spoken acknowledgement)."""
        for k, v in (("live_voice", "nobody"), ("live_idle_s", 3), ("live_idle_s", 61), ("live_wake", 0.4),
                     ("live_language", "xx"), ("live_detail", "long"), ("live_prompt", "x" * 4001), ("live_ack", "yes"),
                     ("live_chime", 1)):
            self.assertEqual(self.req("POST", "/api/settings", {k: v})[0], 400, (k, v))
        code, o = self.req("GET", "/api/live/options")
        self.assertEqual(code, 200)
        self.assertIn({"name": "harema", "about": "Japanese, feminine"}, o["voices"])
        s = self.new_session("Prev")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "earlier work"})
        self.wait_card(s["id"], 1, ("completed",))
        set_ = {"live_voice": "cedar", "live_language": "zh", "live_detail": "detailed", "live_idle_s": 0, "live_wake": 1,
                "live_context": False, "live_model": "gpt-live-1", "live_prompt": "Call me Sam.", "live_ack": True,
                "live_chime": False}
        self.assertEqual(self.req("POST", "/api/settings", {**set_, "live_wake": 0.7})[0], 200)
        try:
            r = self.req("POST", "/api/live/start", {"sid": s["id"], "sdp": "v=0 offer"})[1]
            self.assertIs(r["chime"], False)  # (the page's sounds: off)
            self.assertTrue(self.fake.wait_attached(1))
            se = self.fake.bodies[0]["session"]
            self.assertEqual((se["model"], se["audio"], se["wake_policy"]["vad_threshold"]),
                             ("gpt-live-1", {"output": {"voice": "cedar"}}, 0.7))
            self.assertNotIn("idle_timeout_s", se)  # never standby: active until hang-up
            self.assertEqual(len(se["input"]), 1)  # the session's facts only, not its recent requests
            ins = se["instructions"]
            self.assertIn("Always speak Mandarin Chinese", ins)
            self.assertIn("key details", ins)
            self.assertTrue(ins.endswith("Call me Sam."))
            self.fake.emit(t_in("check the build", 500, 1500, "a1"), delegation("del_a", 1600, "da"))
            self.wait_card(s["id"], 2, ("completed",))
            self.fake.wait_got("session.commentary.append")
            self.assertEqual([e for e in self.fake.got("session.thinking.append") if e.get("content") == ""], [])  # it may say "on it"
        finally:
            self.req("POST", "/api/settings", {k: live.config.DEFAULTS[k] for k in set_})
        self.assertEqual(self.req("POST", "/api/settings", {"live_wake": 1})[0], 400)  # (an int for a float: 1.0, not a choice)

    def test_voice_preview(self):
        """GET /api/live/preview: a short WebSocket-transport session where the voice greets (its line in its own
        language, or the chosen one), kept as a WAV and served from the cache after; this machine only."""
        import wave
        code, _ = self.req("GET", "/api/live/preview?voice=nobody")
        self.assertEqual(code, 400)
        n0 = len(self.fake.ws_sessions)
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/live/preview?voice=harema") as r:
            data = r.read()
            self.assertIn("audio/", r.headers["Content-Type"])
        start = self.fake.ws_sessions[n0]["session"]
        self.assertEqual(start["audio"], {"format": {"type": "audio/pcm", "rate": 24000}, "output": {"voice": "harema"}})
        self.assertIn("こんにちは、Haremaです", start["input"][0]["content"][0]["text"])  # (a Japanese voice: Japanese)
        self.assertTrue(any(e.get("type") == "session.input_audio.append" for e in self.fake.ws_received))  # the input runs
        f = Path(self.state, "live", "voices", "harema-ja.wav")
        with wave.open(str(f)) as w:
            self.assertEqual((w.getframerate(), w.getnchannels(), w.getnframes()), (24000, 1, 7200))
        self.assertEqual(data, f.read_bytes())
        urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/live/preview?voice=harema").read()
        self.assertEqual(len(self.fake.ws_sessions), n0 + 1)  # (kept: made once)
        self.req("POST", "/api/settings", {"live_language": "zh"})
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/live/preview?voice=marin").read()
            self.assertIn("你好，我是Marin", self.fake.ws_sessions[-1]["session"]["input"][0]["content"][0]["text"])
        finally:
            self.req("POST", "/api/settings", {"live_language": "auto"})
        from orbit.machines import remote
        net = {"Host": f"100.64.0.9:{self.port}", "Authorization": f"Bearer {remote.token(self.state)}"}
        self.assertEqual(self.req("GET", "/api/live/preview?voice=marin", headers=net)[0], 403)

    def test_usage_and_balance(self):
        """What calls cost, from their logs: OpenAI's active/standby seconds by the second; a call that ended without
        usage counts its length as active; last month's calls are not this month's; a balance the user gave counts
        down by the calls since (and saying it again starts again)."""
        import tempfile
        from datetime import datetime
        from orbit.services.live import usage
        with tempfile.TemporaryDirectory() as d:
            live_dir = Path(d, "live"); live_dir.mkdir()
            now = datetime(2026, 11, 23, 12).timestamp()
            def log(name, t, rows):
                Path(live_dir, f"call-{name}.jsonl").write_text("".join(json.dumps({"t": t + r.get("ms", 0) / 1000, **r}) + "\n" for r in rows))
            log("a", now - 3600, [{"ms": 0, "stage": "create"}, {"ms": 600000, "stage": "ended", "usage": {"active_seconds": 300, "paused_seconds": 300}}])
            log("b", now - 1800, [{"ms": 0, "stage": "create"}, {"ms": 90000, "stage": "ended", "usage": {"seconds": 90}}])
            log("c", now - 900, [{"ms": 0, "stage": "create"}, {"ms": 60000, "stage": "ended", "usage": {}}])  # (no usage: its length)
            log("d", datetime(2026, 10, 30).timestamp(), [{"ms": 0, "stage": "create"}, {"ms": 600000, "stage": "ended", "usage": {"active_seconds": 600}}])
            log("e", now - 60, [{"ms": 0, "stage": "create"}, {"ms": 200, "stage": "create_failed"}])  # (never made: free)
            u = usage.summary(Path(d), {}, now=now)
            self.assertEqual(u["month"], {"calls": 3, "active_s": 450, "standby_s": 300, "cost": round(450 / 60 * .05 + 300 / 60 * .02, 4), "estimated": True})
            self.assertNotIn("budget", u)
            b = usage.summary(Path(d), {"live_budget": 10.0, "live_budget_at": now - 2000}, now=now)["budget"]  # (calls b and c since)
            self.assertEqual((b["spent"], b["left"]), (0.125, 9.875))
            self.assertEqual((b["active_min"], b["standby_min"]), (197.5, 493.8))
        before = time.time()
        self.assertEqual(self.req("POST", "/api/settings", {"live_budget": 20})[0], 200)  # (an int is taken as a float)
        try:
            self.assertGreaterEqual(self.desk.settings["live_budget_at"], before)
            code, u = self.req("GET", "/api/live/usage")
            self.assertEqual((code, u["budget"]["given"]), (200, 20.0))
            self.assertEqual(self.req("POST", "/api/settings", {"live_budget": -1})[0], 400)
        finally:
            self.req("POST", "/api/settings", {"live_budget": 0})

    def test_no_key_and_another_machine(self):
        s = self.new_session("Nokey")
        keys.set_key(self.state, "openai", None)
        try:
            code, r = self.req("POST", "/api/live/start", {"sid": s["id"], "sdp": "v=0"})
            self.assertEqual((code, r.get("need")), (400, "openai-key"))
            self.assertIn("Settings → Voice", r["error"])
        finally:
            keys.set_key(self.state, "openai", fake_live.KEY)
        from orbit.machines import remote
        net = {"Host": f"100.64.0.9:{self.port}", "Authorization": f"Bearer {remote.token(self.state)}"}
        # the phone (another device with the token) may hold a call; the key never goes to it; without the token, no
        self.assertEqual(self.req("GET", "/api/live", headers=net)[0], 200)
        code, r = self.req("POST", "/api/live/start", {"sid": s["id"], "sdp": "v=0 phone"}, headers=net)
        self.assertEqual(code, 200, r)
        self.assertEqual(r["sdp"], fake_live.CANNED_SDP)
        self.assertNotIn(fake_live.KEY, json.dumps(r))
        self.assertEqual(self.req("POST", "/api/live/stop", {}, headers=net)[0], 200)
        bare = {"Host": f"100.64.0.9:{self.port}"}
        self.assertEqual(self.req("POST", "/api/live/start", {"sid": s["id"], "sdp": "v=0"}, headers=bare)[0], 401)
        self.assertEqual(self.req("GET", "/api/keys", headers=net)[0], 403)  # (the key itself: this machine only)
        self.assertEqual(len(self.fake.bodies), 1)  # (the phone's call only)

    def test_several_keys_one_refused_the_next(self):
        """Keys tried in order: one refused (401) and the call goes on with the next (its sideband attaches with that
        one); the refused key is then tried last for a while, and Settings shows how each last did, never the key."""
        bad = "sk-proj-" + "b" * 40 + "DEAD"
        keys.set_key(self.state, "openai", None)
        try:
            keys.edit_keys(self.state, "openai", "add", value=bad, label_="Old project")
            st = keys.edit_keys(self.state, "openai", "add", value=fake_live.KEY, label_="Personal")
            self.assertEqual([k["name"] for k in st["openai"]["keys"]], ["Old project", "Personal"])
            s, r = self.start_call("Keys")  # (attached: with the key that made it, not the first)
            c = self.desk.live.call
            self.assertEqual(c.key_label, "Personal")
            self.assertEqual(len(self.fake.bodies), 1)
            log = [json.loads(x) for x in (self.state / "live" / f"{c.id}.jsonl").read_text().splitlines()]
            fail = next(x for x in log if x["stage"] == "create_failed")
            self.assertEqual((fail["status"], fail["key"]), (401, "Old project"))
            self.assertNotIn(bad, json.dumps(log))
            self.req("POST", "/api/live/stop", {})
            code, k = self.req("GET", "/api/keys")
            ks = k["keys"]["openai"]["keys"]
            self.assertEqual([(x["name"], x["last"]["ok"]) for x in ks], [("Old project", False), ("Personal", True)])
            self.assertIn("401", ks[0]["last"]["error"])
            self.assertNotIn(bad, json.dumps(k))
            self.assertEqual(keys.get(self.state, "openai"), fake_live.KEY)  # (the refused one: after the others now)
            # All refused: one error that says so
            keys.edit_keys(self.state, "openai", "remove", ks[1]["id"])
            code, r = self.req("POST", "/api/live/start", {"sid": s["id"], "sdp": "v=0"})
            self.assertNotEqual(code, 200)
            self.assertIn("401", r["error"])
        finally:
            keys.set_key(self.state, "openai", None)
            keys.set_key(self.state, "openai", fake_live.KEY)

    def test_key_list_edits(self):
        """add (named, last), update (name), move, remove, check by id; an older file's one key reads as a list."""
        import tempfile
        from pathlib import Path
        st = Path(tempfile.mkdtemp())
        (st / "secrets").mkdir()
        (st / "secrets" / "_orbit.json").write_text(json.dumps({"openai": fake_live.KEY}))
        k = keys.status(st)["openai"]
        self.assertEqual((k["set"], len(k["keys"]), k["keys"][0]["name"]), (True, 1, ""))
        a = "sk-" + "a" * 30
        k = keys.edit_keys(st, "openai", "add", value=a, label_="  Work   key ")["openai"]
        self.assertEqual([x["name"] for x in k["keys"]], ["", "Work key"])
        with self.assertRaises(ValueError):
            keys.edit_keys(st, "openai", "add", value=a)  # (once)
        with self.assertRaises(ValueError):
            keys.edit_keys(st, "openai", "add", value="nope")
        k2 = k["keys"][1]["id"]
        keys.edit_keys(st, "openai", "move", k2, to=0)
        self.assertEqual(keys.get(st, "openai"), a)
        keys.edit_keys(st, "openai", "update", k2, label_="Team")
        self.assertEqual(keys.status(st)["openai"]["keys"][0]["name"], "Team")
        keys.edit_keys(st, "openai", "remove", k2)
        self.assertEqual(keys.get(st, "openai"), fake_live.KEY)
        self.assertEqual(json.loads((st / "secrets" / "_orbit.json").read_text())["openai"][0]["key"], fake_live.KEY)
        self.assertEqual(oct((st / "secrets" / "_orbit.json").stat().st_mode & 0o777), "0o600")
        with self.assertRaises(ValueError):
            keys.edit_keys(st, "openai", "remove", "nope")
        # over HTTP (this machine): ops and check by id
        code, r = self.req("POST", "/api/keys", {"name": "openai", "op": "update", "id": "nope", "label": "x"})
        self.assertEqual(code, 400)
        kid = keys.status(self.state)["openai"]["keys"][0]["id"]
        code, r = self.req("POST", "/api/keys/check", {"name": "openai", "id": kid})
        self.assertEqual(code, 200)  # (this fake has no /v1/models: it is that key that was asked)
        self.assertNotEqual(r.get("error"), "no such key")
        self.assertEqual(self.req("POST", "/api/keys/check", {"name": "openai", "id": "nope"})[1], {"ok": False, "error": "no such key"})

    # ------------------------------------------------------------------ transcripts and delegations
    def test_delegation_makes_one_card_from_the_transcript(self):
        """Fragments out of order, one straddling the delegation's offset, one late (in the grace window), one after
        it (the next request's); a repeated delegation; the empty thinking append at once; the answer as one commentary."""
        s, r = self.start_call("Deleg")
        self.fake.emit(t_in("nightly build", 1400, 2000, "e2"), t_in("check the ", 1000, 1400, "e1"),
                       t_in(" now", 2100, 2400, "e3"), delegation("del_1", 2200, "ed1"))
        time.sleep(0.1)
        self.fake.emit(t_in("please ", 600, 1000, "e0"), t_in("Also, ", 2600, 3000, "e4"),
                       delegation("del_1", 2200, "ed1b"))  # (the same delegation again: nothing new)
        quiet = self.fake.wait_got("session.thinking.append")
        self.assertEqual(quiet[0]["content"], "")
        self.assertEqual(quiet[0]["delegation_id"], "del_1")
        self.assertTrue(quiet[0]["_masked"])
        c = self.wait_card(s["id"], 1, ("completed",))
        self.assertEqual(c["text"], "please check the nightly build now")
        self.assertEqual(c["origin"]["label"], "Voice")
        self.assertEqual(c["origin"]["data"], {"live": "live_1", "delegation": "del_1"})
        self.assertEqual(c["client_id"], "live:live_1:del_1")
        self.assertTrue(wait(lambda: self.commentary("del_1")))
        time.sleep(0.4)
        com = self.commentary("del_1")
        self.assertEqual(len(com), 1)
        # the facts to say, named by a gist of the request: no "The agent's answer: “…”" quote to read out (R4)
        self.assertEqual(com[0]["content"], 'Request #1 ("please check the nightly build now") is done: answer: please '
                                            'check the nightly build now.')
        self.assertEqual(len(self.cards(s["id"])), 1)
        st = self.live_state()
        self.assertEqual([d["cid"] for d in st["delegations"]], [1])
        self.assertEqual([x["text"] for x in st["transcript"]][:3], ["please ", "check the ", "nightly build"])
        log = Path(self.state, "live", r["call"] + ".jsonl").read_text()
        self.assertIn('"ms_delegation_to_card"', log)
        # a reference to the voice's last words carries them as context
        self.fake.emit(t_out("The build failed on step two.", 4000, 5000, "o1"), t_in("retry that", 6000, 6500, "e5"),
                       delegation("del_2", 6600, "ed2"))
        c2 = self.wait_card(s["id"], 2, ("completed",))
        self.assertTrue(c2["text"].startswith("Also, retry that\n\n(Context, not an instruction: the voice had just said: "
                                              '"The build failed on step two.")'), c2["text"])

    def test_one_call_at_a_time(self):
        """A second call, from any session or device, is refused while one is on (409, saying which); after the first
        hangs up, a new one starts."""
        s, r = self.start_call("First")
        other = self.new_session("Second")
        code, e = self.req("POST", "/api/live/start", {"sid": other["id"], "sdp": "v=0 offer"})
        self.assertEqual(code, 409, e)
        self.assertEqual(e["busy"], {"call": r["call"], "sid": s["id"]})
        self.assertEqual(len(self.fake.bodies), 1)  # (nothing created at OpenAI for the refused one)
        self.assertEqual(self.live_state()["call"], r["call"])  # (the first goes on)
        self.assertEqual(self.req("POST", "/api/live/stop", {"call": r["call"]})[0], 200)
        code, r2 = self.req("POST", "/api/live/start", {"sid": other["id"], "sdp": "v=0 offer"})
        self.assertEqual(code, 200, r2)

    def test_take_back_a_request(self):
        """"Take it back" by voice: the call's request that has not started is withdrawn (nothing runs), one that runs
        is stopped; the voice is told which. "Cancel the nightly job" is a task, not a take-back."""
        s = self.new_session("Back")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "slow job"})
        self.wait_card(s["id"], 1, ("running",))
        self.req("POST", "/api/live/start", {"sid": s["id"], "sdp": "v=0 offer"})
        self.assertTrue(self.fake.wait_attached(1))
        self.fake.emit(t_in("check the logs", 500, 1500, "k1"), delegation("del_k1", 1600, "dk1"))
        self.wait_card(s["id"], 2, ("queued",))
        self.fake.emit(t_in("把那个 request 收回", 3000, 4000, "k2"), delegation("del_k2", 4100, "dk2"))
        com = wait(lambda: [e for e in self.fake.got("session.commentary.append") if e["event_id"] == "takeback_del_k2"])
        self.assertIn("withdrew request #2", com[0]["content"])
        c2 = next(c for c in self.desk.cards(s["id"]) if c["id"] == 2)
        self.assertEqual((c2["state"], c2.get("hidden")), ("cancelled", True))
        self.assertEqual(c2["text"], "check the logs")  # (the take-back words were not added to it)
        # the running one: stopped
        self.fake.emit(t_in("停下", 6000, 6500, "k3"), delegation("del_k3", 6600, "dk3"))
        com = wait(lambda: [e for e in self.fake.got("session.commentary.append") if e["event_id"] == "takeback_del_k3"])
        self.assertIn("stopped request #1", com[0]["content"])
        self.wait_card(s["id"], 1, ("cancelled",))
        ins = self.fake.bodies[-1]["session"]["instructions"]
        self.assertIn("- The user wants to stop, add to, withdraw or cancel a request.", ins)
        self.assertIn("to stop the running request, add something to it while it runs, or take back one that has not "
                      "started, delegate the user's words: Orbit does it and tells you which.", ins)

    def test_take_back_and_steer_phrases(self):
        """What is a take-back, a move-in or a steer cue, and what stays a task for the agent."""
        for w in ("撤回", "把那个 request 收回", "停下", "算了", "cancel", "停止当前在跑的任务", "现在正在运行的停掉",
                  "stop the running task", "stop the current one now"):
            self.assertTrue(live.takes_back(w), w)
        for w in ("取消那个定时任务", "stop the server", "撤回上一个commit", "cancel the nightly job",
                  "stop running the tests now", "停止运行测试服务器"):
            self.assertFalse(live.takes_back(w), w)
        for w in ("插进去", "把它插进去吧", "放到当前任务里", "steer it in", "add it to the running one"):
            self.assertTrue(live.moves_in(w), w)
        self.assertFalse(live.moves_in("插进去一个新功能"))
        for w, add in (("插一句，只看错误日志", "只看错误日志"), ("tell it to skip the slow tests", "skip the slow tests"),
                       ("Also tell it: use the staging config", "use the staging config"), ("顺便看看测试", "看看测试"),
                       ("add this to the running task: and the docs", "and the docs"), ("补充", ""),
                       ("steering wheel", ""), ("telling it apart", ""), ("check the logs", "")):
            self.assertEqual(live.steer_words(w), add, w)

    def test_stop_the_running_request_by_name(self):
        """"停止当前在跑的任务" while the call's own request waits: the running one (typed, not the call's) stops; the
        waiting one stays and runs next."""
        s = self.new_session("StopRun")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "slow job"})
        self.wait_card(s["id"], 1, ("running",))
        self.req("POST", "/api/live/start", {"sid": s["id"], "sdp": "v=0 offer"})
        self.assertTrue(self.fake.wait_attached(1))
        self.fake.emit(t_in("check the logs", 500, 1500, "sr1"), delegation("del_sr1", 1600, "dsr1"))
        self.wait_card(s["id"], 2, ("queued",))
        self.fake.emit(t_in("停止当前在跑的任务", 3000, 4000, "sr2"), delegation("del_sr2", 4100, "dsr2"))
        com = wait(lambda: [e for e in self.fake.got("session.commentary.append") if e["event_id"] == "takeback_del_sr2"])
        self.assertIn("stopped request #1", com[0]["content"])
        self.wait_card(s["id"], 1, ("cancelled",))
        c2 = self.wait_card(s["id"], 2, ("completed",))
        self.assertFalse(c2.get("hidden"))

    def test_steer_cue_into_a_request_the_call_did_not_make(self):
        """"插一句 …" with a typed request running: the words (without the cue) join it; the voice is told so."""
        s = self.new_session("SteerIn")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "slow job"})
        self.wait_card(s["id"], 1, ("running",))
        self.req("POST", "/api/live/start", {"sid": s["id"], "sdp": "v=0 offer"})
        self.assertTrue(self.fake.wait_attached(1))
        self.fake.emit(t_in("插一句，只看错误日志", 500, 1500, "si1"), delegation("del_si", 1600, "dsi"))
        c = wait(lambda: (lambda c: c if c.get("steers") else None)(self.cards(s["id"])[0]))
        self.assertEqual(c["steers"][0]["text"], "只看错误日志")
        self.assertEqual(len(self.cards(s["id"])), 1)
        self.assertEqual([(d["cid"], d["how"]) for d in self.live_state()["delegations"]], [(1, "steer")])
        st = wait(lambda: [e for e in self.fake.got("session.thinking.append") if e["event_id"] == "status_del_si"])
        self.assertIn("added these words to request #1 while the agent works on it", st[0]["content"])
        self.assertTrue(wait(lambda: self.commentary("del_si")))  # (its result now answers these words)
        self.assertIn("steered: 只看错误日志", self.commentary("del_si")[0]["content"])

    def test_steer_cue_with_nothing_running_is_a_request(self):
        s, _ = self.start_call("SteerIdle")
        self.fake.emit(t_in("tell it to check the docs", 500, 1500, "sn1"), delegation("del_sn", 1600, "dsn"))
        c = self.wait_card(s["id"], 1, ("completed",))
        self.assertEqual(c["text"], "check the docs")
        self.assertEqual([d["how"] for d in self.live_state()["delegations"]], ["new"])

    def test_steer_cue_when_the_agent_cannot_take_additions(self):
        """A harness that cannot steer: the words are queued as a request, and the voice is told why."""
        s = self.new_session("NoSteer")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "slow job"})
        self.wait_card(s["id"], 1, ("running",))
        from orbit.desk.session import DRIVERS
        cls = DRIVERS[self.desk.get(s["id"]).harness]
        cls.steers = False  # (as a harness without mid-request messages; put back below)
        try:
            self.req("POST", "/api/live/start", {"sid": s["id"], "sdp": "v=0 offer"})
            self.assertTrue(self.fake.wait_attached(1))
            self.fake.emit(t_in("tell it to skip the docs", 500, 1500, "ns1"), delegation("del_ns", 1600, "dns"))
            c2 = self.wait_card(s["id"], 2, ("queued",))
            self.assertEqual(c2["text"], "skip the docs")
            st = wait(lambda: [e for e in self.fake.got("session.thinking.append") if e["event_id"] == "status_del_ns"])
            self.assertIn("This agent can't take additions while a request runs, so Orbit queued these words as "
                          "request #2; it waits: the agent is busy with request #1", st[0]["content"])
            self.assertEqual(self.cards(s["id"])[0].get("steers"), None)
        finally:
            cls.steers = True
            self.req("POST", f"/api/sessions/{s['id']}/stop")

    def test_move_a_waiting_request_into_the_running_one(self):
        """"插进去": the call's waiting request is withdrawn and its words join the running one."""
        s = self.new_session("MoveIn")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "slow job"})
        self.wait_card(s["id"], 1, ("running",))
        self.req("POST", "/api/live/start", {"sid": s["id"], "sdp": "v=0 offer"})
        self.assertTrue(self.fake.wait_attached(1))
        self.fake.emit(t_in("check the logs", 500, 1500, "mi1"), delegation("del_mi1", 1600, "dmi1"))
        self.wait_card(s["id"], 2, ("queued",))
        self.fake.emit(t_in("插进去", 3000, 3600, "mi2"), delegation("del_mi2", 3700, "dmi2"))
        com = wait(lambda: [e for e in self.fake.got("session.commentary.append") if e["event_id"] == "movein_del_mi2"])
        self.assertIn("took back request #2 and added its words to request #1", com[0]["content"])
        c2 = next(c for c in self.desk.cards(s["id"]) if c["id"] == 2)
        self.assertEqual((c2["state"], c2.get("hidden")), ("cancelled", True))
        c1 = wait(lambda: (lambda c: c if c.get("steers") else None)(self.cards(s["id"])[0]))
        self.assertEqual(c1["steers"][0]["text"], "check the logs")
        self.assertEqual([d["how"] for d in self.live_state()["delegations"]], ["new", "steer"])
        self.req("POST", f"/api/sessions/{s['id']}/stop")

    def test_do_this_first(self):
        """"打断一下，先…" by voice: the running request stops, the new one goes ahead of the waiting ones and runs at
        once, and the waiting one keeps its turn after it; the voice is told."""
        s = self.new_session("First")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "slow job"})
        self.wait_card(s["id"], 1, ("running",))
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "next thing"})
        self.req("POST", "/api/live/start", {"sid": s["id"], "sdp": "v=0 offer"})
        self.assertTrue(self.fake.wait_attached(1))
        self.fake.emit(t_in("打断一下，先看一下日志", 500, 2000, "u1"), delegation("del_u", 2100, "du"))
        st = wait(lambda: [e for e in self.fake.got("session.thinking.append") if e["event_id"] == "status_del_u"])
        self.assertIn("Orbit stopped request #1 and put this first, as request #3", st[0]["content"])
        self.wait_card(s["id"], 1, ("cancelled",))
        c3 = self.wait_card(s["id"], 3, ("completed",))
        self.assertEqual(c3["text"], "先看一下日志")
        c2 = self.wait_card(s["id"], 2, ("completed",))
        self.assertGreaterEqual(c2["started"], c3["finished"])  # (the waiting one after it, not dropped)
        self.assertEqual([c["id"] for c in self.desk.cards(s["id"])][:3], [1, 3, 2])  # (the queue's order: the cards')
        self.assertIn("a new request first", self.fake.bodies[-1]["session"]["instructions"])

    def test_status_in_an_idle_session(self):
        """A request in an idle session is "queued" only for the moment before its agent takes it: the voice is told it
        runs now, never that it waits (a real call said "it is queued behind another request" with nothing running)."""
        s, r = self.start_call("Idle")
        self.fake.emit(t_in("check the logs", 500, 1500, "i1"), delegation("del_i", 1600, "di"))
        st = wait(lambda: [e for e in self.fake.got("session.thinking.append") if e["event_id"] == "status_del_i"])
        self.assertEqual(st[0]["content"], "Orbit sent this as request #1; the agent started on it. Don't state the "
                                           "answer until the result arrives.")  # ("nothing else runs before it" was read back)

    def test_status_while_the_agent_is_busy(self):
        """A request that waits behind another: the voice is told (silently) that it is queued and why; more words
        while it waits join it, and the voice is told it is one request, not several."""
        s = self.new_session("Busy")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "slow job"})
        self.wait_card(s["id"], 1, ("running",))
        self.req("POST", "/api/live/start", {"sid": s["id"], "sdp": "v=0 offer"})
        self.assertTrue(self.fake.wait_attached(1))
        self.fake.emit(t_in("check the logs", 500, 1500, "b1"), delegation("del_q", 1600, "dq"))
        st = wait(lambda: [e for e in self.fake.got("session.thinking.append") if e["event_id"] == "status_del_q"])
        self.assertIn("request #2; it waits: the agent is busy with request #1", st[0]["content"])
        self.assertNotIn("Tell the user", st[0]["content"])  # (#1 has run for a moment only: no wait worth saying)
        self.fake.emit(t_in("and the build", 2000, 2800, "b2"), delegation("del_q2", 2900, "dq2"))
        st = wait(lambda: [e for e in self.fake.got("session.thinking.append") if e["event_id"] == "status_del_q2"])
        self.assertIn("added these words to request #2, which has not started", st[0]["content"])
        self.assertIn("one request, not several", st[0]["content"])
        self.assertEqual(self.cards(s["id"])[1]["text"], "check the logs\n\nand the build")
        self.req("POST", f"/api/sessions/{s['id']}/stop")

    def test_assemble(self):
        F = lambda key, who, text, a, b, seq: {"key": key, "who": who, "text": text, "start": a, "end": b, "seq": seq}
        frags = [F("b", "user", "world", 500, 900, 1), F("a", "user", "hello ", 100, 500, 2),
                 F("v", "voice", "sure", 300, 600, 3), F("c", "user", "!", 950, 1300, 4), F("d", "user", " next", 1400, 1600, 5)]
        used = set()
        self.assertEqual(live.assemble(frags, 1000, 0, used), ("hello world!", ["a", "b", "c"]))  # c straddles 1000
        used |= {"a", "b", "c"}
        self.assertEqual(live.assemble(frags, 2000, 1000, used), ("next", ["d"]))
        self.assertEqual(live.assemble(frags, 2000, 1500, set())[0], "next")  # what ended before the last cut stays out
        self.assertEqual(live.last_reply(frags + [F("w", "voice", " ok", 3000, 3100, 6)], 3500), "ok")
        self.assertEqual([w for w, _, _ in live.turns(frags)], ["user", "voice", "user"])

    def test_takeaway(self):
        """An answer for speaking: code and tables left out, Markdown marks gone, whole paragraphs within the spoken
        limit, the next part as silent detail."""
        ans = "The build is green.\n\n```py\nprint(1)\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nTwo tests were flaky."
        self.assertEqual(live.takeaway(ans), ("The build is green. Two tests were flaky.", "", True))
        self.assertEqual(live.takeaway("Short and plain."), ("Short and plain.", "", False))
        long = "\n\n".join(f"Paragraph {i}. " + "word " * 120 for i in range(10))
        say, detail, more = live.takeaway(long)
        self.assertTrue(more)
        self.assertLessEqual(live.est_tokens(say), live.SPEAK_TOKENS["brief"])
        self.assertLessEqual(live.est_tokens(detail), live.DETAIL_TOKENS)
        self.assertTrue(say.startswith("Paragraph 0."))
        self.assertTrue(detail)  # (the rest of paragraph 0, cut, goes on silently)
        cjk = "构建" * 2000
        self.assertLessEqual(live.est_tokens(live.takeaway(cjk)[0]), live.SPEAK_TOKENS["brief"])
        self.assertEqual(live.result_text({"id": 3, "state": "error", "error": "boom"}), "Request #3 failed: boom.")
        self.assertEqual(live.result_text({"id": 3, "state": "cancelled"}), "Request #3 was stopped.")

    def test_result_text_is_facts_to_say(self):
        """R4: the request named by a gist, Markdown stripped, no quote framing, the spoken part within ~100 tokens
        (brief) or ~200 (detailed), a question it ends with named, the next ~300 tokens as silent detail."""
        md = ("## Summary\n\nThe build is **green** and [CI](http://ci.example) passed.\n\n- three commits landed\n"
              "- one test is *flaky*\n- `orbit/x.py` changed\n\n```py\nprint(1)\n```\n\n| a | b |\n|---|---|\n\n"
              "Should I fix the flaky test?")
        c = {"id": 7, "state": "completed", "text": "[orbit] voice\ncheck the nightly build please\n\n(Context: …)", "answer": md}
        t = live.result_text(c)
        self.assertEqual(t, 'Request #7 ("check the nightly build please") is done: The build is green and CI passed. '
                            "three commits landed; one test is flaky; orbit/x.py changed. Should I fix the flaky test? "
                            "The full answer is in Orbit.")
        for bad in ("**", "##", "- ", "`", "](", "\u201c", "The agent's answer"):
            self.assertNotIn(bad, t)
        self.assertEqual(live.result_detail(c), "")  # (all of it was said)
        paras = [f"Point {i}: " + "detail " * 40 + "end." for i in range(8)]
        long = {"id": 8, "state": "completed", "text": "summarise the release " + "notes " * 40,
                "answer": "\n\n".join(paras) + "\n\nShall I tag the release?"}
        brief, detailed = live.result_text(long), live.result_text(long, "detailed")
        self.assertTrue(brief.startswith('Request #8 ("summarise the release notes notes'), brief)
        self.assertIn("…\")", brief)  # (the gist is cut)
        self.assertLessEqual(live.est_tokens(brief), live.SPEAK_TOKENS["brief"] + 50)
        self.assertGreater(len(detailed), len(brief))
        self.assertLessEqual(live.est_tokens(detailed), live.SPEAK_TOKENS["detailed"] + 50)
        self.assertIn("It ends by asking: Shall I tag the release?", brief)
        more = live.result_detail(long)
        self.assertTrue(more.startswith("More of request #8's answer, context for follow-up questions (do not read it out): Point "))
        self.assertNotIn(paras[0], more)  # (what is said is not repeated)
        self.assertLessEqual(live.est_tokens(more), live.DETAIL_TOKENS + 25)
        self.assertEqual(live.result_detail({"id": 9, "state": "error", "error": "x"}), "")
        zh = live.plain("## 结论\n\n- 构建通过\n- 两个测试不稳定\n\n要我修一下吗？")[0]
        self.assertEqual(zh, ["构建通过；两个测试不稳定。", "要我修一下吗？"])

    def test_instructions(self):
        """Status questions are answered from the latest Session state, not delegated (R1); background facts come
        silently (R3); results are authoritative, nothing invented (R4); auto language: Simplified Chinese (R8)."""
        ins = live.instructions("x", "/tmp", "gpt-live-1.1")
        self.assertIn("- The user asks about status, progress, what is running or queued, or how long something has "
                      "taken: answer at once from the latest Session state. Give elapsed times; never invent an estimate.", ins)
        self.assertIn("- The answer needs the session's files, tools or history.", ins)
        self.assertNotIn("current state", ins)
        self.assertIn("Use a previous result when it still answers the question", ins)
        self.assertIn("Orbit sends you background facts (session state, progress) silently. Do not announce them", ins)
        self.assertIn("Results and Orbit's facts are authoritative", ins)
        self.assertIn("Never invent facts, names, numbers or details that are not in a result", ins)  # (a real call made up a README)
        self.assertNotIn("say only what it says", ins)
        self.assertIn("for Chinese, speak Mandarin and use Simplified characters", ins)
        zh = live.instructions("x", "/tmp", "gpt-live-1.1", {**live.config.DEFAULTS, "live_language": "zh"})
        self.assertIn("Always speak Mandarin Chinese (Simplified)", zh)
        self.assertNotIn("for Chinese, speak Mandarin", zh)

    def test_correction_edits_the_queued_card(self):
        s, _ = self.start_call("Fix")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "slow first"})
        self.wait_card(s["id"], 1, ("running",))
        self.fake.emit(t_in("Check the staging build", 1000, 2000, "f1"), delegation("del_a", 2100, "fd1"))
        c = self.wait_card(s["id"], 2, ("queued",))
        self.assertEqual(c["text"], "Check the staging build")
        self.fake.emit(t_in("no, production", 2500, 3000, "f2"), delegation("del_b", 3100, "fd2"))
        self.assertTrue(wait(lambda: "production" in self.cards(s["id"])[1]["text"]))
        cs = self.cards(s["id"])
        self.assertEqual(len(cs), 2)  # no second card
        self.assertEqual(cs[1]["text"], "Check the staging build\n\nno, production")
        st = self.live_state()
        self.assertEqual([(d["cid"], d["how"]) for d in st["delegations"]], [(2, "new"), (2, "edit")])
        self.req("POST", f"/api/sessions/{s['id']}/stop")  # (the queued one is cancelled with it)
        self.assertTrue(wait(lambda: self.commentary("del_b")))
        time.sleep(0.3)
        self.assertEqual(self.commentary("del_a"), [])  # the result answers the latest delegation, once
        self.assertEqual([e["content"] for e in self.commentary("del_b")], ['Request #2 ("Check the staging build") was stopped.'])

    def test_correction_steers_the_running_card(self):
        s, _ = self.start_call("Steer")
        self.fake.emit(t_in("slow check of the logs", 1000, 2000, "g1"), delegation("del_s1", 2100, "gd1"))
        self.wait_card(s["id"], 1, ("running",))
        self.fake.emit(t_in("only the errors", 2500, 3000, "g2"), delegation("del_s2", 3100, "gd2"))
        c = wait(lambda: (lambda c: c if c.get("steers") else None)(self.cards(s["id"])[0]))
        self.assertEqual(c["steers"][0]["text"], "only the errors")
        self.assertEqual(len(self.cards(s["id"])), 1)
        self.assertEqual([d["how"] for d in self.live_state()["delegations"]], ["new", "steer"])
        self.req("POST", f"/api/sessions/{s['id']}/stop")

    def test_no_progress_lines_and_the_result_said_once(self):
        """A request with many steps: no per-step lines on its delegation (the old 10 s "still running: Bash …" feed
        made the voice narrate); the session's state goes out as a few snapshots (start, end; a change of step only
        every STEP_EVERY_S), never a tool command; the result once."""
        old = live.COALESCE_S
        live.COALESCE_S = 0.3
        try:
            s, _ = self.start_call("Flood")
            self.fake.emit(t_in("flood the output", 1000, 2000, "h1"), delegation("del_f", 2100, "hd1"))
            self.wait_card(s["id"], 1, ("completed",), timeout=30)
            self.assertTrue(wait(lambda: self.commentary("del_f")))
            time.sleep(0.8)
        finally:
            live.COALESCE_S = old
        th = self.fake.got("session.thinking.append")
        mine = [e for e in th if e["delegation_id"] == "del_f"]
        self.assertEqual([e["event_id"] for e in mine], ["quiet_del_f", "status_del_f"])
        snaps = [e for e in th if e["delegation_id"] is None]
        self.assertGreaterEqual(len(snaps), 1)
        self.assertLessEqual(len(snaps), 3)  # (40 rounds of steps: not one each)
        for e in snaps:
            self.assertTrue(e["content"].startswith('Session state at '), e["content"])
            self.assertNotIn("echo", e["content"])
            self.assertNotIn("Nothing to say", e["content"])
            self.assertLessEqual(live.est_tokens(e["content"]), live.SNAPSHOT_TOKENS)
        self.assertIn('Last finished: request #1 ("flood the output")', snaps[-1]["content"])
        com = self.commentary("del_f")
        self.assertEqual(len(com), 1)
        self.assertLessEqual(live.est_tokens(com[0]["content"]), 500)
        self.assertTrue(com[0]["content"].startswith('Request #1 ("flood the output") is done: flooded.'))

    def test_state_snapshot_for_cards_the_call_did_not_make(self):
        """R1: a request typed in the session (not by the call) reaches the voice as session state: what runs, for how
        long, the agent's latest words (not its tool command); stopped, it is the last finished. Nothing is sent at
        the start (the startup input has it)."""
        old = live.COALESCE_S
        live.COALESCE_S = 0.3
        try:
            s, _ = self.start_call("Typed")
            time.sleep(0.6)
            self.assertEqual(self.fake.got("session.thinking.append"), [])
            self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "slow job"})
            snap = wait(lambda: [e for e in self.fake.got("session.thinking.append")
                                 if "its latest words" in e["content"]])
            self.assertTrue(snap, self.fake.got("session.thinking.append"))
            t = snap[-1]["content"]
            self.assertEqual(snap[-1]["delegation_id"], None)
            self.assertIn('"Typed" is working on request #1 ("slow job") for ', t)
            self.assertIn(': "Working on it"', t)
            self.assertNotIn("sleep", t)  # (its Bash command)
            n = len(self.fake.got("session.thinking.append"))
            self.req("POST", f"/api/sessions/{s['id']}/stop")
            done = wait(lambda: [e for e in self.fake.got("session.thinking.append")[n:] if "Last finished" in e["content"]])
            self.assertIn('is idle: nothing running or queued. Last finished: request #1 ("slow job")', done[-1]["content"])
            self.assertIn(", stopped.", done[-1]["content"])
            self.assertEqual(self.commentary(), [])  # (not this call's request: no result spoken)
        finally:
            live.COALESCE_S = old

    def test_state_snapshot_is_coalesced_and_unchanged_skipped(self):
        """A quick request's burst of card changes (queued, thinking, running, completed) is one snapshot; the same
        state again (a card event with nothing new) sends none."""
        s = self.new_session("Burst")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "warm up"})  # (the agent started: the next is quick)
        self.wait_card(s["id"], 1, ("completed",))
        self.req("POST", "/api/live/start", {"sid": s["id"], "sdp": "v=0 offer"})
        self.assertTrue(self.fake.wait_attached(1))
        events = []
        q = self.desk.subscribe()
        try:
            self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "hello there"})
            self.wait_card(s["id"], 2, ("completed",))
            snaps = wait(lambda: self.fake.got("session.thinking.append"), timeout=5)
            time.sleep(live.COALESCE_S + 0.6)
            while not q.empty():
                events.append(q.get())
        finally:
            self.desk.unsubscribe(q)
        snaps = self.fake.got("session.thinking.append")
        self.assertGreaterEqual(sum('"t": "card"' in e for e in events), 3)
        self.assertEqual(len(snaps), 1, [e["content"] for e in snaps])
        self.assertIn('Last finished: request #2 ("hello there") at ', snaps[0]["content"])
        self.assertIn(', done: "answer: hello there".', snaps[0]["content"])
        c = next(c for c in self.desk.cards(s["id"]) if c["id"] == 2)
        with self.desk.lock:
            self.desk._card_changed(next(x for x in self.desk.get(s["id"]).cards if x["id"] == 2))  # (the same, again)
        time.sleep(live.COALESCE_S + 0.6)
        self.assertEqual(len(self.fake.got("session.thinking.append")), 1)
        self.assertEqual(c["state"], "completed")

    def test_state_snapshot_unit(self):
        now = time.time()
        cards = [{"id": 1, "state": "completed", "text": "hello", "answer": "**Done.** All good.\n\nmore", "finished": now - 300},
                 {"id": 2, "state": "running", "text": "slow job", "started": now - 372,
                  "steps": [{"kind": "note", "text": "Running the tests now."},
                            {"kind": "tool", "name": "Bash", "summary": "sed -n 1,2p x", "t0": now - 100}]},
                 {"id": 3, "state": "queued", "text": "next thing"}, {"id": 4, "state": "queued", "text": "x", "hidden": True},
                 {"id": 4.001, "kind": "note", "state": "note", "text": "Voice call"}]
        t, big, step = live.state_snapshot("IDE", cards, now)
        self.assertIn('"IDE" is working on request #2 ("slow job") for 6 min 12 s, 1 step so far; its latest words '
                      '(1 min 40 s ago): "Running the tests now". Queued after it: #3 ("next thing"). Last finished: '
                      'request #1 ("hello") at ', t)
        self.assertTrue(t.endswith(', done: "Done. All good".'), t)
        self.assertNotIn("sed", t)
        self.assertEqual((big, step), ((2, (3,), (1, "completed")), (2, 1, "Running the tests now")))
        t2, big2, step2 = live.state_snapshot("IDE", cards, now + 50)
        self.assertEqual((big2, step2), (big, step))  # (only the clock moved: unchanged)
        self.assertTrue(live.state_snapshot("IDE", [], now)[0].endswith('"IDE" is idle: nothing running or queued.'))
        failed = live.state_snapshot("IDE", [{"id": 5, "state": "error", "text": "build", "error": "boom", "finished": now}], now)[0]
        self.assertIn('Last finished: request #5 ("build") at ', failed)
        self.assertTrue(failed.endswith(", failed: boom."))

    def test_result_waits_while_the_user_talks(self):
        """R2: a result that comes while the user is speaking waits until they pause (then goes out), or HOLD_MAX_S."""
        s, r = self.start_call("Hold")
        talking, n = threading.Event(), [0]

        def talk():
            while talking.is_set():
                n[0] += 1
                self.fake.emit(t_in("and another thing ", 5000 + n[0] * 200, 5200 + n[0] * 200, f"talk{n[0]}"))
                time.sleep(0.2)

        talking.set()
        threading.Thread(target=talk, daemon=True).start()
        try:
            self.fake.emit(t_in("check the build", 500, 1500, "w1"), delegation("del_w", 1600, "dw"))
            self.wait_card(s["id"], 1, ("completed",))
            time.sleep(1.0)
            self.assertEqual(self.commentary("del_w"), [])  # (held: the user is talking)
        finally:
            talking.clear()
        quiet = time.time()
        self.assertTrue(wait(lambda: self.commentary("del_w"), timeout=4))
        self.assertGreater(time.time() - quiet, 0.5)  # (after a pause of USER_HOLD_S)
        log = [json.loads(x) for x in Path(self.state, "live", r["call"] + ".jsonl").read_text().splitlines()]
        self.assertTrue(any(x["stage"] == "held" for x in log))
        self.assertGreater(next(x for x in log if x["stage"] == "result")["held_ms"], 900)
        # talking on and on: the result goes out after HOLD_MAX_S anyway
        old = live.HOLD_MAX_S
        live.HOLD_MAX_S = 1.0
        talking.set()
        threading.Thread(target=talk, daemon=True).start()
        try:
            time.sleep(0.5)
            self.fake.emit(delegation("del_w2", 5000 + n[0] * 200, "dw2"))  # (its words: the talk so far)
            self.wait_card(s["id"], 2, ("completed",))
            self.assertTrue(wait(lambda: self.commentary("del_w2"), timeout=4))
            self.assertTrue(talking.is_set())
        finally:
            talking.clear()
            live.HOLD_MAX_S = old

    def test_result_detail_goes_silently_with_it(self):
        """R4: a long answer: the spoken part in the commentary, the next part as a silent thinking append on the same
        delegation, sent just before it."""
        s, _ = self.start_call("Detail")
        words = " ".join(f"Sentence number {i} about the build." for i in range(60))
        self.fake.emit(t_in(words, 500, 9000, "l1"), delegation("del_l", 9100, "dl"))
        self.wait_card(s["id"], 1, ("completed",))
        com = wait(lambda: self.commentary("del_l"))
        self.assertLessEqual(live.est_tokens(com[0]["content"]), live.SPEAK_TOKENS["brief"] + 40)
        self.assertIn("is done: answer: Sentence number 0 about the build.", com[0]["content"])
        got = self.fake.got()
        k = next(i for i, e in enumerate(got) if e.get("event_id") == "result_del_l")
        det = got[k - 1]
        self.assertEqual((det["type"], det["event_id"], det["delegation_id"]), ("session.thinking.append", "detail_del_l", "del_l"))
        self.assertIn("(do not read it out)", det["content"])
        last_said = re.findall(r"Sentence number (\d+)", com[0]["content"])[-1]
        self.assertIn(f"Sentence number {int(last_said) + 1} about", det["content"])

    def test_empty_delegation_sends_nothing(self):
        s, _ = self.start_call("Empty")
        self.fake.emit(delegation("del_e", 500, "ee1"))
        com = wait(lambda: self.commentary("del_e"))
        self.assertIn("no words", com[0]["content"])
        self.assertEqual(self.cards(s["id"]), [])

    # ------------------------------------------------------------------ the sideband
    def test_reattach_after_a_drop_and_replay_dedupe(self):
        s, r = self.start_call("Drop")
        self.fake.emit(t_in("one ", 100, 300, "r1"), t_out("hi", 200, 400, "r2"))
        self.assertTrue(wait(lambda: len(self.live_state()["transcript"]) == 2))
        self.fake.drop()
        self.fake.emit(t_in("two", 300, 500, "r3"))  # while it is away: replayed on the next attach
        self.assertTrue(self.fake.wait_attached(2))
        self.assertTrue(wait(lambda: len(self.live_state()["transcript"]) == 3))
        time.sleep(0.3)
        st = self.live_state()
        self.assertEqual([x["id"] for x in st["transcript"]], ["r1", "r2", "r3"])  # r1, r2 replayed: not twice
        self.assertEqual(st["phase"] in ("listening", "speaking"), True)
        log = Path(self.state, "live", r["call"] + ".jsonl").read_text()
        self.assertIn('"stage": "replayed"', log)
        self.assertIn('"stage": "reattach"', log)
        # the session gone while away (404 on attach): the call ends
        self.fake.attach_status = 404
        self.fake.drop()
        self.assertTrue(wait(lambda: self.live_state()["phase"] == "ended"))

    def test_standby_events(self):
        self.start_call("Standby")
        self.assertTrue(wait(lambda: self.live_state()["phase"] == "listening"))
        self.fake.emit({"type": "session.paused", "event_id": "p1"})
        self.assertTrue(wait(lambda: self.live_state()["phase"] == "standby"))
        self.fake.emit({"type": "session.resumed", "event_id": "p2"})
        self.assertTrue(wait(lambda: self.live_state()["phase"] == "listening"))
        self.fake.emit(t_out("Hello.", 100, 600, "p3"))
        self.assertTrue(wait(lambda: self.live_state()["phase"] == "speaking"))
        self.assertTrue(wait(lambda: self.live_state()["phase"] == "listening", timeout=4))

    def test_hang_up_adds_the_note_card(self):
        s, r = self.start_call("Hang")
        self.fake.emit(t_in("what time is it", 1000, 2000, "k1"), t_out("It's three.", 2500, 3000, "k2"),
                       t_in("thanks", 3500, 3800, "k3"))
        self.assertTrue(wait(lambda: len(self.live_state()["transcript"]) == 3))
        code, st = self.req("POST", "/api/live/stop", {"call": r["call"]})
        self.assertEqual((code, st["phase"]), (200, "ended"))
        self.assertEqual(len(self.fake.got("session.close")), 1)
        self.assertEqual(self.desk.live.call.reason, "close_requested")
        cs = self.cards(s["id"])
        self.assertEqual(len(cs), 1)
        n = cs[0]
        self.assertEqual((n["kind"], n["state"]), ("note", "note"))
        self.assertTrue(n["text"].startswith("Voice call · "))
        self.assertIn("**You** · 0:01  \nwhat time is it", n["answer"])
        self.assertIn("**Voice** · 0:02  \nIt's three.", n["answer"])
        self.assertIn("No requests from this call.", n["answer"])
        self.assertTrue(wait(lambda: self.fake.conn is None))  # the sideband closed
        self.assertEqual(self.req("POST", "/api/live/stop", {"call": "nope"})[0], 404)

    def test_closed_over_there_ends_the_call(self):
        s, _ = self.start_call("Expire")
        self.fake.emit(t_in("hello", 100, 400, "x1"))
        self.assertTrue(wait(lambda: self.live_state()["transcript"]))
        self.fake.emit({"type": "session.closed", "event_id": "x2", "reason": "expired",
                        "session": {"id": "live_1", "status": "active"}, "usage": {"seconds": 3600}})
        self.assertTrue(wait(lambda: self.live_state()["phase"] == "ended"))
        self.assertEqual(self.live_state()["reason"], "expired")
        self.assertIn("time ran out", self.cards(s["id"])[0]["answer"])
        self.assertEqual(self.fake.got("session.close"), [])

    # ------------------------------------------------------------------ Orbit: client_id, note cards
    def test_client_id_send_is_idempotent_across_a_restart(self):
        s = self.new_session("Idem")
        sid = s["id"]
        code, c1 = self.req("POST", f"/api/sessions/{sid}/send", {"text": "hello", "client_id": "abc-1"})
        self.assertEqual((code, c1["id"], c1["client_id"]), (200, 1, "abc-1"))
        code, c2 = self.req("POST", f"/api/sessions/{sid}/send", {"text": "hello again", "client_id": "abc-1"})
        self.assertEqual((code, c2["id"], c2["duplicate"]), (200, 1, True))
        self.assertEqual(self.req("POST", f"/api/sessions/{sid}/send", {"text": "x", "client_id": ""})[0], 400)
        self.wait_card(sid, 1, ("completed",))
        self.desk.add_note(sid, "Voice call · 10:00 · 1 min", "**You** · 0:01  \nhi")
        code, c3 = self.req("POST", f"/api/sessions/{sid}/send", {"text": "two", "client_id": "abc-2"})
        self.assertEqual(c3["id"], 2)  # (the note card is not counted)
        self.wait_card(sid, 2, ("completed",))
        self._unserve()
        self._serve()
        cs = self.cards(sid)
        self.assertEqual([c["id"] for c in cs], [1, 1.001, 2])
        self.assertEqual(cs[1]["kind"], "note")
        self.assertEqual((cs[0].get("client_id"), cs[2].get("client_id")), ("abc-1", "abc-2"))
        code, again = self.req("POST", f"/api/sessions/{sid}/send", {"text": "hello", "client_id": "abc-1"})
        self.assertEqual((again["id"], again["duplicate"]), (1, True))
        self.assertEqual(len(self.cards(sid)), 3)


class WebSocketClientTest(unittest.TestCase):
    """orbit/services/live/ws.py against a scripted server (frames checked byte by byte)."""

    def serve(self, script):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        self.got = {}

        def run():
            conn, _ = srv.accept()
            raw = b""
            while b"\r\n\r\n" not in raw:
                raw += conn.recv(4096)
            head = raw.split(b"\r\n\r\n")[0].decode()
            hs = {k.lower(): v.strip() for k, _, v in (h.partition(":") for h in head.split("\r\n")[1:])}
            self.got["path"], self.got["headers"] = head.split(" ")[1], hs
            status = script.get("status", 101)
            if status != 101:
                body = b'{"error": "no such session"}'
                conn.sendall(f"HTTP/1.1 {status} Nope\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
                conn.close()
                return
            conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                          f"Sec-WebSocket-Accept: {wsock.accept_key(hs['sec-websocket-key'])}\r\n\r\n").encode())
            script["run"](conn)
            conn.close()

        t = threading.Thread(target=run, daemon=True)
        t.start()
        self.addCleanup(srv.close)
        return srv.getsockname()[1], t

    def test_handshake_masking_fragments_ping_close(self):
        frames = []

        def run(conn):
            frames.append(fake_live.read_client_frame(conn))  # a text message from the client
            frames.append(fake_live.read_client_frame(conn))  # a long one (16-bit length)
            frames.append(fake_live.read_client_frame(conn))  # a ping
            conn.sendall(fake_live.server_frame(0x1, b"Hel", fin=False) + fake_live.server_frame(0x9, b"pp")
                         + fake_live.server_frame(0x0, "lo ✓".encode(), fin=True))
            frames.append(fake_live.read_client_frame(conn))  # the pong for "pp"
            conn.sendall(fake_live.server_frame(0x2, b"\x00" * 70000))  # 64-bit length, binary
            conn.sendall(fake_live.server_frame(0x8, struct.pack(">H", 1000) + b"bye"))
            frames.append(fake_live.read_client_frame(conn))  # the close answered

        port, t = self.serve({"run": run})
        w = wsock.connect(f"ws://127.0.0.1:{port}/v1/live/sessions/live_9/attach", headers={"Authorization": "Bearer sk-x"})
        self.assertEqual(self.got["path"], "/v1/live/sessions/live_9/attach")
        self.assertEqual(self.got["headers"]["authorization"], "Bearer sk-x")
        self.assertEqual(self.got["headers"]["sec-websocket-version"], "13")
        w.send('{"type": "session.close"}')
        w.send("x" * 300)
        w.ping(b"hi")
        self.assertEqual(w.recv(), "Hello ✓")  # put together around the ping
        self.assertEqual(w.recv(), b"\x00" * 70000)
        with self.assertRaises(wsock.Closed) as cm:
            w.recv()
        self.assertEqual((cm.exception.code, cm.exception.reason), (1000, "bye"))
        t.join(5)
        (f1, op1, d1, m1), (f2, op2, d2, m2), (_, op3, d3, m3), (_, op4, d4, m4), (_, op5, d5, m5) = frames
        self.assertEqual((f1, op1, d1, m1), (True, 1, b'{"type": "session.close"}', True))  # masked, unmasked right
        self.assertEqual((op2, len(d2), m2), (1, 300, True))
        self.assertEqual((op3, d3, m3), (0x9, b"hi", True))
        self.assertEqual((op4, d4, m4), (0xA, b"pp", True))
        self.assertEqual((op5, d5[:2], m5), (0x8, struct.pack(">H", 1000), True))
        with self.assertRaises(wsock.Closed):
            w.send("after close")

    def test_mask_is_applied(self):
        key = b"\x01\x02\x03\x04"
        f = wsock.frame(0x1, b"abcdef", key=key)
        self.assertEqual(f[:2], bytes([0x81, 0x80 | 6]))
        self.assertEqual(f[2:6], key)
        self.assertEqual(bytes(c ^ key[i % 4] for i, c in enumerate(f[6:])), b"abcdef")
        self.assertEqual(wsock.frame(0x2, b"x" * 200)[1:4], bytes([126]) + struct.pack(">H", 200))

    def test_refused_handshake(self):
        port, t = self.serve({"status": 404, "run": None})
        with self.assertRaises(wsock.HandshakeError) as cm:
            wsock.connect(f"ws://127.0.0.1:{port}/x")
        self.assertEqual(cm.exception.status, 404)
        self.assertIn("no such session", cm.exception.body)

    def test_urls(self):
        old = keys.OPENAI_BASE
        try:
            keys.OPENAI_BASE = "https://api.openai.com"
            self.assertEqual(live.ws_base(), "wss://api.openai.com")
            keys.OPENAI_BASE = "http://127.0.0.1:9/"
            self.assertEqual((live.http_base(), live.ws_base()), ("http://127.0.0.1:9", "ws://127.0.0.1:9"))
        finally:
            keys.OPENAI_BASE = old


# Do not run the inherited skeleton tests twice.
for name in [n for n in dir(test_skeleton.SkeletonTest) if n.startswith("test_")]:
    if name not in LiveTest.__dict__:
        setattr(LiveTest, name, None)

if __name__ == "__main__":
    unittest.main()
