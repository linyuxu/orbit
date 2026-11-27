"""A GPT Live call (builddoc/gpt-live.md), one at a time per server.

The page sends its SDP offer; this server creates the Live session with POST /v1/live/sessions (the key never leaves
it), hands the SDP answer back, and attaches the sideband websocket (/v1/live/sessions/{id}/attach). Everything else
happens there: transcripts (kept by start_ms), delegations (a request in the bound Orbit session, sent once), the
session's state (silent snapshots when it changes), each request's result (one commentary, held while the user
talks), standby, hang-up (session.close, then the transcript as a note card). Each stage is logged with its time and
ids in <state>/live/<call>.jsonl (never the key, never audio).
"""

from __future__ import annotations

import array
import base64
import collections
import copy
import json
import queue
import re
import struct
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orbit.services import keys
from orbit.services.live import config
from orbit.services.live import ws as wsock

ACK_DELAY_MS = 800  # time to cancel the automatic spoken acknowledgement with an empty thinking append (§5)
GRACE_S = 0.4  # after a delegation, late transcript fragments still join its request
# GPT Live delegates on its own (no turn-detection setting) and often while the user is still talking: in 258 real
# delegations the pause before it had a median of 200 ms, and in 67 the user went on within 2 s (the rest of the
# sentence became a second request). So a request waits for the user to finish: until no fragment of theirs came for
# the turn pause (Settings → Voice, live_turn_pause: 1.5 s; fragments arrive every ~200 ms while someone talks), at
# most TURN_WAIT_S; their words up to then (a run of fragments with no gap over that pause after the delegation's
# offset) are its words, and a delegation GPT Live makes meanwhile joins it (the request is answered once, for the
# latest).
TURN_WAIT_S = 8.0
HANG_UP_WAIT_S = 6.0  # the voice's goodbye, at most this long, before Orbit hangs up when the user asked
SPEAKING_HOLD_S = 1.2  # "speaking" this long after the last fragment of the voice's speech
SNAP_AFTER_SAY_S = 4.0  # a state snapshot waits this long after the voice was asked to speak
MIRROR_BEAT_S = 5.0  # a call held for another machine's session tells it it runs at least this often (the mirror)
OP_WAIT_S = 3.0  # how long a voice op for a call held elsewhere waits for that machine to say it took it
# The session's state, told silently (§6): changes within COALESCE_S go out as one snapshot; one that only moves the
# agent's step on, at most every STEP_EVERY_S (each append is a chance for the voice to speak: real calls answered a
# 10 s progress feed with "still working, one moment" 9 times in 13).
COALESCE_S, STEP_EVERY_S, SNAPSHOT_TOKENS = 2.0, 30.0, 150
# A result is never spoken over the user: it waits while a fragment of theirs came within USER_HOLD_S (they arrive
# every ~200 ms while someone talks), at most HOLD_MAX_S.
USER_HOLD_S, HOLD_MAX_S = 1.2, 8.0
QUEUE_REAL_S = 30.0  # a request waits "for real" (worth saying) when the one before it has run this long
SPEAK_TOKENS = {"brief": 100, "detailed": 200}  # the spoken part of a result: ~75 / ~150 English words
DETAIL_TOKENS = 300  # the next part of the answer, as silent context for follow-up questions
RECONNECT_BASE_S, RECONNECT_MAX_S, STABLE_S = 0.2, 5.0, 30.0  # sideband reattach backoff (as Codex)
ATTACH_GIVE_UP_S = 30.0  # never attached in this long: the call ends
CLOSE_WAIT_S = 5.0  # hang-up: wait this long for session.closed
# The watch's call (§12): the server holds the GPT Live session over its WebSocket transport, PCM 16-bit mono 24 kHz
# both ways; the watch talks to this server over plain HTTP (watchOS keeps low-level networking from most apps).
RATE = 24000
WS_START_WAIT_S = 15.0  # session.start → session.started, else the call does not start
UP_MAX_BYTES = RATE * 2  # an input_audio.append carries at most 1 s (a longer upload is cut into pieces)
GAP_FILL_S = 0.6  # no audio from the watch this long: silence goes in for it (the docs: keep input audio running)
QUIET_PEAK = 4  # an output chunk whose loudest sample is below this is silence: not sent to the watch (Bluetooth)
SINK_MAX = 400  # frames waiting for a slow watch (~40 s of speech); past it the oldest go
APPEND_TOKENS = 450  # an append may carry 500 tokens; a little room for the estimate
FINAL = ("completed", "error", "cancelled", "paused")
ACTIVE = ("thinking", "running")
# What the page's data channel may carry: transcripts and session state for its captions and button; it may send
# nothing (only this server delegates, adds context or closes the call).
PAGE_EVENTS = ("session.started", "session.input_transcript.delta", "session.output_transcript.delta",
               "session.paused", "session.resumed", "session.closed")
# "Take it back": the user's words are only that (a verb of withdrawing or stopping, and words naming the request):
# by what they name, the running request is stopped ("停止当前在跑的任务", "stop the running task") or a waiting one
# withdrawn ("撤回"); else the call's last request is withdrawn (not started) or stopped (running). Anything more
# ("cancel the nightly job", "stop the server") is a task for the agent.
TAKE_BACK_VERBS = ("撤回", "撤掉", "撤销", "收回", "取消", "不要了", "别做了", "不用做了", "不用了", "算了", "停掉", "停下来", "停下", "停止",
                   "cancel", "withdraw", "nevermind", "forgetit", "scratchthat", "stop", "drop")
TAKE_BACK_FILL = ("request", "请求", "任务", "那个", "这个", "刚才", "刚刚", "上一个", "前面", "之前", "那条", "这条", "的", "把", "将", "你",
                  "请", "麻烦", "先", "就", "那", "吧", "了", "掉", "一下", "它", "啊", "嗯", "呃", "好", "ok", "please", "that",
                  "the", "this", "it", "last", "one", "my", "just", "okay", "yes",
                  "运行中", "进行中", "当前", "现在", "正在", "在跑", "跑的", "运行", "在", "current", "running", "now", "task", "job")
RUNNING_WORDS = ("在跑", "正在", "当前", "运行", "进行中", "running", "current")  # words naming the running request
STOP_VERBS = ("停下来", "停下", "停止", "停掉", "stop")
WITHDRAW_VERBS = ("撤回", "收回", "撤销", "撤掉", "withdraw")
# "Put it in the running one": a waiting request moved into the running one (withdrawn, its words steered in).
MOVE_IN_VERBS = ("插进去", "放进去", "加进去", "放到当前任务里", "放到当前任务", "加到当前任务", "插到当前任务", "steeritin", "steerin",
                 "putitin", "addittotherunningone", "addittothecurrentone", "addittotherunningrequest", "addittotherunningtask")
# "Tell it, while it works: …": the words after a cue join the running request (any of the session's, if its agent
# takes additions mid-request).
STEER_CUE = re.compile(r"^\s*(?:(?:请|麻烦)?你?(?:插一句|插个话|补充一下|补充|告诉它|跟它说|顺便(?:跟它说|告诉它)?)"
                       r"|(?:please\s+)?(?:also\s+tell\s+it(?:\s+to)?|tell\s+it(?:\s+to)?|steer(?:\s+it)?"
                       r"|add\s+(?:this\s+)?to\s+the\s+(?:current|running)(?:\s+(?:one|request|task))?)\b)[\s,，:：、;；.。!！-]*", re.I)


# "Do this first": the running request stops (what waits behind it stays) and the words after (or before) the cue
# run at once, ahead of the queue.
URGENT_CUE = (r"(?:打断(?:一下|它|当前的?(?:任务|request)?)?|先(?:停下来?|停一下)?(?:做|处理|弄|干)这个|插队|优先(?:处理|做)?(?:这个)?"
              r"|马上(?:做|处理)(?:这个)?|立刻(?:做|处理)(?:这个)?|紧急|urgent(?:ly)?|do\s+this\s+first|interrupt(?:\s+it)?(?:\s+and)?"
              r"|stop\s+(?:it|that|what\s+you(?:'re|\s+are)\s+doing)\s+and)")
URGENT_HEAD = re.compile(r"^\s*(?:(?:请|麻烦)?你?)" + URGENT_CUE + r"[\s,，:：、;；.。!！-]+", re.I)  # (a pause after it: "优先级…" is no cue)
URGENT_TAIL = re.compile(r"[\s,，:：、;；.。!！-]*(?:请|麻烦)?你?" + URGENT_CUE + r"(?:吧|啊)?[\s,，.。!！]*$", re.I)


def urgent_words(words: str) -> str:
    """The words to run first when a cue says so ("打断一下，先把测试跑了" / "…，先做这个" / "do this first: …"), else ""."""
    w = words or ""
    m = URGENT_HEAD.match(w)
    rest = w[m.end():] if m and m.group(0).strip() else ""
    if not rest:
        t = URGENT_TAIL.search(w)
        rest = w[:t.start()] if t and t.group(0).strip() else ""
    rest = rest.strip()
    return rest if len(_norm(rest)) >= 2 else ""  # (a cue with nothing to do is no request)


def _norm(words: str) -> str:
    return re.sub(r"[\s\W_]+", "", (words or "").lower())


def _only(w: str, verbs: tuple, fill: tuple) -> bool:
    """w (normalized) is one of verbs and nothing but fill words besides."""
    if not w or len(w) > 24 or not any(v in w for v in verbs):
        return False
    for t in sorted(verbs + fill, key=len, reverse=True):
        w = w.replace(t, "")
    return not w


# "Hang up": the user's words are only that (a verb of ending the call, and words naming it): the call ends, after a
# short goodbye. GPT Live cannot end a call itself; told to, it delegates the words and Orbit does it. Anything more
# ("close the file", "关掉这个服务") is a task.
HANG_UP_VERBS = ("挂断", "挂掉", "挂了", "挂电话", "结束通话", "结束语音", "结束对话", "退出语音", "关闭实时语音", "关掉实时语音",
                 "关闭语音", "关掉语音", "停止语音", "关闭通话", "关掉通话", "关闭", "关掉", "关了", "再见", "拜拜",
                 "hangup", "endthecall", "endcall", "endthevoice", "stopthecall", "stopvoice", "turnoffvoice",
                 "turnoffthevoice", "closethecall", "goodbye", "bye")
HANG_UP_FILL = ("你", "请", "帮我", "麻烦", "把", "这个", "那个", "实时", "语音", "通话", "电话", "模式", "吧", "了", "一下",
                "啊", "嗯", "呃", "好", "好的", "ok", "okay", "现在", "先", "就", "可以", "掉", "the", "call", "voice", "live",
                "realtime", "mode", "now", "please", "you", "can", "this", "it", "for", "today", "那", "我们", "今天", "到这",
                "到这里", "就到这", "就这样", "thanks", "thank", "谢谢")


def hangs_up(words: str) -> bool:
    return _only(_norm(words), HANG_UP_VERBS, HANG_UP_FILL)


def takes_back(words: str) -> bool:
    return _only(_norm(words), TAKE_BACK_VERBS, TAKE_BACK_FILL)


def moves_in(words: str) -> bool:
    return _only(_norm(words), MOVE_IN_VERBS, TAKE_BACK_FILL + ("里", "中", "into", "to", "add", "put"))


def steer_words(words: str) -> str:
    """The words to add to the running request when they start with a steer cue ("插一句 …", "tell it to …"), else ""."""
    m = STEER_CUE.match(words or "")
    return (words[m.end():].strip() if m and m.group(0).strip() else "")


REFERS = re.compile(r"\b(that|this|it|those|these|them|the (first|second|third|last|other|same) one|the same)\b", re.I)
CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿]")


class Busy(ValueError):
    """A call is on already (one at a time, on every page and device): hang it up first."""

    def __init__(self, call: "Call"):
        self.call = call
        super().__init__("a voice call is on already: hang it up first (⌘⇧V)")


class NoKey(ValueError):
    """No OpenAI API key is set (Settings → Voice)."""


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


FENCED = re.compile(r"(?ms)^\s*(```|~~~).*?^\s*\1[^\n]*$")
TABLE_ROW = re.compile(r"\s*\|")
TABLE_RULE = re.compile(r"\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")
BULLET = re.compile(r"\s*([-*+•]|\d{1,3}[.)])\s+")


def unmark(line: str) -> str:
    """A line of Markdown as plain words to say: no heading, bullet or quote marks, emphasis, links (their text kept),
    images or code backticks. (A voice reads "**" and "- " aloud, or stumbles on them.)"""
    t = re.sub(r"^\s*(#{1,6}\s+|>\s?)", "", line)
    t = BULLET.sub("", t, count=1) if BULLET.match(t) else t
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"`([^`\n]+)`", r"\1", t)
    t = re.sub(r"(\*\*|__|~~)(.+?)\1", r"\2", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"\1", t)
    return " ".join(t.split())


def plain(answer: str) -> tuple[list[str], bool]:
    """An answer as plain paragraphs to say: fenced code, tables, images, headings and rules left out, Markdown marks
    removed, a list's items joined into one sentence-like paragraph. (paragraphs, whether code or tables were left out)."""
    text, left = FENCED.subn("", answer or "")
    paras: list[str] = []
    cur: list[str] = []
    for ln in text.splitlines() + [""]:
        if TABLE_ROW.match(ln) or TABLE_RULE.match(ln):
            left += 1
            continue
        if not ln.strip() or re.match(r"\s*#{1,6}\s", ln) or re.match(r"\s*([-*_]\s*){3,}$", ln):
            if cur:  # a blank line, a heading or a rule ends the paragraph (a heading is display structure)
                p = "".join(x if not k or (CJK.match(cur[k - 1][-1]) or cur[k - 1][-1] in "；。") and CJK.match(x[0])
                            else " " + x for k, x in enumerate(cur))  # (no space between Chinese lines)
                paras.append(p if p[-1] in ".!?…。！？" else p + ("。" if CJK.match(p[-1]) else "."))  # (said in one run)
                cur = []
            continue
        t = unmark(ln)
        if not t:
            continue
        if BULLET.match(ln) and cur and cur[-1][-1] not in ".!?;:…。！？；：":
            cur[-1] += "；" if CJK.match(cur[-1][-1]) else ";"  # list items read as one sentence
        cur.append(t)
    return [p for p in paras if p], bool(left)


def gist(text: str, limit: int = 14) -> str:
    """A short name for a request (or an answer) to say: its first line, the [orbit] line and Markdown left out,
    within about limit tokens."""
    from orbit.drivers.base import strip_orbit_line
    line = next((t for t in (unmark(x) for x in strip_orbit_line(text or "").splitlines()) if t), "")
    out, cut = cut_tokens(line, limit)
    return out + "…" if cut else out


def dur(s: float) -> str:
    s = int(max(0, s))
    return f"{s} s" if s < 60 else f"{s // 60} min {s % 60} s" if s < 3600 else f"{s // 3600} h {s // 60 % 60} min"


def http_base() -> str:
    return keys.OPENAI_BASE.rstrip("/")


def ws_base() -> str:
    b = http_base()
    return "wss://" + b[len("https://"):] if b.startswith("https://") else "ws://" + b[len("http://"):] if b.startswith("http://") else b


# ---------------------------------------------------------------------------------------------- the bound session
class Target:
    """The session a call works with: one of this server's (the desk), or another machine's (`name~sid`, through
    the federation: its requests are sent to that server, its cards come back on this server's event stream)."""

    def __init__(self, desk, sid: str):
        from orbit.machines import federation
        self.desk, self.sid = desk, sid
        self.remote = federation.split(sid) if getattr(desk, "federation", None) else None
        if self.remote is None:
            desk.get(sid)  # (KeyError: no such session)
        elif not self._shown():
            raise KeyError(sid)

    def _shown(self) -> dict:
        fed = self.desk.federation
        with fed.lock:
            return dict(fed.sessions.get(self.remote[0], {}).get(self.remote[1]) or {})  # (kept by that machine's id)

    def info(self) -> dict:
        """name, cwd (as that machine names it), harness, mode, state, steers."""
        if self.remote is None:
            s = self.desk.get(self.sid)
            with self.desk.lock:
                pub = s.public()
        else:
            from orbit.machines import federation
            pub = self._shown()
            pub["cwd"] = federation.local_paths(self.remote[0], pub.get("cwd") or "")
        return {k: pub.get(k) for k in ("name", "cwd", "harness", "mode", "state", "steers")}

    def cards(self) -> list[dict]:
        if self.remote is None:
            s = self.desk.get(self.sid)
            self.desk._ensure_loaded(s)
            with self.desk.lock:
                return copy.deepcopy(s.cards)
        return self._call("GET", "/cards")["cards"]

    def voice(self) -> dict:
        """Its agent's voice settings (instructions, reminder, ack: agentvoice.effective), from its own server."""
        from orbit.services.live import agentvoice
        try:
            if self.remote is None:
                return agentvoice.effective(self.desk.get(self.sid))
            return self._call("POST", "/voice", {"op": "get"}) or {}
        except (KeyError, RuntimeError, OSError):
            return {}

    def _call(self, method: str, action: str, body: dict | None = None):
        name, rsid = self.remote
        code, d = self.desk.federation.call(name, method, f"/api/sessions/{rsid}{action}", body)
        if code != 200:
            raise RuntimeError(f"{name}: {(d or {}).get('error') or code}")
        return d

    def send(self, text: str, origin: dict, client_id: str, first: bool = False) -> dict:
        if self.remote is None:
            return self.desk.send(self.sid, text, origin=origin, client_id=client_id, first=first)
        return self._call("POST", "/send", {"text": text, "origin": origin, "client_id": client_id, "first": first})

    def steer(self, text: str, origin: dict, client_id: str) -> dict:
        if self.remote is None:
            return self.desk.steer(self.sid, text, origin=origin, client_id=client_id)
        return self._call("POST", "/steer", {"text": text, "origin": origin, "client_id": client_id})

    def edit(self, cid: int, text: str) -> dict:
        if self.remote is None:
            return self.desk.edit(self.sid, cid, text)
        return self._call("POST", "/edit", {"cid": cid, "text": text})

    def withdraw(self, cid) -> None:
        if self.remote is None:
            return self.desk.withdraw(self.sid, cid)
        self._call("POST", "/withdraw", {"cid": cid})

    def stop(self) -> None:
        """The running request only: what waits behind it goes on (the voice said which one it stopped)."""
        if self.remote is None:
            return self.desk.stop(self.sid, keep_queue=True)
        self._call("POST", "/stop", {"keep_queue": True})

    def note(self, title: str, text: str, origin: dict) -> dict:
        if self.remote is None:
            return self.desk.add_note(self.sid, title, text, origin=origin)
        return self._call("POST", "/note", {"title": title, "text": text, "origin": origin})


# ---------------------------------------------------------------------------------------------- the session's setup
def instructions(name: str, cwd: str, model: str, cfg: dict | None = None, session_text: str = "") -> str:
    """§3, in the structure of OpenAI's live prompting template (its Backchannel / Interruption / Delegation policy
    headings), with Orbit's noise rule; then Settings → Voice (its language, how much of a result to say, the user's own
    instructions). For gpt-live-1 the guide's handoff words are its token."""
    cfg = cfg or config.current(None)
    # (auto: a real call drifted into Traditional characters mid-answer)
    lang = f" Always speak {config.LANGUAGES[cfg['live_language']]}, whatever language the user or a result uses." \
        if cfg["live_language"] != "auto" else \
        " Speak the language the user speaks; for Chinese, speak Mandarin and use Simplified characters."
    detail = "give the takeaway in one to three short sentences" if cfg["live_detail"] == "brief" else \
        "give the result in a few sentences with its key details (names, numbers, what changed, what is left)"
    text = f"""You are the voice in Orbit, an IDE where the user works with coding agents. An agent session named \
"{name}" (folder {cwd}) is your backend, and its work is yours: speak of whatever it does, reading, checking or \
changing things, in the first person ("I'll look at the logs", "I'm still on request #3", "I've fixed the test"), not as \
"the agent" or by the session's name (unless the user asks which session). Session state and Orbit's facts describe \
your backend's work: say it the same way. Name another agent only when a result or Orbit's facts say the work went to \
one. Orbit is the platform that routes requests and reports their status: mention it only when that matters.
Speak naturally and briefly. Be clear and direct.{lang}

Backchannel policy: Listen mostly in silence. An occasional quiet "mm-hm" at a natural pause is fine.

Interruption policy: Stop speaking when the user interrupts, and listen. Keep listening through coughs, throat-clears, \
sighs, a brief "mm-hm", music, other people talking, and thinking pauses: none of them is a request. Respond to a clear \
request, a question, or being addressed. After you answer, wait for the user; do not follow up on your own unless a \
result, a blocker or a missing detail arrives.

Delegation policy:
Backend tools:
- The agent session "{name}": it reads and edits the files in {cwd}, runs commands and tools, and knows the session's \
history.
Delegate to the backend when:
- The user asks for any action or task.
- The answer needs the session's files, tools or history.
- A correction changes the work already requested.
- The user wants to stop, add to, withdraw or cancel a request.
- The user wants to end the call ("挂断", "关闭实时语音", "关掉", "hang up", "end the call"): you cannot end it \
yourself; delegate their words and Orbit hangs up after your goodbye.
- Session instructions (given below or later in this call) say to.
What Orbit can do with requests (answer questions about it yourself): they run one at a time in this session; to do \
a new request first (interrupting the running one, ahead of what waits), or to stop \
the running request, add something to it while it runs, or take back one that has not started, delegate the user's \
words: Orbit does it and tells you which.
Do not delegate when:
- The question is self-contained, or about what was already said or reported in this call.
- The user asks about status, progress, what is running or queued, or how long something has taken: answer at once \
from the latest Session state. Give elapsed times; never invent an estimate. If the state does not answer it, say what \
you know and offer to check.
- You need a brief clarification to understand the request.
Use a previous result when it still answers the question (more of an answer may have come with it as silent context); \
delegate again if it is missing, out of date, or the user asks to check again.
Delegate to the backend before giving an answer that depends on backend work. Do not guess the result while waiting.
Never describe how Orbit or your backend handles requests beyond what Orbit tells you in this call.
Results and Orbit's facts are authoritative: do not add to them or contradict them. Say the key takeaway, status or \
next step in your own words. Never invent facts, names, numbers or details that are not in a result, in Orbit's facts \
or in this call; if the result does not answer what the user asked, say so plainly and offer to check again.

Orbit sends you background facts (session state, progress) silently. Do not announce them, and do not say you are \
still working, unless the user asks or a meaningful change happened (a step that matters finished, a delay that \
matters, or the user must answer something).
Avoid unnecessary narration: no repeated confirmations, filler, re-acknowledgements or play-by-play. When a result \
arrives, {detail}; code, tables and logs stay in Orbit."""
    if session_text.strip():  # the session's agent's (agentvoice.py): under Orbit's rules, over the user's style
        from orbit.services.live.agentvoice import INSTRUCT_HEAD
        text += "\n\n" + INSTRUCT_HEAD + "\n" + session_text.strip()
    if cfg["live_prompt"].strip():
        text += ("\n\nThe user's own instructions for this voice (they win over the style rules above, never over the "
                 "delegation and grounding rules):\n" + cfg["live_prompt"].strip())
    if model == "gpt-live-1":
        text = text.replace("Delegate to the backend", "Generate <|SpawnThinking|>").replace(
            "Do not delegate", "Do not generate <|SpawnThinking|>")
    return text


def _msg(role: str, text: str) -> dict:
    return {"type": "message", "role": role,
            "content": [{"type": "output_text" if role == "assistant" else "input_text", "text": text}]}


def state_snapshot(name: str, cards: list[dict], now: float | None = None) -> tuple[str, tuple, tuple]:
    """§6: the bound session's state in a few facts (< ~150 tokens): what runs, for how long, the agent's latest words
    (its last interim text, never a tool command), what is queued, the last finished request with a gist of its
    answer. (text, the key of its big changes: started, finished, queued; the key of a step change.) The voice answers
    "is it done / what is it doing / how long" from this instead of queuing the question behind the work."""
    now = now or time.time()
    cs = [c for c in cards if c.get("kind") != "note" and not c.get("hidden")]
    # (a turn the agent took by itself, after a background task, is not a request: no number to name it by)
    what = lambda c, n=14: (f'a turn of its own ("{gist(((c.get("auto") or {}).get("what") or c.get("text") or ""), n)}")'
                            if c.get("kind") == "auto" else f'request #{c["id"]} ("{gist(c.get("text") or "", n)}")')
    run = next((c for c in cs if c.get("state") in ACTIVE), None)
    waiting = [c for c in cs if c.get("state") == "queued"]
    done = [c for c in cs if c.get("state") in FINAL]
    last = done[-1] if done else None
    out = f'Session state at {datetime.fromtimestamp(now).strftime("%H:%M:%S")}: "{name}"'
    step: tuple = ()
    if run:
        out += f' is working on {what(run)} for ' \
               f'{dur(now - (run.get("started") or run.get("time") or now))}'
        steps = run.get("steps") or []
        tools = sum(1 for x in steps if x.get("kind") == "tool")
        if tools:
            out += f", {tools} step{'s' if tools > 1 else ''} so far"
        i = max((k for k, x in enumerate(steps) if x.get("kind") == "note"), default=-1)
        words = gist(" ".join((steps[i].get("text") or steps[i].get("summary") or "").split()), 30).rstrip(".。") \
            if i >= 0 else ""
        if words:  # (a note is recorded as its tool starts: that tool's start is when it was said)
            said = steps[i + 1].get("t0") if i + 1 < len(steps) else None
            out += f'; its latest words{f" ({dur(now - said)} ago)" if said else ""}: "{words}"'
        step = (run["id"], tools, words)
    else:
        out += " has nothing running" if waiting else " is idle: nothing running or queued"
    if waiting:
        out += f'. Queued{" after it" if run else ""}: ' + ", ".join(
            f'#{c["id"]} ("{gist(c.get("text") or "", 10)}")' for c in waiting[:2]) + \
            (f" and {len(waiting) - 2} more" if len(waiting) > 2 else "")
    if last:
        st = last["state"]
        retrying = st == "paused" and "retrying in" in (last.get("error") or "")
        out += f'. Last finished: {what(last, 10)}'
        if last.get("finished"):
            out += " at " + datetime.fromtimestamp(last["finished"]).strftime("%H:%M")
        if st == "completed":
            paras, _ = plain(last.get("answer") or "")
            out += f', done: "{cut_tokens(paras[0], 30)[0].rstrip(".。")}"' if paras else ", done"
        else:
            err = " ".join((last.get("error") or "").split())
            out += {"cancelled": ", stopped", "paused": ", paused (it retries by itself)" if retrying else ", paused"}.get(
                st, ", failed") + (f": {cut_tokens(err, 20)[0]}" if err and st != "cancelled" and not retrying else "")
    out, _ = cut_tokens(out + ".", SNAPSHOT_TOKENS)
    big = (run["id"] if run else None, tuple(c["id"] for c in waiting),
           (last["id"], last["state"]) if last else None)
    return out, big, step


def startup_input(desk, s, history: bool = True) -> list[dict]:
    """§4: the session and its state (the same snapshot the call sends on changes), its last 3 requests and answers
    (each answer cut to 600 characters), what still runs or waits; observed_at on each. About 2 000 tokens at most.
    (s: a Target, or one of the desk's sessions.)"""
    from orbit.desk.session import DRIVERS, clip
    from orbit.drivers.base import strip_orbit_line
    t = s if isinstance(s, Target) else Target(desk, s.id)
    pub = t.info()
    cards = [c for c in t.cards() if c.get("kind") != "note" and not c.get("hidden")]
    at = now_iso()
    label = getattr(DRIVERS.get(pub["harness"]), "label", pub["harness"])
    running = [c for c in cards if c["state"] in ACTIVE]
    waiting = [c for c in cards if c["state"] == "queued"]
    line = lambda c: clip(strip_orbit_line(c.get("text") or ""), 160)
    facts = (f'The Orbit session "{pub["name"]}": harness {label}, folder {pub["cwd"]}, permission mode {pub["mode"]}. '
             + state_snapshot(pub["name"], cards)[0] + f" (observed_at {at})")
    items = [_msg("developer", facts)]
    done = [c for c in cards if c["state"] in FINAL][-3:] if history else []
    if done:
        items.append(_msg("developer", f"Its last {len(done)} requests and your backend's answers follow: user is the "
                                       f"request as sent to it, assistant its answer, cut. (observed_at {at})"))
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


def session_body(desk, s, sdp: str, model: str, cfg: dict | None = None) -> dict:
    """The POST /v1/live/sessions body (§2): fixed for the call's life (Settings → Voice as the call starts)."""
    cfg = cfg or config.current(desk.settings)
    t = s if isinstance(s, Target) else Target(desk, s.id)
    pub = t.info()
    v = t.voice()
    if v.get("ack") is not None:  # (this session's agent chose; cfg is the call's own: it stays so)
        cfg["live_ack"] = bool(v["ack"])
    cfg["_voice_remind"] = v.get("remind") or ""
    return {
        "session": {
            "model": model,
            "instructions": instructions(pub["name"], pub["cwd"], model, cfg, v.get("instructions") or ""),
            "input": startup_input(desk, t, history=cfg["live_context"]),
            "audio": {"output": {"voice": cfg["live_voice"]}},
            "delegation": {"type": "client", "acknowledgement_delay_ms": ACK_DELAY_MS},
            "wake_policy": {"type": "speech", "vad_threshold": cfg["live_wake"]},
            **({"idle_timeout_s": cfg["live_idle_s"]} if cfg["live_idle_s"] else {}),  # (left out: active until hang-up)
            "store": False,
            "client": {"data_channel": {"allowed_client_events": [],
                                        "allowed_server_events": [{"type": t} for t in PAGE_EVENTS]}},
        },
        "transport": {"type": "webrtc", "sdp": sdp},
    }


def ws_session(desk, s, model: str, cfg: dict | None = None) -> dict:
    """The session.start of a call over the WebSocket transport (the watch, §12): the session of session_body, with its
    audio format named (PCM 24 kHz both ways), without what that transport refuses (wake_policy and idle_timeout_s:
    "requires a session with wake policy support", so no standby) or does not have (the page's data channel)."""
    se = session_body(desk, s, "", model, cfg)["session"]
    for k in ("wake_policy", "idle_timeout_s", "client"):
        se.pop(k, None)
    se["audio"] = {"format": {"type": "audio/pcm", "rate": RATE}, "output": dict(se["audio"]["output"])}
    return {"type": "session.start", "session": se}


def frame(kind: bytes, payload: bytes) -> bytes:
    """One message of the watch's down stream: a type byte (b"a" audio, b"j" JSON), its length (4 bytes, big-endian)."""
    return kind + struct.pack(">I", len(payload)) + payload


def quiet(pcm: bytes) -> bool:
    """PCM 16-bit that is silence (the output streams all the time, silence too)."""
    a = array.array("h", pcm[: len(pcm) // 2 * 2])
    return not a or max(max(a), -min(a)) < QUIET_PEAK


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


def turn_end(frags: list[dict], offset_ms: int, used: set, gap_ms: int = 1500) -> int:
    """Where the user's turn ends after a delegation at offset_ms: the end of their run of fragments (not taken yet)
    that goes on from before it with no gap over gap_ms; offset_ms when they stopped before it."""
    end = offset_ms
    for f in ordered(frags):
        if f["who"] != "user" or f["key"] in used or f["end"] <= end:
            continue
        if f["start"] - end > gap_ms:
            break
        end = f["end"]
    return end


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


def transcript_turns(frags: list[dict], ended: bool, quiet: bool, since: int = 0) -> list[dict]:
    """turns() for `orbit-agent voice transcript`: [{who, start, text, done}] from since (ms); the last turn is done
    once the call ended or nobody has spoken for a moment (quiet), the others always."""
    ts = turns(frags)
    return [{"who": w, "start": st, "text": x, "done": i < len(ts) - 1 or ended or quiet}
            for i, (w, st, x) in enumerate(ts) if st >= since]


def takeaway(answer: str, limit: int = SPEAK_TOKENS["brief"], more: int = DETAIL_TOKENS) -> tuple[str, str, bool]:
    """An answer for speaking (§6): plain paragraphs (plain()); whole ones while they fit in limit tokens (the first
    cut at a sentence when it alone is longer); the next ~more tokens as silent context for follow-up questions.
    (what to say, the detail, whether anything stays only in Orbit)."""
    paras, left = plain(answer)
    say, k = "", 0
    for k, p in enumerate(paras):
        if est_tokens((say + " " + p) if say else p) > limit:
            break
        say, k = (say + " " + p) if say else p, k + 1
    rest = paras[k:]
    if not say and rest:
        say, _ = cut_tokens(rest[0], limit)
        rest = [rest[0][len(say):].strip()] + rest[1:]
    detail, cut = cut_tokens("\n\n".join(x for x in rest if x), more)
    return say, detail, left or cut


def result_text(c: dict, length: str = "brief") -> str:
    """The one commentary for a finished card: facts to say, not a report to read out. The request named by a gist
    (not only its number), the answer's opening in plain words within SPEAK_TOKENS[length], and a question it ends
    with (the next step it asks of the user). No quote framing: GPT-Live is given the facts and says them its way;
    the grounding is the instructions' "authoritative, add nothing" rule."""
    n, what = c["id"], gist(c.get("text") or "")
    name = f'Request #{n} ("{what}")' if what else f"Request #{n}"
    if c["state"] == "completed":
        say, _, more = takeaway(c.get("answer") or "", SPEAK_TOKENS.get(length, SPEAK_TOKENS["brief"]))
        if not say:
            return f"{name} is done; its answer is only code or tables, which stay in Orbit."
        paras, _ = plain(c.get("answer") or "")
        ask = paras[-1] if paras and paras[-1].rstrip()[-1:] in "?？" and paras[-1] not in say else ""
        return (f"{name} is done: {say}" + (f" It ends by asking: {cut_tokens(ask, 40)[0]}" if ask else "")
                + (" The full answer is in Orbit." if more else ""))
    err = " ".join((c.get("error") or "").split())[:300]
    if c["state"] == "cancelled":
        return f"{name} was stopped."
    if c["state"] == "paused":
        return f"{name} paused" + (f": {err}" if err else "") + "."
    return f"{name} failed" + (f": {err}" if err else "") + "."


def result_detail(c: dict, length: str = "brief") -> str:
    """More of a finished card's answer than result_text says (~DETAIL_TOKENS), to go silently with it: "what were
    the other commits?" is answered from it, without a new request. "" when there is no more."""
    if c["state"] != "completed":
        return ""
    _, detail, _ = takeaway(c.get("answer") or "", SPEAK_TOKENS.get(length, SPEAK_TOKENS["brief"]))
    return (f"More of request #{c['id']}'s answer, context for follow-up questions (do not read it out): {detail}"
            if detail else "")


# ---------------------------------------------------------------------------------------------- the call
class Call:
    def __init__(self, live: "Live", sid: str, model: str, transport: str = "webrtc"):
        self.live, self.desk, self.sid, self.model = live, live.desk, sid, model
        self.transport = transport  # "webrtc" (a page: media to OpenAI, this server on the sideband) | "watch" (§12)
        self.target = Target(live.desk, sid)  # (this server's session, or another machine's)
        self.cfg = config.current(live.desk.settings)  # (again as the call is created)
        self.id = "call-" + uuid.uuid4().hex[:10]
        self.t0 = time.time()
        self.lock = threading.RLock()
        self.say_lock = threading.Lock()  # a result's appends go together; a request's status never after its result
        self.said: set = set()  # the cards whose result was sent
        self.phase = "connecting"
        self.live_id = ""
        self.key = self.key_label = ""  # (the key that made the session: its sideband attaches with it)
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
        self.last_user = 0.0  # when a fragment of the user's speech last came (results wait while they talk)
        self.held: list[dict] = []  # appends that make the voice speak, waiting for the user to finish
        self.cache: dict = {}  # the bound session's cards as the event stream last showed them (card id → card)
        self.quiet: set = set()  # cards whose agent spoke through `voice say`: their result is not spoken again
        self.said_at = 0.0  # when the voice was last asked to speak (a result, `voice say`): snapshots wait a moment
        self.mirrored = (0, -1, False)  # (turns done, chars, ended) last mirrored to the session's machine
        self.mirroring = False
        self.name = ""
        self.seeded = False
        self.snap_due = 0.0  # when the next session-state snapshot is due (0: no change since the last)
        self.snap_big: tuple | None = None  # the last snapshot told (its keys), and when
        self.snap_step: tuple | None = None
        self.snap_at = 0.0
        self.snap_n = 0
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
        # The watch's call: the down streams listening (frames), the audio up, session.started waited for at the start
        self.sinks: list[queue.Queue] = []
        self.started_ev = threading.Event()
        self.start_error = ""
        self.audio_at = 0.0  # when the watch's audio last came
        self.up_bytes = self.down_bytes = self.quiet_chunks = 0

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
            out = {"call": self.id, "phase": self.phase, "sid": self.sid, "live": self.live_id, "started": self.t0, "usage": dict(self.usage),
                   "transport": self.transport,
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
        ev = {"t": "live", "kind": kind, "call": self.id, **kw}
        self.desk.broadcast(ev)
        if self.sinks:  # (the watch: the same events on its down stream)
            self._to_sinks(frame(b"j", json.dumps(ev, ensure_ascii=False).encode()))

    def _set_phase(self, p: str) -> None:
        with self.lock:
            if self.phase == p or (self.phase == "ended" and p != "ended"):
                return
            self.phase = p
        self.log("phase", phase=p)
        self._tell("state", state=self.state(transcript=False))

    # ------------------------------------------------------------------ create
    def create(self, sdp: str) -> dict:
        ks = keys.entries(self.desk.state_dir, "openai")
        if not ks:
            raise NoKey("No OpenAI API key: set one in Settings → Voice")
        s = self.target
        self.cfg = config.current(self.desk.settings)
        body = session_body(self.desk, s, sdp, self.model, self.cfg)
        self.log("create", sid=self.sid, model=self.model, input=len(body["session"]["input"]),
                 instructions_tokens=est_tokens(body["session"]["instructions"]), offer_bytes=len(sdp))
        data = json.dumps(body).encode()
        for i, k in enumerate(ks):  # the keys in order: one refused (bad, no quota, rate-limited), the next
            req = urllib.request.Request(f"{http_base()}/v1/live/sessions", data=data, method="POST",
                                         headers={"Authorization": f"Bearer {k['key']}", "Content-Type": "application/json"})
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
                self.log("create_failed", status=e.code, error=msg[:500], key=keys.label(k),
                         ms_create=int((time.time() - t) * 1000))
                if e.code in keys.FAILOVER:
                    keys.note(self.desk.state_dir, k["id"], False, f"OpenAI answered {e.code}" + (f": {msg}" if msg else ""))
                    if i + 1 < len(ks):
                        continue
                    many = f" (all {len(ks)} keys)" if len(ks) > 1 else ""
                    self._fail(f"OpenAI answered {e.code}{many}" + (f": {msg}" if msg else ""))
                self._fail(f"OpenAI answered {e.code}" + (f": {msg}" if msg else ""))
            except (OSError, ValueError) as e:
                self.log("create_failed", error=str(e)[:300])
                self._fail(f"could not reach OpenAI: {e}")
            keys.note(self.desk.state_dir, k["id"], True)
            self.key, self.key_label = k["key"], keys.label(k)  # (the sideband attaches with the key that made it)
            break
        self.live_id = str(((d.get("session") or {}).get("id")) or "")
        answer = str(((d.get("transport") or {}).get("sdp")) or "")
        if not self.live_id or not answer:
            self.log("create_failed", status=status, error="no session id or SDP answer")
            self._fail("OpenAI's answer had no session id or SDP answer")
        self.log("created", status=status, ms_create=int((time.time() - t) * 1000), answer_bytes=len(answer),
                 key=self.key_label)
        self.q = self.desk.subscribe()
        for fn in (self._sideband, self._follow_cards, self._tick):
            threading.Thread(target=fn, daemon=True, name=f"live-{fn.__name__}").start()
        return {"call": self.id, "sdp": answer, "live": self.live_id, "chime": self.cfg["live_chime"] and self.cfg["live_chime_sound"]}

    def create_ws(self) -> dict:
        """The watch's call (§12): this server opens the GPT Live session over the WebSocket transport (the key in
        order, as create), sends session.start and waits for session.started; then the same threads as a page's call,
        with this socket in the sideband's place (it carries the audio too)."""
        ks = keys.entries(self.desk.state_dir, "openai")
        if not ks:
            raise NoKey("No OpenAI API key: set one in Settings → Voice")
        self.cfg = config.current(self.desk.settings)
        start = ws_session(self.desk, self.target, self.model, self.cfg)
        self.log("create", sid=self.sid, model=self.model, transport="websocket", input=len(start["session"]["input"]),
                 instructions_tokens=est_tokens(start["session"]["instructions"]))
        ws = None
        for i, k in enumerate(ks):
            t = time.time()
            try:
                ws = wsock.connect(f"{ws_base()}/v1/live/sessions", headers={"Authorization": f"Bearer {k['key']}"}, timeout=15)
            except wsock.HandshakeError as e:
                try:
                    msg = str((json.loads(e.body or "{}").get("error") or {}).get("message") or "")
                except (ValueError, AttributeError):
                    msg = ""
                self.log("create_failed", status=e.status, error=msg[:500], key=keys.label(k), ms_create=int((time.time() - t) * 1000))
                if e.status in keys.FAILOVER:
                    keys.note(self.desk.state_dir, k["id"], False, f"OpenAI answered {e.status}" + (f": {msg}" if msg else ""))
                    if i + 1 < len(ks):
                        continue
                    many = f" (all {len(ks)} keys)" if len(ks) > 1 else ""
                    self._fail(f"OpenAI answered {e.status}{many}" + (f": {msg}" if msg else ""))
                self._fail(f"OpenAI answered {e.status}" + (f": {msg}" if msg else ""))
            except OSError as e:
                self.log("create_failed", error=str(e)[:300])
                self._fail(f"could not reach OpenAI: {e}")
            keys.note(self.desk.state_dir, k["id"], True)
            self.key, self.key_label = k["key"], keys.label(k)
            break
        try:
            ws.send(json.dumps(start))
        except OSError as e:
            ws.abort()
            self._fail(f"could not reach OpenAI: {e}")
        with self.lock:
            self.ws, self.attached = ws, 1
        threading.Thread(target=self._ws_reader, args=(ws,), daemon=True, name="live-_ws_reader").start()
        if not self.started_ev.wait(WS_START_WAIT_S) or self.start_error or not self.live_id:
            why = self.start_error or ("the voice session did not start" if self.live_id or not self.ended else "the connection closed")
            self.log("create_failed", error=why[:500])
            with self.lock:
                self.closing = True  # (the reader, seeing the socket go, finishes nothing: this call never started)
            ws.abort()
            self._fail(f"OpenAI: {why}")
        self.log("created", transport="websocket", ms_create=int((time.time() - t) * 1000), key=self.key_label)
        self.audio_at = time.time()
        self.q = self.desk.subscribe()
        for fn in (self._follow_cards, self._tick):
            threading.Thread(target=fn, daemon=True, name=f"live-{fn.__name__}").start()
        return {"call": self.id, "live": self.live_id, "rate": RATE, "format": "pcm16",
                "chime": self.cfg["live_chime"] and self.cfg["live_chime_sound"]}

    def _ws_reader(self, ws) -> None:
        """The WebSocket transport's events: the voice's audio to the watch, everything else as the sideband's. The
        session lives as long as this socket: when it goes, the call ends (no reattach on this transport)."""
        try:
            while True:
                m = ws.recv()
                if isinstance(m, bytes):
                    continue
                try:
                    ev = json.loads(m)
                except ValueError:
                    continue
                if not isinstance(ev, dict):
                    continue
                typ = ev.get("type")
                if typ == "session.output_audio.delta":
                    self._audio_out(ev.get("delta") or "")
                    continue
                if typ == "error" and not self.started_ev.is_set():
                    err = ev.get("error") or {}
                    self.start_error = str(err.get("message") or err.get("code") or "error")[:300] if isinstance(err, dict) else str(err)[:300]
                self._on_message(ev)
                if typ in ("session.started", "error"):
                    self.started_ev.set()
        except wsock.Closed as e:
            self.log("ws_closed", code=e.code, reason=e.reason[:200])
        except Exception as e:  # noqa: BLE001 - a bug in a handler must not end the call silently
            self.log("ws_error", error=f"{type(e).__name__}: {e}"[:300])
            ws.abort()
        with self.lock:
            if self.ws is ws:
                self.ws = None
        self.started_ev.set()
        self.closed_ev.set()  # (a hang-up waiting for session.closed: the socket going says as much)
        if not self.ended and not self.closing:
            self._finish(self.reason or "connection_lost")

    def audio_in(self, pcm: bytes) -> None:
        """The watch's microphone: PCM 16-bit mono 24 kHz, as input audio (never logged; counted)."""
        if self.ended or self.closing:
            raise KeyError(self.id)
        pcm = pcm[: len(pcm) // 2 * 2]
        self.audio_at = time.time()
        self.up_bytes += len(pcm)
        self._append_audio(pcm)

    def _append_audio(self, pcm: bytes) -> None:
        ws = self.ws
        if ws is None:
            return
        for i in range(0, len(pcm), UP_MAX_BYTES):
            try:
                ws.send(json.dumps({"type": "session.input_audio.append", "audio": base64.b64encode(pcm[i:i + UP_MAX_BYTES]).decode()}))
            except OSError:
                return

    def _audio_out(self, b64: str) -> None:
        try:
            pcm = base64.b64decode(b64)
        except ValueError:
            return
        if not pcm:
            return
        if quiet(pcm):  # (the output streams silence too: the watch plays what comes, so silence need not travel)
            self.quiet_chunks += 1
            return
        self.down_bytes += len(pcm)
        self._to_sinks(frame(b"a", pcm))

    def listen(self) -> queue.Queue:
        """A down stream (the watch's GET …/down): its frames come on this queue; None after the last."""
        q: queue.Queue = queue.Queue()
        st = self.state(transcript=False)
        q.put(frame(b"j", json.dumps({"t": "live", "kind": "state", "call": self.id, "state": st}, ensure_ascii=False).encode()))
        with self.lock:
            if self.ended:
                q.put(None)
            else:
                self.sinks.append(q)
        return q

    def unlisten(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.sinks:
                self.sinks.remove(q)

    def _to_sinks(self, data: bytes | None) -> None:
        with self.lock:
            sinks = list(self.sinks)
        for q in sinks:
            while q.qsize() >= SINK_MAX:  # (a watch that stopped reading: the oldest go, the call goes on)
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
            q.put(data)

    def _fail(self, msg: str) -> None:
        with self.lock:
            self.error, self.ended, self.phase = msg, True, "ended"
        self._tell("state", state=self.state(transcript=False))
        raise ValueError(msg)

    # ------------------------------------------------------------------ the sideband
    def _sideband(self) -> None:
        rapid = 0
        while not self.ended:
            key = self.key
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

    def _on_message(self, msg: str | bytes | dict) -> None:
        try:
            ev = msg if isinstance(msg, dict) else json.loads(msg)
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
                self.live_id = self.live_id or str(se.get("id") or "")  # (the WebSocket transport: its id comes here)
            if self.phase == "connecting":
                self._set_phase("listening")
        elif typ == "session.paused":
            self._set_phase("standby")
        elif typ == "session.resumed":
            self._set_phase("listening")
        elif typ == "session.usage.updated":
            r = (ev.get("context_window") or {}).get("usage_ratio")
            with self.lock:
                self.usage = ev.get("usage") or {}
                if r is not None:  # (at 0.9 GPT Live replaces the engine: instructions + 8 192 tokens of history)
                    self.usage["context_ratio"] = r
                    step = int(r * 10)
                    log = step > getattr(self, "ratio_step", 0)
                    self.ratio_step = max(step, getattr(self, "ratio_step", 0))
                else:
                    log = False
            if log:
                self.log("context_window", usage_ratio=round(r, 3))
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
            else:
                self.last_user = time.time()
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
            self.dels[did] = {"offset": int(ev.get("offset_ms") or 0), "at": time.time()}
            waiting = next((x for x in reversed(self.order) if self.dels[x].get("waiting")), None)
            if waiting:  # the user still talking for an earlier one: these words join it
                self.dels[waiting].setdefault("joined", []).append(did)
                self.dels[did]["into"] = waiting
            else:
                self.dels[did]["waiting"] = True
            self.order.append(did)
        self.log("delegation", event_id=ev.get("event_id"), delegation=did, offset_ms=self.dels[did]["offset"],
                 **({"joins": waiting} if waiting else {}))
        # At once: an empty thinking append cancels the automatic spoken acknowledgement (§5.1), unless the user
        # wants it (Settings → Voice: "Say when a task starts").
        if not self.cfg["live_ack"]:
            self._send({"type": "session.thinking.append", "event_id": f"quiet_{did}", "delegation_id": did, "content": ""})
        if not waiting:
            self._later(GRACE_S, self._await_turn, did)

    def _later(self, secs: float, fn, *args) -> None:
        tm = threading.Timer(secs, fn, args=args)
        tm.daemon = True
        tm.start()

    def _await_turn(self, did: str) -> None:
        """The request waits while the user is still talking (TURN_WAIT_S at most), then goes."""
        with self.lock:
            if self.ended:
                return
            wait = (time.time() - self.last_user < self.cfg["live_turn_pause"]
                    and time.time() - self.dels[did]["at"] < TURN_WAIT_S)
        if wait:
            self._later(0.2, self._await_turn, did)
        else:
            self._dispatch(did)

    def _dispatch(self, did: str) -> None:
        """After the user's turn ended: the request, from the transcript, into the bound session."""
        with self.lock:
            if self.ended:
                return
            d = self.dels[did]
            d["waiting"] = False
            joined = list(d.get("joined") or [])
            upto = turn_end(self.frags, max([d["offset"]] + [self.dels[j]["offset"] for j in joined]), self.used,
                            int(self.cfg["live_turn_pause"] * 1000))
            words, keys_ = assemble(self.frags, upto, self.cut_ms, self.used)
            self.used.update(keys_)
            self.cut_ms = max(self.cut_ms, upto)
            if upto > d["offset"] or joined:
                self.log("turn_waited", delegation=did, offset_ms=d["offset"], to_ms=upto, joined=joined,
                         ms_waited=int((time.time() - d["at"]) * 1000))
            for j in joined:  # (answered through this one's request: the latest of them gets the result)
                self.dels[j].update(how="joined", done=True)
            reply = last_reply(self.frags, d["offset"]) if words and REFERS.search(words) else ""
            prev = next((self.dels[x] for x in reversed(self.order) if x != did and self.dels[x].get("cid")), None)
        if words:
            self.live.hear(self.sid, words, self.live_id or self.id, did)  # (the user's own words: relay's proof)
        if not words:
            self.log("delegation_empty", delegation=did)
            self._send({"type": "session.commentary.append", "event_id": f"empty_{did}", "delegation_id": did,
                        "content": "Orbit heard no words for that request, so nothing was sent. Ask the user to say it again."})
            with self.lock:
                d.update(done=True, how="none")
            return
        if hangs_up(words):
            return self._hang_up_asked(did, d, words)
        if moves_in(words):
            return self._move_in(did, d, prev, words)
        if takes_back(words):
            return self._take_back(did, d, prev, words)
        first = urgent_words(words)  # "打断一下，先做这个 …": the running request stops; these run at once
        if first:
            return self._urgent(did, d, first, reply)
        add = steer_words(words)  # "插一句 / tell it to …": for the running request, whoever made it
        text = (add or words) + (f'\n\n(Context, not an instruction: the voice had just said: "{reply[:600]}")' if reply else "")
        origin = {"label": "Voice", "data": {"live": self.live_id, "delegation": did}}
        how, c, cant_steer = "new", None, False
        try:
            t = self.target
            cards = t.cards() if prev is not None or add else []
            pc = next((x for x in cards if x["id"] == prev["cid"]), None) if prev is not None else None
            run = next((x for x in cards if x["state"] in ACTIVE), None)
            if add and run is not None:
                cant_steer = not t.info().get("steers")
                c = t.send(text, origin, f"live:{self.live_id}:{did}") if cant_steer else \
                    t.steer(text, origin, f"live:{self.live_id}:{did}")
                how = "steer" if c.get("steered") else "new"  # (not steered: it ended just now, a request then)
            elif add:  # nothing runs: an ordinary request
                c = t.send(text, origin, f"live:{self.live_id}:{did}")
            elif pc is not None and pc["state"] == "queued" and not pc.get("hidden"):
                c, how = t.edit(pc["id"], pc["text"] + "\n\n" + text), "edit"  # the correction joins it
            elif pc is not None and pc["state"] in ACTIVE and t.info().get("steers"):
                c = t.steer(text, origin, f"live:{self.live_id}:{did}")
                how = "steer" if c.get("steered") else "new"
            else:
                c = t.send(text, origin, f"live:{self.live_id}:{did}")
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
            if joined:  # its result is said for the latest delegation that joined it
                last = joined[-1]
                self.dels[last].update(cid=c["id"], done=False)
                self.card_del[c["id"]] = last
                d["superseded"] = last
        self.log("request", delegation=did, cid=c["id"], how=how, state=c["state"], words=words,
                 ms_delegation_to_card=int((time.time() - d["at"]) * 1000), duplicate=bool(c.get("duplicate")))
        self._tell("delegation", delegation=did, cid=c["id"], how=how, sid=self.sid, offset=d["offset"])
        if not c.get("duplicate") and c["state"] not in FINAL:
            self._status(did, c, how, cant_steer)
        self._on_card(c)  # (a duplicate may already be done)

    # ------------------------------------------------------------------ the session's agent shapes the voice
    def voice_op(self, op: str, b: dict) -> dict:
        """agentvoice.py: new session instructions (appended, replacing the earlier), silent material, the ack."""
        from orbit.services.live import agentvoice
        with self.lock:
            if self.ended:
                return {"call": "ended"}
            self.vn = getattr(self, "vn", 0) + 1
            n = self.vn
        if op == "instruct":
            if b.get("remind") is not None:
                self.cfg["_voice_remind"] = str(b.get("remind") or "")
            evs = agentvoice.instruct_events(n, str(b.get("text") or ""))
        elif op == "context":
            evs = agentvoice.context_events(n, str(b.get("text") or ""), self.cfg.get("_voice_remind") or "")
        elif op == "ack":
            self.cfg["live_ack"] = config.current(self.desk.settings)["live_ack"] if b.get("ack") is None else bool(b["ack"])
            evs = []
        elif op == "say":  # the agent speaks through the voice; the request it is running then gets no spoken result
            with self.lock:
                running = [cid for cid, c in self.cache.items() if c.get("state") in ACTIVE]
                self.quiet.update(running)
                did = next((self.card_del[cid] for cid in running if cid in self.card_del), None)
            evs = agentvoice.say_events(n, str(b.get("text") or ""), did)
            self._say(evs)
            self.said_at = time.time()
            self.log("voice_op", op=op, events=[e["event_id"] for e in evs], quiet=running)
            return {"call": self.id, "appends": len(evs), "quiet": running}
        else:
            raise ValueError(f"no such voice op: {op}")
        for ev in evs:
            self._send(ev)
        self.log("voice_op", op=op, events=[e["event_id"] for e in evs],
                 tokens=sum(agentvoice.tokens(e["content"]) for e in evs))
        return {"call": self.id, "appends": len(evs)}

    def transcript(self, since: int = 0) -> dict:
        with self.lock:
            quiet = time.time() - max(getattr(self, "last_user", 0) or 0, getattr(self, "last_voice", 0) or 0) > 2
            ts = transcript_turns(self.frags, self.ended, quiet, since)
        return {"call": self.id, "ended": self.ended, "started": self.t0, "turns": ts}

    def _mirror(self, final: bool = False) -> None:
        """The transcript to the session's machine when it is another's (its agent reads it there: `voice transcript`),
        from the first turn not yet sent as done; at most one push in flight, nothing when nothing changed."""
        if self.target.remote is None:
            return
        with self.lock:
            if self.mirroring and not final:
                return
            ts = transcript_turns(self.frags, self.ended or final, False)
            k, chars, was_ended = self.mirrored
            size = sum(len(t["text"]) for t in ts)
            fresh = time.time() - getattr(self, "mirrored_at", 0) < MIRROR_BEAT_S
            if (len([t for t in ts if t["done"]]), size, final) == (k, chars, was_ended) and fresh:
                return
            self.mirroring = True
        try:
            self.target._call("POST", "/voice", {"op": "mirror", "call": self.id, "started": self.t0, "from": k,
                                                 "turns": ts[k:], "ended": final})
            with self.lock:
                self.mirrored = (len([t for t in ts if t["done"]]), size, final)
                self.mirrored_at = time.time()
        except Exception as e:  # noqa: BLE001 - the next change tries again
            self.log("mirror_failed", error=f"{type(e).__name__}: {e}"[:200])
        finally:
            with self.lock:
                self.mirroring = False

    def _hang_up_asked(self, did: str, d: dict, words: str) -> None:
        """"挂断" / "hang up": the voice says a short goodbye, then the call ends (as the page's hang-up does)."""
        with self.lock:
            d.update(done=True, how="hangup", text=words)
        self.log("hang_up_asked", delegation=did, words=words)
        self._send({"type": "session.commentary.append", "event_id": f"bye_{did}", "delegation_id": did,
                    "content": "The user asked to end the call. Say a short goodbye (a few words) now; Orbit hangs up "
                               "right after."})
        self._later(0, self._hang_up_after_bye)

    def _hang_up_after_bye(self) -> None:
        start = time.time()
        while not self.ended and time.time() - start < HANG_UP_WAIT_S:  # (its goodbye spoken, or a few seconds)
            if time.time() - start > 1.2 and time.time() - self.last_voice > 1.0:
                break
            time.sleep(0.2)
        if not self.ended:
            self.hang_up()

    def _move_in(self, did: str, d: dict, prev: dict | None, words: str) -> None:
        """"Put it in the running one": the waiting request (the call's last, else the session's latest) is withdrawn
        and its words steered into the running request, when its agent takes additions; the voice is told what
        happened (spoken: the user asked)."""
        from orbit.drivers.base import strip_orbit_line
        t, how, fact, c = self.target, "none", "", None
        try:
            cards = t.cards()
            run = next((x for x in cards if x["state"] in ACTIVE), None)
            mine = next((x for x in cards if prev is not None and x["id"] == prev["cid"]), None)
            q = mine if mine is not None and mine["state"] == "queued" and not mine.get("hidden") else \
                next((x for x in reversed(cards) if x["state"] == "queued" and not x.get("hidden")), None)
            if q is None:
                fact = "There is no waiting request to add to the running one."
            elif run is None:
                fact = f"Nothing is running to add request #{q['id']} to; it stays queued and starts next."
            elif not t.info().get("steers"):
                fact = f"Your backend can't take additions while a request runs: request #{q['id']} stays queued."
            else:
                t.withdraw(q["id"])
                c = t.steer(strip_orbit_line(q.get("text") or ""), {"label": "Voice", "data": {"live": self.live_id, "delegation": did}},
                            f"live:{self.live_id}:{did}")
                if c.get("steered"):
                    how, fact = "steer", (f"Orbit took back request #{q['id']} and added its words to request #{c['id']} "
                                          "while your backend works on it.")
                else:  # (the running one ended just now)
                    how, fact = "new", f"Request #{run['id']} had just ended, so request #{q['id']}'s words were sent as request #{c['id']}."
        except Exception as e:  # noqa: BLE001 - the voice is told, the call goes on
            fact = f"Orbit could not move the request: {e}"[:300]
        with self.lock:
            d.update(how=how, text=words, cid=(c or {}).get("id"), done=c is None)
            if c is not None:
                self.card_del[c["id"]] = did  # (its result now answers these words too)
        self.log("move_in", delegation=did, how=how, cid=(c or {}).get("id"), words=words)
        if c is not None:
            self._tell("delegation", delegation=did, cid=c["id"], how=how, sid=self.sid, offset=d["offset"])
        self._send({"type": "session.commentary.append", "event_id": f"movein_{did}", "delegation_id": did, "content": fact})

    def _urgent(self, did: str, d: dict, words: str, reply: str) -> None:
        """Do this first: the running request (whoever made it) stops, what waits behind it keeps its order, and these
        words go ahead of them all: the agent takes them next, i.e. at once."""
        t, run, c = self.target, None, None
        text = words + (f'\n\n(Context, not an instruction: the voice had just said: "{reply[:600]}")' if reply else "")
        origin = {"label": "Voice", "data": {"live": self.live_id, "delegation": did}}
        try:
            run = next((x for x in t.cards() if x["state"] in ACTIVE and not x.get("hidden")), None)
            c = t.send(text, origin, f"live:{self.live_id}:{did}", first=True)  # (first: the stop must not let the queue's head start)
            if run is not None:
                t.stop()
        except Exception as e:  # noqa: BLE001 - the voice is told, the call goes on
            self.log("send_failed", delegation=did, error=f"{type(e).__name__}: {e}"[:300])
            self._send({"type": "session.commentary.append", "event_id": f"fail_{did}", "delegation_id": did,
                        "content": f"Orbit could not do that first: {e}"[:400]})
            with self.lock:
                d.update(done=True, how="failed")
            return
        with self.lock:
            d.update(cid=c["id"], how="urgent", text=words)
            self.card_del[c["id"]] = did
        self.log("request", delegation=did, cid=c["id"], how="urgent", state=c["state"], words=words,
                 stopped=run["id"] if run else None, ms_delegation_to_card=int((time.time() - d["at"]) * 1000))
        self._tell("delegation", delegation=did, cid=c["id"], how="urgent", sid=self.sid, offset=d["offset"])
        fact = (f"Orbit stopped request #{run['id']} and put this first, as request #{c['id']}: your backend starts on it now "
                "(what was waiting stays queued after it)." if run else
                f"Orbit put this first, as request #{c['id']}: nothing was running, so your backend starts on it now.")
        self._send({"type": "session.thinking.append", "event_id": f"status_{did}", "delegation_id": did,
                    "content": fact + " Don't state the answer until the result arrives."})

    def _take_back(self, did: str, d: dict, prev: dict | None, words: str) -> None:
        """The user took the request back: the call's last request is withdrawn if it has not started, stopped if it
        runs; the voice is told which (spoken: the user asked)."""
        t, how, fact, c = self.target, "none", "", None
        w = _norm(words)
        running = any(x in w for x in RUNNING_WORDS + STOP_VERBS)  # the words name the running one ("停止当前在跑的")
        withdraw = any(x in w for x in WITHDRAW_VERBS)
        try:
            cards = t.cards()
            mine = next((x for x in cards if prev is not None and x["id"] == prev["cid"]), None)
            if running and not withdraw:  # the session's running request, whoever made it
                c = next((x for x in cards if x["state"] in ACTIVE), None)
            elif withdraw and not any(x in w for x in RUNNING_WORDS):  # a waiting one: the call's, else the latest
                c = mine if mine is not None and mine["state"] == "queued" and not mine.get("hidden") else \
                    next((x for x in reversed(cards) if x["state"] == "queued" and not x.get("hidden")), None)
            if c is None:
                c = mine
            if c is None or c["state"] in FINAL or c.get("hidden"):  # (none of this call's: the session's last waiting one)
                c = next((x for x in reversed(cards) if x["state"] == "queued" and not x.get("hidden")), None) or \
                    next((x for x in reversed(cards) if x["state"] in ACTIVE), None)
            if c is None:
                fact = "There is no request waiting or running to take back."
            elif c["state"] == "queued":
                t.withdraw(c["id"])
                how, fact = "withdraw", f"Orbit withdrew request #{c['id']}: it had not started, so nothing was done."
            elif c["state"] in ACTIVE:
                t.stop()
                how, fact = "stop", f"Orbit stopped request #{c['id']}, which was running; what it did so far stays in its card."
            else:
                fact = f"Request #{c['id']} has already finished: there is nothing to take back."
        except Exception as e:  # noqa: BLE001 - the voice is told, the call goes on
            fact = f"Orbit could not take the request back: {e}"[:300]
        with self.lock:
            d.update(done=True, how=how, text=words, cid=(c or {}).get("id") if how != "none" else None)
        self.log("take_back", delegation=did, how=how, cid=(c or {}).get("id"), words=words)
        if how != "none":
            self._tell("delegation", delegation=did, cid=c["id"], how=how, sid=self.sid, offset=d["offset"])
        self._send({"type": "session.commentary.append", "event_id": f"takeback_{did}", "delegation_id": did, "content": fact})

    def _status(self, did: str, c: dict, how: str, cant_steer: bool = False) -> None:
        """What became of the request, as silent context: the voice knows the facts (sent, queued behind another,
        joined to a waiting one) instead of guessing how Orbit handles it. Plain facts only: "nothing else runs
        before it" was read back as "it is not queued", and an order to say "queued" made the voice narrate a wait of
        a second; it is asked to say so only when the wait is real."""
        # A card is "queued" for a moment even in an idle session (until its agent takes it): it waits only when
        # another request runs, or waits ahead of it.
        try:
            cards = self.target.cards()
        except (RuntimeError, KeyError):
            cards = []
        run = next((x for x in cards if x["state"] in ACTIVE and x["id"] != c["id"]), None)
        ahead = [x for x in cards if x["state"] == "queued" and x["id"] < c["id"]]
        now = next((x for x in cards if x["id"] == c["id"]), c)
        busy = (f'your backend is busy with request #{run["id"]}' if run else
                f'request #{ahead[0]["id"]} waits before it' if ahead else "")
        wait_ = " Don't state the answer until the result arrives."  # (a real call said "yes" before the result said "not yet")
        speak = False
        if how == "edit":
            fact = (f"Orbit added these words to request #{c['id']}, which has not started" + (f": {busy}" if busy else "")
                    + ". It is one request, not several. Its answer comes when your backend gets to it." + wait_)
        elif how == "steer":
            fact = f"Orbit added these words to request #{c['id']} while your backend works on it." + wait_
        elif cant_steer:  # (asked to add to the running request; its agent cannot take that mid-request)
            fact = (f"Your backend can't take additions while a request runs, so Orbit queued these words as request "
                    f"#{c['id']}" + (f"; it waits: {busy}" if busy else "") + ". Its answer comes when your backend gets to it." + wait_)
            speak = True  # (the user asked for something else: they should hear it)
        elif now["state"] == "queued" and busy:
            speak = run is not None and time.time() - (run.get("started") or time.time()) > QUEUE_REAL_S
            fact = (f"Orbit sent this as request #{c['id']}; it waits: {busy}."
                    + (" Tell the user in a few words that it is queued; its" if speak else " Its")
                    + " answer comes when your backend gets to it." + wait_)
        else:
            fact = f"Orbit sent this as request #{c['id']}; your backend started on it." + wait_
        ev = {"type": "session.thinking.append", "event_id": f"status_{did}", "delegation_id": did, "content": fact}

        def send_now() -> None:
            with self.say_lock:
                if c["id"] not in self.said:  # (its result went first: the status is stale)
                    self._send(ev)
        out = (lambda: self._say([ev])) if speak else send_now
        wait = ACK_DELAY_MS / 1000 + 0.3 - (time.time() - self.dels[did]["at"]) if self.cfg["live_ack"] else 0
        if wait > 0:  # (any append cancels the spoken acknowledgement the user asked for: after its moment)
            tm = threading.Timer(wait, lambda: None if self.ended else out())
            tm.daemon = True
            tm.start()
        else:
            out()

    # ------------------------------------------------------------------ the session's state → silent context
    def _follow_cards(self) -> None:
        """The desk's event stream (a remote session's cards come here too, as name~sid): every card of the bound
        session updates the state the voice is told (whoever made it: typed, a plugin, a follow-up); this call's own
        requests are also followed to their result."""
        q = self.q
        self._seed()
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
            if c.get("sid") != self.sid or "id" not in c:
                continue
            with self.lock:
                old = self.cache.get(c["id"])
                if old is None or c.get("rev", 0) >= old.get("rev", 0):  # (an event older than the seed: kept out)
                    self.cache[c["id"]] = c
                # A burst of changes goes out as one snapshot, COALESCE_S after its first (also when a step-only
                # change is waiting out STEP_EVERY_S: this one may be bigger).
                self.snap_due = min(self.snap_due or float("inf"), time.time() + COALESCE_S)
            if c["id"] in self.card_del:
                self._on_card(c)

    def _seed(self, baseline: bool = True) -> bool:
        """The session's cards and name, read once (one request for another machine's session); after that the event
        stream keeps them. The state as the call starts is in its startup input (baseline), so it is not sent again;
        read late (it failed at first), whatever it shows is told."""
        try:
            cards, name = self.target.cards(), self.target.info()["name"]
        except Exception as e:  # noqa: BLE001 - tried again on the next change
            self.log("state_failed", error=f"{type(e).__name__}: {e}"[:300])
            return False
        with self.lock:
            for c in cards:
                old = self.cache.get(c["id"])
                if old is None or c.get("rev", 0) > old.get("rev", 0):
                    self.cache[c["id"]] = c
            self.name, self.seeded = name or "", True
            if baseline and self.snap_big is None:
                _, self.snap_big, self.snap_step = state_snapshot(self.name, cards)
                self.snap_at = time.time()
        return True

    def _snapshot(self) -> None:
        """The state snapshot when one is due (polled by the clock): skipped when nothing but the clock changed; a
        change of step only waits until STEP_EVERY_S after the last; never while a result waits to be said (it would
        give the answer away before it is spoken)."""
        now = time.time()
        with self.lock:
            if not self.snap_due or now < self.snap_due or self.held or self.ended:
                return
            # Not into speech: a snapshot landing as the voice began a result's reply came out spoken, garbled
            # ("一个 vector to 28; keep waiting 斜着…", 2026-11-27). After the voice pauses, and SNAP_AFTER_SAY_S
            # after a spoken result (which already says what the snapshot would).
            if now - (getattr(self, "last_voice", 0) or 0) < SPEAKING_HOLD_S or now - self.said_at < SNAP_AFTER_SAY_S:
                return
            self.snap_due = 0.0
            seeded = self.seeded
        if not seeded and not self._seed(baseline=False):
            with self.lock:
                self.snap_due = self.snap_due or now + 5
            return
        with self.lock:
            shown = [c for c in self.cache.values() if not c.get("silent") and c["id"] not in self.quiet]
            text, big, step = state_snapshot(self.name, sorted(shown, key=lambda c: c["id"]), now)
            if (big, step) == (self.snap_big, self.snap_step):
                return
            if big == self.snap_big and now - self.snap_at < STEP_EVERY_S:
                self.snap_due = self.snap_due or self.snap_at + STEP_EVERY_S
                return
            self.snap_big, self.snap_step, self.snap_at = big, step, now
            self.snap_n += 1
            n = self.snap_n
        self._send({"type": "session.thinking.append", "event_id": f"state_{n}", "delegation_id": None, "content": text})

    # ------------------------------------------------------------------ results → spoken
    def _on_card(self, c: dict) -> None:
        """A card of this call's: its end becomes the result (said once, for the latest delegation on it). Its
        progress is in the state snapshots, not lines of its own (a tool command read out is noise)."""
        with self.lock:
            did = self.card_del.get(c["id"])
            d = self.dels.get(did) if did else None
            if d is None or d.get("done") or self.ended:
                return
            st = c.get("state")
            if st not in FINAL or (st == "paused" and "retrying in" in (c.get("error") or "")):
                return
            d["done"] = True
        length = self.cfg["live_detail"]
        if c["id"] in self.quiet:  # (its agent said what matters through `voice say`: done, silently)
            self._say([{"type": "session.thinking.append", "event_id": f"result_{did}", "delegation_id": did,
                        "content": f"Request #{c['id']} is {'done' if st == 'completed' else st}; its agent already told "
                                   "the user what matters. Do not announce it."}],
                      {"delegation": did, "cid": c["id"], "state": st, "finished": c.get("finished"), "quiet": True})
            return
        evs = []
        more = result_detail(c, length)
        if more:  # first, silently: the voice has it when the commentary asks it to speak
            evs.append({"type": "session.thinking.append", "event_id": f"detail_{did}", "delegation_id": did, "content": more})
        evs.append({"type": "session.commentary.append", "event_id": f"result_{did}", "delegation_id": did,
                    "content": result_text(c, length)})
        self._say(evs, {"delegation": did, "cid": c["id"], "state": st, "finished": c.get("finished")})

    def _talking(self) -> bool:
        return time.time() - self.last_user < USER_HOLD_S

    def _say(self, evs: list[dict], result: dict | None = None) -> None:
        """Appends that make the voice speak: held while the user is talking (a result read out over them collided in
        three real calls), released by the clock when they pause, or after HOLD_MAX_S."""
        with self.lock:
            if self.held or self._talking():  # (behind anything held already: in order)
                self.held.append({"at": time.time(), "evs": evs, "result": result})
                held = True
            else:
                held = False
        if held:
            self.log("held", events=[e["event_id"] for e in evs])
        else:
            self._out(evs, result, 0)

    def _out(self, evs: list[dict], result: dict | None, held_ms: int) -> None:
        if result is not None:
            fin = result.get("finished") or time.time()
            self.log("result", **{k: v for k, v in result.items() if k != "finished"},
                     ms_card_to_result=int((time.time() - fin) * 1000), **({"held_ms": held_ms} if held_ms else {}))
        with self.say_lock:  # (together: nothing between a result's detail and its commentary)
            if result is not None and result.get("cid") is not None:
                self.said.add(result["cid"])
            if any(e["type"] == "session.commentary.append" for e in evs):
                self.said_at = time.time()
            for ev in evs:
                self._send(ev)

    def _release(self) -> None:
        with self.lock:
            if not self.held or self.ended or (self._talking() and time.time() - self.held[0]["at"] < HOLD_MAX_S):
                return
            out, self.held = self.held, []
        for h in out:
            self._out(h["evs"], h["result"], int((time.time() - h["at"]) * 1000))

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
            if self.transport == "watch" and not self.closing and time.time() - self.audio_at > GAP_FILL_S:
                self._append_audio(b"\0" * int(RATE * 0.25) * 2)  # (the watch's audio paused: the session's timeline goes on)
            if n % 4 == 0 and self.target.remote is not None and not self.mirroring:
                threading.Thread(target=self._mirror, daemon=True).start()
            try:
                self._release()
                self._snapshot()
            except Exception as e:  # noqa: BLE001 - the clock goes on (it also ends an expired call)
                self.log("tick_error", error=f"{type(e).__name__}: {e}"[:300])
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
        threading.Thread(target=self._mirror, args=(True,), daemon=True).start()
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
        if self.transport == "watch":
            self.log("audio", up_bytes=self.up_bytes, down_bytes=self.down_bytes, quiet_chunks=self.quiet_chunks)
        self._to_sinks(None)  # (the watch's down stream ends)

    def _note(self) -> dict | None:
        """The call as a note card (no model call): its transcript by turn, the requests it made."""
        with self.lock:
            ts = turns(self.frags)
            reqs = [(k, self.dels[k]) for k in self.order if self.dels[k].get("cid") is not None]
        if not ts and not reqs:
            return None
        mins = max(1, round((time.time() - self.t0) / 60))
        when = datetime.fromtimestamp(self.t0).strftime("%H:%M")
        sent: dict[int, list[int]] = {}  # a turn of the user's → the requests it became (the delegation's offset falls in it)
        for _, d in reqs:
            k = max((i for i, (w, st, _x) in enumerate(ts) if w == "user" and st <= d["offset"]), default=None)
            if k is not None and d["cid"] not in sent.setdefault(k, []):
                sent[k].append(d["cid"])
        lines = []
        for i, (who, start, text) in enumerate(ts):
            to = (" · → " + ", ".join(f"#{c}" for c in sent[i])) if sent.get(i) else ""
            lines.append(f"**{'You' if who == 'user' else 'Voice'}** · {start // 60000}:{start // 1000 % 60:02d}{to}  \n{text}")
        made = sorted({d['cid'] for _, d in reqs})
        tail = ("Requests from this call: " + " · ".join(f"#{c}" for c in made)) if made else "No requests from this call."
        why = {"close_requested": "", "expired": " It ended: the voice session's time ran out.",
               "remote_hangup": " It ended: the page's connection closed.", "connection_lost": " It ended: the connection was lost.",
               "content": " It ended: a safety filter stopped it."}.get(self.reason, f" It ended ({self.reason})." if self.reason else "")
        body = "\n\n".join(lines) + f"\n\n{tail}{why}"
        return self.target.note(f"Voice call · {when} · {mins} min", body,
                                {"label": "Voice", "data": {"live": self.live_id, "call": self.id}})


class Live:
    """The server's voice calls: one at a time."""

    def __init__(self, desk):
        self.desk = desk
        self.call: Call | None = None
        self.lock = threading.Lock()
        # What the user said for each request of a call (its words, from the transcript GPT Live sent here): the
        # proof that words an agent relays are the user's own (orbit/desk/relay.py). Memory only; the latest 300.
        self.heard: collections.deque = collections.deque(maxlen=300)
        # The transcripts of calls another machine holds with this server's sessions (that machine mirrors them here,
        # Call._mirror): sid → {call, started, turns, ended}. Memory only; the latest call of each session.
        self.mirrors: dict = {}
        self.pending: dict = {}  # voice ops sent out to the machine holding the call: id → (event, its answer)

    def mirror(self, sid: str, b: dict) -> dict:
        with self.lock:
            m = self.mirrors.get(sid)
            if m is None or m["call"] != b.get("call"):
                m = self.mirrors[sid] = {"call": str(b.get("call") or ""), "started": b.get("started"), "turns": [],
                                         "ended": False}
            k = max(0, min(int(b.get("from") or 0), len(m["turns"])))
            m["turns"] = m["turns"][:k] + [t for t in (b.get("turns") or []) if isinstance(t, dict)]
            m["ended"] = bool(b.get("ended"))
            m["at"] = time.time()
            if len(self.mirrors) > 50:
                self.mirrors.pop(next(iter(self.mirrors)))
        return {"ok": True, "turns": len(m["turns"])}

    def transcript_for(self, sid: str, since: int = 0) -> dict:
        """The transcript of the latest call with this session: this server's call, else the mirror of another
        machine's."""
        c = self.call
        if c is not None and c.sid == sid:
            return c.transcript(since)
        with self.lock:
            m = self.mirrors.get(sid)
            if m is None:
                return {"call": None, "ended": True, "turns": []}
            return {**m, "turns": [t for t in m["turns"] if t.get("start", 0) >= since]}

    def hear(self, sid: str, words: str, call: str, delegation: str) -> None:
        if words.strip():
            self.heard.append({"sid": sid, "words": words, "call": call, "delegation": delegation, "at": time.time()})

    def model(self) -> str:
        return config.current(self.desk.settings)["live_model"]

    def start(self, sid: str, sdp: str) -> dict:
        if not isinstance(sdp, str) or not sdp.strip():
            raise ValueError("an SDP offer is needed")
        Target(self.desk, sid)  # (KeyError: no such session, here or on a machine this server reaches)
        if not keys.get(self.desk.state_dir, "openai"):
            raise NoKey("No OpenAI API key: set one in Settings → Voice")
        with self.lock:  # one call at a time, whoever asks: a second is refused, not put in the first's place
            if self.call is not None and not self.call.ended:
                raise Busy(self.call)
            new = self.call = Call(self, sid, self.model())
        return new.create(sdp)

    def start_watch(self, sid: str) -> dict:
        """A call whose audio comes and goes through this server (the watch, §12)."""
        Target(self.desk, sid)
        if not keys.get(self.desk.state_dir, "openai"):
            raise NoKey("No OpenAI API key: set one in Settings → Voice")
        with self.lock:
            if self.call is not None and not self.call.ended:
                raise Busy(self.call)
            new = self.call = Call(self, sid, self.model(), transport="watch")
        return new.create_ws()

    def watch_call(self, call: str) -> Call:
        """The watch's call by its id (KeyError: none, another, or not the watch's)."""
        c = self.call
        if c is None or c.id != call or c.transport != "watch":
            raise KeyError(call)
        return c

    def stop(self, call: str = "") -> dict:
        c = self.call
        if c is None or (call and c.id != call):
            raise KeyError(call or "call")
        return c.hang_up()

    def voice_op_for(self, sid: str, op: str, b: dict) -> dict:
        """A voice op of a session's agent: into this server's call with that session, else out as an event for the
        server holding a call with it (its federation takes it, as hang_up_for), which says it took it (op_applied);
        no answer within OP_WAIT_S: no call with this session runs anywhere."""
        c = self.call
        if c is not None and not c.ended and c.sid == sid:
            return {"applied": True, **c.voice_op(op, b)}
        oid = uuid.uuid4().hex[:12]
        ev, box = threading.Event(), {}
        with self.lock:
            self.pending[oid] = (ev, box)
        try:
            self.desk.broadcast({"t": "live-op", "sid": sid, "op": op, "b": b, "id": oid})
            if ev.wait(OP_WAIT_S):
                return {"applied": True, "by": box.get("by", ""), **(box.get("result") or {})}
        finally:
            with self.lock:
                self.pending.pop(oid, None)
        return {"applied": False, "call": None}

    def op_applied(self, oid: str, b: dict) -> dict:
        """The machine holding the call took a voice op this server sent out (voice_op_for)."""
        with self.lock:
            ev, box = self.pending.get(oid, (None, None))
        if ev is None:
            return {"ok": False}
        box.update({"by": str(b.get("by") or "")[:80], "result": b.get("result") if isinstance(b.get("result"), dict) else {}})
        ev.set()
        return {"ok": True}

    def call_elsewhere(self, sid: str) -> bool:
        """Another machine holds a running call with this session (its mirror is fresh)."""
        with self.lock:
            m = self.mirrors.get(sid)
            return bool(m and not m["ended"] and time.time() - m.get("at", 0) < 3 * MIRROR_BEAT_S)

    def hang_up_for(self, sid: str) -> dict:
        """`orbit-agent hangup` from a session's agent: this server's call with that session ends; with no call here
        (the call is held by another machine's server, this one's session its backend) the ask goes out as an event,
        which that server's federation takes."""
        c = self.call
        if c is not None and not c.ended and (not sid or c.sid == sid):
            c.hang_up()
            return {"ended": True, "call": c.id}
        self.desk.broadcast({"t": "live-hangup", "sid": sid})
        return {"ended": False, "asked": True}

    def state(self) -> dict:
        c = self.call
        return {"call": None} if c is None else c.state()

    def shutdown(self) -> None:
        c = self.call
        if c is not None and not c.ended:
            c.hang_up()
