"""Plugin services, secrets, origins, a plugin's command and agent hint (builddoc/plugins.md), end to end with the
public example examples/webhook (a copy listening on a free port) and the fake Claude Code.

Run: uv run python -m unittest discover -s tests -v
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import test_skeleton
from orbit import plugins
from orbit.services import plugin_services

ROOT = Path(__file__).resolve().parent.parent


class PluginServicesTest(test_skeleton.SkeletonTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        t = Path(cls.tmp.name)
        cls.plugdir = t / "webhook"
        shutil.copytree(ROOT / "examples" / "webhook", cls.plugdir)
        toml = cls.plugdir / "plugin.toml"
        toml.write_text(toml.read_text().replace('WEBHOOK_PORT = "8790"', 'WEBHOOK_PORT = "0"'))
        flaky = t / "flaky"  # a service that always fails: restarts with backoff, then gives up
        flaky.mkdir()
        (flaky / "plugin.toml").write_text(f'[plugin]\nname = "flaky"\n[service.boom]\ncommand = [{json.dumps(sys.executable)}, "-c", "import sys; print(\'boom\'); sys.exit(3)"]\n')
        os.environ["ORBIT_PORT"] = str(cls.port)
        cls.found = plugins.load(cls.state, [str(cls.plugdir), str(flaky)])
        cls.data = cls.state / "plugin-data" / "webhook"

    @classmethod
    def tearDownClass(cls):
        plugins.unload()  # (stops the services)
        os.environ.pop("ORBIT_PORT", None)
        super().tearDownClass()

    def until(self, check, timeout=10.0, what="it"):
        end = time.time() + timeout
        while time.time() < end:
            v = check()
            if v:
                return v
            time.sleep(0.05)
        self.fail(f"{what} did not happen in {timeout} s")

    def plugin(self, name):
        _, d = self.req("GET", "/api/plugins")
        return next(p for p in d["plugins"] if p["name"] == name)

    def hook(self, body, secret):
        port = (self.data / "port").read_text().strip()
        r = urllib.request.Request(f"http://127.0.0.1:{port}/hook", data=json.dumps(body).encode(), method="POST",
                                   headers={"Content-Type": "application/json", "X-Webhook-Secret": secret})
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_the_whole_loop(self):
        by = {p["name"]: p for p in self.found}
        self.assertTrue(by["webhook"]["ok"], by["webhook"])
        self.assertEqual(by["webhook"]["services"], ["listener"])
        # no secret yet: the service cannot start; it is listed as not set
        p = self.plugin("webhook")
        self.assertEqual(p["secrets"], [{"key": "WEBHOOK_SECRET", "set": False}])
        plugin_services.start_all()
        self.until(lambda: self.plugin("webhook")["services"][0]["state"] == "needs secrets", what="waiting for its secret")
        self.assertIn("set WEBHOOK_SECRET", self.plugin("webhook")["services"][0]["error"])
        # the secret: kept 0600, never in /api/plugins; the service restarts with it and listens
        code, _ = self.req("POST", "/api/plugins/webhook/secret", {"key": "WEBHOOK_SECRET", "value": "s3cret"})
        self.assertEqual(code, 200)
        f = self.state / "secrets" / "webhook.json"
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(f.parent.stat().st_mode), 0o700)
        _, raw = self.req("GET", "/api/plugins")
        self.assertNotIn("s3cret", json.dumps(raw))
        self.assertEqual(self.plugin("webhook")["secrets"], [{"key": "WEBHOOK_SECRET", "set": True}])
        self.until(lambda: (self.data / "port").exists() and self.plugin("webhook")["services"][0]["state"] == "running",
                   what="the service listening")
        code, _ = self.req("POST", "/api/plugins/webhook/secret", {"key": "OTHER", "value": "x"})
        self.assertEqual(code, 400)  # not declared
        self.assertEqual(self.hook({"session": "x", "text": "hi"}, "wrong")[0], 401)

        # a request through the hook: the card is marked with its origin; the answer comes back at once (wait)
        s = self.new_session("hooked")
        code, out = self.hook({"session": "hooked", "text": "hello from outside", "label": "CI", "wait": True}, "s3cret")
        self.assertEqual(code, 200, out)
        self.assertEqual((out["state"], out["answer"]), ("completed", "answer: hello from outside"))
        _, d = self.req("GET", f"/api/sessions/{s['id']}/cards")
        self.assertEqual(d["cards"][0]["origin"], {"label": "CI", "data": {"callback": ""}, "plugin": "webhook"})
        self.assertEqual(d["cards"][0]["text"], "hello from outside")  # the card: the text as sent
        said = (Path(self.tmp.name) / "projects" / "inputs.log").read_text()
        self.assertIn("This request came via CI (the webhook plugin)", said)  # the agent: where its answer goes

        # a callback: the answer is POSTed there
        got = []
        class CB(BaseHTTPRequestHandler):
            def do_POST(self):
                got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(204)
                self.end_headers()
            def log_message(self, *a):
                pass
        cb = HTTPServer(("127.0.0.1", 0), CB)
        threading.Thread(target=cb.serve_forever, daemon=True).start()
        try:
            code, out = self.hook({"session": s["id"], "text": "later please",
                                   "callback": f"http://127.0.0.1:{cb.server_address[1]}/"}, "s3cret")
            self.assertEqual((code, out["request"]), (202, 2))
            self.until(lambda: got, what="the callback")
            self.assertEqual((got[0]["request"], got[0]["answer"]), (2, "answer: later please"))
        finally:
            cb.shutdown()
            cb.server_close()

        # the origin survives a restart (cards rebuilt from the harness's transcript)
        type(self)._unserve()
        type(self)._serve()
        os.environ["ORBIT_PORT"] = str(self.port)
        _, d = self.req("GET", f"/api/sessions/{s['id']}/cards")
        self.assertEqual([c.get("origin", {}).get("label") for c in d["cards"]], ["CI", "webhook"])

        # the plugin's command, for agents: run by the server with the plugin's environment
        run = lambda: self.req("POST", "/api/plugins/webhook/run", {"args": ["recent"]})[1]
        r = self.until(lambda: (lambda r: r if "answer: later please" in r["stdout"] else None)(run()),
                       what="the webhook remembering it")  # (it writes after the callback)
        self.assertEqual(r["code"], 0, r)
        out = subprocess.run([str(ROOT / "cli" / "orbit-agent"), "p", "webhook", "url"], capture_output=True, text=True,
                             env={**os.environ, "ORBIT_PORT": str(self.port)}, timeout=30)  # (stdin: this test's, open)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(out.stdout.startswith("http://127.0.0.1:"))
        # the agent hint is on the [orbit] line
        from orbit.desk.session import orbit_line
        self.assertIn("webhook: requests marked “via webhook” came over HTTP", orbit_line(self.desk.get(s["id"])))
        # its log
        _, lg = self.req("GET", "/api/plugins/webhook/log?service=listener")
        self.assertIn("listening on", lg["log"])

    def test_a_failing_service_backs_off_and_gives_up(self):
        from unittest import mock
        with mock.patch.object(plugin_services, "BACKOFF_MAX", 0.05), mock.patch.object(plugin_services, "CRASH_MAX", 3):
            svc = plugin_services.of("flaky")[0]
            svc.crashes = plugin_services.deque(maxlen=3)
            svc.start()
            self.until(lambda: svc.state == "failed", timeout=15, what="giving up")
        st = self.plugin("flaky")["services"][0]
        self.assertEqual((st["last_exit"], st["restarts"]), (3, 2))
        self.assertIn("3 failures", st["error"])
        self.assertEqual(svc.tail().splitlines().count("boom"), 3)

    def test_from_another_machine_plugins_cannot_be_managed(self):
        from orbit.machines import remote
        h = type(self).srv.RequestHandlerClass
        tok = {"Authorization": "Bearer " + remote.token(self.state)}  # another machine, with the right token
        h.trust_loopback = False  # every request now counts as another machine's
        try:
            self.assertEqual(self.req("GET", "/api/plugins", headers=tok)[1]["local"], False)
            for path, body in (("/api/plugins/webhook/secret", {"key": "WEBHOOK_SECRET", "value": "x"}),
                               ("/api/plugins/webhook/run", {"args": ["recent"]}),
                               ("/api/plugins/webhook/restart", {})):
                self.assertEqual(self.req("POST", path, body, headers=tok)[0], 403, path)
            self.assertEqual(self.req("GET", "/api/plugins/webhook/log", headers=tok)[0], 403)
        finally:
            h.trust_loopback = True

for name in [n for n in dir(test_skeleton.SkeletonTest) if n.startswith("test_")]:
    if name not in PluginServicesTest.__dict__:
        setattr(PluginServicesTest, name, None)

if __name__ == "__main__":
    unittest.main()
