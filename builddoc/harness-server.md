# The Harness Server: agents that outlive the Orbit Server

Status: phase 1 shipped (21b471b); phase 2 implemented (§10, notes in §11).

## 1. Why

Every agent process (Claude Code, `codex app-server`, agy, a protocol agent) and every ⌘J shell is a child of the
Orbit Server, joined to it by pipes (a pty for shells). When the server stops (an update, View → Restart Server, a
crash), the pipes close and the agents exit: the running request is cut off (`interrupted`, then "Orbit's server
restarted … Continue it"), and a background subagent is lost (`_end_background`). The server is the part we change
every day, so this happens all the time.

Split it in two:

| Process | Owns | Restarts |
|---|---|---|
| **Harness Server** (`orbit/harness_server/`) | the agent processes: spawn, stdin, an output journal on disk, signals, exit codes, memory; later the ⌘J ptys | rarely: small, stdlib only, almost never changed |
| **Orbit Server** (`orbit/server`, `orbit/desk`, `orbit/drivers` …) | everything else: drivers (each harness's output), sessions, cards, queue, API, pages, plugins, schedules, federation, notes, files | any time: a restart no longer touches the agents |
| Front ends (the app, a browser, the phone) | clients of the Orbit Server, unchanged | any time |

The drivers stay in the Orbit Server on purpose: they change with every harness release. The Harness Server knows
nothing about harnesses: bytes in, bytes out, byte offsets.

**Restart = hand-off, not replay.** A graceful stop of the Orbit Server stops reading each agent at a line boundary
and writes a hand-off: the session's cards exactly as they are, the driver's state, and the byte offset read so far.
The next Orbit Server restores those and reads on from that offset. The Harness Server's journal only has to hold
the output of the few seconds in between. A *crash* of the Orbit Server (no hand-off) is handled as today in phase 1:
its agents are ended and their requests resumed (`interrupted`).

## 2. The Harness Server

`python -m orbit.harness_server --state <state dir>`. One per state dir. It imports nothing outside
`orbit/harness_server/` (stdlib only; `orbit/__init__.py` stays trivial), all at start-up, so a change to the code
on disk has no effect on a running one.

### 2.1 Files and start-up

In `<state>/harness/` (mode 0700, created before anything in it):

- `server.sock` (the socket; bound with a path relative to that directory, so a deep state dir never exceeds the
  104-byte `sun_path` limit of macOS), `server.lock` (an exclusive `flock` held for its life: one instance; a stale
  socket is unlinked only while holding it), `server.pid`, `server.log` (rotated at 5 MB, one old copy kept).
- `procs/<proc>/meta.json`: argv, cwd, tag, started, pid, exit code once exited. Never the environment (protocol
  agents get plugin secrets through it). `procs/<proc>/journal.<n>`: the output journal (§2.3).

Start: `chdir("/")`, its own session (`start_new_session`), stdio to its log, `RLIMIT_NOFILE` raised (a GUI-started
process gets 256; each agent holds four). `procs/` left by an earlier instance is deleted: a process is never adopted
by pid (pids are reused, and it could not `wait()` them).

It exits on `shutdown`, on SIGTERM (both end every process: SIGTERM to each process group, SIGKILL after 3 s), when
its state dir is gone, and after 60 s with no process and no client (so tests and a forgotten state dir leak
nothing).

### 2.2 Protocol

JSON lines over the socket: `{"id": N, "op": …}` → `{"id": N, "ok": true, …}` or `{"id": N, "ok": false, "error"}`.
Every request is answered at once (no op blocks on an agent); a slow or dead client never blocks an agent's pipe or
another client: process readers only append to the journal, and each `read` stream has its own thread.

| op | arguments | does |
|---|---|---|
| `hello` | `client`, `protocol` | `{"version", "protocol", "pid", "started"}`; a major protocol mismatch: the client stops there (§4.1) |
| `claim` | `owner`, `takeover`? | this connection's client owns the server (an Orbit Server's id); only the owner may `spawn` `write` `signal` `read`. A second Orbit Server on the same state dir is refused unless `takeover` (the previous owner's connection is closed first) |
| `spawn` | `argv`, `cwd`, `env`, `tag` | starts it (its own process group, so a group signal can reach what it started; pipes for stdin, stdout, stderr); `{"proc", "pid"}` |
| `write` | `proc`, `data` (base64) | queued to its stdin, in order; answered at once (an error later shows as `exit` or in `list`) |
| `close_stdin` | `proc` | |
| `signal` | `proc`, `sig` (`INT` `TERM` `KILL`), `group` (default false) | to its pid, or its group |
| `list` | `tag`? | `[{"proc", "tag", "pid", "alive", "code", "end", "started", "rss"}]` (`end`: journal length; `rss`: its process tree, from one `ps -axo pid,ppid,rss`) |
| `trim` | `proc`, `before` | drops the journal before that offset (§2.3) |
| `forget` | `proc` | an exited process whose `exit` frame was read: its files go |
| `read` | `proc`, `from` | turns the connection into a stream (below) |
| `shutdown` | | ends every process, then itself |

A `read` stream, one JSON line per frame:

```
{"t": "out", "off": 4096, "ts": 1732300000.12, "b64": "…"}   stdout bytes starting at that offset
{"t": "err", "off": 4180, "ts": …, "b64": "…"}               stderr bytes, the same journal and offsets
{"t": "live", "off": 4500}                                    the reader reached the end: what follows is new
{"t": "exit", "off": 4500, "code": 0, "ts": …}                the process ended; nothing follows
```

Offsets are byte positions in the journal (both streams, in the order they arrived; two pipes give no stronger
order than that). Data is base64 (no encoding issues: a UTF-8 character or a line may be split between frames, the
client reassembles bytes first, then lines, then decodes, as `shell.py` does). `ts` is when the bytes arrived, so a
step's `t0` is right even when read later.

### 2.3 The journal

Appended frame by frame. Segments roll at 64 MB, on a frame boundary. `trim before=N` deletes whole segments that end
before N. The Orbit Server trims at every hand-off and at every request's start (to the offset it has read), so
the journal holds the unread gap and little more. Over 512 MB unread (a client gone for a long time): the oldest
segments go, `meta.json` says `truncated_before`, and a `read` from before it is refused (the session is then
treated as a crash, §4.4).

### 2.4 Not its job

No parsing, no harness knowledge, no network, no auth beyond the socket directory's mode (same user). It never
restarts a process: an exit is reported (`exit` frame, `list`) and stays.

## 3. The seam in the Orbit Server

`ProcessDriver._spawn` is the one place an agent process starts. It takes the process from a **launcher**:

- `DirectLauncher`: `subprocess.Popen` as today. The default everywhere but `orbit serve` (`run.main` turns hosting
  on; tests and `make_server` stay direct unless a test asks), and the fallback whenever a Harness Server cannot be
  used (logged, shown in the status: §6).
- `HostedLauncher` (`orbit/harness_server/client.py`): `spawn` returns a `HostedProcess` with what the drivers use
  of `Popen`: `stdin.write/flush/close` (fire-and-forget: never blocks the Desk's lock on the Harness Server),
  iterating `stdout` / `stderr` by line, `poll()` and `returncode` (answered locally, from the `exit` frame),
  `wait(timeout)`, `send_signal`, `terminate`, `kill`, `pid`. If the Harness Server's connection breaks, its streams
  end and `wait()` returns (as if the process had died: today's paths take over). `offset`: the journal offset just
  after the last complete line handed to the driver. `detach()`: its readers stop at the next line boundary and
  return without reporting an exit.

Signals keep today's meaning: Stop is the harness's interrupt line, then SIGINT to the pid; `close()` is SIGINT if a
request runs, close stdin, TERM to the pid, KILL (pid only: a dev server an agent left behind keeps running as
today). Group kill is only for the Harness Server's own `shutdown`. One difference: agents have no controlling tty
under the Harness Server (as under the app today); under `orbit serve` in a terminal they used to inherit it.

## 4. Lifecycle

### 4.1 Start of the Orbit Server

1. Take an exclusive `flock` on `<state>/server.lock` **before** building the Desk: a second Orbit Server on the same
   state dir (the app starting one while another loads, a manual `orbit serve`) exits at once.
2. Connect to the Harness Server; if none answers, start it detached and wait up to 5 s for `hello`. Then `claim`.
   A major protocol mismatch: the old Harness Server is asked to `shutdown` when no process of it is in a hand-off
   (else: direct mode, and the status says an update is waiting, §6). Never two Harness Servers, never a bypass that
   leaves live agents behind (§4.4).
3. Load the hand-off (§4.3), off the start-up path's critical section: the port opens first; a session being
   restored answers its cards from the hand-off at once.

### 4.2 Graceful stop: the hand-off

Two ways to stop, which the server must tell apart:

- **Restart** (View → Restart Server, an update, `orbit serve` restarted by `ml-services`, SIGTERM): hand off.
- **Stop with the agents** (the app's "Quit Orbit and Stop Agents" ⌥⌘Q, `orbit stop --agents`): close every agent
  as today, then `shutdown` the Harness Server. The app calls `POST /api/shutdown {"agents": "stop"}` (local only)
  instead of sending SIGTERM; SIGUSR1 does the same for scripts.

Hand-off, in `Desk.shutdown`, per session:

1. No new work: `stopping_all` (no `_pump`, no retries, sends refused with 503).
2. A session is **handed off** when its driver is hosted, `reattachable`, its process is alive, and it is not in a
   state that cannot be carried over: a Stop in progress (`stopping`, an escalation timer), a process starting
   (`_launch` running), a request written but not yet acknowledged by the harness. Those are closed as today
   (and resumed after the restart: `interrupted`).
3. For a session handed off: `s.driver = None` first (the emit filter in `_make_driver` then drops anything the old
   driver still says), `HostedProcess.detach()` (its readers stop at a line boundary, without `_exited`), then the
   hand-off is written.
4. Only then `save()`.

The hand-off, `<state>/handoff/<session>.json`, written atomically, one per session:

```json
{"proc": "…", "offset": 48211, "driver": {…checkpoint()…},
 "session": {"state", "model_seen", "commands", "terminal", "context", "attention", "compactions", "auto_turn"},
 "cards": [ …every card of the session, verbatim… ], "busy": true, "time": 1732300000.0}
```

The cards are saved whole (ids, hidden, origins, retries, timestamps, steps and their `t0`, background subagents
still at work, steers, follow-ups, a turn the agent began itself still running): the next server shows exactly what
this one showed, and never rebuilds a handed-off session from `history()` (whose numbering differs from the Desk's
ids, and which marks every step done). Such a turn is handed off too: the engine's checkpoint carries `auto`, so its
result still ends its card; a session cut off with one running ends it (cancelled), and is not "interrupted" (there
is no request to continue).

### 4.3 Start again: taking the hand-off

For each hand-off file (then deleted), before `resume_interrupted`:

- Its process is in `list` and alive: the session gets its cards from the file (`loaded = True`), its session
  fields, and a driver made with `_make_driver(s, resume=True)` and **`driver.attach(proc, checkpoint, busy)`**
  instead of `start()`: `restore(checkpoint)`, the process becomes `proc`/`req_proc`, readers start at `offset`,
  `intro_pending` stays false (the `[orbit]` line was sent to this process already). While it reads the gap the
  session is **catching up**: no `_pump`, new requests wait in the queue, broadcasts for its cards are coalesced; at
  the `live` frame it emits `ready` (never `starting`: that would end its background subagents) and the queue moves.
  What it reads is new output: the driver handles it as live (a Codex approval asked in the gap is answered).
- Its process is in `list` but exited: the same, and the `exit` frame then runs `_exited` as if live (a request cut
  off → paused, continued by itself as today).
- Its process is not in `list` (the Harness Server or the machine restarted meanwhile): the cards are restored, a
  busy one is marked `interrupted` and resumed as today, background subagents end (`lost`).

Driver hooks (base `Driver`, default not reattachable):

| hook | |
|---|---|
| `reattachable` | class attribute; phase 1: Claude Code |
| `checkpoint() -> dict` | the state needed to read on: Claude: `agent_calls`, `resume`, `turn_text`, `busy`; later Codex: thread, turn, RPC counter, calls in flight |
| `restore(dict)` | its reverse, on a new instance |
| `attach(proc, checkpoint, busy)` | in `ProcessDriver` |

### 4.4 Processes nobody attached

After taking the hand-offs, every process the Harness Server lists that was not attached is ended (TERM, then KILL,
waited for, then `forget`) **before** any session may start a new process: otherwise `claude --resume` would start
on a conversation a live process still writes. This covers a crash of the Orbit Server, a hand-off refused
(`truncated_before`), a session closed meanwhile, and in phase 1 every non-reattachable harness. Their sessions are
resumed as today. After a crash nobody set `interrupted`, so it is set here: a session whose process is ended here
while its last card was running is marked `interrupted` (and resumed by `resume_interrupted`).

### 4.5 Ending a process

`close()` as in §3, through the Harness Server; `forget` once its `exit` frame was read.

## 5. Memory (phase 4)

`list` gives each process tree's resident memory: the session menu and Settings → Machines show it ("IDE Dev ·
1.2 GB"). **End idle agents**: every process with no request running and no subagent at work is closed (the session
stays; the next request starts it with `--resume`), by hand or after N idle minutes (a setting, off by default).

## 6. Status and upgrades

`orbit harness-server status | restart [--when-idle] | stop`. (`restart` without `--when-idle` is done: ⋯ / View →
Restart Harness Server…, after a confirmation, `POST /api/harness-server/restart`: `Desk.restart_harness_server`
marks requests at work interrupted, closes every agent, shuts the Harness Server down, `host()`s a new one, and
resumes them; requests sent meanwhile queue.) The status bar's ⋯ says when the Orbit Server runs
direct (and why) or a newer Harness Server is waiting; `restart --when-idle` waits until no request runs and no
subagent works, then restarts it (the agents end; their sessions resume on the next request). Never automatic.

`orbit-agent` retries a refused connection for up to 30 s: an agent that outlives a restart may call it in the gap.
Copy resume command warns when the session's process is alive (hosted or not): two writers on one conversation.

## 7. Phases

1. Harness Server, client, launchers; hand-off for Claude Code; ⌥⌘Q and Restart told apart; server flock;
   unattached processes ended; `orbit-agent` retry. Other harnesses: spawned through the Harness Server but closed
   at a restart as today.
2. Hand-off for Codex (`codex app-server`), protocol agents, agy.
3. The ⌘J shells on ptys in the Harness Server (`spawn` with `pty`, `resize`).
4. Memory and End idle agents.
5. `harness-server restart --when-idle` and the update notice.
6. Maybe: a crash of the Orbit Server recovered too (journal stdin writes as frames with the card id, so the record
   is write-ahead; replay from the last request's start). Not before phases 1–2 have run for a while.

## 8. Tests (phase 1)

Temporary state dirs, the fake agents; nothing touches `~/.orbit` or a running Orbit Server. Hosting is off in
`test_skeleton`; the harness tests turn it on and start and stop their Harness Server themselves. The fake agent
gets a `pid` in its init event and a mode with lots of output (`flood …`).

- Harness Server alone: spawn, write, read from 0 and from an offset, split UTF-8 and split lines, `live` and
  `exit` frames, signal (pid and group), list (alive, code, rss), trim, forget, claim (a second owner refused,
  takeover), one instance under a race of two starters, a slow client does not block another, idle exit, exit when
  its state dir goes, shutdown ends everything.
- Through a Desk, hosted: a request's card is identical to one run direct.
- **Restart mid-request** (`slow …`): Desk A shuts down (hand-off), Desk B starts on the same state dir: the same
  card goes on and completes with the same id; the pid is unchanged; no "server restarted" request is sent; the
  `[orbit]` line is not sent again.
- **Background subagent** (`bgslow …`): after the restart it still runs (`agents` 1) and its report lands on its
  step.
- **Output while down** (`flood …`): the card after the restart equals one from a run without restart.
- **The agent exits while down** (`die …`): the card is paused, then continued.
- **Stop in progress at the restart**: closed and resumed as today, not handed off.
- **⌥⌘Q path** (`/api/shutdown {"agents": "stop"}`): every agent and the Harness Server end.
- **A second Orbit Server** on the same state dir exits at once; the agents are untouched.
- **No hand-off** (a crash: Desk B without A's shutdown): A's processes are ended before B starts any, the running
  request resumes as today.
- **Direct mode** (`ORBIT_HARNESS_SERVER=0`, or none can start): everything as today, all existing tests pass.

## 9. Implementation notes (phase 1)

Decisions taken where the design left a detail open, and the few places it was changed:

- **Files.** `orbit/harness_server/` (`server.py`, `client.py`, `__main__.py`); the Desk side is `orbit/desk/handoff.py`
  (`Desk.host()`, `_take_handoffs`, `_end_unattached`, `_hand_off`). `DirectLauncher` lives in
  `orbit/drivers/process.py` (`ProcessDriver.launcher`; the Desk sets its `HostedLauncher` in `_make_driver`); the
  client never imports from outside its package, so its fallback launcher is handed to it.
- **The journal** is JSON-line frames exactly as a `read` stream sends them (`{"t", "off", "ts", "b64"}`), in
  `journal.<n>` segments; a stream sends the stored lines as they are (only a frame that straddles the offset asked for
  is cut). The 512 MB cap counts the journal's size on disk.
- **The socket path** is bound relative to `<state>/harness`; a client whose absolute path would pass 100 bytes reaches
  it through a symlink of its own in its temp dir (`orbit-hs-<hash>`, checked to be this user's).
- **Owners.** An owner is an id; every connection that claims the same id may act (each `read` stream is its own
  connection and claims first). The owner is gone once its last connection closes. The Orbit Server claims with
  `takeover` at start (it holds `<state>/server.lock`, so an owner still connected is an earlier server that is dead
  or hung). A client whose connection broke reconnects without `takeover` (refused: start nothing) and ends every
  listed process it has no stream for before spawning (never two writers).
- **An exit is reported** once the process was reaped and its stdout reached EOF, or 2 s after it was reaped: a
  process it left behind (a dev server) can hold the pipes open, and its output after the exit is not journaled.
  `shutdown` reaches such leftovers through `ps` (members of the leader's group), never by a pid that may be reused.
- **Order at the hand-off: `detach()` first, then `s.driver = None`** (§4.2 says the reverse). The reader finishes the
  line it is handling before it parks, and the offset is the one after it; with `driver = None` first, that line's
  events would be dropped while the offset counts it. Nothing else emits for a reattachable driver at that point (a
  Stop in progress, a start, an unacknowledged request are not handed off). `detach()` returns None (closed as
  before) when the exit was already read, or when the reader does not park within 5 s.
- **"Not acknowledged"** = no stdout line since the request was written (`ProcessDriver.acked`).
- **Start-up is synchronous**: `host()` (connect, take the hand-offs, end the unattached processes) runs before the
  port opens, not beside it (§4.1.3). It is fast; ending processes nobody attached (after a crash) can add up to 5 s.
- **After a crash** the Desk knows a request was running from `"running": true` in `sessions.json`, now saved when a
  request starts (`_pump`) as well as when one ends.
- **A handed-off session whose process is gone** (not listed, or its journal truncated): its running card becomes
  `paused` (`continued`, so it shows no Continue button: `resume_interrupted` continues it), the session is
  `interrupted`, background subagents end (`lost`).
- **`t0`** of a step read from a hosted process is the frame's `ts` (when the bytes arrived), also live.
- **A major protocol mismatch** retires the old Harness Server at once (its agents end; their requests resume as after
  a crash) instead of the "update waiting" state: that is phase 5. `ORBIT_HARNESS_SERVER=0` likewise stops a
  Harness Server left running for the state dir, so no live agent is bypassed.
- **`POST /api/shutdown`** without `{"agents": "stop"}` is a restart-style stop (hand-off). `GET /api/harness-server`
  says how the agents run (`mode`, `why`, the processes): the status bar does not show it yet.
- **`orbit-agent`** retries only a refused connection, for `$ORBIT_AGENT_WAIT` s (30; a test sets 0). While the old
  server is still stopping, its port stays open (accepted, never served) as before, so a call in that window waits
  for its timeout.
- **Not in phase 1**: the status bar's line (§6), Copy resume command's warning (§6), `orbit stop --agents`.

## 10. Phase 2: Codex, agy and protocol agents handed off; phase 1's leftovers

The Desk side is done (§4, §9): any driver whose `can_hand_off()` is true is handed off. Phase 2 is per driver: be
`reattachable`, say when a hand-off is safe, and `checkpoint()` / `restore()` exactly the state its `handle()` reads.
Nothing is sent to the agent at attach (no `initialize`, no hello: `attach` never calls `spawned`). Every checkpoint
is plain JSON (sets → sorted lists, int keys → strings and back, no object references).

### 10.1 Codex (`codex app-server` only)

- **Exec mode is not handed off** (`self.exec`: a process per request, its subagents followed in the rollouts by
  `SubagentWatch`, a thread of this server). Closed and resumed as today.
- **Safe** (on top of `ProcessDriver.can_hand_off`): `_initialized` and `_thread` set (no handshake or
  `thread/*` in flight), `_queued is None`, not `_stop_pending`; when busy, `_turn` set (`turn/start` was
  answered). Calls still in flight (a `turn/steer`) are fine: `_calls` is carried.
- **Checkpoint**: `rpc_id`, `calls` (`{str(id): method}`), `thread`, `model`, `turn`, `started_items`, `rollouts`,
  and `subs`: `{thread: {id, name, done, last, steps: [..], root: <root thread> | null}}` (a nested subagent's
  `root` is the thread of the top one; `restore` links it back to that dict, so `_sub_event` adds to the same
  steps set). `restore` sets `_initialized = True`.
- What arrives in the gap is handled live by the next server: a `turn/completed` ends the card, a subagent's
  `subAgentActivity completed` ends its step (`agent_done` with its report), a server request (an approval) is
  answered by mode as usual (its reply goes to the same process through the Harness Server).
- The main win: a Codex subagent working in the background after its turn ended (busy false, `_subs` not done)
  keeps running across a restart, and its report lands on its step.

### 10.2 agy

- Safe: the base rule (Stop is SIGINT: `stopping`/`doomed` are already excluded).
- Checkpoint: `conversation`, `parts` (`{str(step_index): [deltas]}`), `started_steps`.

### 10.3 Protocol agents (a plugin's `[harness.<id>]`)

- The hand-off is invisible to the agent (the same process, the same pipes), so every protocol agent qualifies; no
  protocol change and no opt-in.
- Safe: the base rule. Checkpoint: `seq`, `req_id`, `conversation`, `terminal`. (`describe()` is cached per class.)

### 10.3a OpenCode (`opencode serve`)

- Its events come over HTTP (SSE), not stdout: the journal carries only its start-up line. The checkpoint carries
  the server's address and password, its session and what the request has shown (parts said, steps started and
  ended, subagent sessions, open asks, posted / went busy); the next server subscribes again and catches up from
  `GET /session/:id/message`, `GET /permission` and `GET /session/status` ([opencode.md §7](opencode.md)).
- Safe: the base rule, the session open, no request waiting for it.

### 10.4 Phase 1's leftovers

- **Status bar.** The ⋯ menu (or wherever the status bar says how the server is: find the existing place) gets
  one line from `GET /api/harness-server` when `mode` is `direct`: "Agents run in the server: a restart ends them
  (<why>)". Nothing when hosted (the normal case says nothing). Also per remote machine where the status bar
  already lists machines, if cheap.
- **Copy resume command's warning.** `GET /api/sessions/{id}/resume-command` also returns `alive` (its process is
  running: hosted or direct). The toast, in order: remote → "Copied: run it on X"; working or subagents at work →
  as now; alive but idle → "Copied. Its agent is still open here: after the terminal, use Session → Restart so
  Orbit sees what you did" (check that Restart really starts a new process with `--resume`/the thread, and pick
  the words to match what it does); else "Copied: paste it in a terminal".
- **`orbit stop [--agents]`** (new: there is no `orbit stop` yet; `ml-services` and the app stop the server by
  signal). Stops this machine's Orbit Server of the state dir (`--state-dir`, as `serve`): `POST /api/shutdown`
  (local only) — without `--agents` a restart-style stop (the agents are handed off and run on in the Harness Server
  until the next `orbit serve` takes them; a live process keeps it from its idle exit), with `--agents` `{"agents":"stop"}` (every agent and the Harness Server end). Not reachable: SIGTERM /
  SIGUSR1 to the pid holding `<state>/server.lock` (find how the server records its pid; add `server.pid` beside the
  lock if nothing does). Waits until the server is gone (up to 30 s), then says what happened. The help text says
  which form ends the agents.

### 10.5 Tests (phase 2)

In `tests/test_harness_server.py`, the hosted Desk tests, with the existing fakes (`fake_codex_app.py`,
`fake_agy.py`, `fake_protocol_agent.py`; add a slow mode, a background-subagent mode, or a flood mode where one is
missing):

- For each of Codex app-server, agy and a protocol agent: **restart mid-request**: Desk A shuts down, Desk B on the
  same state dir: same pid, the same card completes with the same id, no "server restarted" request, no second
  `initialize`/hello written to the process (the fake can record what it read on stdin).
- **Codex: a background subagent survives a restart** (turn done, subagent still at work): after B attaches, its
  step still runs (`agents` 1) and its report lands on it; a nested subagent's step goes under the top one.
- **Codex: a server request in the gap** is answered by B (the fake asks an approval while A is down and only goes
  on once answered).
- **Codex exec mode** is not handed off (closed and resumed as today).
- **Codex: not safe** (a request queued before the thread opened, or Stop pending): closed and resumed as today.
- Checkpoints round-trip through `json.dumps`/`loads` (a test calls `restore(json.loads(json.dumps(checkpoint())))`
  on a fresh instance and compares).
- Copy resume command: `alive` true/false; `orbit stop --agents` ends the agents and the Harness Server, `orbit
  stop` hands off.

### 10.6 After review: decisions (these override §10.1–10.5 where they differ)

1. **`orbit stop` finds its own server, never port 8770 by default.** Right after taking `<state>/server.lock`
   the server writes `<state>/server.json` `{pid, port, started}` (port filled in once bound; removed at exit;
   `server.pid` stays for the app). `orbit stop` reads it; the server is alive only while the lock is held (a
   non-blocking `flock` probe); it POSTs `/api/shutdown` to that port with `{"pid": <pid>}` and the server refuses
   (409) a pid that is not its own; unreachable → SIGTERM/SIGUSR1 to that pid, only while the lock is held. It waits
   for the lock to be released (30 s) and reports the server's `mode`: in direct mode plain `orbit stop` ends the
   agents too, and says so.
2. **Signals during start-up.** SIGTERM/SIGINT/SIGUSR1 handlers are installed before `host()`; a signal during
   start-up is remembered and acted on as soon as the server is up (a hand-off again, or stop with the agents), so
   `_take_handoffs` is never cut in half.
3. **Plugin tokens survive a restart** (else a handed-off protocol agent's calls back are no longer recognised): a
   plugin keeps its `ORBIT_PLUGIN_TOKEN` across server starts, in `<state>/plugin-tokens.json` (0600). Test it.
4. **B attaches only what it can.** The hand-off records `harness` (and Codex `exec: false`). B attaches only when
   its new driver is a `ProcessDriver`, `reattachable`, of the same harness (Codex: `restore` forces
   `_set_exec(False)`); `attach` is wrapped in a try. Otherwise the session is cut off (`interrupted`, resumed as
   today) and the process is ended with the unattached ones. Plugin gone, `ORBIT_CODEX_EXEC` toggled: tested.
5. **No Stop or steer slips into a hand-off.** `Desk.stop` and steer refuse (503) while `stopping_all`, like send;
   the `can_hand_off()` check and `detach()` run under the driver's lock; the Stop escalation timer does nothing to a
   detached process.
6. **Checkpoints are `super().checkpoint()` plus** the driver's own: everything its `handle`, `steer_line`,
   `interrupt_line`, `begin`, `_exited`, `terminal_command` and `answer_so_far` read (`turn_text` comes from the
   base). Codex nested subagents (`{id, root, done}` entries) are saved with `root` = the top entry's thread
   (found by identity) and linked back in a second pass. Protocol also carries `described` (B sets the class cache
   from it when empty, so `host()` never runs `--describe`).
7. **Catching up ends only at `live` or the process's end.** `on_live` emits `state: ready` with `live: true`; the
   Desk clears `catching_up` on that, or on a state other than ready (`stopped`, `not ready`); a protocol agent's
   `error` (`state: ready` without `live`) does not end it.
8. **Copy resume command's `alive`** = the session's driver has a process that is running (`proc.poll() is None`),
   or the session is catching up. Toast for alive but idle: "Copied. Its agent is still open here: after the
   terminal, use Session → Restart so it continues from what you did there (those turns won't show as cards)."
9. **Status bar**: this machine only (`/api/harness-server` is local-only); no per-remote line.

## 11. Implementation notes (phase 2)

Where §10 left a detail open, and the few places it was changed:

- **The detach is two steps.** `ProcessDriver.detach_for_hand_off()` runs `can_hand_off()` and
  `HostedProcess.begin_detach()` (the reader stops at the next line boundary) under the driver's lock, then waits for
  the reader to park *outside* it (§10.6.5 says both under the lock): the reader may need that lock to finish the line
  in hand (`finish`, Codex's `_rpc`), and would then miss the 5 s wait and the session be closed instead. What that
  line writes (Codex answering an approval) still goes out; once parked the process is `handed_off` and `_write`
  refuses anything more.
- **No Stop, steer or request slips in.** `Desk.shutdown` sets `stopping_all` under the Desk's lock; `Desk.stop` and
  `steer` check it under that lock (503) and call the driver under it, as `_pump` already sends. A write from the
  Desk lands before the hand-off begins or is refused. The Stop escalation timer returns for a `detached` process.
  `resume_interrupted` does nothing once `stopping_all` (a stop right after start kept the marks for the next server).
- **B attaches only what it can** (`Handoff._attachable`): the session's driver class is a `ProcessDriver`,
  `reattachable`, and the hand-off's `harness` is the session's; a hand-off without `harness` (written by phase 1:
  only Claude Code then) counts as the session's, so the restart onto this version still hands Claude off. Making the
  driver, `launcher.attach` and `driver.attach` are in one `try` (any exception): the stream is closed, the session cut
  off, its process ended with the unattached ones.
- **`Driver.primed(checkpoint)`**, a classmethod run before the driver is made: `ProtocolDriver.__init__` calls
  `describe()`, so the class cache has to be set from the checkpoint's `described` before, not in `restore`.
- **Codex.** `can_hand_off` as §10.1 (and never exec). The checkpoint carries `exec: false`; `restore` refuses any other
  value (cut off) and forces `_set_exec(False)` (`ORBIT_CODEX_EXEC` turned on meanwhile: tested). A nested subagent's
  entry is `{id, root: <top thread>, done}`, found by identity; one whose top entry is gone is dropped.
- **agy** has no delta events (a step's text is joined at its end), so `parts` is what keeps an answer whole across a
  restart (tested with an answer split around one).
- **Copy resume command**: `alive` = `ProcessDriver.running()` or catching up. Session → Restart closes the process and
  starts one on the same conversation (`--resume`, Codex `thread/resume`, agy and protocol `--conversation`), which is
  what the toast tells the user to do.
- **`orbit stop`** (`cmd_stop` in `orbit/cli/orbit.py`): the lock probe opens `server.lock` without creating it (none:
  not running). `server.json` is written once the signal handlers are installed (a signal sent as soon as it appears
  is not fatal), `port: null` until bound, removed at exit only when its pid is this process's. The POST carries
  `{"pid"}` (and `"agents": "stop"`); the answer carries `mode`, `why`, `pid`. Refused or unreachable (a 409: another
  server on that port), it signals server.json's pid while the lock is held; a lock held without server.json (an
  older server) is reported, never signalled by a guessed pid. After a signal the mode is unknown, and it says what
  each mode means.
- **Signals at start-up**: one handler for SIGTERM, SIGINT and SIGUSR1 from right after the lock; it records the
  wish (SIGUSR1 wins) and, once the HTTP server exists, stops it. A wish recorded during start-up is acted on just
  before `serve_forever` (tested: a SIGTERM while the hand-offs are taken; they are handed off again).
- **Plugin tokens**: `<state>/plugin-tokens.json` `{plugin: token}`, 0600, rewritten at each `plugins.load` (tokens
  of plugins not loaded this time are kept); not writable: logged, and they change at the next start as before.
- **Status bar**: the first, disabled line of the status bar's ⋯ menu, from `GET /api/harness-server` asked at each
  `hello` of the event stream (page load and every server restart). This machine only (§10.6.9).
- **Fakes**: `fake_codex_app.py` `slow N`, `gapask N`, `nested`, `$FAKE_THREAD_DELAY`, `stdin.log`; `fake_agy.py`
  `slow N`, `split N`, `stdin.log`, pids in `inputs.log`; `fake_protocol_agent.py` `slow N`, `stdin.log`, `token.log`.
- **Not in phase 2**: the ⌘J shells (phase 3); a line per remote machine (§10.6.9).

