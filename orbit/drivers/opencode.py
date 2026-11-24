"""OpenCode driver: one long-lived `opencode serve` per session (its HTTP server on 127.0.0.1, on a port of its own,
with a password made for it), on the shared engine (process.py). Design: builddoc/opencode.md.

The process says its address on stdout (`opencode server listening on http://127.0.0.1:PORT`, read by plain_line);
the rest is HTTP with basic auth (OPENCODE_SERVER_PASSWORD, in its environment). The driver subscribes to `GET /event`
(SSE) first, then opens the session's OpenCode session: its own (`GET /session/:id`, linked by base.Driver.link), a
fork of the source's for a clone (`POST /session/:id/fork`), else a new one (`POST /session`). A request is
`POST /session/:id/prompt_async` (the model as provider/model; the plan agent in plan mode); a request that names one
of OpenCode's commands (`GET /command`: its commands and skills) is `POST /session/:id/command`, and `/compact` is
`POST /session/:id/summarize`. Stop is `POST /session/:id/abort` (then, as for every harness, SIGINT and kill).

Events: `message.part.delta` → delta (text) or thinking (reasoning); `message.part.updated`: a text part's end → text,
a tool part running → tool_start, completed or error → tool_end, `step-finish` → usage (the call's input, cache read
and cache write); a `task` tool is an Agent step, and its child session's parts (the session named in the tool's
metadata, or a `session.created` whose parent is ours) go under it; `session.compacted` → compaction; `todo.updated`
→ activity (the item in progress). The request ends when the session goes idle after it was busy (`session.status`
idle, `session.idle`): an error (`session.error`, the assistant message's) makes it an error, an abort a cancelled one.

Permissions: OpenCode asks (`permission.asked`) only what its config says to ask (by default little: a path outside
the project, the same call over and over). bypassPermissions answers every ask "once", acceptEdits the `edit` ones;
any other ask, and every question (`question.asked`), waits for the user in OpenCode's own TUI: the session's
terminal (⌘T) is `opencode attach` on this server and session, and the session's attention mark says what it asks
until it is answered there.

Hand-off (builddoc/harness-server.md): the server runs in the Harness Server like any agent; its address, password
and what this request has shown are in the checkpoint. The next server subscribes again and catches up from
`GET /session/:id/message` (the parts it missed, through the same handlers: each shown once), `GET /permission` and
`GET /session/status` (a request that ended in between ends now).

The conversation lives in OpenCode's store (`~/.local/share/opencode/opencode.db`, SQLite: tables session, message,
part; the data as JSON). History and the conversations to resume are read from it, read-only.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import re
import secrets
import shlex
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from orbit.drivers.base import DriverEvent, HistoryCard, strip_orbit_line, tool_summary
from orbit.drivers.process import ProcessDriver


LISTENING = re.compile(r"listening on (https?://\S+)")
CONNECT_WAIT = 20.0  # s the server has to open its event stream once it said its address
ABORTED = "MessageAbortedError"
# OpenCode's tool names, as the other harnesses' steps are named (an MCP tool, server_tool, stays as it is).
TOOL_NAMES = {"bash": "Bash", "read": "Read", "edit": "Edit", "multiedit": "Edit", "patch": "Edit", "apply_patch": "Edit",
              "write": "Write", "glob": "Glob", "grep": "Grep", "list": "LS", "webfetch": "WebFetch",
              "websearch": "WebSearch", "codesearch": "CodeSearch", "todowrite": "TodoWrite", "todoread": "TodoRead",
              "task": "Task", "skill": "Skill", "question": "Question", "lsp": "LSP", "invalid": "Invalid"}
TASK_RESULT = re.compile(r"<task_result>\s*(.*?)\s*</task_result>", re.S)
TASK_ID = re.compile(r'<task id="([^"]+)"')
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 127.0.0.1: never through a proxy


def db_path() -> str:
    if os.environ.get("ORBIT_OPENCODE_DB"):
        return os.environ["ORBIT_OPENCODE_DB"]
    data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return os.path.join(data, "opencode", "opencode.db")


def _ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)  # (never written: OpenCode's own store)


def _error_text(err: Any) -> str:
    """OpenCode's error ({name, data: {message}}) as one line."""
    if not isinstance(err, dict):
        return str(err or "error")
    data = err.get("data") if isinstance(err.get("data"), dict) else {}
    return str(data.get("message") or err.get("message") or err.get("name") or "error")


def _tool_name(name: Any) -> str:
    return TOOL_NAMES.get(str(name or ""), str(name or "tool"))


def _summary(state: dict) -> str:
    return tool_summary(state.get("input") or {}) or str(state.get("title") or "")[:200]


def _task_result(state: dict) -> str:
    """A task's report: the subagent's last words, without the <task> wrapper OpenCode puts around them."""
    if state.get("status") == "error":
        return str(state.get("error") or "")
    out = str(state.get("output") or "")
    m = TASK_RESULT.search(out)
    return m.group(1) if m else out.strip()


def _task_session(state: dict) -> str:
    meta = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    if isinstance(meta.get("sessionId"), str):
        return meta["sessionId"]
    m = TASK_ID.search(str(state.get("output") or ""))
    return m.group(1) if m else ""


class HTTPFail(RuntimeError):
    def __init__(self, code: int, text: str):
        super().__init__(f"OpenCode answered {code}: {text}" if text else f"OpenCode answered {code}")
        self.code = code


class OpenCodeDriver(ProcessDriver):
    harness = "opencode"
    label = "OpenCode"
    modes = ("default", "acceptEdits", "plan", "bypassPermissions")
    reattachable = True  # a restart of the server hands its process off (the next server subscribes again)
    _models: list[dict[str, str]] | None = None

    def __init__(self, **kw):
        super().__init__(**kw)
        self.base = ""  # the server's address, as its stdout said it
        self.password = secrets.token_urlsafe(24)  # its basic-auth password: only this driver (and ⌘T) knows it
        self.conv = ""  # the OpenCode session
        self.commands: list[str] = []
        self.model_seen = ""
        self.terminal = []
        self._open = False  # subscribed and the session open: requests go out
        self._queued: str | None = None  # a request sent before that
        self._connected = threading.Event()  # this process's event stream is open
        self._asks: dict[str, str] = {}  # permission asks and questions waiting for the user → what they ask
        self._turn_reset()

    def _turn_reset(self) -> None:
        self._sent = False  # the request was posted
        self._busy_seen = False  # the session went busy after it (its idle then ends the request)
        self._error = ""
        self._aborted = False
        self._turn_msg = ""  # the request's user message
        self._roles: dict[str, str] = {}  # message → role
        self._kinds: dict[str, str] = {}  # part → type (deltas name only the part)
        self._said: set[str] = set()  # text parts shown whole
        self._started: set[str] = set()  # tool parts shown as steps
        self._ended: set[str] = set()
        self._thinking: set[str] = set()
        self._subs: dict[str, str] = {}  # a subagent's session → the Agent step it works under
        self._task_steps: set[str] = set()  # the request's Agent steps (task tool parts)

    # ------------------------------------------------------------------ the class
    @classmethod
    def models(cls) -> list[dict[str, str]]:
        if cls._models is None:
            try:
                out = subprocess.run(shlex.split(os.environ.get("ORBIT_OPENCODE_BIN", "opencode")) + ["models"],
                                     capture_output=True, text=True, timeout=20).stdout
            except (OSError, subprocess.TimeoutExpired):
                return []
            models = [{"value": x, "label": x} for x in (l.strip() for l in out.splitlines())
                      if re.fullmatch(r"[\w.@-]+/\S+", x)]
            if models:
                cls._models = models
            return models
        return cls._models

    @classmethod
    def external_sessions(cls, limit, skip):
        return conversations(limit, skip)

    @classmethod
    def skill_dirs(cls, folder):
        # OpenCode reads its own skills and commands (either spelling of the folder) and Claude Code's skills.
        home, cfg = Path.home(), Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "opencode"
        out = []
        for base, source in ((folder / ".opencode", "folder"), (cfg, "user")):
            out += [(base / d, kind, source) for d, kind in (("skills", "skills"), ("skill", "skills"),
                                                               ("commands", "commands"), ("command", "commands"))]
        return out + [(folder / ".claude" / "skills", "skills", "folder"), (home / ".claude" / "skills", "skills", "user")]

    # ------------------------------------------------------------------ process
    def program(self) -> list[str]:
        return shlex.split(os.environ.get("ORBIT_OPENCODE_BIN", "opencode"))

    def command(self) -> list[str]:
        return self.program() + ["serve", "--hostname", "127.0.0.1", "--port", "0"]

    def env(self) -> dict[str, str]:
        env = {**super().env(), "OPENCODE_SERVER_PASSWORD": self.password}
        env.pop("OPENCODE_SERVER_USERNAME", None)  # (the default user, "opencode")
        return env

    @property
    def terminal_env(self) -> dict[str, str]:
        """⌘T's environment: `opencode attach` reads the password from it (in argv, ps would show it)."""
        return {"OPENCODE_SERVER_PASSWORD": self.password}

    def terminal_command(self) -> list[str] | None:
        own = self.conv or self.linked()
        return self.program() + ["--session", own] if own else None

    def spawned(self, proc) -> None:
        self.base, self._open, self._connected = "", False, threading.Event()

    def plain_line(self, line: str) -> None:
        m = LISTENING.search(line)
        if m and not self.base:
            self.base = m.group(1).rstrip("/")
            threading.Thread(target=self._connect, args=(self.proc, self._connected), daemon=True,
                             name="opencode open").start()
            return
        super().plain_line(line)

    def begin(self) -> None:
        self._turn_reset()

    def close(self) -> None:
        """End the server. It does not end with its input (the engine's way): SIGINT at once, then TERM, KILL."""
        self.closing = True
        p = self.proc
        if p is None or p.poll() is not None:
            self.emit(DriverEvent("state", {"state": "stopped"}))
            return
        if self.busy:
            self.stopping = True
        self._sigint(p)
        try:
            p.wait(3)
        except subprocess.TimeoutExpired:
            p.terminate()
            try:
                p.wait(3)
            except subprocess.TimeoutExpired:
                p.kill()

    # ------------------------------------------------------------------ HTTP
    def _headers(self) -> dict[str, str]:
        auth = base64.b64encode(f"opencode:{self.password}".encode()).decode()
        return {"Authorization": f"Basic {auth}", "Content-Type": "application/json", "Accept": "application/json"}

    def _call(self, method: str, path: str, body: Any = None, timeout: float | None = 30) -> Any:
        if not self.base:
            raise RuntimeError("OpenCode's server is not up")
        req = urllib.request.Request(self.base + path, method=method, headers=self._headers(),
                                     data=None if body is None else json.dumps(body).encode())
        try:
            with _OPENER.open(req, timeout=timeout) as r:
                data = r.read()
        except urllib.error.HTTPError as e:
            text = ""
            try:
                text = e.read().decode("utf-8", "replace")
                d = json.loads(text)
                text = _error_text(d.get("error") if isinstance(d, dict) and "error" in d else d)
            except (OSError, ValueError, AttributeError):
                pass
            raise HTTPFail(e.code, str(text)[:300]) from None
        except (OSError, http.client.HTTPException) as e:
            raise RuntimeError(f"OpenCode's server: {e}") from None
        return json.loads(data) if data.strip() else None

    def _later(self, fn, *args) -> None:
        """An HTTP call off the caller's thread (the engine's hooks run under its lock)."""
        threading.Thread(target=fn, args=args, daemon=True).start()

    # ------------------------------------------------------------------ opening
    def _connect(self, proc, connected: threading.Event) -> None:
        """The server is up: subscribe to its events, open the session, send a request that waited."""
        threading.Thread(target=self._listen, args=(proc, connected, False), daemon=True, name="opencode events").start()
        try:
            if not connected.wait(CONNECT_WAIT):
                raise RuntimeError("its event stream did not open")
            self._open_session()
            cmds = self._call("GET", "/command") or []
            self.commands = sorted({str(c.get("name")) for c in cmds if isinstance(c, dict) and c.get("name")} | {"compact"})
        except (RuntimeError, OSError, ValueError, KeyError, TypeError) as e:
            if proc is self.proc:
                self.stderr_tail.append(f"OpenCode: {e}")
                self.finish(False, error=f"OpenCode: {e}")  # (a request that waited for it)
                try:
                    proc.terminate()  # its reader reports the error
                except OSError:
                    pass
            return
        self.terminal = self.program() + ["attach", self.base, "--session", self.conv]  # (its password: terminal_env)
        self.emit(DriverEvent("info", {"model": self.model or self.model_seen, "commands": self.commands,
                                       "terminal": True}))
        with self.lock:
            if proc is not self.proc:
                return
            self._open = True
            text, self._queued = self._queued, None
        if text is not None:
            self._send_request(text)

    def _open_session(self) -> None:
        info = None
        own = self.conv or self.linked()
        if own:
            try:
                info = self._call("GET", f"/session/{own}")
            except HTTPFail as e:
                if e.code not in (400, 404):
                    raise
        src = self.linked(self.fork_from) if self.fork_from and not info else ""
        if src:
            try:
                info = self._call("POST", f"/session/{src}/fork", {})
            except HTTPFail as e:
                if e.code not in (400, 404):
                    raise
        if not info:
            info = self._call("POST", "/session", {})
        self.conv = str(info["id"])
        self.link(self.conv)
        m = info.get("model") if isinstance(info.get("model"), dict) else {}
        if m.get("id") and m.get("providerID"):
            self.model_seen = f"{m['providerID']}/{m['id']}"

    # ------------------------------------------------------------------ requests
    def request_line(self, text: str) -> str:
        with self.lock:
            if not self._open:  # sent once the session is open
                self._queued = text
                return ""
        self._later(self._send_request, text)
        return ""

    def _request(self, text: str) -> tuple[str, dict]:
        model = self.model or ""
        if model and "/" not in model:
            raise RuntimeError(f"OpenCode names a model provider/model, not {model!r}")
        pm = dict(zip(("providerID", "modelID"), model.split("/", 1))) if model else None
        bare = strip_orbit_line(text).strip()
        m = re.fullmatch(r"/([\w:.-]+)(?:\s+(.*))?", bare, re.S)
        if m and m.group(1) == "compact":
            seen = model or self.model_seen
            if "/" in seen:
                p, mid = seen.split("/", 1)
                return f"/session/{self.conv}/summarize", {"providerID": p, "modelID": mid}
        elif m and m.group(1) in self.commands:
            body: dict[str, Any] = {"command": m.group(1), "arguments": m.group(2) or ""}
            if model:
                body["model"] = model
            if self.mode == "plan":
                body["agent"] = "plan"
            return f"/session/{self.conv}/command", body
        body = {"parts": [{"type": "text", "text": text}]}
        if pm:
            body["model"] = pm
        if self.mode == "plan":
            body["agent"] = "plan"
        return f"/session/{self.conv}/prompt_async", body

    def _send_request(self, text: str) -> None:
        try:
            path, body = self._request(text)
        except RuntimeError as e:
            self.finish(False, error=str(e))
            return
        self._sent = True
        sync = not path.endswith("/prompt_async")  # (a command and summarize answer when they are done)
        if sync:
            with self.lock:
                self.acked = True
        try:
            self._call("POST", path, body, timeout=None if sync else 30)
        except (RuntimeError, OSError) as e:
            self.finish(False, error=str(e))
            return
        with self.lock:  # (after the engine's own acked = False: it holds the lock while it writes)
            self.acked = True

    def interrupt_line(self) -> str:
        with self.lock:
            if self._queued is not None:  # never sent: dropped
                self._queued = None
                self._later(lambda: self.finish(False, cancelled=True))
                return ""
        if self.conv and self._open:
            self._later(self._abort)
        return ""

    def _abort(self) -> None:
        try:
            self._call("POST", f"/session/{self.conv}/abort")
        except (RuntimeError, OSError):
            pass  # (the engine sends SIGINT if nothing ends the request)

    # ------------------------------------------------------------------ the event stream
    def _gone(self, proc) -> bool:
        return proc is not self.proc or proc.poll() is not None or getattr(proc, "detached", False) or self.closing

    def _listen(self, proc, connected: threading.Event, catch_up: bool) -> None:
        """The events, for as long as this process is this driver's; if the stream drops, subscribed again and
        caught up."""
        while not self._gone(proc):
            try:
                req = urllib.request.Request(self.base + "/event",
                                             headers={**self._headers(), "Accept": "text/event-stream"})
                with _OPENER.open(req, timeout=None) as r:
                    for raw in r:
                        if self._gone(proc):
                            return
                        line = raw.decode("utf-8", "replace").strip()
                        if not line.startswith("data:"):
                            continue
                        try:
                            e = json.loads(line[5:])
                        except ValueError:
                            continue
                        if not isinstance(e, dict):
                            continue
                        try:
                            if e.get("type") == "server.connected":
                                if catch_up:
                                    self._catch_up()
                                catch_up = True  # (a later reconnect catches up)
                                connected.set()
                            else:
                                self._event(e)
                        except Exception as x:  # noqa: BLE001 - a bad event must not end the stream
                            self.stderr_tail.append(f"orbit: {type(x).__name__}: {x}")
            except (OSError, http.client.HTTPException):
                pass
            if self._gone(proc):
                return
            time.sleep(0.5)
            catch_up = True

    def _event(self, e: dict) -> None:
        t, p = str(e.get("type") or ""), e.get("properties") or {}
        if t == "session.created":
            info = p.get("info") or {}
            par, sid = info.get("parentID"), str(info.get("id") or "")
            if sid and par and (par == self.conv or par in self._subs) and sid not in self._subs:
                # a subagent's session: under the open task that started it (the tool's metadata confirms it), a
                # nested one under its top one's step
                step = self._subs.get(par) or next(reversed(sorted(self._task_steps - self._ended)), "")
                if step:
                    self._subs[sid] = step
            return
        sid = p.get("sessionID") or (p.get("part") or {}).get("sessionID") or (p.get("info") or {}).get("sessionID")
        mine = bool(sid) and sid == self.conv
        if not mine and sid not in self._subs:
            return
        if t == "message.updated":
            self._message(p.get("info") or {}, mine)
        elif t == "message.part.updated":
            self._part(p.get("part") or {}, mine)
        elif t == "message.part.delta":
            if mine:
                self._delta(p)
        elif t in ("session.status", "session.idle") and mine:
            st = p.get("status") or {}
            kind = "idle" if t == "session.idle" else st.get("type")
            if kind in ("busy", "retry"):
                if self._sent and not self._busy_seen:
                    self._busy_seen = True
                    with self.lock:
                        self.acked = True
                if kind == "retry" and self.busy:
                    self.emit(DriverEvent("activity", {"detail": f"retrying: {st.get('message') or ''}"[:160]}))
            elif kind == "idle":
                self._idle()
        elif t == "session.error" and mine:
            err = p.get("error")
            if isinstance(err, dict) and err.get("name") == ABORTED:
                self._aborted = True
            elif err:
                self._error = _error_text(err)
        elif t == "permission.asked":
            self._permission(p)
        elif t == "question.asked":
            q = next((x for x in p.get("questions") or [] if isinstance(x, dict)), {})
            self._ask(str(p.get("id") or ""), f"OpenCode asks: {q.get('question') or q.get('header') or 'a question'}")
        elif t in ("permission.replied", "question.replied", "question.rejected"):
            if self._asks.pop(str(p.get("requestID") or ""), None) is not None:
                self._attention()
        elif t == "session.compacted" and mine:
            self.emit(DriverEvent("compaction", {}))
        elif t == "todo.updated" and mine and self.busy:
            now = next((x for x in p.get("todos") or [] if isinstance(x, dict) and x.get("status") == "in_progress"), None)
            if now and now.get("content"):
                self.emit(DriverEvent("activity", {"detail": str(now["content"])[:160]}))

    def _message(self, info: dict, mine: bool) -> None:
        mid, role = str(info.get("id") or ""), info.get("role")
        if mid and role:
            self._roles[mid] = role
        if not mine:
            return
        if role == "user" and self._sent and not self._turn_msg:
            self._turn_msg = mid
        elif role == "assistant" and (not self._turn_msg or info.get("parentID") == self._turn_msg):
            if info.get("providerID") and info.get("modelID"):
                self.model_seen = f"{info['providerID']}/{info['modelID']}"
            err = info.get("error")
            if isinstance(err, dict) and err.get("name") == ABORTED:
                self._aborted = True
            elif err:
                self._error = _error_text(err)

    def _part(self, part: dict, mine: bool) -> None:
        pid, kind = str(part.get("id") or ""), part.get("type")
        if not pid or self._roles.get(str(part.get("messageID") or "")) == "user" or part.get("synthetic") or part.get("ignored"):
            return
        self._kinds[pid] = str(kind)
        if kind == "text":
            text = str(part.get("text") or "").strip()
            if not part.get("time"):  # (a request's own text has no time)
                return
            if part["time"].get("end") and pid not in self._said and text:
                self._said.add(pid)
                if mine:
                    self.said(text)
                else:
                    self.emit(DriverEvent("sub_text", {"parent": self._subs[part["sessionID"]], "text": text}))
        elif kind == "reasoning" and mine and pid not in self._thinking:
            self._thinking.add(pid)
            self.emit(DriverEvent("thinking", {"text": "", "start": True}))
        elif kind == "tool":
            self._tool(part, mine)
        elif kind == "step-finish" and mine:
            tok = part.get("tokens") or {}
            cache = tok.get("cache") or {}
            ctx = int(tok.get("input") or 0) + int(cache.get("read") or 0) + int(cache.get("write") or 0)
            if ctx:
                self.emit(DriverEvent("usage", {"context": ctx, "model": self.model_seen or self.model}))

    def _delta(self, p: dict) -> None:
        pid, d = str(p.get("partID") or ""), p.get("delta")
        if p.get("field") != "text" or not isinstance(d, str) or not d or pid in self._said:
            return
        kind = self._kinds.get(pid)
        if kind == "text":
            self.emit(DriverEvent("delta", {"text": d}))
        elif kind == "reasoning":
            if pid not in self._thinking:
                self._thinking.add(pid)
                self.emit(DriverEvent("thinking", {"text": "", "start": True}))
            self.emit(DriverEvent("thinking", {"text": d}))

    def _tool(self, part: dict, mine: bool) -> None:
        st = part.get("state") or {}
        status, name, pid = st.get("status"), str(part.get("tool") or "tool"), str(part["id"])
        if name == "task" and mine:
            child = _task_session(st)
            if child:
                self._subs[child] = pid
        if status not in ("running", "completed", "error"):
            return  # (pending: its input is not known yet)
        parent = "" if mine else self._subs.get(str(part.get("sessionID") or ""), "")
        if pid not in self._started:
            self._started.add(pid)
            step: dict[str, Any] = {"id": pid, "name": _tool_name(name), "summary": _summary(st)}
            if mine:
                if name == "task":
                    step.update(agent=str((st.get("input") or {}).get("subagent_type") or "general"), sub=[], result="")
                    self._task_steps.add(pid)
                self.tool_started(step)
            else:
                self.emit(DriverEvent("sub_tool_start", {"parent": parent, **step, "kind": "tool", "done": False,
                                                         "error": False}))
        if status in ("completed", "error") and pid not in self._ended:
            self._ended.add(pid)
            err = status == "error"
            if mine:
                d: dict[str, Any] = {"id": pid, "error": err}
                if name == "task":
                    d["result"] = _task_result(st)
                self.emit(DriverEvent("tool_end", d))
            else:
                self.emit(DriverEvent("sub_tool_end", {"parent": parent, "id": pid, "error": err}))

    def _idle(self) -> None:
        if not self.busy and self.auto:  # a turn OpenCode began itself is over (the engine ends its card)
            self.finish(not self._error, error=self._error or "")
            return
        if not (self.busy and self._sent) or not (self._busy_seen or self._error or self._aborted):
            return  # (an idle from before this request)
        if self._asks:
            self._asks.clear()
            self.emit(DriverEvent("attention", {"detail": ""}))
        if self.stopping or self._aborted:
            for pid in sorted(self._started - self._ended):  # steps the stop cut short: not done, whatever OpenCode says
                self._ended.add(pid)
                self.emit(DriverEvent("tool_end", {"id": pid, "error": True}))
            self.finish(False, cancelled=True)
        elif self._error:
            self.finish(False, error=self._error)
        else:
            self.finish(True)

    # ------------------------------------------------------------------ asks
    def _permission(self, p: dict) -> None:
        rid, perm = str(p.get("id") or ""), str(p.get("permission") or "")
        if not rid or rid in self._asks:
            return
        if self.mode == "bypassPermissions" or (self.mode == "acceptEdits" and perm == "edit"):
            self._later(self._reply, rid)
            return
        pats = ", ".join(str(x) for x in p.get("patterns") or [] if x)
        self._ask(rid, f"OpenCode asks permission: {perm}" + (f" {pats}" if pats else ""))

    def _reply(self, rid: str) -> None:
        try:
            self._call("POST", f"/permission/{rid}/reply", {"reply": "once"})
        except (RuntimeError, OSError) as e:
            self.stderr_tail.append(f"OpenCode: answering a permission: {e}")

    def _ask(self, rid: str, detail: str) -> None:
        if rid:
            self._asks[rid] = detail
            self._attention()

    def _attention(self) -> None:
        last = list(self._asks.values())[-1] if self._asks else ""
        more = f" (+{len(self._asks) - 1} more)" if len(self._asks) > 1 else ""
        self.emit(DriverEvent("attention", {"detail": (last[:180] + more) if last else ""}))

    # ------------------------------------------------------------------ a restart of the server (hand-off)
    def can_hand_off(self) -> bool:
        """Only with the session open, no request waiting for it, and a running request taken by the server."""
        with self.lock:
            return super().can_hand_off() and self._open and self._queued is None and bool(self.base)

    def checkpoint(self) -> dict[str, Any]:
        return {**super().checkpoint(), "base": self.base, "password": self.password, "conv": self.conv,
                "commands": list(self.commands), "model_seen": self.model_seen, "terminal": list(self.terminal),
                "sent": self._sent, "busy_seen": self._busy_seen, "error": self._error, "aborted": self._aborted,
                "turn_msg": self._turn_msg, "roles": dict(self._roles), "kinds": dict(self._kinds),
                "said": sorted(self._said), "started": sorted(self._started), "ended": sorted(self._ended),
                "thinking": sorted(self._thinking), "subs": dict(self._subs),
                "tasks": sorted(self._task_steps), "asks": dict(self._asks)}

    def restore(self, cp: dict[str, Any]) -> None:
        super().restore(cp)
        self.base, self.password, self.conv = str(cp.get("base") or ""), str(cp.get("password") or ""), str(cp.get("conv") or "")
        self.commands = [str(x) for x in cp.get("commands") or []]
        self.model_seen = str(cp.get("model_seen") or "")
        self.terminal = [str(x) for x in cp.get("terminal") or []]
        self._sent, self._busy_seen = bool(cp.get("sent")), bool(cp.get("busy_seen"))
        self._error, self._aborted = str(cp.get("error") or ""), bool(cp.get("aborted"))
        self._turn_msg = str(cp.get("turn_msg") or "")
        self._roles = {str(k): str(v) for k, v in (cp.get("roles") or {}).items()}
        self._kinds = {str(k): str(v) for k, v in (cp.get("kinds") or {}).items()}
        self._said, self._started = {str(x) for x in cp.get("said") or []}, {str(x) for x in cp.get("started") or []}
        self._ended, self._thinking = {str(x) for x in cp.get("ended") or []}, {str(x) for x in cp.get("thinking") or []}
        self._subs = {str(k): str(v) for k, v in (cp.get("subs") or {}).items()}
        self._task_steps = {str(x) for x in cp.get("tasks") or []}
        self._asks = {str(k): str(v) for k, v in (cp.get("asks") or {}).items()}
        self._open = bool(self.base and self.conv)

    def attach(self, proc, checkpoint: dict[str, Any], busy: bool) -> None:
        super().attach(proc, checkpoint, busy)
        self._connected = threading.Event()
        threading.Thread(target=self._listen, args=(proc, self._connected, True), daemon=True,
                         name="opencode events").start()

    def _catch_up(self) -> None:
        """What happened while nobody listened (a hand-off, a dropped stream): this request's parts and its
        subagents' (each shown once), the asks still open, and its end if it ended meanwhile."""
        if not self.conv:
            return
        if self.busy and self._sent:
            msgs = self._call("GET", f"/session/{self.conv}/message?limit=200") or []
            start = next((i for i, m in enumerate(msgs) if (m.get("info") or {}).get("id") == self._turn_msg), None)
            if start is None:  # (its message not seen yet: the turn is what follows the last request)
                start = max((i for i, m in enumerate(msgs) if (m.get("info") or {}).get("role") == "user"), default=-1)
            for m in msgs[start + 1:]:
                self._message(m.get("info") or {}, True)
                for part in m.get("parts") or []:
                    self._part(part, True)
            for child in list(self._subs):
                for m in self._call("GET", f"/session/{child}/message") or []:
                    self._message(m.get("info") or {}, False)
                    for part in m.get("parts") or []:
                        self._part(part, False)
        for ask in self._call("GET", "/permission") or []:
            if isinstance(ask, dict) and (ask.get("sessionID") == self.conv or ask.get("sessionID") in self._subs):
                self._permission(ask)
        if self.busy and self._sent:
            status = self._call("GET", "/session/status") or {}
            if (status.get(self.conv) or {}).get("type") in (None, "idle"):
                self._busy_seen = True  # it ran, and it is done
                self._idle()

    # ------------------------------------------------------------------ history
    def history(self) -> list[HistoryCard]:
        conv = self.conv or self.linked()
        return cards(db_path(), conv) if conv else []


# ---------------------------------------------------------------------- OpenCode's store (read-only)
def _rows(db: sqlite3.Connection, sid: str) -> tuple[list[tuple[str, int, dict]], dict[str, list[dict]]]:
    msgs = [(mid, t, json.loads(d)) for mid, t, d in
            db.execute("select id, time_created, data from message where session_id = ? order by time_created, id", (sid,))]
    parts: dict[str, list[dict]] = {}
    for pid, mid, d in db.execute("select id, message_id, data from part where session_id = ? order by id", (sid,)):
        parts.setdefault(mid, []).append({**json.loads(d), "id": pid})  # (the ids are columns, not in the data)
    return msgs, parts


def _note(text: str) -> dict:
    return {"id": "", "kind": "note", "name": "", "summary": text[:300], "text": text, "done": True, "error": False}


def _sub_steps(db: sqlite3.Connection, sid: str, depth: int = 0) -> list[dict]:
    """A subagent's steps and words (its own subagents' steps flattened into them)."""
    out: list[dict] = []
    if not sid or depth > 3:
        return out
    msgs, parts = _rows(db, sid)
    for mid, _t, info in msgs:
        if info.get("role") != "assistant":
            continue
        for p in parts.get(mid, []):
            if p.get("type") == "text" and str(p.get("text") or "").strip() and not p.get("synthetic"):
                out.append(_note(p["text"].strip()))
            elif p.get("type") == "tool":
                st = p.get("state") or {}
                out.append({"id": str(p.get("id") or ""), "kind": "tool", "name": _tool_name(p.get("tool")),
                            "summary": _summary(st), "done": True, "error": st.get("status") == "error"})
                if p.get("tool") == "task":
                    out += _sub_steps(db, _task_session(st), depth + 1)
    return out


def cards(path: str, sid: str) -> list[HistoryCard]:
    """A session's requests as cards, from OpenCode's store."""
    if not os.path.isfile(path):
        return []
    try:
        db = _ro(path)
        try:
            msgs, parts = _rows(db, sid)
            out: list[HistoryCard] = []
            cur: HistoryCard | None = None
            texts: list[str] = []
            for mid, created, info in msgs:
                ps = parts.get(mid, [])
                if info.get("role") == "user":
                    if any(p.get("type") == "compaction" for p in ps):
                        if cur is not None:
                            cur.compactions += 1
                        continue
                    text = strip_orbit_line("\n".join(str(p.get("text") or "") for p in ps if p.get("type") == "text"
                                                      and not p.get("synthetic") and not p.get("ignored")))
                    if not text.strip():
                        continue
                    if cur is not None:
                        cur.answer = "\n\n".join(texts)
                    cur, texts = HistoryCard(text=text, time=created / 1000), []
                    out.append(cur)
                    continue
                if cur is None or info.get("summary"):  # (a compaction's summary is not an answer)
                    continue
                t = info.get("time") or {}
                cur.finished = (t.get("completed") or created) / 1000
                err = info.get("error")
                if isinstance(err, dict):
                    cur.state = "cancelled" if err.get("name") == ABORTED else "error"
                for p in ps:
                    if p.get("type") == "text" and str(p.get("text") or "").strip() and not p.get("synthetic"):
                        texts.append(p["text"].strip())
                    elif p.get("type") == "tool":
                        cur.steps += [_note(x) for x in texts]  # text before a tool call is narration
                        texts = []
                        st = p.get("state") or {}
                        step = {"id": str(p.get("id") or ""), "kind": "tool", "name": _tool_name(p.get("tool")),
                                "summary": _summary(st), "done": True, "error": st.get("status") == "error"}
                        if p.get("tool") == "task":
                            step.update(agent=str((st.get("input") or {}).get("subagent_type") or "general"),
                                        sub=_sub_steps(db, _task_session(st)), result=_task_result(st))
                        cur.steps.append(step)
            if cur is not None:
                cur.answer = "\n\n".join(texts)
            return out
        finally:
            db.close()
    except (sqlite3.Error, ValueError):
        return []


def conversations(limit: int, skip: set[str] | None = None) -> list[dict[str, Any]]:
    """OpenCode's sessions to resume (⌘O → Other sessions), newest first: top-level ones (not a subagent's), not
    archived, with a request."""
    path = db_path()
    if not os.path.isfile(path):
        return []
    skip = skip or set()
    out: list[dict[str, Any]] = []
    try:
        db = _ro(path)
        try:
            q = "select id, directory, title, time_updated from session where parent_id is null{} order by time_updated desc limit ?"
            try:
                rows = db.execute(q.format(" and time_archived is null"), (limit + len(skip),)).fetchall()
            except sqlite3.OperationalError:  # (an older store: nothing archived)
                rows = db.execute(q.format(""), (limit + len(skip),)).fetchall()
            for sid, cwd, title, updated in rows:
                if sid in skip:
                    continue
                first = ""
                for (d,) in db.execute("select p.data from part p join message m on m.id = p.message_id where "
                                       "m.session_id = ? and json_extract(m.data, '$.role') = 'user' "
                                       "order by m.time_created, p.id limit 8", (sid,)):
                    p = json.loads(d)
                    if p.get("type") == "text" and not p.get("synthetic") and str(p.get("text") or "").strip():
                        first = strip_orbit_line(p["text"]).strip()
                        break
                if not first:
                    continue
                out.append({"harness": "opencode", "id": sid, "cwd": cwd, "first": first[:300], "name": "",
                            "title": str(title or "")[:200], "time": (updated or 0) / 1000, "live": None})
                if len(out) >= limit:
                    break
        finally:
            db.close()
    except (sqlite3.Error, ValueError):
        return out
    return out
