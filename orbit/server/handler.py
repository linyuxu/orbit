"""The request handler: guards (Host / Origin, the token), then the routes of GET and POST.

The page is read from disk on every request, so ⌘⇧R reloads the UI without a restart.
Requests with a foreign Host or Origin are refused (DNS rebinding, cross-site POSTs)."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import subprocess
import sys
import time
from html import escape as html_escape
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse

from orbit import ROOT
from orbit.desk import MODES, Desk
from orbit.desk.session import DRIVERS, Stopping
from orbit.machines import federation, https, remote
from orbit.server.forward import FILE_ROUTES, Forward
from orbit.server.streams import Streams
from orbit.server.watch import Watch
from orbit.server.support import UPLOAD_MAX, USAGE, append_log, complete_dir, font_families, save_upload
from orbit.services import schedule
from orbit.services.shell import open_terminal_app
from orbit.workspace import files, findfiles, notes, skills


WEB = ROOT / "web"
DEFAULT_PORT = 8770


# Orbit's keys from an agent's page in a side tab (see Handler._show_page; side.js takes the same set, nothing else)
SHOW_KEYS = ("<script>/* Orbit: its keys from this side tab */(function(){var K=" + json.dumps(
    ["MS:KeyO", "M:KeyW", "AS:BracketLeft", "AS:BracketRight", "M:Backslash", "MS:BracketLeft", "MS:BracketRight",
     "M:KeyO", "MC:KeyO", "M:KeyK", "M:KeyJ", "M:KeyN", "M:KeyB"] + [f"M:Digit{i}" for i in range(1, 10)]) +
    ";addEventListener('keydown',function(e){var m=(e.metaKey?'M':'')+(e.ctrlKey?'C':'')+(e.altKey?'A':'')+(e.shiftKey?'S':'');"
    "if(!m)return;var c=m+':'+e.code;if(K.indexOf(c)<0)return;e.preventDefault();e.stopPropagation();"
    "parent.postMessage({orbit:'web-key',combo:c},'*')},true)})();</script>")

class Handler(Forward, Streams, Watch, BaseHTTPRequestHandler):
    server_version = "orbit"
    desk: Desk
    port: int
    sharing = None  # orbit.server.sharing.Sharing, set by make_server
    trust_loopback = True  # tests turn it off to play another machine over 127.0.0.1
    stop_server = None  # stops the server (POST /api/shutdown), set by make_server (and main)

    def log_message(self, fmt, *args):  # quiet: only errors
        if len(args) > 1 and isinstance(args[1], str) and args[1].startswith(("4", "5")):
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    # ------------------------------------------------------------------ guards
    def _allowed_hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}"}

    def _guard(self, write: bool) -> bool:
        # From another machine (`orbit serve --remote`): the token decides, the Host header does not (a page that
        # rebinds a DNS name to this address still has no token). Writes from a browser must come from this origin.
        # A request that names another host is another machine's, wherever it comes from: a proxy on this machine
        # (Tailscale in userspace mode, an ssh tunnel, a reverse proxy) hands it over from 127.0.0.1. It needs the
        # token like any other; a page that rebinds a DNS name to 127.0.0.1 has none.
        local_host = self.headers.get("Host", "") in self._allowed_hosts()
        self.from_remote = not (self.trust_loopback and remote.is_loopback(self.client_address[0]) and local_host)
        if self.from_remote and not https.via_https(self.headers):  # from the network, HTTPS only (once it is on)
            to = https.saved_url(self.desk.state_dir)
            if to:
                if self.command in ("GET", "HEAD"):
                    self.send_response(308)
                    self.send_header("Location", to + self.path)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    self._json({"error": f"this server takes requests from other machines over HTTPS: {to}"}, 421)
                return False
        if self.from_remote:
            given = remote.presented(self.headers, urlparse(self.path).query)
            self.push_relay = ""
            if urlparse(self.path).path == "/api/push/relay" and self.command == "POST":  # another machine's relay token
                from orbit.services import push
                self.push_relay = push.of(self.desk).relay_name(given)
            if not self.push_relay and not remote.valid(self.desk.state_dir, given):
                if self.command == "GET" and "text/html" in (self.headers.get("Accept") or ""):
                    self._unpaired_page()  # a browser (a phone) opened the address without its link
                else:
                    self._json({"error": "this server needs its token (on that machine: orbit url)"}, 401)
                return False
            origin = self.headers.get("Origin")
            if write and origin is not None and urlparse(origin).netloc != self.headers.get("Host", ""):
                self._json({"error": "forbidden origin"}, 403)
                return False
            return True
        host = self.headers.get("Host", "")
        if host not in self._allowed_hosts():
            self._json({"error": "forbidden host"}, 403)
            return False
        origin = self.headers.get("Origin")
        if write and origin is not None:
            ok = {f"http://{h}" for h in self._allowed_hosts()} | {"tauri://localhost", "http://tauri.localhost"}
            if origin not in ok:
                self._json({"error": "forbidden origin"}, 403)
                return False
        return True

    # ------------------------------------------------------------------ plugins
    def _calling_plugin(self) -> str:
        """The plugin making this request (its ORBIT_PLUGIN_TOKEN in X-Orbit-Plugin-Token), or ''."""
        from orbit import plugins
        return plugins.TOKENS.get(self.headers.get("X-Orbit-Plugin-Token", ""), "") if not self.from_remote else ""

    def _origin(self, o: Any) -> dict | None:
        """A request's origin as sent ({"label", "url"?, "data"?}), checked; the calling plugin's name added."""
        if not isinstance(o, dict):
            return None
        out: dict[str, Any] = {"label": str(o.get("label") or "")[:80]}
        url = str(o.get("url") or "")
        if url.startswith(("https://", "http://", "slack://")) and len(url) <= 1000:
            out["url"] = url
        if "data" in o:
            data = json.dumps(o["data"])
            if len(data) > 8000:
                raise ValueError("origin.data: 8000 characters of JSON at most")
            out["data"] = o["data"]
        out["plugin"] = self._calling_plugin() or str(o.get("plugin") or "")[:40]
        return out if out["label"] or out["plugin"] else None

    def _plugin_action(self, name: str, action: str, b: dict) -> None:
        from orbit import plugins
        from orbit.services import plugin_services
        if name not in plugins.LOADED:
            return self._json({"error": f"no plugin {name} (loaded)"}, 404)
        if action == "secret":  # set (or with value null, forget) one of its secrets; its services restart with it
            v = b.get("value")
            plugins.set_secret(name, str(b.get("key", "")), None if v is None else str(v))
            plugin_services.restart(name)
            return self._json({"ok": True, "plugins": plugins.public()})
        if action == "restart":
            return self._json({"services": plugin_services.restart(name, str(b.get("service") or ""))})
        if action == "status":  # a status-bar item (text "" removes it): every page shows it at once
            items = plugins.set_status(name, b)
            self.desk.broadcast({"t": "plugin-status", "status": items})
            return self._json({"status": items})
        if action == "badge":  # a count on one of its left-panel tabs (API 1.1)
            bs = plugins.set_badge(name, str(b.get("id") or ""), int(b.get("count") or 0), str(b.get("level") or "info"))
            self.desk.broadcast({"t": "plugin-badges", "badges": bs})
            return self._json({"badges": bs})
        if action == "open-tab":  # open (or select) one of its main tabs in this machine's pages
            kind, key = str(b.get("kind") or ""), str(b.get("key") or "")
            plugins.tab_kind(name, kind)
            if not key or len(key) > 300:
                raise ValueError("key: 1–300 characters")
            self.desk.broadcast({"t": "plugin-open-tab", "plugin": name, "kind": kind, "key": key,
                                 "title": str(b.get("title") or "")[:80], "focus": bool(b.get("focus", True))})
            return self._json({"ok": True})
        if action == "notify":  # the bell list of every page, and a macOS notification; a click opens its tab
            o = b.get("open") if isinstance(b.get("open"), dict) else None
            if o:
                plugins.tab_kind(name, str(o.get("kind") or ""))
            note = {"plugin": name, "title": str(b.get("title") or name)[:120], "body": str(b.get("body") or "")[:400],
                    "time": time.time(), **({"open": {"kind": str(o["kind"]), "key": str(o.get("key") or ""),
                                                      "title": str(o.get("title") or "")[:80]}} if o else {})}
            self.desk.broadcast({"t": "plugin-notify", "note": note})
            from orbit.desk.meetings import macos_notify
            macos_notify(note["title"], note["body"])
            return self._json({"ok": True})
        args = b.get("args") or []
        if not isinstance(args, list):
            return self._json({"error": "args: a list"}, 400)
        return self._json(plugins.run_cli(name, [str(a) for a in args], str(b.get("stdin") or ""),
                                          str(b.get("session") or "")))

    def _items(self, name: str, source: str, b: dict, whole: bool) -> None:
        """(1.3) PUT (the whole list) or POST (upsert, remove) a plugin's item source: only that plugin, by its token."""
        from orbit import plugins
        from orbit.services import plugin_items
        if self.from_remote:
            return self._json({"error": "only on the server's own machine"}, 403)
        if name not in plugins.LOADED:
            return self._json({"error": f"no plugin {name} (loaded)"}, 404)
        if self._calling_plugin() != name:
            return self._json({"error": f"only {name} writes its items: send its X-Orbit-Plugin-Token "
                                        "($ORBIT_PLUGIN_TOKEN)"}, 403)
        try:
            if whole:
                return self._json(plugin_items.put(name, source, b.get("items")))
            return self._json(plugin_items.change(name, source, b.get("upsert"), b.get("remove")))
        except ValueError as e:
            return self._json({"error": str(e)}, 400)

    def _palette_search(self, qs: dict) -> None:
        """(1.3) GET /api/palette/search?plugin=&source=&q=: {"items"} from the plugin's service, or an error with
        {"items": []} (503 down, 504 too slow, 502 a bad answer)."""
        from orbit.services import plugin_items
        q = qs.get("q", "").strip()
        if len(q) < 2:
            return self._json({"items": []})
        try:
            return self._json({"items": plugin_items.search(qs.get("plugin", ""), qs.get("source", ""), q[:200])})
        except KeyError:
            return self._json({"error": f"no plugin {qs.get('plugin', '')} (loaded)", "items": []}, 404)
        except ValueError as e:
            return self._json({"error": str(e), "items": []}, 404)
        except plugin_items.SearchError as e:
            return self._json({"error": str(e), "items": []}, e.code)

    def _people(self, names: Any) -> None:
        """/api/people (plugin API 1.2): who these names are, from the plugins' [people]; this machine only."""
        from orbit import plugins
        if self.from_remote:  # (it runs the plugins' commands)
            return self._json({"error": "only on the server's own machine"}, 403)
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            return self._json({"error": "names: a list of strings"}, 400)
        return self._json({"people": plugins.people(names)})

    # ------------------------------------------------------------------ native views (plugin API 2.0)
    VIEW_RE = r"/api/plugins/([a-z0-9_-]+)/(threads|docs)/(?:([a-z0-9_-]+)/)?([^/]+)"

    def _view_route(self, p: str) -> tuple[str, str, str, str] | None:
        """(plugin, "threads" | "docs", kind, key) of …/threads/KIND/KEY or …/docs/ID (the key URL-encoded: a / as
        %2F), else None."""
        from urllib.parse import unquote
        m = re.fullmatch(self.VIEW_RE, p)
        if not m or (m.group(2) == "threads") != bool(m.group(3)):
            return None
        return m.group(1), m.group(2), m.group(3) or "", unquote(m.group(4))

    def _push_view(self, route: tuple[str, str, str, str], whole: bool) -> None:
        """PUT (the whole thread or doc) or PATCH (a thread's changes): only that plugin, by its token; its declared
        kinds only; a body of plugin_views.BODY_MAX at most."""
        from orbit import plugins
        from orbit.services import plugin_views
        name, what, kind, key = route
        if self.from_remote:
            return self._json({"error": "only on the server's own machine"}, 403)
        if name not in plugins.LOADED:
            return self._json({"error": f"no plugin {name} (loaded)"}, 404)
        if self._calling_plugin() != name:
            return self._json({"error": f"only {name} writes its {what}: send its X-Orbit-Plugin-Token "
                                        "($ORBIT_PLUGIN_TOKEN)"}, 403)
        b = self._body(plugin_views.BODY_MAX)
        if b is None:
            return
        try:
            if what == "docs":
                if not whole:
                    return self._json({"error": "a doc is PUT whole"}, 405)
                return self._json(plugin_views.put_doc(name, key, b))
            return self._json(plugin_views.put_thread(name, kind, key, b) if whole else
                              plugin_views.patch_thread(name, kind, key, b))
        except plugin_views.Stale as e:
            return self._json({"error": str(e)}, 409)
        except ValueError as e:
            return self._json({"error": str(e)}, 400)

    def _get_view(self, route: tuple[str, str, str, str], qs: dict) -> None:
        """GET a thread or a doc (from the store at once; asked for in the background), or ?before=ID: older
        messages, through the service."""
        from orbit.services import plugin_views
        name, what, kind, key = route
        try:
            if what == "docs":
                return self._json(plugin_views.get_doc(name, key, fresh=qs.get("fresh") == "1"))
            if qs.get("before"):
                try:
                    return self._json(plugin_views.older(name, kind, key, qs["before"][:300]))
                except ValueError as e:
                    if "declares no" in str(e):
                        raise
                    return self._json({"error": str(e), "messages": [], "more": False}, 502)
            return self._json(plugin_views.get_thread(name, kind, key, fresh=qs.get("fresh") == "1"))
        except KeyError:
            return self._json({"error": f"no plugin {name} (loaded)"}, 404)
        except ValueError as e:
            return self._json({"error": str(e)}, 404)

    def _svc(self, name: str, raw: str, query: str) -> None:
        """GET / HEAD /api/plugins/NAME/svc/PATH: a file of the plugin's service (a Slack picture it can fetch with
        its login), served as inert content only (builddoc/plugin-views.md §8.1-8.2): http.client to 127.0.0.1, only
        under its declared `files` prefix, never /orbit/, no redirect followed, only Range forwarded, PROXY_MAX bytes
        in PROXY_TIMEOUT s, PROXY_AT_ONCE at once per plugin."""
        import http.client
        import socket
        from orbit.services import plugin_views as pv
        head_only = self.command == "HEAD"
        try:
            port, token, path, sem = pv.proxy_target(name, raw, query)
        except KeyError:
            return self._json({"error": f"no plugin {name} (loaded)"}, 404)
        except pv.ProxyError as e:
            return self._json({"error": str(e)}, e.code)
        deadline = time.time() + pv.PROXY_TIMEOUT
        if not sem.acquire(timeout=pv.PROXY_TIMEOUT):
            return self._json({"error": f"{name}: {pv.PROXY_AT_ONCE} files on their way already"}, 503)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=pv.PROXY_TIMEOUT)
        sent = False
        try:
            hdrs = {"X-Orbit-Proxy": "1", "X-Orbit-Plugin-Token": token, "Accept-Encoding": "identity"}
            rng = self.headers.get("Range") or ""
            if rng and len(rng) < 200 and re.fullmatch(r"bytes=[0-9, -]+", rng):
                hdrs["Range"] = rng
            conn.request(self.command, path, headers=hdrs)
            sock = conn.sock  # (the answer reads from it; the connection lets go of it once it says close)
            r = conn.getresponse()
            if 300 <= r.status < 400:
                return self._json({"error": f"{name}'s service answered a redirect ({r.status}): not followed"}, 502)
            if r.status not in (200, 206):
                return self._json({"error": f"{name}'s service answered {r.status}"}, r.status if r.status < 500 else 502)
            length = r.getheader("Content-Length")
            n = int(length) if length and length.isdigit() else None
            if n is not None and n > pv.PROXY_MAX:
                return self._json({"error": f"larger than {pv.PROXY_MAX // 1024 // 1024} MB"}, 502)
            body = b""
            if not head_only:  # the first bytes say what it is; without a length, all of it (up to the cap)
                while (n is None or len(body) < min(n, pv.SNIFF)) and time.time() < deadline:
                    sock.settimeout(max(0.05, deadline - time.time()))
                    chunk = r.read1(65536) if n is None or len(body) < pv.SNIFF else b""
                    if not chunk:
                        break
                    body += chunk
                    if len(body) > pv.PROXY_MAX:
                        return self._json({"error": f"larger than {pv.PROXY_MAX // 1024 // 1024} MB"}, 502)
                if time.time() >= deadline:
                    return self._json({"error": f"{name}'s service: not within {pv.PROXY_TIMEOUT:g} s"}, 504)
            ctype, attach = pv.inert_type(None if head_only else body[:pv.SNIFF], r.getheader("Content-Type") or "")
            self.send_response(r.status)
            self.send_header("Content-Type", ctype)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "sandbox; default-src 'none'; style-src 'unsafe-inline'"
                             if ctype == "image/svg+xml" else "sandbox")
            if attach:
                fname = re.sub(r"[^\w.-]+", "_", raw.rstrip("/").split("/")[-1])[:100] or "file"
                self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            total = n if n is not None else len(body)
            self.send_header("Content-Length", str(total))
            for h in ("ETag", "Cache-Control") + (("Content-Range",) if r.status == 206 else ()):
                v = r.getheader(h)
                if v and "\n" not in v and "\r" not in v:
                    self.send_header(h, v)
            self.end_headers()
            sent = True
            if head_only:
                return
            self.wfile.write(body)
            got = len(body)
            while n is not None and got < n:
                if time.time() >= deadline:
                    raise TimeoutError
                sock.settimeout(max(0.05, deadline - time.time()))
                chunk = r.read1(min(65536, n - got))
                if not chunk:
                    break
                got += len(chunk)
                self.wfile.write(chunk)
        except (OSError, http.client.HTTPException, socket.timeout) as e:
            if not sent:
                late = isinstance(e, (TimeoutError, socket.timeout))
                return self._json({"error": f"{name}'s service: " + (f"not within {pv.PROXY_TIMEOUT:g} s" if late else
                                                                      str(e) or "no answer")}, 504 if late else 502)
            self.close_connection = True  # (cut off: the client sees it short)
        finally:
            conn.close()
            sem.release()

    def _view_action(self, route: tuple[str, str, str, str], action: str, b: dict) -> None:
        """POST …/threads/KIND/KEY/send {markdown, files, client_id} or …/discard {client_id}: a reply from the page,
        this machine's or another's (the phone: token-checked by _guard, so the user). Another machine's files are
        only its uploads (it has no other path here); the plugin's own actions stay this machine's."""
        from orbit.services import plugin_views
        name, _, kind, key = route
        if self.from_remote and action == "send":
            up = (Path(self.desk.state_dir) / "uploads").resolve()
            for f in b.get("files") or []:
                p = f.get("path") if isinstance(f, dict) else None
                if not isinstance(p, str) or up not in Path(p).resolve().parents:
                    return self._json({"error": "files: another machine sends only what it uploaded (/api/upload)"}, 400)
        try:
            if action == "send":
                return self._json(plugin_views.send(name, kind, key, b.get("markdown"), b.get("files") or [],
                                                    b.get("client_id")))
            return self._json(plugin_views.discard(name, kind, key, b.get("client_id")))
        except KeyError:
            return self._json({"error": f"no plugin {name} (loaded)"}, 404)
        except ValueError as e:
            return self._json({"error": str(e)}, 400)

    # ------------------------------------------------------------------ helpers
    def _relay(self, b: dict) -> None:
        """POST /api/relay {from, target, text}: the words, if they are the user's own (said in a voice call to `from`,
        or typed there), as a request in `target`, alone (orbit/desk/relay.py has the rules and the trust boundary)."""
        from orbit.desk import relay
        try:
            text = relay.check_text(b.get("text"))
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        src, target = str(b.get("from") or ""), str(b.get("target") or "")
        s = self.desk.sessions.get(src)
        if s is None:
            return self._json({"error": "from: the relaying session (yours: $ORBIT_SESSION)"}, 404)
        if target == src:
            return self._json({"error": "that is this session"}, 400)
        proof = relay.find_proof(self.desk, src, text)
        rem = federation.split(target)
        if rem:  # another machine's session: that server delivers (and looks itself if nothing was found here)
            if not self.desk.federation:
                return self._json({"error": "no other machines here"}, 404)
            body = {"from": src, "from_name": s.name, "from_machine": relay.host(), "target": rem[1], "text": text}
            if proof:
                body.update(proof=proof, client_id=relay.client_id(src, target, text, proof))
            code, r = self.desk.federation.call(rem[0], "POST", "/api/relay/forward", body)
            return self._json(r, code)
        if target not in self.desk.sessions:
            return self._json({"error": "no such session (target)"}, 404)
        if not proof:
            return self._json({"error": relay.NOT_THEIRS}, 403)
        return self._json(relay.deliver(self.desk, target, text, {"sid": src, "name": s.name}, proof))

    def _relay_forward(self, b: dict) -> None:
        """POST /api/relay/forward: a relay from the server of another machine (with this one's token; never this
        machine's own processes, which use /api/relay and its check). Its proof is taken as that server's (marked
        verified_by it); without one, the words must be found here (a call here to that machine's session)."""
        from orbit.desk import relay
        if not self.from_remote:
            return self._json({"error": "only from another machine's server: here, use /api/relay"}, 403)
        try:
            text = relay.check_text(b.get("text"))
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        src, target = str(b.get("from") or "")[:200], str(b.get("target") or "")
        if target not in self.desk.sessions:
            return self._json({"error": "no such session (target)"}, 404)
        machine = str(b.get("from_machine") or "")[:60]
        proof, verified_by = b.get("proof"), ""
        if isinstance(proof, dict) and proof.get("kind") in ("voice", "typed"):
            proof = {k: proof[k] for k in ("kind", "call", "delegation", "card", "at") if k in proof}
            verified_by = machine or "another machine"
        else:
            proof = relay.find_proof(self.desk, src, text)
        if not proof:
            return self._json({"error": relay.NOT_THEIRS}, 403)
        cid = b.get("client_id") if isinstance(b.get("client_id"), str) and b["client_id"].startswith("relay:") else ""
        info = {"sid": src, "name": str(b.get("from_name") or "")[:120], "machine": machine}
        return self._json(relay.deliver(self.desk, target, text, info, proof, verified_by=verified_by, cid=cid))

    def _show_page(self, data: bytes) -> None:
        """An agent's page (/show/<id>), sandboxed: it runs scripts but in its own origin (no Orbit API). Orbit's
        own keys still work in it (⌘⇧O back to the main tab, ⌘W, …): a small script passes them to the page around
        it, which takes only those (side.js); the page's own keys (⌘C, ⌘F, …) stay its own."""
        bridge = SHOW_KEYS.encode()
        if b"/* Orbit: its keys from this side tab */" in data[:8192]:
            bridge = b""  # (another machine's page: its server put it in already; twice would act twice)
        m = next((x for x in (re.search(pat, data[:4096], re.I) for pat in (rb"<head[^>]*>", rb"<html[^>]*>", rb"<!doctype[^>]*>")) if x), None)  # (in that order)
        data = data[:m.end()] + bridge + data[m.end():] if m else bridge + data
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "sandbox allow-scripts allow-popups allow-forms")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self, limit: int = 0) -> dict | None:
        """The JSON object sent ({} when none or not one); with a limit, None (and 413 answered) past it."""
        n = int(self.headers.get("Content-Length") or 0)
        if limit and n > limit:
            left = n if n <= 16 * limit else 0  # (read and dropped, so the client hears the answer; a huge one: cut)
            while left > 0:
                got = self.rfile.read(min(left, 65536))
                if not got:
                    break
                left -= len(got)
            self.close_connection = True
            self._json({"error": f"{n} bytes: a request is {limit} bytes at most"}, 413)
            return None
        if not n:
            return {}
        try:
            v = json.loads(self.rfile.read(n))
        except json.JSONDecodeError:
            return {}
        return v if isinstance(v, dict) else {}

    def _https_status(self, fresh: bool = False) -> dict:
        """HTTPS by Tailscale (machines/https.py), with the URLs that carry this server's token (this machine only)."""
        st = https.status(self.desk.state_dir, self.port, fresh=fresh)
        if st["on"]:
            tok = remote.token(self.desk.state_dir)
            st["open_url"] = f"{st['url']}/?token={tok}"
            st["phone_url"] = f"{st['url']}/m/?token={tok}"
        return st

    UNPAIRED = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Orbit</title><style>
body { margin:0; min-height:100vh; display:grid; place-items:center; background:#f9f9f8; color:#1d1d1f;
  font:16px/1.5 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif; }
@media (prefers-color-scheme: dark) { body { background:#18191b; color:#ececec; } p { color:#a8a8ad !important; } code { background:#2a2c31 !important; } }
main { max-width:340px; padding:32px 24px; text-align:center; }
img { width:72px; height:72px; border-radius:16px; }
h1 { font-size:21px; margin:16px 0 8px; }
p { color:#6b6b70; margin:0 0 12px; }
code { font:14px ui-monospace,Menlo,Consolas,monospace; background:#eeeeec; padding:1px 5px; border-radius:5px; }
</style></head><body><main><img src="/m/icon-192.png" alt="">
<h1>Orbit on {{HOST}}</h1>
<p>This address needs Orbit's link, once per phone or browser.</p>
<p>On that Mac, open Orbit → <b>Settings → Machines → iPhone…</b> and scan the code with the Camera.</p>
<p>Without the app there: <code>orbit https status</code> prints the link.</p></main></body></html>"""

    def _unpaired_page(self) -> None:
        host = (self.headers.get("Host") or "").split(":")[0]
        data = self.UNPAIRED.replace("{{HOST}}", html_escape(host.split(".")[0] or "this machine")).encode()
        self.send_response(401)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------------ the phone's app (web/m/, a PWA)
    def _mobile_token(self) -> str:
        """The token a page from the network carries (the home-screen app keeps its own storage on iOS: its manifest's
        start_url logs it in); "" on this machine."""
        return remote.presented(self.headers, urlparse(self.path).query) if self.from_remote else ""

    def _token_cookie(self) -> str:
        """The cookie that carries the token a browser presented (the page's fetches, events and images)."""
        return (f"{remote.COOKIE}={remote.presented(self.headers, urlparse(self.path).query)}; "
                "HttpOnly; SameSite=Strict; Path=/; Max-Age=31536000")

    def _mobile_page(self, cookie: bool = False) -> None:
        try:
            html = (WEB / "m" / "index.html").read_text()
        except OSError:
            return self._json({"error": "not found"}, 404)
        tok = self._mobile_token()
        data = html.replace("{{TOKEN_QS}}", ("?token=" + tok) if tok else "").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", self._token_cookie())
        self.end_headers()
        self.wfile.write(data)

    def _mobile_manifest(self) -> None:
        tok = self._mobile_token()
        host = (self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "").split(":")[0]
        machine = host.split(".")[0] if host and not host[0].isdigit() and host != "localhost" else "this Mac"
        doc = {"name": f"Orbit · {machine}", "short_name": "Orbit", "id": "/m/",
               "start_url": "/m/" + (f"?token={tok}" if tok else ""), "scope": "/m/", "display": "standalone",
               "background_color": "#f9f9f8", "theme_color": "#f9f9f8",
               "icons": [{"src": "/m/icon-192.png", "sizes": "192x192", "type": "image/png"},
                         {"src": "/m/icon-512.png", "sizes": "512x512", "type": "image/png"},
                         {"src": "/m/apple-touch-icon.png", "sizes": "180x180", "type": "image/png"}]}
        data = json.dumps(doc).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/manifest+json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path: Path, cache: bool, headers: dict | None = None) -> None:
        try:
            data = path.read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            self._json({"error": "not found"}, 404)
            return
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # Local and small: always revalidate, so a rebuilt bundle loads on the next ⌘⇧R.
        self.send_header("Cache-Control", "no-cache" if cache else "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------------ GET
    MOBILE_PUBLIC = ("/m/icon-192.png", "/m/icon-512.png", "/m/apple-touch-icon.png")

    def do_GET(self):
        path0 = urlparse(self.path).path
        if path0 in self.MOBILE_PUBLIC:  # (fetched by the phone without the cookie: nothing secret)
            return self._file(WEB / "m" / path0[3:], cache=True)
        m = re.fullmatch(r"/vendor/fonts/([\w-]+\.woff2)", path0)
        if m:  # the shipped fonts: a stylesheet's fetch carries no token (the phone's app may have no cookie yet)
            return self._file(WEB / "vendor" / "fonts" / m.group(1), cache=True)
        if not self._guard(write=False):
            return
        u = urlparse(self.path)
        p = u.path
        m = re.fullmatch(r"/api/remote/([a-z0-9_-]+)(/.*)", p)
        if m:
            return self._proxy(m.group(1), m.group(2), u.query)
        if p in FILE_ROUTES:
            q0 = {k: v[0] for k, v in parse_qs(u.query).items()}
            name = federation.machine_in(q0)
            if name:  # a file of another machine (/@NAME/...): that server reads it
                return self._remote_files(name, "GET", p, q0, None)
        m = re.fullmatch(r"/api/shell/(\w[\w-]*~\w+)/stream", p)
        if m:  # another machine's shell: its stream comes through
            name, shid = federation.split(m.group(1))
            return self._proxy(name, f"/api/shell/{shid}/stream", "")
        if p == "/m":  # the phone's app lives under /m/ (its service worker's scope), with the query as it is
            self.send_response(301)
            self.send_header("Location", "/m/" + ("?" + u.query if u.query else ""))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if p == "/m/" and self.from_remote and "token=" in u.query:
            # The phone's link (also its home-screen app's start_url): the page keeps the token itself (the home-screen
            # app has its own storage, where the cookie may not be yet) and takes it out of the address bar.
            return self._mobile_page(cookie=True)
        if p in ("/", "/index.html", "/m/"):
            if self.from_remote and "token=" in u.query:
                # A browser opened the URL with ?token=: a cookie carries it from now on (also for the page's
                # fetches, events and images), and the token leaves the address bar.
                self.send_response(302)
                self.send_header("Set-Cookie", self._token_cookie())
                rest = urlencode([(k, v) for k, v in parse_qsl(u.query) if k != "token"])
                self.send_header("Location", ("/m/" if p.startswith("/m") else "/") + ("?" + rest if rest else ""))
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if p.startswith("/m"):
                return self._mobile_page()
            return self._file(WEB / "index.html", cache=False)
        if p == "/m/manifest.webmanifest":
            return self._mobile_manifest()
        m = re.fullmatch(r"/m/([\w.-]+)", p)
        if m:  # the phone's app (web/m/): its scripts, styles, icons, service worker
            target = WEB / "m" / m.group(1)
            extra = {"Service-Worker-Allowed": "/m/"} if m.group(1) == "sw.js" else None
            return self._file(target, cache=True, headers=extra)
        m = re.fullmatch(r"/(vendor|styles|src)/(.+)", p)
        if m:  # the page's files: vendored libraries, its styles and scripts (read from disk: ⌘⇧R loads edits)
            base = (WEB / m.group(1)).resolve()
            target = (base / m.group(2)).resolve()
            if not str(target).startswith(str(base) + os.sep):
                return self._json({"error": "not found"}, 404)
            return self._file(target, cache=m.group(1) == "vendor")
        m = re.fullmatch(r"/plugins/([a-z0-9_-]+)/(ui/[\w./-]+)", p)
        if m:  # a plugin harness's icon (its ui/ folder): the only file of a plugin served (2.0: no plugin pages)
            from orbit import plugins
            f = plugins.harness_icon(m.group(1), m.group(2))
            return self._file(f, cache=False, headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy":
                                                       "sandbox; default-src 'none'; style-src 'unsafe-inline'"}) \
                if f else self._json({"error": "not found"}, 404)
        m = re.fullmatch(r"/api/plugins/([a-z0-9_-]+)/svc(/.*)", p)
        if m:  # (2.0) a file of the plugin's service, inert
            return self._svc(m.group(1), m.group(2), u.query)
        route = self._view_route(p)
        if route:  # (2.0) a thread or a doc a plugin keeps
            return self._get_view(route, {k: v[0] for k, v in parse_qs(u.query).items()})
        m = re.fullmatch(r"/show/(\w+)", p)
        if m:
            target = self.desk.show_path(m.group(1))
            if not target:
                return self._json({"error": "not found"}, 404)
            return self._show_page(target.read_bytes())
        if p == "/api/ctx":
            return self._json(self.desk.get_ctx())
        if p == "/api/schedules":
            return self._json({"schedules": self.desk.scheduler.list(), "archived": self.desk.scheduler.list_archived(), "now": time.time()})
        m = re.fullmatch(r"/api/schedules/([\w-]+)/journal", p)
        if m:  # a job's journal, also a removed one's (the Schedules cards' Task and journal)
            try:
                return self._json({"text": self.desk.scheduler.journal_text(m.group(1))})
            except KeyError:
                return self._json({"error": "no such schedule"}, 404)
        if p == "/api/push":  # the phone's notifications: the VAPID public key, the devices, the last sends
            from orbit.services import push
            return self._json(push.of(self.desk).status())
        if p == "/api/version":
            from orbit import API_VERSION, __version__
            return self._json({"version": __version__, "api": f"{API_VERSION[0]}.{API_VERSION[1]}"})
        if p == "/api/settings":
            return self._json(self.desk.settings)
        if p == "/api/keys":  # Orbit's own API keys: whether each is set and its last four characters (this machine only)
            if self.from_remote:
                return self._json({"error": "only on the server's own machine"}, 403)
            from orbit.services import keys
            return self._json({"keys": keys.status(self.desk.state_dir)})
        if p == "/api/live/preview":  # Settings → Voice: a voice saying hello (made once, then kept; this machine only)
            if self.from_remote:
                return self._json({"error": "only on the server's own machine"}, 403)
            from orbit.services.live import preview as live_preview
            voice = (parse_qs(u.query).get("voice") or [""])[0]
            try:
                f = live_preview.preview(self.desk.state_dir, self.desk.settings, voice)
            except PermissionError as e:
                return self._json({"error": str(e), "need": "openai-key"}, 400)
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            except (RuntimeError, OSError) as e:
                return self._json({"error": f"no preview: {e}"}, 502)
            return self._file(f, cache=True)
        if p == "/api/live/options":  # Settings → Voice: what each setting may be
            from orbit.services.live import config as live_config
            return self._json({"voices": [{"name": v, "about": a} for v, a in live_config.VOICES],
                               "languages": live_config.LANGUAGES, "models": live_config.MODELS,
                               "wake": live_config.WAKE, "prompt_max": live_config.PROMPT_MAX})
        if p == "/api/live/usage":  # what the voice calls cost: this month, and the balance left if the user gave one
            from orbit.services.live import usage as live_usage
            return self._json(live_usage.summary(self.desk.state_dir, self.desk.settings))
        m = re.fullmatch(r"/api/live/watch/(call-[0-9a-f]+)/down", p)
        if m:  # the watch's call: the voice's audio and the call's events, as frames (server/watch.py)
            return self._watch_down(m.group(1))
        if p == "/api/live":  # the voice call: phase, bound session, transcript so far (any client with the token: the phone)
            return self._json(self.desk.live.state())
        if p == "/api/term-font":  # the font of iTerm's default profile, for the ⌘J terminal ({} without iTerm)
            from orbit.services import termfont
            return self._json(termfont.public(termfont.iterm_font()))
        if p in ("/api/term-font/file/regular", "/api/term-font/file/bold"):  # its file (only the one found for it)
            from orbit.services import termfont
            f = termfont.font_file(p.rsplit("/", 1)[1])
            if not f:
                return self._json({"error": "no such font file"}, 404)
            return self._file(f, cache=True, headers={"Content-Type": "font/otf" if f.suffix.lower() == ".otf" else "font/ttf"})
        if p == "/api/harnesses":
            return self._json({"harnesses": self.desk.harnesses()})
        if p == "/api/harness-server":  # whether the agents run in the Harness Server (or directly, and why)
            if self.from_remote:
                return self._json({"error": "only on the server's own machine"}, 403)
            return self._json(self.desk.harness_status())
        if p == "/api/plugins":  # the plugins found, loaded or not (Settings → Plugins, `orbit plugin list`)
            from orbit import plugins
            from orbit import API_VERSION
            return self._json({"api": f"{API_VERSION[0]}.{API_VERSION[1]}", "plugins": plugins.public(),
                               "local": not self.from_remote})
        if p == "/api/plugin-ui":  # what the plugins add to the page: status items, side-pane pages, actions
            from orbit import plugins
            return self._json({**plugins.ui(), "local": not self.from_remote})
        if p == "/api/people":  # ?names=A,B (or names= repeated): who they are, from the plugins (1.2)
            return self._people([n for v in parse_qs(u.query).get("names", []) for n in v.split(",")])
        if p == "/api/palette":  # (1.3) the plugins' item sources with their items (⌘O); ?plugin=&source=: one
            from orbit.services import plugin_items
            qs = {k: v[0] for k, v in parse_qs(u.query).items()}
            return self._json({"sources": plugin_items.palette(qs.get("plugin", ""), qs.get("source", ""),
                                                               fresh=qs.get("fresh") == "1"),
                               "local": not self.from_remote})
        if p == "/api/palette/search":  # (1.3) ?plugin=&source=&q=: a live search, answered by the plugin's service
            return self._palette_search({k: v[0] for k, v in parse_qs(u.query).items()})
        m = re.fullmatch(r"/api/plugins/([a-z0-9_-]+)/log", p)
        if m:  # a service's log (the end of it)
            if self.from_remote:
                return self._json({"error": "only on the server's own machine"}, 403)
            from orbit.services import plugin_services
            qs = {k: v[0] for k, v in parse_qs(u.query).items()}
            svcs = [s for s in plugin_services.of(m.group(1)) if s.id == qs.get("service", s.id)]
            if not svcs:
                return self._json({"error": "no such service"}, 404)
            return self._json({"service": svcs[0].id, "log": svcs[0].tail(max(1, min(2000, int(qs.get("lines", "200")))))})
        m = re.fullmatch(r"/api/sessions/([\w-]+)/cards/(\d+(?:\.\d+)?)", p)
        if m:  # one card; ?wait=S: once it is final (following a paused request to its continuation), or after S s;
            # ?exact=1: that card as it is now, whatever its id (a client that missed a card patch)
            qs = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if qs.get("exact") == "1" or "." in m.group(2):
                    return self._json({"card": self.desk.card(m.group(1), float(m.group(2)))})
                return self._json(self.desk.wait_card(m.group(1), int(m.group(2)), float(qs.get("wait", "0"))))
            except KeyError:
                return self._json({"error": "no such session or request"}, 404)
        if p == "/api/resolve":  # a file an answer names → its absolute path (or null)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            hit = files.resolve(q.get("ref", ""), q.get("cwd", ""))
            root = files.git_root(Path(hit)) if hit else None
            return self._json({"path": hit, "root": str(root) if root else None})
        if p == "/api/badge":
            return self._json({"count": self.desk.badge})
        if p == "/api/fonts":
            return self._json({"families": font_families()})
        if p == "/api/calendar":
            if not self.desk.settings.get("calendar"):
                return self._json({"enabled": False, "events": []})
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                frm, to = float(q["from"]), float(q["to"])
            except (KeyError, ValueError):
                return self._json({"error": "from and to (epoch seconds) are required"}, 400)
            return self._json({"enabled": True, **self.desk.calendar.events(frm, to, fresh=q.get("fresh") == "1")})
        if p == "/api/events":
            return self._sse()
        if p == "/api/fleet":
            return self._json({"fleet": self.desk.fleet()})
        if p == "/api/shells":
            return self._json({"shells": self.desk.shells.list()})
        m = re.fullmatch(r"/api/shell/(\w+)/stream", p)
        if m:
            return self._shell_stream(m.group(1))
        if p == "/api/sessions":
            qs = parse_qs(u.query)
            closed = qs.get("closed", ["0"])[0] == "1"
            if qs.get("local", ["0"])[0] == "1":  # another server asking: this machine's only (one hop, no loops)
                return self._json({"sessions": self.desk.list(closed=closed), "groups": self.desk.groups, "modes": MODES})
            return self._json({"sessions": self.desk.all_sessions(closed=closed), "groups": self.desk.all_groups(),
                               "modes": MODES, "machines": self.desk.federation.machines() if self.desk.federation else []})
        m = re.fullmatch(r"/api/sessions/(\w[\w-]*~[\w-]+)/(cards(?:/\d+(?:\.\d+)?)?|resume-command)", p)
        if m:  # a session on another machine (all its cards, or one; its resume command, for a terminal there)
            fed = federation.split(m.group(1))
            return self._json(*reversed(self.desk.federation.call(fed[0], "GET", f"/api/sessions/{fed[1]}/{m.group(2)}"
                                                                  + (f"?{u.query}" if u.query else ""))))
        if p == "/api/layout":  # the tab layout every screen shares (desk/layout.py)
            return self._json(self.desk.layout())
        m = re.fullmatch(r"/api/sessions/([\w-]+(?:~[\w-]+)?)/side", p)
        if m:  # its side panel (desk/sides.py); another machine's: from there
            fed = federation.split(m.group(1))
            if fed:
                return self._json(*reversed(self.desk.federation.side(fed[0], fed[1])))
            try:
                return self._json(self.desk.side_state(m.group(1)))
            except KeyError:
                return self._json({"error": "no such session"}, 404)
        m = re.fullmatch(r"/api/remote-show/([\w-]+)/(\w+)", p)
        if m:  # an agent's page on another machine (its side tab here): fetched from there, as sandboxed
            st, data = self.desk.federation.raw(m.group(1), f"/show/{m.group(2)}")
            if st != 200:
                return self._json({"error": "not found"}, st if st in (404, 502) else 502)
            return self._show_page(data)
        m = re.fullmatch(r"/api/sessions/([\w-]+)/resume-command", p)
        if m:  # Copy resume command: the session's conversation, continued in a terminal
            try:
                return self._json(self.desk.resume_command(m.group(1)))
            except KeyError:
                return self._json({"error": "no such session"}, 404)
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
        m = re.fullmatch(r"/api/sessions/([\w-]+)/cards", p)
        if m:
            try:
                return self._json({"cards": self.desk.cards(m.group(1))})
            except KeyError:
                return self._json({"error": "no such session"}, 404)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if p == "/api/tree":
                return self._json(files.tree(q.get("root", "")))
            if p == "/api/sharing":  # Settings → Machines: whether other machines reach this server, and its URLs
                if self.from_remote:
                    return self._json({"error": "only on the server's own machine"}, 403)
                st = self.sharing.status()
                st["urls"] = [remote.url(self.desk.state_dir, h, st["port"]) for h in st["addresses"]] if st["on"] else []
                st["https"] = self._https_status(fresh=q.get("fresh") == "1")
                return self._json(st)
            if p == "/api/remotes":  # the servers on other machines this one reaches (and their sessions)
                return self._json({"remotes": self.desk.federation.status() if self.desk.federation
                                   else remote.status(self.desk.state_dir)})
            if p == "/api/usage":  # how much of each harness's plan is used (the status bar's "Used")
                return self._json(USAGE.get())
            if p == "/api/external-sessions":  # conversations to resume that are no session here (every machine's)
                # (?q=: the ones that match, best first, from the whole index; else the newest)
                n = max(1, min(500, int(q.get("limit", "40"))))
                text = q.get("q", "").strip()[:200]
                found = self.desk.external(n, text)
                if self.desk.federation and q.get("local") != "1":  # another server asking: this machine's only
                    theirs = self.desk.federation.external(n, text)
                    if text:
                        from orbit.desk.external_index import search
                        labels = {h: getattr(c, "label", h) for h, c in DRIVERS.items()}
                        found = search(found + theirs, text, labels, n)
                    else:
                        found = sorted(found + theirs, key=lambda x: -x["time"])
                return self._json({"sessions": found, "pending": self.desk.external_index.pending()})
            if p == "/api/file-root":  # the tree root for a file outside the session's folder: its git repo, else its folder
                fp = files._abs(q.get("path", ""))
                g = files.git_root(fp.parent if not fp.is_dir() else fp)
                return self._json({"root": str(g) if g else str(fp.parent)})
            if p == "/api/skills":  # `/` in the input: the skills and commands an agent in that folder can run
                return self._json({"skills": skills.list_skills(q.get("cwd", ""), q.get("harness", "claude"))})
            if p == "/api/file":
                return self._json(files.read(q.get("path", "")))
            if p == "/api/dirlist":
                return self._json(files.dirlist(q.get("path", "")))
            if p == "/api/findfiles":  # ⌘O → All Files: file names in every open session's folder and the home, here
                # and (unless ?local=1: another server asking) on every machine this one reaches, their paths /@NAME/…
                text = q.get("q", "").strip()[:200]
                n = max(1, min(500, int(q.get("limit", str(findfiles.LIMIT)))))
                roots = [s.cwd for s in list(self.desk.sessions.values()) if not s.closed and not s.fleet]
                found = findfiles.find(text, roots, n)
                found["unreachable"] = []
                if self.desk.federation and q.get("local") != "1" and text:
                    theirs, down = self.desk.federation.find_files(text, n)
                    found["files"] = findfiles.rank(found["files"] + theirs, text)[:n]
                    found["unreachable"] = down
                return self._json(found)
            if p == "/api/search":
                text = q.get("q", "")
                terms = [t.lower() for t in text.split()]
                limit = max(1, min(200, int(q.get("limit", "60"))))
                return self._json({"q": text, "cards": self.desk.search(text, limit),
                                   "notes": notes.search(terms, limit) if terms else []})
            if p == "/api/notes/info":
                kind = notes.journal_kind()  # ⌘⇧D: Obsidian's daily note, else this week's
                return self._json({"root": str(notes.root()), "journal": {"kind": kind, "label": "Today" if kind == "day" else "This week"}})
            if p == "/api/notes/list":
                return self._json({"root": str(notes.root()), "notes": notes.all_notes()})
            if p == "/api/notes/resolve":
                hit = notes.resolve(q.get("name", ""), q.get("from") or None)
                return self._json({"path": hit, "exists": bool(hit)})
            if p == "/api/notes/backlinks":
                return self._json({"links": notes.backlinks(q.get("path", ""))})
            if p == "/api/review":
                return self._json(files.review(q.get("root", ""), summary=q.get("summary") == "1"))
            if p in ("/api/raw", "/api/embed"):
                if p == "/api/embed":  # ![[name]] in a note: resolved like Obsidian, then served like /api/raw
                    hit = notes.resolve_file(q.get("name", ""), q.get("from") or None)
                    if not hit:
                        return self._json({"error": "not found"}, 404)
                    target = files._abs(hit)
                else:
                    target = files._abs(q.get("path", ""))
                if not target.is_file():
                    return self._json({"error": "not found"}, 404)
                data = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", files.mime(target))
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                if files.mime(target) in ("image/svg+xml", "text/html"):
                    self.send_header("Content-Security-Policy", "sandbox")  # a served SVG / HTML cannot run script
                self.end_headers()
                self.wfile.write(data)
                return
        except FileNotFoundError as e:
            return self._json({"error": f"no such file: {e}"}, 404)
        except (ValueError, RuntimeError, OSError) as e:
            return self._json({"error": str(e)}, 400)
        if p == "/api/complete-dir":
            q = parse_qs(u.query).get("q", [""])[0]
            return self._json({"dirs": complete_dir(q)})
        self._json({"error": "not found"}, 404)

    # ------------------------------------------------------------------ PUT
    def do_PUT(self):
        if not self._guard(write=True):
            return
        p = urlparse(self.path).path
        m = re.fullmatch(r"/api/plugins/([a-z0-9_-]+)/items/([a-z0-9_-]+)", p)
        if m:  # (1.3) one of its item sources, the whole list: {"items": [...]}
            return self._items(m.group(1), m.group(2), self._body(), whole=True)
        route = self._view_route(p)
        if route:  # (2.0) a thread or a doc, whole
            return self._push_view(route, whole=True)
        self._json({"error": "not found"}, 404)

    # ------------------------------------------------------------------ PATCH
    def do_PATCH(self):
        if not self._guard(write=True):
            return
        route = self._view_route(urlparse(self.path).path)
        if route:  # (2.0) a thread's changes: {"append", "update", "remove", "title", …}
            return self._push_view(route, whole=False)
        self._json({"error": "not found"}, 404)

    # ------------------------------------------------------------------ HEAD
    def do_HEAD(self):
        if not self._guard(write=False):
            return
        u = urlparse(self.path)
        m = re.fullmatch(r"/api/plugins/([a-z0-9_-]+)/svc(/.*)", u.path)
        if m:  # (2.0) the svc: proxy answers HEAD too
            return self._svc(m.group(1), m.group(2), u.query)
        self.send_response(405)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ------------------------------------------------------------------ POST
    def do_POST(self):
        if not self._guard(write=True):
            return
        u = urlparse(self.path)
        p = u.path
        m = re.fullmatch(r"/api/remote/([a-z0-9_-]+)(/.*)", p)
        if m:
            return self._proxy(m.group(1), m.group(2), u.query, method="POST")
        if re.fullmatch(r"/api/plugins/([a-z0-9_-]+)/svc(/.*)", p):  # (the proxy is not a gateway: GET and HEAD only)
            return self._json({"error": "the svc: proxy takes GET and HEAD only"}, 405)
        m = re.fullmatch(r"(/api/plugins/[a-z0-9_-]+/threads/[a-z0-9_-]+/[^/]+)/(send|discard)", p)
        route = self._view_route(m.group(1)) if m else None
        if route:  # (2.0) a reply typed in a native thread (any page with the token: the phone's too)
            return self._view_action(route, m.group(2), self._body())
        if self._watch_post(p):  # the watch's voice call (server/watch.py): its audio is a raw body, not JSON
            return
        b = self._body()
        if p in FILE_ROUTES and federation.machine_in(b):
            return self._remote_files(federation.machine_in(b), "POST", p, {}, b)
        m = re.fullmatch(r"/api/shell/(\w[\w-]*~\w+)/(\w+)", p)
        if m:
            return self._remote_shell(m.group(1), m.group(2), b)
        try:
            if p == "/api/harness-server/restart":  # Restart Harness Server: every agent process ends, a new one starts
                if self.from_remote:
                    return self._json({"error": "only on the server's own machine"}, 403)
                try:
                    return self._json(self.desk.restart_harness_server())
                except ValueError as e:
                    return self._json({"error": str(e)}, 409)
            if p == "/api/shutdown":  # stop this server: its agents handed off to the next one (a restart), or with
                # {"agents": "stop"} closed with it, and the Harness Server too (Quit Orbit and Stop Agents)
                if self.from_remote:
                    return self._json({"error": "only on the server's own machine"}, 403)
                if b.get("pid") is not None and b.get("pid") != os.getpid():  # (`orbit stop` asks its own server only)
                    return self._json({"error": f"this server's pid is {os.getpid()}, not {b.get('pid')}"}, 409)
                self.desk.stop_agents = b.get("agents") == "stop"
                self._json({"ok": True, "agents": "stop" if self.desk.stop_agents else "handoff", "pid": os.getpid(),
                            "mode": self.desk.hosting.get("mode"), "why": self.desk.hosting.get("why", "")})
                return self.stop_server()
            if p == "/api/ctx":
                self.desk.set_ctx(b)
                return self._json({"ok": True})
            if p == "/api/open":
                line = b.get("line")
                return self._json(self.desk.open_for_user(str(b.get("path", "")), int(line) if line else None,
                                                          bool(b.get("side")), str(b.get("by", ""))))
            if p == "/api/layout":  # {"layout", "base"}: the tabs as a page arranged them; 409 + the current one if behind
                from orbit.desk.layout import Stale
                try:
                    return self._json(self.desk.set_layout(b.get("layout"), b.get("base")))
                except Stale as e:
                    return self._json({"error": str(e), **e.current}, 409)
            m = re.fullmatch(r"/api/sessions/([\w-]+(?:~[\w-]+)?)/side", p)
            if m:  # {"op": open|close|select|clear, "spec"?, "id"?}: its side panel, on every screen (desk/sides.py)
                fed = federation.split(m.group(1))
                if fed:
                    return self._json(*reversed(self.desk.federation.side(fed[0], fed[1], b)))
                try:
                    return self._json(self.desk.side_op(m.group(1), str(b.get("op", "")), b.get("spec"), str(b.get("id") or "")))
                except KeyError:
                    return self._json({"error": "no such session"}, 404)
            if p == "/api/relay":  # the user's own words to another session (orbit-agent relay; orbit/desk/relay.py)
                return self._relay(b)
            if p == "/api/relay/forward":  # … from another machine's server (its token): delivered here
                return self._relay_forward(b)
            if p == "/api/show":
                if b.get("url"):  # a web address beside the conversation
                    return self._json(self.desk.show_url(str(b["url"]), str(b.get("title", "")), str(b.get("by", ""))))
                html = b.get("html")
                if html is None and b.get("path"):
                    html = files._abs(str(b["path"])).read_text(encoding="utf-8", errors="replace")
                return self._json(self.desk.show(str(html or ""), str(b.get("title", "")), str(b.get("by", ""))))
            if p == "/api/settings":
                return self._json(self.desk.set_settings(b))
            if p in ("/api/keys", "/api/keys/check"):  # {"name", "op": add|update|remove|move, "id", "value", "label",
                # "to"} edits the keys (tried in order); {"name", "value"} alone: that key first (null / "": forgets
                # them all); check (one "id", else the first) asks the provider
                if self.from_remote:
                    return self._json({"error": "only on the server's own machine"}, 403)
                from orbit.services import keys
                name = str(b.get("name", ""))
                if p == "/api/keys/check":
                    return self._json(keys.check(self.desk.state_dir, name, str(b.get("id") or "")))
                if b.get("op"):
                    to = b.get("to")
                    return self._json({"keys": keys.edit_keys(self.desk.state_dir, name, str(b["op"]), str(b.get("id") or ""),
                                                              b.get("value"), b.get("label"), to if isinstance(to, int) else None)})
                return self._json({"keys": keys.set_key(self.desk.state_dir, name, b.get("value"))})
            if p in ("/api/live/start", "/api/live/stop"):  # a voice call (GPT Live): this machine, or a client with the
                # token (the phone): the key stays here, the client gets only the call's SDP answer
                from orbit.services.live import Busy, NoKey
                if p == "/api/live/stop":
                    try:
                        return self._json(self.desk.live.stop(str(b.get("call") or "")))
                    except KeyError:
                        return self._json({"error": "no such call"}, 404)
                try:
                    return self._json(self.desk.live.start(str(b.get("sid", "")), b.get("sdp")))
                except NoKey as e:
                    return self._json({"error": str(e), "need": "openai-key"}, 400)
                except Busy as e:
                    return self._json({"error": str(e), "busy": {"call": e.call.id, "sid": e.call.sid}}, 409)
                except ValueError as e:
                    return self._json({"error": str(e)}, 502 if str(e).startswith(("OpenAI", "could not reach")) else 400)
            if p.startswith("/api/push"):  # Web Push (orbit/services/push.py): behind the token, as every /api call
                from orbit.services import push
                pu = push.of(self.desk)
                if p == "/api/push":  # the status, and whether this device's subscription is one of them
                    return self._json(pu.status(str(b.get("endpoint") or "")))
                if p == "/api/push/subscribe":
                    return self._json({"subscription": pu.subscribe(b.get("subscription"), str(b.get("label") or "")),
                                       **pu.status(str((b.get("subscription") or {}).get("endpoint") or ""))})
                if p == "/api/push/unsubscribe":
                    gone = pu.unsubscribe(str(b.get("endpoint") or ""), str(b.get("id") or ""))
                    return self._json({"removed": gone, **pu.status()})
                if p == "/api/push/tap":  # sw.js: a notification was tapped (the device's record of how it went)
                    pu._log({"at": time.time(), "outcome": "tap", "sid": str(b.get("sid") or "")[:200],
                             "via": str(b.get("how") or "")[:40], "windows": int(b.get("windows") or 0),
                             "v": str(b.get("v") or "")[:20]})
                    return self._json({"ok": True})
                if p == "/api/push/relay":  # another machine's notification (its relay token): its session as we name it
                    name = getattr(self, "push_relay", "") or pu.relay_name(remote.presented(self.headers, u.query))
                    if not name:
                        return self._json({"error": "a relay token only (orbit push-relay add NAME)"}, 403)
                    sid = str(b.get("sid") or "")
                    return self._json(pu.notify(str(b.get("title") or ""), str(b.get("body") or ""), f"{name}~{sid}" if sid else "",
                                                key=f"{name}:{b['key']}" if b.get("key") else "", via=f"{name}: {str(b.get('via') or '')}"[:60]))
                if p == "/api/push/send":  # orbit-agent notify; the phone's "Send a test" (to itself: endpoint)
                    sid = str(b.get("sid") or "")
                    if sid and sid not in self.desk.sessions:
                        raise KeyError(sid)
                    only = pu.sub_id(str(b["endpoint"])) if b.get("endpoint") else str(b.get("sub") or "")
                    if b.get("endpoint") and not pu.status(str(b["endpoint"]))["subscribed"]:
                        return self._json({"error": "this device is not subscribed"}, 404)
                    return self._json(pu.notify(str(b.get("title") or ""), str(b.get("body") or ""), sid,
                                                key=str(b.get("key") or ""), sub=only, via=str(b.get("via") or "api")[:40]))
            if p == "/api/schedules":
                return self._json(self.desk.scheduler.create(b))
            if p == "/api/schedules/parse":
                spec = schedule.parse(str(b.get("when", "")))
                return self._json({"spec": spec, "describe": schedule.describe(spec),
                                   "next": schedule.next_run(spec, time.time())})
            m = re.fullmatch(r"/api/schedules/([\w-]+)/(update|delete|run|check)", p)
            if m:
                sid, action = m.groups()
                if action == "check":  # a watch's check command now: nothing sent
                    return self._json(self.desk.scheduler.check(sid))
                if action == "update":
                    return self._json(self.desk.scheduler.update(sid, b))
                if action == "run":
                    return self._json(self.desk.scheduler.run(sid))
                self.desk.scheduler.delete(sid)
                return self._json({"ok": True})
            if p == "/api/shell":  # the session's shell (started when missing or exited)
                key, cwd, env = str(b.get("key", "")), str(b.get("cwd", "")), {}
                if federation.split(key) or federation.machine_of(key):
                    return self._remote_shell(key, "", b)
                if b.get("terminal"):  # ⌘T: the agent's own terminal (protocol 1.1), attached on a pty here
                    sess = self.desk.get(key)
                    if not (sess.driver and sess.driver.terminal):
                        raise ValueError(f"{sess.name}'s agent has no terminal")
                    argv, title = list(sess.driver.terminal), sess.name
                    env = {"ORBIT_SESSION": key, **(getattr(sess.driver, "terminal_env", None) or {})}  # (a secret goes here, never in argv: ps shows argv)
                    return self._json(self.desk.shells.open("term:" + key, sess.cwd, title, env, argv).public())
                if key in self.desk.sessions:
                    sess = self.desk.get(key)
                    cwd = cwd or sess.cwd
                    env = {"ORBIT_SESSION": key}
                    title = sess.name
                else:
                    title = str(b.get("title", ""))
                if not key:
                    raise ValueError("key (a session id or folder) is required")
                return self._json(self.desk.shells.open(key, cwd, title, env).public())
            m = re.fullmatch(r"/api/shell/(\w+)/(input|resize|kill|restart|terminal)", p)
            if m:
                try:
                    sh = self.desk.shells.get(m.group(1))
                except KeyError:
                    return self._json({"error": "no such shell"}, 404)
                action = m.group(2)
                if action == "input":
                    sh.write(str(b.get("data", "")))
                elif action == "resize":
                    sh.resize(int(b.get("cols", 80)), int(b.get("rows", 24)))
                elif action == "kill":
                    sh.kill()
                elif action == "restart":
                    sh.restart()
                else:
                    return self._json({"ok": True, "app": open_terminal_app(sh.cwd)})
                return self._json({"ok": True, **sh.public()})
            if p == "/api/log":  # the page's errors and slow operations, one JSON line each
                append_log(self.desk.state_dir / "page.log", b)
                return self._json({"ok": True})
            if p == "/api/resolve-session":
                s = self.desk.find_session(str(b.get("ref", "")), closed=bool(b.get("closed")))
                return self._json(s.public())
            if p == "/api/file":
                if b.get("create_only") and files._abs(b.get("path", "")).exists():  # a new file only (Save mine as a copy)
                    return self._json({"error": "exists", "exists": True}, 409)
                try:
                    return self._json(files.save(b.get("path", ""), str(b.get("text", "")), b.get("base_hash"),
                                                 force=bool(b.get("force")), allow_empty=bool(b.get("allow_empty"))))
                except files.Conflict as c:
                    return self._json({"error": "changed on disk", "conflict": True, "current": c.current}, 409)
                except files.EmptyOverwrite as e:
                    return self._json({"error": str(e), "empty": True}, 409)
            if p == "/api/notes/create":
                return self._json(notes.create(str(b.get("name", ""))))
            if p == "/api/notes/week":
                return self._json(notes.week_note())
            if p == "/api/notes/today":
                return self._json(notes.append_today(str(b.get("text", "")), str(b.get("source", ""))))
            if p == "/api/stats":
                return self._json(files.stats([str(x) for x in b.get("paths", [])]))
            if p == "/api/files/create":
                return self._json(files.create(b.get("path", ""), folder=bool(b.get("folder"))))
            if p == "/api/files/rename":
                r = files.rename(b.get("path", ""), b.get("new", ""))
                old = str(files._abs(b.get("path", "")))
                if notes.in_vault(old):  # renaming a note (or an attachment) rewrites the links to it
                    r["links"] = notes.update_links(old, r["path"])
                return self._json(r)
            if p == "/api/https":  # HTTPS for other machines and the phone, by Tailscale: on / off
                if self.from_remote:
                    return self._json({"error": "only on the server's own machine"}, 403)
                try:
                    st = https.enable(self.desk.state_dir, self.port, int(b.get("port") or https.DEFAULT_PORT)) if b.get("on") \
                        else https.disable(self.desk.state_dir, self.port)
                except RuntimeError as e:
                    return self._json({"error": str(e)}, 400)
                if st["on"] and self.sharing.status()["on"]:  # HTTPS replaces serving plain HTTP on the network
                    self.sharing.set(False)
                    self.desk.set_settings({"serve_remote": False})
                return self._json(self._https_status())
            if p in ("/api/sharing", "/api/sharing/rotate"):  # switch serving other machines; or a new token
                if self.from_remote:
                    return self._json({"error": "only on the server's own machine"}, 403)
                if p == "/api/sharing/rotate":
                    remote.rotate(self.desk.state_dir)
                else:
                    on = bool(b.get("on"))
                    self.sharing.set(on)
                    self.desk.set_settings({"serve_remote": on})
                st = self.sharing.status()
                st["urls"] = [remote.url(self.desk.state_dir, h, st["port"]) for h in st["addresses"]] if st["on"] else []
                return self._json(st)
            if p == "/api/remotes":
                try:
                    r = remote.add(self.desk.state_dir, str(b.get("name", "")), str(b.get("url", "")))
                    if self.desk.federation:
                        self.desk.federation.kick()
                    return self._json(r)
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
                except remote.RemoteError as e:  # the remote's own refusal (a wrong token) or no answer
                    return self._json({"error": str(e)}, 400)
            m = re.fullmatch(r"/api/remotes/([a-z0-9_-]+)/open", p)
            if m:  # that machine's page in this machine's browser, signed in (the page itself never sees the token)
                if self.from_remote:
                    return self._json({"error": "opens a browser on the server's machine: only from that machine"}, 403)
                info = remote.load(self.desk.state_dir).get(m.group(1))
                if not info:
                    return self._json({"error": f"no remote called {m.group(1)}"}, 404)
                q = [("token", info["token"])] + ([("session", str(b["session"]))] if b.get("session") else [])
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", f"{info['url']}/?{urlencode(q)}"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return self._json({"ok": True})
            m = re.fullmatch(r"/api/remotes/([a-z0-9_-]+)/delete", p)
            if m:
                try:
                    remote.remove(self.desk.state_dir, m.group(1))
                except KeyError:
                    return self._json({"error": f"no remote called {m.group(1)}"}, 404)
                if self.desk.federation:
                    self.desk.federation.kick()
                return self._json({"ok": True})
            if p == "/api/usage/refresh":
                USAGE.kick()
                return self._json({"ok": True})
            if p == "/api/calendar/open-app":  # ↗ in the Calendar: macOS Calendar
                if sys.platform != "darwin":
                    return self._json({"error": "Calendar.app is on macOS only"}, 400)
                subprocess.Popen(["open", "-a", "Calendar"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return self._json({"ok": True})
            if p == "/api/calendar/note":  # {event, create?}: a meeting's note (where the meeting_notes setting says)
                return self._json(self.desk.meeting_note(b.get("event") or {}, bool(b.get("create"))))
            if p == "/api/calendar/related":  # {event}: what the plugins' [meeting] related say (API 2.1)
                from orbit import plugins
                if self.from_remote:  # (it runs the plugins' commands)
                    return self._json({"items": [], "local": False})
                return self._json({"items": plugins.meeting_related(b.get("event") or {}), "local": True})
            if p == "/api/calendar/prep":  # a Fleet helper prepares the user for a meeting, into its note
                return self._json(self.desk.meeting_prep(b.get("event") or {}, str(b.get("path", ""))))
            if p == "/api/attach-session" and b.get("machine"):  # another machine's conversation: resumed there
                machine, fed = str(b["machine"]), self.desk.federation
                if not fed:
                    return self._json({"error": f"no remote called {machine}"}, 404)
                body = federation.local_paths(machine, {k: v for k, v in b.items() if k not in ("machine", "group")})
                code, r = fed.call(machine, "POST", "/api/attach-session", body)
                if code == 200 and isinstance(r, dict) and r.get("id"):
                    fed.remember(machine, r)
                    r = fed.place(r["id"], group=str(b.get("group") or "") or None) or r
                return self._json(r, code)
            if p == "/api/attach-session":
                return self._json(self.desk.attach(str(b.get("harness", "claude")), str(b.get("id", "")), str(b.get("cwd", "")),
                                                   name=str(b.get("name", "")), group=str(b.get("group", "")),
                                                   mode=str(b.get("mode", "default"))))
            if p == "/api/open-terminal":  # a session's folder in the user's terminal app (iTerm, else Terminal)
                sid = str(b.get("sid", ""))
                if federation.split(sid):  # a session on another machine: its folder is not here (⌘J is its shell)
                    return self._json({"error": f"that session is on {federation.split(sid)[0]}: its shell runs in ⌘J"}, 400)
                return self._json({"ok": True, "app": open_terminal_app(self.desk.get(sid).cwd)})
            if p == "/api/attach":  # a file dropped / pasted into a Markdown document: copied beside it, linked
                data = None
                if b.get("data"):
                    import base64
                    data = base64.b64decode(str(b["data"]), validate=True)
                    if len(data) > UPLOAD_MAX:
                        raise ValueError(f"too large (more than {UPLOAD_MAX // 1024 // 1024} MB)")
                return self._json(notes.attach(str(files._abs(b.get("doc", ""))), str(b.get("name", "")), data=data,
                                               src=b.get("src") or None))
            if p == "/api/files/trash":
                if notes.in_vault(str(files._abs(b.get("path", "")))):
                    notes.trash(str(files._abs(b.get("path", ""))))  # the vault's own .trash, as Obsidian does
                else:
                    files.trash(b.get("path", ""))
                return self._json({"ok": True})
            if p == "/api/review/revert":
                files.revert_hunk(b.get("root", ""), b.get("path", ""), b.get("header", ""))
                return self._json({"ok": True})
            if p == "/api/open-external":
                target = files._abs(b.get("path", ""))
                if not target.exists():
                    raise ValueError(f"no such file: {target}")
                how = b.get("how", "editor")
                line = b.get("line")
                if how == "terminal":  # a folder in iTerm (else Terminal): the Files tree's folder menu
                    if not target.is_dir():
                        raise ValueError(f"not a folder: {target}")
                    return self._json({"ok": True, "app": open_terminal_app(str(target))})
                if how == "finder":
                    cmd = ["open", "-R", str(target)]
                elif how == "default":  # the file's default app, whatever the setting
                    cmd = files.editor_command(target, None, "default")
                else:  # the editor the "Open files in" setting names
                    cmd = files.editor_command(target, int(line) if line else None, self.desk.settings.get("open_in", "default"))
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                return self._json({"ok": True})
            if p == "/api/open-url":
                url = str(b.get("url", ""))
                if not re.match(r"(?i)^(https?|mailto):", url):
                    raise ValueError("only http(s) and mailto links open outside")
                subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return self._json({"ok": True})
            if p == "/api/badge":  # the unread count the page shows on its bell; the app shell puts it on the Dock icon
                self.desk.badge = max(0, int(b.get("count", 0)))
                return self._json({"ok": True})
            machine = str(b.get("machine", ""))
            if p == "/api/sessions" and str(b.get("group", "")).startswith(federation.GROUP):  # earlier pages
                machine, b = str(b["group"])[len(federation.GROUP):], {**b, "group": ""}
            if p == "/api/sessions" and machine:  # a new session on another machine: made there, in a group here
                fed = self.desk.federation
                body = federation.local_paths(machine, {k: v for k, v in b.items() if k not in ("machine", "group")})
                code, r = fed.call(machine, "POST", "/api/sessions", body)
                if code == 200 and isinstance(r, dict) and r.get("id"):
                    fed.remember(machine, r)
                    r = fed.place(r["id"], group=str(b.get("group") or "") or None) or r
                return self._json(r, code)
            if p == "/api/sessions" and self.from_remote and getattr(DRIVERS.get(str(b.get("harness") or "")), "local_only", False):
                return self._json({"error": f"{b.get('harness')} runs on its own machine only"}, 403)
            if p == "/api/sessions":
                return self._json(self.desk.create(cwd=b.get("cwd", ""), model=b.get("model", ""),
                                                   mode=b.get("mode", "default"), name=b.get("name", ""),
                                                   harness=b.get("harness") or "claude", group=b.get("group", ""),
                                                   effort=b.get("effort", "")))
            if p == "/api/upload":  # a pasted / dropped / attached file: saved, and the agent gets its path
                return self._json(save_upload(self.desk.state_dir, str(b.get("name", "")), str(b.get("data", ""))))
            if p == "/api/fleet/ask":
                return self._json(self.desk.fleet_ask(str(b.get("text", "")), str(b.get("cwd", ""))))
            if p == "/api/groups":  # {name, parent?}: a group (under parent: a subgroup)
                return self._json(self.desk.group_create(b.get("name", ""), str(b.get("parent") or "")))
            m = re.fullmatch(r"/api/groups/([\w-]+)/(style|home|pin|unpin)", p)
            if m:  # a group's look, its home page's widgets, what is pinned to it
                gid, action = m.groups()
                if action == "style":
                    return self._json(self.desk.group_style(gid, b))
                if action == "home":
                    return self._json(self.desk.group_home(gid, b.get("widgets")))
                if action == "pin":
                    return self._json(self.desk.group_pin(gid, str(b.get("text", "")), str(b.get("by", "")),
                                                          str(b.get("sid", "")), str(b.get("cid", ""))))
                return self._json(self.desk.group_unpin(gid, str(b.get("id", ""))))
            m = re.fullmatch(r"/api/groups/([\w-]+)/(rename|move|delete|nest)", p)
            if m:
                gid, action = m.groups()
                if action == "rename":
                    return self._json(self.desk.group_rename(gid, b.get("name", "")))
                if action == "nest":  # {parent ("" = the top), after?}: into another group, or out to the top
                    return self._json(self.desk.group_nest(gid, str(b.get("parent") or ""), str(b.get("after") or "")))
                if action == "move":
                    self.desk.group_move(gid, int(b.get("delta", 0)))
                else:
                    self.desk.group_delete(gid)
                return self._json({"ok": True})
            if p == "/api/people":  # {"names": [...]}: who they are, from the plugins (1.2)
                return self._people(b.get("names"))
            m = re.fullmatch(r"/api/plugins/([a-z0-9_-]+)/items/([a-z0-9_-]+)", p)
            if m:  # (1.3) changes to one of its item sources: {"upsert": [...], "remove": [keys]}
                return self._items(m.group(1), m.group(2), b, whole=False)
            m = re.fullmatch(r"/api/plugins/([a-z0-9_-]+)/(secret|restart|run|status|badge|open-tab|notify)", p)
            if m:  # a plugin's secrets, services and command: this machine only
                if self.from_remote:
                    return self._json({"error": "only on the server's own machine"}, 403)
                return self._plugin_action(m.group(1), m.group(2), b)
            m = re.fullmatch(r"/api/sessions/(\w[\w-]*~[\w-]+)/(\w+)", p)
            if m:
                return self._remote_action(m.group(1), m.group(2), b)
            m = re.fullmatch(r"/api/sessions/([\w-]+)/(send|stop|close|rename|restart|reopen|resume|update|move|clone|fork|continue|withdraw|edit|mention|mode|steer|model|note)", p)
            if m:
                sid, action = m.groups()
                if action == "steer":
                    return self._json(self.desk.steer(sid, str(b.get("text", "")), origin=self._origin(b.get("origin")),
                                                      client_id=b.get("client_id")))
                if action == "note":  # a note card (a voice call's transcript, from the server that held the call)
                    return self._json(self.desk.add_note(sid, str(b.get("title", ""))[:200], str(b.get("text", ""))[:200_000],
                                                         origin=self._origin(b.get("origin"))))
                if action == "model":
                    return self._json(self.desk.set_model(sid, b.get("model"), b.get("effort")))
                if action == "mode":
                    return self._json(self.desk.set_mode(sid, str(b.get("mode", ""))))
                if action == "continue":
                    return self._json(self.desk.continue_(sid, int(b.get("cid", 0))))
                if action == "reopen":
                    return self._json(self.desk.reopen(sid))
                if action == "resume":
                    return self._json(self.desk.resume(sid))
                if action in ("fork", "clone"):  # (clone: its earlier name)
                    return self._json(self.desk.fork(sid, str(b.get("name") or ""), harness=str(b.get("harness") or ""),
                                                     model=str(b.get("model") or "")))
                if action == "update":
                    return self._json(self.desk.update(sid, group=b.get("group"), favorite=b.get("favorite")))
                if action == "move":
                    self.desk.move(sid, b.get("before"))
                    return self._json({"ok": True})
                if action == "send":
                    return self._json(self.desk.send(sid, b.get("text", ""), origin=self._origin(b.get("origin")),
                                                     client_id=b.get("client_id"), first=bool(b.get("first"))))
                if action == "withdraw":
                    self.desk.withdraw(sid, int(b.get("cid", 0)))
                    return self._json({"ok": True})
                if action == "edit":
                    return self._json(self.desk.edit(sid, int(b.get("cid", 0)), b.get("text", "")))
                if action == "mention":
                    src = self.desk.find_session(str(b.get("from", ""))).id
                    return self._json(self.desk.mention(sid, src, b.get("text", ""), agent=bool(b.get("agent"))))
                if action == "stop":
                    self.desk.stop(sid, keep_queue=bool(b.get("keep_queue")))
                    return self._json({"ok": True})
                if action == "close":
                    self.desk.close(sid)
                    return self._json({"ok": True})
                if action == "rename":
                    return self._json(self.desk.rename(sid, b.get("name", "")))
                if action == "restart":
                    return self._json(self.desk.restart(sid))
        except Stopping as e:
            return self._json({"error": str(e)}, 503)
        except KeyError:
            what = "schedule" if p.startswith("/api/schedules/") else "session"
            return self._json({"error": f"no such {what}"}, 404)
        except FileNotFoundError as e:
            return self._json({"error": f"no such file: {e}"}, 404)
        except (ValueError, RuntimeError, OSError) as e:
            return self._json({"error": str(e)}, 400)
        self._json({"error": "not found"}, 404)


# Routes about files by path: when a path is another machine's (/@NAME/...), that server does them.
