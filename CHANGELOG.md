# Changelog

Orbit's version is `__version__` in `orbit/__init__.py` (`orbit --version`, Settings → About); the plugin API it
offers is `API_VERSION` there, which a plugin's `requires_api` is checked against. A plugin API change says so here:
**added** (a minor version: old plugins keep working) or **changed** (a major version).

## 0.2.0 (unreleased)

Plugins: Orbit can be extended without changing it, and updated with `git pull` without touching the extensions.

- **Settings → Voice** (GPT Live): the OpenAI API key (moved from General), the voice (38 built-in voices, each with
  the language and accent OpenAI lists), the language it speaks (or the user's), results brief or detailed, a spoken
  "on it" when a task starts (off: the automatic acknowledgement is cancelled, as before), standby after 10–60 s or
  never, how clear speech must be to wake it (vad_threshold 0.3 / 0.5 / 0.7), the session's recent work at the start,
  the model, and the user's own instructions (after Orbit's; up to 4 000 characters). Settings `live_*`, checked by
  the desk (`orbit/services/live/config.py`), read as a call starts; `GET /api/live/options` lists the choices.
- The voice button: five bars in a circle where Send is while the input is empty; in a call they follow the sound.
- Voice: after each request the voice is told what became of it (running, queued behind another, joined to the
  waiting one), so it says "queued" instead of guessing; "via Voice" on a card explains what the agent got.
- Settings → Voice: hovering a voice (or ↑↓ in the list) plays it saying hello, ▶ plays the chosen one. OpenAI
  publishes no samples, so the voice makes its own: a short WebSocket-transport session told to greet in one sentence
  (in the voice's language, else the chosen one), its speech cut out by loudness and kept as a WAV in
  `<state>/live/voices/` (made once per voice: a few seconds, a fraction of a cent). `GET /api/live/preview?voice=`.
- Live captions fold to one line (the latest words) with a click on their head, and stay folded until opened.
- **OpenCode** is a built-in harness (⌘N → Harness, `orbit-agent new --harness opencode`), beside Claude Code, Codex
  and agy ([builddoc/opencode.md](builddoc/opencode.md)). One `opencode serve` per session on 127.0.0.1 with a random
  password; its event stream gives the answer as it is written, thinking, tool steps, subagents (a `task` and its
  child session's work under one Agent step), context use and compactions. Stop aborts the request; models are
  OpenCode's `provider/model` (its `opencode models`); plan mode is OpenCode's plan agent. OpenCode's own commands and
  skills run by name (`/review …`, `/compact`). What OpenCode asks the user waits in its own TUI: ⌘T attaches it to
  the session, and the tab is marked until the ask is answered; bypass answers every ask, accept edits the edit ones.
  Its sessions are offered by ⌘O → Other sessions, its history is read from its store (read-only), forks are
  OpenCode's, and a restart of the server hands its process to the next one, which catches up on what it missed.
  `$ORBIT_OPENCODE_BIN` overrides the command. The engine's `plain_line` hook reads a stdout line that is not JSON.

- **Plugin API 2.0** (changed): a plugin's views are drawn by Orbit from the data it gives, never in an iframe ([builddoc/plugin-views.md](builddoc/plugin-views.md)).
  - **Removed**: `path` pages on `[[ui.tab]]`, `[[ui.sidebar]]` and `[[ui.panel]]`, the page bridge (`/src/plugin-bridge.js`, its postMessage messages, key forwarding) and the serving of a plugin's `ui/` folder (only a harness's declared `icon` is still served). An entry with `path` is ignored with an error ("iframe pages are gone in plugin API 2.0: use view = …"); the plugin still loads. A plugin whose `requires_api` fits 1.3 (e.g. `">=1,<2"`) still loads under 2.0. What the bridge did is over HTTP: `open-tab`, `badge`, `notify`, `status`, a thread's `unread` and `title`.
  - `view = "thread"` on a `[[ui.tab]]`: a chat thread drawn like a conversation (the same type, Markdown, code blocks, KaTeX, image viewer, ⌘F, the quote menu, Latest ↓), Orbit's editor to reply (pasted images go as files), a context menu per message (Copy, Copy as Markdown, Open at the source, Quote in a session…, Send to a session…, the plugin's `[[ui.message.menu]]`). Pushed (`PUT` / `PATCH /api/plugins/NAME/threads/KIND/KEY`, its token) or asked of its service (`GET /orbit/thread`, older pages with `before=`) or `[cli] thread KIND KEY` in the background; replies to `POST /orbit/send` (or `[cli] send`) with a pending message ("Sending…", "Not sent" with Retry and Edit) kept by the server. Kept in `<state>/plugin-threads/`: 2 000 messages a thread, 300 threads a plugin.
  - `view = "list"` + `source` on a `[[ui.sidebar]]` (or a panel): its item source drawn like the Sessions list (groups, unread dots, the filter box, its keys and menu; the tab's count: its unread items). Items may say `unread`, `group` and `open`.
  - `view = "doc"`: Markdown it pushes (`PUT /api/plugins/NAME/docs/ID`) or its service answers (`/orbit/doc?id=`), live, in a tab, the left panel or the side pane (a `[[ui.panel]]` is a doc, a list or a thread).
  - **On the phone** too (`/m/`, Misc): a plugin's lists, docs and threads as pages, drawn like the Sessions list and the conversation; replies with the phone's editor (photos as files). Replies are taken from any client with the server's token (the phone, another machine's page); its files must be uploads. `[[ui.message.menu]]` stays this machine's. `GET /api/palette?…&fresh=1` runs a source's refresh command now (a pull on the phone).
  - What a plugin sends is drawn in plugin mode: no local paths, images only `https:` and `svc:` (its service's files, through `/api/plugins/NAME/svc/…`: inert types only, `nosniff`, `sandbox`, GET and HEAD under its declared `files` prefix, never `/orbit/`, no redirects, 20 MB, 10 s, 4 at once), links only https, http, mailto and its `schemes`. An SVG (by its bytes) is served as `image/svg+xml` with `Content-Security-Policy: sandbox` whatever the browser asks for (it no longer depends on `Sec-Fetch-Dest`): opened as a page it runs no script. `/api/raw` sends `nosniff` too.
- **Plugin API 1.3** (added): shortcuts for a plugin's tab and panels, a harness's quota in the status bar, and ⌘O tabs of a plugin's items.
  - `shortcut = "Mod-Shift-S"` (⌘⇧S; Ctrl+Shift+S off macOS) or `"Ctrl-Alt-S"` on a `[[ui.sidebar]]` (shows its left tab with the keyboard; again: the tab before) or a `[[ui.panel]]` (toggles it in the side pane). It works from the page, the input and editors, the shell, side tabs and plugin pages, and shows in the tab's tooltip, the More menu and ⌘/.
  - A malformed key, one of Orbit's (`RESERVED_SHORTCUTS` in `orbit/plugins.py`, checked against Orbit's bindings by the tests) or one an earlier plugin took is ignored with an error (Settings → Plugins, `orbit plugin check`, `/api/plugins`); the plugin still loads. `/api/plugin-ui` carries each one's `shortcut`.
  - A protocol agent's `--usage` may say `"quota": "8 USD left"`: shown after its meter in the status bar's "Used", or alone (Harness Protocol 1.3).
  - `[[ui.palette]]`: a tab in ⌘O of items the plugin keeps in Orbit, pushed by its service (`PUT` / `POST /api/plugins/NAME/items/SOURCE`, with its token) or refreshed by a command in the background (`refresh = {args, every}`); kept across restarts (`<state>/plugin-items/`), 10 000 items of 2 KB at most per source. The page filters them itself: no process per key. `GET /api/palette`; `plugin-items` events redraw an open ⌘O.
  - Live search through the plugin's running service: each service gets `$ORBIT_SERVICE_PORT`; ⌘O asks `GET /orbit/search?source=&q=&limit=30` there (2+ characters, 150 ms after the last key, one request in flight, 1.5 s), and shows the answers under "Search in …"; a service that is down leaves the tab on its items.
  - Resume another session (⌘O → Other sessions) opens at once from an index the server keeps: Claude Code and Codex conversations are read once and again only when their file changed; a plugin agent's `--list-conversations` runs in the background (when older than 30 s), never waited on, or its plugin pushes them to the source `conversations`. A typed query is matched by the server against all of them, not only the 200 newest.
- **Voice calls (GPT Live)**, phase 1 of [builddoc/gpt-live.md](builddoc/gpt-live.md): ⌘⇧V or the microphone in the composer row calls the session in front. Microphone and speaker go to OpenAI over WebRTC; the server creates the call with the OpenAI key (Settings → General) and follows it on the sideband: what the voice delegates becomes a request in the session (once: a repeated or replayed delegation makes no second card; a correction edits a queued request or steers a running one), its progress goes back as silent context (at most every 10 s) and its answer as one short spoken result. Standby after 30 s of quiet; hang-up keeps the transcript as a note card. `POST /api/live/start {sid, sdp}`, `POST /api/live/stop {call}`, `GET /api/live` (this machine only); `{"t": "live"}` events. A setting `live_model` (`gpt-live-1.1`, or `gpt-live-1`).
- `POST /api/sessions/ID/send` takes `client_id`: the same id again returns the card it made (`"duplicate": true`) instead of a second one, also after a restart.
- **HTTPS for everything that crosses the network**, by Tailscale: `orbit https on` (or Settings → Machines → HTTPS) serves `https://<machine>.<tailnet>.ts.net:8443` with a real certificate (`tailscale serve`).
  - The server itself stays on 127.0.0.1 over HTTP, so this machine's window works with Tailscale down.
  - Plain HTTP from the network is sent to the HTTPS address (308). Turning HTTPS on turns plain-HTTP sharing off.
  - A userspace tailscaled: `tailscale = "tailscale --socket …"` in config.toml (or `ORBIT_TAILSCALE`).
  - Settings → Machines: the HTTPS switch, its URL (Copy URL) and **iPhone…**: a QR code that opens the phone's app.
- **Interface size** (Settings → Appearance, 10–15 px, default 12): menus, lists, tabs, the status bar, dialogs and Settings, next to the conversation, note and code sizes.
- A more compact title bar: 38pt at any page zoom (it was 46px, 55pt at zoom 1.2); the app's window buttons stay centred on it.
- New defaults for Appearance: Light text, conversation 13.5 px; page zoom 1 (⌘0; it was 1.2). Consolas joins the code font presets.
- **The work folds once the answer is there.** What the agent said and ran before its answer (messages, tool groups, subagents) is one fold: open while the request runs, with a click to fold it; folded when the answer comes, as "Worked for 3m 12s · 6 messages · 18 steps", which opens it again.
- Menus after Chrome's on macOS: a translucent sheet with 10px corners and a fine edge, 24px rows of 13px text, separators inset to the text, a quiet grey selection; the tab menu grouped by purpose, without the duplicate Unfavorite in Favorites. The letter keys are underlined only in a menu opened from the keyboard (⌘E).
- The title bar's lead breathes: a smaller ☰ and logo, more room between them and the group pill.
- The title bar is white; the active tab is the page's colour, outlined on three sides and open into the page. The app's window buttons are centred on the bar (they sat 9pt high).
- A session whose subagents still work in the background, after its request ended, shows the working ring (tabs, left panel, the status bar), "now", and counts as working in its group; not a ✓ until they report. Its info carries `agents` (how many); its state stays ready, so a new request runs at once. Closing it warns that they stop.
- **Agents outlive a restart of the server.** They run in the **Harness Server**, a small daemon the server starts (`orbit/harness_server/`). A restart (View → Restart Server, an update, SIGTERM) hands each Claude Code session over to the next server, which reads on from where the last one stopped: a request at work goes on with the same card and the same process, and background subagents keep working; nothing is asked again.
  - **Quit Orbit and Stop Agents** (⌥⌘Q) still ends everything: `POST /api/shutdown {"agents": "stop"}` (SIGUSR1 for scripts).
  - `orbit harness-server status | stop`. One server per state dir (`server.lock`). `orbit-agent` waits up to 30 s for a server that is restarting.
  - Codex (`codex app-server`), agy and protocol agents are handed over too. A Codex subagent still at work after its turn keeps working across a restart and its report lands on its step; an approval asked meanwhile is answered by the next server. Codex's exec mode (`ORBIT_CODEX_EXEC=1`, a process per request) is still closed and resumed. `ORBIT_HARNESS_SERVER=0`: agents as the server's children, as before.
  - **`orbit stop [--agents]`**: stops the server of the state dir, found by `<state>/server.json` (its pid and port), never by the default port. Plain: its agents run on in the Harness Server until the next `orbit serve` takes them (in direct mode they end too, and it says so); `--agents`: every agent and the Harness Server end.
  - A plugin keeps its `ORBIT_PLUGIN_TOKEN` across restarts (`<state>/plugin-tokens.json`), so a handed-over agent's calls back are still its plugin's. A SIGTERM while the server starts is acted on once it is up. Stop and steer wait (503) while the server stops.
  - **Restart Harness Server…** (⋯ menu, View menu, `orbit harness-server restart`) asks first, saying how many agent processes end and which are at work; then every agent process ends, a new Harness Server starts (the code on disk), a request it cut off goes on by itself and one sent meanwhile waits for it. Sessions stay. A confirmation whose OK cannot be undone starts on Cancel.
  - Copy resume command says when the session's agent is still open here: after the terminal, Session → Restart so it continues from what was done there. The status bar's ⋯ says when this machine's agents run in the server itself (a restart ends them), and why.
- **The phone's input is the desktop's live-preview Markdown editor**: images, links, math and code render in place; an image pasted or dropped in is uploaded and goes in as `![name](path)` (another file as a link); the keyboard's autocorrect and capitals stay on.
- Phone: the app keeps one history entry (its address names the open session): iOS's own edge swipes no longer slid a stale snapshot of the list beside the app's swipe, nor reopened a session closed long ago (a swipe from the right edge).
- A table's short columns keep their text on one line: Chinese breaks between any two characters, so a column of short labels was squeezed to one character beside long text.
- The Markdown editor fits its pane at any width: a wide table no longer widened the text column (lines wrapped past the pane's edge, a sideways scrollbar), tables and formulas keep the text's 32 px sides, and table cells wrap.
- An existing Codex conversation is found by its session metadata, not only by its file name (a rollout whose name lacks the thread id, as on some machines, showed no history); a thread with no rollout yet is not searched again for 30 s.
- An existing Codex conversation whose requests Codex did not record (Codex Desktop's VS Code extension keeps them only in the model's input) shows its history again: a card per turn, its work and answers, the request marked as not on record. It showed no cards at all.
- ⌘O (and ⌘R) finds a session by its id too: 4 or more characters of Orbit's id or the agent's conversation id (Other sessions), or a pasted session link; ahead of matches by name.
- **Copy resume command** in a session's menu: a shell line that continues the same conversation in a terminal, in the agent's own UI (`cd <folder> && claude --resume <id>`, `codex resume <thread>`, `agy --conversation <id>`); `GET /api/sessions/{id}/resume-command`, also for another machine's session (run it there). Stop the session in Orbit first if it is working: both would write the same conversation.
- **The journal lives where Obsidian keeps daily notes.** When the vault sets them up (`.obsidian/daily-notes.json`: folder, date format, template), this week's note (Monday to Sunday) goes there, dated by the week's Monday (e.g. `Journal/202611/2026-11-16.md`), with a heading a day; ⌘⇧D opens it at today, and selected answer text and `orbit-agent note` go under today. Without them, the week note as before.
- **Built-in fonts, the same on every machine**: Orbit ships Inter, Inconsolata and Noto Sans SC (SIL OFL) and uses them by default: Inter for the interface and conversation, Inconsolata (made after Consolas) for code (14 px) and notes (15 px). They head every font list as "· built in"; a system font chosen instead falls back to them where it is missing. Their Chinese is built in too: Noto Sans SC (Source Han Sans), in 95 slices (4.3 MB in all) of which a page fetches only those of its characters; emoji stay the system's colour ones. Appearance prefs saved before are dropped once, for these defaults; Settings → Appearance → Defaults → Reset does it again.
- **Orbit on iPhone**: `/m/`, a home-screen app (PWA) for the same server.
  - Sessions by group with their marks, a Working-now strip, unread dots, pull to refresh.
  - Live conversations: steps, subagents, Markdown, the desktop's scroll rule; Send / Steer / Queue / Stop, photos; New session, the ⋯ session actions, Settings.
  - Pairing: the QR code (Settings → Machines → iPhone…) opens `/m/?token=…`; the app keeps the token and sends it with every request, so the home-screen app (its own storage) is signed in from its first start. An address opened without it shows how to get it.
  - It reconnects when it comes back to the foreground and starts at once from its service worker; a phone opening `/` goes to `/m/`.
- **Fork a session**: Fork in the session's menu, `/fork-session [NAME]` in the input, or `orbit-agent fork [SESSION|.] [--name N] [first request]` (also in the orbit-agents skill). It makes a new session in the same group that continues the conversation with all its settings: folder, agent, model, effort, mode.
  - It is server-side and works for every harness. Claude Code (`--fork-session`), Codex (thread fork) and protocol agents fork natively.
  - agy, or a protocol agent declaring `capabilities.fork: false`, gets the conversation so far with the fork's first request. The card and the history never show it.
  - `/api/sessions/{id}/fork` (`clone`, its old name, still answers).
- A protocol agent's `--usage` (`used` 0–1) shows as a percent in the status bar, like Claude's and Codex's (it showed 0%). Harness Protocol 1.2: `capabilities.fork: false`.
- Closing a session always asks first (⌘W, its × or the menu); ⏎ closes, Esc keeps it, ⌘⇧T reopens. In Favorites, ⌘W and × only remove it from the favorites, without asking (it stays open in its group).
- In the input, ⌥⇧, ⌥⇧. go to the input's own start / end; they used to scroll the conversation.
- **Codex runs as one long-lived `codex app-server` per session** (JSON-RPC over stdio, as the Codex app does).
  - Its subagents keep working after the main agent's turn and report on their card when done, as a background Agent step.
  - Messages sent mid-turn join the running turn (steer); Stop interrupts the turn; effort is passed on.
  - Approvals are answered by the session's mode.
  - `codex exec` stays as the fallback, for an old codex or with `ORBIT_CODEX_EXEC=1`.
- **Plugin API 1.2** (added): a plugin may say who a name is.
  - `[people] args = [...]` runs its `[cli]` with stdin `{"names": [...]}` and gets back `{name: {"avatar", "handle"?}}`.
  - Orbit caches each answer for a day ("not known" for 6 h) and serves it at `GET/POST /api/people` (this machine only).
  - The Calendar and the meetings popover show the photos. `examples/hello` has a toy provider.
- **Tabs after a browser's**: a tinted strip, and the active tab in the page's colour flared into it, with a × on every tab. **Drag to reorder**, as in a browser: the dragged tab or row follows the pointer and the others slide aside. Works for tabs, sessions (within or across groups) and groups (with their sessions).
- **The conversation keeps its place** per session across resizes, new answers and reloads. It is either stuck to the end (following new lines) or held where you scrolled to; ⌘L or Latest sticks it to the end again.
- **⌘⇧C, or a click on the status bar's meeting**, opens a meetings popover above it. A click opens that meeting in the Calendar.
- **UX pass:**
  - one set of status marks everywhere;
  - a reading width (Comfortable 900px, or Full);
  - requests show their code and fold past 12 lines;
  - Resume on a stopped session;
  - a details strip at the left panel's foot instead of big tooltips;
  - row actions in Schedules and the Calendar;
  - a guided empty session;
  - the schedule dialog's target as one choice with an Advanced section;
  - the status bar under the shell;
  - usage of every harness always shown, in a steady order (the session's own one a shade darker);
  - a slim side-tab file head;
  - names in one column on every left-panel tab.
- **Subagents above the input.** A bar on top of the input shows every subagent this session is running, in the foreground or the background, from any request. Each row gives its type and task, what it runs now, its steps and elapsed time; a click opens it right there, with what it says and runs (following the latest), and Show in conversation goes to its card. A background subagent whose agent's process ended (a restart) is marked as ended, instead of staying "running" forever.
- **Codex subagents** are shown. `codex exec --json` does not report them, so Orbit reads them live from Codex's rollouts. Because Orbit runs Codex one process per request, a subagent still at work when the turn ends is stopped with it; Orbit marks it so, and tells Codex to wait for its subagents (`wait_agent`). The `[orbit]` line can carry a per-harness hint (`Driver.agent_hint`).
- **Visual audit, 22 fixes, measured in Chrome and WebKit.**
  - Tooltips never cover a menu.
  - Dialogs never run past the window (they scroll, with their buttons kept in sight).
  - Tabs have equal room on both sides; × shows over the end of the name.
  - A long filter keeps its caret in sight.
  - One keyboard-cursor look; the keyboard row's tooltip appears beside the panel, after the keys rest.
  - Every small control is 26px.
  - One dropdown style.
  - Contrast: light `--ink-3` 4.6:1, dark text on the dark accent, a visible off switch.
  - The status bar's state chip is no longer clipped, and usage gives way without cutting a glyph.
  - Tables line up with the text around them.
  - Status marks are drawn icons.
  - A side tab fits its frame in WebKit; before, it was laid out 1.2× too wide.
- **Calmer Calendar and Schedules.**
  - Content carries the colour; the chrome is hairlines and tints.
  - The list is centred at 900px, with no row boxes.
  - The keyboard row is a 3px bar plus a tint, never a fill.
  - Times use the UI font with tabular figures.
  - Badges are coloured text; Join is a quiet text button.
  - An opened meeting starts under the title.
- A left-panel plugin page passes ⌘⇧[ / ⌘⇧] to the panel's tabs; before, they went to the main tabs.
- **Plugin API 1.0.** A plugin is a folder with a `plugin.toml` in `~/.orbit/plugins/` or listed in `plugin_paths`
  (`~/.orbit/config.toml`); `orbit plugin list|check`; `GET /api/plugins`.
- **Services from plugins** (`[service.<id>]`): processes Orbit keeps running beside the server (restarts with
  backoff, logs, waits for missing secrets); they use the HTTP API v1 (`builddoc/plugins.md` §4). Requests they send
  carry an **origin** (shown as "via …", kept across restarts, `data` for the way back);
  `GET /api/sessions/{id}/cards/{n}?wait=S` waits for a request to end. `examples/webhook/`.
- **Secrets** (`secrets = [...]`): `orbit plugin secret`, Settings → Plugins; 0600, never served, given to the
  plugin's processes as environment.
- **A command for agents** (`[cli]`, `orbit-agent p NAME …`, run by the server with the plugin's secrets) and a
  **hint** on the `[orbit]` line (`[agent] hint`).
- Settings → Plugins; `orbit plugin secret|restart|logs`.
- **Plugin API 1.1** (added): a left-panel tab (`[[ui.sidebar]]`, with a badge), main tabs of a plugin's own kinds
  (`[[ui.tab]]`, `[[ui.tab.menu]]`: real tabs, kept across reloads, a session beside), the page bridge
  (`/src/plugin-bridge.js`), `open-tab` and `notify` (the bell + a macOS notification). `examples/hello/` has both.
- **Harness Protocol 1.1** (added): agents that live in a terminal: `capabilities.terminal` + `hello.terminal.command`,
  shown by ⌘T under the conversation (attached on a pty; closing detaches); the `attention` event (a tab mark, a toast,
  the bell); `skill_dirs` in `--describe`.
- Left panel (handoff 02): rows 30px, flat panel tabs, a quieter filter box, `running/total` on every group, empty
  groups hidden, stopped sessions as a hollow dot and italic, `name · key` for twins, a mono tree head; Misc is one
  row per tab (Calendar, Schedules) with what is next.
- Schedules (handoff 04): a main tab (⌘⌃S); the dialog sets the Fleet target (editing a Fleet job no longer turns it
  into a folder job), a watch's check command, max runs a day and until; Pause / Resume, Journal (O), last run (L),
  Remove; skipped runs and watches shown.
- Calendar (handoff 03): the status bar and Misc look at today and tomorrow; server cache 60 s; help text.
- A session whose harness's plugin is gone says so ("not ready"), instead of failing.
- **Plugins on the page**: status items a plugin sets (`POST /api/plugins/{name}/status`), panels from its `ui/`
  folder in the side pane (`[[ui.panel]]`), actions in the More menu and on answers (`[[ui.action]]`). `examples/hello/`.
- **Harnesses from plugins**: any agent CLI that speaks the Orbit Harness Protocol (`builddoc/harness-protocol.md`)
  joins as a harness; `local_only` ones never run for another machine. `examples/opencode/` wraps opencode.
- **One engine for every harness** (`orbit/drivers/process.py`): Claude Code, Codex, agy and protocol harnesses
  stop, die, continue and restart the same way.
- File names in answers open beside the conversation (right-click: a tab, the editor).
- Settings → Appearance: interface, conversation, note and code fonts; code size.
- A message steered just as its turn ended becomes a request instead of being lost.
- `orbit --version`, `GET /api/version`; CI runs the tests on macOS and Linux.

## 0.1.0

The first public version: sessions of Claude Code, Codex and agy as documents; notes, files, ± Review, the ⌘J
shell, schedules and watches, the Calendar, the Fleet, other machines' servers.
