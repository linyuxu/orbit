"""A stand-in for `codex exec --json [resume <thread> | fork <thread>] -` (and, with `app-server` as its first
argument, for `codex app-server`: fake_codex_app.py).

Prints the same JSONL events and writes a rollout under $ORBIT_CODEX_SESSIONS, so tests exercise the real driver
without spending API calls. Behaviour per request text (after the [orbit] line):
  "slow ..."  narration, then a command that runs 10 s unless interrupted (SIGINT: the turn is aborted)
  "fail ..."  turn.failed
  "die ..."   the process exits in the middle of the request
  "subagents ..." two subagents (in rollouts only, as Codex does): `checker` runs a command and reports before the
              turn ends (waited for); `straggler` is still at work when it ends
  anything else: one command step, then "answer: <text>"
"""

import json
import os
import signal
import sys
import time
import uuid
from datetime import datetime, timezone

args = sys.argv[1:]
root = os.environ["ORBIT_CODEX_SESSIONS"]
os.makedirs(root, exist_ok=True)
if args[:1] == ["app-server"]:  # the long-lived JSON-RPC server: fake_codex_app.py
    import fake_codex_app
    fake_codex_app.main(args)
with open(os.path.join(root, "argv.log"), "a") as f:
    f.write(json.dumps(args) + "\n")
assert args[:2] == ["exec", "--json"], args
interrupted = False


def on_int(*_):
    global interrupted
    interrupted = True


signal.signal(signal.SIGINT, on_int)


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def find(thread):
    for dp, _, files in os.walk(root):
        for n in files:
            if n.endswith(f"{thread}.jsonl"):
                return os.path.join(dp, n)
    return None


day = os.path.join(root, "2026", "01", "02")
os.makedirs(day, exist_ok=True)
if "resume" in args:
    thread = args[args.index("resume") + 1]
    rollout = find(thread)
    assert rollout, f"no rollout for {thread}"
    meta = None
else:
    thread = str(uuid.uuid4())
    rollout = os.path.join(day, f"rollout-2026-01-02T00-00-00-{thread}.jsonl")
    meta = {"id": thread, "timestamp": now(), "cwd": os.getcwd(), "originator": "codex_exec", "source": "exec"}
    if "fork" in args:  # a fork starts with the source's history, copied as plain response items
        src = find(args[args.index("fork") + 1])
        meta["forked_from_id"] = args[args.index("fork") + 1]
        with open(src) as f, open(rollout, "w") as g:
            g.write(json.dumps({"timestamp": now(), "type": "session_meta", "payload": meta}) + "\n")
            for line in f:
                d = json.loads(line)
                if d["type"] == "response_item":
                    g.write(line)
        meta = None


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def log(kind, payload):
    with open(rollout, "a") as f:
        f.write(json.dumps({"timestamp": now(), "type": kind, "payload": payload}) + "\n")


def event(payload):
    log("event_msg", payload)


def item(it):
    event({"type": "item_completed", "thread_id": thread, "item": it})


if meta:
    log("session_meta", meta)
raw = sys.stdin.read()
with open(os.path.join(root, "inputs.log"), "a") as f:
    f.write(json.dumps({"thread": thread, "text": raw}) + "\n")
text = raw.split("\n\n", 1)[1] if raw.startswith("[orbit] ") and "\n\n" in raw else raw

out({"type": "thread.started", "thread_id": thread})
out({"type": "turn.started"})
log("turn_context", {"model": "fake-codex", "cwd": os.getcwd()})
event({"type": "task_started"})
item({"type": "UserMessage", "content": [{"type": "text", "text": raw}]})


def say(i, words, phase="commentary"):
    out({"type": "item.completed", "item": {"id": f"item_{i}", "type": "agent_message", "text": words}})
    item({"type": "AgentMessage", "content": [{"type": "Text", "text": words}], "phase": phase})


def command(i, cmd, secs=0.0):
    started = {"id": f"item_{i}", "type": "command_execution", "command": f"/bin/zsh -lc '{cmd}'",
               "aggregated_output": "", "exit_code": None, "status": "in_progress"}
    out({"type": "item.started", "item": started})
    end = time.time() + secs
    while time.time() < end:
        if interrupted:
            event({"type": "turn_aborted", "reason": "interrupted"})
            item({"type": "CommandExecution", "command": ["/bin/zsh", "-lc", cmd], "status": "failed"})
            sys.exit(1)
        time.sleep(0.05)
    out({"type": "item.completed", "item": {**started, "aggregated_output": "hi\n", "exit_code": 0,
                                            "status": "completed"}})
    item({"type": "CommandExecution", "id": f"exec-{i}", "command": ["/bin/zsh", "-lc", cmd], "status": "completed",
          "exit_code": 0})


def done(answer):
    event({"type": "token_count", "info": {"last_token_usage": {"input_tokens": 1234}}})
    event({"type": "task_complete", "last_agent_message": answer})
    out({"type": "turn.completed", "usage": {"input_tokens": 5000, "cached_input_tokens": 4000, "output_tokens": 9}})


def subagent(name, sub_thread, steps, final=None):
    """A subagent: SubAgentActivity in the main rollout, its own rollout with its steps (and final answer)."""
    item({"type": "SubAgentActivity", "model": "fake-codex", "reasoning_effort": "high", "id": f"call_{name}",
          "kind": "started", "agent_thread_id": sub_thread, "agent_path": f"/root/{name}"})
    path = os.path.join(day, f"rollout-2026-01-02T00-00-01-{sub_thread}.jsonl")
    def sub(payload):
        with open(path, "a") as f:
            f.write(json.dumps({"timestamp": now(), "type": "event_msg", "payload": payload}) + "\n")
    for k, (cmd, finished) in enumerate(steps):
        it = {"type": "CommandExecution", "id": f"exec-{name}-{k}", "command": ["/bin/zsh", "-lc", cmd]}
        sub({"type": "item_started", "thread_id": sub_thread, "item": {**it, "status": "in_progress"}})
        if finished:
            time.sleep(0.4)
            sub({"type": "item_completed", "thread_id": sub_thread, "item": {**it, "status": "completed", "exit_code": 0}})
    if final:
        sub({"type": "item_completed", "thread_id": sub_thread,
             "item": {"type": "AgentMessage", "content": [{"type": "Text", "text": final}], "phase": "final_answer"}})
        item({"type": "SubAgentActivity", "id": f"subagent-completed-{name}", "kind": "completed",
              "agent_thread_id": sub_thread, "agent_path": f"/root/{name}"})


if text.startswith("subagents"):
    say(0, "Starting two helpers.")
    out({"type": "item.started", "item": {"id": "item_1", "type": "collab_tool_call", "tool": "wait", "status": "in_progress"}})
    subagent("straggler", str(uuid.uuid4()), [("sleep 600", False)])
    time.sleep(float(os.environ.get("FAKE_SUB_PAUSE", "0.5")))  # (both at work now)
    subagent("checker", str(uuid.uuid4()), [("curl -sI https://example.org", True)], final="checker: all good")
    out({"type": "item.completed", "item": {"id": "item_1", "type": "collab_tool_call", "tool": "wait", "status": "completed"}})
    say(2, "answer: helpers ran", "final_answer")
    done("answer: helpers ran")
elif text.startswith("slow"):
    say(0, "Working on it.")
    command(1, "sleep 10", 10)
    say(2, "slept", "final_answer")
    done("slept")
elif text.startswith("fail"):
    out({"type": "turn.failed", "error": {"message": "boom"}})
    sys.exit(1)
elif text.startswith("die"):
    say(0, "half way")
    os._exit(3)
else:
    command(0, "echo hi")
    answer = f"answer: {text}"
    say(1, answer, "final_answer")
    done(answer)
