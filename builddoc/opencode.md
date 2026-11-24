# OpenCode as a built-in harness

[OpenCode](https://opencode.ai) (`opencode`, tested with 1.19.6) runs as the fourth built-in harness, next to Claude
Code, Codex and agy: `orbit/drivers/opencode.py`, on the shared engine (`process.py`), behind the driver contract
([harness-protocol.md §6](harness-protocol.md)). The page knows it only as a harness id (`opencode`), its label and
its mark (`web/src/brands.js`, Simple Icons' OpenCode, CC0).

## 1. Which interface

OpenCode offers three ways in. The driver uses the server.

| | What it gives | Taken? |
|---|---|---|
| `opencode serve` | HTTP API (OpenAPI at `/doc`), an SSE stream of every change (`GET /event`): text and reasoning deltas, tool parts with their states, step usage, permission asks and questions, session status; abort, fork, commands, summarize; basic auth | **yes** |
| `opencode acp` | Agent Client Protocol (JSON-RPC on stdio): prompt, cancel, load / fork / resume, permission requests | no: it fits the stdio engine (and the hand-off journal) best, but it carries less: no subagent sessions, no per-step usage, no questions, no commands; and the server is what OpenCode's own TUI attaches to |
| `opencode run` | one prompt, one process, its output | no: no stream of tool states, no permissions, no abort |

## 2. The process

One `opencode serve --hostname 127.0.0.1 --port 0` per Orbit session (like Codex's `app-server`), started through
the launcher (the Harness Server by default). Its environment carries `OPENCODE_SERVER_PASSWORD`: a random password
the driver makes (`secrets.token_urlsafe`); every call sends it as basic auth (user `opencode`), so nothing else on
the machine (nor a web page) can drive the agent. The port is whatever OpenCode takes (4096 when free, else any): the
driver reads it from the first stdout line, `opencode server listening on http://127.0.0.1:PORT` (the engine's
`plain_line` hook: a stdout line that is not JSON). Calls bypass any HTTP proxy.

OpenCode does not end when its input closes; `close()` sends SIGINT at once (then TERM, KILL).

## 3. A session

1. Subscribe: `GET /event` (it opens with `server.connected`). Only then is anything sent, so no event is missed.
2. Open the session's OpenCode session: its own (`GET /session/:id`; the id is linked to the Orbit session in
   `harness-ids.json`, `Driver.link`), else a fork of the source's for a clone (`POST /session/:src/fork`: OpenCode
   copies the conversation, so `native_fork` stays true), else a new one (`POST /session`).
3. `GET /command`: OpenCode's commands and skills, reported as the session's native commands (`info`), plus
   `compact`.
4. `info`: the model (Orbit's, else the session's last), the commands, and `terminal: true`.

A request sent before this is done waits (`_queued`) and goes out when it is.

## 4. A request

| Orbit | OpenCode |
|---|---|
| a request | `POST /session/:id/prompt_async {parts: [{type: text, text}], model?: {providerID, modelID}, agent?}` |
| `/name args` where `name` is one of `GET /command` | `POST /session/:id/command {command, arguments, model?, agent?}` |
| `/compact` | `POST /session/:id/summarize {providerID, modelID}` (Orbit's model, else the last one seen) |
| Stop | `POST /session/:id/abort`; no end within 5 s: SIGINT (the engine), the next request starts a new server on the same session |
| model | `provider/model` (as `opencode models` lists them, the ⌘N list); one without a provider is refused |
| effort | not offered (OpenCode's variants differ per model) |

The request ends when the session goes idle (`session.status` idle or `session.idle`) after it went busy. An error on
the way (`session.error`, or the assistant message's `error`) makes it an error card; `MessageAbortedError` a
cancelled one.

## 5. Events → driver events

| OpenCode | Orbit |
|---|---|
| `message.part.delta` (field `text`) of a text part | `delta` |
| … of a reasoning part | `thinking` (`start` at a new part) |
| `message.part.updated`, text part with `time.end` | `text` (each part once) |
| tool part `running` (its input known) | `tool_start` (name as the other harnesses': bash → Bash, read → Read, …; summary from the input, else OpenCode's title) |
| tool part `completed` / `error` | `tool_end` |
| `task` tool | an Agent step (`agent` = its `subagent_type`); the child session (the tool's `metadata.sessionId`, or a `session.created` whose parent is ours) sends `sub_text`, `sub_tool_start`, `sub_tool_end` under it; its report is the text inside `<task_result>` |
| `step-finish` part | `usage`: input + cache read + cache write of that call; the model from the assistant message |
| `session.compacted` | `compaction` |
| `todo.updated` | `activity`: the item in progress |
| `session.status` retry | `activity`: "retrying: …" |

User-role parts (the request itself, synthetic text) are not shown. Events of other sessions on the same server (a
TUI attached with ⌘T may open others) are ignored.

## 6. Permissions and questions

OpenCode asks only what its config says to ask (by default little: a path outside the project, a doom loop).
Orbit has no approval UI, but OpenCode has one: its TUI, which can attach to this server.

| Mode | Asks answered by Orbit | Agent |
|---|---|---|
| default | none | the user's default |
| acceptEdits | `edit` → `once` | the user's default |
| plan | none | `plan` (OpenCode's read-only planner) |
| bypassPermissions | every one → `once` | the user's default |

Any other ask, and every question (`question.asked`), waits for the user: the session's `attention` says what it asks
("OpenCode asks permission: bash rm -rf build"; the tab's mark, a toast), and ⌘T opens the session's terminal,
`opencode attach <url> --session <id> --password <password>`, OpenCode's own TUI on the same server and session,
where it is answered. `permission.replied` / `question.replied` / `question.rejected` clear the mark; so does the end
of the request. (The password is in that terminal's argv while it runs: visible to `ps` on this machine.)

## 7. Hand-off (a restart of the server)

`reattachable`: the server process runs on in the Harness Server; the checkpoint carries its address and password,
the session, and what this request has shown (part ids said, tool steps started and ended, reasoning parts, the
subagent sessions, open asks, whether the request was posted and went busy). Safe to hand off when the session is
open, nothing waits for it, and a running request was taken (`prompt_async` answered, or the session went busy; a
command or summarize call, which answers only at its end, is taken once the session is busy).

The next server subscribes again and catches up before reading on: the request's messages since its user message
(`GET /session/:id/message`) and each subagent session's, through the same handlers (each part shown once), the
open asks (`GET /permission`, answered by mode or marked), and the session's status (`GET /session/status`: not busy
→ the request ended meanwhile, and ends now). A dropped event stream is caught up the same way.

## 8. History and Resume

OpenCode keeps its sessions in `~/.local/share/opencode/opencode.db` (`$XDG_DATA_HOME`; `$ORBIT_OPENCODE_DB` for the
tests): SQLite, tables `session`, `message`, `part`, ids in columns and the rest as JSON. Orbit opens it read-only
(`mode=ro`) and reads nothing else in it.

- **History** (`history()`): a card per user message with text (the `[orbit]` line stripped); the assistant
  messages after it give the steps (text before a tool is narration), the answer (the text after the last tool),
  `finished`, cancelled / error from the message's error; a `task` step gets its child session's steps and report;
  a compaction (a user message with a `compaction` part) counts, its summary is not an answer.
- **Resume** (⌘O → Other sessions): `external_sessions` lists top-level sessions (no parent, not archived) with a
  request, newest first, with OpenCode's title; the index asks it in the background, as for a plugin's harness.
  Sessions linked to an Orbit session are not offered.
- **Copy resume command**: `cd <folder> && opencode --session <id>`.

## 9. Left out

- An approval UI in Orbit itself (asks go to OpenCode's TUI through ⌘T).
- Effort (OpenCode's model variants), steering (OpenCode has no message that joins a running turn), plan usage
  ("Used": OpenCode has no plan; cost per session is in its store, not shown).
- A server shared by sessions of one folder: one per session keeps Stop, close and the hand-off per session.
- Files and images dropped into a request go as their paths in the text (as for agy), not as OpenCode file parts.
- Background subagents (`/experimental/session/:id/background`): a task runs inside its request.
