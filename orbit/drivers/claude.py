"""Claude Code driver: one long-lived `claude -p --input-format stream-json --output-format stream-json` per session
(on the shared engine, process.py: this file is the command line, the stream's events and the history).

The conversation lives in Claude Code's own store (`~/.claude/projects/<cwd>/<session id>.jsonl`); Orbit only keeps
the session id and re-reads that file for history. `--session-id` names a new conversation, `--resume` continues one.
"""

from __future__ import annotations

import glob
import json
import re
import os
import shlex
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from orbit import ROOT
from orbit.drivers.base import DriverEvent, HistoryCard
from orbit.drivers.process import ProcessDriver


# Prefixes of user-role text that Claude Code writes itself (slash commands, background notices): not requests.
_NOT_A_REQUEST = ("<local-command", "<command-", "Caveat:", "<task-notification", "<system-reminder")


def tool_summary(name: str, inp: dict[str, Any]) -> str:
    """One short line naming what a tool call works on."""
    for key in ("command", "file_path", "path", "pattern", "url", "query", "description", "prompt", "notebook_path"):
        v = inp.get(key)
        if isinstance(v, str) and v.strip():
            line = v.strip().splitlines()[0]
            home = str(Path.home())
            if line.startswith(home):
                line = "~" + line[len(home):]
            return line[:200]
    return ""


AGENT_TOOLS = ("Agent", "Task")  # the tool that starts a subagent (its later name, and the earlier one)


def _step(b: dict) -> dict:
    """A tool call as a card step; a subagent's call also carries its type and, filled as it works, its steps."""
    name, inp = b.get("name", "tool"), b.get("input") or {}
    st = {"id": b.get("id", ""), "kind": "tool", "name": name, "summary": tool_summary(name, inp), "done": False, "error": False}
    if name in AGENT_TOOLS:
        st.update(agent=str(inp.get("subagent_type") or "general-purpose"), sub=[], result="")
    return st


ASYNC_LAUNCH = "Async agent launched"  # an Agent call run in the background answers this at once


def _launched(tool_result: dict, extra: Any = None) -> bool:
    """A background Agent call's immediate answer (its report comes later, in a task notification)."""
    if isinstance(extra, dict) and extra.get("status") == "async_launched":
        return True
    return _result_text(tool_result.get("content")).lstrip().startswith(ASYNC_LAUNCH)


NOTE_RE = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)


def _notifications(obj: Any) -> list[dict]:
    """Task notifications anywhere in a message: {id (the Agent call), status, result}."""
    out: list[dict] = []
    def walk(v: Any) -> None:
        if isinstance(v, str):
            if "<task-notification>" in v:
                for body in NOTE_RE.findall(v):
                    tag = lambda k: (re.search(rf"<{k}>(.*?)</{k}>", body, re.S) or [None, ""])[1].strip()
                    if tag("tool-use-id"):
                        out.append({"id": tag("tool-use-id"), "status": tag("status"), "result": tag("result")})
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(obj)
    return out


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _sub_steps(path: Path) -> list[dict]:
    """A subagent's own transcript (<session>/subagents/agent-*.jsonl) as steps: what it said, the tools it ran."""
    steps: list[dict] = []
    open_: dict[str, dict] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return steps
    for line in lines:
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = (d.get("message") or {}).get("content")
        if d.get("type") == "assistant" and isinstance(content, list):
            for b in content:
                if b.get("type") == "text" and b.get("text", "").strip():
                    steps.append({"id": "", "kind": "note", "name": "", "summary": b["text"][:300], "text": b["text"].strip(), "done": True, "error": False})
                elif b.get("type") == "tool_use":
                    st = _step(b); steps.append(st); open_[st["id"]] = st
        elif d.get("type") == "user" and isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in open_:
                    st = open_.pop(b["tool_use_id"]); st["done"], st["error"] = True, bool(b.get("is_error"))
    for st in steps:
        st["done"] = True
    return steps


def _ts(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _user_text(content: Any) -> str | None:
    """The request text of a user-role message, or None if it is a tool result or a harness notice."""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        if not parts and any(isinstance(b, dict) and b.get("type") == "image" for b in content):
            parts = ["[image]"]
        text = "\n".join(parts)
    else:
        return None
    if text.startswith("[orbit] "):  # the context line Orbit adds for the agent; the user did not write it
        text = text.split("\n", 1)[1].lstrip("\n") if "\n" in text else ""
    if not text.strip() or text.lstrip().startswith(_NOT_A_REQUEST):
        return None
    return text



PLUGIN = ROOT / "plugin"  # <repo>/plugin: .claude-plugin/plugin.json + skills/

class ClaudeDriver(ProcessDriver):
    harness = "claude"
    label = "Claude Code"

    efforts = ("low", "medium", "high", "xhigh", "max")
    steers = True  # a user message written mid-turn joins that turn (stream-json input): one result at the end
    identity = "orbit-id"  # the conversation is the Orbit session's id (--session-id)
    reattachable = True  # a restart of the server hands its process off (the Harness Server keeps it running)
    # auto: Claude Code's own classifier approves what is safe (--permission-mode auto); default passes no flag, so
    # the user's settings decide (their permissions.defaultMode)
    modes = ("default", "acceptEdits", "auto", "plan", "bypassPermissions")

    @classmethod
    def external_sessions(cls, limit, skip):
        from orbit.drivers import external
        return external.claude_sessions(limit, skip=skip)

    @classmethod
    def external_files(cls):
        from orbit.drivers import external
        return external.claude_files()

    @classmethod
    def external_row(cls, path):
        from orbit.drivers import external
        return external.claude_row(path)

    @classmethod
    def external_extra(cls):
        from orbit.drivers import external
        live = external.claude_live()
        return {k: {"live": v} for k, v in live.items()}

    @classmethod
    def usage(cls):
        from orbit.services.usage import claude_usage
        return claude_usage()

    @classmethod
    def skill_dirs(cls, folder):
        home = Path.home()
        return [(folder / ".claude" / "skills", "skills", "folder"), (folder / ".claude" / "commands", "commands", "folder"),
                (home / ".claude" / "skills", "skills", "user"), (home / ".claude" / "commands", "commands", "user")]

    # The models by full name; an alias (opus …) resolves to the newest, which Orbit learns from each start-up
    # (the init event names the model) and shows in the ⌘N dialog.
    KNOWN = [("claude-opus-5-5", "Claude Opus 5.5"), ("claude-sonnet-5-5", "Claude Sonnet 5.5"),
             ("claude-haiku-5-5", "Claude Haiku 5.5"), ("claude-fable-5-2", "Claude Fable 5.2")]

    @classmethod
    def models(cls) -> list[dict[str, str]]:
        return [{"value": v, "label": f"{label} · {v}"} for v, label in cls.KNOWN]

    def __init__(self, **kw):
        super().__init__(**kw)
        self.agent_calls: set[str] = set()  # this session's Agent tool calls (their results are subagents' reports)

    # ------------------------------------------------------------------ process
    def program(self) -> list[str]:
        return shlex.split(os.environ.get("ORBIT_CLAUDE_BIN", "claude"))

    def command(self) -> list[str]:
        cmd = self.program() + ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
        if self.model:
            cmd += ["--model", self.model]
        if self.mode and self.mode != "default":
            cmd += ["--permission-mode", self.mode]
        if self.effort:
            cmd += ["--effort", self.effort]
        cmd += ["--include-partial-messages"]  # text and thinking as they are written (the card streams them)
        # Orbit's own skills (orbit:orbit-agents …) as a plugin, unless `orbit tools install` already put the skill in
        # ~/.claude/skills (then every Claude Code has it, and loading it twice would list it twice).
        if PLUGIN.is_dir() and not (Path.home() / ".claude" / "skills" / "orbit-agents").exists():
            cmd += ["--plugin-dir", str(PLUGIN)]
        if self.resume and self.transcript():
            cmd += ["--resume", self.session_id]
        elif self.fork_from and self.transcript(self.fork_from):
            cmd += ["--resume", self.fork_from, "--fork-session", "--session-id", self.session_id]
        else:
            cmd += ["--session-id", self.session_id]
        return cmd

    def spawned(self, proc) -> None:
        self.resume = True  # later processes continue this conversation

    def checkpoint(self) -> dict[str, Any]:
        return {**super().checkpoint(), "agent_calls": sorted(self.agent_calls)}

    def restore(self, cp: dict[str, Any]) -> None:
        super().restore(cp)
        self.agent_calls = {str(x) for x in cp.get("agent_calls") or []}

    def terminal_command(self) -> list[str] | None:
        return self.program() + ["--resume", self.session_id] if self.transcript() else None

    @staticmethod
    def _user(text: str) -> str:
        return json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}})

    def request_line(self, text: str) -> str:
        return self._user(text)

    def steer_line(self, text: str) -> str:
        """Mid-turn: the message joins the running turn (Claude Code folds it in at the next step)."""
        return self._user(text)

    def interrupt_line(self) -> str:
        return json.dumps({"type": "control_request", "request_id": uuid.uuid4().hex, "request": {"subtype": "interrupt"}})

    # ------------------------------------------------------------------ stream
    def handle(self, msg: dict) -> None:
        t, st = msg.get("type"), msg.get("subtype")
        if t == "system" and st == "task_notification":  # a background subagent finished: its status and report
            if msg.get("tool_use_id") in self.agent_calls:
                self.emit(DriverEvent("agent_done", {"id": msg["tool_use_id"], "status": str(msg.get("status") or ""),
                                                     "result": str(msg.get("summary") or "")}))
            return
        if self.agent_calls and t != "assistant":  # the same as a <task-notification> text (older versions)
            for n in _notifications(msg):
                if n["id"] in self.agent_calls:
                    self.emit(DriverEvent("agent_done", n))
        if t == "stream_event":  # --include-partial-messages: deltas of the block being written
            if msg.get("parent_tool_use_id"):
                return  # a subagent's inner turn
            ev = msg.get("event") or {}
            if ev.get("type") == "content_block_delta":
                dl = ev.get("delta") or {}
                if dl.get("type") == "text_delta" and dl.get("text"):
                    self.emit(DriverEvent("delta", {"text": dl["text"]}))
                elif dl.get("type") == "thinking_delta" and dl.get("thinking"):
                    self.emit(DriverEvent("thinking", {"text": dl["thinking"]}))
            elif ev.get("type") == "content_block_start" and (ev.get("content_block") or {}).get("type") == "thinking":
                self.emit(DriverEvent("thinking", {"text": "", "start": True}))
            return
        if t == "assistant":
            m = msg.get("message") or {}
            parent = msg.get("parent_tool_use_id")
            if parent:  # a subagent's turn: what it says and runs goes under its Agent step
                for b in m.get("content") or []:
                    if b.get("type") == "text" and b.get("text", "").strip():
                        self.emit(DriverEvent("sub_text", {"parent": parent, "text": b["text"]}))
                    elif b.get("type") == "tool_use":
                        self.emit(DriverEvent("sub_tool_start", {"parent": parent, **_step(b)}))
                return
            for b in m.get("content") or []:
                bt = b.get("type")
                if bt == "text" and b.get("text", "").strip():
                    self.said(b["text"])
                elif bt == "tool_use":
                    st = _step(b)
                    if "agent" in st:
                        self.agent_calls.add(st["id"])
                    self.tool_started(st)
            u = m.get("usage") or {}
            ctx = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                     "cache_creation_input_tokens"))
            if ctx:
                self.emit(DriverEvent("usage", {"context": ctx, "model": m.get("model", "")}))
        elif t == "user":
            content = (msg.get("message") or {}).get("content")
            parent = msg.get("parent_tool_use_id")
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        tid = b.get("tool_use_id", "")
                        if parent:
                            self.emit(DriverEvent("sub_tool_end", {"parent": parent, "id": tid, "error": bool(b.get("is_error"))}))
                            continue
                        ev = {"id": tid, "error": bool(b.get("is_error"))}
                        if tid in self.agent_calls and _launched(b, msg.get("tool_use_result")):
                            ev["background"] = True  # it runs on; its report comes in a task notification
                        elif tid in self.agent_calls:  # a subagent's report
                            ev["result"] = _result_text(b.get("content"))[:20000]
                        self.emit(DriverEvent("tool_end", ev))
        elif t == "system" and st == "compact_boundary":
            self.emit(DriverEvent("compaction", {}))
        elif t == "system" and st == "init":  # the model in use (an alias resolved) and the slash commands it takes
            term = set(msg.get("terminal_slash_commands") or [])
            cmds = [c for c in (msg.get("slash_commands") or []) if isinstance(c, str) and c not in term
                    and not c.startswith("__")]
            self.emit(DriverEvent("info", {"model": str(msg.get("model") or ""), "commands": cmds,
                                           "skills": [x for x in (msg.get("skills") or []) if isinstance(x, str)]}))
        elif t == "system" and st == "task_summary" and msg.get("detail"):
            self.emit(DriverEvent("activity", {"detail": str(msg["detail"])[:200]}))
        elif t == "result":
            text = msg.get("result")
            if not isinstance(text, str) or not text:
                text = self.answer_so_far()
            cancelled = st == "error_during_execution" or msg.get("stop_reason") in ("interrupted", "cancelled")
            ok = not msg.get("is_error") and st == "success"
            self.finish(ok, text, cancelled=cancelled, error=text or st or "error",
                        paused=st == "error_max_turns")  # out of turns: Continue gives it more

    # ------------------------------------------------------------------ history
    def transcript(self, sid: str | None = None) -> str | None:
        root = os.environ.get("ORBIT_CLAUDE_PROJECTS", str(Path.home() / ".claude" / "projects"))
        hits = glob.glob(os.path.join(glob.escape(root), "*", f"{sid or self.session_id}.jsonl"))
        return max(hits, key=os.path.getmtime) if hits else None

    def history(self) -> list[HistoryCard]:
        # A clone that has not run yet has no transcript of its own: show the conversation it forks.
        path = self.transcript() or (self.transcript(self.fork_from) if self.fork_from else None)
        if not path:
            return []
        cards: list[HistoryCard] = []
        cur: HistoryCard | None = None
        texts: list[str] = []  # text since the last tool call, the candidate answer
        open_steps: dict[str, dict] = {}

        def settle():
            if cur is not None:
                cur.answer = "\n\n".join(texts)

        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = d.get("type")
                if d.get("isSidechain"):
                    continue
                if t == "user":
                    content = (d.get("message") or {}).get("content")
                    if d.get("isMeta") or d.get("isCompactSummary"):
                        continue
                    text = _user_text(content)
                    if text is not None:
                        settle()
                        cur = HistoryCard(text=text, time=_ts(d.get("timestamp")))
                        cards.append(cur)
                        texts, open_steps = [], {}
                    elif cur is not None and isinstance(content, list):
                        for b in content:
                            if isinstance(b, dict) and b.get("type") == "tool_result":
                                s = open_steps.pop(b.get("tool_use_id", ""), None)
                                if s:
                                    s["done"], s["error"] = True, bool(b.get("is_error"))
                                    if "agent" in s and _launched(b, d.get("toolUseResult")):
                                        s["background"] = True
                                    elif "agent" in s:  # a subagent's report
                                        s["result"] = _result_text(b.get("content"))[:20000]
                        cur.finished = _ts(d.get("timestamp")) or cur.finished
                elif t == "assistant" and cur is not None:
                    for b in (d.get("message") or {}).get("content") or []:
                        if b.get("type") == "text" and b.get("text", "").strip():
                            texts.append(b["text"])
                        elif b.get("type") == "tool_use":
                            for tx in texts:
                                cur.steps.append({"id": "", "kind": "note", "name": "", "summary": tx[:300], "text": tx.strip(),
                                                  "done": True, "error": False})
                            texts = []
                            s = _step(b)
                            cur.steps.append(s)
                            open_steps[s["id"]] = s
                    cur.finished = _ts(d.get("timestamp")) or cur.finished
                elif t == "system" and d.get("subtype") == "compact_boundary" and cur is not None:
                    cur.compactions += 1
                elif t == "attachment" and cur is not None:  # a message sent mid-turn (a steer) is kept as a queued command
                    a = d.get("attachment") or {}
                    if a.get("type") == "queued_command":
                        text = _user_text(a.get("prompt"))
                        if text:
                            cur.steers.append({"text": text, "time": _ts(a.get("timestamp") or d.get("timestamp"))})
        settle()
        subs = Path(path).with_suffix("") / "subagents"  # <session>.jsonl → <session>/subagents/agent-*.jsonl (+ .meta.json)
        if subs.is_dir():
            by_id = {s["id"]: s for c in cards for s in c.steps if "agent" in s}
            for meta in subs.glob("*.meta.json"):
                try:
                    m = json.loads(meta.read_text())
                except (OSError, ValueError):
                    continue
                st = by_id.get(m.get("toolUseId", ""))
                if st is not None:
                    st["sub"] = _sub_steps(meta.with_name(meta.name[:-len(".meta.json")] + ".jsonl"))
                    st["agent"] = m.get("agentType") or st["agent"]
                    if st.get("background") and not st.get("result"):  # its report: its last words
                        said = [x for x in st["sub"] if x["kind"] == "note"]
                        st["result"] = said[-1]["text"] if said else ""
        for c in cards:
            for s in c.steps:
                s["done"] = True
        return cards
