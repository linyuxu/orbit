# orbit

An AI IDE.

## Setup

Python 3.12 and [uv](https://docs.astral.sh/uv/); no third-party packages.

```sh
uv sync
```

## Command-line tools (`cli/`)

Every command lives in `cli/` (thin launchers that run the repository's own Python; the code is in `orbit/`):

| Command | For | Does |
|---|---|---|
| `cli/orbit serve` | people | the server, the backend (the app starts it when nothing listens on 8770; without the app, e.g. on Linux, run it yourself) |
| `cli/orbit tools install` | people | makes Orbit's tools available to every harness in any terminal: `~/.local/bin/orbit-agent` and `~/.local/bin/orbit`, and the orbit-agents skill in `~/.claude/skills`, `~/.codex/skills`, `~/.gemini/skills` (agy). `uninstall` removes exactly those links; `status` shows them |
| `cli/orbit stop [--agents]` | people | stops the server of the state dir (`--state-dir`): its agents run on for the next `serve`; `--agents` ends them too |
| `cli/orbit serve --remote` · `url` · `token` · `remote add\|ls\|rm` | people | other machines: see below |
| `cli/orbit-agent …` | agents | talk to the server's sessions: `sessions`, `send`, `steer`, `read`, `mention`, `wait`, … |

After `orbit tools install`, a `claude`, `codex` or `agy` started in any terminal knows `orbit-agent` and can send to, steer
and read the agents the server runs; it signs as "a terminal session in <folder>" (`$ORBIT_AGENT_NAME` overrides). It
cannot be written to (the server does not own its process): ⌘O → Terminal attaches it for two-way work.

## The orbit server and other machines

The **orbit server** (`orbit serve`) is the backend: it owns the agents, their queues, schedules and state
(`~/.orbit`). Everything you look at is a client of one server over HTTP: the app, a browser, later a phone, and
`orbit-agent`. The app is only the face: it shows this Mac's server by default (and starts it when nothing listens on
8770), or any other server you point it at (**Orbit → Connect to Server…**: a URL from `orbit url` on that machine).

A server can also reach other servers. Their sessions then show in it as its own, with live answers, in your own groups:
a group is what the work is about, the machine is where it runs. Each session from another machine carries a small tag
(`gpu-box`; struck through while gpu-box is unreachable), any session can go in any group (where you put gpu-box's sessions is
kept here, not on gpu-box), and the Sessions panel's chips (All · This Mac · gpu-box) show one machine at a time. New session
(⌘N) has a Machine field; sending, steering, renaming and closing a gpu-box session all happen over there. So the app
connected to this Mac's server sees and drives every machine's agents, and so do the agents (`gpu-box/trainer`).

```sh
# on the other machine (a Linux box, no app): keep the server running in tmux
tmux new -d -s orbit 'cd ~/orbit && cli/orbit serve --remote'   # or --bind 100.x.y.z (only that address)
cli/orbit url                                                    # http://100.x.y.z:8770/?token=…  (keep it private)

# here
orbit remote add gpu-box 'http://100.x.y.z:8770/?token=…'           # or in the app: ⌘, Settings → Machines → Add
orbit-agent sessions                                             # also lists gpu-box/<session> (id gpu-box~<id>)
orbit-agent new ~/src/model --on gpu-box --name trainer "run the training smoke test"
orbit-agent send gpu-box/trainer "status?" --wait                   # send, steer, read, wait, history: NAME/session
```

**HTTPS.** Over Tailscale, the server is reached at `https://<machine>.<tailnet>.ts.net:8443` with a real certificate:
`orbit https on` (or Settings → Machines → HTTPS) has `tailscale serve` hand that address to the server on 127.0.0.1,
which keeps listening there only, so this machine's own window works with Tailscale down. Plain HTTP from the network
is then sent to the HTTPS address. A phone needs HTTPS (its home-screen app, the microphone, notifications); machines
reach each other by it too. A userspace tailscaled is named in `~/.orbit/config.toml`, e.g.
`tailscale = "tailscale --socket ~/.local/state/tailscale/tailscaled.sock"`. Certificates must be on for the
tailnet (Tailscale admin console → DNS → HTTPS Certificates).

```sh
orbit https on        # https://gpu-box.tailnet.ts.net:8443/?token=…  (another machine: remote add; the phone: /m/)
orbit https status    # or off
```

**Settings → Machines** (⌘,) does both sides in the app: **This server** turns HTTPS on and shows its URL to Copy, and
**iPhone…** a QR code that opens the phone's app (Safari, then Share → Add to Home Screen); **Plain HTTP on the
network** listens on the Tailscale address, else the LAN's (for a machine without Tailscale's certificates);
**Other machines** lists the servers this one reaches (reachable, sessions), Remove, and Add (paste a URL).

Requests from other machines need the token (`Authorization: Bearer`, `?token=`, or the cookie a browser gets when it
opens the URL); requests from the machine itself need nothing new. A request that names another host counts as another machine's even when a
proxy on this machine hands it over from 127.0.0.1 (Tailscale in userspace mode, an ssh tunnel): there the server can stay
on 127.0.0.1 (`orbit serve`), and the other side connects to the proxy's address with the token. The token is `<state dir>/token` (0600; `orbit token
--rotate` replaces it); `<state dir>/remotes.json` (0600) keeps the servers this one reaches. One hop: a server shows the
sessions of the servers it reaches, not theirs in turn, so two servers that reach each other do not loop.

Another machine's files work the same as this one's: a path over there is written `/@gpu-box/<its path>` here (shown as
`gpu-box:~/…`), so a gpu-box session's tree, file tabs (open, edit, save), ± Review, file links and images in answers all
come from gpu-box, and you edit and save them in Orbit's editor; they open only there (not in an outside editor or
Finder). ⌘J opens the session's shell on gpu-box. Everything goes over gpu-box's HTTP service (its URL and token); no SSH is involved. A
file pasted or dropped into a gpu-box session is saved on gpu-box. Paths written into a request (`/@gpu-box/…`) reach the
agent as its own paths. Plain HTTP (without `orbit https`): use it only on a network that is already private and
encrypted (Tailscale is).

## App

```sh
app/build-app.sh        # builds dist/Orbit.app and installs ~/Applications/Orbit.app
```

Open **Orbit** from Spotlight, Launchpad or `~/Applications`. The app starts the server from this checkout
(or reuses one already on port 8770). **The server is the backend: agents and schedules keep running when the
window closes or the app quits (⌘Q)**, and the next launch reattaches to it. **Quit Orbit and Stop Agents** (⌥⌘Q)
stops everything; View → Restart Server works on one an earlier launch left running (`~/.orbit/server.pid`).
**Agents outlive a restart of the server**: they run in the Harness Server (`orbit/harness_server/`, a small daemon
the server starts), so View → Restart Server or an update hands each session (Claude Code, Codex, agy, a plugin's
agent) over to the next server, which reads on where the last one stopped: a request at work and its background
subagents go on (`builddoc/harness-server.md`). `orbit stop` stops the server of a state dir and leaves its agents
running for the next `orbit serve`; `orbit stop --agents` ends them too. `orbit harness-server status | restart | stop`
(restart, also ⋯ → Restart Harness Server… and View → Restart Harness Server…, after a confirmation: every agent
process ends and a new Harness Server starts; a request at work goes on by itself; stop: every agent ends, their
sessions resume on the next request). `ORBIT_HARNESS_SERVER=0` runs the agents as the
server's children, as before (the status bar's ⋯ then says a restart ends them).
The Dock icon brings the window back and shows the unread count. The window keeps its size and place.
There is no native title bar: the tab row is the title bar (drag its empty space to move the window, double-click to
zoom), with the window buttons at its left end.
The File and Help menus run the page's own shortcuts (New Session, Open, Schedules, Calendar, Keyboard Shortcuts…);
⌘, opens Settings: a sidebar of sections (General: Open files in, Calendar, shortcuts · Appearance: text weight,
fonts (interface, conversation, notes, code) and sizes · Machines · About), each change applied at once. It runs your working tree, so
edits need no rebuild:

| Change | Do |
|---|---|
| The page (`web/`) | ⌘⇧R (View → Reload UI), drafts are kept |
| Python (`orbit/`) | View → Restart Server |
| The shell (`app/src/main.rs`) | `app/build-app.sh` again |

The server log is `~/.orbit/server.log` (View → Show Server Log). The page logs its errors, slow file opens and
folder lists (over 300 ms, with the fetch's share) and refused empty saves to `~/.orbit/page.log`. A blank editor never
saves over a file that has content: auto-save skips it and ⌘S asks.

## Shell, quick ask, attachments

- **⌘J** opens a shell in the session's folder (your login shell on a pty the server owns, shown with xterm.js
  below the status bar). Same three states as ⌘\`; Esc goes to the shell, Esc Esc back to the input; ⌃⌥K / ⌃⌥J
  resize it; Kill, Open in iTerm. The shell outlives ⌘⇧R and ends with its session. `$ORBIT_SESSION` is set in it.
- **⌘⇧V voice call** (GPT Live; the microphone beside Send): talk with the session in front. The voice answers what
  it can itself and hands the rest to the session as requests (cards "via Voice"); their answers come back spoken, in
  short. The button says connecting · listening · speaking · standby (after 30 s of quiet, cheaper; speech wakes it),
  a red dot while the microphone is open; captions run in the conversation, and on hang-up the call's transcript stays
  as a note card. Needs an OpenAI API key (Settings → General: it stays on this machine, the page never sees it).
  Each call is logged in `~/.orbit/live/<call>.jsonl` (stages, times, ids; never the key). Design: `builddoc/gpt-live.md`.
- **⌘K** quick ask: a one-off request to a Fleet helper (a background agent in the current folder; no tab, its
  answer shows in the side tab without taking the keyboard) or to any open session. Up to 3 helpers per folder.
- **⌘V / drop / attach**: drop any file from Finder on the conversation or the input: it goes into the request as
  `![name](path)` (an image shows in place) or `[name](path)` (in the app the real path; a paste or a browser drop is
  saved under `~/.orbit/uploads/` first). Dropped on a Markdown file it is copied beside it and linked (`attachments/` +
  `![[name]]` in the vault); on a code file its path is typed in; anywhere else it opens in a tab.
- **The input** is the same live-preview editor as notes. `@` picks a file or folder of the session's folder, `/` a
  skill or command (the folder's, yours, and Orbit's own in `skills/`), `@@` another session; ↑ in the empty input
  brings back an earlier request.
- **❝ Quote**: select text in an answer, a file or a side tab: the Quote button (or ⌘E → Quote into / Quote to ▸)
  puts `> path:lines` (or `> session #id`) and the quoted lines into the input.
- **`/` commands**: Orbit's own (`/model NAME [EFFORT]`, `/effort LEVEL`, `/mode NAME`, `/status`), the folder's and
  your skills, then the harness's native commands as its process reports them (`/compact`, `/context`, `/review` …,
  sent to the agent as typed). `/model`, `/effort` and `/mode` (also a click on the model or the mode chip) restart the
  agent on the same conversation; refused while a request runs.
- **Steering**: while the agent works, ⌘⏎ queues the next request and ⌘⇧⏎ steers: the message joins the running
  request (Claude Code folds it in at its next step; the card shows it under the request). A queued card has
  **steer now** and **withdraw** (back into the input, to edit).
- **Models by name**: ⌘N lists `Claude Opus 5.5 · claude-opus-5-5` …, an Effort (low … max), and "Default" says what it
  ran last (the init event names the model); the input shows what the request runs on (`Claude Opus 5.5 High`).
- **A file changed on disk while you edited it**: Reload from disk · Overwrite with mine · Save mine as a copy
  (`<name> (mine).md` beside it, opened next to the file).
- **Plan usage** in the status bar: `Used  Claude ▬ 94% 12h 25m · 86% Fable  Codex ▬ 40% 5d 8h` (Claude from its
  `/usage`, no model call; Codex from its last turn's rate limits; every 5 min, Refresh in the popover).
- **Resume any other session**: ⌘O → Other sessions (or + in the Sessions panel → Resume another session…) lists
  everything that can be resumed and is not in the Sessions tab, newest first: closed unnamed Orbit sessions, and
  Claude Code and Codex conversations that are no Orbit session (a terminal, an IDE, anywhere), on every connected
  machine (tagged with its name). A conversation named there (Claude Code's /rename, a Codex thread name) shows and is
  found by that name, else by Claude Code's own title. Picking one makes an Orbit session on that machine that resumes
  it, under that name, its history as cards. (Orbit's own names stay Orbit's: it reads those stores, never writes them.) One a process still runs is marked "running elsewhere" (both would write to it: quit it there first).
  It opens at once: the server keeps an index of them (a file is read again only when it changed; a plugin agent's
  list comes in the background), and a typed query searches all of them, not only the newest.
- **Fleet**: the helper sessions (⌘K quick asks, meeting Prep, `--fleet` schedules, `orbit-agent ask`) are listed
  under **Fleet** at the bottom of the Sessions panel, with what each did last; one opens beside, in the side pane.
- **Groups**: a fresh Orbit has Main, Projects, Personal; "New group…" sits under the groups in the Sessions panel
  (also the + button, the group chip, and a group's right-click menu: rename, move, delete).
- **While a request runs** the card streams the text as it is written and the model's thinking, and its steps stay
  open (Claude Code, `--include-partial-messages`); they fold when it ends.
- **The Files tree** follows what is in front: the session's folder, or for a file outside it the vault (a note) or
  its git repo.
- **Agents working together**: `plugin/skills/orbit-agents/SKILL.md` (a Claude Code plugin Orbit loads with
  `--plugin-dir`, nothing written to `~/.claude`) tells an agent when to `orbit-agent send` (a task, `--wait` for
  the answer), `orbit-agent mention` (a note), `orbit-agent read` (context), and how to answer an @@ mention without ping-pong; the
  `[orbit]` line points every agent at it, and `/orbit-agents` in the input hands it to any harness.
- **Markdown live preview**: marks hide off the cursor line; images and `![[embeds]]`, `$math$`, tables (a grid),
  `[ ]` checkboxes (click toggles), `==highlight==` and `#tags` render; a click on a rendering shows its source.
  Renaming a note rewrites the links to it; deleting in the vault moves to its `.trash`.
- **Side tabs**: each tab keeps its own side tabs, small tabs in the side pane's head. ⌥⇧[ ⌥⇧] switch them (from the
  main page or inside the pane), ⌘W / × closes the shown one, ⌃X 1 closes them all. A tab remembers whether the
  keyboard was in its main pane or its side pane and gives it back when you return.
- **Open files in** (⌘,): ↗ and “Open in …” open a file in JetBrains (IntelliJ IDEA for Java / Kotlin / Scala / Groovy /
  Gradle, PyCharm otherwise, `--line N`), VS Code (`code -g file:N`), the side tab (a file of another repo opens with
  that repo as its folder), or the default app (`$ORBIT_EDITOR` if set).
- Inline code in an answer that names a file (`src/app.py:42`, `app.py`) is a link: it opens the file at that line
  in the side pane (⌘-click or right-click → Open in new tab: a tab of its own), found in the session's folder, its
  repo, or the git repos next to it.
- **@@ mentions**: `@@` in the input lists the other sessions; on send each mentioned session gets the request with
  this session's last 3 turns and how to answer back (`orbit-agent mention`), and this one hears who else got it.
- On a card: **✎** puts the request back into the input (sending replaces it; a queued one changes in place), **↺**
  resends it, and a queued request can be **withdrawn**.
- A card whose agent went away mid-request (or ran out of turns) is **paused**: Continue resumes it.
- Answers render `$math$` and `$$display math$$` with KaTeX.

## Notes and `orbit-agent`

The notes vault is an Obsidian-compatible folder: `<data root>/notes`. The data root is `--data-root`,
`$ORBIT_DATA_ROOT`, or `~/.orbit/data`; `$ORBIT_VAULT` points at a vault directly (for example an existing Obsidian
vault). The app reads these from your login shell. ⌘⇧D opens the journal: this week's note, Monday to Sunday, at today's heading. A vault with Obsidian's Daily notes
set up (`.obsidian/daily-notes.json`) keeps it where Obsidian keeps daily notes, in their date format, dated by the
week's Monday (e.g. `Journal/202611/2026-11-16.md` for the week of the 16th), with a `## Mon 11-16` … `## Sun 11-22`
heading a day; ⌘⇧D, selected answer text and `orbit-agent note` add under today. Otherwise: with an `Organization Guide.md` at the
vault root, `Journal/<YYYY>/Week of <YYYYMMDD>.md` (a week note from the last 6 days is reused; a new one comes from the
guide's weekly template, else Focus · Daily Work with `### Mon 11/16`… · Projects in Flight · Ideas · Carry-overs ·
Reflections); without a guide, `Weekly/Week of <Monday>.md`.

## `orbit-agent`: Orbit for agents

Every agent Orbit starts hears one `[orbit]` line with its first request (hidden from the card): its session
name, id and folder; what the cards render (Markdown tables, code, KaTeX math, and images: `![caption](path)` with an
absolute, `~/` or folder-relative path shows the picture, so a plot is a saved PNG embedded in the answer; `file.py:42`
in inline code opens the file); and that `orbit-agent` exists. The line goes again whenever Orbit starts a new process
for the session (a new or attached session, a restart), not with every request. `orbit-agent` is on the agent's `PATH`; `$ORBIT_SESSION` and `$ORBIT_PORT` are set.

```sh
orbit-agent ctx                          # what the user is looking at: tab, file:line, selection, open files
orbit-agent open src/app.py:42 [--side]  # open a file for the user (a tab, or the side tab); never takes the keyboard
orbit-agent show report.html             # show a page in the side tab (sandboxed); `-` reads HTML from stdin
orbit-agent sessions                     # the open sessions
orbit-agent send helper "check the migration" --wait   # ask another session; prints its answer
orbit-agent steer helper "skip the slow tests"           # add to the request it is running now (no new turn)
orbit-agent read helper --last 10                      # another session's recent turns
orbit-agent new ~/src/api --name api "run the tests"     # start a session (and its first request)
orbit-agent wait api                                     # until its current request ends; prints the answer
orbit-agent send gpu-box/trainer "status?" --wait           # a session on another machine (orbit remote add gpu-box URL)
orbit-agent history api --last 30                        # its turns, also of a closed session
orbit-agent search flaky test                            # every session's history and the notes
orbit-agent groups · orbit-agent continue api · orbit-agent open src/app.py:40-60
orbit-agent mention helper "the schema changed"        # a note to another agent, not a task (it will not ping back)
orbit-agent ask "which test covers the parser?"        # a Fleet helper in this folder; prints its answer
orbit-agent note "found the cause: the cache key ignores the locale"   # to the journal: under today in this week's note
orbit-agent note --new "Projects/Cache bug"                            # a new note; prints its path
orbit-agent wake 30m "check whether the deploy finished"               # wake this session later
orbit-agent schedule add "weekdays 9am" "summarize last night's CI" --folder ~/proj   # a new session each run
orbit-agent calendar --days 2                                          # the user's meetings (when Calendar is on)
```

## Schedules and Calendar

A schedule sends a request at set times: `every 2h`, `daily 09:00`, `weekdays 9am`, `mon,wed 18:00`, `in 30m`,
`tomorrow 9am`, or a 5-field cron (`cron 0 9 * * 1-5`). It targets a session (reopened if closed), a folder (a new
session each run) or the Fleet (an idle helper). They live in `<data root>/schedules.json`; a run missed while Orbit
was off runs once on start if it is under 6 h late. ⌘⌃S opens Schedules as a main tab (⏎ edit · N new · Space
pause/resume · R run now · O journal · L last run · ⌫ remove · ⌘E menu; More → in the side pane); the dialog sets the
target (a session, a folder, the Fleet), a watch's check command and the guards. Misc lists Calendar and Schedules,
each with what is next.

- **Watch**: `orbit-agent schedule watch --every 30s --check CMD TEXT` runs CMD on its interval with no agent and no
  cost; each line it prints is an event (JSON with an `id`, or text). Only new events wake the agent, with TEXT and the
  events; the first run only learns what is there (`--include-existing` to act on it). `schedule check ID` tries it.
- **Journal**: a repeating job has `<data root>/jobs/<id>.md`; every run is told to read it first and append what it
  did, so a wake-up knows the last run without the session's context (`schedule journal ID`).
- **Guards**: at most `--max N` agent runs a day (default 24), none after `--until HH:MM`; an agent wakes once a
  minute at most (a watch may check every 10 s).
- **Agents schedule themselves**: the `[orbit]` line and the orbit-agents skill tell every agent about `wake`,
  `schedule add` and `schedule watch`, and to schedule its own follow-ups instead of asking to be reminded.

Calendar is read-only and off until you turn it on (⌘, Settings). It reads macOS Calendar through a small EventKit
helper compiled on first use (`swiftc`, into `<state dir>/bin/`); macOS asks once for Calendar access.

- **⌘⌃C** opens it as a main tab (the side pane stays free for a session: **Attach session ▾**, or Prep's helper):
  today and tomorrow (W: a week, paged with ⌥← ⌥→), the header counting real meetings (`today 3 · tomorrow 2`),
  ↗ Calendar.app, ↻ refresh.
- **One 44px row per meeting**: `09:00–09:30`, the organizer's initial, the title, `now` / `overlap`,
  `organizer · accepted/total`, Join. The current or next meeting has an accent bar; past ones are dimmed. Blocks (all
  day, declined, canceled, free, untitled, `TEAM:TEAM`-style placeholders, your own blocks with no one invited) hide
  behind each day's `N blocks` (B).
- **A meeting opens in place** (→ or a click; ← closes; several can be open): its people (accepted: a green ring,
  declined: struck through), the invitation (collapsed, links work), up to 4 related sessions (a word of its title in
  a session's name or last answer; a click opens it beside), ✨ Prep · Copy link. The opened meeting the cursor is on
  shows its **note** under it (`Meetings/<date> <title>.md`, made with a header: when, organizer, who, the link),
  read-only; ⏎ writes it (Markdown editor, autosaved), Esc goes back to the list (again: to the session beside).
- **Prep** (P, ✨ Prep, the reminder, ⌘E): a Fleet helper prepares you, read-only (topic, who will ask what as a
  table, open items, links to sessions and files); it opens beside, and its brief is appended to the note as
  `## Prep · <helper>, <time>`, merged into an open editor without moving your cursor.
- **Status bar**: the current or next meeting with a countdown chip (`25m`, `soon` at 10 min, `now`); a click or
  ⌘⇧C lists today's and tomorrow's (⏎ opens it in the Calendar, ⌘⏎ joins).
- **Reminder**: 10 minutes before, once: a floating note (Join · ✨ Prep · Open · ×, gone after 10 min) and a macOS
  notification.

`$ORBIT_CALENDAR_BIN` replaces the helper (tests use `tests/fake_calendar.py`).

## Plugins

A plugin adds to Orbit without changing it, so `git pull` updates Orbit and the plugin keeps working: a folder with
a `plugin.toml` (usually a git repository of its own, in any language) in `~/.orbit/plugins/` or listed in
`plugin_paths` (`~/.orbit/config.toml`). It can add **agents** (any CLI speaking the
[Orbit Harness Protocol](builddoc/harness-protocol.md) becomes a harness of ⌘N), **services** Orbit keeps running
beside the server (a chat bridge, a webhook: they send requests to sessions with an origin, shown as "via …" on the
card, and take the answers back), a **command** for agents (`orbit-agent p NAME …`), a sentence on the agents'
`[orbit]` line, **secrets** kept on this machine, and things on the page, all drawn by Orbit from the data it gives
(plugin API 2.0: no iframes): **threads** that read and reply like a conversation (a Slack thread: Orbit's Markdown,
editor, ⌘F, image viewer, quote menu; on the phone too, under Misc), **lists** drawn like the Sessions list (an inbox in the left panel), **docs**
(Markdown it pushes, in a tab or the side pane), **status items**, **actions** in the More menu and on answers,
**notifications**, **shortcuts** for its tab and panels, **⌘O tabs** of items it keeps in Orbit (searched live through
its service); an agent may show a quota in the status bar, and its conversations join Resume another session. [builddoc/plugins.md](builddoc/plugins.md) is the guide and the
HTTP API it may rely on.

| Command | Does |
|---|---|
| `cli/orbit plugin list` | the plugins: loaded or why not, their services, which secrets are set |
| `cli/orbit plugin check [DIR…]` | also asks each agent to `--describe` itself; DIR: a plugin folder not installed yet |
| `cli/orbit plugin secret NAME KEY` · `restart NAME` · `logs NAME` | a secret (asked for, never echoed), its services, their log |

Settings → Plugins shows the same and sets secrets. Examples: `examples/opencode/` (opencode as an agent),
`examples/webhook/` (requests over HTTP, answers to a callback: the shape of a chat bridge), `examples/hello/` (a
status item, a doc panel, actions, a list, a thread with replies, a ⌘O source).

## Run without the app

```sh
uv run python -m orbit            # http://127.0.0.1:8770/ (state in ~/.orbit)
uv run python -m orbit --port 8771 --state-dir /tmp/orbit-state
```

`$ORBIT_CLAUDE_BIN`, `$ORBIT_CODEX_BIN` and `$ORBIT_AGY_BIN` override the `claude`, `codex` and `agy` commands
(tests point them at `tests/fake_claude.py`, `tests/fake_codex.py` and `tests/fake_agy.py`).

## Test

```sh
uv run python -m unittest discover -s tests -v
```

CI (`.github/workflows/test.yml`) runs them on macOS and Linux for every push. The version is `__version__` in
`orbit/__init__.py` (`orbit --version`); what changed, and what changed for plugins, is in [CHANGELOG.md](CHANGELOG.md).

## Layout

| Path | Role |
|---|---|
| `orbit/server/` | The orbit server: `handler.py` (Host / Origin / token guards, the GET and POST routes), `streams.py` (SSE), `forward.py` (another machine's sessions, files, shells), `sharing.py` (listening for other machines), `support.py`, `run.py` (`make_server`, `main`) |
| `orbit/desk/` | Sessions, request queues, cards (rebuilt from the harness's own transcript): `desk.py` (state, events, lifecycle), `session.py`, `driving.py`, `requests.py`, `groups.py`, `fleet.py`, `agents.py` |
| `orbit/drivers/` | One adapter per harness, all on one engine: `base.py` (the contract; Orbit session → harness conversation ids), `process.py` (the engine: the process, the request's life, Stop, a process that dies), `claude.py` (one long-lived `claude -p --input-format stream-json` per session), `codex.py` (`codex exec --json [resume\|fork]` per request; history from rollouts), `agy.py` (stream-json; history from its SQLite store), `external.py` (conversations started in a terminal), `protocol.py` (any harness that speaks the Orbit Harness Protocol) |
| `orbit/machines/` | `remote.py` (the token, the remotes registry), `federation.py` (other servers' sessions, files and shells as this one's: `NAME~ID`, `/@NAME/…`) |
| `orbit/harness_server/` | The Harness Server: a daemon (stdlib only) that runs the agent processes and journals their output, so they outlive a restart of the server; `client.py` (the launcher the drivers spawn through) |
| `orbit/workspace/` | `files.py` (tree, read / save, ± Review), `notes.py` (the vault, wiki links, week note), `skills.py` |
| `orbit/services/` | `schedule.py`, `calendar.py` (via `helpers/calendar.swift`), `usage.py` (plan usage), `shell.py` (the ⌘J shells on a pty), `plugin_services.py` (plugins' services), `plugin_items.py` (their item sources), `plugin_views.py` (their threads and docs, the `svc:` proxy), `keys.py` (Orbit's own API keys), `live/` (GPT Live voice calls: `session.py` the call, `ws.py` a stdlib websocket client) |
| `orbit/cli/` | `orbit.py` (the `orbit` command), `agent.py` (`orbit-agent`); launched by `cli/orbit` and `cli/orbit-agent` |
| `orbit/plugins.py` | Plugins: finds and loads them, registers their harnesses (`drivers/protocol.py` runs those) and services (`services/plugin_services.py` keeps them running), their secrets, command and hint |
| `orbit/home.py` | `~/.orbit` (moved once from the old `~/.aide`, a link left behind) |
| `web/index.html` | The page's markup; it loads `web/styles/*.css` and `web/src/*.js` (plain scripts sharing one scope, in order: `core`, `markdown`, `state`, … `main` boots) |
| `web/src/` | The UI by feature: `fileicons` (file-type and folder icons), `plugins` (what plugins add to the page), `pviews` (a plugin's thread, doc and list, drawn natively), `msglist` (the message list a conversation and a thread share), `lineedit` (Emacs keys in one-line fields), `cards`, `composer`, `attach`, `tabs`, `files`, `find`, `notes`, `left`, `side`, `review`, `schedules`, `settings`, `modal`, `finder`, `machines`, `shell`, `commands`, `keys`, `live` (the server's events), `voice` (a GPT Live call), `main` |
| `web/styles/` | `base` (tokens), `layout`, `conversation`, `editor`, `side`, `panels`, `dialogs`, `settings`, `embed` |
| `web/vendor/` · `web/cm/` | markdown-it, highlight.js, KaTeX, xterm.js and the CodeMirror bundle; its sources (`npm install && npm run build` writes `web/vendor/cm.js`) |
| `app/` | Tauri 2 shell: native window (WKWebView), app menu; shows any orbit server, starts this Mac's |
| `cli/` | The launchers (`orbit`, `orbit-agent`) |
| `builddoc/` | The spec (`harness-protocol.md`: how an agent joins as a harness) |
| `examples/` | Example plugins (`opencode/`: an agent; `webhook/`: a service; `hello/`: status item, doc panel, actions, list, thread) |

## License

MIT, see [LICENSE](LICENSE). The libraries bundled in `web/vendor/` keep their own licenses, which sit beside them.
The agents' marks in `web/src/brands.js` (Claude, OpenAI, Gemini) are their owners' trademarks, used only to name each
agent; their SVG paths come from [Simple Icons](https://simpleicons.org) (CC0).
