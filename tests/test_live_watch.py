"""The watch's voice call (builddoc/gpt-live.md §12; orbit/server/watch.py): this server holds the GPT Live session
over its WebSocket transport (tests/fake_live.py) and relays the audio to and from a client over plain HTTP: start,
PCM uploads on one kept-alive connection, a framed down stream (the voice's audio, silence left out; the call's
events), a delegation becoming a card, hang-up into a note card, errors, the token from another machine.
Never calls OpenAI.
"""

import base64
import http.client
import json
import struct
import threading
import time
import unittest
from pathlib import Path

import fake_live
import test_skeleton
from orbit.machines import remote
from orbit.services import keys

RATE = 24000


def pcm(samples, value=0):
    return struct.pack(f"<{samples}h", *([value] * samples))


def wait(fn, timeout=10.0, step=0.03):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return fn()


class Down:
    """A watch's down stream: GET …/down read frame by frame in a thread."""

    def __init__(self, port, call, headers=None):
        self.frames, self.done, self.status = [], threading.Event(), 0
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        self.conn.request("GET", f"/api/live/watch/{call}/down", headers=headers or {})
        r = self.conn.getresponse()
        self.status = r.status
        if r.status != 200:
            r.read()
            self.done.set()
            return
        threading.Thread(target=self._read, args=(r,), daemon=True).start()

    def _read(self, r):
        try:
            while True:
                head = r.read(5)
                if len(head) < 5:
                    break
                n = struct.unpack(">I", head[1:])[0]
                body = r.read(n)
                self.frames.append((head[:1], body if head[:1] == b"a" else json.loads(body)))
        except (OSError, ValueError):
            pass
        self.done.set()

    def events(self, kind=None):
        return [f for t, f in self.frames if t == b"j" and (kind is None or f.get("kind") == kind)]

    def audio(self):
        return [f for t, f in self.frames if t == b"a"]


class LiveWatchTest(test_skeleton.SkeletonTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fake = fake_live.FakeLive().start()
        cls._base, keys.OPENAI_BASE = keys.OPENAI_BASE, cls.fake.base
        keys.set_key(cls.state, "openai", fake_live.KEY)
        cls.desk.set_settings({"live_ack": False})

    @classmethod
    def tearDownClass(cls):
        keys.OPENAI_BASE = cls._base
        cls.fake.stop()
        super().tearDownClass()

    def setUp(self):
        f = self.fake
        f.drop()
        with f.lock:
            f.bodies.clear(), f.received.clear(), f.history.clear(), f.ws_sessions.clear()
            f.attached, f.audio_in, f.audio_chunks, f.start_error = 0, 0, 0, ""

    def tearDown(self):
        c = self.desk.live.call
        if c is not None and not c.ended:
            c.hang_up()

    def start(self, name="watch"):
        s = self.new_session(name)
        code, r = self.req("POST", "/api/live/watch/start", {"sid": s["id"]})
        self.assertEqual(code, 200, r)
        return s, r

    # ------------------------------------------------------------------ the call
    def test_a_call_end_to_end(self):
        s, r = self.start("Wrist")
        self.assertEqual((r["rate"], r["format"], r["live"]), (24000, "pcm16", "live_ws_1"))
        se = self.fake.ws_sessions[-1]["session"]
        self.assertEqual(se["audio"], {"format": {"type": "audio/pcm", "rate": 24000}, "output": {"voice": "marin"}})
        for refused in ("wake_policy", "idle_timeout_s", "client"):  # (the WebSocket transport refuses / has none)
            self.assertNotIn(refused, se)
        self.assertEqual(se["delegation"], {"type": "client", "acknowledgement_delay_ms": 800})
        self.assertIn('session named "Wrist"', se["instructions"])
        st = self.req("GET", "/api/live")[1]
        self.assertEqual((st["call"], st["transport"], st["phase"]), (r["call"], "watch", "listening"))

        down = Down(self.port, r["call"])
        self.assertEqual(down.status, 200)
        self.assertTrue(wait(lambda: down.events("state")))
        self.assertEqual(down.events("state")[0]["state"]["phase"], "listening")

        up = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)  # (one connection: kept alive)
        socks = set()
        for _ in range(5):
            up.request("POST", f"/api/live/watch/{r['call']}/up", body=pcm(2400, 300),
                       headers={"Content-Type": "application/octet-stream"})
            res = up.getresponse()
            res.read()
            self.assertEqual(res.status, 204)
            socks.add(id(up.sock))
        self.assertEqual(len(socks), 1, "the uploads did not keep their connection")
        self.assertTrue(wait(lambda: self.fake.audio_in >= 5 * 4800))

        # The voice's audio: a loud chunk reaches the watch, a silent one does not travel.
        self.fake.emit({"type": "session.output_audio.delta", "delta": base64.b64encode(pcm(2400, 0)).decode()},
                       {"type": "session.output_audio.delta", "delta": base64.b64encode(pcm(2400, 1000)).decode()})
        self.assertTrue(wait(lambda: down.audio()))
        time.sleep(0.2)
        self.assertEqual([len(a) for a in down.audio()], [4800])
        self.assertEqual(struct.unpack("<h", down.audio()[0][:2])[0], 1000)

        # The words and the delegation: a card in the bound session, the captions on the watch's stream.
        self.fake.emit({"type": "session.input_transcript.delta", "event_id": "w1", "delta": "list the files",
                        "start_ms": 100, "end_ms": 900},
                       {"type": "session.delegation.created", "event_id": "w2", "offset_ms": 1000,
                        "delegation": {"id": "del_w", "type": "delegation", "target": "client"}})
        cards = wait(lambda: [c for c in self.req("GET", f"/api/sessions/{s['id']}/cards")[1]["cards"] if c.get("origin")])
        self.assertEqual(cards[0]["origin"]["label"], "Voice")
        self.assertIn("list the files", cards[0]["text"])
        self.assertTrue(wait(lambda: down.events("caption")))
        self.assertEqual(down.events("caption")[0]["caption"]["text"], "list the files")
        self.assertTrue(wait(lambda: down.events("delegation")))
        self.assertTrue(wait(lambda: self.fake.got("session.commentary.append")), "the result was not said")

        code, out = self.req("POST", f"/api/live/watch/{r['call']}/stop")
        self.assertEqual((code, out["phase"]), (200, "ended"))
        self.assertTrue(down.done.wait(5), "the down stream did not end with the call")
        self.assertEqual(down.events("state")[-1]["state"]["phase"], "ended")
        self.assertTrue(self.fake.got("session.close"))
        note = [c for c in self.req("GET", f"/api/sessions/{s['id']}/cards")[1]["cards"] if c.get("kind") == "note"]
        self.assertEqual(len(note), 1)
        log = Path(self.state, "live", r["call"] + ".jsonl").read_text()
        self.assertNotIn(fake_live.KEY, log)
        self.assertNotIn("input_audio", log)  # (audio is never logged: counted)
        self.assertIn('"up_bytes": 24000', log)
        up.close()

    def test_silence_goes_in_while_the_watch_sends_nothing(self):
        _, r = self.start()
        self.assertTrue(wait(lambda: self.fake.audio_in > 0, timeout=3), "no silence went in")

    def test_the_connection_lost_ends_the_call(self):
        s, r = self.start()
        down = Down(self.port, r["call"])
        self.fake.drop()
        self.assertTrue(down.done.wait(5))
        st = self.req("GET", "/api/live")[1]
        self.assertEqual((st["phase"], st["reason"]), ("ended", "connection_lost"))
        self.assertEqual(self.req("POST", f"/api/live/watch/{r['call']}/up")[0], 404)

    # ------------------------------------------------------------------ errors
    def test_errors(self):
        self.assertEqual(self.req("POST", "/api/live/watch/start", {"sid": "nope"})[0], 404)
        self.assertEqual(self.req("POST", "/api/live/watch/call-0123456789/up")[0], 404)
        self.assertEqual(self.req("GET", "/api/live/watch/call-0123456789/down")[0], 404)
        self.assertEqual(self.req("POST", "/api/live/watch/call-0123456789/stop")[0], 404)
        self.assertEqual(self.req("POST", "/api/live/watch/whatever")[0], 404)
        s, r = self.start()
        code, e = self.req("POST", "/api/live/watch/start", {"sid": s["id"]})
        self.assertEqual((code, e["busy"]["call"]), (409, r["call"]))  # (one call at a time, a page's or the watch's)
        big = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        big.request("POST", f"/api/live/watch/{r['call']}/up", body=b"\0" * (300 * 1024))
        self.assertEqual(big.getresponse().status, 413)
        big.close()
        self.req("POST", f"/api/live/watch/{r['call']}/stop")
        # a page's call is not the watch's: its routes do not take it
        s2 = self.new_session("page")
        code, page = self.req("POST", "/api/live/start", {"sid": s2["id"], "sdp": "v=0 offer"})
        self.assertEqual(code, 200, page)
        self.assertEqual(self.req("POST", f"/api/live/watch/{page['call']}/up")[0], 404)
        self.desk.live.call.hang_up()

    def test_refused_at_the_start(self):
        s = self.new_session("refused")
        self.fake.start_error = "model not available"
        code, e = self.req("POST", "/api/live/watch/start", {"sid": s["id"]})
        self.assertEqual(code, 502, e)
        self.assertIn("model not available", e["error"])
        self.fake.start_error = ""
        self.assertEqual(self.req("POST", "/api/live/watch/start", {"sid": s["id"]})[0], 200)  # (not left busy)

    def test_from_another_machine_with_the_token(self):
        s = self.new_session("remote")
        h = self.srv.RequestHandlerClass
        h.trust_loopback = False  # every client counts as another machine
        try:
            tok = remote.token(self.state)
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            conn.request("POST", "/api/live/watch/start", body=json.dumps({"sid": s["id"]}), headers={"Content-Type": "application/json"})
            self.assertEqual(conn.getresponse().status, 401)
            conn.close()
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            conn.request("POST", "/api/live/watch/start", body=json.dumps({"sid": s["id"]}),
                         headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
            res = conn.getresponse()
            r = json.loads(res.read())
            self.assertEqual(res.status, 200, r)
            down = Down(self.port, r["call"], {"Authorization": f"Bearer {tok}"})
            self.assertEqual(down.status, 200)
            self.assertEqual(Down(self.port, r["call"]).status, 401)
        finally:
            h.trust_loopback = True


def load_tests(loader, tests, pattern):  # (its own tests only: not the skeleton's again, which it builds on)
    return unittest.TestSuite(LiveWatchTest(n) for n in loader.getTestCaseNames(LiveWatchTest) if n in vars(LiveWatchTest))


if __name__ == "__main__":
    unittest.main()
