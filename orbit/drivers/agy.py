"""agy driver: one long-lived `agy --input-format stream-json --output-format stream-json -p=` per session
(on the shared engine, process.py).

Protocol (agy 1.4): each request is one stdin line `{"event": "user", "message": {"role": "user", "content":
[{"type": "text", "text": ...}]}}`. stdout carries `init` (with the conversation id), `step_update` (step_index,
state ACTIVE|DONE|ERROR|..., step_type user_input|agent_response|tool|system_message|..., text_delta, tool_info,
usage) and one terminal `result` per request (status SUCCESS|ERROR, finished_reason).

agy names its own conversations; the id from `init` is linked to the Orbit session (base.Driver.link) and later
processes start with `--conversation <id>`. The conversation lives in agy's store
(`~/.gemini/antigravity-cli/conversations/<id>.db`, SQLite with protobuf steps); history is rebuilt from it.

Stop sends SIGINT: agy ends the request (`finished_reason: interrupted`) and exits; the next request starts a new
process on the same conversation. agy cannot fork a conversation, so a clone starts a new one (its card list shows
the source's cards from before the clone, but the agent does not see them).

Modes: default runs with the user's own agy settings; acceptEdits = `--mode accept-edits`; plan = `--mode plan`;
bypassPermissions = `--dangerously-skip-permissions`. In print mode agy cannot ask, so a refused tool fails as a step.
"""

from __future__ import annotations

import json
import os
import shlex
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from orbit.drivers.base import DriverEvent, HistoryCard, strip_orbit_line, tool_summary
from orbit.drivers.process import ProcessDriver


MODE_FLAGS = {
    "default": [],
    "acceptEdits": ["--mode", "accept-edits"],
    "plan": ["--mode", "plan"],
    "bypassPermissions": ["--dangerously-skip-permissions"],
}
FINAL = ("DONE", "ERROR", "CANCELED", "CANCELLED", "HALTED")
# Store step types (agy's CortexStepType numbers) and step statuses.
USER_INPUT, AGENT_RESPONSE = 14, 15
STATUS_ERROR, STATUS_CANCELLED = 7, 6


def conversations_root() -> str:
    return os.environ.get("ORBIT_AGY_CONVERSATIONS",
                          str(Path.home() / ".gemini" / "antigravity-cli" / "conversations"))


# ---------------------------------------------------------------------- protobuf (wire format only, no schema)
def _varint(b: bytes, i: int) -> tuple[int, int]:
    r = s = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 0x7F) << s
        s += 7
        if c < 0x80:
            return r, i


def pb(b: bytes | None) -> dict[int, list]:
    """Fields of one message: number -> values (int for varints, bytes for length-delimited). {} if not a message."""
    out: dict[int, list] = {}
    if not b:
        return out
    i = 0
    try:
        while i < len(b):
            key, i = _varint(b, i)
            f, wt = key >> 3, key & 7
            if wt == 0:
                v, i = _varint(b, i)
            elif wt == 1:
                v, i = b[i:i + 8], i + 8
            elif wt == 5:
                v, i = b[i:i + 4], i + 4
            elif wt == 2:
                n, i = _varint(b, i)
                v, i = b[i:i + n], i + n
            else:
                return {}
            if f == 0 or i > len(b):
                return {}
            out.setdefault(f, []).append(v)
    except IndexError:
        return {}
    return out


def _first(m: dict[int, list], *path: int) -> Any:
    """The first value at a field path, decoding nested messages on the way."""
    for k, f in enumerate(path):
        vals = m.get(f)
        if not vals:
            return None
        if k == len(path) - 1:
            return vals[0]
        if not isinstance(vals[0], bytes):
            return None
        m = pb(vals[0])
    return None


def _str(v: Any) -> str:
    return v.decode("utf-8", "replace") if isinstance(v, bytes) else ""


class AgyDriver(ProcessDriver):
    native_fork = False  # agy cannot fork: a fork's first request carries the conversation over (Desk)
    harness = "agy"
    label = "agy"
    reattachable = True  # a restart of the server hands its process off (the Harness Server keeps it running)
    _models: list[dict[str, str]] | None = None

    def __init__(self, **kw):
        super().__init__(**kw)
        self.conversation = ""
        self.parts: dict[int, list[str]] = {}  # agent_response text deltas by step index
        self.started_steps: set[int] = set()

    @classmethod
    def usage(cls):
        from orbit.services.usage import agy_usage
        return agy_usage()

    @classmethod
    def models(cls) -> list[dict[str, str]]:
        if cls._models is None:
            try:
                out = subprocess.run(shlex.split(os.environ.get("ORBIT_AGY_BIN", "agy")) + ["models"],
                                     capture_output=True, text=True, timeout=20).stdout
            except (OSError, subprocess.TimeoutExpired):
                return []
            models = []
            for line in out.splitlines():
                slug, _, name = line.partition("\t")
                if slug.strip() and name.strip():
                    models.append({"value": slug.strip(), "label": name.strip()})
            if models:
                cls._models = models
            return models
        return cls._models

    # ------------------------------------------------------------------ process
    def program(self) -> list[str]:
        return shlex.split(os.environ.get("ORBIT_AGY_BIN", "agy"))

    def command(self) -> list[str]:
        cmd = self.program() + ["--input-format", "stream-json", "--output-format", "stream-json"]
        if self.model:
            cmd += ["--model", self.model]
        cmd += MODE_FLAGS.get(self.mode, [])
        own = self.conversation or self.linked()
        if own:
            cmd += ["--conversation", own]
        return cmd + ["-p="]

    def terminal_command(self) -> list[str] | None:
        own = self.conversation or self.linked()
        return self.program() + ["--conversation", own] if own else None

    def request_line(self, text: str) -> str:
        return json.dumps({"event": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}})

    def begin(self) -> None:
        self.parts, self.started_steps = {}, set()

    def checkpoint(self) -> dict[str, Any]:
        return {**super().checkpoint(), "conversation": self.conversation,
                "parts": {str(k): list(v) for k, v in self.parts.items()}, "started_steps": sorted(self.started_steps)}

    def restore(self, cp: dict[str, Any]) -> None:
        super().restore(cp)
        self.conversation = str(cp.get("conversation") or "")
        self.parts = {int(k): [str(x) for x in v] for k, v in (cp.get("parts") or {}).items()}
        self.started_steps = {int(x) for x in cp.get("started_steps") or []}

    # ------------------------------------------------------------------ stream
    def handle(self, msg: dict) -> None:
        ev = msg.get("event")
        if ev == "init":
            if msg.get("conversation_id"):
                self.conversation = msg["conversation_id"]
                self.link(self.conversation)
        elif ev == "step_update":
            su = msg.get("step_update") or {}
            if self.conversation and su.get("conversation_id") not in (None, "", self.conversation):
                return  # a subagent's step
            self._step(su)
        elif ev == "result":
            r = msg.get("result") or {}
            if r.get("conversation_id") and not self.conversation:
                self.conversation = r["conversation_id"]
                self.link(self.conversation)
            text = self.answer_so_far()
            denied = [d.get("display_name") or d.get("action") for d in r.get("denied_actions") or []
                      if isinstance(d, dict)]
            if r.get("status") == "SUCCESS":
                if denied:
                    text = (text + "\n\n" if text else "") + f"*Refused without asking: {', '.join(denied)}.*"
                self.finish(True, text)
            elif self.stopping or r.get("finished_reason") == "interrupted":
                self.finish(False, text, cancelled=True)
            else:  # (a result while idle, e.g. "input closed" when closed, is ignored by finish)
                self.finish(False, text, error=r.get("error") or r.get("finished_reason") or "error")

    def _step(self, su: dict) -> None:
        idx, st, state = su.get("step_index"), su.get("step_type", ""), su.get("state", "")
        if not isinstance(idx, int):
            return
        if st == "agent_response":
            if su.get("text_delta"):
                self.parts.setdefault(idx, []).append(su["text_delta"])
            if state in FINAL:
                text = "".join(self.parts.pop(idx, [])).strip()
                if text:
                    self.said(text)
                u = su.get("usage") or {}
                ctx = int(u.get("input_tokens") or 0) + int(u.get("cache_read_tokens") or 0)
                if ctx:
                    self.emit(DriverEvent("usage", {"context": ctx, "model": self.model}))
        elif st == "tool":
            info = su.get("tool_info") or {}
            sid = f"s{idx}"
            if idx not in self.started_steps:
                self.started_steps.add(idx)
                self.tool_started({"id": sid, "name": info.get("name") or su.get("tool_name") or "tool",
                                                     "summary": tool_summary(info.get("parameters") or {})})
            if state in FINAL:
                self.emit(DriverEvent("tool_end", {"id": sid, "error": state != "DONE" or bool(info.get("error"))}))
        elif "compaction" in st and state == "DONE":
            self.emit(DriverEvent("compaction", {}))

    # ------------------------------------------------------------------ history
    def store(self, sid: str | None = None) -> str | None:
        conv = (self.conversation if sid is None else "") or self.linked(sid)
        if not conv:
            return None
        p = os.path.join(conversations_root(), f"{conv}.db")
        return p if os.path.isfile(p) else None

    def _cards(self, path: str) -> list[HistoryCard]:
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
            try:
                rows = db.execute("select idx, step_type, status, step_payload from steps order by idx").fetchall()
            finally:
                db.close()
        except sqlite3.Error:
            return []
        cards: list[HistoryCard] = []
        cur: HistoryCard | None = None
        texts: list[str] = []
        open_steps: dict[str, dict] = {}
        last_status = 0

        def settle():
            if cur is not None:
                cur.answer = "\n\n".join(texts)
                if last_status == STATUS_CANCELLED:
                    cur.state = "cancelled"

        for _idx, stype, status, payload in rows:
            m = pb(payload)
            secs, nanos = _first(m, 5, 1, 1), _first(m, 5, 1, 2)
            at = secs + (nanos if isinstance(nanos, int) else 0) / 1e9 if isinstance(secs, int) else None
            if stype == USER_INPUT:
                text = strip_orbit_line(_str(_first(m, 19, 2)))
                if not text.strip():
                    continue
                settle()
                cur = HistoryCard(text=text, time=at)
                cards.append(cur)
                texts, open_steps, last_status = [], {}, status
                continue
            if cur is None:
                continue
            last_status = status
            cur.finished = at or cur.finished
            if stype == AGENT_RESPONSE:
                resp = pb(_first(m, 20))
                tx = _str((resp.get(1) or [b""])[0]).strip()
                if tx:
                    texts.append(tx)
                for call in resp.get(7) or []:
                    c = pb(call)
                    for t in texts:
                        cur.steps.append({"id": "", "kind": "note", "name": "", "summary": t[:300], "text": t.strip(),
                                          "done": True, "error": False})
                    texts = []
                    s = {"id": _str((c.get(1) or [b""])[0]), "kind": "tool", "name": _str((c.get(2) or [b""])[0]) or "tool",
                         "summary": tool_summary(_str((c.get(3) or [b""])[0])), "done": True, "error": False}
                    cur.steps.append(s)
                    open_steps[s["id"]] = s
            else:
                s = open_steps.pop(_str(_first(m, 5, 4, 1)), None)
                if s:
                    s["error"] = status == STATUS_ERROR
        settle()
        return cards

    def history(self) -> list[HistoryCard]:
        own = self.store()
        cards = self._cards(own) if own else []
        if self.fork_from:  # a clone shows the source's cards from before its own first request
            src = self.store(self.fork_from)
            if src:
                first = cards[0].time if cards and cards[0].time else None
                cards = [c for c in self._cards(src) if first is None or (c.time or 0) <= first] + cards
        return cards
