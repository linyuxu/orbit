"""A stand-in for `agy --input-format stream-json --output-format stream-json [--conversation ID] -p=`.

Speaks agy's NDJSON protocol (init / step_update / result) and writes the conversation as agy does: a SQLite file
under $ORBIT_AGY_CONVERSATIONS whose steps hold protobuf payloads. Behaviour per request text:
  "slow [N] ..."  narration, then a tool step that runs N s (10) unless interrupted (SIGINT: the request ends, agy
              exits)
  "split N"   an answer streamed in two deltas N s apart (a restart between them: the next server joins them)
  "fail ..."  an error result
  "die ..."   the process exits in the middle of the request
  anything else: one run_command step, then "answer: <text>"
`agy models` prints a model list. inputs.log: each request (with the pid); stdin.log: every line read.
"""

import json
import os
import queue
import signal
import sqlite3
import sys
import threading
import time
import uuid

args = sys.argv[1:]
if args[:1] == ["models"]:
    print("Fetching available models...")
    print("fake-pro\tFake Pro")
    print("fake-flash\tFake Flash")
    sys.exit(0)
root = os.environ["ORBIT_AGY_CONVERSATIONS"]
os.makedirs(root, exist_ok=True)
with open(os.path.join(root, "argv.log"), "a") as f:
    f.write(json.dumps(args) + "\n")
assert args[-1] == "-p=" and "stream-json" in args, args
conv = args[args.index("--conversation") + 1] if "--conversation" in args else str(uuid.uuid4())
if not os.path.exists(os.path.join(root, f"{conv}.db")) and "--conversation" in args:
    sys.stderr.write(f'warning: conversation "{conv}" not found\n')
    conv = str(uuid.uuid4())
db = sqlite3.connect(os.path.join(root, f"{conv}.db"), check_same_thread=False)
db.execute("create table if not exists steps (idx integer primary key, step_type integer, status integer, "
           "step_payload blob)")
db.commit()
out_lock = threading.Lock()
interrupted = threading.Event()
inbox: queue.Queue = queue.Queue()


# ---------------------------------------------------------------------- protobuf encoding (wire format)
def varint(n):
    b = bytearray()
    while True:
        c = n & 0x7F
        n >>= 7
        b.append(c | (0x80 if n else 0))
        if not n:
            return bytes(b)


def field(num, v):
    if isinstance(v, int):
        return varint(num << 3) + varint(v)
    if isinstance(v, str):
        v = v.encode()
    return varint(num << 3 | 2) + varint(len(v)) + v


def msg(*fields):
    return b"".join(field(n, v) for n, v in fields)


def meta(call_id=""):
    t = time.time()
    m = [(1, msg((1, int(t)), (2, int((t % 1) * 1e9))))]
    if call_id:
        m.append((4, msg((1, call_id))))
    return msg(*m)


def store(step_type, status, payload):
    idx = db.execute("select coalesce(max(idx), -1) + 1 from steps").fetchone()[0]
    db.execute("insert into steps values (?, ?, ?, ?)", (idx, step_type, status, payload))
    db.commit()
    return idx


def out(obj):
    with out_lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def update(idx, state, step_type, **kw):
    out({"event": "step_update", "step_update": {"conversation_id": conv, "step_index": idx, "state": state,
                                                  "step_type": step_type, **kw}})


def result(status="SUCCESS", response="", **kw):
    out({"event": "result", "result": {"conversation_id": conv, "status": status, "response": response, **kw}})


def respond(words, calls=()):
    """An agent_response step: text (streamed in two deltas) and tool calls."""
    payload = [(1, words)] + [(7, msg((1, cid), (2, name), (3, json.dumps(params)))) for cid, name, params in calls]
    idx = store(15, 3, msg((5, meta()), (20, msg(*payload))))
    if words:
        half = len(words) // 2
        update(idx, "ACTIVE", "agent_response", text_delta=words[:half])
        update(idx, "DONE", "agent_response", text_delta=words[half:],
               usage={"input_tokens": 300, "cache_read_tokens": 700, "output_tokens": 5})
    else:
        update(idx, "DONE", "agent_response", usage={"input_tokens": 300, "cache_read_tokens": 700})


def tool(n, cmd, secs=0.0):
    cid = f"call_{n}"
    respond("", [(cid, "run_command", {"CommandLine": cmd})])
    params = {"name": "run_command", "parameters": {"CommandLine": cmd}}
    idx = store(132, 3, msg((5, meta(cid))))  # rewritten below if cancelled
    update(idx, "ACTIVE", "tool", tool_name="run_command", tool_info=params)
    end = time.time() + secs
    while time.time() < end:
        if interrupted.is_set():
            db.execute("update steps set status = 6 where idx = ?", (idx,))
            db.commit()
            result("ERROR", error="interrupted", finished_reason="interrupted")
            os._exit(1)
        time.sleep(0.05)
    update(idx, "DONE", "tool", tool_name="run_command", tool_info={**params, "output": "hi\n"})


def handle(raw, n):
    interrupted.clear()
    text = raw.split("\n\n", 1)[1] if raw.startswith("[orbit] ") and "\n\n" in raw else raw
    idx = store(14, 3, msg((5, meta()), (19, msg((2, raw)))))
    update(idx, "DONE", "user_input")
    words = text.split()
    secs = float(words[1]) if len(words) > 1 and words[1].replace(".", "", 1).isdigit() else 10.0
    if text.startswith("split"):
        idx = store(15, 3, msg((5, meta()), (20, msg((1, "first half, second half")))))
        update(idx, "ACTIVE", "agent_response", text_delta="first half, ")
        time.sleep(secs)
        update(idx, "DONE", "agent_response", text_delta="second half")
        result(response="first half, second half\n")
        return
    if text.startswith("slow"):
        respond("Working on it.")
        tool(n, f"sleep {secs:g}", secs)
        respond("slept")
        result(response="Working on it.\nslept\n")
        return
    if text.startswith("fail"):
        result("ERROR", error="boom", finished_reason="error")
        return
    if text.startswith("die"):
        respond("half way")
        os._exit(3)
    tool(n, "echo hi")
    answer = f"answer: {text}"
    respond(answer)
    result(response=answer + "\n")


def worker():
    n = 0
    while True:
        raw = inbox.get()
        if raw is None:
            return
        n += 1
        handle(raw, n)


def main():
    signal.signal(signal.SIGINT, lambda *_: interrupted.set())
    out({"event": "init", "conversation_id": conv, "init": {"cwd": os.getcwd(), "tools": ["run_command"]}})
    w = threading.Thread(target=worker)
    w.start()
    for line in sys.stdin:
        with open(os.path.join(root, "stdin.log"), "a") as f:
            f.write(json.dumps({"line": line.strip()[:200], "pid": os.getpid()}) + "\n")
        m = json.loads(line)
        if "event" not in m:
            result("ERROR", error='stream input message is missing the "event" field', finished_reason="error")
            continue
        raw = "".join(b.get("text", "") for b in m["message"]["content"])
        with open(os.path.join(root, "inputs.log"), "a") as f:
            f.write(json.dumps({"conv": conv, "text": raw, "pid": os.getpid()}) + "\n")
        inbox.put(raw)
    inbox.put(None)
    w.join()
    result("ERROR", error="stream input cancelled: context canceled", finished_reason="interrupted")


main()
