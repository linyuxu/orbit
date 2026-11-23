"""Orbit's own API keys (services/keys.py, Settings → General): kept 0600, write-only, this machine only, checked
against the provider."""

import json
import os
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from orbit.machines import remote
from orbit.services import keys

GOOD = "sk-proj-" + "a" * 40 + "WXYZ"


class FakeOpenAI(BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.headers.get("Authorization") == f"Bearer {GOOD}" and self.path.startswith("/v1/models")
        body = b'{"data": []}' if ok else b'{"error": {"message": "Incorrect API key provided"}}'
        self.send_response(200 if ok else 401)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class KeysTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from orbit.desk import Desk
        from orbit.server import make_server
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        cls.state = t / "state"
        cls.desk = Desk(cls.state)
        cls.srv = make_server(0, cls.desk, schedules=t / "schedules.json")
        cls.port = cls.srv.server_address[1]
        cls.srv.RequestHandlerClass.port = cls.port
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAI)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        cls._base, keys.OPENAI_BASE = keys.OPENAI_BASE, f"http://127.0.0.1:{cls.fake.server_address[1]}"
        cls.token = remote.token(cls.state)

    @classmethod
    def tearDownClass(cls):
        keys.OPENAI_BASE = cls._base
        cls.fake.shutdown()
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.desk.shutdown()
        cls.tmp.cleanup()

    def req(self, method, path, body=None, headers=None):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method, headers={"Content-Type": "application/json", **(headers or {})},
                                   data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(r, timeout=15) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_set_check_remove(self):
        code, body = self.req("GET", "/api/keys")
        self.assertEqual((code, json.loads(body)["keys"]["openai"]["set"]), (200, False))
        self.assertEqual(json.loads(self.req("POST", "/api/keys/check", {"name": "openai"})[1]), {"ok": False, "error": "no key is set"})
        for bad in ("hello", "sk-short", "sk-" + "a" * 30 + " b", "pk-" + "a" * 40):
            self.assertEqual(self.req("POST", "/api/keys", {"name": "openai", "value": bad})[0], 400, bad)
        self.assertEqual(self.req("POST", "/api/keys", {"name": "anthropic", "value": GOOD})[0], 400)
        code, body = self.req("POST", "/api/keys", {"name": "openai", "value": "  " + GOOD + "\n"})
        self.assertEqual(code, 200)
        st = json.loads(body)["keys"]["openai"]
        self.assertEqual((st["set"], st["hint"]), (True, "sk-…WXYZ"))
        self.assertNotIn(GOOD, body)
        self.assertNotIn(GOOD, self.req("GET", "/api/keys")[1])  # never sent back
        self.assertNotIn(GOOD, self.req("GET", "/api/settings")[1])
        f = self.state / "secrets" / "_orbit.json"
        self.assertEqual(stat.S_IMODE(os.stat(f).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(f.parent).st_mode), 0o700)
        self.assertEqual(keys.get(self.state, "openai"), GOOD)
        self.assertEqual(json.loads(self.req("POST", "/api/keys/check", {"name": "openai"})[1]), {"ok": True})
        keys.set_key(self.state, "openai", "sk-proj-" + "b" * 44)  # a key the provider refuses
        r = json.loads(self.req("POST", "/api/keys/check", {"name": "openai"})[1])
        self.assertFalse(r["ok"])
        self.assertIn("401", r["error"])
        self.assertIn("Incorrect API key", r["error"])
        code, body = self.req("POST", "/api/keys", {"name": "openai", "value": None})
        self.assertEqual((code, json.loads(body)["keys"]["openai"]["set"]), (200, False))
        self.assertEqual(keys.get(self.state, "openai"), "")

    def test_another_machine_cannot_read_or_set_it(self):
        net = {"Host": f"100.64.0.9:{self.port}", "Authorization": f"Bearer {self.token}"}
        self.assertEqual(self.req("GET", "/api/keys", headers=net)[0], 403)
        self.assertEqual(self.req("POST", "/api/keys", {"name": "openai", "value": GOOD}, headers=net)[0], 403)
        self.assertEqual(self.req("POST", "/api/keys/check", {"name": "openai"}, headers=net)[0], 403)
        self.assertEqual(keys.get(self.state, "openai"), "")


if __name__ == "__main__":
    unittest.main()
