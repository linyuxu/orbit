# To do

Work agreed on but not started. When an item is done, remove it here and record it in CHANGELOG.md.

## Later (decided 2026-11-22: not now)

### Agents outlive a server restart

**Problem.** Every harness process (`claude -p …`, codex, agy, protocol agents) is a child of the Orbit server.
When the server stops, `Desk.shutdown()` (orbit/desk/desk.py) closes each driver (`ProcessDriver.close`, orbit/drivers/process.py):
- stdin is closed;
- then terminate after 3 s;
- then kill after 3 more seconds.

A killed server also takes them down, because their pipes reach EOF.

After the restart the conversation itself comes back: the transcript is on disk, the harness is restarted with `--resume`, and `resume_interrupted` carries on a request that was cut off. **Anything that lived only inside the harness process is lost:**
- Claude Code's background sub-agents;
- background Bash tasks;
- Monitors;
- in-memory state.

"Wait until no session is working" does not protect them. "Working" means that a request is running. A session whose turn has ended but whose background agent is still running counts as idle, so the restart goes ahead and kills it. This happened on 2026-11-22: a UI-audit sub-agent was killed by a restart.

**Direction (recommended).** Decouple a harness's life from the server's:
- Start each harness detached, in its own session (`start_new_session`), under a tiny per-session holder (the idea of dtach/tmux). The holder owns the harness's stdin/stdout and exposes them on a unix socket under the state dir (`<state>/run/<session>.sock`), keeping a bounded backlog of output written while no server is attached.
- On start, the server reattaches to the live holders instead of spawning new harnesses. It replays the backlog through the driver's normal parsing, then resumes streaming. Only sessions without a live holder are spawned (with `--resume`) as now.
- **Stop** (close a session, the stop button, SIGINT) goes through the holder. **Server shutdown** detaches and does not kill. An explicit "stop all agents" remains for quitting for real.

Open questions:
- holder liveness and cleanup (a holder whose harness died; stale sockets);
- per-request harnesses (codex, `per_request`), which may not need it;
- the ⌘T terminals (`shells`), which could use the same holder;
- remote machines (federation): nothing changes;
- Windows: out of scope.

Tests:
1. Restart the server mid-request: the request finishes, with no `--resume` and no duplicate card.
2. Restart while idle with a background task in the harness: the task survives.
3. Kill the holder: the session falls back to `--resume`.

**Stopgap meanwhile.**
- Web-only changes do not need a server restart (reload the page).
- Do not restart the server while any session has background work running.
- Optional: a "background tasks" signal in Harness Protocol, counted as busy by the restart wait. Check first whether Claude Code's stream-json reports background tasks at all.


## To Do (from User)
* Integrate GPT Live 支持实时语音
* Enhance the orbit cross-agent collabration, should be able to know the position, info of orbit managed agent