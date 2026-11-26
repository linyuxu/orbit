"""Web Push to the phone (orbit/services/webpush.py, push.py; /api/push; schedules with notify; orbit-agent notify /
remind), against the RFCs' test vectors and a local push service that decrypts what it gets. No real push service
is reached: the server is told (push.ALLOW_EXTRA) that this test's local address is one.

Run: uv run python -m unittest discover -s tests -p test_push.py -v
"""

import json
import os
import stat
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import test_skeleton
from orbit.services import push, webpush as wp


class VectorsTest(unittest.TestCase):
    def test_rfc8291_payload(self):  # RFC 8291 §5 (and its appendix A's keys)
        body = wp.encrypt(b"When I grow up, I want to be a watermelon",
                          "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4", "BTBZMqHH6r4Tts7J_aSIgg",
                          as_private=int.from_bytes(wp.b64u_dec("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"),
                          salt=wp.b64u_dec("DGv6ra1nlYgDCS1FRnbzlw"))
        self.assertEqual(wp.b64u_enc(body),
                         "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95b"
                         "Qpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")
        ua = int.from_bytes(wp.b64u_dec("q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"), "big")
        self.assertEqual(wp.decrypt(body, ua, "BTBZMqHH6r4Tts7J_aSIgg"), b"When I grow up, I want to be a watermelon")

    def test_rfc6979_ecdsa(self):  # RFC 6979 A.2.5, P-256, SHA-256, "sample"
        x = 0xC9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721
        sig = wp.sign(x, b"sample")
        self.assertEqual(sig.hex().upper(), "EFD48B2AACB6A8FD1140DD9CD45E81D69D2C877B56AAF991C34D0EA84EAF3716"
                                            "F7CB1C942D657C41D436C7A1B6E29F65F3E900DBB9AFF4064DC4AB2F843ACDA8")
        pub = wp.public_key(x)
        self.assertEqual(pub[1:33].hex().upper(), "60FED4BA255A9D31C961EB74C6356D68C049B8923B61FA6CE669622E60F29FB6")
        self.assertTrue(wp.verify(pub, b"sample", sig))
        self.assertFalse(wp.verify(pub, b"sample!", sig))

    def test_aes_and_gcm(self):
        rk = wp._expand(bytes.fromhex("000102030405060708090a0b0c0d0e0f"))  # FIPS-197 C.1
        self.assertEqual(wp._encrypt_block(rk, bytes.fromhex("00112233445566778899aabbccddeeff")).hex(), "69c4e0d86a7b0430d8cdb78070b4c55a")
        # GCM spec test case 4 (key, IV, 60 bytes, AAD)
        k, iv = bytes.fromhex("feffe9928665731c6d6a8f9467308308"), bytes.fromhex("cafebabefacedbaddecaf888")
        pt = bytes.fromhex("d9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a721c3c0c95956809532fcf0e2449a6b525"
                           "b16aedf5aa0de657ba637b39")
        aad = bytes.fromhex("feedfacedeadbeeffeedfacedeadbeefabaddad2")
        out = wp.aes_gcm_encrypt(k, iv, pt, aad)
        self.assertEqual(out[-16:].hex(), "5bc94fbc3221a5db94fae95ae7121a47")
        self.assertEqual(wp.aes_gcm_decrypt(k, iv, out, aad), pt)
        with self.assertRaises(ValueError):
            wp.aes_gcm_decrypt(k, iv, out[:-1] + bytes([out[-1] ^ 1]), aad)

    def test_vapid_token(self):
        v = wp.new_vapid()
        h = wp.vapid_authorization("https://web.push.apple.com/QGx/abc", v, "https://orbit.example", now=1000)
        t, k = h.removeprefix("vapid t=").split(", k=")
        self.assertEqual(k, v["publicKey"])
        head, claims, sig = t.split(".")
        self.assertEqual(json.loads(wp.b64u_dec(head)), {"typ": "JWT", "alg": "ES256"})
        self.assertEqual(json.loads(wp.b64u_dec(claims)), {"aud": "https://web.push.apple.com", "exp": 4600, "sub": "https://orbit.example"})
        self.assertTrue(wp.verify(wp.b64u_dec(k), f"{head}.{claims}".encode(), wp.b64u_dec(sig)))

    def test_only_push_services(self):
        ok = ["https://web.push.apple.com/abc", "https://fcm.googleapis.com/fcm/send/x", "https://updates.push.services.mozilla.com/wpush/v2/x",
              "https://wns2-par02p.notify.windows.com/w/?token=x", "https://web.push.apple.com:443/x"]
        bad = ["http://web.push.apple.com/x", "https://push.apple.com.evil.example/x", "https://127.0.0.1/x", "https://evil.example/web.push.apple.com",
               "https://u:p@web.push.apple.com/x", "https://web.push.apple.com:8443/x", "https://localhost/x", "file:///etc/passwd", "nonsense"]
        self.assertEqual([u for u in ok if not wp.allowed_endpoint(u)], [])
        self.assertEqual([u for u in bad if wp.allowed_endpoint(u)], [])


# ---------------------------------------------------------------- a push service, here
class FakePushService:
    """Takes POSTs at /ok/*, /gone/*, /err/*, /redirect/*: records each (headers, decrypted body) and answers as a
    push service would (201, 410, 500 with a reason, a redirect)."""

    def __init__(self):
        self.got = []
        me = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                me.got.append({"path": self.path, "headers": dict(self.headers), "body": self.rfile.read(n)})
                kind = self.path.split("/")[1]
                code, body = {"ok": (201, b""), "gone": (410, b'{"reason":"Unregistered"}'),
                              "err": (500, b'{"reason":"InternalServerError"}'), "redirect": (302, b"")}[kind]
                self.send_response(code)
                if kind == "redirect":
                    self.send_header("Location", "http://127.0.0.1:1/elsewhere")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}/"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


def device(base, kind="ok", name="phone"):
    """A device's subscription (its private key kept, to read what it is sent)."""
    d = wp.new_private()
    auth = wp.b64u_enc(os.urandom(16))
    return {"endpoint": f"{base}{kind}/{name}-{os.urandom(4).hex()}", "keys": {"p256dh": wp.b64u_enc(wp.public_key(d)), "auth": auth}}, d


def read(got, d, sub):
    return json.loads(wp.decrypt(got["body"], d, sub["keys"]["auth"]))


class PushTest(test_skeleton.SkeletonTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fake = FakePushService()
        push.ALLOW_EXTRA = (cls.fake.base,)

    @classmethod
    def tearDownClass(cls):
        push.ALLOW_EXTRA = ()
        cls.fake.stop()
        super().tearDownClass()

    def setUp(self):
        self.fake.got.clear()
        for s in self.req("GET", "/api/push")[1]["subscriptions"]:
            self.req("POST", "/api/push/unsubscribe", {"id": s["id"]})
        for it in list(self.desk.scheduler.items):
            self.desk.scheduler.delete(it["id"])

    def subscribe(self, kind="ok", name="phone"):
        sub, d = device(self.fake.base, kind, name)
        code, r = self.req("POST", "/api/push/subscribe", {"subscription": sub, "label": "iPhone"})
        self.assertEqual(code, 200, r)
        self.assertTrue(r["subscribed"])
        return sub, d

    def agent(self, *args, session=""):
        env = {**os.environ, "ORBIT_PORT": str(self.port), "ORBIT_SESSION": session}
        return subprocess.run([sys.executable, "-m", "orbit.cli.agent", *args], env=env, capture_output=True, text=True, timeout=60)

    def cards_everywhere(self):
        return sum(len(self.req("GET", f"/api/sessions/{s['id']}/cards")[1]["cards"]) for s in self.req("GET", "/api/sessions")[1]["sessions"])

    # ---------- subscribe, send, what the phone gets
    def test_subscribe_and_send_reaches_the_device_encrypted(self):
        st = self.req("GET", "/api/push")[1]
        pub = st["publicKey"]
        wp.point(wp.b64u_dec(pub))  # (a P-256 key for applicationServerKey)
        sub, d = self.subscribe()
        s = self.new_session("report")
        code, r = self.req("POST", "/api/push/send", {"title": "Report ready", "body": "Q3 numbers are in", "sid": s["id"]})
        self.assertEqual((code, r["sent"], r["devices"]), (200, 1, 1), r)
        g = self.fake.got[-1]
        self.assertEqual(read(g, d, sub), {"title": "Report ready", "body": "Q3 numbers are in", "sid": s["id"],
                                           "url": f"/m/#s/{s['id']}", "tag": s["id"]})
        h = {k.lower(): v for k, v in g["headers"].items()}
        self.assertEqual((h["content-encoding"], h["ttl"], h["urgency"]), ("aes128gcm", "86400", "normal"))
        t, k = h["authorization"].removeprefix("vapid t=").split(", k=")
        self.assertEqual(k, pub)
        head, claims, sig = t.split(".")
        self.assertTrue(wp.verify(wp.b64u_dec(pub), f"{head}.{claims}".encode(), wp.b64u_dec(sig)))
        self.assertEqual(json.loads(wp.b64u_dec(claims))["aud"], self.fake.base.rstrip("/"))
        # what the API shows: never an endpoint, a key or the VAPID private key; the log has the push service's answer
        st = self.req("GET", "/api/push")[1]
        dump = json.dumps(st)
        self.assertNotIn(sub["endpoint"], dump)
        self.assertNotIn(sub["keys"]["auth"], dump)
        self.assertNotIn(json.loads((self.state / "push" / "vapid.json").read_text())["privateKey"], dump)
        self.assertEqual((st["log"][-1]["outcome"], st["log"][-1]["status"], st["log"][-1]["title"]), ("accepted", 201, "Report ready"))
        self.assertNotIn("Q3 numbers", (self.state / "push" / "log.jsonl").read_text())  # (no body in the log)
        for f in ("vapid.json", "subscriptions.json", "log.jsonl"):
            self.assertEqual(stat.S_IMODE((self.state / "push" / f).stat().st_mode), 0o600, f)
        # the phone's own test: to it alone
        other, d2 = self.subscribe(name="ipad")
        n = len(self.fake.got)
        r = self.req("POST", "/api/push/send", {"endpoint": sub["endpoint"], "title": "Orbit", "body": "test"})[1]
        self.assertEqual((r["sent"], r["devices"], len(self.fake.got) - n), (1, 1, 1))
        self.assertTrue(self.fake.got[-1]["path"].startswith("/ok/phone-"))

    def test_declarative_on_an_https_server(self):
        """With an HTTPS address: Declarative Web Push, navigate an absolute /m/?session= address (iOS opens it itself)."""
        sub, d = self.subscribe()
        s = self.new_session("decl")
        f = self.state / "https.json"
        f.write_text(json.dumps({"url": "https://orbit.example.ts.net:8443"}))
        try:
            self.req("POST", "/api/push/send", {"title": "Report ready", "body": "Q3", "sid": s["id"]})
            msg = read(self.fake.got[-1], d, sub)
            self.assertEqual(set(msg), {"web_push", "notification"})
            self.assertEqual(msg["web_push"], 8030)
            n = msg["notification"]
            self.assertEqual((n["title"], n["body"]), ("Report ready", "Q3"))
            self.assertRegex(n["navigate"], r"^https://orbit\.example\.ts\.net:8443/m/\?session=" + s["id"] + r"&at=\d+$")
            self.req("POST", "/api/push/send", {"title": "No session"})
            self.assertEqual(read(self.fake.got[-1], d, sub)["notification"], {"title": "No session", "navigate": "https://orbit.example.ts.net:8443/m/"})
        finally:
            f.unlink()

    def test_refused_endpoints_and_keys(self):
        bad_hosts = ["https://evil.example/push", "http://127.0.0.1:9/ok/x", "https://web.push.apple.com.evil.example/x"]
        _, d = device(self.fake.base)
        for ep in bad_hosts:
            sub = {"endpoint": ep, "keys": {"p256dh": wp.b64u_enc(wp.public_key(d)), "auth": wp.b64u_enc(os.urandom(16))}}
            code, e = self.req("POST", "/api/push/subscribe", {"subscription": sub})
            self.assertEqual(code, 400, ep)
            self.assertIn("push service", e["error"])
        sub, _ = device(self.fake.base)
        sub["keys"]["p256dh"] = wp.b64u_enc(b"\x04" + b"\x01" * 64)  # (not on the curve)
        self.assertEqual(self.req("POST", "/api/push/subscribe", {"subscription": sub})[0], 400)
        sub, _ = device(self.fake.base)
        sub["keys"]["auth"] = wp.b64u_enc(b"short")
        self.assertEqual(self.req("POST", "/api/push/subscribe", {"subscription": sub})[0], 400)
        self.assertEqual(self.req("GET", "/api/push")[1]["subscriptions"], [])
        # and the sender itself, for a subscription stored some other way: no request leaves
        r = wp.send({"endpoint": "https://evil.example/x", "keys": {}}, b"{}", wp.new_vapid(), "https://x")
        self.assertEqual((r["outcome"], r["status"]), ("error", 0))

    def test_once_per_key_gone_and_errors(self):
        sub, d = self.subscribe()
        r1 = self.req("POST", "/api/push/send", {"title": "Build green", "key": "build:42"})[1]
        r2 = self.req("POST", "/api/push/send", {"title": "Build green", "key": "build:42"})[1]  # (a retry)
        self.assertEqual((r1["sent"], r2["sent"], r2["duplicate"], len(self.fake.got)), (1, 0, 1, 1))
        # a device added after: the retry reaches it (once), not the first one again
        sub2, d2 = self.subscribe(name="second")
        r3 = self.req("POST", "/api/push/send", {"title": "Build green", "key": "build:42"})[1]
        self.assertEqual((r3["sent"], r3["duplicate"], len(self.fake.got)), (1, 1, 2))
        self.assertTrue(self.fake.got[-1]["path"].startswith("/ok/second-"))
        # a device gone (410): forgotten; one that fails (500): kept, the failure logged with the service's reason
        gone, _ = self.subscribe("gone")
        bad, _ = self.subscribe("err")
        redir, _ = self.subscribe("redirect")
        r = self.req("POST", "/api/push/send", {"title": "hello"})[1]
        out = {x["sub"]: (x["outcome"], x["status"]) for x in r["results"]}
        self.assertEqual(out[push.Push.sub_id(gone["endpoint"])], ("gone", 410))
        self.assertEqual(out[push.Push.sub_id(bad["endpoint"])], ("error", 500))
        self.assertEqual(out[push.Push.sub_id(redir["endpoint"])], ("error", 302))  # (a redirect is not followed)
        self.assertEqual(r["sent"], 2)
        st = self.req("GET", "/api/push")[1]
        ids = {s["id"] for s in st["subscriptions"]}
        self.assertNotIn(push.Push.sub_id(gone["endpoint"]), ids)
        self.assertIn(push.Push.sub_id(bad["endpoint"]), ids)
        self.assertIn("InternalServerError", [l.get("reason") for l in st["log"]])
        self.assertEqual(self.req("POST", "/api/push/send", {"title": "x", "sid": "nope"})[0], 404)
        self.assertEqual(self.req("POST", "/api/push/send", {"title": "x", "endpoint": self.fake.base + "ok/unknown"})[0], 404)

    def test_unsubscribe(self):
        sub, _ = self.subscribe()
        r = self.req("POST", "/api/push/unsubscribe", {"endpoint": sub["endpoint"]})[1]
        self.assertTrue(r["removed"])
        self.assertEqual(self.req("POST", "/api/push/send", {"title": "x"})[1]["devices"], 0)
        self.assertEqual(self.fake.got, [])

    # ---------- reminders: schedules with notify
    def test_a_reminder_is_sent_with_no_agent(self):
        sub, d = self.subscribe()
        s = self.new_session("dentist")
        before = self.cards_everywhere()
        code, x = self.req("POST", "/api/schedules", {"when": "in 10m", "text": "Call the dentist", "name": "Dentist", "notify": True, "sid": s["id"]})
        self.assertEqual(code, 200, x)
        self.assertEqual((x["notify"], x["target_name"], x["journal"]), (True, "phone notification → dentist", ""))
        self.assertEqual(self.desk.scheduler.tick(x["next"] - 5), [])
        self.assertEqual(self.desk.scheduler.tick(x["next"] + 1), [x["id"]])
        msg = read(self.fake.got[-1], d, sub)
        self.assertEqual((msg["title"], msg["body"], msg["sid"], msg["url"]), ("Dentist", "Call the dentist", s["id"], f"/m/#s/{s['id']}"))
        self.assertEqual(self.fake.got[-1]["headers"].get("Urgency"), "high")
        y = self.desk.scheduler.get(x["id"])
        self.assertEqual((y["enabled"], y["runs"], y["last"]["push"]["sent"]), (False, 1, 1))
        self.assertNotIn("error", y["last"])
        self.assertEqual(self.cards_everywhere(), before)  # no agent woke
        self.assertEqual(len(self.req("GET", "/api/sessions")[1]["sessions"]), len(self.req("GET", "/api/sessions")[1]["sessions"]))
        # no session: a tap opens Orbit
        _, z = self.req("POST", "/api/schedules", {"when": "in 5m", "text": "Stretch", "notify": True})
        self.assertEqual(z["target_name"], "phone notification")
        self.desk.scheduler.tick(z["next"] + 1)
        self.assertEqual(read(self.fake.got[-1], d, sub)["url"], "/m/")

    def test_a_repeating_reminder_and_no_double_after_a_restart(self):
        sub, d = self.subscribe()
        _, x = self.req("POST", "/api/schedules", {"when": "every 1h", "text": "Drink water", "notify": True})
        first = x["next"]
        self.desk.scheduler.tick(first + 1)
        y = self.desk.scheduler.get(x["id"])
        self.assertAlmostEqual(y["next"], first + 1 + 3600, delta=1)
        self.desk.scheduler.tick(y["next"] + 1)  # the next hour: sent again
        self.assertEqual(len(self.fake.got), 2)
        self.assertEqual(self.desk.scheduler.get(x["id"])["runs"], 2)
        # Orbit stops right after a send, before the schedule's next time is saved: back up, the same time is due again
        path = self.desk.scheduler.path
        data = json.loads(path.read_text())
        due = time.time() - 60
        for it in data["schedules"]:
            it["next"] = due
        type(self)._unserve()
        path.write_text(json.dumps(data))
        type(self)._serve()
        self.desk.scheduler.tick()  # (sent now: a new due time)
        self.assertEqual(len(self.fake.got), 3)
        type(self)._unserve()
        path.write_text(json.dumps(data))  # (that same due time once more, as if the save after it was lost)
        type(self)._serve()
        self.desk.scheduler.tick()
        self.assertEqual(len(self.fake.got), 3)  # not twice
        y = self.desk.scheduler.get(x["id"])
        self.assertEqual(y["last"]["push"]["duplicate"], 1)
        self.assertNotIn("error", y["last"])
        self.assertGreater(y["next"], time.time())
        # Run now always sends (its own key)
        self.req("POST", f"/api/schedules/{x['id']}/run")
        self.req("POST", f"/api/schedules/{x['id']}/run")
        self.assertEqual(len(self.fake.got), 5)

    def test_a_reminder_with_no_phone_says_so(self):
        _, x = self.req("POST", "/api/schedules", {"when": "in 5m", "text": "nobody hears", "notify": True})
        self.desk.scheduler.tick(x["next"] + 1)
        self.assertIn("no phone has notifications on", self.desk.scheduler.get(x["id"])["last"]["error"])
        self.assertEqual(self.req("POST", "/api/schedules", {"when": "every 1m", "text": "x", "notify": True, "check": "echo hi"})[0], 400)
        self.assertEqual(self.req("POST", "/api/schedules", {"when": "in 5m", "text": " ", "notify": True})[1]["error"], "say what the reminder says")

    def test_an_ordinary_schedule_still_wakes_its_agent(self):
        self.subscribe()
        s = self.new_session("wakes")
        _, x = self.req("POST", "/api/schedules", {"when": "in 5m", "text": "check CI", "sid": s["id"]})
        self.assertFalse(x["notify"])
        self.desk.scheduler.tick(x["next"] + 1)
        self.wait_card(s["id"], 1, ("completed",))
        self.assertEqual(self.fake.got, [])  # (and nothing to the phone)

    # ---------- the CLI
    def test_cli_notify_and_remind(self):
        sub, d = self.subscribe()
        s = self.new_session("cli-push")
        r = self.agent("notify", "Report ready", "see", "the", "summary", session=s["id"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("sent to 1 of 1 device(s)", r.stdout)
        msg = read(self.fake.got[-1], d, sub)
        self.assertEqual((msg["title"], msg["body"], msg["sid"]), ("Report ready", "see the summary", s["id"]))
        r = self.agent("notify", "Once", "--key", "k1", session=s["id"])
        r2 = self.agent("notify", "Once", "--key", "k1", session=s["id"])
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("1 had it already", r2.stdout)
        r = self.agent("remind", "30m", "Stand up", "--title", "Break", session=s["id"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("phone notification → cli-push", r.stdout)
        it = next(x for x in self.desk.scheduler.items if x["name"] == "Break")
        self.assertEqual((it["notify"], it["sid"], it["spec"]["kind"], it["text"]), (True, s["id"], "once", "Stand up"))
        r = self.agent("remind", "daily 08:00", "Standup notes", "--no-session", session=s["id"])
        it = next(x for x in self.desk.scheduler.items if x["text"] == "Standup notes")
        self.assertEqual((it["spec"]["kind"], it["sid"]), ("weekly", ""))
        self.req("POST", "/api/push/unsubscribe", {"endpoint": sub["endpoint"]})
        r = self.agent("notify", "Nobody", session=s["id"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no phone has notifications on", r.stderr)


def load_tests(loader, tests, pattern):  # (their own tests only: not the skeleton's again)
    suite = unittest.TestSuite()
    for cls in (VectorsTest, PushTest):
        suite.addTests(cls(n) for n in loader.getTestCaseNames(cls) if n in vars(cls))
    return suite


if __name__ == "__main__":
    unittest.main()
