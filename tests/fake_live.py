"""A stand-in for OpenAI's GPT Live API: POST /v1/live/sessions and the sideband websocket
(/v1/live/sessions/{id}/attach), speaking the documented events, so tests never call OpenAI.

In-process: FakeLive().start(), then emit(...) server events, drop() the sideband, read received / bodies.
As a process (the browser check): `python tests/fake_live.py PORT [--browser-answer]` with control routes:
  POST /fake/emit {"events": [...]}   send server events on the sideband (and keep them for replay)
  POST /fake/drop                     drop the sideband connection (the client reattaches)
  GET  /fake/state                    {"bodies", "received", "attached", "offer"}
  POST /fake/answer {"sdp"}           with --browser-answer, the SDP answer for the waiting session create
Behaviour: each attach first replays the events of the last 3 s (by event_id, as documented); session.close is
answered with session.closed; thinking / commentary appends are acknowledged (….appended with client_event_id).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
KEY = "sk-proj-" + "f" * 40 + "LIVE"
CANNED_SDP = "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=fake-answer\r\nt=0 0\r\n"


def server_frame(op: int, payload: bytes, fin: bool = True) -> bytes:
    """A server frame: never masked."""
    n = len(payload)
    head = bytes([(0x80 if fin else 0) | op])
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack(">H", n)
    else:
        head += bytes([127]) + struct.pack(">Q", n)
    return head + payload


def read_exact(sock: socket.socket, n: int) -> bytes:
    out = b""
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise ConnectionError("closed")
        out += chunk
    return out


def read_client_frame(sock: socket.socket) -> tuple[bool, int, bytes, bool]:
    """(fin, opcode, unmasked payload, was it masked)."""
    b0, b1 = read_exact(sock, 2)
    n = b1 & 0x7F
    if n == 126:
        n = struct.unpack(">H", read_exact(sock, 2))[0]
    elif n == 127:
        n = struct.unpack(">Q", read_exact(sock, 8))[0]
    masked = bool(b1 & 0x80)
    key = read_exact(sock, 4) if masked else b"\0\0\0\0"
    data = read_exact(sock, n)
    return bool(b0 & 0x80), b0 & 0x0F, bytes(c ^ key[i % 4] for i, c in enumerate(data)), masked


class FakeLive:
    def __init__(self, port: int = 0, browser_answer: bool = False):
        self.port_wanted = port
        self.browser_answer = browser_answer
        self.bodies: list[dict] = []
        self.received: list[dict] = []
        self.history: list[tuple[float, dict]] = []  # server events sent, for the 3 s replay
        self.attached = 0
        self.ws_sessions: list[dict] = []  # session.start of each WebSocket-transport session (voice previews)
        self.ws_received: list[dict] = []
        self.attach_status = 0  # non-zero: refuse attaches with this HTTP status
        self.conn: socket.socket | None = None
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.offer = ""
        self.answer = ""
        self.n = 0
        self.closed_reason = "close_requested"
        self.srv: ThreadingHTTPServer | None = None

    # ------------------------------------------------------------------ control
    def start(self) -> "FakeLive":
        fake = self

        class H(Handler):
            pass
        H.fake = fake
        self.srv = ThreadingHTTPServer(("127.0.0.1", self.port_wanted), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    @property
    def port(self) -> int:
        return self.srv.server_address[1]

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.drop()
        if self.srv:
            self.srv.shutdown()
            self.srv.server_close()

    def emit(self, *events: dict) -> None:
        with self.lock:
            for ev in events:
                self.history.append((time.time(), ev))
                self._send_locked(ev)

    def _send_locked(self, ev: dict) -> None:
        if self.conn is None:
            return
        try:
            self.conn.sendall(server_frame(0x1, json.dumps(ev).encode()))
        except OSError:
            self.conn = None

    def drop(self) -> None:
        with self.lock:
            c, self.conn = self.conn, None
        if c is not None:
            try:
                c.shutdown(socket.SHUT_RDWR)
                c.close()
            except OSError:
                pass

    def wait_attached(self, n: int = 1, timeout: float = 10) -> bool:
        end = time.time() + timeout
        with self.cond:
            while self.attached < n or self.conn is None:
                left = end - time.time()
                if left <= 0:
                    return False
                self.cond.wait(left)
        return True

    def got(self, typ: str | None = None) -> list[dict]:
        with self.lock:
            return [e for e in self.received if typ is None or e.get("type") == typ]

    def wait_got(self, typ: str, n: int = 1, timeout: float = 10) -> list[dict]:
        end = time.time() + timeout
        while time.time() < end:
            if len(self.got(typ)) >= n:
                return self.got(typ)
            time.sleep(0.02)
        return self.got(typ)

    # ------------------------------------------------------------------ the sideband
    def serve_ws(self, sock: socket.socket) -> None:
        with self.cond:
            self.conn = sock
            self.attached += 1
            now = time.time()
            for t, ev in self.history:  # the documented replay window
                if now - t <= 3.0:
                    self._send_locked(ev)
            self.cond.notify_all()
        try:
            while True:
                fin, op, data, masked = read_client_frame(sock)
                if op == 0x8:
                    with self.lock:
                        try:
                            sock.sendall(server_frame(0x8, data[:2]))
                        except OSError:
                            pass
                    break
                if op == 0x9:
                    with self.lock:
                        sock.sendall(server_frame(0xA, data))
                    continue
                if op != 0x1:
                    continue
                ev = json.loads(data)
                ev["_masked"] = masked
                with self.lock:
                    self.received.append(ev)
                self.react(ev)
        except (ConnectionError, OSError, ValueError):
            pass
        finally:
            with self.lock:
                if self.conn is sock:
                    self.conn = None
            try:
                sock.close()
            except OSError:
                pass

    def serve_ws_session(self, sock: socket.socket) -> None:
        """A WebSocket-transport session: session.started, then the voice's audio (3 chunks of 0.1 s), then quiet."""
        send = lambda ev: sock.sendall(server_frame(0x1, json.dumps(ev).encode()))
        try:
            while True:
                fin, op, data, masked = read_client_frame(sock)
                if op == 0x8:
                    break
                if op != 0x1:
                    continue
                ev = json.loads(data)
                with self.lock:
                    self.ws_sessions.append(ev) if ev.get("type") == "session.start" else self.ws_received.append(ev)
                if ev.get("type") == "session.start":
                    send({"type": "session.started", "event_id": "evt_ws_1", "session": {"id": "live_ws_1", **ev["session"]}})
                    for k in range(3):
                        send({"type": "session.output_audio.delta", "event_id": f"evt_au_{k}",
                              "delta": base64.b64encode(struct.pack("<2400h", *([1000 * (k + 1)] * 2400))).decode()})
                elif ev.get("type") == "session.close":
                    send({"type": "session.closed", "event_id": "evt_ws_closed", "reason": "close_requested"})
        except (ConnectionError, OSError, ValueError):
            pass
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def react(self, ev: dict) -> None:
        typ, eid = ev.get("type"), ev.get("event_id")
        self.n += 1
        if typ in ("session.thinking.append", "session.commentary.append", "session.instructions.append"):
            self.emit({"type": typ + "ed", "event_id": f"evt_ack_{self.n}", "client_event_id": eid,
                       "start_ms": 1000 * self.n, "end_ms": 1000 * self.n})
        elif typ == "session.close":
            self.emit({"type": "session.closed", "event_id": f"evt_closed_{self.n}", "client_event_id": eid,
                       "reason": self.closed_reason, "session": {"id": "live_1", "status": "active"},
                       "usage": {"seconds": 12}})


class Handler(BaseHTTPRequestHandler):
    fake: FakeLive

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        f = self.fake
        if self.path == "/fake/state":
            return self._json({"bodies": f.bodies, "received": f.got(), "attached": f.attached, "offer": f.offer})
        if self.path == "/v1/live/sessions" and self.headers.get("Upgrade", "").lower() == "websocket":
            # the WebSocket transport (a voice preview): session.start first, audio deltas back
            if self.headers.get("Authorization") != f"Bearer {KEY}":
                return self._json({"error": {"message": "bad key"}}, 401)
            key = self.headers.get("Sec-WebSocket-Key", "")
            accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
            self.wfile.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                              f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
            self.wfile.flush()
            self.close_connection = True
            f.serve_ws_session(self.request)
            return
        if self.path.startswith("/v1/live/sessions/") and self.path.endswith("/attach"):
            if self.headers.get("Authorization") != f"Bearer {KEY}":
                return self._json({"error": {"message": "bad key"}}, 401)
            if f.attach_status:
                return self._json({"error": {"message": "no such session"}}, f.attach_status)
            key = self.headers.get("Sec-WebSocket-Key", "")
            accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
            self.wfile.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                              f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
            self.wfile.flush()
            self.close_connection = True
            f.serve_ws(self.request)
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        f = self.fake
        b = self._body()
        if self.path == "/fake/emit":
            f.emit(*b.get("events", []))
            return self._json({"ok": True})
        if self.path == "/fake/drop":
            f.drop()
            return self._json({"ok": True})
        if self.path == "/fake/answer":
            with f.cond:
                f.answer = b.get("sdp", "")
                f.cond.notify_all()
            return self._json({"ok": True})
        if self.path == "/v1/live/sessions":
            if self.headers.get("Authorization") != f"Bearer {KEY}":
                return self._json({"error": {"message": "Incorrect API key provided"}}, 401)
            f.bodies.append(b)
            sdp = CANNED_SDP
            if f.browser_answer:  # the browser check answers the offer with a second peer connection
                with f.cond:
                    f.offer, f.answer = (b.get("transport") or {}).get("sdp", ""), ""
                    f.cond.wait_for(lambda: f.answer, timeout=30)
                    sdp = f.answer
            return self._json({"session": {"id": f"live_{len(f.bodies)}"}, "transport": {"type": "webrtc", "sdp": sdp}}, 201)
        self._json({"error": "not found"}, 404)


if __name__ == "__main__":
    fl = FakeLive(int(sys.argv[1]), browser_answer="--browser-answer" in sys.argv).start()
    print(f"fake GPT Live on {fl.base}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        fl.stop()
