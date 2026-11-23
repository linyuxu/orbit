"""Codex driver: one long-lived `codex app-server --listen stdio://` per session (JSON-RPC over stdio, the server the
Codex desktop app runs), on the shared engine (process.py). `codex exec --json`, one process per request, is the
fallback.

**App-server** (the default). At start the driver sends `initialize` (then `initialized`) and opens the session's
thread: `thread/resume` for the thread linked to the session (base.Driver.link: threads the exec mode created too:
the same store), `thread/fork` of the source's thread for a clone, else `thread/start` (the new thread is linked at
once). A request is `turn/start` (with the effort, if set); a message sent mid-turn is `turn/steer` (the running
turn's id as `expectedTurnId`); Stop is `turn/interrupt`. A request sent before the thread is open waits for it.
Notifications map to the same events as exec: `item/started|completed` (agentMessage → text, commandExecution /
fileChange / mcpToolCall / webSearch / … → tool steps), `item/agentMessage/delta` → delta, reasoning summaries →
thinking, `thread/tokenUsage/updated` → usage (the last call's input), `turn/plan/updated` → activity,
`turn/completed` → the result (completed · interrupted = cancelled · failed). The thread's model → info.

Subagents are reported by the server itself, on the same connection: a `subAgentActivity` item (kind started, with the
subagent's thread and path) in the main thread, then the subagent thread's own `turn/*` and `item/*` notifications,
and a `subAgentActivity` completed (or interrupted) when it is done, even after the main turn ended. Each one is an
Agent step that runs in the background from the start (like Claude Code's background agents); its steps and words go
under it and its last words are its report (`agent_done`). The process outlives the turn, so the subagent does too;
if the process ends first, they are marked lost.

Approvals: Orbit has no approval UI for Codex, so a server request is answered at once by mode (never left waiting):
acceptEdits, plan and bypassPermissions run with `approvalPolicy: never` (as exec did), so they are rarely asked;
default keeps the user's own config, which may ask. Commands (and network) are accepted only in bypassPermissions,
file changes in acceptEdits and bypassPermissions, extra permissions are granted only in bypassPermissions;
everything else is declined (a refused step fails, as in exec). Questions for the user get no answers, MCP
elicitations are declined, client-side tool calls fail.

**Exec** (`ORBIT_CODEX_EXEC=1`, or when `codex app-server` fails at once: exits before it answers `initialize`, or
does not know `thread/start`): `codex exec --json [resume <thread> | fork <thread>] -` per request; the first reports
its thread (`thread.started`). Stop is SIGINT. Exec reports no subagents, so `SubagentWatch` follows them in the
rollouts; they stop when the request's process exits, so one still at work then is marked lost.

Modes: default runs with the user's own Codex config; acceptEdits = workspace-write sandbox; plan = read-only;
bypassPermissions = no sandbox and no approvals (exec: `--dangerously-bypass-approvals-and-sandbox`).

The conversation lives in Codex's own store (`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*-<thread>.jsonl`, subagents
in their own rollouts); history is rebuilt from those files, whichever mode wrote them.
"""

from __future__ import annotations

import glob
import json
import os
import shlex
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from orbit.drivers.base import ORBIT_PREFIX, DriverEvent, HistoryCard, strip_orbit_line, tool_summary
from orbit.drivers.codex_store import find_rollout, sessions_root
from orbit.drivers.process import ProcessDriver


MODE_FLAGS = {  # exec
    "default": [],
    "acceptEdits": ["-s", "workspace-write"],
    "plan": ["-s", "read-only"],
    "bypassPermissions": ["--dangerously-bypass-approvals-and-sandbox"],
}
APP_MODES = {  # app-server: thread/start · resume · fork settings (default: none, the user's own config)
    "default": {},
    "acceptEdits": {"sandbox": "workspace-write", "approvalPolicy": "never"},
    "plan": {"sandbox": "read-only", "approvalPolicy": "never"},
    "bypassPermissions": {"sandbox": "danger-full-access", "approvalPolicy": "never"},
}
# Notifications Orbit does not use and would only parse (command output can be large).
QUIET = ["item/commandExecution/outputDelta", "item/fileChange/outputDelta", "command/exec/outputDelta",
         "process/outputDelta", "item/reasoning/textDelta", "account/rateLimits/updated", "hook/started",
         "hook/completed", "mcpServer/startupStatus/updated", "remoteControl/status/changed"]
APP_HINT = (" Subagents you spawn keep running after your turn ends, and Orbit shows the user each one's report when it "
            "arrives: wait for one (wait_agent) only when your answer needs its result.")
EXEC_HINT = (" Subagents you spawn stop when your turn ends (Orbit runs Codex one process per request): "
             "wait for them (wait_agent) before you give your answer.")
# User-role text Codex writes itself (environment and instruction blocks): not requests.
NO_REQUEST = "(Its request is not in Codex's record of this conversation.)"
_NOT_A_REQUEST = ("<environment_context", "<user_instructions", "<turn_aborted", "# AGENTS.md", "<permissions")


def _ts(s: Any) -> float | None:
    if isinstance(s, (int, float)):
        return float(s)
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


SPAWN_TOOLS = ("spawn_agent", "spawn", "spawnAgent")  # a collab call that starts a subagent (its own step instead)
COLLAB = {"wait": "wait for them", "send_input": "message one", "sendInput": "message one", "sendMessage": "message one",
          "close_agent": "close one", "closeAgent": "close one", "resumeAgent": "resume one", "followupTask": "give one more",
          "interruptAgent": "interrupt one", "listAgents": "list them"}


def _step(item: dict) -> tuple[str, str] | None:
    """(name, summary) of an item that is a tool step: `exec --json` items, app-server items and rollout items alike."""
    t = item.get("type", "")
    if t in ("command_execution", "CommandExecution", "commandExecution"):
        cmd = item.get("command")
        if isinstance(cmd, list):  # rollout: ["/bin/zsh", "-lc", "echo hi"]
            cmd = cmd[-1] if cmd else ""
        elif isinstance(cmd, str) and " -lc " in cmd:  # exec --json, app-server: "/bin/zsh -lc 'echo hi'"
            inner = cmd.split(" -lc ", 1)[1]
            try:
                cmd = shlex.split(inner)[0]
            except ValueError:
                cmd = inner
        return "Bash", tool_summary({"command": cmd or ""})
    if t in ("file_change", "FileChange", "fileChange"):
        ch = item.get("changes")
        paths = [c.get("path", "") for c in ch if isinstance(c, dict)] if isinstance(ch, list) else list(ch or {})
        return "Edit", tool_summary({"path": ", ".join(p for p in paths if p)})
    if t in ("mcp_tool_call", "McpToolCall", "mcpToolCall"):
        name = f"{item.get('server', 'mcp')}.{item.get('tool', 'tool')}"
        return name, tool_summary(item.get("arguments") or {})
    if t in ("dynamicToolCall",):
        return str(item.get("tool") or "tool"), tool_summary(item.get("arguments") or {})
    if t in ("web_search", "WebSearch", "webSearch"):
        act = item.get("action") if isinstance(item.get("action"), dict) else {}
        return "WebSearch", tool_summary({"query": item.get("query") or act.get("query") or act.get("url") or ""})
    if t == "imageView":
        return "ViewImage", tool_summary({"path": item.get("path") or ""})
    if t == "Extension":
        return item.get("kind") or "tool", tool_summary({"query": item.get("query") or ""})
    if t in ("collab_tool_call", "CollabAgentToolCall", "collabAgentToolCall") and item.get("tool") not in SPAWN_TOOLS:
        tool = str(item.get("tool") or "")
        return "Subagents", COLLAB.get(tool, tool)
    return None


def _sub_history(path: str) -> tuple[list[dict], str]:
    """A subagent's steps (and what it said) and its last words, from its rollout."""
    sub: list[dict] = []
    last = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                p = d.get("payload") or {}
                if d.get("type") != "event_msg" or p.get("type") != "item_completed":
                    continue
                item = p.get("item") or {}
                if item.get("type") == "AgentMessage":
                    tx = _texts(item.get("content")).strip()
                    if tx:
                        last = tx
                        sub.append({"id": "", "kind": "note", "name": "", "summary": tx[:300], "text": tx, "done": True, "error": False})
                    continue
                step = _step(item)
                if step:
                    sub.append({"id": str(item.get("id") or ""), "kind": "tool", "name": step[0], "summary": step[1],
                                "done": True, "error": _failed(item)})
    except OSError:
        pass
    return sub, last


def _failed(item: dict) -> bool:
    if str(item.get("status", "")).lower() in ("failed", "declined", "error", "interrupted"):
        return True
    code = item.get("exit_code", item.get("exitCode"))
    return isinstance(code, int) and code != 0


def _texts(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("text"))
    return ""


SUB_LOST_EXEC = ("It stopped when its request ended: Orbit runs Codex as one process per request (`codex exec`), and "
                 "a subagent still at work when the main agent finishes its turn ends with that process.")
SUB_LOST_APP = "It ended when its Codex process did (stopped or restarted), before it reported."
SUB_LOST = "It ended before it reported: its Codex process stopped first."  # history: no report in the rollouts


class SubagentWatch:
    """Codex's subagents, live. `codex exec --json` does not report them (no spawn, no ids), but Codex writes them as
    they happen: the main thread's rollout has `SubAgentActivity` items (started / completed, with the subagent's
    thread) and each subagent has its own rollout with its steps and final answer. This follows those files during a
    request and turns them into the same events as Claude Code's subagents (an Agent step, its steps, its report)."""

    POLL = 0.3

    def __init__(self, drv: "CodexDriver"):
        self.drv = drv
        self.stop_ev = threading.Event()
        self.main_off: int | None = None  # where this request starts in the main rollout
        self.main_path: str | None = None
        self.subs: dict[str, dict] = {}  # subagent thread -> {id, name, path, off, steps, done}
        path = drv.rollout()
        if path:
            self.main_path, self.main_off = path, os.path.getsize(path) if os.path.exists(path) else 0
        self.thread = threading.Thread(target=self._run, name="codex subagents", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        while not self.stop_ev.wait(self.POLL):
            self.poll()

    def stop(self) -> None:
        """The request ended: read what is left, then end the subagents still at work (they end with the process)."""
        self.stop_ev.set()
        if self.thread is not threading.current_thread():
            self.thread.join(2)
        self.poll()
        for sub in self.subs.values():
            if not sub["done"]:
                sub["done"] = True
                self.drv.emit(DriverEvent("agent_done", {"id": sub["id"], "status": "ended", "lost": True,
                                                         "result": SUB_LOST_EXEC}))

    # ------------------------------------------------------------------ reading
    @staticmethod
    def _lines(path: str, off: int) -> tuple[list[dict], int]:
        try:
            with open(path, "rb") as f:
                f.seek(off)
                data = f.read()
        except OSError:
            return [], off
        end = data.rfind(b"\n") + 1  # whole lines only: the last may be half written
        out = []
        for line in data[:end].splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict):
                out.append(d)
        return out, off + end

    def poll(self) -> None:
        if self.main_path is None:
            self.main_path = self.drv.rollout()
            if self.main_path is None:
                return
            self.main_off = 0  # a new thread: its rollout is all this request
        lines, self.main_off = self._lines(self.main_path, self.main_off or 0)
        for d in lines:
            p = d.get("payload") or {}
            item = p.get("item") or {}
            if d.get("type") == "event_msg" and p.get("type") == "item_completed" and item.get("type") == "SubAgentActivity":
                self._activity(item)
        for th, sub in self.subs.items():
            if sub["done"] and sub.get("read_all"):
                continue
            if not sub["path"]:
                sub["path"] = self._find(th)
                if not sub["path"]:
                    continue
            lines, sub["off"] = self._lines(sub["path"], sub["off"])
            for d in lines:
                self._sub_line(sub, d)
            if sub["done"]:
                sub["read_all"] = True

    def _find(self, thread: str) -> str | None:
        dirs = {os.path.dirname(self.main_path or "")} | {os.path.join(sessions_root(), time.strftime("%Y/%m/%d"))}
        for d in dirs:
            hits = glob.glob(os.path.join(glob.escape(d), f"rollout-*{thread}.jsonl"))
            if hits:
                return hits[0]
        return None

    def _activity(self, item: dict) -> None:
        th, kind = str(item.get("agent_thread_id") or ""), item.get("kind")
        if not th:
            return
        if kind == "started" and th not in self.subs:
            name = str(item.get("agent_path") or "subagent").rstrip("/").rsplit("/", 1)[-1]
            sid = str(item.get("id") or th)
            self.subs[th] = {"id": sid, "name": name, "path": None, "off": 0, "steps": set(), "done": False, "last": ""}
            model = " · ".join(x for x in (item.get("model"), item.get("reasoning_effort")) if x)
            self.drv.emit(DriverEvent("tool_start", {"id": sid, "name": "spawn_agent", "summary": model, "agent": name,
                                                     "sub": [], "result": ""}))
            self.drv.emit(DriverEvent("tool_end", {"id": sid, "error": False, "background": True}))  # it runs on
        elif kind and kind != "started" and th in self.subs and not self.subs[th]["done"]:
            sub = self.subs[th]
            if sub["path"]:  # its final answer: the rest of its rollout
                lines, sub["off"] = self._lines(sub["path"], sub["off"])
                for d in lines:
                    self._sub_line(sub, d)
            sub["done"] = True
            self.drv.emit(DriverEvent("agent_done", {"id": sub["id"], "status": "completed" if kind == "completed" else str(kind),
                                                     "result": sub["last"]}))

    def _sub_line(self, sub: dict, d: dict) -> None:
        if d.get("type") != "event_msg":
            return
        p = d.get("payload") or {}
        pt, item = p.get("type"), p.get("item") or {}
        if pt not in ("item_started", "item_completed"):
            return
        it, iid = item.get("type"), str(item.get("id") or "")
        if it == "AgentMessage":
            tx = _texts(item.get("content")).strip()
            if pt == "item_completed" and tx:
                sub["last"] = tx
                self.drv.emit(DriverEvent("sub_text", {"parent": sub["id"], "text": tx}))
            return
        step = _step(item)
        if not step:
            return
        if iid not in sub["steps"]:
            sub["steps"].add(iid)
            self.drv.emit(DriverEvent("sub_tool_start", {"parent": sub["id"], "id": iid, "kind": "tool", "name": step[0],
                                                         "summary": step[1], "done": False, "error": False}))
        if pt == "item_completed":
            self.drv.emit(DriverEvent("sub_tool_end", {"parent": sub["id"], "id": iid, "error": _failed(item)}))


class CodexDriver(ProcessDriver):
    harness = "codex"
    label = "Codex"
    efforts = ("low", "medium", "high", "xhigh", "max")
    steers = True  # app-server: turn/steer (exec cannot: the instance says so)
    agent_hint = APP_HINT  # (exec: EXEC_HINT instead, put in by send)
    reattachable = True  # app-server: a restart hands its process off (exec, a process per request, is not: can_hand_off)

    _no_app: set[str] = set()  # programs whose app-server failed at once: their later sessions go straight to exec

    @classmethod
    def external_sessions(cls, limit, skip):
        from orbit.drivers import external
        return external.codex_sessions(limit, skip=skip)

    @classmethod
    def external_files(cls):
        from orbit.drivers import external
        return external.codex_files()

    @classmethod
    def external_row(cls, path):
        from orbit.drivers import external
        return external.codex_row(path)

    @classmethod
    def external_extra(cls):
        from orbit.drivers import external
        names = external.codex_names(external.codex_home())
        return {k: {"name": v[:200]} for k, v in names.items()}

    @classmethod
    def usage(cls):
        from orbit.services.usage import codex_usage
        return codex_usage()

    @classmethod
    def skill_dirs(cls, folder):
        return [(Path.home() / ".codex" / "prompts", "commands", "user")]

    def __init__(self, **kw):
        super().__init__(**kw)
        self.started_items: set[str] = set()
        self._rollouts: dict[str, str] = {}  # thread id -> rollout file
        self._watch: SubagentWatch | None = None  # exec: the subagents in the rollouts
        exec_ = os.environ.get("ORBIT_CODEX_EXEC", "").strip() not in ("", "0") or " ".join(self.program()) in self._no_app
        self._set_exec(exec_)
        # app-server, per process
        self._rpc_id = 0
        self._calls: dict[int, str] = {}  # request id -> method, until its response
        self._initialized = False
        self._thread: str | None = None  # the session's thread, once open
        self._model = ""  # the thread's model, as the server reports it
        self._turn: str | None = None  # the running turn
        self._queued: str | None = None  # a request sent before the thread was open
        self._stop_pending = False  # Stop came before the turn had an id
        self._subs: dict[str, dict] = {}  # subagent thread -> {id, name, done, last, steps, root}

    def _set_exec(self, on: bool) -> None:
        self.exec = on
        self.per_request = on  # `codex exec` runs one request and exits
        self.steers = not on

    @classmethod
    def models(cls) -> list[dict[str, str]]:
        home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        try:
            data = json.loads((home / "models_cache.json").read_text())
        except (OSError, ValueError):
            return []
        out = []
        for m in data.get("models") or []:
            if isinstance(m, dict) and m.get("slug") and m.get("visibility", "list") == "list":
                out.append({"value": m["slug"], "label": m.get("display_name") or m["slug"]})
        return out

    # ------------------------------------------------------------------ process
    def program(self) -> list[str]:
        return shlex.split(os.environ.get("ORBIT_CODEX_BIN", "codex"))

    def terminal_command(self) -> list[str] | None:
        own = self.linked()
        return self.program() + ["resume", own] if own else None

    def command(self) -> list[str]:
        if not self.exec:
            return self.program() + ["app-server", "--listen", "stdio://"]
        cmd = self.program() + ["exec", "--json", "--skip-git-repo-check", "-C", self.cwd]
        if self.model:
            cmd += ["-m", self.model]
        if self.effort:
            cmd += ["-c", f"model_reasoning_effort={json.dumps(self.effort)}"]
        cmd += MODE_FLAGS.get(self.mode, [])
        own = self.linked()
        src = self.linked(self.fork_from) if self.fork_from else ""
        if own:
            cmd += ["resume", own, "-"]
        elif src:
            cmd += ["fork", src, "-"]
        else:
            cmd += ["-"]
        return cmd

    def send(self, text: str) -> None:
        if self.exec:
            text = self._exec_text(text)
        super().send(text)

    def _exec_text(self, text: str) -> str:
        """Exec: the [orbit] line says subagents end with the request (the class's hint says they run on)."""
        if text.startswith(ORBIT_PREFIX) and APP_HINT:
            head, sep, rest = text.partition("\n")
            return head.replace(APP_HINT, EXEC_HINT, 1) + sep + rest
        return text

    def begin(self) -> None:
        self.started_items = set()
        self._stop_pending = False
        if self.exec:
            self._watch = SubagentWatch(self)

    def finish(self, ok: bool, text: str | None = None, **kw) -> bool:
        w, self._watch = self._watch, None
        if w:  # (before the result: the Desk settles the card on it)
            w.stop()
        return super().finish(ok, text, **kw)

    def keep_err(self, line: str) -> bool:
        return not line.startswith("Reading additional input")

    # ------------------------------------------------------------------ app-server: requests
    def _rpc(self, method: str, params: dict) -> str:
        """A JSON-RPC request line (its response comes to _response)."""
        with self.lock:
            self._rpc_id += 1
            self._calls[self._rpc_id] = method
            return json.dumps({"id": self._rpc_id, "method": method, "params": params})

    def _send_line(self, line: str) -> None:
        p = self.proc
        if p is None:
            return
        try:
            self._write(p, line)
        except RuntimeError:
            pass  # its reader reports how the process ended

    def spawned(self, proc) -> None:
        if self.exec:
            return
        self._calls, self._initialized, self._thread, self._turn = {}, False, None, None
        self._subs = {}
        caps = {"experimentalApi": False, "requestAttestation": False, "optOutNotificationMethods": QUIET}
        try:
            self._write(proc, self._rpc("initialize", {"clientInfo": {"name": "orbit", "title": "Orbit", "version": "1"},
                                                       "capabilities": caps}))
        except RuntimeError:
            pass

    def _settings(self) -> dict:
        s: dict[str, Any] = {"cwd": self.cwd, **APP_MODES.get(self.mode, {})}
        if self.model:
            s["model"] = self.model
        return s

    def _open_thread(self) -> str:
        """The request that opens the session's thread: resume its own, fork the source's (a clone), or start one."""
        own = self.linked()
        if own and self.rollout():  # (a linked thread that never ran has no rollout: a new one)
            return self._rpc("thread/resume", {"threadId": own, **self._settings()})
        src = self.linked(self.fork_from) if self.fork_from else ""
        if src and self.rollout(self.fork_from):
            return self._rpc("thread/fork", {"threadId": src, **self._settings()})
        return self._rpc("thread/start", self._settings())

    def _turn_line(self, text: str) -> str:
        params: dict[str, Any] = {"threadId": self._thread, "input": [{"type": "text", "text": text, "text_elements": []}]}
        if self.effort:
            params["effort"] = self.effort
        return self._rpc("turn/start", params)

    def request_line(self, text: str) -> str:
        with self.lock:
            if self._thread is None:  # still opening: sent when it is open
                self._queued = text
                return ""
            return self._turn_line(text)

    def steer_line(self, text: str) -> str:
        if self.exec:
            return super().steer_line(text)
        with self.lock:
            if not self._thread or not self._turn:
                raise RuntimeError("the request is still starting")
            return self._rpc("turn/steer", {"threadId": self._thread, "expectedTurnId": self._turn,
                                            "input": [{"type": "text", "text": text, "text_elements": []}]})

    def interrupt_line(self) -> str | None:
        if self.exec:
            return None  # SIGINT
        with self.lock:
            if self._thread and self._turn:
                return self._rpc("turn/interrupt", {"threadId": self._thread, "turnId": self._turn})
            self._stop_pending = True  # interrupted as soon as it has an id (or dropped, if not sent yet)
            return ""

    # ------------------------------------------------------------------ a restart of the server (hand-off)
    def can_hand_off(self) -> bool:
        """App-server only, and only between RPCs that matter: the handshake done and the thread open, no request
        waiting for it, no Stop waiting for a turn id, and a running request's turn/start answered. (Calls still in
        flight, a turn/steer, are fine: they are carried.)"""
        with self.lock:
            return (not self.exec and super().can_hand_off() and self._initialized and bool(self._thread)
                    and self._queued is None and not self._stop_pending and (not self.busy or bool(self._turn)))

    def checkpoint(self) -> dict[str, Any]:
        with self.lock:
            tops = {id(v): th for th, v in self._subs.items() if v.get("root") is None}
            subs: dict[str, dict] = {}
            for th, v in self._subs.items():
                if v.get("root") is None:
                    subs[th] = {"id": v["id"], "name": v.get("name", ""), "done": bool(v["done"]),
                                "last": v.get("last", ""), "steps": sorted(v.get("steps") or ()), "root": None}
                elif id(v["root"]) in tops:  # a nested one: its top one's thread (linked back by restore)
                    subs[th] = {"id": v["id"], "done": bool(v["done"]), "root": tops[id(v["root"])]}
            return {**super().checkpoint(), "exec": False, "rpc_id": self._rpc_id,
                    "calls": {str(k): m for k, m in self._calls.items()}, "thread": self._thread, "model": self._model,
                    "turn": self._turn, "started_items": sorted(self.started_items), "rollouts": dict(self._rollouts),
                    "subs": subs}

    def restore(self, cp: dict[str, Any]) -> None:
        if cp.get("exec") is not False:
            raise ValueError("only a codex app-server process is handed off")
        super().restore(cp)
        self._set_exec(False)  # (whatever ORBIT_CODEX_EXEC says now: this process is an app-server)
        self._initialized = True
        self._rpc_id = int(cp.get("rpc_id") or 0)
        self._calls = {int(k): str(m) for k, m in (cp.get("calls") or {}).items()}
        self._thread = str(cp["thread"]) if cp.get("thread") else None
        self._model = str(cp.get("model") or "")
        self._turn = str(cp["turn"]) if cp.get("turn") else None
        self.started_items = {str(x) for x in cp.get("started_items") or []}
        self._rollouts = {str(k): str(v) for k, v in (cp.get("rollouts") or {}).items()}
        self._queued, self._stop_pending = None, False
        raw = cp.get("subs") or {}
        subs: dict[str, dict] = {}
        for th, v in raw.items():
            if v.get("root") is None:
                subs[th] = {"id": str(v["id"]), "name": str(v.get("name") or ""), "done": bool(v.get("done")),
                            "last": str(v.get("last") or ""), "steps": {str(x) for x in v.get("steps") or []}, "root": None}
        for th, v in raw.items():  # nested ones: linked to their top one's dict (its steps are theirs too)
            top = subs.get(str(v.get("root") or ""))
            if v.get("root") is not None and top is not None:
                subs[th] = {"id": top["id"], "root": top, "done": bool(v.get("done"))}
        self._subs = subs

    # ------------------------------------------------------------------ app-server: fallback to exec
    def _exited(self, proc, code: int) -> None:
        with self.lock:  # (after _spawn and spawned: they hold it)
            at_once = not self.exec and proc is self.proc and not self._initialized and not self.closing
            gone = [s for s in self._subs.values() if not s["done"] and s.get("root") is None] if proc is self.proc else []
            for s in gone:
                s["done"] = True
        for s in gone:  # its subagents ended with it
            self.emit(DriverEvent("agent_done", {"id": s["id"], "status": "ended", "lost": True, "result": SUB_LOST_APP}))
        if at_once:
            with self.lock:
                self.proc = None
            self._fallback(proc, f"it exited ({code}) before it answered: {self.stderr_text(2)}")
            return
        super()._exited(proc, code)

    def _fallback(self, proc, why: str) -> None:
        """`codex app-server` is not usable here: this session (and later ones with this program) use `codex exec`;
        a request waiting for the thread goes to exec."""
        with self.lock:
            self._set_exec(True)
            CodexDriver._no_app.add(" ".join(self.program()))
            self.stderr_tail.append(f"orbit: codex app-server is not usable ({why.strip()}): using codex exec")
            text, self._queued = self._queued, None
            busy = self.busy
            self.req_proc = None
            if proc.poll() is None:
                self.doomed = proc  # (its exit is expected)
            reader = self.reader
        if proc.poll() is None:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except OSError:
                pass
        if busy and text is not None:
            self.begin()
            threading.Thread(target=self._launch, args=(proc, reader, self._exec_text(text)), daemon=True).start()

    # ------------------------------------------------------------------ stream
    def handle(self, msg: dict) -> None:
        if not self.exec:
            if "id" in msg and "method" in msg:
                self._answer(msg)
            elif "id" in msg:
                self._response(msg)
            elif isinstance(msg.get("method"), str):
                self._notification(msg["method"], msg.get("params") or {})
            return
        t = msg.get("type")
        if t == "thread.started" and msg.get("thread_id"):
            self.link(msg["thread_id"])
        elif t in ("item.started", "item.updated", "item.completed"):
            item = msg.get("item") or {}
            iid, it = str(item.get("id", "")), item.get("type")
            if it == "agent_message":
                if t == "item.completed" and str(item.get("text", "")).strip():
                    self.said(item["text"].strip())
                return
            if it == "todo_list" and item.get("items"):
                todo = next((x for x in item["items"] if not x.get("completed")), None)
                if todo:
                    self.emit(DriverEvent("activity", {"detail": str(todo.get("text", ""))[:200]}))
                return
            if it == "error":
                self.stderr_tail.append(str(item.get("message", "")))
                return
            step = _step(item)
            if not step:
                return
            if iid not in self.started_items:
                self.started_items.add(iid)
                self.tool_started({"id": iid, "name": step[0], "summary": step[1]})
            if t == "item.completed":
                self.emit(DriverEvent("tool_end", {"id": iid, "error": _failed(item)}))
        elif t == "turn.completed":
            self._usage(msg.get("usage") or {})
            self.finish(True)
        elif t == "turn.failed":
            self.finish(False, error=((msg.get("error") or {}).get("message")) or "the turn failed")
        elif t == "error" and msg.get("message"):
            self.stderr_tail.append(str(msg["message"]))

    # ------------------------------------------------------------------ app-server: the stream
    def _response(self, msg: dict) -> None:
        with self.lock:
            method = self._calls.pop(msg["id"], "") if isinstance(msg.get("id"), int) else ""
        res, err = msg.get("result"), msg.get("error")
        why = str((err or {}).get("message") or err) if err is not None else ""
        if method == "initialize":
            if err is not None:
                if self.proc:
                    self._fallback(self.proc, f"initialize: {why}")
                return
            self._initialized = True
            self._send_line(json.dumps({"method": "initialized"}))
            self._send_line(self._open_thread())
        elif method in ("thread/start", "thread/resume", "thread/fork"):
            if err is not None:
                if (err or {}).get("code") == -32601 and self.proc:  # an older app-server: no thread/start
                    self._fallback(self.proc, f"{method}: {why}")
                    return
                self._thread_failed(f"Codex could not open the conversation ({method}): {why}")
                return
            th = (res or {}).get("thread") or {}
            self._model = str((res or {}).get("model") or th.get("model") or "")
            if (res or {}).get("thread", {}).get("path"):
                self._rollouts[str(th.get("id"))] = th["path"]
            self.link(str(th.get("id") or ""))
            self.emit(DriverEvent("info", {"model": self._model}))
            cancel = False
            with self.lock:
                self._thread = str(th.get("id") or "")
                text, self._queued = self._queued, None
                if text is not None and self.busy:
                    if self._stop_pending:
                        cancel = True
                    else:
                        self._send_line(self._turn_line(text))
            if cancel:
                self.finish(False, "", cancelled=True)
        elif method == "turn/start":
            if err is not None:
                self.finish(False, error=f"Codex refused the request: {why}")
                return
            self._turn_began(str(((res or {}).get("turn") or {}).get("id") or ""))
        elif method in ("turn/steer", "turn/interrupt") and err is not None:
            self.stderr_tail.append(f"orbit: {method}: {why}")

    def _thread_failed(self, error: str) -> None:
        """The thread could not be opened: the request fails, the session is not ready (the next request starts a new
        driver), and this process goes."""
        with self.lock:
            self.failed = True
            self._queued = None
            p = self.proc
            if p is not None:
                self.doomed = p
        self.stderr_tail.append(error)
        try:
            if p is not None and p.stdin:
                p.stdin.close()
        except OSError:
            pass
        self.finish(False, error=error)
        self.emit(DriverEvent("state", {"state": "not ready", "error": error}))

    def _turn_began(self, turn: str) -> None:
        if not turn:
            return
        with self.lock:
            if not self.busy or self._turn == turn:
                return
            self._turn = turn
            stop, self._stop_pending = self._stop_pending, False
            if stop:
                self._send_line(self._rpc("turn/interrupt", {"threadId": self._thread, "turnId": turn}))

    def _answer(self, msg: dict) -> None:
        """A server request (an approval, a question): answered at once, by mode (see the module's docstring)."""
        method, p = msg.get("method"), msg.get("params") or {}
        mode = self.mode
        bypass, edits = mode == "bypassPermissions", mode in ("acceptEdits", "bypassPermissions")
        result: dict[str, Any] | None
        if method == "item/commandExecution/requestApproval":
            result = {"decision": "accept" if bypass else "decline"}
        elif method == "item/fileChange/requestApproval":
            result = {"decision": "accept" if edits else "decline"}
        elif method == "execCommandApproval":  # (the older protocol's)
            result = {"decision": "approved" if bypass else {"denied": {"rejection": "Orbit runs this session without approvals"}}}
        elif method == "applyPatchApproval":
            result = {"decision": "approved" if edits else {"denied": {"rejection": "Orbit runs this session without approvals"}}}
        elif method == "item/permissions/requestApproval":
            want = p.get("permissions") if isinstance(p.get("permissions"), dict) else {}
            granted = {k: want[k] for k in ("network", "fileSystem") if k in want} if bypass else {}
            result = {"permissions": granted, "scope": "turn"}
        elif method == "item/tool/requestUserInput":
            result = {"answers": {}}
        elif method == "mcpServer/elicitation/request":
            result = {"action": "decline", "content": None, "_meta": None}
        elif method == "item/tool/call":
            result = {"contentItems": [], "success": False}
        else:
            result = None
        if result is not None:
            line = json.dumps({"id": msg["id"], "result": result})
        else:
            line = json.dumps({"id": msg["id"], "error": {"code": -32601, "message": f"Orbit does not handle {method}"}})
        self._send_line(line)

    def _notification(self, m: str, p: dict) -> None:
        th = str(p.get("threadId") or "")
        if th and th != self._thread:
            if th in self._subs:
                self._sub_event(self._subs[th], th, m, p)
            return
        item = p.get("item") if isinstance(p.get("item"), dict) else {}
        if m in ("item/started", "item/completed"):
            self._main_item(m == "item/completed", item)
        elif m == "item/agentMessage/delta" and p.get("delta"):
            self.emit(DriverEvent("delta", {"text": str(p["delta"])}))
        elif m == "item/reasoning/summaryTextDelta" and p.get("delta"):
            self.emit(DriverEvent("thinking", {"text": str(p["delta"])}))
        elif m == "turn/started":
            self._turn_began(str((p.get("turn") or {}).get("id") or ""))
        elif m == "turn/completed":
            turn = p.get("turn") or {}
            with self.lock:
                mine = self._turn in (None, turn.get("id"))
                if mine:
                    self._turn = None
            if not mine:
                return
            status = turn.get("status")
            if status == "completed":
                self.finish(True)
            elif status == "interrupted":
                self.finish(False, cancelled=True)
            else:
                err = turn.get("error") or {}
                self.finish(False, error=str(err.get("message") or self.stderr_text(2) or f"the turn {status or 'failed'}"))
        elif m == "thread/tokenUsage/updated":
            last = ((p.get("tokenUsage") or {}).get("last") or {})
            ctx = int(last.get("inputTokens") or 0)
            if ctx:
                self.emit(DriverEvent("usage", {"context": ctx, "model": self._model or self.model}))
        elif m == "turn/plan/updated":
            step = next((x for x in p.get("plan") or [] if isinstance(x, dict) and x.get("status") != "completed"), None)
            if step:
                self.emit(DriverEvent("activity", {"detail": str(step.get("step") or "")[:200]}))
        elif m == "model/rerouted" and p.get("toModel"):
            self._model = str(p["toModel"])
        elif m == "error":
            e = p.get("error") or {}
            if e.get("message") and not p.get("willRetry"):
                self.stderr_tail.append(str(e["message"]))

    def _main_item(self, done: bool, item: dict) -> None:
        it, iid = item.get("type"), str(item.get("id") or "")
        if it == "agentMessage":
            if done and str(item.get("text") or "").strip():
                self.said(str(item["text"]).strip())
            return
        if it == "reasoning":
            if not done:
                self.emit(DriverEvent("thinking", {"text": "", "start": True}))
            return
        if it == "contextCompaction":
            if done:
                self.emit(DriverEvent("compaction", {}))
            return
        if it == "subAgentActivity":
            if done:
                self._sub_activity(item, None)
            return
        if it == "collabAgentToolCall" and item.get("tool") in SPAWN_TOOLS:
            if done:  # (an older multi-agent mode reports the spawn, not the activity)
                for th in item.get("receiverThreadIds") or []:
                    self._sub_started(str(th), iid + ":" + str(th), "subagent", item.get("model"),
                                      item.get("reasoningEffort"), None)
            return
        step = _step(item)
        if not step:
            return
        if iid not in self.started_items:
            self.started_items.add(iid)
            self.tool_started({"id": iid, "name": step[0], "summary": step[1]})
        if done:
            self.emit(DriverEvent("tool_end", {"id": iid, "error": _failed(item)}))

    # ------------------------------------------------------------------ app-server: subagents
    def _sub_started(self, th: str, sid: str, name: str, model: Any, effort: Any, root: dict | None) -> None:
        if not th or th in self._subs:
            return
        if root is not None:  # a subagent's own subagent: its work goes under the first one's step
            self._subs[th] = {"id": root["id"], "root": root, "done": False}
            return
        self._subs[th] = {"id": sid, "name": name, "done": False, "last": "", "steps": set(), "root": None}
        summary = " · ".join(str(x) for x in (model, effort) if x)
        self.emit(DriverEvent("tool_start", {"id": sid, "name": "spawn_agent", "summary": summary, "agent": name,
                                             "sub": [], "result": ""}))
        self.emit(DriverEvent("tool_end", {"id": sid, "error": False, "background": True}))  # it runs on

    def _sub_activity(self, item: dict, parent: dict | None) -> None:
        th, kind = str(item.get("agentThreadId") or ""), item.get("kind")
        if kind == "started":
            name = str(item.get("agentPath") or "subagent").rstrip("/").rsplit("/", 1)[-1] or "subagent"
            self._sub_started(th, str(item.get("id") or th), name, item.get("model"), item.get("reasoningEffort"), parent)
        elif kind in ("completed", "interrupted") and th in self._subs and parent is None:
            self._sub_done(self._subs[th], str(kind))

    def _sub_done(self, sub: dict, status: str) -> None:
        if sub["done"] or sub.get("root") is not None:
            return
        sub["done"] = True
        self.emit(DriverEvent("agent_done", {"id": sub["id"], "status": status, "result": sub["last"]}))

    def _sub_event(self, sub: dict, th: str, m: str, p: dict) -> None:
        top = sub.get("root") or sub
        item = p.get("item") if isinstance(p.get("item"), dict) else {}
        if m == "turn/completed" and sub.get("root") is None:
            status = str((p.get("turn") or {}).get("status") or "completed")
            self._sub_done(sub, status)
            return
        if m not in ("item/started", "item/completed"):
            return
        done, it, iid = m == "item/completed", item.get("type"), str(item.get("id") or "")
        if it == "agentMessage":
            tx = str(item.get("text") or "").strip()
            if done and tx:
                if sub.get("root") is None:
                    top["last"] = tx
                self.emit(DriverEvent("sub_text", {"parent": top["id"], "text": tx}))
            return
        if it == "subAgentActivity":
            if done:
                self._sub_activity(item, top)
            return
        step = _step(item)
        if not step:
            return
        if iid not in top["steps"]:
            top["steps"].add(iid)
            self.emit(DriverEvent("sub_tool_start", {"parent": top["id"], "id": iid, "kind": "tool", "name": step[0],
                                                     "summary": step[1], "done": False, "error": False}))
        if done:
            self.emit(DriverEvent("sub_tool_end", {"parent": top["id"], "id": iid, "error": _failed(item)}))

    def _usage(self, turn_usage: dict) -> None:
        """Context = what the last model call read: the rollout's last token_count, else the turn's input."""
        ctx, model = 0, ""
        path = self.rollout()
        if path:
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()[-200:]
            except OSError:
                lines = []
            for line in reversed(lines):
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                p = d.get("payload") or {}
                if not ctx and p.get("type") == "token_count" and p.get("info"):
                    last = p["info"].get("last_token_usage") or {}
                    ctx = int(last.get("input_tokens") or 0)
                if not model and d.get("type") == "turn_context":
                    model = p.get("model") or ""
                if ctx and model:
                    break
        ctx = ctx or int(turn_usage.get("input_tokens") or 0)
        if ctx:
            self.emit(DriverEvent("usage", {"context": ctx, "model": model or self.model}))

    # ------------------------------------------------------------------ history
    def rollout(self, sid: str | None = None) -> str | None:
        thread = self.linked(sid)
        if not thread:
            return None
        known = self._rollouts.get(thread)
        if known and os.path.exists(known):
            return known
        path = find_rollout(thread)
        if path:
            self._rollouts[thread] = path
        return path

    def _cards(self, path: str) -> tuple[list[HistoryCard], float | None]:
        """Cards of one rollout, and when the thread began. A forked rollout's copied history carries no item
        events, so only the thread's own requests become cards. A user message inside a turn that already has its
        request (between task_started and its end) was sent mid-turn: a steer on that card."""
        cards: list[HistoryCard] = []
        cur: HistoryCard | None = None
        texts: list[str] = []
        began: float | None = None
        steps: dict[str, dict] = {}
        in_task = asked = fresh = False  # inside a turn; its request seen; the user's input reached the model in it

        def settle():
            if cur is not None:
                cur.answer = "\n\n".join(texts)

        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                p = d.get("payload")
                if not isinstance(p, dict):
                    continue
                at = _ts(d.get("timestamp"))
                if d.get("type") == "session_meta":
                    began = began or _ts(p.get("timestamp")) or at
                    continue
                if d.get("type") == "response_item" and p.get("type") == "message" and p.get("role") == "user" and in_task:
                    fresh = True  # the user's input reached the model in this turn: a new request, recorded or not
                if d.get("type") != "event_msg":
                    continue
                pt = p.get("type")
                if pt == "item_completed":
                    item = p.get("item") or {}
                    it = item.get("type")
                    if (cur is None or (fresh and cur.text == NO_REQUEST)) and in_task and not asked \
                            and it not in ("UserMessage", "Reasoning", "FunctionCallOutput"):
                        # A turn with work but no request on record (Codex Desktop's VS Code extension keeps the request
                        # only in the model's input): its card still shows what was done and said.
                        asked = True
                        settle()
                        cur = HistoryCard(text=NO_REQUEST, time=at)
                        cards.append(cur)
                        texts, steps = [], {}
                    if it == "UserMessage":
                        text = strip_orbit_line(_texts(item.get("content")))
                        if not text.strip() or text.lstrip().startswith(_NOT_A_REQUEST):
                            continue
                        if in_task and asked and cur is not None:
                            cur.steers.append({"text": text, "time": at})
                            continue
                        asked = True
                        settle()
                        cur = HistoryCard(text=text, time=at)
                        cards.append(cur)
                        texts, steps = [], {}
                    elif cur is None:
                        continue
                    elif it == "AgentMessage":
                        tx = _texts(item.get("content")).strip()
                        if tx:
                            texts.append(tx)
                        cur.finished = at or cur.finished
                    elif it == "ContextCompaction":
                        cur.compactions += 1
                    elif it == "SubAgentActivity":  # a subagent: an Agent step, its steps and report from its own rollout
                        th = str(item.get("agent_thread_id") or "")
                        if item.get("kind") == "started" and th:
                            name = str(item.get("agent_path") or "subagent").rstrip("/").rsplit("/", 1)[-1]
                            found = glob.glob(os.path.join(glob.escape(os.path.dirname(path)), f"rollout-*{th}.jsonl"))
                            sub, last = _sub_history(found[0]) if found else ([], "")
                            cur.steps.append({"id": str(item.get("id") or th), "kind": "tool", "name": "spawn_agent",
                                              "summary": " · ".join(x for x in (item.get("model"), item.get("reasoning_effort")) if x),
                                              "agent": name, "sub": sub, "result": last, "thread": th, "done": True, "error": False})
                            cur.finished = at or cur.finished
                        elif th and item.get("kind") in ("completed", "interrupted"):  # maybe after its turn, or in a later one
                            for st in (s for c in cards for s in c.steps):
                                if st.get("thread") == th:
                                    st["finished"] = True
                                    st["error"] = item.get("kind") == "interrupted"
                    else:
                        step = _step(item)
                        if not step:
                            continue
                        for tx in texts:
                            cur.steps.append({"id": "", "kind": "note", "name": "", "summary": tx[:300], "text": tx.strip(),
                                              "done": True, "error": False})
                        texts = []
                        cur.steps.append({"id": str(item.get("id", "")), "kind": "tool", "name": step[0],
                                          "summary": step[1], "done": True, "error": _failed(item)})
                        cur.finished = at or cur.finished
                elif pt == "task_started":
                    in_task, asked, fresh = True, False, False
                elif pt in ("task_complete", "turn_aborted"):
                    in_task = False
                    if cur is not None and pt == "task_complete":
                        cur.finished = at or cur.finished
                    elif cur is not None:
                        cur.state, cur.finished = "cancelled", at or cur.finished
        settle()
        for c in cards:  # a subagent that never reported ended with its request
            for st in c.steps:
                if st.get("thread") and not st.get("finished"):
                    st["error"], st["lost"] = True, True
                    st["result"] = SUB_LOST
        return cards, began

    def history(self) -> list[HistoryCard]:
        own = self.rollout()
        cards, began = self._cards(own) if own else ([], None)
        if self.fork_from:  # a clone shows the source's cards from before it forked
            src = self.rollout(self.fork_from)
            if src:
                before, _ = self._cards(src)
                cards = [c for c in before if began is None or (c.time or 0) <= began] + cards
        return cards
