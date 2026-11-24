"""A GPT Live call (builddoc/gpt-live.md), one at a time per server.

The page sends its SDP offer; this server creates the Live session with POST /v1/live/sessions (the key never leaves
it), hands the SDP answer back, and attaches the sideband websocket (/v1/live/sessions/{id}/attach). Everything else
happens there: transcripts (kept by start_ms), delegations (a request in the bound Orbit session, sent once), the
card's progress (silent thinking) and its result (one commentary), standby, hang-up (session.close, then the
transcript as a note card). Each stage is logged with its time and ids in <state>/live/<call>.jsonl (never the key,
never audio).
"""

from __future__ import annotations

import copy
import json
import queue
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orbit.services import keys
from orbit.services.live import ws as wsock

MODELS = ("gpt-live-1.1", "gpt-live-1")
VOICE = "marin"
ACK_DELAY_MS = 800  # time to cancel the automatic spoken acknowledgement with an empty thinking append (§5)
IDLE_TIMEOUT_S = 30  # no transcript either way for this long: standby (§2)
GRACE_S = 0.4  # after a delegation, late transcript fragments still join its request
PROGRESS_EVERY_S = 10.0  # at most one progress line per delegation this often
SPEAKING_HOLD_S = 1.2  # "speaking" this long after the last fragment of the voice's speech
RECONNECT_BASE_S, RECONNECT_MAX_S, STABLE_S = 0.2, 5.0, 30.0  # sideband reattach backoff (as Codex)
ATTACH_GIVE_UP_S = 30.0  # never attached in this long: the call ends
CLOSE_WAIT_S = 5.0  # hang-up: wait this long for session.closed
APPEND_TOKENS = 450  # an append may carry 500 tokens; a little room for the estimate
FINAL = ("completed", "error", "cancelled", "paused")
ACTIVE = ("thinking", "running")
# What the page's data channel may carry: transcripts and session state for its captions and button; it may send
# nothing (only this server delegates, adds context or closes the call).
PAGE_EVENTS = ("session.started", "session.input_transcript.delta", "session.output_transcript.delta",
               "session.paused", "session.resumed", "session.closed")
REFERS = re.compile(r"\b(that|this|it|those|these|them|the (first|second|third|last|other|same) one|the same)\b", re.I)
CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿]")


class NoKey(ValueError):
    """No OpenAI API key is set (Settings → General)."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def est_tokens(text: str) -> int:
    """A rough token count: a CJK character is about one, other text about four characters to one."""
    n = len(CJK.findall(text))
    return n + (len(text) - n + 3) // 4


def cut_tokens(text: str, limit: int) -> tuple[str, bool]:
    """text within about limit tokens, cut at a sentence (else a word) end; (text, cut?)."""
    if est_tokens(text) <= limit:
        return text, False
    lo, hi = 0, len(text)
    while lo < hi:  # the longest prefix within the limit
        mid = (lo + hi + 1) // 2
        if est_tokens(text[:mid]) <= limit:
            lo = mid
        else:
            hi = mid - 1
    head = text[:lo]
    m = max(head.rfind(". "), head.rfind("。"), head.rfind("! "), head.rfind("? "), head.rfind("\n"))
    if m > lo // 2:
        head = head[: m + 1]
    elif " " in head[lo // 2:]:
        head = head[: head.rfind(" ")]
    return head.rstrip(), True


def http_base() -> str:
    return keys.OPENAI_BASE.rstrip("/")


def ws_base() -> str:
    b = http_base()
    return "wss://" + b[len("https://"):] if b.startswith("https://") else "ws://" + b[len("http://"):] if b.startswith("http://") else b


# ---------------------------------------------------------------------------------------------- the session's setup
def instructions(name: str, cwd: str, model: str) -> str:
    """§3, in the structure of OpenAI's live prompting template (its Backchannel / Interruption / Delegation policy
    headings), with Orbit's noise rule. For gpt-live-1 the guide's handoff words are its token."""
    text = f"""You are the voice of Orbit, an IDE where the user works with coding agents. You talk with the user; an agent \
session named "{name}" (folder {cwd}) does the work: it is your backend.
Speak naturally and briefly. Be clear and direct.

Backchannel policy: Listen mostly in silence. An occasional quiet "mm-hm" at a natural pause is fine.

Interruption policy: Stop speaking when the user interrupts, and listen. Keep listening through coughs, throat-clears, \
sighs, a brief "mm-hm", music, other people talking, and thinking pauses: none of them is a request. Respond to a clear \
request, a question, or being addressed. After you answer, wait for the user; do not follow up on your own unless a \
result, a blocker or a missing detail arrives.

Delegation policy:
Backend tools:
- The agent session "{name}": it reads and edits the files in {cwd}, runs commands and tools, and knows the session's \
history and current state.
Delegate to the backend when:
- The user asks for any action or task.
- The answer needs the session's files, tools, current state or history.
- A correction changes the work already requested.
Do not delegate when:
- The question is self-contained, or about what was already said or reported in this call.
- You need a brief clarification to understand the request.
Delegate to the backend before giving an answer that depends on backend work. Do not guess the result while waiting.

Avoid unnecessary narration: no repeated confirmations, filler, re-acknowledgements or play-by-play. When a result \
arrives, give the takeaway in one to three short sentences; code, tables and logs stay in Orbit."""
    if model == "gpt-live-1":
        text = text.replace("Delegate to the backend", "Generate <|SpawnThinking|>").replace(
            "Do not delegate", "Do not generate <|SpawnThinking|>")
    return text


def _msg(role: str, text: str) -> dict:
    return {"type": "message", "role": role,
            "content": [{"type": "output_text" if role == "assistant" else "input_text", "text": text}]}


def startup_input(desk, s) -> list[dict]:
    """§4: the session, its last 3 requests and answers (each answer cut to 600 characters), what still runs or
    waits; observed_at on each. About 2 000 tokens at most."""
    from orbit.desk.session import DRIVERS, clip
    from orbit.drivers.base import strip_orbit_line
    desk._ensure_loaded(s)
    with desk.lock:
        cards = [copy.deepcopy(c) for c in s.cards if c.get("kind") != "note" and not c.get("hidden")]
        pub = s.public()
    at = now_iso()
    label = getattr(DRIVERS.get(s.harness), "label", s.harness)
    running = [c for c in cards if c["state"] in ACTIVE]
    waiting = [c for c in cards if c["state"] == "queued"]
    line = lambda c: clip(strip_orbit_line(c.get("text") or ""), 160)
    facts = (f'The Orbit session "{s.name}": harness {label}, folder {s.cwd}, permission mode {s.mode}, '
             f'{"working" if running else pub.get("state", "ready")}'
             + (f' on request #{running[0]["id"]}: {line(running[0])}' if running else "") + f". (observed_at {at})")
    items = [_msg("developer", facts)]
    done = [c for c in cards if c["state"] in FINAL][-3:]
    if done:
        items.append(_msg("developer", f"Its last {len(done)} requests and the agent's answers follow: user is the "
                                       f"request as sent to the agent, assistant the agent's answer, cut. (observed_at {at})"))
        for c in done:
            items.append(_msg("user", f"#{c['id']}: {clip(strip_orbit_line(c.get('text') or ''), 300)}"))
            ans = (c.get("answer") or "").strip()
            items.append(_msg("assistant", ans[:600] + ("…" if len(ans) > 600 else "") if ans
                              else f"(no answer: the request {c['state']}{': ' + c['error'] if c.get('error') else ''})"))
    if running or waiting:
        rows = [f"#{c['id']} ({c['state']}): {line(c)}" for c in running + waiting]
        items.append(_msg("developer", "Requests of this session still running or queued: " + "; ".join(rows)
                          + f". (observed_at {at})"))
    return items


def session_body(desk, s, sdp: str, model: str) -> dict:
    """The POST /v1/live/sessions body (§2): fixed for the call's life."""
    return {
        "session": {
            "model": model,
            "instructions": instructions(s.name, s.cwd, model),
            "input": startup_input(desk, s),
            "audio": {"output": {"voice": VOICE}},
            "delegation": {"type": "client", "acknowledgement_delay_ms": ACK_DELAY_MS},
            "wake_policy": {"type": "speech", "vad_threshold": 0.5},
            "idle_timeout_s": IDLE_TIMEOUT_S,
            "store": False,
            "client": {"data_channel": {"allowed_client_events": [],
                                        "allowed_server_events": [{"type": t} for t in PAGE_EVENTS]}},
        },
        "transport": {"type": "webrtc", "sdp": sdp},
    }


# ---------------------------------------------------------------------------------------------- transcripts
def ordered(frags: list[dict]) -> list[dict]:
    """By the session's timeline (start_ms), never by arrival."""
    return sorted(frags, key=lambda f: (f["start"], f["end"], f["seq"]))


def assemble(frags: list[dict], offset_ms: int, since_ms: int, used: set) -> tuple[str, list]:
    """The user's words for a delegation at offset_ms: their fragments not taken by an earlier delegation that start
    before offset_ms (one that straddles it included) and end after since_ms (the previous delegation's offset, or
    the call's start), in timeline order, joined exactly as received. (text, the fragments' keys)."""
    take = [f for f in ordered(frags) if f["who"] == "user" and f["key"] not in used
            and f["start"] < offset_ms and f["end"] > since_ms]
    return "".join(f["text"] for f in take).strip(), [f["key"] for f in take]


def last_reply(frags: list[dict], before_ms: int, gap_ms: int = 1500) -> str:
    """The voice's last spoken reply before before_ms: its last run of fragments with no pause longer than gap_ms."""
    out = [f for f in ordered(frags) if f["who"] == "voice" and f["start"] < before_ms]
    run: list[dict] = []
    for f in reversed(out):
        if run and run[0]["start"] - f["end"] > gap_ms:
            break
        run.insert(0, f)
    return "".join(f["text"] for f in run).strip()


def turns(frags: list[dict]) -> list[tuple[str, int, str]]:
    """The transcript as turns: consecutive fragments of one speaker by start_ms. [(who, start_ms, text)]."""
    out: list[list] = []
    for f in ordered(frags):
        if out and out[-1][0] == f["who"]:
            out[-1][2] += f["text"]
        else:
            out.append([f["who"], f["start"], f["text"]])
    return [(w, t, x.strip()) for w, t, x in out if x.strip()]


def takeaway(answer: str) -> tuple[str, bool]:
    """An answer for speaking (§6): code blocks, tables and images left out, cut to its first paragraphs within an
    append's limit. (text, whether anything stays only in Orbit)."""
    text, left = re.subn(r"(?ms)^\s*(```|~~~).*?^\s*\1[^\n]*$", "", answer or "")
    lines, kept = [], False
    for ln in text.splitlines():
        if re.match(r"\s*\|", ln) or re.match(r"\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", ln):
            left += 1
            continue
        lines.append(ln)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", "\n".join(lines))
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n", text) if p.strip()]
    out = ""
    for p in paras:
        nxt = (out + "\n\n" + p) if out else p
        if est_tokens(nxt) > APPEND_TOKENS - 60:
            if not out:
                out, _ = cut_tokens(p, APPEND_TOKENS - 60)
            left += 1
            kept = True
            break
        out = nxt
    return out, bool(left) or kept


def result_text(c: dict) -> str:
    """The one commentary for a finished card."""
    n = c["id"]
    if c["state"] == "completed":
        body, more = takeaway(c.get("answer") or "")
        if not body:
            return f"Request #{n} is done; its answer is only code or tables: it is in Orbit."
        return f"Request #{n} is done. {body}" + (" The rest is in Orbit." if more else "")
    err = " ".join((c.get("error") or "").split())[:300]
    if c["state"] == "cancelled":
        return f"Request #{n} was stopped."
    if c["state"] == "paused":
        return f"Request #{n} paused" + (f": {err}" if err else "") + "."
    return f"Request #{n} failed" + (f": {err}" if err else "") + "."


def progress_line(c: dict) -> str:
    """A factual line about a running card: the step at work."""
    tool = next((st for st in reversed(c.get("steps") or []) if st.get("kind") == "tool" and not st.get("done")), None)
    if tool:
        what = " ".join(f"{tool.get('name', '')} {tool.get('summary', '')}".split())[:200]
        return f"Request #{c['id']} is still running: {what}. Nothing to say yet."
    return f"Request #{c['id']} is still running. Nothing to say yet."


# ---------------------------------------------------------------------------------------------- the call
class Call:
    def __init__(self, live: "Live", sid: str, model: str):
        self.live, self.desk, self.sid, self.model = live, live.desk, sid, model
        self.id = "call-" + uuid.uuid4().hex[:10]
        self.t0 = time.time()
        self.lock = threading.RLock()
        self.phase = "connecting"
        self.live_id = ""
        self.expires_at: int | None = None
        self.reason = ""
        self.error = ""
        self.usage: dict = {}
        self.frags: list[dict] = []
        self.seq = 0
        self.seen: set[str] = set()  # event ids: a replay after a reattach is not handled twice
        self.dups = 0
        self.used: set = set()  # fragments a delegation took
        self.cut_ms = 0  # the last delegation's offset
        self.dels: dict[str, dict] = {}  # delegation id → {offset, cid, how, …}
        self.order: list[str] = []  # delegation ids as they came
        self.card_del: dict[int, str] = {}  # card id → the delegation its result answers (the latest)
        self.ws: wsock.WebSocket | None = None
        self.attached = 0
        self.outbox: list[dict] = []
        self.last_voice = 0.0
        self.closing = False
        self.ended = False
        self.closed_ev = threading.Event()
        self.stop_ev = threading.Event()
        self.note: dict | None = None
        d = Path(self.desk.state_dir) / "live"
        d.mkdir(parents=True, exist_ok=True)
        self.log_path = d / f"{self.id}.jsonl"
        self.log_lock = threading.Lock()
        self.q: queue.Queue | None = None

    # ------------------------------------------------------------------ log and page
    def log(self, stage: str, **kw: Any) -> None:
        row = {"t": round(time.time(), 3), "ms": int((time.time() - self.t0) * 1000), "stage": stage, "call": self.id,
               **({"live": self.live_id} if self.live_id else {}), **kw}
        with self.log_lock:
            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            except OSError:
                pass

    def state(self, transcript: bool = True) -> dict:
        with self.lock:
            out = {"call": self.id, "phase": self.phase, "sid": self.sid, "live": self.live_id, "started": self.t0,
                   "expires_at": self.expires_at, "reason": self.reason, "error": self.error, "model": self.model,
                   "delegations": [{"id": k, **{x: self.dels[k].get(x) for x in ("cid", "how", "text", "done")}}
                                   for k in self.order]}
            if transcript:
                out["transcript"] = [self._cap(f) for f in ordered(self.frags)]
        return out

    @staticmethod
    def _cap(f: dict) -> dict:
        return {"id": f["key"], "who": f["who"], "text": f["text"], "start": f["start"], "end": f["end"]}

    def _tell(self, kind: str, **kw: Any) -> None:
        self.desk.broadcast({"t": "live", "kind": kind, "call": self.id, **kw})

    def _set_phase(self, p: str) -> None:
        with self.lock:
            if self.phase == p or (self.phase == "ended" and p != "ended"):
                return
            self.phase = p
        self.log("phase", phase=p)
        self._tell("state", state=self.state(transcript=False))

    # ------------------------------------------------------------------ create
    def create(self, sdp: str) -> dict:
        key = keys.get(self.desk.state_dir, "openai")
        if not key:
            raise NoKey("No OpenAI API key: set one in Settings → General")
        s = self.desk.get(self.sid)
        body = session_body(self.desk, s, sdp, self.model)
        self.log("create", sid=self.sid, model=self.model, input=len(body["session"]["input"]),
                 instructions_tokens=est_tokens(body["session"]["instructions"]), offer_bytes=len(sdp))
        req = urllib.request.Request(f"{http_base()}/v1/live/sessions", data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        t = time.time()
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read() or b"{}")
                status = r.status
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read(65536) or b"{}").get("error") or {}
                msg = err.get("message") or ""
            except (ValueError, AttributeError):
                msg = ""
            self.log("create_failed", status=e.code, error=msg[:500], ms_create=int((time.time() - t) * 1000))
            self._fail(f"OpenAI answered {e.code}" + (f": {msg}" if msg else ""))
        except (OSError, ValueError) as e:
            self.log("create_failed", error=str(e)[:300])
            self._fail(f"could not reach OpenAI: {e}")
        self.live_id = str(((d.get("session") or {}).get("id")) or "")
        answer = str(((d.get("transport") or {}).get("sdp")) or "")
        if not self.live_id or not answer:
            self.log("create_failed", status=status, error="no session id or SDP answer")
            self._fail("OpenAI's answer had no session id or SDP answer")
        self.log("created", status=status, ms_create=int((time.time() - t) * 1000), answer_bytes=len(answer))
        self.q = self.desk.subscribe()
        for fn in (self._sideband, self._follow_cards, self._tick):
            threading.Thread(target=fn, daemon=True, name=f"live-{fn.__name__}").start()
        return {"call": self.id, "sdp": answer, "live": self.live_id}

    def _fail(self, msg: str) -> None:
        with self.lock:
            self.error, self.ended, self.phase = msg, True, "ended"
        self._tell("state", state=self.state(transcript=False))
        raise ValueError(msg)

    # ------------------------------------------------------------------ the sideband
    def _sideband(self) -> None:
        rapid = 0
        while not self.ended:
            key = keys.get(self.desk.state_dir, "openai")
            url = f"{ws_base()}/v1/live/sessions/{self.live_id}/attach"
            t = time.time()
            try:
                ws = wsock.connect(url, headers={"Authorization": f"Bearer {key}"}, timeout=10)
            except wsock.HandshakeError as e:
                self.log("attach_failed", status=e.status, error=e.body[:300], attempt=rapid + 1)
                gone = e.status in (404, 410) and self.attached  # the session ended while we were away
                if gone or e.status in (401, 403):
                    self._finish(self.reason or ("gone" if gone else "attach refused"),
                                 error="" if gone else f"the sideband was refused (HTTP {e.status})")
                    return
            except OSError as e:
                self.log("attach_failed", error=str(e)[:300], attempt=rapid + 1)
            else:
                with self.lock:
                    self.ws, self.attached = ws, self.attached + 1
                    out, self.outbox = self.outbox, []
                self.log("attached", n=self.attached, ms_attach=int((time.time() - t) * 1000), resend=len(out))
                if self.phase == "connecting":
                    self._set_phase("listening")
                for ev in out:
                    self._send(ev)
                connected = time.time()
                try:
                    while True:
                        self._on_message(ws.recv())
                except wsock.Closed as e:
                    self.log("sideband_closed", code=e.code, reason=e.reason[:200])
                except Exception as e:  # noqa: BLE001 - a bug in a handler must not end the call silently
                    self.log("sideband_error", error=f"{type(e).__name__}: {e}"[:300])
                    ws.abort()
                with self.lock:
                    if self.ws is ws:
                        self.ws = None
                if self.ended or self.closed_ev.is_set():
                    return
                if time.time() - connected >= STABLE_S:
                    rapid = 0
            if not self.attached and time.time() - self.t0 > ATTACH_GIVE_UP_S:
                self._finish("attach failed", error="could not attach to the voice session")
                return
            rapid += 1
            delay = min(RECONNECT_BASE_S * 2 ** (rapid - 1), RECONNECT_MAX_S)
            self.log("reattach", delay_ms=int(delay * 1000))
            if self.stop_ev.wait(delay):
                return

    def _send(self, ev: dict) -> None:
        """A client event on the sideband; while it is down, queued for the next attach."""
        with self.lock:
            ws = self.ws
            if ws is None:
                self.outbox.append(ev)
                return
        try:
            ws.send(json.dumps(ev))
        except OSError:
            with self.lock:
                self.outbox.append(ev)
            return
        self.log("sent", type=ev["type"], event_id=ev.get("event_id"), delegation=ev.get("delegation_id"),
                 **({"content": ev["content"]} if "content" in ev else {}))

    def _on_message(self, msg: str | bytes) -> None:
        try:
            ev = json.loads(msg)
        except ValueError:
            return
        if not isinstance(ev, dict):
            return
        typ, eid = ev.get("type", ""), ev.get("event_id")
        if typ in ("session.output_audio.delta", "session.input_audio.append"):
            return  # reflected audio (no event_id): the page plays the media track
        if eid:
            with self.lock:
                if eid in self.seen:
                    self.dups += 1
                    dup = True
                else:
                    self.seen.add(eid)
                    dup = False
            if dup:
                self.log("replayed", type=typ, event_id=eid)
                return
        if typ in ("session.input_transcript.delta", "session.output_transcript.delta"):
            return self._on_transcript(ev, "user" if typ.startswith("session.input") else "voice")
        if typ == "session.delegation.created":
            return self._on_delegation(ev)
        info = {k: ev[k] for k in ("client_event_id", "start_ms", "end_ms", "reason", "code", "message") if k in ev}
        self.log("event", type=typ, event_id=eid, **info)
        if typ in ("session.started", "session.updated"):
            se = ev.get("session") or {}
            with self.lock:
                self.expires_at = se.get("expires_at") or self.expires_at
            if self.phase == "connecting":
                self._set_phase("listening")
        elif typ == "session.paused":
            self._set_phase("standby")
        elif typ == "session.resumed":
            self._set_phase("listening")
        elif typ == "session.usage.updated":
            with self.lock:
                self.usage = ev.get("usage") or {}
        elif typ == "session.closed":
            with self.lock:
                self.reason = str(ev.get("reason") or "closed")
                self.usage = ev.get("usage") or self.usage
            self.closed_ev.set()
            if not self.closing:  # it ended over there (expired, the page went, a safety filter): finish here
                threading.Thread(target=self._finish, args=(self.reason,), daemon=True).start()
        elif typ == "error":
            err = ev.get("error") or {}
            self.log("error", code=err.get("code"), message=str(err.get("message") or "")[:500],
                     client_event_id=err.get("client_event_id"), param=err.get("param"))
            if not self.closing and not self.ended:
                self._tell("error", message=str(err.get("message") or err.get("code") or "error")[:300])

    def _on_transcript(self, ev: dict, who: str) -> None:
        with self.lock:
            self.seq += 1
            f = {"key": ev.get("event_id") or f"x{self.seq}", "who": who, "text": str(ev.get("delta") or ""),
                 "start": int(ev.get("start_ms") or 0), "end": int(ev.get("end_ms") or ev.get("start_ms") or 0),
                 "seq": self.seq}
            self.frags.append(f)
            if who == "voice":
                self.last_voice = time.time()
        self.log("transcript", who=who, event_id=f["key"], start_ms=f["start"], end_ms=f["end"], text=f["text"])
        self._tell("caption", caption=self._cap(f))
        if who == "voice":
            self._set_phase("speaking")
        elif self.phase in ("standby", "connecting"):
            self._set_phase("listening")

    # ------------------------------------------------------------------ delegations → requests
    def _on_delegation(self, ev: dict) -> None:
        d = ev.get("delegation") or {}
        did = str(d.get("id") or "")
        if not did or (d.get("target") or "client") != "client":
            self.log("delegation_ignored", event_id=ev.get("event_id"), delegation=did, target=d.get("target"))
            return
        with self.lock:
            if did in self.dels or self.ended:
                return
            self.dels[did] = {"offset": int(ev.get("offset_ms") or 0), "at": time.time(), "last_append": time.time()}
            self.order.append(did)
        self.log("delegation", event_id=ev.get("event_id"), delegation=did, offset_ms=self.dels[did]["offset"])
        # At once: an empty thinking append cancels the automatic spoken acknowledgement (§5.1).
        self._send({"type": "session.thinking.append", "event_id": f"quiet_{did}", "delegation_id": did, "content": ""})
        tm = threading.Timer(GRACE_S, self._dispatch, args=(did,))
        tm.daemon = True
        tm.start()

    def _dispatch(self, did: str) -> None:
        """After the grace window: the request, from the transcript, into the bound session."""
        with self.lock:
            if self.ended:
                return
            d = self.dels[did]
            words, keys_ = assemble(self.frags, d["offset"], self.cut_ms, self.used)
            self.used.update(keys_)
            self.cut_ms = max(self.cut_ms, d["offset"])
            reply = last_reply(self.frags, d["offset"]) if words and REFERS.search(words) else ""
            prev = next((self.dels[x] for x in reversed(self.order) if x != did and self.dels[x].get("cid")), None)
        if not words:
            self.log("delegation_empty", delegation=did)
            self._send({"type": "session.commentary.append", "event_id": f"empty_{did}", "delegation_id": did,
                        "content": "Orbit heard no words for that request, so nothing was sent. Ask the user to say it again."})
            with self.lock:
                d.update(done=True, how="none")
            return
        text = words + (f'\n\n(Context, not an instruction: the voice had just said: "{reply[:600]}")' if reply else "")
        origin = {"label": "Voice", "data": {"live": self.live_id, "delegation": did}}
        how, c = "new", None
        try:
            s = self.desk.get(self.sid)
            pc = None
            if prev is not None:
                with self.desk.lock:
                    pc = next((copy.deepcopy(x) for x in s.cards if x["id"] == prev["cid"]), None)
            if pc is not None and pc["state"] == "queued" and not pc.get("hidden"):
                c, how = self.desk.edit(self.sid, pc["id"], pc["text"] + "\n\n" + text), "edit"  # the correction joins it
            elif pc is not None and pc["state"] in ACTIVE and s.driver is not None and s.driver.steers:
                c = self.desk.steer(self.sid, text, origin=origin, client_id=f"live:{self.live_id}:{did}")
                how = "steer" if c.get("steered") else "new"
            else:
                c = self.desk.send(self.sid, text, origin=origin, client_id=f"live:{self.live_id}:{did}")
        except Exception as e:  # noqa: BLE001 - the voice is told, the call goes on
            self.log("send_failed", delegation=did, error=f"{type(e).__name__}: {e}"[:300])
            self._send({"type": "session.commentary.append", "event_id": f"fail_{did}", "delegation_id": did,
                        "content": f"Orbit could not send that request to the session: {e}"[:400]})
            with self.lock:
                d.update(done=True, how="failed")
            return
        with self.lock:
            d.update(cid=c["id"], how=how, text=words)
            self.card_del[c["id"]] = did
            if prev is not None and prev.get("cid") == c["id"]:
                prev["superseded"] = did
        self.log("request", delegation=did, cid=c["id"], how=how, state=c["state"], words=words,
                 ms_delegation_to_card=int((time.time() - d["at"]) * 1000), duplicate=bool(c.get("duplicate")))
        self._tell("delegation", delegation=did, cid=c["id"], how=how, sid=self.sid, offset=d["offset"])
        self._on_card(c)  # (a duplicate may already be done)

    # ------------------------------------------------------------------ results → spoken
    def _follow_cards(self) -> None:
        q = self.q
        while not self.ended and q is not None:
            try:
                data = q.get(timeout=0.5)
            except queue.Empty:
                continue
            if '"t": "card"' not in data[:20]:
                continue
            try:
                c = json.loads(data).get("card") or {}
            except ValueError:
                continue
            if c.get("sid") == self.sid and c.get("id") in self.card_del:
                self._on_card(c)

    def _on_card(self, c: dict) -> None:
        with self.lock:
            did = self.card_del.get(c["id"])
            d = self.dels.get(did) if did else None
            if d is None or d.get("done") or self.ended:
                return
            st = c.get("state")
            retrying = st == "paused" and "retrying in" in (c.get("error") or "")
            if st in FINAL and not retrying:
                d["done"] = True
                ev = {"type": "session.commentary.append", "event_id": f"result_{did}", "delegation_id": did,
                      "content": result_text(c)}
            elif st in ACTIVE and time.time() - d["last_append"] >= PROGRESS_EVERY_S:
                line = progress_line(c)
                if line == d.get("last_line"):
                    return
                d["last_append"], d["last_line"] = time.time(), line
                d["progress"] = d.get("progress", 0) + 1
                ev = {"type": "session.thinking.append", "event_id": f"progress_{did}_{d['progress']}",
                      "delegation_id": did, "content": line}
            else:
                return
        if ev["type"] == "session.commentary.append":
            self.log("result", delegation=did, cid=c["id"], state=c.get("state"),
                     ms_card_to_result=int((time.time() - (c.get("finished") or time.time())) * 1000))
        self._send(ev)

    # ------------------------------------------------------------------ the clock
    def _tick(self) -> None:
        n = 0
        while not self.stop_ev.wait(0.25):
            n += 1
            if self.phase == "speaking" and time.time() - self.last_voice > SPEAKING_HOLD_S:
                self._set_phase("listening")
            if self.expires_at and time.time() > self.expires_at + 10 and not self.closing:
                self._finish("expired")
                return
            if n % 100 == 0:  # every 25 s a ping keeps the sideband honest
                ws = self.ws
                if ws is not None:
                    try:
                        ws.ping()
                    except OSError:
                        pass

    # ------------------------------------------------------------------ hang-up
    def hang_up(self) -> dict:
        """session.close; wait for session.closed (a few seconds); then the sideband closes and the transcript goes
        into the bound session as a note card."""
        with self.lock:
            if self.closing or self.ended:
                return self.state(transcript=False)
            self.closing = True
        self.log("close")
        self._send({"type": "session.close", "event_id": f"close_{self.id}"})
        got = self.closed_ev.wait(CLOSE_WAIT_S)
        self.log("close_done", confirmed=got, reason=self.reason, usage=self.usage)
        self._finish(self.reason or "close_requested", confirmed=got)
        return self.state(transcript=False)

    def _finish(self, reason: str, error: str = "", confirmed: bool | None = None) -> None:
        with self.lock:
            if self.ended:
                return
            self.ended, self.closing = True, True
            self.reason = self.reason or reason
            self.error = self.error or error
        self.stop_ev.set()
        ws = self.ws
        if ws is not None:
            ws.close()
        if self.q is not None:
            self.desk.unsubscribe(self.q)
        try:
            self.note = self._note()
        except Exception as e:  # noqa: BLE001 - the call still ends
            self.log("note_failed", error=f"{type(e).__name__}: {e}"[:300])
        self.log("ended", reason=self.reason, error=self.error, confirmed=confirmed, usage=self.usage,
                 replayed=self.dups, attaches=self.attached, note=(self.note or {}).get("id"))
        with self.lock:
            self.phase = "ended"
        self._tell("state", state={**self.state(transcript=False), "note": (self.note or {}).get("id")})

    def _note(self) -> dict | None:
        """The call as a note card (no model call): its transcript by turn, the requests it made."""
        with self.lock:
            ts = turns(self.frags)
            reqs = [(k, self.dels[k]) for k in self.order if self.dels[k].get("cid") is not None]
        if not ts and not reqs:
            return None
        mins = max(1, round((time.time() - self.t0) / 60))
        when = datetime.fromtimestamp(self.t0).strftime("%H:%M")
        lines = []
        for who, start, text in ts:
            lines.append(f"**{'You' if who == 'user' else 'Voice'}** · {start // 60000}:{start // 1000 % 60:02d}  \n{text}")
        made = sorted({d['cid'] for _, d in reqs})
        tail = ("Requests from this call: " + " · ".join(f"#{c}" for c in made)) if made else "No requests from this call."
        why = {"close_requested": "", "expired": " It ended: the voice session's time ran out.",
               "remote_hangup": " It ended: the page's connection closed.", "connection_lost": " It ended: the connection was lost.",
               "content": " It ended: a safety filter stopped it."}.get(self.reason, f" It ended ({self.reason})." if self.reason else "")
        body = "\n\n".join(lines) + f"\n\n{tail}{why}"
        return self.desk.add_note(self.sid, f"Voice call · {when} · {mins} min", body,
                                  origin={"label": "Voice", "data": {"live": self.live_id, "call": self.id}})


class Live:
    """The server's voice calls: one at a time."""

    def __init__(self, desk):
        self.desk = desk
        self.call: Call | None = None
        self.lock = threading.Lock()

    def model(self) -> str:
        m = (self.desk.settings or {}).get("live_model") or MODELS[0]
        return m if m in MODELS else MODELS[0]

    def start(self, sid: str, sdp: str) -> dict:
        if not isinstance(sdp, str) or not sdp.strip():
            raise ValueError("an SDP offer is needed")
        self.desk.get(sid)  # (KeyError: no such session)
        if not keys.get(self.desk.state_dir, "openai"):
            raise NoKey("No OpenAI API key: set one in Settings → General")
        with self.lock:
            old, self.call = self.call, Call(self, sid, self.model())
            new = self.call
        if old is not None and not old.ended:  # one call at a time: a new one hangs up the last
            threading.Thread(target=old.hang_up, daemon=True).start()
        return new.create(sdp)

    def stop(self, call: str = "") -> dict:
        c = self.call
        if c is None or (call and c.id != call):
            raise KeyError(call or "call")
        return c.hang_up()

    def state(self) -> dict:
        c = self.call
        return {"call": None} if c is None else c.state()

    def shutdown(self) -> None:
        c = self.call
        if c is not None and not c.ended:
            c.hang_up()
