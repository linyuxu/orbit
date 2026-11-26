"""Notifications on the phone's lock screen (Web Push), sent by this server: who is subscribed, what was sent.

The phone (Orbit on its Home Screen, opened from this server's HTTPS address) turns them on in Misc → Notifications:
the browser asks the user, subscribes with this server's VAPID public key, and posts the subscription here
(POST /api/push/subscribe, behind the pairing token like every /api call). A notification names a session; a tap
opens it (web/m/sw.js). Two senders:
  a reminder   a schedule with `notify` (orbit-agent remind): at its time the server sends it, no agent wakes
  an agent     orbit-agent notify TITLE BODY: "the report is ready", pointing at its own session

Files in <state>/push/ (0600): vapid.json (this server's key pair, made once; the private key never leaves it),
subscriptions.json, sent.json (keys already delivered, per subscription: a reminder retried, or fired again after a
restart before its next time was saved, is not sent twice) and log.jsonl (each send: the push service's answer,
accepted / gone / error, never the body).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

from orbit.services import webpush

SENT_KEPT = 2000
LOG_SHOWN = 20
MAX_SUBS = 20
TITLE_MAX, BODY_MAX = 120, 1000
ALLOW_EXTRA: tuple[str, ...] = ()  # tests: their local push service's URL prefix (never set in the server)


def _write(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o700)
    tmp = p.with_name(p.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, p)


def _read(p: Path, default):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


class Push:
    def __init__(self, state_dir: Path, subject: str = ""):
        self.dir = Path(state_dir) / "push"
        self.state_dir = Path(state_dir)
        self.subject = subject
        self.lock = threading.RLock()
        self.sending = threading.Lock()  # one notification at a time: two sends of one key never race past sent.json

    # ---------- keys and subscriptions
    def vapid(self) -> dict:
        with self.lock:
            p = self.dir / "vapid.json"
            v = _read(p, None)
            if not (isinstance(v, dict) and v.get("publicKey") and v.get("privateKey")):
                v = webpush.new_vapid()
                _write(p, v)
            return v

    def public_key(self) -> str:
        return self.vapid()["publicKey"]

    def _subs(self) -> list[dict]:
        s = _read(self.dir / "subscriptions.json", [])
        return s if isinstance(s, list) else []

    @staticmethod
    def sub_id(endpoint: str) -> str:
        return hashlib.sha256(endpoint.encode()).hexdigest()[:12]

    def subscribe(self, sub: dict, label: str = "") -> dict:
        """Add (or renew) a device's subscription; ValueError when it is not a push service's or its keys are wrong."""
        if not isinstance(sub, dict):
            raise ValueError("no subscription")
        endpoint = str(sub.get("endpoint") or "")
        keys = sub.get("keys") if isinstance(sub.get("keys"), dict) else {}
        if not webpush.allowed_endpoint(endpoint, ALLOW_EXTRA):
            raise ValueError("not a push service's endpoint")
        try:
            webpush.point(webpush.b64u_dec(keys.get("p256dh", "")))
            if len(webpush.b64u_dec(keys.get("auth", ""))) != 16:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("the subscription's keys are not P-256 / 16 bytes") from None
        sid = self.sub_id(endpoint)
        with self.lock:
            subs = [s for s in self._subs() if s.get("id") != sid]
            old = next((s for s in self._subs() if s.get("id") == sid), {})
            rec = {"id": sid, "endpoint": endpoint, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]},
                   "label": str(label or old.get("label") or "")[:80], "created": old.get("created") or time.time(),
                   "last": old.get("last") or {}}
            subs.append(rec)
            _write(self.dir / "subscriptions.json", subs[-MAX_SUBS:])
        return self._pub(rec)

    def unsubscribe(self, endpoint: str = "", sid: str = "") -> bool:
        sid = sid or (self.sub_id(endpoint) if endpoint else "")
        with self.lock:
            subs = self._subs()
            keep = [s for s in subs if s.get("id") != sid]
            if len(keep) != len(subs):
                _write(self.dir / "subscriptions.json", keep)
            return len(keep) != len(subs)

    @staticmethod
    def _pub(s: dict) -> dict:
        from urllib.parse import urlparse
        return {"id": s["id"], "label": s.get("label", ""), "service": urlparse(s["endpoint"]).hostname or "",
                "created": s.get("created"), "last": s.get("last") or {}}

    def status(self, endpoint: str = "") -> dict:
        """For the phone's page: the public key, the devices (no endpoints, no keys), whether `endpoint` is one, the log."""
        subs = self._subs()
        return {"publicKey": self.public_key(), "subscriptions": [self._pub(s) for s in subs],
                "subscribed": bool(endpoint) and any(s["id"] == self.sub_id(endpoint) for s in subs),
                "log": self.log()[-LOG_SHOWN:]}

    def log(self) -> list[dict]:
        try:
            lines = (self.dir / "log.jsonl").read_text().splitlines()[-200:]
        except OSError:
            return []
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
        return out

    def _log(self, rec: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.dir / "log.jsonl"
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        try:
            if p.stat().st_size > 512_000:  # (keep the last part)
                tail = p.read_text().splitlines()[-1000:]
                _write_text(p, "\n".join(tail) + "\n")
        except OSError:
            pass

    # ---------- sending
    def origin(self) -> str:
        """This server's HTTPS address (the one the phone subscribed from), or '' when HTTPS is off."""
        from orbit.machines import https
        return https.saved_url(self.state_dir).rstrip("/")

    def payload(self, title: str, body: str, sid: str, key: str) -> bytes:
        """Declarative Web Push (WebKit, iOS 18.4+) when this server has an HTTPS address: {"web_push": 8030,
        "notification": {title, body, navigate}}. iOS opens `navigate` on a tap by itself: it does not reliably give a
        Home Screen app's service worker the tap (notificationclick). navigate is /m/?session=<id> (a page load, which
        opens it; a fragment alone would not load it again). Elsewhere the service worker shows it from the same
        message and opens the session on a tap (sw.js). Without an HTTPS address (no absolute URL to give): the plain
        message the service worker reads."""
        from urllib.parse import quote
        origin = self.origin()
        if origin:
            nav = f"{origin}/m/" + (f"?session={quote(sid, safe='')}&at={int(time.time())}" if sid else "")
            n = {"title": title, "navigate": nav}
            if body:
                n["body"] = body
            return json.dumps({"web_push": 8030, "notification": n}, ensure_ascii=False).encode()
        return json.dumps({"title": title, "body": body, "sid": sid, "url": "/m/" + (f"#s/{sid}" if sid else ""),
                           "tag": key or sid or "orbit"}, ensure_ascii=False).encode()

    def _subject(self) -> str:
        if self.subject:
            return self.subject
        from orbit.machines import https
        return https.saved_url(self.state_dir) or "https://localhost"

    def notify(self, title: str, body: str = "", sid: str = "", key: str = "", sub: str = "", via: str = "") -> dict:
        """Send to every subscribed device (or the one `sub` names). `key`: once per key and device (a retry, a
        restart in between: the devices that took it are not sent it again). Returns {sent, devices, results}."""
        title = (str(title or "").strip() or "Orbit")[:TITLE_MAX]
        body = str(body or "").strip()[:BODY_MAX]
        payload = self.payload(title, body, sid, key)
        vapid = self.vapid()
        with self.sending:
            return self._send_all(title, body, sid, key, sub, via, payload, vapid)

    def _send_all(self, title, body, sid, key, sub, via, payload, vapid) -> dict:
        with self.lock:
            subs = [s for s in self._subs() if not sub or s["id"] == sub]
            sent = _read(self.dir / "sent.json", {})
            sent = sent if isinstance(sent, dict) else {}
        results = []
        for s in subs:
            dk = f"{key}|{s['id']}" if key else ""
            if dk and dk in sent:
                results.append({"sub": s["id"], "outcome": "duplicate"})
                continue
            r = webpush.send(s, payload, vapid, self._subject(), ttl=86400, urgency="high" if key.startswith("sched:") else "normal",
                             allow=ALLOW_EXTRA)
            now = time.time()
            rec = {"at": now, "key": key, "sub": s["id"], "outcome": r["outcome"], "status": r["status"], "title": title,
                   "sid": sid, "via": via}
            if r.get("reason"):
                rec["reason"] = str(r["reason"])[:300]
            self._log(rec)
            with self.lock:
                if dk and r["outcome"] == "accepted":
                    sent = _read(self.dir / "sent.json", {})
                    sent = sent if isinstance(sent, dict) else {}
                    sent[dk] = now
                    if len(sent) > SENT_KEPT:
                        sent = dict(sorted(sent.items(), key=lambda kv: kv[1])[-SENT_KEPT:])
                    _write(self.dir / "sent.json", sent)
                subs_now = self._subs()
                if r["outcome"] == "gone":  # the device unsubscribed (or the app was removed): forget it
                    subs_now = [x for x in subs_now if x["id"] != s["id"]]
                else:
                    for x in subs_now:
                        if x["id"] == s["id"]:
                            x["last"] = {k: rec[k] for k in ("at", "outcome", "status")} | ({"reason": rec["reason"]} if "reason" in rec else {})
                _write(self.dir / "subscriptions.json", subs_now)
            results.append({"sub": s["id"], **r})
        ok = sum(1 for r in results if r["outcome"] == "accepted")
        dup = sum(1 for r in results if r["outcome"] == "duplicate")
        return {"sent": ok, "duplicate": dup, "devices": len(subs), "results": results}


def _write_text(p: Path, text: str) -> None:
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, p)


def of(desk) -> Push:
    """The desk's Push (made on first use)."""
    p = getattr(desk, "push", None)
    if p is None:
        p = desk.push = Push(desk.state_dir, subject=str((getattr(desk, "settings", {}) or {}).get("push_subject") or ""))
    return p
