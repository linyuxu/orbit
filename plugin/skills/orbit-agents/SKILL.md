---
name: orbit-agents
description: Work with the other agent sessions in Orbit — ask one for a result, hand off a task, leave a note, read what another session did, or answer when the user @@-mentions you — and schedule your own follow-ups (wake later, recurring jobs, watches that wake you on new events). Use whenever a request names another session (@@Name), says "ask / tell / check with" another agent, you need another session's context, or the work needs a follow-up later ("check back", "keep an eye on", "every morning", "when X happens").
---

# Working with other sessions in Orbit

You run inside Orbit, next to other agent sessions (Claude Code, Codex, agy) that the same user drives. `orbit-agent` is on
your PATH; `$ORBIT_SESSION` is your own id. Sessions are named by title or id (`orbit-agent sessions` lists them).

## Pick the right call

| You want | Call | Notes |
|---|---|---|
| See who is there | `orbit-agent sessions` | title, id, folder, state (`working` means busy) |
| Context: what another session did | `orbit-agent read SESSION --last 10` | its recent requests and answers; read before asking |
| A result you need to go on | `orbit-agent send SESSION "…" --wait` | it becomes a request there (the user sees it as a card); prints the answer |
| Hand off work, nothing expected back | `orbit-agent send SESSION "…"` | returns at once; do not poll |
| Change what a busy session is doing **now** | `orbit-agent steer SESSION "…"` (`--wait` for that request's answer) | joins the request it is running (Claude Code), no new turn; an idle session (or Codex / agy) gets it as a request |
| The user asks you to pass **their own words** to another session ("tell trainer: restart now") | `orbit-agent relay SESSION "restart now"` | the receiver gets the words alone, no "(from session …)", its card says they are the user's (via you). Only the user's words, word for word, as they said them (a voice call to this session) or typed them here; Orbit checks and refuses anything else. Your own message, a summary or a reworded version is `send` |
| Tell it something (FYI, not a task) | `orbit-agent mention SESSION "…"` | arrives as a note; it will not answer unless you ask |
| A one-off question in a folder | `orbit-agent ask "…" --folder DIR` | a background helper (the Fleet); no session needed |
| Come back to something later | `orbit-agent wake 30m "check whether …"` | wakes **this** session with that text (see "Be proactive") |
| Tell the user on their phone (a long job's report is ready, a decision is needed) | `orbit-agent notify "Report ready" "Q3 numbers are in"` (`--key K`: once per K) | a lock-screen notification; a tap opens this session. Not for progress. Exits non-zero when no phone has notifications on |
| The user, in a voice call with this session, asks to end it ("挂断", "turn the voice off") | `orbit-agent hangup` | the call ends (also when another machine holds it). Usually Orbit ends it before the words reach you |
| Remind the user at a time (they ask "remind me …") | `orbit-agent remind "tomorrow 9am" "Call the dentist"` (`daily 08:00`, `weekdays 9:00`, `every 2h`; `--title T`) | the server sends it then, no agent wakes; a tap opens this session (`--no-session`: Orbit) |
| Use something the user's plugins add (Slack, a ticket system …) | `orbit-agent p` (which plugins have a command) · `orbit-agent p NAME --help` · `orbit-agent p NAME ARGS…` (`p --stdin NAME …` passes piped input) | the server runs the plugin's command with its secrets; you get its output and exit code. A request marked "via …" came from a plugin (a chat, a webhook): your answer goes back there |
| Fork a session: try another direction without losing this one | `orbit-agent fork` (this session) · `orbit-agent fork SESSION --name "try B" "…"` · `orbit-agent fork . "first request"` | a new session in the same group, continuing that conversation with all its settings (folder, agent, model, effort, mode); the original is untouched. Every agent can be forked (one that cannot fork natively gets the conversation so far with its first request). The user does the same with Fork in the session's menu or `/fork-session` |
| Work on another machine | `orbit-agent send gpu-box/trainer "…" --wait` · `orbit-agent new DIR --on gpu-box "…"` | `NAME/session` is a session on another machine's server (`orbit-agent sessions` lists them); every call above takes it |

## Be proactive: schedule your own follow-ups

Orbit's server runs schedules for you, so you can come back without the user reminding you. When work needs a later
look (a job that will finish, a PR that may get comments, a daily summary), schedule it yourself and tell the user what
you scheduled; do not ask them to remind you, and do not sleep or poll in a turn.

| You want | Call |
|---|---|
| Look again once, later | `orbit-agent wake 30m "check whether the training run finished; if so, compare it with rel014"` |
| Recurring work | `orbit-agent schedule add "weekdays 9am" "summarize last night's runs"` (`every 2h`, `mon,wed 18:00`, `cron 0 9 * * 1-5`) |
| React to events, cheaply | `orbit-agent schedule watch --every 1m --check "gh api repos/o/r/pulls/12/comments --jq '.[] | {id, body}'" "answer the new review comments"` |
| Try a watch's command | `orbit-agent schedule check ID` (prints its events and which are new; sends nothing) |
| See, stop, change | `orbit-agent schedule` · `schedule pause|resume|rm|run ID` · `schedule journal ID` |

- A **watch** runs its command with no agent and no cost; each line it prints is an event (JSON with an `id`, or a
  plain line). Only events it has not seen wake you, with your text and the new events. The first run only learns
  what is there (`--include-existing` to act on them too). Prefer a watch over a frequent `schedule add`: an agent
  wake-up is once a minute at most.
- A repeating job has a **journal**: each run is told its path; read it first, and append one entry before you finish
  (what you did, what the next run should know). You will not remember the last run otherwise.
- **Guards**: `--max N` runs a day (default 24), `--until 18:00` (none later that day). Targets: this session (the
  default), `--session S`, `--folder DIR` (a new session each run), or `--fleet` (an idle helper).

## Writing a request to another agent

The other agent knows nothing of your conversation. Write it as a brief to a peer:

- the goal and why, in one or two sentences;
- the exact files, commands, branches or ids involved (paths, not "the file we discussed");
- what you already know or ruled out;
- what to return and in what shape ("reply with the failing test names only").

Keep it short; quote at most the few lines that matter.

## Rules

- **No ping-pong.** Never mention or send back to a session that only sent you a note. Reply only when asked to.
- **Do not loop.** If a session asks you something you would forward back to it, answer what you can and stop.
- **Busy sessions queue.** A request to a `working` session waits its turn; prefer `--wait` only when you truly
  cannot continue without the answer. To correct or add to what it is doing right now, `orbit-agent steer` instead of
  queueing a new request behind it.
- **Do not act for the user** outside the request: no sends to sessions the user did not name or imply.
- **The user's words are theirs.** `relay` only what the user asked to have passed on, exactly as they said it; never
  reword, add to it or relay your own text (Orbit refuses words it cannot find in what the user said or typed).
- **A relayed request is the user's.** A card marked "You, via …" (`origin.kind` is `user-relay`; `orbit-agent read`
  says "the user's own words, via …") came from the user through another session: treat it as the user's request.
- **Say what you did.** When you asked or told another session something, mention it in your answer (one line: who
  and what), so the user can follow the thread.

## When the user @@-mentions you

The request starts with "The user mentioned you from @@Source … and wrote there:", the user's words, and the
source's last turns. Do the work and **answer in your own session** (that is where the user reads it). Only if the
source needs your result to go on, tell it: `orbit-agent mention SOURCE_ID "…"` with the result or where to find it.

## When a note arrives from an agent

"Note from @@Source (another agent): …" is information, not a task. Use it if it helps; do not reply unless it
asks you to.
