"""Web Push, sent from here: RFC 8291 (aes128gcm payload encryption) and RFC 8292 (VAPID), in the standard library.

Orbit has no third-party Python packages, and the standard library has no elliptic curves or AES-GCM, so the pieces
are here: P-256 (ECDH for the payload key, ECDSA with RFC 6979's deterministic nonce for the VAPID token), HKDF (HMAC
SHA-256), AES-128 and GCM. Slow next to OpenSSL (a few milliseconds a notification), and checked against the RFCs' own
test vectors (tests/test_webpush.py). The protocol follows the Web Push sender of the notification web app this
replaces for Orbit (same record size, headers, push-service allowlist and outcomes).

Only push services' hosts are POSTed to (a subscription names its endpoint: without the list it could point the
server at a local service). A 404 / 410 is a subscription gone (the device unsubscribed); "accepted" means the push
service took the message, not that anyone saw it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import json
import re
import secrets
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

RECORD_SIZE = 4096
MAX_PLAINTEXT = RECORD_SIZE - 86 - 1 - 16  # Apple's (and others') 4096-byte body: header, delimiter and tag included
PUSH_HOSTS = [re.compile(p) for p in (r"(^|\.)push\.apple\.com$", r"^fcm\.googleapis\.com$",
                                     r"(^|\.)push\.services\.mozilla\.com$", r"\.notify\.windows\.com$")]


def b64u_enc(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64u_dec(s: str) -> bytes:
    s = str(s)
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def allowed_endpoint(endpoint: str, extra: tuple[str, ...] = ()) -> bool:
    """A push service's https URL (or one of `extra`'s prefixes: tests' local push service)."""
    if any(str(endpoint).startswith(x) for x in extra):
        return True
    try:
        u = urlparse(str(endpoint))
    except ValueError:
        return False
    if u.scheme != "https" or (u.port not in (None, 443)) or u.username or u.password or not u.hostname:
        return False
    return any(p.search(u.hostname) for p in PUSH_HOSTS)


# ------------------------------------------------------------------ P-256
P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
G = (0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
     0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5)


def _on_curve(pt) -> bool:
    x, y = pt
    return 0 <= x < P and 0 <= y < P and (y * y - (x * x * x - 3 * x + B)) % P == 0


def _jdouble(X, Y, Z):
    if Y == 0 or Z == 0:
        return 0, 1, 0
    YY = Y * Y % P
    S = 4 * X * YY % P
    ZZ = Z * Z % P
    M = 3 * (X - ZZ) * (X + ZZ) % P  # (a = -3)
    X3 = (M * M - 2 * S) % P
    Y3 = (M * (S - X3) - 8 * YY * YY) % P
    return X3, Y3, 2 * Y * Z % P


def _jadd(X1, Y1, Z1, X2, Y2, Z2):
    if Z1 == 0:
        return X2, Y2, Z2
    if Z2 == 0:
        return X1, Y1, Z1
    Z1Z1, Z2Z2 = Z1 * Z1 % P, Z2 * Z2 % P
    U1, U2 = X1 * Z2Z2 % P, X2 * Z1Z1 % P
    S1, S2 = Y1 * Z2 * Z2Z2 % P, Y2 * Z1 * Z1Z1 % P
    if U1 == U2:
        return _jdouble(X1, Y1, Z1) if S1 == S2 else (0, 1, 0)
    H, R = (U2 - U1) % P, (S2 - S1) % P
    HH = H * H % P
    HHH = H * HH % P
    V = U1 * HH % P
    X3 = (R * R - HHH - 2 * V) % P
    Y3 = (R * (V - X3) - S1 * HHH) % P
    return X3, Y3, H * Z1 * Z2 % P


def _mul(k: int, pt) -> tuple[int, int]:
    """k·pt (affine in, affine out); the point at infinity raises."""
    X, Y, Z = 0, 1, 0
    Xp, Yp, Zp = pt[0], pt[1], 1
    for bit in bin(k)[2:]:
        X, Y, Z = _jdouble(X, Y, Z)
        if bit == "1":
            X, Y, Z = _jadd(X, Y, Z, Xp, Yp, Zp)
    if Z == 0:
        raise ValueError("point at infinity")
    zi = pow(Z, -1, P)
    return X * zi * zi % P, Y * zi * zi * zi % P


def _add_affine(a, b):
    X, Y, Z = _jadd(a[0], a[1], 1, b[0], b[1], 1)
    if Z == 0:
        raise ValueError("point at infinity")
    zi = pow(Z, -1, P)
    return X * zi * zi % P, Y * zi * zi * zi % P


def public_key(d: int) -> bytes:
    x, y = _mul(d, G)
    return b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big")


def point(pub: bytes) -> tuple[int, int]:
    if len(pub) != 65 or pub[0] != 4:
        raise ValueError("not an uncompressed P-256 public key")
    pt = int.from_bytes(pub[1:33], "big"), int.from_bytes(pub[33:], "big")
    if not _on_curve(pt):
        raise ValueError("not a point of P-256")
    return pt


def new_private() -> int:
    return secrets.randbelow(N - 1) + 1


def ecdh(d: int, pub: bytes) -> bytes:
    return _mul(d, point(pub))[0].to_bytes(32, "big")


def _rfc6979_k(d: int, h1: bytes):
    """RFC 6979 §3.2's nonces for SHA-256 and P-256 (qlen = hlen = 256), one after another."""
    x = d.to_bytes(32, "big")
    h = (int.from_bytes(h1, "big") % N).to_bytes(32, "big")
    V, K = b"\x01" * 32, b"\x00" * 32
    K = _hmac.new(K, V + b"\x00" + x + h, hashlib.sha256).digest()
    V = _hmac.new(K, V, hashlib.sha256).digest()
    K = _hmac.new(K, V + b"\x01" + x + h, hashlib.sha256).digest()
    V = _hmac.new(K, V, hashlib.sha256).digest()
    while True:
        V = _hmac.new(K, V, hashlib.sha256).digest()
        k = int.from_bytes(V, "big")
        if 1 <= k < N:
            yield k
        K = _hmac.new(K, V + b"\x00", hashlib.sha256).digest()
        V = _hmac.new(K, V, hashlib.sha256).digest()


def sign(d: int, msg: bytes) -> bytes:
    """ECDSA P-256 SHA-256, r‖s (32 bytes each, as a JWT's ES256)."""
    h1 = hashlib.sha256(msg).digest()
    e = int.from_bytes(h1, "big")
    for k in _rfc6979_k(d, h1):
        r = _mul(k, G)[0] % N
        if r == 0:
            continue
        s = pow(k, -1, N) * (e + r * d) % N
        if s:
            return r.to_bytes(32, "big") + s.to_bytes(32, "big")
    raise AssertionError("unreachable")


def verify(pub: bytes, msg: bytes, sig: bytes) -> bool:
    r, s = int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")
    if not (1 <= r < N and 1 <= s < N):
        return False
    e, w = int.from_bytes(hashlib.sha256(msg).digest(), "big"), pow(s, -1, N)
    try:
        x = _add_affine(_mul(e * w % N, G), _mul(r * w % N, point(pub)))[0]
    except ValueError:
        return False
    return x % N == r


# ------------------------------------------------------------------ AES-128-GCM
def _sbox():
    sb, p, q = [0] * 256, 1, 1
    while True:  # (the S-box from the multiplicative inverse in GF(2^8), then the affine map)
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q ^ ((q << 1) | (q >> 7)) & 0xFF ^ ((q << 2) | (q >> 6)) & 0xFF ^ ((q << 3) | (q >> 5)) & 0xFF ^ ((q << 4) | (q >> 4)) & 0xFF
        sb[p] = (x ^ 0x63) & 0xFF
        if p == 1:
            break
    sb[0] = 0x63
    return sb


SBOX = _sbox()


def _xt(a: int) -> int:
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1


def _expand(key: bytes) -> list[list[int]]:
    w = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    rcon = 1
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = [SBOX[t[1]] ^ rcon, SBOX[t[2]], SBOX[t[3]], SBOX[t[0]]]
            rcon = _xt(rcon)
        w.append([w[i - 4][j] ^ t[j] for j in range(4)])
    return [sum(w[r * 4:r * 4 + 4], []) for r in range(11)]


def _encrypt_block(rk: list[list[int]], block: bytes) -> bytes:
    s = [b ^ k for b, k in zip(block, rk[0])]
    for rnd in range(1, 11):
        s = [SBOX[b] for b in s]
        s = [s[(i + 4 * (i % 4)) % 16] for i in range(16)]  # ShiftRows (column-major state)
        if rnd != 10:
            m = []
            for c in range(4):
                a = s[c * 4:c * 4 + 4]
                t = a[0] ^ a[1] ^ a[2] ^ a[3]
                m += [a[i] ^ t ^ _xt(a[i] ^ a[(i + 1) % 4]) for i in range(4)]
            s = m
        s = [b ^ k for b, k in zip(s, rk[rnd])]
    return bytes(s)


def _gmul(x: int, y: int) -> int:
    R, z, v = 0xE1 << 120, 0, y
    for i in range(127, -1, -1):
        if (x >> i) & 1:
            z ^= v
        v = (v >> 1) ^ R if v & 1 else v >> 1
    return z


def _ghash(h: int, aad: bytes, ct: bytes) -> int:
    y = 0
    for data in (aad, ct):
        for i in range(0, len(data), 16):
            y = _gmul(y ^ int.from_bytes(data[i:i + 16].ljust(16, b"\x00"), "big"), h)
    lens = (len(aad) * 8).to_bytes(8, "big") + (len(ct) * 8).to_bytes(8, "big")
    return _gmul(y ^ int.from_bytes(lens, "big"), h)


def _gctr(rk, j: bytes, data: bytes) -> bytes:
    out, ctr = bytearray(), int.from_bytes(j[12:], "big")
    for i in range(0, len(data), 16):
        ctr = (ctr + 1) & 0xFFFFFFFF
        ks = _encrypt_block(rk, j[:12] + ctr.to_bytes(4, "big"))
        out += bytes(a ^ b for a, b in zip(data[i:i + 16], ks))
    return bytes(out)


def aes_gcm_encrypt(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    """Ciphertext ‖ 16-byte tag (a 12-byte nonce)."""
    rk = _expand(key)
    h = int.from_bytes(_encrypt_block(rk, b"\x00" * 16), "big")
    j0 = nonce + b"\x00\x00\x00\x01"
    ct = _gctr(rk, j0, plaintext)
    tag = int.from_bytes(_encrypt_block(rk, j0), "big") ^ _ghash(h, aad, ct)
    return ct + tag.to_bytes(16, "big")


def aes_gcm_decrypt(key: bytes, nonce: bytes, data: bytes, aad: bytes = b"") -> bytes:
    rk = _expand(key)
    h = int.from_bytes(_encrypt_block(rk, b"\x00" * 16), "big")
    j0 = nonce + b"\x00\x00\x00\x01"
    ct, tag = data[:-16], data[-16:]
    want = int.from_bytes(_encrypt_block(rk, j0), "big") ^ _ghash(h, aad, ct)
    if not _hmac.compare_digest(want.to_bytes(16, "big"), tag):
        raise ValueError("bad tag")
    return _gctr(rk, j0, ct)


# ------------------------------------------------------------------ RFC 8291: the payload
def _h(key: bytes, data: bytes) -> bytes:
    return _hmac.new(key, data, hashlib.sha256).digest()


def _keys(ecdh_secret: bytes, auth: bytes, ua_pub: bytes, as_pub: bytes, salt: bytes) -> tuple[bytes, bytes]:
    ikm = _h(_h(auth, ecdh_secret), b"WebPush: info\x00" + ua_pub + as_pub + b"\x01")
    prk = _h(salt, ikm)
    return _h(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16], _h(prk, b"Content-Encoding: nonce\x00\x01")[:12]


def encrypt(plaintext: bytes, p256dh: str, auth: str, as_private: int | None = None, salt: bytes | None = None) -> bytes:
    """The request body for a subscription's keys: salt, record size, the server's one-time public key, one record."""
    ua_pub, secret = b64u_dec(p256dh), b64u_dec(auth)
    point(ua_pub)
    if len(secret) != 16:
        raise ValueError("auth: 16 bytes")
    if len(plaintext) > MAX_PLAINTEXT:
        raise ValueError(f"payload too large ({len(plaintext)} > {MAX_PLAINTEXT} bytes)")
    d = as_private or new_private()
    as_pub, salt = public_key(d), salt or secrets.token_bytes(16)
    cek, nonce = _keys(ecdh(d, ua_pub), secret, ua_pub, as_pub, salt)
    body = aes_gcm_encrypt(cek, nonce, plaintext + b"\x02")
    return salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_pub)]) + as_pub + body


def decrypt(body: bytes, ua_private: int, auth: str) -> bytes:
    """The subscriber's side (tests, and a check of encrypt)."""
    salt, idlen = body[:16], body[20]
    as_pub, data = body[21:21 + idlen], body[21 + idlen:]
    ua_pub = public_key(ua_private)
    cek, nonce = _keys(ecdh(ua_private, as_pub), b64u_dec(auth), ua_pub, as_pub, salt)
    out = aes_gcm_decrypt(cek, nonce, data)
    end = out.rstrip(b"\x00")
    if not end.endswith(b"\x02"):
        raise ValueError("no last-record delimiter")
    return end[:-1]


# ------------------------------------------------------------------ RFC 8292: VAPID
def new_vapid() -> dict:
    d = new_private()
    return {"publicKey": b64u_enc(public_key(d)), "privateKey": b64u_enc(d.to_bytes(32, "big"))}


def vapid_authorization(endpoint: str, vapid: dict, subject: str, now: float | None = None) -> str:
    u = urlparse(endpoint)
    head = b64u_enc(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u_enc(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int((now or time.time()) + 3600), "sub": subject},
                                 separators=(",", ":")).encode())
    sig = sign(int.from_bytes(b64u_dec(vapid["privateKey"]), "big"), f"{head}.{claims}".encode())
    return f"vapid t={head}.{claims}.{b64u_enc(sig)}, k={vapid['publicKey']}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # (a push service never redirects: a redirect could lead anywhere)
        return None


def send(sub: dict, payload: bytes, vapid: dict, subject: str, ttl: int = 86400, urgency: str = "normal",
         timeout: float = 15, allow: tuple[str, ...] = ()) -> dict:
    """POST one notification: {"outcome": "accepted" | "gone" | "error", "status", "reason"?}."""
    endpoint = str(sub.get("endpoint") or "")
    if not allowed_endpoint(endpoint, allow):
        return {"outcome": "error", "status": 0, "reason": "endpoint not in the push-service allowlist"}
    keys = sub.get("keys") or {}
    try:
        body = encrypt(payload, keys.get("p256dh", ""), keys.get("auth", ""))
    except ValueError as e:
        return {"outcome": "error", "status": 0, "reason": f"keys: {e}"}
    req = urllib.request.Request(endpoint, method="POST", data=body, headers={
        "Authorization": vapid_authorization(endpoint, vapid, subject), "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream", "TTL": str(int(ttl)), "Urgency": urgency})
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout) as r:
            return {"outcome": "accepted", "status": r.status}
    except urllib.error.HTTPError as e:
        text = e.read()[:300].decode("utf-8", "replace")
        try:
            reason = json.loads(text).get("reason") or text
        except (ValueError, AttributeError):
            reason = text
        return {"outcome": "gone" if e.code in (404, 410) else "error", "status": e.code, "reason": reason or e.reason}
    except (urllib.error.URLError, OSError) as e:
        return {"outcome": "error", "status": 0, "reason": f"network: {getattr(e, 'reason', e)}"}
