"""A small websocket client (RFC 6455), stdlib only: the GPT Live sideband needs one, and nothing else here does.

ws:// or wss:// (TLS through ssl, the system's certificates), extra headers on the handshake (Authorization), masked
client frames, text and binary messages put together from fragments, ping answered with pong, close answered and
reported. One reader at a time; sends may come from any thread.
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import struct
import threading
from urllib.parse import urlparse

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
CONT, TEXT, BINARY, CLOSE, PING, PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA
MAX_MESSAGE = 16 * 1024 * 1024  # a message longer than this ends the connection (the sideband's are small)


class HandshakeError(OSError):
    """The server answered the upgrade with something other than 101: status and the start of its body."""

    def __init__(self, status: int, body: str):
        super().__init__(f"websocket handshake: HTTP {status}" + (f": {body[:300]}" if body else ""))
        self.status, self.body = status, body


class Closed(OSError):
    """The connection is closed (a close frame, or the socket went): code and reason when the server sent them."""

    def __init__(self, code: int | None = None, reason: str = ""):
        super().__init__(f"websocket closed ({code}{': ' + reason if reason else ''})" if code else "websocket closed")
        self.code, self.reason = code, reason


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def mask(payload: bytes, key: bytes) -> bytes:
    if not payload:
        return b""
    n = len(payload)
    k = (key * (n // 4 + 1))[:n]
    return (int.from_bytes(payload, "big") ^ int.from_bytes(k, "big")).to_bytes(n, "big")


def frame(opcode: int, payload: bytes, fin: bool = True, key: bytes | None = None) -> bytes:
    """One frame; with key (4 bytes) masked, as a client must send it."""
    head = bytes([(0x80 if fin else 0) | opcode])
    m = 0x80 if key is not None else 0
    n = len(payload)
    if n < 126:
        head += bytes([m | n])
    elif n < 1 << 16:
        head += bytes([m | 126]) + struct.pack(">H", n)
    else:
        head += bytes([m | 127]) + struct.pack(">Q", n)
    return head + (key + mask(payload, key) if key is not None else payload)


class WebSocket:
    def __init__(self, sock: socket.socket, rest: bytes = b""):
        self.sock = sock
        self.buf = bytearray(rest)
        self.send_lock = threading.Lock()
        self.closed = False
        self.close_code: int | None = None

    # ------------------------------------------------------------------ reading
    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            try:
                chunk = self.sock.recv(65536)
            except (OSError, ValueError) as e:  # (timeout, reset, a socket closed under us)
                self.abort()
                raise Closed() from e
            if not chunk:
                self.abort()
                raise Closed()
            self.buf += chunk
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def _frame(self) -> tuple[bool, int, bytes]:
        b0, b1 = self._read(2)
        fin, op, masked, n = bool(b0 & 0x80), b0 & 0x0F, bool(b1 & 0x80), b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._read(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._read(8))[0]
        if n > MAX_MESSAGE:
            self.abort()
            raise Closed(1009, "message too big")
        key = self._read(4) if masked else None
        data = self._read(n)
        return fin, op, mask(data, key) if key else data

    def recv(self) -> str | bytes:
        """The next message (str for text, bytes for binary). Pings are answered on the way; a close frame is answered
        and raises Closed with its code."""
        parts: list[bytes] = []
        kind = None
        while True:
            fin, op, data = self._frame()
            if op == PING:
                self._send(PONG, data)
                continue
            if op == PONG:
                continue
            if op == CLOSE:
                code = struct.unpack(">H", data[:2])[0] if len(data) >= 2 else None
                reason = data[2:].decode("utf-8", "replace")
                self.close_code = code
                if not self.closed:
                    try:
                        self._send(CLOSE, data[:2])
                    except OSError:
                        pass
                self.abort()
                raise Closed(code, reason)
            if op in (TEXT, BINARY):
                if kind is not None:
                    self.abort()
                    raise Closed(1002, "a new message inside a fragmented one")
                kind = op
            elif op != CONT or kind is None:
                self.abort()
                raise Closed(1002, f"unexpected opcode {op}")
            parts.append(data)
            if sum(map(len, parts)) > MAX_MESSAGE:
                self.abort()
                raise Closed(1009, "message too big")
            if fin:
                msg = b"".join(parts)
                return msg.decode("utf-8") if kind == TEXT else msg

    # ------------------------------------------------------------------ writing
    def _send(self, opcode: int, payload: bytes, fin: bool = True) -> None:
        data = frame(opcode, payload, fin, os.urandom(4))
        with self.send_lock:
            if self.closed:
                raise Closed(self.close_code)
            try:
                self.sock.sendall(data)
            except (OSError, ValueError) as e:
                self.closed = True
                raise Closed() from e

    def send(self, msg: str | bytes) -> None:
        self._send(TEXT if isinstance(msg, str) else BINARY, msg.encode("utf-8") if isinstance(msg, str) else msg)

    def ping(self, payload: bytes = b"") -> None:
        self._send(PING, payload[:125])

    def close(self, code: int = 1000, reason: str = "") -> None:
        """Say goodbye (a close frame) and drop the socket; the server's answer is not waited for."""
        if not self.closed:
            try:
                self._send(CLOSE, struct.pack(">H", code) + reason.encode()[:120])
            except OSError:
                pass
        self.abort()

    def abort(self) -> None:
        self.closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def connect(url: str, headers: dict[str, str] | None = None, timeout: float = 15.0,
            ssl_context: ssl.SSLContext | None = None) -> WebSocket:
    """Open a websocket. HandshakeError when the server refuses the upgrade (its HTTP status: 401, 404, 410 …)."""
    u = urlparse(url)
    if u.scheme not in ("ws", "wss"):
        raise ValueError(f"not a websocket URL: {url}")
    port = u.port or (443 if u.scheme == "wss" else 80)
    sock = socket.create_connection((u.hostname, port), timeout=timeout)
    try:
        if u.scheme == "wss":
            sock = (ssl_context or ssl.create_default_context()).wrap_socket(sock, server_hostname=u.hostname)
        key = base64.b64encode(os.urandom(16)).decode()
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        host = u.hostname + (f":{u.port}" if u.port else "")
        lines = [f"GET {path} HTTP/1.1", f"Host: {host}", "Upgrade: websocket", "Connection: Upgrade",
                 f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13"]
        lines += [f"{k}: {v}" for k, v in (headers or {}).items()]
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = sock.recv(4096)
            if not chunk:
                raise HandshakeError(0, "the server closed the connection during the handshake")
            raw += chunk
            if len(raw) > 65536:
                raise HandshakeError(0, "handshake answer too long")
        head, rest = raw.split(b"\r\n\r\n", 1)
        status_line, *hlines = head.decode("latin-1").split("\r\n")
        parts = status_line.split(" ", 2)
        status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        got = {k.strip().lower(): v.strip() for k, _, v in (h.partition(":") for h in hlines)}
        if status != 101:
            body = rest
            try:  # a short error body says why (401 / 404 …)
                sock.settimeout(1.0)
                n = int(got.get("content-length", "0") or 0)
                while len(body) < min(n, 4096):
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    body += chunk
            except (OSError, ValueError):
                pass
            raise HandshakeError(status, body.decode("utf-8", "replace").strip())
        if got.get("upgrade", "").lower() != "websocket" or got.get("sec-websocket-accept") != accept_key(key):
            raise HandshakeError(status, "the server's upgrade answer does not match")
        sock.settimeout(None)
        return WebSocket(sock, rest)
    except BaseException:
        try:
            sock.close()
        except OSError:
            pass
        raise
