# Orbit Harness Protocol (v1.3)

How a coding agent joins Orbit as a **harness**, next to Claude Code, Codex, agy and OpenCode. Two ways:

- **Speak this protocol** (JSON lines on stdin / stdout). Orbit's generic `ProtocolDriver` runs it; the harness is a
  few lines in a plugin's `plugin.toml`, no Python. Any language; a wrapper around another CLI is the usual shape.
- **Write a driver** (Python) for a CLI that has its own format: subclass `orbit.drivers.base.Driver` (the contract
  in §6). Claude Code, Codex, agy and OpenCode are drivers, because their CLIs are not ours to change (OpenCode's,
  an HTTP server with an event stream rather than JSON lines: [opencode.md](opencode.md)).

The protocol is versioned (`protocol: 1`); fields and message types may be added in a minor version (an agent and
Orbit ignore what they do not know); anything else changes the number.

## 1. Declaring a harness (plugin.toml)

A harness is one of the things a plugin can add ([plugins.md](plugins.md): installing, secrets, services, the HTTP
API). Its declaration:

```toml
[plugin]
name = "acme"
version = "0.1.0"
requires_api = ">=1,<2"

[harness.devin]                  # the harness id: sessions, ⌘N, `orbit-agent new --harness devin`
label = "Devin"                  # shown in the page
command = ["devin-orbit"]        # argv; relative paths are the plugin folder's; PATH is searched
local_only = true                # this machine only: never offered for, or run by, another machine's server
```

Everything else (models, efforts, modes, capabilities) the agent says itself, through `--describe` (§3).

## 2. Running a session

Orbit starts `command` with these arguments, in the session's folder (also its cwd):

| Argument | When |
|---|---|
| `--conversation ID` | continue that conversation (the id the agent reported in `hello`) |
| `--fork ID` | a new conversation that starts as a copy of ID (Clone); `hello` reports the new id |
| `--model M` | a model from `--describe` (omitted: the agent's default) |
| `--mode M` | `default` · `acceptEdits` · `plan` · `bypassPermissions` (map them to your permission levels) |
| `--effort E` | an effort from `--describe` |

Environment: `ORBIT_SESSION` (the Orbit session id), `ORBIT_PORT` (so the agent can run `orbit-agent`), and what
every plugin process gets (plugins.md §3: `ORBIT_URL`, the plugin's token, data folder and secrets); `PATH` starts
with Orbit's `cli/`.

One process serves many requests (it may exit between them: Orbit starts it again with `--conversation`).

### stdin: Orbit → agent (one JSON object per line)

| type | fields | meaning |
|---|---|---|
| `request` | `id`, `text` | the user's request; one at a time (the next comes after its `result`) |
| `steer` | `request`, `text` | a message for the running request (only if `capabilities.steer`): fold it in, no new `result` |
| `interrupt` | | stop the running request; answer with its `result` (`cancelled: true`); keep the process |

The first request of a process starts with Orbit's context line (`[orbit] You run inside Orbit…`, one line) and a
blank line before the user's text: pass it to the model as part of the request.

### stdout: agent → Orbit (one JSON object per line; anything that is not JSON is logged, not shown)

| type | fields | meaning |
|---|---|---|
| `hello` | `conversation`?, `model`?, `commands`?, `terminal`? | at start: the conversation id to continue later, the model in use, slash commands. An agent that learns its id only with the first request sends `hello` again then (the last id counts) |
| `delta` | `text` | answer text as it is written (streaming) |
| `thinking` | `text`, `start`? | the model's thinking as it is written (`start: true`: a new thinking block) |
| `text` | `text` | a whole block of text: replaces its deltas; text before a tool call becomes a note step |
| `tool_start` | `id`, `name`, `summary`?, `input`?, `agent`? | a tool call began (`summary`: one line; else Orbit makes one from `input`); `agent`: it starts a subagent of that type |
| `tool_end` | `id`, `error`, `result`?, `background`? | it finished (`result`: a subagent's report; `background: true`: it runs on, `agent_done` later) |
| `sub_text` · `sub_tool_start` · `sub_tool_end` | `parent` + as above | a subagent at work, under its `tool_start` |
| `agent_done` | `id`, `status`, `result` | a background subagent finished |
| `activity` | `detail` | a short progress line (shown while nothing else moves) |
| `usage` | `context`, `model`? | tokens the last model call read (the context meter) |
| `compaction` | | the conversation was compacted |
| `result` | `request`, `ok`, `text`, `cancelled`?, `error`? | the request finished: `text` is the answer |
| `attention` | `detail` | (1.1) the agent waits for the user, e.g. in its terminal ("waiting for approval"): a mark on its tab, a toast, the bell; `""` clears it |
| `error` | `text` | something went wrong outside a request (shown as the session's error) |

A request always ends with exactly one `result`. Text that arrives between requests is shown as a follow-up of the
last answer. (1.3) An agent may also take a turn of its own between requests (e.g. when a background task it started
finishes): once it runs a tool, that turn gets a card of its own ("On its own", not a request: no edit or resend), with
its steps, subagents and answer; end the turn with a `result` (no `request`). Requests sent meanwhile wait behind it, as
behind a request; Stop sends `interrupt` (no `result` within 5 s: the turn is taken as ended; the process is kept). A
turn that only talks stays follow-ups.

### When the process ends

- During a request: the request is **paused** ("the agent stopped before it finished"); Orbit continues it by itself
  (twice) with a new process and `--conversation`, then waits for Continue.
- Between requests: nothing is lost; the next request starts a new process.
- On Stop: Orbit sends `interrupt`; if no `result` comes within 5 s, it sends SIGINT, then ends the process.

## 3. Questions Orbit asks (separate runs: print JSON, exit 0)

| Run | Prints |
|---|---|
| `command --describe` | `{"protocol": 1, "label"?, "models": [{"value", "label"}], "efforts": [..], "modes": [..], "capabilities": {"steer": bool, "thinking": bool, "subagents": bool}}` |
| `command --history ID` | JSON lines, one per past request: `{"text", "time", "answer", "finished"?, "state"?, "steps"?, "steers"?, "followups"?, "auto"?}`. Steps: `{"id", "kind": "tool" \| "note", "name", "summary", "text"?, "error"?}`. (1.3) `"auto": {"why"?, "what"?}`: a turn the agent began itself (`text`: its title; `why`, e.g. "after a background agent finished", and `what`, the task, label its card) |
| `command --list-conversations [--limit N]` | JSON lines: `{"id", "cwd", "first", "time", "name"?, "live"?}`, newest first: conversations Orbit may resume (⌘O → Other sessions) |
| `command --usage` | `{"plan"?, "limits": [{"label", "used" (0–1), "resets_at"?}], "quota"?}`: the status bar's "Used" |

`--describe` may also say (1.1) `"capabilities": {"terminal": true}` (1.2: `"fork": false` when it cannot fork, §3b notes) and `"skill_dirs": ["<path>", …]` (skill folders
for `/` completion, `<folder>/<name>/SKILL.md`; relative to the session's folder).

`--usage` may also say (1.3) `"quota": "8 USD left"`: one line of what is left, a string (cut to 40 characters),
shown as plain text after the harness's meter in the status bar's "Used" (and in its popover), or alone when it has no
`limits`. An agent that leaves it out is shown as before.

`--list-conversations` (1.3) is run in the background, never waited on: when Resume another session (or ⌘O → Other
sessions) opens and the list Orbit has is older than 30 s, with `--limit 1000`. The dialog shows the list it has and
redraws when the new one comes in; a typed query is matched by the server against the whole list. A slow store makes
the list late, never the dialog. An agent whose plugin has a service can instead push its conversations to the item
source `conversations` ([plugins.md](plugins.md) §5, Item sources): then this command is not run.

`--describe` is required; the rest are optional (exit 2, or print nothing: Orbit leaves that feature out).
Orbit caches `--describe` until the plugin changes.

## 3b. Agents that live in a terminal (1.1)

Some agents are terminal programs: part of them (permission prompts, their own slash commands, a question they ask)
only exists in their terminal. Such a harness declares `capabilities.terminal` and says in `hello` how to reach it:
`"terminal": {"command": ["tmux", "attach", "-t", "<pane>"]}` (any argv that attaches). ⌘T then shows that terminal
under the conversation (the ⌘J pane, the same three states: show + focus → focus → hide; Esc Esc back to the input):
Orbit runs the command on a pty it owns. Closing it only detaches: the agent runs on, ⌘T attaches again. A session
whose harness has none: ⌘T says so. When the agent blocks on the user there, it sends `attention`.

Notes: an agent that only knows its answer at the end may send just `result`; text typed straight into its terminal is
sent as text between requests (a follow-up); keep `steer: false` (typing in the terminal steers); an agent that cannot
fork declares `capabilities.fork: false` in `--describe` (1.2): Orbit then starts it on a new conversation and puts the
conversation so far, between `[orbit-fork]` and `[/orbit-fork]`, before the fork's first request (history drops it).

## 4. A minimal agent

```python
#!/usr/bin/env python3
import json, sys, uuid
if "--describe" in sys.argv:
    print(json.dumps({"protocol": 1, "models": [], "capabilities": {"steer": False}})); sys.exit(0)
out = lambda **m: print(json.dumps(m), flush=True)
out(type="hello", conversation=str(uuid.uuid4()))
for line in sys.stdin:
    m = json.loads(line)
    if m["type"] == "request":
        out(type="tool_start", id="t1", name="Bash", input={"command": "ls"})
        out(type="tool_end", id="t1", error=False)
        out(type="result", request=m["id"], ok=True, text="echo: " + m["text"].split("\n\n")[-1])
```

`tests/fake_protocol_agent.py` is a fuller one (streaming, steer, interrupt, history, a crash) that the tests drive.
`examples/opencode/` is a real one: a plugin that wraps `opencode run --format json` (one opencode run per request,
the id learned from its first event and sent in a second `hello`; history from `opencode export`).

## 5. What Orbit guarantees

- One `request` at a time per process; `steer` only while one runs and only if declared.
- The same conversation id is passed back with `--conversation` after any restart (Orbit's or the process's).
- Nothing from the agent is executed; its text is shown as Markdown (no HTML).

## 6. The driver contract (for a driver written in Python)

Every built-in harness runs on one engine, `orbit.drivers.process.ProcessDriver`, so all of them behave the same
way: one result per request; Stop sends the harness's interrupt (or SIGINT), then SIGINT and kill after 5 s; a
process that dies mid-request pauses the request (continued by itself, twice); one that ends between requests is
replaced by the next request (after Stop, or with exit code 0) or leaves the session "not ready" (any other exit).
A harness on the engine says only: `program()` / `command()`, `request_line`, `steer_line`, `interrupt_line`,
`per_request` (a process per request, the request on its stdin: Codex's `exec` fallback; Codex itself runs one
`codex app-server` per session, JSON-RPC over stdio), `handle(msg)` (its events, through `said`,
`tool_started`, `finish`), and `history()`. `ProtocolDriver` is that, for this protocol.

A turn the harness begins itself (no request: Claude Code wakes when a background task finishes) needs nothing more
from a harness: the engine notices the first turn event (`delta`, `thinking`, `text`, `tool_start`) while idle, emits
`auto` first (`{why, what}` from `self.woke`, which a harness may set when it learns what woke it; Claude Code's task
notification names the finished call), and the harness's ordinary end-of-turn `finish` ends it as a `result` marked
`auto` (as do its process's exit, and a Stop: the interrupt message, never a signal, then 5 s). `busy` stays the
requests' (harnesses key their own bookkeeping on it). The Desk gives such a turn a card of kind `auto` at its first
`tool_start` (the text it said before moves from the last card's follow-ups into it), titled by `why` and `what`, id
after the last request's (5.501: requests keep their history numbers); requests queue behind it; one that only talks
stays follow-ups. A request written while a talking-only turn runs (no card, nothing queued it) takes the turn over,
as before. History rebuilds the same: Claude Code's transcript starts such a turn with a user-role `<task-notification>`.

`orbit.drivers.base.Driver`: one instance per session; the Desk calls `start`, `send`, `steer`, `stop`, `close`,
`alive`, `history`, and receives the events of §2 (stdout table, as `DriverEvent(kind, data)`) plus
`state` (`starting` · `ready` · `not ready` · `stopped`, with `error`) and `info` (`model`, `commands`) through `emit`.
`close` ends the process when the server stops or hands off, or a setting changed (a new process resumes the
conversation); `end` (optional, default `close`) when the user closed the session: a driver whose agent outlives it
(one in a terminal multiplexer, left running by `close` for the next server) ends the agent there.
Such an agent pairs that with two optional classmethods, asked with no instance; `link` is the session's
conversation id as the driver linked it (`link()`), else the Orbit session's id; `session_id` and `state_dir` come as
keywords. (Not named `alive`: that is the instance's own process.)
- `link_alive(link) -> bool`: at the server's start, before the requests a restart cut off go on
  (`Desk.follow_living`): True, the session is followed at once (its driver made and started with `resume=True`, which
  takes the running agent on: no new one), so its state and ⌘T work without a Resume. False (the default): it stays
  stopped until a request or Resume, as before. A driver that does not define it is not asked.
- `end_link(link)`: the user closed a session Orbit has no driver for (not followed since the start): end its agent
  all the same. Default: nothing.

Class attributes and methods say what the harness offers:

| | |
|---|---|
| `harness`, `label` | id, display name |
| `steers`, `efforts`, `modes` | capabilities |
| `local_only` | never run for another machine |
| `identity` | `"orbit-id"` (the conversation id is the Orbit session id: Claude Code) or `"harness-id"` (the harness names it; Orbit keeps the link) |
| `models()` | the models ⌘N offers |
| `external_sessions(limit, skip)` | conversations started outside Orbit (⌘O → Other sessions); the server's index runs it in the background |
| `external_files()`, `external_row(path)`, `external_extra()` | instead, for a store of files: its files `(path, mtime, size)` newest first, one file as a row, what changes without its file (a live process, a name); the index reads a file again only when it changed (`orbit/desk/external_index.py`) |
| `usage()` | the status bar's "Used" figures, or None |
| `skill_dirs(cwd)` | folders of the harness's own skills and commands (`/` completion) |
