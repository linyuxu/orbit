"""Server-Sent Events: the page's live updates and the ⌘J shells' output."""

from __future__ import annotations

import json
import queue


class Streams:
    """Handler methods that stream."""

    def _sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = self.desk.subscribe()
        try:
            self._sse_write({"t": "hello", "sessions": self.desk.all_sessions(), "groups": self.desk.all_groups(),
                             "machines": self.desk.federation.machines() if self.desk.federation else []})
            while True:
                try:
                    ev = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                self._sse_write(ev)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.desk.unsubscribe(q)

    def _shell_stream(self, shid: str) -> None:
        try:
            sh = self.desk.shells.get(shid)
        except KeyError:
            return self._json({"error": "no such shell"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q, screen = sh.subscribe()
        try:
            import base64
            self._sse_write({"t": "screen", "d": base64.b64encode(screen).decode(), **sh.public()})
            if not sh.alive:
                self._sse_write({"t": "exit", "code": sh.code})
            while True:
                try:
                    ev = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                self._sse_write(ev)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            sh.unsubscribe(q)

    def _sse_write(self, ev: dict | str) -> None:
        data = ev if isinstance(ev, str) else json.dumps(ev, ensure_ascii=False)
        self.wfile.write(b"data: " + data.encode() + b"\n\n")
        self.wfile.flush()
