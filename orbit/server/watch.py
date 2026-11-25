"""The watch's voice call over plain HTTP (builddoc/gpt-live.md §12): watchOS keeps low-level networking (WebSocket,
the Network framework) from most apps, but URLSession's HTTP requests always work, over Wi-Fi or through the iPhone
by Bluetooth. This server holds the GPT Live session (its WebSocket transport) and relays the audio:

  POST /api/live/watch/start {sid}        → {call, live, rate: 24000, format: "pcm16", chime}
  POST /api/live/watch/<call>/up          body: raw PCM 16-bit mono 24 kHz little-endian (~100–200 ms) → 204
  GET  /api/live/watch/<call>/down        a stream of frames until the call ends: 1 byte type (b"a" audio PCM like
                                          the upload, b"j" a JSON event: the {"t": "live"} events the pages get, and
                                          {"t": "ping"} every 10 s), 4 bytes length (big-endian), the payload
  POST /api/live/watch/<call>/stop        → the call's state

The token as the phone's (Authorization: Bearer …), checked by the handler's guard. The uploads keep their connection
open (Connection: keep-alive), so a chunk every 100–200 ms is not a new connection each time.
"""

from __future__ import annotations

import json
import queue
import re

UP_LIMIT = 256 * 1024  # an upload's body at most (5 s of audio; the watch sends ~0.1–0.2 s)
PING_S = 10.0


class Watch:
    """Handler methods for the watch's call."""

    def _watch_post(self, p: str) -> bool:
        """POST /api/live/watch/…: handled (True), or not a watch route (False). Runs before the JSON body is read."""
        if not p.startswith("/api/live/watch/"):
            return False
        from orbit.services.live import Busy, NoKey
        if p == "/api/live/watch/start":
            b = self._body() or {}
            try:
                self._json(self.desk.live.start_watch(str(b.get("sid", ""))))
            except NoKey as e:
                self._json({"error": str(e), "need": "openai-key"}, 400)
            except Busy as e:
                self._json({"error": str(e), "busy": {"call": e.call.id, "sid": e.call.sid}}, 409)
            except KeyError:
                self._json({"error": "no such session"}, 404)
            except ValueError as e:
                self._json({"error": str(e)}, 502 if str(e).startswith(("OpenAI", "could not reach")) else 400)
            return True
        m = re.fullmatch(r"/api/live/watch/(call-[0-9a-f]+)/(up|stop)", p)
        if not m:
            self._json({"error": "not found"}, 404)
            return True
        if m.group(2) == "stop":
            self._body()
            try:
                self.desk.live.watch_call(m.group(1))
                self._json(self.desk.live.stop(m.group(1)))
            except KeyError:
                self._json({"error": "no such call"}, 404)
            return True
        n = int(self.headers.get("Content-Length") or 0)
        if n > UP_LIMIT:
            self.close_connection = True
            self._json({"error": f"{n} bytes: an upload is {UP_LIMIT} bytes at most"}, 413)
            return True
        data = self.rfile.read(n) if n else b""
        try:
            self.desk.live.watch_call(m.group(1)).audio_in(data)
        except KeyError:
            self._json({"error": "no such call (it ended)"}, 404)
            return True
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "keep-alive")  # (the next chunk on this connection: see handle_one_request)
        self.end_headers()
        self.close_connection = False
        self.connection.settimeout(30)  # (a watch gone without a goodbye: its connection closes, not waits forever)
        return True

    def _watch_down(self, call: str) -> None:
        """The call's frames until it ends (the response has no length: it ends with the connection)."""
        try:
            c = self.desk.live.watch_call(call)
        except KeyError:
            return self._json({"error": "no such call"}, 404)
        q = c.listen()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")  # (a proxy in front: pass each frame on at once)
        self.end_headers()
        from orbit.services.live.session import frame
        ping = frame(b"j", json.dumps({"t": "ping"}).encode())
        try:
            while True:
                try:
                    data = q.get(timeout=PING_S)
                except queue.Empty:
                    data = ping
                if data is None:
                    break
                self.wfile.write(data)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            c.unlisten(q)
        self.close_connection = True
