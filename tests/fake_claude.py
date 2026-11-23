"""A stand-in for `claude -p --input-format stream-json --output-format stream-json`.

Speaks the same protocol and writes a transcript under $ORBIT_CLAUDE_PROJECTS, so tests exercise the real driver
without spending API calls. Behaviour per request text:
  "slow ..."  a tool step that runs 10 s unless interrupted
  "fail ..."  an error result
  "die ..."   the process exits in the middle of the request (paused); "die 2": after a Bash step of 2 s
  "deaf ..."  like slow, but it ignores an interrupt (a Stop stays in progress)
  "flood ..." lots of output: $FAKE_FLOOD (40) rounds of a long text (UTF-8 across frame boundaries) and a Bash
              step, 0.05 s apart, one 200 KB text in the middle (longer than a pipe read), then the answer
  "maxturns ..." out of turns (paused)
  "follow ..." an answer, then a follow-up message 0.3 s later
  "narrate ..." text between its tool calls (a long one, then a list)
  "subagent ..." an Agent call whose subagent says something, greps, and reports back (also in subagents/)
  "bgagent ..." the same in the background: launched at once, the answer, then the subagent works and a task
                notification brings its report
  "bgslow ..." a background subagent that greps, then works on for $FAKE_BG_SLOW s (60) before it reports
  anything else: one Bash step, then "answer: <text>"
Its init event names its pid; inputs.log records each message it received with its pid.
"""

import json
import os
import queue
import sys
import threading
import time
from datetime import datetime, timezone

args = sys.argv[1:]
sid = args[args.index("--session-id") + 1] if "--session-id" in args else args[args.index("--resume") + 1]
root = os.environ["ORBIT_CLAUDE_PROJECTS"]
os.makedirs(os.path.join(root, "fake"), exist_ok=True)
transcript = os.path.join(root, "fake", f"{sid}.jsonl")
if "--fork-session" in args:  # a fork starts with a copy of the source conversation
    src = os.path.join(root, "fake", f"{args[args.index('--resume') + 1]}.jsonl")
    if os.path.exists(src) and not os.path.exists(transcript):
        with open(src) as f, open(transcript, "w") as g:
            g.write(f.read())
with open(os.path.join(root, "argv.log"), "a") as f:
    f.write(json.dumps(args) + "\n")
out_lock = threading.Lock()
interrupted = threading.Event()
inbox: queue.Queue = queue.Queue()
running = threading.Event()  # a turn is in progress: a user message now steers it (as Claude Code does)
steered: list = []


NARRATE = ["**First** I'll read `a.py` to see how the parser handles empty input. " + "It matters because the cache key ignores the locale, so two requests in different languages share an entry. " * 3,
           "Found it: the key is built before the locale is set.\n\n- fix the key\n- add a test"]


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def out(obj):
    with out_lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def log(obj):
    obj.setdefault("timestamp", now())
    with open(transcript, "a") as f:
        f.write(json.dumps(obj) + "\n")


def assistant(content):
    m = {"type": "assistant", "message": {"role": "assistant", "model": "fake-1", "content": content,
                                          "usage": {"input_tokens": 10, "cache_read_input_tokens": 1000}},
         "parent_tool_use_id": None, "session_id": sid}
    out(m)
    log({"type": "assistant", "message": m["message"]})


def tool_result(tid, text, err=False):
    c = [{"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": err}]
    out({"type": "user", "message": {"role": "user", "content": c}, "parent_tool_use_id": None})
    log({"type": "user", "message": {"role": "user", "content": c}})


SUB_REPORT = "The parser lives in `a.py`: `parse()` returns early on empty input."


def subagent(tid, background=False, slow=False):
    """An Agent call: the subagent's turn streams with parent_tool_use_id (and goes to <session>/subagents/)."""
    assistant([{"type": "tool_use", "id": tid, "name": "Agent",
                "input": {"subagent_type": "Explore", "description": "Find the parser", "prompt": "Where is the parser?",
                          **({"run_in_background": True} if background else {})}}])
    sub_dir = os.path.join(root, "fake", sid, "subagents")
    os.makedirs(sub_dir, exist_ok=True)
    with open(os.path.join(sub_dir, "agent-x1.meta.json"), "w") as f:
        json.dump({"agentType": "Explore", "description": "Find the parser", "toolUseId": tid,
                   "requestShape": "background" if background else "foreground"}, f)
    if background:  # answered at once; the work and the report come after
        launch = [{"type": "tool_result", "tool_use_id": tid, "content": [{"type": "text", "text": "Async agent launched successfully. agentId: x1"}]}]
        out({"type": "user", "message": {"role": "user", "content": launch}, "parent_tool_use_id": None,
             "tool_use_result": {"isAsync": True, "status": "async_launched"}})
        log({"type": "user", "message": {"role": "user", "content": launch}, "toolUseResult": {"isAsync": True, "status": "async_launched"}})
        return lambda: subagent_work(tid, sub_dir, background=True, slow=slow)
    subagent_work(tid, sub_dir)


def subagent_work(tid, sub_dir, background=False, slow=False):
    def sub(msg):
        out({**msg, "parent_tool_use_id": tid, "session_id": sid})
        with open(os.path.join(sub_dir, "agent-x1.jsonl"), "a") as f:
            f.write(json.dumps({**msg, "isSidechain": True, "agentId": "x1", "timestamp": now()}) + "\n")
    sub({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Looking for the parser."}]}})
    sub({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": tid + "g", "name": "Grep", "input": {"pattern": "def parse"}}]}})
    sub({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid + "g", "content": "a.py:3"}]}})
    if slow:  # still at work: reading a file, for a while
        sub({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": tid + "r", "name": "Read", "input": {"file_path": "/src/parser/a.py"}}]}})
        time.sleep(float(os.environ.get("FAKE_BG_SLOW", "60")))
        sub({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid + "r", "content": "..."}]}})
    sub({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Found it in a.py."}]}})
    if background:  # its report arrives as a task notification (a queued command for the main agent)
        sub({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": SUB_REPORT}]}})
        note = (f"<task-notification>\n<task-id>x1</task-id>\n<tool-use-id>{tid}</tool-use-id>\n<status>completed</status>\n"
                f"<summary>Agent \"Find the parser\" finished</summary>\n<result>{SUB_REPORT}</result>\n</task-notification>")
        out({"type": "system", "subtype": "task_notification", "task_id": "x1", "tool_use_id": tid, "status": "completed",
             "summary": SUB_REPORT, "summary_is_report": True, "session_id": sid})  # as Claude Code sends it
        log({"type": "attachment", "attachment": {"type": "queued_command", "prompt": note}})
        return
    tool_result(tid, [{"type": "text", "text": SUB_REPORT}])


def result(text, ok=True, subtype=None):
    out({"type": "result", "subtype": subtype or ("success" if ok else "error_during_execution"),
         "is_error": not ok, "result": text, "session_id": sid})


def handle(text, n):
    running.set()
    try:
        _handle(text, n)
    finally:
        running.clear()


def _handle(text, n):
    interrupted.clear()
    log({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}})
    out({"type": "system", "subtype": "init", "session_id": sid, "cwd": os.getcwd(), "model": "fake-1", "pid": os.getpid(),
         "slash_commands": ["compact", "context", "model", "doctor", "review"], "terminal_slash_commands": ["doctor"]})
    tid = f"toolu_{n}"
    if text.startswith("deaf"):  # a Stop never reaches it: the request ends in its own time
        assistant([{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": "sleep 10"}}])
        time.sleep(10)
        tool_result(tid, "")
        result("deaf")
        return
    if text.startswith("flood"):
        for k in range(int(os.environ.get("FAKE_FLOOD", "40"))):
            size = 200_000 if k == 20 else 8000
            assistant([{"type": "text", "text": f"round {k}: " + ("é中x" * (size // 3))}])
            assistant([{"type": "tool_use", "id": f"{tid}_{k}", "name": "Bash", "input": {"command": f"echo {k}"}}])
            tool_result(f"{tid}_{k}", str(k))
            time.sleep(0.05)
        assistant([{"type": "text", "text": "flooded"}])
        result("flooded")
        return
    if text.startswith("slow"):
        assistant([{"type": "text", "text": "Working on it."}])
        assistant([{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": "sleep 10"}}])
        for _ in range(100):
            if interrupted.is_set():
                tool_result(tid, "interrupted", True)
                result("", ok=False)
                return
            if steered:  # a message mid-turn: folded into this turn, one result
                time.sleep(0.2)
                tool_result(tid, "")
                for x in steered:  # Claude Code keeps a mid-turn message as a queued-command attachment
                    log({"type": "attachment", "attachment": {"type": "queued_command", "prompt": [{"type": "text", "text": x}]}})
                answer = "slept; steered: " + ", ".join(steered)
                steered.clear()
                assistant([{"type": "text", "text": answer}])
                result(answer)
                return
            time.sleep(0.1)
        tool_result(tid, "")
        assistant([{"type": "text", "text": "slept"}])
        result("slept")
        return
    if text.startswith("fail"):
        result("boom", ok=False, subtype="error")
        return
    if text.startswith("maxturns"):
        result("", ok=False, subtype="error_max_turns")
        return
    if text.startswith("die"):
        wait = text.split()[1:2]
        if wait and wait[0].replace(".", "", 1).isdigit():  # at work for a while first
            assistant([{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": f"sleep {wait[0]}"}}])
            time.sleep(float(wait[0]))
        assistant([{"type": "text", "text": "half way"}])
        sys.stdout.flush()
        os._exit(3)
    later = None
    if text.startswith("subagent"):
        subagent(tid + "s")
    if text.startswith("bgagent"):
        later = subagent(tid + "s", background=True)
    if text.startswith("bgslow"):  # (on its own thread: the next requests are not held up)
        work = subagent(tid + "s", background=True, slow=True)
        later = lambda: threading.Thread(target=work, daemon=True).start()
    if text.startswith("narrate"):  # a turn that talks between its tools (the card shows all of it)
        assistant([{"type": "text", "text": NARRATE[0]}])
        assistant([{"type": "tool_use", "id": tid + "a", "name": "Read", "input": {"file_path": "/tmp/a.py"}}])
        tool_result(tid + "a", "...")
        assistant([{"type": "text", "text": NARRATE[1]}])
        for k in "bc":
            assistant([{"type": "tool_use", "id": tid + k, "name": "Bash", "input": {"command": f"pytest -k {k}"}}])
            tool_result(tid + k, "ok")
    assistant([{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": "echo hi"}}])
    tool_result(tid, "hi")
    answer = f"answer: {text}"
    assistant([{"type": "text", "text": answer}])
    result(answer)
    if later:  # a background subagent works on after the answer
        time.sleep(0.3)
        later()
    if text.startswith("follow"):
        time.sleep(0.3)
        if inbox.empty():  # like the real harness: a spontaneous turn only while no request is waiting
            assistant([{"type": "text", "text": "a follow-up"}])


def worker():
    n = 0
    while True:
        text = inbox.get()
        if text is None:
            return
        n += 1
        handle(text, n)


def main():
    # Turns run one at a time, as in Claude Code; the reader stays free for interrupts.
    w = threading.Thread(target=worker)
    w.start()
    for line in sys.stdin:
        msg = json.loads(line)
        if msg.get("type") == "control_request":
            interrupted.set()
        elif msg.get("type") == "user":
            raw = "".join(b.get("text", "") for b in msg["message"]["content"])
            with open(os.path.join(root, "inputs.log"), "a") as f:  # what the agent really received
                f.write(json.dumps({"sid": sid, "text": raw, "pid": os.getpid()}) + "\n")
            # Like a model, act on the user's text; the [orbit] context line comes first.
            if raw.startswith("[orbit] ") and "\n\n" in raw:
                raw = raw.split("\n\n", 1)[1]
            if running.is_set():
                steered.append(raw)
            else:
                inbox.put(raw)
    inbox.put(None)
    w.join()


main()
