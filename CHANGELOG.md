# Changelog

Orbit's version is `__version__` in `orbit/__init__.py` (`orbit --version`, Settings → About); the plugin API it
offers is `API_VERSION` there, which a plugin's `requires_api` is checked against. A plugin API change says so here:
**added** (a minor version: old plugins keep working) or **changed** (a major version).

## 0.2.0 (unreleased)

Plugins: Orbit can be extended without changing it, and updated with `git pull` without touching the extensions.

- File and view tabs move to another group too, like sessions: a drag in the Groups panel (under the other group's
  head, or among its rows), or the tab's menu → Move to (the tab row, the panel). Its side tabs go with it.
- ⌘O has a Notes tab (after Files): every note by its title and folder, filtered as you type; for 2+ characters the
  notes whose text has it come below ("Search in Notes", with the line). ⌘O in a note starts there; ⌘⇧F still opens
  History.
- Quote from a file or note goes to the session beside it: with a session open in the side panel, the quote
  (`path:line`) goes to the end of its input and the side panel gets the keyboard; with none, a menu of sessions, and
  the chosen one opens beside with the quote in its input (⌘E → "Quote beside: …" / "Quote into a session beside").
- The image viewer zooms: fit first; a pinch zooms around the pointer (ctrl + wheel; WebKit's gesture events, counted
  once), `+` `−` `0`; two fingers or a drag pan while zoomed; a double-click toggles fit / 2.5×; a click on the dark
  area closes (zoomed or not), on the zoomed picture it does not; Esc always closes, before any view's own keys. One
  transform, painted once a frame, no transition. A picture in any view opens in it; in the app, `window.open` of a
  picture's address does too (`web/src/lightbox.js`).
- Req room #31. Agents that outlive the server (pairs with `Driver.end()`): two optional driver classmethods,
  `link_alive(link)` (at the server's start a session whose agent still runs is followed at once, its driver started
  with resume: no new agent; a stopped one stays stopped) and `end_link(link)` (closing a session Orbit has no driver
  for still ends its agent); `link` is the conversation id linked, else the session's id (harness-protocol.md; not
  named `alive`, which is the instance's process). A page script that fails to load (not fetched, or dead at its top
  level: a duplicate top-level `const` drops a whole file) now says so on the page, the Mac's and the phone's: one
  notice per script (`web/src/scriptguard.js`, loaded first). File links in notes open: relative to the note's
  folder, `~/…`, absolute or `file://`; a text file in Orbit, anything else in its default app (a PDF in Preview), a
  folder in the Finder; a link's right-click menu: Open · Open in Orbit · Reveal in Finder · Copy path (a web link:
  Open link · Copy link). `test_harnesses` no longer hard-codes the harnesses (a build may register its own).
- A home page's cards: each its own icon (before its title), colour (tints the card) and background picture (under a
  wash, so the text stays readable), and its own title: the card's ⋯ → "Icon and background…" / "Rename…" (`icon`,
  `color`, `bg`, `title` on a widget); the phone draws them too. Cards resize with the mouse: the right edge sets the
  width (snapped to a third, a half, two thirds, the row; the width shows while dragging), the bottom edge the height
  (what it shows scrolls inside; `h`), the corner both; a double-click on the bottom edge, or ⋯ → "Height: as tall as
  what it shows", gives back the natural height.
- Voice calls from an Apple Watch, the server side (builddoc/gpt-live.md §12): `POST /api/live/watch/start`, PCM
  uploads (`…/up`, kept-alive), a framed down stream of the voice's audio and the call's events (`…/down`), `…/stop`;
  plain HTTP with the phone's token. The server holds the GPT Live session over its WebSocket transport; delegations,
  results and the note card work as in a page's call (no standby on that transport). The watchOS app comes next.
- The page is tested in real browsers: `tests/test_ui_smoke.py` (Chromium and WebKit, skipped without Playwright:
  `uv run --with playwright python -m unittest discover -s tests -p test_ui_smoke.py`) checks a group's home after a
  reload, ⌘- back across groups, the icon search, dragging a home widget, Space folding a group, the phone's tint.
  It found a bug: a home widget being dragged was let go of when the page redrew under it (an agent of the group
  working); the home now waits for the drag, as the tabs and the left panel do.
- The Groups panel: Space folds / unfolds the group (on a row in it: its group folds, the cursor on it).
- The phone: a session of a coloured group wears its tint at the top of the conversation, as on the Mac.
- A group's icon is picked from all of Unicode's emoji (about 1,600, loaded when the picker opens): a search box
  (English names and common Chinese words: 火箭, 股票, 猫, 工具…; Enter takes the first match), category chips,
  a Suggested row of the usual project icons; any short text or a picture still works.
- A group's home page, its first tab (the group pill; a click on the group in the left panel; its ⌘ digit pressed
  again): widgets on a grid of sixths: its sessions as cards (state, last answer), its subgroups (a click goes in), its
  files and views, the schedules that wake its sessions, what happened lately, what was pinned, and Markdown notes of
  your own (files in the vault, `Groups/<path>/…`; Edit or ⏎ writes in place, Esc is done; their checkboxes tick on the
  page; templates: About, Next steps, Decisions). A widget is dragged by its head, takes a third / a half / two thirds
  / the row, hides. A group's look: an icon (an emoji or a picture), a colour (the pill, its row in the panel, the
  head's tint, a subgroup card's edge) and a cover picture over its home, never under what is read
  (`POST /api/groups/<id>/style`, `…/home {widgets}`). Pins: an answer's menu (right-click) has "Pin to <group>'s
  home"; agents run `orbit-agent pin TEXT|-|--file F [--group G]` (their [orbit] line says: decisions, conclusions,
  key facts, never progress); kept newest first in the group's `Pins.md` (`…/pin`, `…/unpin`). The phone: a group's
  name opens its home, one column, notes and pins included.
- ⌘⇧[ ⌘⇧] go through the group's home too (first, as on screen).
- Every tab of a coloured group (its sessions, files, views) wears a light tint of its colour at the top of the page
  (10%, fading out within 110px, so muted text keeps its contrast; over the page: clicks and selection go through it).
  Appearance → Group colour on every tab turns it off (the home, the pill and the panel row keep their colour).
- Dragging (tabs, the left panel, a home page's widgets) selects no text on the way: a press that may become a drag
  starts no selection, and the page cannot select while one is dragged.
- Groups nest, like folders: a group holds sessions, file tabs and subgroups, 3 levels at most ("Work › Orbit ›
  Release"). The server keeps `parent` on a group (`POST /api/groups {name, parent}`, `…/<id>/nest {parent, after}`;
  `move` is among siblings); names are unique among siblings; deleting a group moves its sessions and subgroups up a
  level. The left panel draws the tree (a group's own rows, then its subgroups, each level indented); a folded group's
  count is its whole subtree's; ← goes up to the parent; Tab puts a group into the one above it, ⇧Tab takes it out a
  level; its menu has New subgroup…, Move into ▸. The tab row shows the chosen group's own tabs; the group pill, "Move
  to", the new-session dialog and toasts show the path. ⌘2…⌘9 are the top groups'. The phone: a subgroup is a row in
  its parent, opened as a page of its own. `orbit-agent groups` prints paths; `--group` takes a path.
- The Groups panel: ⌃D on a row closes its session or tab, or deletes its group, always asked first (a tab closed
  there reopens with ⌘⇧T); ⌘R renames the row: a group, a session, or a file tab's file. In the filter ⌃D still deletes
  a character, except at its end. ⌘⇧T reopens a file tab in its own group.
- Fixed ⌘- / ⌘= (back / forward through visited tabs) across groups: a file tab (a file, a note, the Calendar, a plugin
  view) is remembered with its group, so back returns to that group's tab instead of opening the file in the group in
  front; switching groups records the tab it lands on; closed tabs are skipped. 200 entries, a tab once.
- The ⌘J terminal looks like iTerm: the font, size, line and letter spacing (and thin strokes) of iTerm's default
  profile (Appearance → Terminal font: Like iTerm, the default, or the code font). The server finds that font's file
  by its PostScript name in the font folders and serves it (`GET /api/term-font`, `…/file/regular|bold`): WebKit, the
  app's engine, keeps fonts the user installed from pages. A variable font gives every weight from one file.
- ⌥D kills the word after the cursor and ⌥⌫ the word before it in the editors (the input, code, Markdown), as in
  Emacs: into the kill ring (⌃Y yanks it back), presses in a row make one kill. (⌥D used to type “∂”.)
- Extension points, so a customized build needs no core patches (request #29):
  - `Driver.end()`: closing a session calls it (default: `close()`); a server restart, a hand-off or a changed setting
    still calls `close()`. A driver whose agent outlives it (in a terminal multiplexer) ends the agent only on `end()`.
  - `$ORBIT_HOME`: Orbit's folder (default `~/.orbit`; state and data under it). The folder's old name is no longer
    moved on its own (the server, the app): `orbit migrate-home` does it; the server says so when it would apply.
  - `orbit-agent` and `orbit` without `$ORBIT_PORT` reach the server of their state folder (`$ORBIT_STATE_DIR`, else
    `$ORBIT_HOME`, else `~/.orbit`: its `server.json`, while that server holds `server.lock`), else 8770.
  - Settings → General → Fleet helpers / Fleet model (`fleet_harness`, `fleet_model`): the harness and model ⌘K
    asks, Prep and `orbit-agent ask` run (default Claude Code). Helpers of another harness are not reused.
  - `app/build-app.sh --name N --port P --state DIR [--home DIR]`: a second app (its own name, bundle id, port,
    folder; the settings in its Info.plist's LSEnvironment as `ORBIT_APP_*`, which win over a terminal's
    `ORBIT_PORT`). The app honours `$ORBIT_HOME`.
  - Settings → General → Week note template (`week_template`): a note whose text starts each new week note
    (`{days}` the Mon–Fri headings, `{monday}`, `{week}`).
- The Calendar (request #27): the cursor's meeting's note is a pane pinned under the list (the list scrolls, the
  pane stays; its top edge drags its height): read-only while you move, ⏎ edits it (autosaved), Esc back to the list.
  Where a note is made is a setting, `meeting_notes` (Settings → General): `{vault}/` or `{data}/`, then a path with
  `{date}` `{YYYY}` `{MM}` `{DD}` `{HH}` `{mm}` `{YYYYMM}` `{HHMM}` `{title}`; the server makes it
  (`POST /api/calendar/note`). A meeting's menu (⌘E, right-click) has **Quote to session ▸**: its title, time,
  organizer and note as a quote in that session's input. ⏎ and P no longer open the meeting in place.
- **Plugin API 2.1** (added): `[meeting]`, hooks on the Calendar's meetings: `related` (items shown when a meeting is
  opened: a link or a file; `POST /api/calendar/related`) and `prep` (context added to its Prep prompt)
  ([builddoc/plugins.md](builddoc/plugins.md) §5 Meetings).
- Fixed: a page load could fail with `pviewsReloaded is not defined` (plugins.js ran before pviews.js loaded).
- The left panel's background (light theme) is #f4f4f2, a quiet light grey, instead of #f5f4ef's warm paper.
- "Sessions" is "Groups" now (the left panel's tab on the Mac, the phone's first tab): a group holds its sessions and
  its other tabs. Under its sessions each group lists its file tabs (files, notes, plugin views, Schedules, Calendar),
  one quieter line each, in the tab row's order, icon and name under the sessions'; the filter finds them too. On the
  Mac a click opens it in that group, ⌘W or its menu closes it (on every screen); on the phone a tap opens it over the
  list (Back comes back), Calendar and Schedules with their data.
- The tab layout is the server's too (`orbit/desk/layout.py`): each group's file tabs (files, notes, plugin views,
  Schedules, Calendar), the tab row's order, and a file tab's side tabs; every page shows the same (`GET/POST
  /api/layout`, a `layout` event). A page writes it whole from the revision it had; one that is behind (409) puts
  what it added or closed since on top of the current one (a three-way merge) and writes again. Which tab a page
  shows, its scroll, a panel put away: the page's own. The tabs a page had move to the server the first time.
- A session's side panel is the server's, like its conversation (`orbit/desk/sides.py`): every screen shows the same
  tabs and the same one shown (the page, the phone, a page on another machine), and a change on one (open, pick, close,
  an agent's `orbit-agent show` or `open --side`) goes to all as a `side` event (`GET/POST /api/sessions/<id>/side`,
  `{"op": open|close|select|clear}`; `<state>/side.json`). Another machine's session's panel comes through this server
  (its paths as `/@name/…`, its agents' pages at `/api/remote-show/<name>/<id>`, sandboxed). Only the panel put away
  (⌘\, ⌃X 1 now puts it away) and its width are a screen's own; a file tab's side tabs stay the page's. The side tabs a
  page had for a session move to the server the first time it loads. The phone: a toggle in a session's title bar
  between the conversation and that panel (pages live, files drawn; picking or closing a tab there does it everywhere).
- Voice and agents speak of their own work in the first person. The voice: its backend's work is its own ("I'll look
  at the logs", "I'm still on request #3"), never "the agent" or the session's name unless asked; another agent is named
  only when a result says the work went there; Orbit is the platform that routes requests and reports their status.
  Its rules and Orbit's facts say "your backend" instead of "the agent". Agents: Orbit routes requests and gives the
  orbit-agent commands (their other tools are their own); reads and checks in the first person; a session they hand
  work to is named. Checked in real calls on gpt-live-1.1 and gpt-live-1 (Chinese).
- The live stream sends a streaming card's changes as patches to clients that ask (`/api/events?delta=1`: the page, the
  phone, another Orbit server following this one): `{"t": "cardp", sid, id, rev, base, ops}`, what changed since
  revision `base` (text appended, a step's fields, new steps; `orbit/desk/cardpatch.py`), instead of the whole card on
  every text and tool event; a client that missed one fetches the card (`GET /api/sessions/<id>/cards/<n>?exact=1`).
  Starting or stopping work and final states still go whole; clients that do not ask (plugins, older pages) get whole
  cards as before. A flood of 12 tool steps with long text: 0.6 MB to a patched client instead of 4.3 MB.
  A card read at work (its list, `?exact=1`) is the one last broadcast: its streaming text grows before its revision
  moves, and read then it would get text its next patch adds again. Each client gets the changes in the order made.
- The state folder (sessions, settings, logs) is made 0700 at start; the README says what 127.0.0.1 without a token means
  on a machine shared with others; the CI workflow reads the repository only (`permissions: contents: read`).
- Defaults: Appearance as Regular weight, the conversation in Consolas 14 (the built-in Inconsolata where Consolas, which
  cannot be shipped, is missing) at full width; the voice says when a task starts (`live_ack` on) and its sound is Single.
  Saved preferences stay as they are.
- The side panel can be hidden and shown again, its tabs kept: ⌘\\, the panel's ◫ button, the session's menu → Side
  tab, or ⋯; hidden, a handle at the right edge (◫ and the number of tabs) brings it back. Kept per tab, across reloads.
- `orbit-agent show` / `open --side`: the side tab goes with the session that asked (its tab's side tabs), not the tab
  in front; from another tab a toast offers to go there. The events carry `sid`.
- The status bar's usage: Antigravity (agy) too, from `agy -p /usage` (a local command: no model call, no
  conversation): Gemini and Claude/GPT, each week and 5 h, the tightest on the bar; the popover lays out more than two
  windows as a grid.
- The status bar's usage: OpenAI (GPT Live voice) next to Claude and Codex: this month's cost, or, with a balance given
  in Settings → Voice, what is left (a bar of the share spent); its popover has calls, talking and standby minutes and
  the talking time left. Orbit's own calls only (`/api/live/usage`): an API key cannot read the account.
- Voice: a soft rising ding once the call is connected and listening (speak then), a falling one when it ends;
  Settings → Voice → Sound: Off, Wood, Low bell, Single (default), Bowl, Temple; a click plays it (`live_chime`,
  `live_chime_sound`). Made by the page (Web Audio), desktop and phone.
- Voice: several OpenAI API keys, each with a name, tried in order: one refused (a bad key, no quota, rate-limited:
  401 / 402 / 403 / 429) and the call goes on with the next (its sideband attaches with the key that made it); a key
  refused in the last 10 minutes is tried after the others; Settings → Voice lists them (name edited in place, last
  four characters, how each last did, ↑ to try it earlier, Check, Remove, Add). `POST /api/keys` takes
  `{"op": "add"|"update"|"remove"|"move", "id", "value", "label", "to"}` (`{"name", "value"}` alone still works);
  `/api/keys/check` takes an `id`. An older `_orbit.json` (one key) reads as a one-key list.
- **Settings → Voice** (GPT Live): the OpenAI API key (moved from General), the voice (38 built-in voices, each with
  the language and accent OpenAI lists), the language it speaks (or the user's), results brief or detailed, a spoken
  "on it" when a task starts (off: the automatic acknowledgement is cancelled, as before), standby after 10–60 s or
  never, how clear speech must be to wake it (vad_threshold 0.3 / 0.5 / 0.7), the session's recent work at the start,
  the model, and the user's own instructions (after Orbit's; up to 4 000 characters). Settings `live_*`, checked by
  the desk (`orbit/services/live/config.py`), read as a call starts; `GET /api/live/options` lists the choices.
- The voice button: five bars in a circle where Send is while the input is empty; in a call they follow the sound.
- Voice: after each request the voice is told what became of it (running, queued behind another, joined to the
  waiting one), so it says "queued" instead of guessing; "via Voice" on a card explains what the agent got.
- Voice: "take it back" (撤回 · 撤掉 · 收回 · 取消 · 算了 · cancel · withdraw · stop …, when those words are all the
  user said about it) withdraws the call's request that has not started, or stops the one running, and the voice
  says which; "cancel the nightly job" stays a task for the agent.
- Voice: "do this first" (打断一下 · 先做这个 · 插队 · 紧急 · urgent · do this first …, before or after the words): the
  running request stops, the new one goes ahead of the waiting ones and runs at once, and those keep their order after
  it (`send` takes `first`). The voice's session state names a turn the agent took by itself as such, not as a request.
- Voice, more natural (from a study of Codex's voice and 27 real calls): the voice is told the session's state
  silently whenever it changes (what runs and for how long, the agent's latest words, what is queued, the last
  result; also for requests typed or made elsewhere, and another machine's session), so "is it done / how long?" is
  answered at once instead of queued behind the work; no more "still running: Bash …" lines every 10 s; a result
  waits while you are talking; results are short facts to say (named by the request, no Markdown, ~75 words brief,
  the next part kept as silent context for follow-ups) instead of a quoted report; "don't state the answer until the
  result arrives" at each request; in auto language, Chinese is Mandarin in Simplified characters. By voice you can
  also stop the running request ("停止当前在跑的任务", whoever made it; the queue goes on), add to it while it runs
  ("插一句 …", "tell it to …"), or move a waiting request into it ("插进去"); the voice knows it can.
- One voice call at a time, everywhere: the server refuses a second (409, saying which call is on) instead of
  hanging up the first. Every page knows of a call another page or device holds (its state events, `GET /api/live`):
  the voice buttons wait (grey), the pill says "another device" (go to its session, hang it up), and ⌘⇧V hangs up
  whichever call is on, never starting a second. The phone's voice button waits the same way.
- A voice call shows wherever you are: a pill first in the status bar (its state and session; red when muted); a
  click: mute or unmute, go to its session (or show the voice again), hang up. From anywhere in the window (a dialog,
  a menu, an editor, the terminal) ⌘⇧A mutes or unmutes and ⌘⇧V hangs up. Mute is shared with the phone's.
- Settings → Appearance → Text weight: Thin · Light · Regular · Medium · Bold. Every weight moves together (`--wd`;
  the built-in fonts are variable), so bold stays as much bolder; Thin and Light draw thinner antialiased strokes.
- Settings → Voice → Usage: what Orbit's calls cost this month (from GPT Live's own active and standby seconds, by the
  second: $0.05 and $0.02 a minute), and, once the user gives what is left on their OpenAI account (an API key cannot
  read it: the billing endpoints answer 403), the talking time it buys, counting down by the calls since.
  `GET /api/live/usage`; settings `live_budget`, `live_budget_at`.
- In a call, a gyroscope takes the input's place: a cloud core that swells with the sound (yours and the voice's) and
  two rings tumbling around it in 3D (each ring's near half in front of the core, its far half behind), faster while
  the voice speaks, nearly still and grey in standby; ⌨ types instead (the call goes on; the state by
  the voice button brings the orb back). The voice button is five blue bars beside Send, as big as its arrow.
- A turn the agent takes on its own (Claude Code wakes when a background subagent or command finishes) is a card of
  its own, "On its own · after a background agent finished" and the task, with its steps, the subagents it launches
  (their live work and report) and its answer; before, only its text showed (a follow-up line) and a subagent it
  launched was nowhere. Requests sent meanwhile queue behind it, Stop stops it; history rebuilds it after a restart.
  One that only talks stays a follow-up. Any harness on the engine (`auto` event; Harness Protocol 1.3: a `result`
  without a request, history lines with `"auto"`).
- Sessions list: a session clicked in a filtered list no longer reshuffles the rows under the pointer; the filter
  clears once the pointer has been away a moment, the rows sliding to their places.
- A voice call's note card is folded to its head (turns, the requests it made); a click shows the transcript, where
  each of the user's turns that became a request says so (`→ #706`): the words no longer seem sent twice.
- Voice calls from the phone (`/m/`): the five blue bars beside Send; in a call the gyroscope takes the input's place
  with the state word and big Type · End · Mute controls, captions (folded or not) at the end of the conversation,
  and the note card folds as on the desktop. Away from the call's session (another session, the list, another area)
  a bar says "in a call with X": a tap goes back, its red button hangs up. iOS: the microphone, the AudioContext and
  the voice's playback start in the tap; back from the background the sound resumes, a microphone iOS ended comes
  back with one tap (same call), and the event stream's reconnect re-syncs the call (`GET /api/live`); leaving the
  page hangs up. The call itself (WebRTC, phases, captions, the sound level, the gyroscope) is now
  `web/src/voice-core.js`, shared by both pages; a call starting no longer takes the last call's "ended" for its own.
- Voice calls work with another machine's sessions too (`name~id`): this server holds the call; the requests,
  the session's context and the call's note card go to that machine through the federation, and its answers come
  back on this server's events to be spoken. A session's `steer` takes `origin` and `client_id` like `send`; `note`
  adds a note card (`POST /api/sessions/ID/note {title, text, origin}`).
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
