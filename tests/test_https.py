"""HTTPS by Tailscale (machines/https.py), the guard that sends plain network HTTP to it, and the phone's app routes."""

import json
import os
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from orbit.machines import https, remote

TS_HOST = "box.tail0000.ts.net"


class HttpsTest(unittest.TestCase):
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
        cls.token = remote.token(cls.state)
        # A tailscale that answers like a machine whose serve hands :8443 to this server (or nothing: TS_OFF)
        cls.fake = t / "tailscale"
        cls.fake.write_text(f"""#!/bin/sh
case "$*" in
  "status --json") echo '{{"Self": {{"DNSName": "{TS_HOST}."}}, "CertDomains": ["{TS_HOST}"]}}' ;;
  "serve status --json")
    if [ -f "{t}/on" ]; then
      echo '{{"Web": {{"{TS_HOST}:8443": {{"Handlers": {{"/": {{"Proxy": "http://127.0.0.1:{cls.port}"}}}}}}}}}}'
    else echo '{{}}'; fi ;;
  "serve --bg --https=8443 http://127.0.0.1:{cls.port}") touch "{t}/on" ;;
  "serve --https=8443 off") rm -f "{t}/on" ;;
  *) echo "unexpected: $*" >&2; exit 1 ;;
esac
""")
        cls.fake.chmod(cls.fake.stat().st_mode | stat.S_IEXEC)
        cls._env = os.environ.get("ORBIT_TAILSCALE")
        os.environ["ORBIT_TAILSCALE"] = str(cls.fake)

    @classmethod
    def tearDownClass(cls):
        if cls._env is None:
            os.environ.pop("ORBIT_TAILSCALE", None)
        else:
            os.environ["ORBIT_TAILSCALE"] = cls._env
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.desk.shutdown()
        cls.tmp.cleanup()

    def tearDown(self):
        https.save(self.state, {"on": False})

    def raw(self, path, method="GET", headers=None, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method, headers=headers or {},
                                     data=json.dumps(body).encode() if body is not None else None)

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=10) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_switch_on_and_off(self):
        st = self.raw("/api/sharing?fresh=1")
        self.assertEqual(st[0], 200)
        h = json.loads(st[2])["https"]
        self.assertEqual((h["available"], h["on"], h["host"], h["error"]), (True, False, TS_HOST, ""))
        code, _, b = self.raw("/api/https", "POST", body={"on": True})
        on = json.loads(b)
        self.assertEqual(code, 200, b)
        self.assertEqual((on["on"], on["url"], on["port"]), (True, f"https://{TS_HOST}:8443", 8443))
        self.assertEqual(on["phone_url"], f"https://{TS_HOST}:8443/m/?token={self.token}")
        self.assertEqual(https.saved_url(self.state), f"https://{TS_HOST}:8443")  # what the guard reads
        off = json.loads(self.raw("/api/https", "POST", body={"on": False})[2])
        self.assertFalse(off["on"])
        self.assertEqual(https.saved_url(self.state), "")
        # another machine may not switch it
        code, _, _ = self.raw("/api/https", "POST", {"Host": f"{TS_HOST}:8443", "X-Forwarded-Proto": "https",
                                                      "Authorization": f"Bearer {self.token}"}, {"on": True})
        self.assertEqual(code, 403)

    def test_plain_http_from_the_network_goes_to_https(self):
        https.save(self.state, {"on": True, "url": f"https://{TS_HOST}:8443"})
        net = {"Host": f"100.64.0.9:{self.port}", "Authorization": f"Bearer {self.token}"}
        code, h, _ = self.raw("/api/version?x=1", headers=net)
        self.assertEqual((code, h.get("Location")), (308, f"https://{TS_HOST}:8443/api/version?x=1"))
        self.assertEqual(self.raw("/api/sessions", "POST", net, {})[0], 421)  # a body is not sent on: refused
        # through Tailscale's HTTPS: served (with the token), refused without it
        tls = {"Host": f"{TS_HOST}:8443", "X-Forwarded-Proto": "https"}
        self.assertEqual(self.raw("/api/version", headers={**tls, "Authorization": f"Bearer {self.token}"})[0], 200)
        self.assertEqual(self.raw("/api/version", headers=tls)[0], 401)
        # this machine itself: plain HTTP as before
        self.assertEqual(self.raw("/api/version")[0], 200)

    def test_without_https_nothing_is_redirected(self):
        code, _, _ = self.raw("/api/version", headers={"Host": f"100.64.0.9:{self.port}", "Authorization": f"Bearer {self.token}"})
        self.assertEqual(code, 200)

    def test_the_ipad_app(self):
        """The full page as an iPad's home-screen app: its manifest starts it at /?app=1 with the token (its own storage),
        and that start is the page itself with the cookie, no redirect (the browser's link still redirects)."""
        tls = {"Host": f"{TS_HOST}:8443", "X-Forwarded-Proto": "https"}
        code, _, b = self.raw("/manifest.webmanifest", headers={**tls, "Cookie": f"orbit_token={self.token}"})
        self.assertEqual(code, 200, b)
        m = json.loads(b)
        self.assertEqual((m["start_url"], m["scope"], m["id"], m["display"]), (f"/?app=1&token={self.token}", "/", "/", "standalone"))
        self.assertEqual(self.raw("/manifest.webmanifest", headers=tls)[0], 401)
        code, h, b = self.raw(f"/?app=1&token={self.token}", headers=tls)
        self.assertEqual(code, 200)
        self.assertIn(self.token, h.get("Set-Cookie", ""))
        self.assertIn(b'rel="manifest" href="/manifest.webmanifest"', b)
        self.assertIn(b'apple-mobile-web-app-capable', b)
        code, h, _ = self.raw(f"/?token={self.token}", headers=tls)  # (a browser's link: to the address without it)
        self.assertEqual((code, h.get("Location")), (302, "/"))
        st = json.loads(self.raw("/api/https")[2])
        if st.get("on"):
            self.assertTrue(st["ipad_url"].endswith(f"/?token={self.token}"))

    def test_the_phone_app(self):
        tls = {"Host": f"{TS_HOST}:8443", "X-Forwarded-Proto": "https"}
        # the link: the page itself (it keeps the token and takes it out of the address), and the cookie
        code, h, b = self.raw(f"/m/?token={self.token}", headers=tls)
        self.assertEqual(code, 200)
        self.assertIn(self.token, h.get("Set-Cookie", ""))
        self.assertIn(f'manifest.webmanifest?token={self.token}'.encode(), b)
        code, h, _ = self.raw(f"/m?token={self.token}", headers=tls)  # /m: to /m/, the query kept
        self.assertEqual((code, h.get("Location")), (301, f"/m/?token={self.token}"))
        # the home-screen app keeps its own storage: its manifest starts it with the token
        code, _, b = self.raw("/m/manifest.webmanifest", headers={**tls, "Cookie": f"orbit_token={self.token}"})
        self.assertEqual(code, 200, b)
        m = json.loads(b)
        self.assertEqual((m["start_url"], m["scope"], m["display"]), (f"/m/?token={self.token}", "/m/", "standalone"))
        self.assertEqual(self.raw("/m/manifest.webmanifest", headers=tls)[0], 401)
        # a browser that opens the address without the link: a page that says how to get it
        code, h, b = self.raw("/m/", headers={**tls, "Accept": "text/html"})
        self.assertEqual((code, h.get("Content-Type")), (401, "text/html; charset=utf-8"))
        self.assertIn(b"Settings \xe2\x86\x92 Machines \xe2\x86\x92 iPhone", b)
        self.assertIn(b"Orbit on box", b)
        # icons: fetched without the cookie
        for icon in ("/m/icon-192.png", "/m/icon-512.png", "/m/apple-touch-icon.png", "/vendor/fonts/Inter-latin.woff2"):
            code, h, _ = self.raw(icon, headers=tls)
            self.assertEqual(code, 200, icon)
            self.assertIn(h.get("Content-Type"), ("image/png", "font/woff2"), icon)
        self.assertEqual(self.raw("/vendor/fonts/../../index.html", headers=tls)[0], 401)  # (only the font files)
        # the built-in Chinese: every slice its stylesheet names is there, and served
        import re
        css = (Path(__file__).resolve().parent.parent / "web/vendor/fonts/orbit-cjk.css").read_text()
        files = re.findall(r"url\(/vendor/fonts/([\w-]+\.woff2)\)", css)
        self.assertGreater(len(files), 50)
        for f in files:
            self.assertTrue((Path(__file__).resolve().parent.parent / "web/vendor/fonts" / f).is_file(), f)
        self.assertEqual(self.raw(f"/vendor/fonts/{files[0]}", headers=tls)[0], 200)
        self.assertNotIn("1F680", css)  # (emoji: the system's colour ones)


if __name__ == "__main__":
    unittest.main()
