#!/usr/bin/env python3
"""A stand-in agent that speaks the Orbit Harness Protocol (builddoc/harness-protocol.md), for the tests.

Conversations are JSON files in $FAKE_PROTOCOL_DIR (one per id). Requests, by their first word:
  (anything)  stream "answer: <text>" with a Bash step
  slow [N] ...  a long step that waits for steer / interrupt (up to N s, 8)
  agent ...   a subagent with two steps of its own
  fail ...    a failed result
  die ...     the process exits in the middle of the request
Every process logs its argv to $FAKE_PROTOCOL_DIR/argv.log; a session's process also every stdin line (stdin.log,
with its pid) and its ORBIT_PLUGIN_TOKEN (token.log).
"""
import json
import os
import select
import sys
import time
import uuid

STORE = os.environ.get("FAKE_PROTOCOL_DIR", "/tmp/fake-protocol")
os.makedirs(STORE, exist_ok=True)
argv = sys.argv[1:]
with open(os.path.join(STORE, "argv.log"), "a") as f:
    f.write(json.dumps(argv) + "\n")


def arg(name):
    return argv[argv.index(name) + 1] if name in argv else None


def path(conv):
    return os.path.join(STORE, f"{conv}.json")


def load(conv):
    try:
        with open(path(conv)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"cwd": os.getcwd(), "turns": []}


def save(conv, d):
    with open(path(conv), "w") as f:
        json.dump(d, f)


if "--describe" in argv:
    print(json.dumps({"protocol": 1, "label": "Fake Agent", "models": [{"value": "fast", "label": "Fast"}, {"value": "deep", "label": "Deep"}],
                      "efforts": ["low", "high"], "capabilities": {"steer": True, "thinking": True, "subagents": True,
                                                                     "terminal": bool(os.environ.get("FAKE_TERMINAL"))},
                      **({"skill_dirs": [".fake/skills"]} if os.environ.get("FAKE_TERMINAL") else {})}))
    sys.exit(0)
if "--history" in argv:
    for t in load(arg("--history"))["turns"]:
        print(json.dumps(t))
    sys.exit(0)
if "--list-conversations" in argv:
    time.sleep(float(os.environ.get("FAKE_LIST_SLEEP") or 0))  # (a slow store: the dialog must not wait for it)
    rows = []
    for name in os.listdir(STORE):
        if name.endswith(".json"):
            d = load(name[:-5])
            if d["turns"]:
                rows.append({"id": name[:-5], "cwd": d["cwd"], "first": d["turns"][0]["text"], "time": os.path.getmtime(path(name[:-5])),
                             "name": d.get("name", "")})
    for r in sorted(rows, key=lambda r: -r["time"])[: int(arg("--limit") or 40)]:
        print(json.dumps(r))
    sys.exit(0)
if "--usage" in argv:
    print(json.dumps({"plan": "Team", "limits": [{"label": "Day", "used": 0.25, "resets_at": None}],
                      "quota": os.environ.get("FAKE_QUOTA", "8 USD left")}))
    sys.exit(0)

conv = arg("--conversation")
if not conv:
    conv = str(uuid.uuid4())
    src = arg("--fork")
    d = load(src) if src else {"cwd": os.getcwd(), "turns": []}
    save(conv, d)
state = load(conv)


def out(**m):
    print(json.dumps(m), flush=True)


def user_text(text):  # what the user wrote (the [orbit] line goes to the model, not into the store)
    return text.split("\n\n", 1)[1] if text.startswith("[orbit] ") and "\n\n" in text else text


out(type="hello", conversation=conv, model=arg("--model") or "fast", commands=["/review"],
    **({"terminal": {"command": ["sh", "-c", "cat"]}} if os.environ.get("FAKE_TERMINAL") else {}))  # (1.1) ⌘T attaches with cat
pending = []  # messages read while a request runs (steer / interrupt)
with open(os.path.join(STORE, "token.log"), "a") as f:
    f.write(json.dumps({"token": os.environ.get("ORBIT_PLUGIN_TOKEN", ""), "pid": os.getpid()}) + "\n")


def logged(line):
    if line:
        with open(os.path.join(STORE, "stdin.log"), "a") as f:
            f.write(json.dumps({"line": line.strip(), "pid": os.getpid()}) + "\n")
    return line


def read_line(timeout):
    r, _, _ = select.select([sys.stdin], [], [], timeout)
    return logged(sys.stdin.readline()) if r else None


while True:
    line = pending.pop(0) if pending else logged(sys.stdin.readline())
    if not line:
        break
    m = json.loads(line)
    if m["type"] != "request":
        continue
    rid, text = m["id"], user_text(m["text"])
    first = text.split()[0] if text.split() else ""
    turn = {"text": text, "time": time.time(), "steps": [], "steers": []}
    if first == "attention":  # (1.1) it waits for the user in its terminal; "attention clear" clears it
        out(type="attention", detail="" if "clear" in text else "waiting for approval")
        out(type="result", request=rid, ok=True, text="flagged")
        continue
    if first == "die":
        out(type="text", text="half way")
        os._exit(3)
    out(type="thinking", text="", start=True)
    out(type="thinking", text="considering")
    words = text.split()
    secs = float(words[1]) if len(words) > 1 and words[1].replace(".", "", 1).isdigit() else 8.0
    if first == "slow":
        out(type="tool_start", id="s1", name="Bash", input={"command": f"sleep {secs:g}"})
        steered, cancelled, end = [], False, time.time() + secs
        while time.time() < end:
            ln = read_line(0.1)
            if ln is None:
                continue
            if not ln:
                break
            x = json.loads(ln)
            if x["type"] == "steer":
                steered.append(x["text"])
                turn["steers"].append({"text": x["text"]})
                break  # fold it in: finish now
            if x["type"] == "interrupt":
                cancelled = True
                break
            pending.append(ln)
        out(type="tool_end", id="s1", error=cancelled)
        if cancelled:
            out(type="result", request=rid, ok=False, cancelled=True, text="")
            continue
        answer = "done" + "".join(f" · steered: {s}" for s in steered)
    elif first == "agent":
        out(type="tool_start", id="a1", name="Agent", agent="explorer", input={"description": "look around"})
        out(type="sub_tool_start", parent="a1", id="a1s1", name="Read", input={"file_path": "README.md"})
        out(type="sub_tool_end", parent="a1", id="a1s1", error=False)
        out(type="sub_text", parent="a1", text="found the README")
        out(type="tool_end", id="a1", error=False, result="the README says hello")
        answer = "the subagent looked"
    elif first == "fail":
        out(type="result", request=rid, ok=False, error="boom", text="")
        continue
    else:
        out(type="tool_start", id="b1", name="Bash", input={"command": "ls -la"})
        out(type="tool_end", id="b1", error=False)
        turn["steps"].append({"id": "b1", "kind": "tool", "name": "Bash", "summary": "ls -la"})
        answer = "answer: " + text
    out(type="usage", context=1234, model=arg("--model") or "fast")
    for i in range(0, len(answer), 8):
        out(type="delta", text=answer[i:i + 8])
    out(type="text", text=answer)
    turn.update(answer=answer, finished=time.time())
    state["turns"].append(turn)
    save(conv, state)
    out(type="result", request=rid, ok=True, text=answer)
