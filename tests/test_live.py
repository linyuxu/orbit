"""GPT Live voice (builddoc/gpt-live.md), against a fake GPT Live (tests/fake_live.py) and the fake `claude`: the
session body, the sideband (replay dedupe, reattach), transcript assembly, idempotent send, corrections, results,
standby, hang-up, and the websocket client's frames. Never calls OpenAI.
"""

import json
import os
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
                     ("live_language", "xx"), ("live_detail", "long"), ("live_prompt", "x" * 4001), ("live_ack", "yes")):
            self.assertEqual(self.req("POST", "/api/settings", {k: v})[0], 400, (k, v))
        code, o = self.req("GET", "/api/live/options")
        self.assertEqual(code, 200)
        self.assertIn({"name": "harema", "about": "Japanese, feminine"}, o["voices"])
        s = self.new_session("Prev")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "earlier work"})
        self.wait_card(s["id"], 1, ("completed",))
        set_ = {"live_voice": "cedar", "live_language": "zh", "live_detail": "detailed", "live_idle_s": 0, "live_wake": 1,
                "live_context": False, "live_model": "gpt-live-1", "live_prompt": "Call me Sam.", "live_ack": True}
        self.assertEqual(self.req("POST", "/api/settings", {**set_, "live_wake": 0.7})[0], 200)
        try:
            self.req("POST", "/api/live/start", {"sid": s["id"], "sdp": "v=0 offer"})
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
        self.assertEqual(self.req("GET", "/api/live", headers=net)[0], 403)
        self.assertEqual(self.req("POST", "/api/live/start", {"sid": s["id"], "sdp": "v=0"}, headers=net)[0], 403)
        self.assertEqual(self.req("POST", "/api/live/stop", {}, headers=net)[0], 403)
        self.assertEqual(self.fake.bodies, [])

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
        self.assertEqual(com[0]["content"], "Request #1 is done. The agent's answer: \u201canswer: please check the nightly build now\u201d")
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
        ans = "The build is green.\n\n```py\nprint(1)\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nTwo tests were flaky."
        self.assertEqual(live.takeaway(ans), ("The build is green.\n\nTwo tests were flaky.", True))
        self.assertEqual(live.takeaway("Short and plain."), ("Short and plain.", False))
        long = "\n\n".join(f"Paragraph {i}. " + "word " * 120 for i in range(10))
        body, more = live.takeaway(long)
        self.assertTrue(more)
        self.assertLessEqual(live.est_tokens(body), live.APPEND_TOKENS)
        self.assertTrue(body.startswith("Paragraph 0."))
        cjk = "构建" * 2000
        self.assertLessEqual(live.est_tokens(live.takeaway(cjk)[0]), live.APPEND_TOKENS)
        self.assertEqual(live.result_text({"id": 3, "state": "error", "error": "boom"}), "Request #3 failed: boom.")
        self.assertEqual(live.result_text({"id": 3, "state": "cancelled"}), "Request #3 was stopped.")

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
        self.assertEqual([e["content"] for e in self.commentary("del_b")], ["Request #2 was stopped."])

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

    def test_progress_is_rate_limited_and_the_result_said_once(self):
        old = live.PROGRESS_EVERY_S
        live.PROGRESS_EVERY_S = 0.3
        try:
            s, _ = self.start_call("Flood")
            t0 = time.time()
            self.fake.emit(t_in("flood the output", 1000, 2000, "h1"), delegation("del_f", 2100, "hd1"))
            self.wait_card(s["id"], 1, ("completed",), timeout=30)
            took = time.time() - t0
            self.assertTrue(wait(lambda: self.commentary("del_f")))
            time.sleep(0.5)
        finally:
            live.PROGRESS_EVERY_S = old
        th = [e for e in self.fake.got("session.thinking.append") if e["delegation_id"] == "del_f"]
        progress = [e for e in th[1:] if not e["event_id"].startswith("status_")]  # (the first is the empty one; then what became of the request)
        self.assertEqual(th[0]["content"], "")
        self.assertGreaterEqual(len(progress), 1)
        self.assertLessEqual(len(progress), took / 0.3 + 1)
        self.assertTrue(all(p["content"].startswith("Request #1 is still running") for p in progress))
        com = self.commentary("del_f")
        self.assertEqual(len(com), 1)
        self.assertLessEqual(live.est_tokens(com[0]["content"]), 500)
        self.assertTrue(com[0]["content"].startswith("Request #1 is done."))

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

    def test_a_new_call_hangs_up_the_last(self):
        s1, r1 = self.start_call("One")
        s2 = self.new_session("Two")
        code, r2 = self.req("POST", "/api/live/start", {"sid": s2["id"], "sdp": "v=0 offer"})
        self.assertEqual(code, 200)
        self.assertTrue(wait(lambda: self.fake.got("session.close")))
        self.assertEqual(self.live_state()["call"], r2["call"])
        self.assertEqual(self.live_state()["sid"], s2["id"])

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
