"""A stand-in for `codex app-server --listen stdio://`: JSON-RPC over stdio, the methods and notifications Orbit uses.

fake_codex.py hands over to this when its first argument is `app-server`. Like the exec fake it writes rollouts under
$ORBIT_CODEX_SESSIONS (history is rebuilt from them) and logs to that folder: argv.log (the command), rpc.log (the
client's requests), inputs.log (each request and steer, with its thread and this process's pid), answers.log (what
the client answered to server requests), stdin.log (every line read: its method, or "answer", and the pid).
Behaviour per request text (after the [orbit] line):
  "slow [N] ..."  narration, then a command that runs N s (10) unless interrupted (turn/interrupt: the turn is
                  interrupted) or steered (turn/steer: it ends at once, the answer names the message)
  "gapask N"      a command that runs N s, then a command approval it waits for (30 s), then "answer: cmd=<decision>"
                  (a restart in those N s: the next server answers it)
  "nested ..."    a subagent `top` that starts one of its own (`inner`); the turn ends; $FAKE_SUB_PAUSE s later
                  `inner` runs a command (under top's step) and `top` reports
  "fail ..."      the turn fails ("boom")
  "die ..."       the process exits in the middle of the request
  "subagents ..." two subagents, reported as the app-server does (subAgentActivity in the main thread, their own
                  thread's turn/item notifications): `checker` reports before the turn ends; `straggler` is still at
                  work when it ends and reports $FAKE_SUB_PAUSE s (default 0.8) later
  "approve ..."   asks for a command approval and a file-change approval, then answers with the decisions
  anything else:  one command step, then "answer: <text>"
$FAKE_CODEX_NO_APP=1: `app-server` is not a subcommand (exits 2 at once), as with an old codex.
$FAKE_THREAD_DELAY=N: thread/start (and resume, fork) is answered N s late.
"""

import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

root = os.environ["ORBIT_CODEX_SESSIONS"]
day = os.path.join(root, "2026", "01", "02")
os.makedirs(day, exist_ok=True)
wlock = threading.Lock()
state = {"thread": None, "rollout": None, "turn": None, "model": "fake-codex"}
pending: dict[int, dict] = {}  # server request id -> {"ev", "result"}
next_id = [1000]


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def out(obj):
    with wlock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def note(method, params):
    out({"method": method, "params": params})


def logf(name, obj):
    with open(os.path.join(root, name), "a") as f:
        f.write(json.dumps(obj) + "\n")


def find(thread):
    for dp, _, files in os.walk(root):
        for n in files:
            if n.endswith(f"{thread}.jsonl"):
                return os.path.join(dp, n)
    return None


def write(path, kind, payload):
    with open(path, "a") as f:
        f.write(json.dumps({"timestamp": now(), "type": kind, "payload": payload}) + "\n")


def item_done(path, thread, it):
    write(path, "event_msg", {"type": "item_completed", "thread_id": thread, "item": it})


def thread_obj(th, path):
    return {"id": th, "path": path, "model": state["model"], "status": {"type": "idle"}, "turns": []}


def ask(method, params, timeout=5.0):
    """A server request; the client's answer (None: none in time)."""
    with wlock:
        next_id[0] += 1
        rid = next_id[0]
    pending[rid] = {"ev": threading.Event(), "result": None}
    out({"id": rid, "method": method, "params": params})
    pending[rid]["ev"].wait(timeout)
    return pending.pop(rid)["result"]


class Turn:
    def __init__(self, text):
        self.id = str(uuid.uuid4())
        self.raw = text
        self.text = text.split("\n\n", 1)[1] if text.startswith("[orbit] ") and "\n\n" in text else text
        self.thread, self.rollout = state["thread"], state["rollout"]
        self.interrupted = threading.Event()
        self.steered: list[str] = []
        self.n = 0

    # what the client sees, and the rollout
    def item(self, it, started=True, completed=True, thread=None, turn=None):
        th = thread or self.thread
        if started:
            note("item/started", {"item": it, "threadId": th, "turnId": turn or self.id})
        if completed:
            note("item/completed", {"item": it, "threadId": th, "turnId": turn or self.id})

    def say(self, words, phase="commentary"):
        self.n += 1
        it = {"type": "agentMessage", "id": f"msg_{self.n}", "text": words, "phase": phase}
        note("item/started", {"item": {**it, "text": ""}, "threadId": self.thread, "turnId": self.id})
        note("item/agentMessage/delta", {"threadId": self.thread, "turnId": self.id, "itemId": it["id"], "delta": words})
        note("item/completed", {"item": it, "threadId": self.thread, "turnId": self.id})
        item_done(self.rollout, self.thread, {"type": "AgentMessage", "content": [{"type": "Text", "text": words}], "phase": phase})

    def command(self, cmd, secs=0.0, status="completed"):
        """A command step; False if the turn was interrupted (or steered) while it ran."""
        self.n += 1
        it = {"type": "commandExecution", "id": f"exec-{self.n}", "command": f"/bin/zsh -lc '{cmd}'", "status": "inProgress",
              "aggregatedOutput": None, "exitCode": None}
        self.item(it, completed=False)
        end = time.time() + secs
        while time.time() < end:
            if self.interrupted.is_set() or self.steered:
                return False
            time.sleep(0.05)
        done = {**it, "status": status, "aggregatedOutput": "hi\n", "exitCode": 0 if status == "completed" else None}
        self.item(done, started=False)
        item_done(self.rollout, self.thread, {"type": "CommandExecution", "id": it["id"], "command": ["/bin/zsh", "-lc", cmd],
                                              "status": status, "exit_code": done["exitCode"]})
        return True

    def end(self, status="completed", error=None):
        if status == "completed":
            note("thread/tokenUsage/updated", {"threadId": self.thread, "turnId": self.id, "tokenUsage": {
                "total": {"inputTokens": 5000}, "last": {"inputTokens": 1234, "cachedInputTokens": 1000}, "modelContextWindow": 9999}})
            write(self.rollout, "event_msg", {"type": "token_count", "info": {"last_token_usage": {"input_tokens": 1234}}})
            write(self.rollout, "event_msg", {"type": "task_complete"})
        elif status == "interrupted":
            write(self.rollout, "event_msg", {"type": "turn_aborted", "reason": "interrupted"})
        state["turn"] = None
        note("turn/completed", {"threadId": self.thread, "turn": {"id": self.id, "status": status, "items": [],
                                                                 "error": {"message": error} if error else None}})

    def steer(self, text):
        self.steered.append(text)
        it = {"type": "userMessage", "id": str(uuid.uuid4()), "content": [{"type": "text", "text": text, "text_elements": []}]}
        self.item(it)
        item_done(self.rollout, self.thread, {"type": "UserMessage", "content": [{"type": "text", "text": text}]})

    # the request
    def run(self):
        logf("inputs.log", {"thread": self.thread, "text": self.raw, "pid": os.getpid()})
        note("turn/started", {"threadId": self.thread, "turn": {"id": self.id, "status": "inProgress", "items": []}})
        write(self.rollout, "turn_context", {"model": state["model"], "cwd": os.getcwd()})
        write(self.rollout, "event_msg", {"type": "task_started"})
        self.item({"type": "userMessage", "id": str(uuid.uuid4()), "content": [{"type": "text", "text": self.raw}]})
        item_done(self.rollout, self.thread, {"type": "UserMessage", "content": [{"type": "text", "text": self.raw}]})
        t = self.text
        words = t.split()
        secs = float(words[1]) if len(words) > 1 and words[1].replace(".", "", 1).isdigit() else 10.0
        if t.startswith("slow"):
            self.say("Working on it.")
            if not self.command(f"sleep {secs:g}", secs):
                if self.interrupted.is_set():
                    return self.end("interrupted")
                self.say(f"steered: {self.steered[0]}", "final_answer")
                return self.end()
            self.say("slept", "final_answer")
            return self.end()
        if t.startswith("fail"):
            return self.end("failed", "boom")
        if t.startswith("die"):
            self.say("half way")
            os._exit(3)
        if t.startswith("subagents"):
            return self.subagents()
        if t.startswith("nested"):
            return self.nested()
        if t.startswith("gapask"):
            self.command(f"sleep {secs:g}", secs)
            cmd = ask("item/commandExecution/requestApproval", {"threadId": self.thread, "turnId": self.id,
                                                               "itemId": "exec-g", "command": "make", "cwd": os.getcwd()}, 30)
            logf("answers.log", {"cmd": (cmd or {}).get("decision"), "pid": os.getpid()})
            self.say(f"answer: cmd={(cmd or {}).get('decision')}", "final_answer")
            return self.end()
        if t.startswith("approve"):
            cmd = ask("item/commandExecution/requestApproval", {"threadId": self.thread, "turnId": self.id, "itemId": "exec-a",
                                                               "command": "rm -rf /tmp/x", "cwd": os.getcwd()})
            fc = ask("item/fileChange/requestApproval", {"threadId": self.thread, "turnId": self.id, "itemId": "patch-a"})
            q = ask("item/tool/requestUserInput", {"threadId": self.thread, "turnId": self.id, "itemId": "q-a",
                                                   "questions": [], "isBlocking": True})
            decided = {"cmd": (cmd or {}).get("decision"), "file": (fc or {}).get("decision"), "ask": q}
            logf("answers.log", decided)
            self.command("rm -rf /tmp/x", status="completed" if decided["cmd"] == "accept" else "declined")
            answer = f"answer: cmd={decided['cmd']} file={decided['file']}"
            self.say(answer, "final_answer")
            return self.end()
        self.command("echo hi")
        self.say(f"answer: {t}", "final_answer")
        return self.end()

    def subagent(self, name):
        """A subagent starts: subAgentActivity in the main thread, its own thread and rollout."""
        th = str(uuid.uuid4())
        sub_turn = str(uuid.uuid4())
        path = os.path.join(day, f"rollout-2026-01-02T00-00-01-{th}.jsonl")
        act = {"type": "subAgentActivity", "id": f"call_{name}", "kind": "started", "agentThreadId": th,
               "agentPath": f"/root/{name}", "model": "fake-codex", "reasoningEffort": "high"}
        self.item(act)
        item_done(self.rollout, self.thread, {"type": "SubAgentActivity", "id": f"call_{name}", "kind": "started",
                                              "agent_thread_id": th, "agent_path": f"/root/{name}", "model": "fake-codex",
                                              "reasoning_effort": "high"})
        note("turn/started", {"threadId": th, "turn": {"id": sub_turn, "status": "inProgress", "items": []}})
        return {"name": name, "thread": th, "turn": sub_turn, "path": path, "main_turn": self.id}

    def sub_command(self, s, cmd, finish=True):
        it = {"type": "commandExecution", "id": f"exec-{s['name']}", "command": f"/bin/zsh -lc '{cmd}'", "status": "inProgress"}
        if not finish:
            self.item(it, completed=False, thread=s["thread"], turn=s["turn"])
            write(s["path"], "event_msg", {"type": "item_started", "thread_id": s["thread"],
                                           "item": {"type": "CommandExecution", "id": it["id"], "command": ["/bin/zsh", "-lc", cmd]}})
            return
        self.item({**it, "status": "completed", "exitCode": 0}, started=False, thread=s["thread"], turn=s["turn"])
        item_done(s["path"], s["thread"], {"type": "CommandExecution", "id": it["id"], "command": ["/bin/zsh", "-lc", cmd],
                                           "status": "completed", "exit_code": 0})

    def sub_report(self, s, words):
        it = {"type": "agentMessage", "id": f"msg-{s['name']}", "text": words, "phase": "final_answer"}
        self.item(it, thread=s["thread"], turn=s["turn"])
        item_done(s["path"], s["thread"], {"type": "AgentMessage", "content": [{"type": "Text", "text": words}], "phase": "final_answer"})
        note("turn/completed", {"threadId": s["thread"], "turn": {"id": s["turn"], "status": "completed", "items": []}})
        act = {"type": "subAgentActivity", "id": f"subagent-completed-{s['name']}", "kind": "completed",
               "agentThreadId": s["thread"], "agentPath": f"/root/{s['name']}", "model": None, "reasoningEffort": None}
        self.item(act, turn=s["main_turn"])
        item_done(self.rollout, self.thread, {"type": "SubAgentActivity", "id": act["id"], "kind": "completed",
                                              "agent_thread_id": s["thread"], "agent_path": f"/root/{s['name']}"})

    def nested(self):
        self.say("Starting a helper.")
        top = self.subagent("top")
        th = str(uuid.uuid4())
        act = {"type": "subAgentActivity", "id": "call_inner", "kind": "started", "agentThreadId": th,
               "agentPath": "/root/top/inner", "model": "fake-codex", "reasoningEffort": "low"}
        self.item(act, thread=top["thread"], turn=top["turn"])  # (in top's own thread)
        inner = {"name": "inner", "thread": th, "turn": str(uuid.uuid4()),
                 "path": os.path.join(day, f"rollout-2026-01-02T00-00-02-{th}.jsonl"), "main_turn": self.id}
        self.say("answer: nested ran", "final_answer")
        self.end()
        time.sleep(float(os.environ.get("FAKE_SUB_PAUSE", "0.8")))
        self.sub_command(inner, "make test")
        self.sub_report(top, "top: done, inner tested")

    def subagents(self):
        self.say("Starting two helpers.")
        straggler = self.subagent("straggler")
        self.sub_command(straggler, "sleep 600", finish=False)
        checker = self.subagent("checker")
        self.sub_command(checker, "curl -sI https://example.org", finish=False)
        time.sleep(0.2)
        self.sub_command(checker, "curl -sI https://example.org")
        self.sub_report(checker, "checker: all good")
        self.say("answer: helpers ran", "final_answer")
        self.end()
        time.sleep(float(os.environ.get("FAKE_SUB_PAUSE", "0.8")))  # the straggler outlives the turn
        self.sub_command(straggler, "sleep 600")
        self.sub_report(straggler, "straggler: done at last")


def open_rollout(th, meta):
    path = os.path.join(day, f"rollout-2026-01-02T00-00-00-{th}.jsonl")
    write(path, "session_meta", meta)
    return path


def request(msg):
    m, p, rid = msg["method"], msg.get("params") or {}, msg["id"]
    if m in ("thread/start", "thread/resume", "thread/fork", "turn/start", "turn/steer", "turn/interrupt"):
        logf("rpc.log", {"method": m, "params": p, "pid": os.getpid()})
    if m in ("thread/start", "thread/resume", "thread/fork") and os.environ.get("FAKE_THREAD_DELAY"):
        time.sleep(float(os.environ["FAKE_THREAD_DELAY"]))
    if m == "initialize":
        return out({"id": rid, "result": {"userAgent": "fake/0", "codexHome": os.environ.get("CODEX_HOME", ""),
                                          "platformFamily": "unix", "platformOs": "macos"}})
    if m in ("thread/start", "thread/fork"):
        th = str(uuid.uuid4())
        meta = {"id": th, "timestamp": now(), "cwd": p.get("cwd") or os.getcwd(), "originator": "orbit", "source": "vscode"}
        if m == "thread/fork":
            src = find(p["threadId"])
            if not src:
                return out({"id": rid, "error": {"code": -32600, "message": f"no rollout for {p['threadId']}"}})
            meta["forked_from_id"] = p["threadId"]
            path = open_rollout(th, meta)
            with open(src) as f, open(path, "a") as g:
                for line in f:
                    if json.loads(line)["type"] == "response_item":
                        g.write(line)
        else:
            path = open_rollout(th, meta)
        state.update(thread=th, rollout=path, model=p.get("model") or "fake-codex")
        out({"id": rid, "result": {"thread": thread_obj(th, path), "model": state["model"], "cwd": meta["cwd"]}})
        return note("thread/started", {"thread": thread_obj(th, path)})
    if m == "thread/resume":
        path = find(p["threadId"])
        if not path:
            return out({"id": rid, "error": {"code": -32600, "message": f"no rollout found for thread id {p['threadId']}"}})
        state.update(thread=p["threadId"], rollout=path, model=p.get("model") or "fake-codex")
        return out({"id": rid, "result": {"thread": thread_obj(p["threadId"], path), "model": state["model"]}})
    if m == "turn/start":
        if p.get("threadId") != state["thread"]:
            return out({"id": rid, "error": {"code": -32600, "message": "thread not loaded"}})
        turn = Turn(p["input"][0]["text"])
        state["turn"] = turn
        out({"id": rid, "result": {"turn": {"id": turn.id, "status": "inProgress", "items": []}}})
        threading.Thread(target=turn.run, daemon=True).start()
        return
    if m == "turn/steer":
        turn = state["turn"]
        if turn is None or turn.id != p.get("expectedTurnId"):
            return out({"id": rid, "error": {"code": -32600, "message": "no such active turn"}})
        logf("inputs.log", {"thread": turn.thread, "steer": p["input"][0]["text"], "pid": os.getpid()})
        out({"id": rid, "result": {"turnId": turn.id}})
        return turn.steer(p["input"][0]["text"])
    if m == "turn/interrupt":
        turn = state["turn"]
        if turn is not None and turn.id == p.get("turnId"):
            turn.interrupted.set()
        return out({"id": rid, "result": {}})
    out({"id": rid, "error": {"code": -32601, "message": f"unknown method {m}"}})


def main(args):
    with open(os.path.join(root, "argv.log"), "a") as f:
        f.write(json.dumps(args) + "\n")
    if os.environ.get("FAKE_CODEX_NO_APP") == "1":
        sys.stderr.write("error: unrecognized subcommand 'app-server'\n")
        sys.exit(2)
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        logf("stdin.log", {"method": msg.get("method") or "answer", "pid": os.getpid()})
        if "method" in msg and "id" in msg:
            request(msg)
        elif "id" in msg and msg["id"] in pending:
            pending[msg["id"]]["result"] = msg.get("result", msg.get("error"))
            pending[msg["id"]]["ev"].set()
    sys.exit(0)  # stdin closed: the client is gone


if __name__ == "__main__":
    main(sys.argv[1:])
