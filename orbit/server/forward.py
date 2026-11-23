"""Another machine's sessions, files and shells: requests done by that server (see orbit.machines.federation)."""

from __future__ import annotations

import json
import socket
from urllib.parse import urlencode

from orbit.machines import federation, remote


FILE_ROUTES = {"/api/tree", "/api/file-root", "/api/skills", "/api/file", "/api/dirlist", "/api/review", "/api/raw",
               "/api/embed", "/api/resolve", "/api/stats", "/api/files/create", "/api/files/rename", "/api/files/trash",
               "/api/review/revert", "/api/attach", "/api/open-external"}



class Forward:
    """Handler methods that hand a request to another server."""

    def _remote_action(self, sid: str, action: str, b: dict) -> None:
        """POST /api/sessions/NAME~ID/<action>: done by that server."""
        name, rsid = federation.split(sid) or ("", "")
        fed = self.desk.federation
        if action == "move":  # tab order is this server's (across machines), not that machine's
            try:
                self.desk.move(sid, b.get("before"))
            except KeyError:
                return self._json({"error": "no such session"}, 404)
            return self._json({"ok": True})
        if action == "update":  # its group and star are kept here (this server's groups), not over there
            shown = fed.place(sid, group=b.get("group"), favorite=b.get("favorite"))
            return self._json(shown or {"ok": True})
        if action == "mention":  # the sender is not a session there: a note as a plain request
            src = self.desk.sessions.get(str(b.get("from", "")))
            who = f'session "{src.name}"' if src else "a session"
            action, b = "send", {"text": f"(from {who} on {socket.gethostname().split('.')[0]}) Note, not a task: "
                                         f"{str(b.get('text', '')).strip()}\n\nDo not reply unless it asks you to."}
        if isinstance(b.get("text"), str):  # paths of that machine written here as /@NAME/… are its own paths there
            b = {**b, "text": b["text"].replace(f"{federation.PFX}{name}/", "/")}
        code, r = fed.call(name, "POST", f"/api/sessions/{rsid}/{action}", b)
        if code == 200 and isinstance(r, dict) and str(r.get("id", "")).startswith(name + federation.SEP):
            fed.remember(name, r)  # reopen / clone / resume answer with a session
        self._json(r, code)

    def _remote_files(self, name: str, method: str, p: str, q: dict, b: dict | None) -> None:
        """A file request about another machine's path: done by that server, its paths written back as /@NAME/…"""
        import urllib.error
        import urllib.request
        info = remote.load(self.desk.state_dir).get(name)
        if not info:
            return self._json({"error": f"no remote called {name}"}, 404)
        if p == "/api/open-external":  # another machine's file opens only in Orbit's own editor (no outside editor)
            return self._json({"ok": False, "orbit": True, "why": f"that file is on {name}: it opens in Orbit's editor"})
        q, b = federation.local_paths(name, q), federation.local_paths(name, b)
        url = info["url"] + p + ("?" + urlencode(q) if q else "")
        req = urllib.request.Request(url, method=method, data=json.dumps(b).encode() if b is not None else None,
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {info['token']}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                code, ctype, data = r.status, r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as e:
            code, ctype, data = e.code, e.headers.get("Content-Type", ""), e.read()
        except OSError as e:
            return self._json({"error": f"{name} is unreachable ({info['url']}): {e}"}, 502)
        if "json" in ctype:
            try:
                return self._json(federation.our_paths(name, json.loads(data or b"{}")), code)
            except ValueError:
                pass
        self.send_response(code)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if any(t in ctype for t in ("svg", "html")):
            self.send_header("Content-Security-Policy", "sandbox")  # as for this machine's files
        self.end_headers()
        self.wfile.write(data)

    def _remote_shell(self, ref: str, action: str, b: dict) -> None:
        """⌘J for another machine's session (or folder): its shell over there, as shell NAME~ID here.
        ref is NAME~SESSION (open) or NAME~SHELL (an action). Over the server's HTTP only: it stays in ⌘J."""
        fed = self.desk.federation
        if action == "":
            name, key = federation.split(ref) or federation.machine_of(ref)
            body = federation.local_paths(name, {**b, "key": key})
            code, r = fed.call(name, "POST", "/api/shell", body)
        else:
            name, shid = federation.split(ref)
            if action == "terminal":  # a terminal app here cannot reach a shell over there
                return self._json({"error": f"that shell is on {name}: it runs in ⌘J"}, 400)
            code, r = fed.call(name, "POST", f"/api/shell/{shid}/{action}", b)
        if code == 200 and isinstance(r, dict):
            r = dict(r)
            if r.get("id"):
                r["id"] = federation.join(name, str(r["id"]))
            if isinstance(r.get("key"), str):
                r["key"] = federation.join(name, r["key"]) if not r["key"].startswith("/") else federation.ours(name, r["key"])
            r["cwd"] = federation.ours(name, r.get("cwd", ""))
        self._json(r, code)

    def _proxy(self, name: str, path: str, query: str, method: str = "GET") -> None:
        """/api/remote/<name>/<path>: the same request to that server, with its token; events stream through."""
        import urllib.error
        import urllib.parse
        import urllib.request
        info = remote.load(self.desk.state_dir).get(name)
        if not info:
            return self._json({"error": f"no remote called {name} (orbit remote add {name} URL)"}, 404)
        if not path.startswith("/api/") and path not in ("/",):
            return self._json({"error": "only the API is forwarded"}, 404)
        data = None
        if method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            data = self.rfile.read(n) if n else b"{}"
        q = urllib.parse.parse_qs(query)
        q.pop("token", None)
        target = info["url"] + path + ("?" + urllib.parse.urlencode(q, doseq=True) if q else "")
        req = urllib.request.Request(target, data=data, method=method, headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {info['token']}"})
        stream = path == "/api/events" or path.endswith("/stream")
        try:
            up = urllib.request.urlopen(req, timeout=None if stream else 30)
        except urllib.error.HTTPError as e:
            body = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        except OSError as e:
            return self._json({"error": f"cannot reach {name} ({info['url']}): {e}"}, 502)
        with up:
            self.send_response(up.status)
            ctype = up.headers.get("Content-Type", "application/octet-stream")
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            if stream or "event-stream" in ctype:
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                try:
                    for line in up:  # line by line: each event goes out as it comes
                        self.wfile.write(line)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                return
            body = up.read()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
