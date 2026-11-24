"""A stand-in for `opencode serve --hostname 127.0.0.1 --port 0` (and `opencode models`).

Speaks the subset of OpenCode's HTTP API the driver uses: basic auth (OPENCODE_SERVER_PASSWORD, user "opencode"), the
SSE stream `GET /event`, sessions (create, get, fork, messages, status, abort), `prompt_async`, `command`, `summarize`,
`GET /command`, permission and question replies. It says its address on stdout as OpenCode does, and keeps its
sessions in a SQLite store shaped like OpenCode's (tables session, message, part; ids in columns, the rest as JSON) at
$ORBIT_OPENCODE_DB. Behaviour per request text (after the [orbit] line):
  "slow [N]"   narration, then a bash step that runs N s (10) unless aborted (the request then ends as aborted)
  "split N"    an answer streamed in two deltas N s apart
  "fail"       a session error ("boom")
  "die"        the process exits in the middle of the request
  "ask" / "edit"  a permission ask (bash / edit) that waits for its reply: "answer: allowed" or "answer: refused"
  "question"   a question that waits for its reply: "answer: <the answers>"
  "task"       a task step: a child session (a subagent) that runs `make test`, says "checker: all good"
  "think"      reasoning (two deltas), then the answer
  anything else: one bash step (echo hi), then "answer: <text>"
Logs beside the store: argv.log, inputs.log (each request: session, text, model, agent, pid), http.log (each call).
"""

import base64
import json
import os
import queue
import random
import signal
import sqlite3
import string
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

args = sys.argv[1:]
if args[:1] == ["models"]:
    print("fake/one")
    print("fake/two")
    sys.exit(0)
DB = os.environ["ORBIT_OPENCODE_DB"]
LOGS = os.path.dirname(DB)
os.makedirs(LOGS, exist_ok=True)


def log(name, obj):
    with open(os.path.join(LOGS, name), "a") as f:
        f.write(json.dumps({**obj, "pid": os.getpid()}) + "\n")


log("argv.log", {"argv": args})
assert args[:1] == ["serve"], args
PASSWORD = os.environ.get("OPENCODE_SERVER_PASSWORD", "")
CWD = os.path.realpath(os.getcwd())

db = sqlite3.connect(DB, check_same_thread=False, timeout=10, isolation_level=None)
db.execute("pragma journal_mode=wal")
db.execute("create table if not exists session (id text primary key, project_id text, parent_id text, slug text, "
           "directory text, title text, version text, time_created integer, time_updated integer, time_archived integer)")
db.execute("create table if not exists message (id text primary key, session_id text, time_created integer, "
           "time_updated integer, data text)")
db.execute("create table if not exists part (id text primary key, message_id text, session_id text, "
           "time_created integer, time_updated integer, data text)")
dblock = threading.RLock()
_n = [0]


def now():
    return int(time.time() * 1000)


def new_id(prefix):
    with dblock:
        _n[0] += 1
        return f"{prefix}_{now():012x}{_n[0]:04x}" + "".join(random.choices(string.ascii_letters, k=8))


# ---------------------------------------------------------------------- events
clients: list[queue.Queue] = []
clock = threading.Lock()


def emit(kind, props):
    e = {"id": new_id("evt"), "type": kind, "properties": props}
    with clock:
        for q in clients:
            q.put(e)


# ---------------------------------------------------------------------- the store
def session_row(sid):
    r = db.execute("select id, parent_id, directory, title, time_created, time_updated from session where id = ?",
                   (sid,)).fetchone()
    if not r:
        return None
    info = {"id": r[0], "slug": "fake", "projectID": "global", "directory": r[2], "title": r[3], "version": "fake",
            "time": {"created": r[4], "updated": r[5]}}
    if r[1]:
        info["parentID"] = r[1]
    return info


def create_session(parent=None, title="New session"):
    sid, t = new_id("ses"), now()
    with dblock:
        db.execute("insert into session values (?, 'global', ?, 'fake', ?, ?, 'fake', ?, ?, null)",
                   (sid, parent, CWD, title, t, t))
    info = session_row(sid)
    emit("session.created", {"sessionID": sid, "info": info})
    return info


def put_message(sid, info):
    mid = info["id"]
    data = {k: v for k, v in info.items() if k not in ("id", "sessionID")}
    with dblock:
        db.execute("insert or replace into message values (?, ?, ?, ?, ?)",
                   (mid, sid, info["time"]["created"], now(), json.dumps(data)))
    emit("message.updated", {"sessionID": sid, "info": info})


def put_part(part):
    data = {k: v for k, v in part.items() if k not in ("id", "sessionID", "messageID")}
    with dblock:
        db.execute("insert or replace into part values (?, ?, ?, ?, ?, ?)",
                   (part["id"], part["messageID"], part["sessionID"], now(), now(), json.dumps(data)))
    emit("message.part.updated", {"sessionID": part["sessionID"], "part": part, "time": now()})


def messages(sid, limit=None):
    out = []
    for mid, d in db.execute("select id, data from message where session_id = ? order by time_created, id", (sid,)):
        parts = [{**json.loads(pd), "id": pid, "sessionID": sid, "messageID": mid} for pid, pd in
                 db.execute("select id, data from part where message_id = ? order by id", (mid,))]
        out.append({"info": {**json.loads(d), "id": mid, "sessionID": sid}, "parts": parts})
    return out[-limit:] if limit else out


# ---------------------------------------------------------------------- a request
busy: dict[str, bool] = {}
aborts: dict[str, threading.Event] = {}
pending: dict[str, dict] = {}  # permission / question id → {"ask": ..., "event": Event, "reply": ...}
MODEL = {"providerID": "fake", "modelID": "one"}


class Aborted(Exception):
    pass


class Turn:
    def __init__(self, sid, model):
        self.sid, self.model = sid, model or MODEL
        self.abort = aborts.setdefault(sid, threading.Event())
        self.user = ""

    def check(self):
        if self.abort.is_set():
            raise Aborted

    def user_message(self, parts):
        self.user = new_id("msg")
        put_message(self.sid, {"id": self.user, "sessionID": self.sid, "role": "user", "time": {"created": now()},
                               "agent": "build", "model": self.model})
        for p in parts:
            put_part({"id": new_id("prt"), "sessionID": self.sid, "messageID": self.user, **p})

    def assistant(self, sid=None, **kw):
        info = {"id": new_id("msg"), "sessionID": sid or self.sid, "role": "assistant", "time": {"created": now()},
                "parentID": self.user, "modelID": self.model["modelID"], "providerID": self.model["providerID"],
                "mode": "build", "agent": "build", "cost": 0, **kw}
        put_message(info["sessionID"], info)
        return info

    def done(self, am, error=None, finish="stop"):
        am = {**am, "time": {**am["time"], "completed": now()}, "finish": finish}
        if error:
            am["error"] = error
        put_message(am["sessionID"], am)
        put_part({"id": new_id("prt"), "sessionID": am["sessionID"], "messageID": am["id"], "type": "step-finish",
                  "reason": finish, "cost": 0, "tokens": {"input": 300, "output": 5, "reasoning": 0,
                                                          "cache": {"read": 700, "write": 0}}})

    def text(self, am, words, pause=0.0):
        p = {"id": new_id("prt"), "sessionID": am["sessionID"], "messageID": am["id"], "type": "text", "text": "",
             "time": {"start": now()}}
        put_part(p)
        half = len(words) // 2
        for i, chunk in enumerate((words[:half], words[half:])):
            if i and pause:
                time.sleep(pause)
            emit("message.part.delta", {"sessionID": am["sessionID"], "messageID": am["id"], "partID": p["id"],
                                        "field": "text", "delta": chunk})
        put_part({**p, "text": words, "time": {"start": p["time"]["start"], "end": now()}})

    def tool(self, am, name, inp, secs=0.0, output="ok\n", ask=None):
        p = {"id": new_id("prt"), "sessionID": am["sessionID"], "messageID": am["id"], "type": "tool",
             "callID": new_id("call"), "tool": name}
        put_part({**p, "state": {"status": "pending", "input": {}, "raw": ""}})
        t0 = now()
        put_part({**p, "state": {"status": "running", "input": inp, "time": {"start": t0}}})
        if ask:
            if self.wait(ask, p) != "once":
                put_part({**p, "state": {"status": "error", "input": inp, "error": "refused",
                                         "time": {"start": t0, "end": now()}}})
                return False
        end = time.time() + secs
        while time.time() < end:
            if self.abort.is_set():
                put_part({**p, "state": {"status": "error", "input": inp, "error": "Tool execution aborted",
                                         "time": {"start": t0, "end": now()}}})
                raise Aborted
            time.sleep(0.05)
        put_part({**p, "state": {"status": "completed", "input": inp, "output": output, "title": str(inp)[:40],
                                 "metadata": {}, "time": {"start": t0, "end": now()}}})
        return True

    def wait(self, ask, part):
        rid = new_id(ask["kind"][:3])
        ev = threading.Event()
        body = {"id": rid, "sessionID": part["sessionID"], **{k: v for k, v in ask.items() if k != "kind"},
                "tool": {"messageID": part["messageID"], "callID": part["callID"]}}
        pending[rid] = {"ask": body, "event": ev, "reply": None, "kind": ask["kind"]}
        emit(f"{ask['kind']}.asked", body)
        while not ev.wait(0.05):
            self.check()
        r = pending.pop(rid)
        return r["reply"]

    def run(self, text, behave=None):
        busy[self.sid] = True
        self.abort.clear()
        emit("session.status", {"sessionID": self.sid, "status": {"type": "busy"}})
        try:
            (behave or self.behave)(text)
        except Aborted:
            err = {"name": "MessageAbortedError", "data": {"message": "The operation was aborted."}}
            emit("session.error", {"sessionID": self.sid, "error": err})
        busy.pop(self.sid, None)
        emit("session.status", {"sessionID": self.sid, "status": {"type": "idle"}})
        emit("session.idle", {"sessionID": self.sid})

    def behave(self, raw):
        text = raw.split("\n\n", 1)[1] if raw.startswith("[orbit] ") and "\n\n" in raw else raw
        words = text.split()
        secs = float(words[1]) if len(words) > 1 and words[1].replace(".", "", 1).isdigit() else 10.0
        am = self.assistant()
        if text.startswith("slow"):
            self.text(am, "Working on it.")
            try:
                self.tool(am, "bash", {"command": f"sleep {secs:g}"}, secs)
            except Aborted:
                self.done(am, {"name": "MessageAbortedError", "data": {"message": "aborted"}})
                raise
            self.done(am, finish="tool-calls")
            am = self.assistant()
            self.text(am, "slept")
            self.done(am)
        elif text.startswith("split"):
            self.text(am, "first half, second half", pause=secs)
            self.done(am)
        elif text.startswith("fail"):
            err = {"name": "UnknownError", "data": {"message": "boom"}}
            self.done(am, err)
            emit("session.error", {"sessionID": self.sid, "error": err})
        elif text.startswith("die"):
            self.text(am, "half way")
            os._exit(3)
        elif text.startswith(("ask", "edit")):
            perm, pat = ("bash", "rm -rf build") if text.startswith("ask") else ("edit", "notes.txt")
            ok = self.tool(am, "bash" if perm == "bash" else "edit", {"command": pat} if perm == "bash" else {"filePath": pat},
                           ask={"kind": "permission", "permission": perm, "patterns": [pat], "metadata": {}, "always": [pat]})
            self.done(am, finish="tool-calls")
            am = self.assistant()
            self.text(am, "answer: allowed" if ok else "answer: refused")
            self.done(am)
        elif text.startswith("question"):
            p = {"id": new_id("prt"), "sessionID": self.sid, "messageID": am["id"], "callID": new_id("call")}
            got = self.wait({"kind": "question", "questions": [{"question": "Which color?", "header": "Color",
                                                                "options": [{"label": "red"}, {"label": "blue"}]}]}, p)
            self.text(am, f"answer: {got}")
            self.done(am)
        elif text.startswith("task"):
            self.task(am, {"description": "check things", "prompt": "run the tests", "subagent_type": "checker"})
            self.done(am, finish="tool-calls")
            am = self.assistant()
            self.text(am, "answer: task")
            self.done(am)
        elif text.startswith("think"):
            p = {"id": new_id("prt"), "sessionID": self.sid, "messageID": am["id"], "type": "reasoning", "text": "",
                 "time": {"start": now()}}
            put_part(p)
            for d in ("Let me ", "think."):
                emit("message.part.delta", {"sessionID": self.sid, "messageID": am["id"], "partID": p["id"],
                                            "field": "text", "delta": d})
            put_part({**p, "text": "Let me think.", "time": {"start": p["time"]["start"], "end": now()}})
            self.text(am, "answer: thought")
            self.done(am)
        else:
            self.tool(am, "bash", {"command": "echo hi"}, output="hi\n")
            self.done(am, finish="tool-calls")
            am = self.assistant()
            self.text(am, f"answer: {text}")
            self.done(am)

    def task(self, am, inp):
        """A task step: its child session (created while it runs, named in its metadata) does the subagent's work."""
        p = {"id": new_id("prt"), "sessionID": am["sessionID"], "messageID": am["id"], "type": "tool",
             "callID": new_id("call"), "tool": "task"}
        put_part({**p, "state": {"status": "pending", "input": {}, "raw": ""}})
        t0 = now()
        put_part({**p, "state": {"status": "running", "input": inp, "time": {"start": t0}}})
        c = create_session(parent=self.sid, title="check things (@checker subagent)")
        meta = {"sessionId": c["id"], "model": self.model}
        put_part({**p, "state": {"status": "running", "input": inp, "time": {"start": t0}, "metadata": meta}})
        sub = self.assistant(sid=c["id"])
        self.tool(sub, "bash", {"command": "make test"})
        self.text(sub, "checker: all good")
        self.done(sub)
        out = f'<task id="{c["id"]}" state="completed">\n<task_result>\nchecker: all good\n</task_result>\n</task>'
        put_part({**p, "state": {"status": "completed", "input": inp, "output": out, "title": "check things",
                                 "metadata": meta, "time": {"start": t0, "end": now()}}})


def start_turn(sid, text, model, agent, behave=None):
    log("inputs.log", {"conv": sid, "text": text, "model": model, "agent": agent})
    t = Turn(sid, model)
    t.user_message([{"type": "text", "text": text}])

    def go():
        try:
            t.run(text, behave and (lambda _t: behave(t)))
        except Exception as e:  # noqa: BLE001
            log("errors.log", {"error": repr(e)})
    th = threading.Thread(target=go, daemon=True)
    th.start()
    return th


# ---------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _authed(self):
        if not PASSWORD:
            return True
        want = "Basic " + base64.b64encode(f"opencode:{PASSWORD}".encode()).decode()
        return self.headers.get("Authorization") == want

    def _json(self, obj, code=200):
        data = b"" if obj is None else json.dumps(obj).encode()
        self.send_response(code if data or code != 200 else 204)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def _route(self, method):
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)
        log("http.log", {"method": method, "path": path})
        if not self._authed():
            return self._json({"error": "unauthorized"}, 401)
        body = self._body() if method == "POST" else {}
        parts = path.strip("/").split("/")
        if method == "GET" and path == "/event":
            return self._events()
        if method == "GET" and path == "/command":
            return self._json([{"name": "review", "description": "review changes"}])
        if method == "GET" and path == "/session/status":
            return self._json({sid: {"type": "busy"} for sid in busy})
        if method == "GET" and path == "/permission":
            return self._json([p["ask"] for p in pending.values() if p["kind"] == "permission"])
        if method == "POST" and path == "/session":
            return self._json(create_session())
        if parts[0] in ("permission", "question") and len(parts) == 3 and method == "POST":
            p = pending.get(parts[1])
            if not p:
                return self._json({"error": "no such request"}, 404)
            log("replies.log", {"id": parts[1], "action": parts[2], "body": body})
            p["reply"] = (body.get("reply") if parts[0] == "permission" else
                          ("rejected" if parts[2] == "reject" else ",".join(sum(body.get("answers") or [], []))))
            p["event"].set()
            emit(f"{parts[0]}.replied", {"sessionID": p["ask"]["sessionID"], "requestID": parts[1]})
            return self._json(True)
        if parts[0] == "session" and len(parts) >= 2:
            sid = parts[1]
            info = session_row(sid)
            if info is None:
                return self._json({"name": "NotFoundError", "data": {"message": f"Session not found: {sid}"}}, 404)
            rest = parts[2:]
            if method == "GET" and not rest:
                return self._json(info)
            if method == "GET" and rest == ["message"]:
                return self._json(messages(sid, int(qs["limit"][0]) if "limit" in qs else None))
            if method == "POST" and rest == ["fork"]:
                new = create_session(title=info["title"] + " (fork)")
                with dblock:
                    for mid, t, d in db.execute("select id, time_created, data from message where session_id = ? "
                                                "order by time_created, id", (sid,)).fetchall():
                        nm = new_id("msg")
                        db.execute("insert into message values (?, ?, ?, ?, ?)", (nm, new["id"], t, t, d))
                        for pid, pd in db.execute("select id, data from part where message_id = ? order by id", (mid,)).fetchall():
                            db.execute("insert into part values (?, ?, ?, ?, ?, ?)", (pid + "f" + nm[-4:], nm, new["id"], t, t, pd))
                return self._json(new)
            if method == "POST" and rest == ["abort"]:
                aborts.setdefault(sid, threading.Event()).set()
                return self._json(True)
            if method == "POST" and rest == ["prompt_async"]:
                text = "".join(p.get("text", "") for p in body.get("parts") or [] if p.get("type") == "text")
                start_turn(sid, text, body.get("model"), body.get("agent"))
                return self._json(None)
            if method == "POST" and rest == ["command"]:
                text = f"/{body['command']} {body.get('arguments', '')}".strip()

                def ran(t, b=body):
                    am = t.assistant()
                    t.text(am, f"ran {b['command']}: {b.get('arguments', '')}")
                    t.done(am)
                start_turn(sid, text, None, body.get("agent"), ran).join()  # (it answers when it is done)
                return self._json({"info": {}, "parts": []})
            if method == "POST" and rest == ["summarize"]:
                log("inputs.log", {"conv": sid, "text": "(summarize)", "model": body})
                t = Turn(sid, body)
                t.user_message([{"type": "compaction", "auto": False}])

                def summary(_):
                    am = t.assistant(summary=True)
                    t.text(am, "a summary")
                    t.done(am)
                    emit("session.compacted", {"sessionID": sid})
                t.run("", summary)
                return self._json(True)
        return self._json({"error": f"not here: {method} {path}"}, 404)

    def _events(self):
        q: queue.Queue = queue.Queue()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with clock:
            clients.append(q)
        try:
            self.wfile.write(b'data: {"type":"server.connected","properties":{}}\n\n')
            self.wfile.flush()
            while True:
                try:
                    e = q.get(timeout=1)
                except queue.Empty:
                    continue
                self.wfile.write(f"data: {json.dumps(e)}\n\n".encode())
                self.wfile.flush()
        except OSError:
            pass
        finally:
            with clock:
                clients.remove(q)
        self.close_connection = True


class Server(ThreadingHTTPServer):
    daemon_threads = True


def main():
    signal.signal(signal.SIGINT, lambda *_: os._exit(130))  # (OpenCode ends on SIGINT; its input closing does nothing)
    srv = Server(("127.0.0.1", 0), Handler)
    if not PASSWORD:
        print("Warning: OPENCODE_SERVER_PASSWORD is not set; server is unsecured.", flush=True)
    print(f"opencode server listening on http://127.0.0.1:{srv.server_address[1]}", flush=True)
    srv.serve_forever()


main()
