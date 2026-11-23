# Plugins (plugin API 2.0)

A plugin adds to Orbit without changing it, so Orbit updates with `git pull` and the plugin keeps working. It is a
folder with a `plugin.toml`, usually a git repository of its own, in any language: Orbit runs its programs and talks to
them over stdin/stdout or HTTP; nothing of the plugin runs inside Orbit's process.

| A plugin can add | Declared as | Orbit |
|---|---|---|
| **an agent** (a harness: ⌘N, history, Stop, Continue) | `[harness.<id>]` | runs it per session; it speaks the [Harness Protocol](harness-protocol.md) |
| **a service** (a chat bridge, a webhook, a watcher) | `[service.<id>]` | keeps it running beside the server; it uses the HTTP API below |
| **a command for agents** | `[cli]` | runs it for `orbit-agent p <plugin> ARGS…`, with the plugin's secrets |
| **a sentence for agents** | `[agent] hint` | adds it to the `[orbit]` line every agent process starts with |
| **secrets** (tokens, passwords) | `secrets = [...]` | keeps them on this machine (0600) and gives them to the plugin's processes |
| **status items** (a quota, a count) | set over HTTP | shows them at the right of the status bar (§5) |
| **threads** (a Slack thread, a ticket's comments) | `[[ui.tab]] view = "thread"` | draws it like a conversation (its Markdown, editor, ⌘F, image viewer, quote menu); replies go to the plugin (§5 Views, 2.0) |
| **lists** (an inbox) | `view = "list"` on `[[ui.sidebar]]` / `[[ui.panel]]` | draws an item source like the Sessions list: a tab after Sessions · Files · Notes · Misc, or the side pane (§5 Views, 2.0) |
| **docs** (a dashboard, a status page) | `view = "doc"` on `[[ui.tab]]` / `[[ui.sidebar]]` / `[[ui.panel]]` | draws Markdown the plugin pushes, live (§5 Views, 2.0) |
| **actions** (More menu, right-click an answer or a thread's message) | `[[ui.action]]`, `[[ui.message.menu]]` | runs the plugin's command with what it acts on (§5) |
| **main tabs of its own kinds** | `[[ui.tab]]` | real tabs: ⌘W, history, a session beside, kept across reloads (§5, 1.1) |
| **notifications** | `POST …/notify` | the bell and a macOS notification; a click opens its tab (§5, 1.1) |
| **people** (who a name is: an avatar, a handle) | `[people]` | asks its command about the names it shows (the Calendar's people), caches the answers (§5, 1.2) |
| **shortcuts** (a key for its tab or panel) | `shortcut` on `[[ui.sidebar]]` / `[[ui.panel]]` | binds it wherever the keyboard is; shows it in the tooltip, the More menu and ⌘/ (§5, 1.3) |
| **a ⌘O tab** (threads, tickets, commands) | `[[ui.palette]]` | keeps the items it pushes (or a command refreshes), filters them in the page; a live search goes to its running service (§5, 1.3) |
| **its agent's conversations** to resume | the source `conversations` | Resume another session lists them with Claude Code's and Codex's, without running anything (§5, 1.3) |

Examples in this repository: `examples/opencode/` (an agent), `examples/webhook/` (a service, a secret, a command, a
hint: requests to sessions over HTTP, answers to a callback; a chat bridge, Slack …, has its shape), `examples/hello/`
(a status item kept by a service, a doc panel, actions, a list, a thread its service fetches and answers, a doc tab, a
toy `[people]`, a ⌘O source its service pushes and searches).

## 1. Installing one

```toml
# ~/.orbit/config.toml
plugin_paths = ["~/src/acme-orbit"]   # a folder anywhere (relative: ~/.orbit's); or put it in ~/.orbit/plugins/
disabled = ["some-plugin"]            # found, not loaded
```

Then restart Orbit's server (the app: quit and open it). `orbit plugin list` shows what loaded and why not;
`orbit plugin check [DIR]` also asks its agents to describe themselves. Settings → Plugins shows the same, with each
service's state and log, and the secrets to set.

| Command | |
|---|---|
| `orbit plugin secret NAME KEY [--clear]` | keep a secret (asked for, not echoed; or piped in); its services restart with it |
| `orbit plugin restart NAME [SERVICE]` | restart its services |
| `orbit plugin logs NAME [SERVICE] [-n N]` | the end of a service's log |

## 2. plugin.toml

```toml
[plugin]
name = "acme"                    # lower-case letters, digits, - _ (the folder name if absent)
version = "0.3.0"
requires_api = ">=2,<3"          # the plugin API it needs (CHANGELOG.md says what each version added)
schemes = ["slack"]              # (2.0) link schemes its Markdown may use besides https, http, mailto
description = "Acme's agent and chat bridge"
secrets = ["ACME_TOKEN", "SLACK_APP_TOKEN"]   # environment variable names

[harness.devin]                  # an agent: builddoc/harness-protocol.md
label = "Devin"
command = ["devin-orbit"]        # relative: this folder's program; else found on PATH
local_only = true                # this machine only: never offered for, or run by, another machine's server
icon = "ui/devin.svg"            # its mark in the session lists (else its initial)

[service.bridge]                 # a process Orbit keeps running
command = ["uv", "run", "bridge.py"]   # its own environment and dependencies (uv, a venv, a binary: its choice)
files = "/files/"                # (2.0) its paths the svc: proxy may fetch for the page (a thread's pictures)
restart = "on-failure"           # on-failure (default: again unless it exited 0) · always · never
env = { BRIDGE_CHANNEL = "#ops" }

[cli]                            # `orbit-agent p acme ARGS…`
command = ["acme-cli"]

[agent]                          # one sentence (300 characters at most), on the [orbit] line
hint = "Slack: `orbit-agent p acme post CHANNEL TEXT`, `orbit-agent p acme thread URL`"

[[ui.panel]]                     # in the side pane (§5)
id = "status"
title = "Acme status"
view = "doc"                     # (2.0) a doc it pushes (a list, a thread: §5 Views)
shortcut = "Mod-Shift-S"         # (1.3) ⌘⇧S (Ctrl+Shift+S off macOS) toggles it; or "Ctrl-Alt-S" (§5 Shortcuts)

[[ui.tab]]                       # (2.0) a native thread: Orbit draws it like a conversation (§5 Views)
kind = "thread"
title = "Slack thread"
view = "thread"

[[ui.action]]                    # a menu item that runs [cli] (§5)
id = "share"
title = "Share to Slack…"
where = "answer"                 # answer: right-click an answer · menu: the More menu
args = ["share"]

[people]                         # (1.2) who a name is: runs [cli] with these args (§5)
args = ["people"]

[[ui.palette]]                   # (1.3) a tab in ⌘O: items the bridge pushes; typing also asks it (§5 Item sources)
id = "slack"
title = "Slack"
open = "tab"                     # choosing one: tab (a [[ui.tab]] of it) · action (runs [cli]) · url
search = true                    # its service answers live searches
```

A plugin that cannot load (a bad manifest, an API it does not fit, a harness id taken, two plugins with one name) is
skipped and reported; Orbit starts anyway.

## 3. What a plugin's processes get

Every process of the plugin (its agents, services and command) runs in its folder with Orbit's environment plus:

| Variable | |
|---|---|
| `ORBIT_URL` | the server, `http://127.0.0.1:<port>` (also `ORBIT_PORT`) |
| `ORBIT_PLUGIN` | the plugin's name |
| `ORBIT_PLUGIN_TOKEN` | sent as `X-Orbit-Plugin-Token`: requests it sends are marked as this plugin's (new each server start) |
| `ORBIT_PLUGIN_DATA` | a folder for what it keeps (`~/.orbit/plugin-data/<name>/`) |
| each declared secret | its value, if set |
| `ORBIT_SESSION` | (its command, run for an agent) the session that ran it |
| `ORBIT_SERVICE_PORT` | (1.3, a service) a free port Orbit picked for it (kept across its restarts while it stays free): where it answers Orbit's live search (§5 Item sources) |

`PATH` starts with Orbit's `cli/`, so `orbit-agent` works.

**Services.** Orbit starts them once its server listens, stops them when it stops (SIGTERM to the process group,
SIGKILL after 5 s), and logs their stdout and stderr to `~/.orbit/logs/plugins/<plugin>.<service>.log`. A service
whose plugin's secrets are not all set waits ("needs secrets") and starts once they are. Restarts wait 1, 2, 4 … 60 s;
five failures within five minutes stop it ("failed") until it is restarted.

**Secrets** live in `~/.orbit/secrets/<plugin>.json` (0600, in a 0700 folder). The HTTP API never returns them;
setting one is refused from another machine. Keep them out of the plugin's folder and its git history.

**The command** (`[cli]`) is run by the server, not by the agent: the agent never sees the secrets, only the output.
It gets the arguments (and stdin with `orbit-agent p --stdin NAME …`), 120 s, and its exit code is the agent's.

## 4. The HTTP API (v1)

`ORBIT_URL` + the paths below, JSON in and out. From this machine no token is needed; send `X-Orbit-Plugin-Token`
anyway (it marks what you send). What is listed here is the API's promise: fields may be added in a minor version,
nothing listed changes or goes before a major one. Anything else the server answers is Orbit's own page's and may
change.

### Sessions and requests

| | |
|---|---|
| `GET /api/version` | `{"version", "api"}` |
| `GET /api/sessions?local=1` | `{"sessions": [Session]}`: this machine's open sessions |
| `POST /api/sessions` `{"cwd", "name"?, "harness"?, "model"?, "mode"?, "effort"?, "group"?}` | a new session → Session |
| `POST /api/sessions/{id}/send` `{"text", "origin"?}` | a request (queued behind a running one) → Card |
| `GET /api/sessions/{id}/cards` | `{"cards": [Card]}` |
| `GET /api/sessions/{id}/cards/{n}?wait=S` | `{"card": Card, "final": bool}`: once the request is final, or after S s (≤ 3600). A request that paused because its agent went away and that Orbit continues by itself is followed to the request that continues it |
| `POST /api/sessions/{id}/steer` `{"text"}` | a message for the running request (or a new one) → Card with `steered` |
| `POST /api/sessions/{id}/stop` | stop the running request |
| `GET /api/events` | Server-Sent Events, below |

**Session**: `id`, `name`, `cwd`, `harness`, `model`, `mode`, `state` (`starting` · `ready` · `working` ·
`not ready` · `stopped`), `error`, `queued`, `closed`.

**Card** (one request): `id` (n, per session), `sid`, `text`, `state` (`queued` · `thinking` · `running` · `completed`
· `error` · `cancelled` · `paused`), `answer`, `error`, `time`, `finished`, `steps` ([{`kind`: tool · note, `name`,
`summary`, `done`, `error`}]), `origin`. Final states: completed, error, cancelled, paused.

**origin** `{"label", "url"?, "data"?}`: where a request came from. The card shows "via <label>" (a link to `url`:
https, http or slack); `data` (JSON, 8000 characters at most) is yours, given back on the card: what you need to
answer there (a channel, a thread). Orbit adds `plugin` (from your token). It survives restarts and goes along when
Orbit continues a paused request.

### Events

`GET /api/events` streams `data: {json}` lines; the first is `{"t": "hello", "sessions": [Session], …}`, then:

| `t` | |
|---|---|
| `card` | `{"card": Card}`: a request changed (created, streaming, a step, finished) |
| `session` | `{"session": Session}` |
| `plugin-items` | (1.3) `{"plugin", "source", "updated"}`: a source's list changed |
| `plugin-thread` | (2.0) `{"plugin", "kind", "key", "rev", "change"}`: a thread changed (`change` has what did: `append`, `update`, `remove`, `replace`, its fields; `whole`: fetch it) |
| `plugin-doc` | (2.0) `{"plugin", "id", "rev"}`: a doc changed |

Other `t` values are the page's and may change. To take answers back, watch for `card` events whose
`card.origin.plugin` is yours and whose state is final, or wait on each request with `?wait=`.

### The plugin's own

| | |
|---|---|
| `GET /api/plugins` | every plugin found: `ok`, `error`, `harnesses`, `services` (state, error; 1.3: `port`), `secrets` ([{`key`, `set`}]), `people` (bool, 1.2) and `people_error` (its last failure, until it answers again), `warnings` (1.3: what was ignored in a plugin that loaded, e.g. a shortcut; also in `error`), `palette` (1.3: its sources' ids) and `palette_errors` ({source: its refresh command's last failure}) |
| `PUT /api/plugins/{name}/items/{source}` `{"items": [Item]}` | (1.3) the source's whole list → `{"count", "dropped"}`. Only the plugin itself: its `X-Orbit-Plugin-Token`, else 403; a source it did not declare: 400 (§5 Item sources) |
| `POST /api/plugins/{name}/items/{source}` `{"upsert": [Item], "remove": [key]}` | (1.3) changes only, the same rules |
| `GET /api/palette` (`?plugin=&source=`: one) | (1.3) `{"sources": [{"plugin", "id", "title", "open", "kind"?, "search", "refresh", "updated", "count", "error"?, "items"}]}`, the items newest first |
| `PUT /api/plugins/{name}/threads/{kind}/{key}` · `PATCH …` | (2.0) a thread, whole or its changes (§5 Views); its token, its declared kinds; 1 MB a request |
| `GET /api/plugins/{name}/threads/{kind}/{key}` (`?before=ID`) | (2.0) what Orbit has (asking the plugin in the background); older messages through its service |
| `PUT /api/plugins/{name}/docs/{id}` · `GET …` | (2.0) a doc (§5 Views) |
| `GET /api/plugins/{name}/svc/PATH` | (2.0) a file of its service, for the page (§5 Views: the svc: proxy) |
| `GET /api/palette/search?plugin=&source=&q=` | (1.3) `{"items"}` from the plugin's service (q of 2+ characters); 503 when it is down, 504 after 1.5 s, 502 a bad answer (each with `"error"` and `"items": []`) |
| `POST /api/plugins/{name}/secret` `{"key", "value"}` | keep a secret (`null`: forget it); its services restart (this machine only) |
| `POST /api/plugins/{name}/restart` `{"service"?}` | (this machine only) |
| `GET /api/plugins/{name}/log?service=ID&lines=N` | (this machine only) |
| `POST /api/plugins/{name}/run` `{"args", "stdin"?, "session"?}` | its command → `{"code", "stdout", "stderr"}` (this machine only) |
| `GET /api/people?names=A,B` · `POST /api/people` `{"names"}` | (1.2) who they are, from the plugins' `[people]` (§5) (this machine only) |

## 5. Adding to the page

Nothing of a plugin runs inside Orbit's page, and since 2.0 none of its pages either: it declares what it adds and
gives data, and Orbit draws it with its own code (Views, below).

**Status items.** `POST /api/plugins/{name}/status` `{"id", "text", "title"?, "level"? (info · warn · error),
"panel"? or "url"?}` puts `text` (60 characters) at the right of the status bar of every open window, at once;
`"text": ""` removes it. A click opens its panel, or its URL in the browser. A service usually keeps one up to date
(`examples/hello`: "N working"). They last until the plugin removes them or the server stops. This machine only.

**Panels.** A `[[ui.panel]]` is one of its views (a doc, a list, a thread: below) in the side pane, opened from the
More menu, a status item or its shortcut.

**Actions.** A `[[ui.action]]` runs the plugin's `[cli]` command with its `args`, and what it acts on as JSON on
stdin; the first line it prints (or the last of its errors) is shown as a toast. `where = "menu"`: in the More menu,
stdin `{"session", "file"}` (what the user is looking at). `where = "answer"`: right-click an answer, stdin
`{"session", "card": {"id", "text", "answer", "state", "origin"}}`. This machine's page only.

### A left-panel tab (1.1)

```toml
[[ui.sidebar]]
id = "inbox"
title = "Inbox"            # its tab (shown as its icon, when it gives one of Orbit's line icons; the title on hover)
icon = "quote"
view = "list"              # (2.0) its item source drawn like the Sessions list; or "doc"
source = "inbox"           # one of its [[ui.palette]] ids
order = 50                 # Sessions 10, Files 20, Notes 30, Misc 40
shortcut = "Ctrl-Alt-I"    # (1.3) shows this tab with the keyboard; again: the tab before (Shortcuts, below)
```

⌘⇧[ ⌘⇧] reach it like the others. A count on the tab: `POST /api/plugins/{name}/badge` `{"id", "count", "level"?}`
from its programs (`0` hides it); a list without one counts its unread items.

### Main tabs of the plugin's kinds (1.1)

```toml
[[ui.tab]]
kind = "thread"            # Orbit names a tab by (plugin, kind, key)
title = "Conversation"     # the default title (the opener and the view's data can set the tab's own)
icon = "quote"
view = "thread"            # (2.0) or "doc"
service = "bridge"         # (2.0) the service Orbit asks for it, when the plugin has several

[[ui.tab.menu]]            # items of the tab's ⌘E menu: run [cli] with stdin {"kind", "key"}
id = "source"
title = "Open at the source"
args = ["open-source"]
```

A plugin tab is a real main tab: in the tab row with its icon and title (and an unread dot), ⌘W, ⌘1–9, ⌘⇧[ ⌘⇧],
history and ⌘R include it, a session (any side tab) sits beside it, it can be a side tab itself, and it is back after
a reload. Opening the same (kind, key) again selects it. Opened from its programs:
`POST /api/plugins/{name}/open-tab` `{"kind", "key", "title"?, "focus"?}` (this machine's pages), from a list's item,
a ⌘O item or a notification.

**Notifications.** `POST /api/plugins/{name}/notify` `{"title", "body", "open"?: {"kind", "key", "title"?}}`: an entry
in every page's bell list (a toast too) and a macOS notification; a click opens that tab (opening it clears that
plugin's notices for it).

`GET /api/plugin-ui` → `{"status", "panels", "actions", "sidebars", "tabs", "badges", "palette", "messages", "schemes",
"local"}` is what the page draws (each panel, left tab and tab kind with its `view`; 1.3: a panel or sidebar with a
key has `shortcut`, the canonical `"Mod-Shift-S"` / `"Ctrl-Alt-S"` it got; `palette`: the item sources, without their
items); `/api/events` sends `plugin-status`, `plugin-badges`, `plugin-open-tab`, `plugin-notify`, `plugin-thread` and
`plugin-doc` when they change.

### Views (2.0)

A plugin gives **data**; Orbit draws it in its own page with the conversation's code, so a plugin's thread reads,
scrolls, searches and is answered exactly like a conversation, on every engine and later on the phone.
(builddoc/plugin-views.md has the design and why.) Three views:

| `view` | On | Shows |
|---|---|---|
| `"thread"` | `[[ui.tab]]`, `[[ui.panel]]` (`kind`, `key`) | a chat thread: others' messages as answers (an author line: avatar, name, time; one line for an author's messages within 5 minutes), the user's own in a bubble; Orbit's editor below |
| `"list"` | `[[ui.sidebar]]`, `[[ui.panel]]` (`source`) | an item source (1.3) drawn like the Sessions list: grouped by `group`, an unread dot, the filter box, ↑↓ ⏎, a menu (Open, Open in the side tab, the tab kind's menu) |
| `"doc"` | `[[ui.tab]]` (its key is the doc's id), `[[ui.sidebar]]`, `[[ui.panel]]` (`doc`, else its id) | Markdown, in the answers' type, redrawn when it changes |

An entry with `path` (an iframe page of 1.x) is ignored with an error; so is a `view` it cannot draw, a list without
one of its sources, a thread panel without a thread kind and key. The plugin loads anyway.

**A thread.**

```json
{"title": "#infra · deploy failed", "sub": "Slack · 14 replies", "url": "https://…",
 "messages": [{"id": "1732…", "author": {"name": "Ana Li", "me": false, "avatar": "https://…"},
               "time": 1732300000, "markdown": "It failed at **step 3**", "url": "https://…",
               "files": [{"name": "log.png", "url": "svc:/files/F123", "image": true}],
               "reactions": [{"emoji": "👍", "count": 2, "me": true}], "edited": false,
               "replies": {"count": 3, "kind": "thread", "key": "C1/1732…"}, "client_id": "…"}],
 "composer": {"placeholder": "Reply in #infra", "enabled": true}, "more": true, "unread": false}
```

Checked as items are: an avatar `https:` or `data:image/…` (else the `[people]` plugins', else initials), `url`
https, http or one of its `schemes`, `files[].url` `https:` or `svc:/path`, `replies` one of its thread kinds; a
message is 32 KB at most. A request with a bad one is refused whole (400, `"error"` naming it).

| | |
|---|---|
| `PUT /api/plugins/{name}/threads/{kind}/{key}` | the whole thread (`key` URL-encoded: a `/` as `%2F`) → `{"count", "dropped", "rev"}` |
| `PATCH …` `{"append", "update", "remove": [ids], "title", "sub", "url", "composer", "more", "unread"}` | changes only → `{"count", "rev"}` |

Only the plugin, by its token, and only its `view = "thread"` kinds; 1 MB a request. Orbit keeps the newest 2 000
messages of a thread and 300 threads per plugin (the least recently opened go), in memory and in
`<state>/plugin-threads/`. `unread: true` (or a message from someone else while its tab is not in view) puts the
tab's unread dot; `title` names the tab.

**Asked for.** When a thread opens with no copy, or one older than 60 s (or on Reload), Orbit asks in the background
and draws what it has meanwhile ("Loading…" only when it has nothing):

```
GET http://127.0.0.1:$ORBIT_SERVICE_PORT/orbit/thread?kind=thread&key=C1/1732…   (X-Orbit-Plugin-Token: its token)
→ a thread
```

Without a service (the tab's `service`, else the plugin's only one): `[cli] thread KIND KEY`, printing the thread,
one run per key at a time. Scrolled to the top with `more: true`, the page asks `GET …?before=<the oldest id>` (the
service only): `{"messages", "more"}` go above the others, the view keeps its place; they stay in the page.

**Replies.** The editor is Orbit's input editor (live preview, ⌘⏎, a draft per tab); a pasted or dropped file is
uploaded to this machine and goes as a file, not as text. On send a message shows at once, "Sending…", kept by the
server (every window, a reload); Orbit calls

```
POST http://127.0.0.1:$ORBIT_SERVICE_PORT/orbit/send
{"kind", "key", "markdown", "files": [{"name", "path"}], "client_id"}   → {"id"?}
```

(without a service: `[cli] send` with that on stdin, its first line `{"id"}`). When the plugin pushes the real
message with the same `client_id` (or the `id` it answered), it takes the pending one's place. A failure shows
"Not sent" with Retry (the same `client_id` again: de-duplicate by it) and Edit. `composer.enabled: false` shows no
editor. Replies are typed on this machine's page only.

**A message's menu**: Copy, Copy as Markdown, Open at the source (`url`), Quote in a session… (into a session's input,
with the thread's title), Send to a session… (the message and the thread's last 30 messages, 20 KB at most, as a
request whose origin is the thread), and the plugin's own:

```toml
[[ui.message.menu]]        # [cli] with {"kind", "key", "message"} on stdin; this machine's page only
id = "save"
title = "Save to Notion"
args = ["save-message"]
```

**A doc.** `PUT /api/plugins/{name}/docs/{id}` `{"title", "markdown", "rev"?}` (a `rev` older than the one kept: 409);
or asked of the service as a thread is (`GET /orbit/doc?id=`; `[cli] doc ID`). 1 MB of Markdown; 300 docs per plugin.

**Plugin-mode Markdown.** What a plugin sends is drawn with the answers' rules (code blocks with Copy, KaTeX, tables,
the image viewer), except: no local paths (no `/api/raw`, no file links, no `~/`); images only `https:` and `svc:`;
links only `https:`, `http:`, `mailto:` and the plugin's `schemes` (`schemes = ["slack"]` under `[plugin]`). Anything
else stays as its text.

**The svc: proxy.** A `svc:/files/F123` URL is fetched through Orbit, `GET /api/plugins/{name}/svc/files/F123`, from the
service that declares that prefix (`files = "/files/"`), with its token: private files load with the service's login
and the page never sees it. It is not a gateway:
- GET and HEAD only; only under a declared `files` prefix, never `/orbit/`; a plain path (no `..`, `//`, `\`, no
  encoded `.` `/` `\` `%` or control characters); `http.client` to 127.0.0.1; a redirect is not followed (502);
- only `Range` is forwarded, and Orbit adds `X-Orbit-Proxy: 1` (refuse what you never serve to a page);
- the answer is inert: png, jpeg, gif, webp by their bytes; SVG only as an `<img>`; anything else
  `application/octet-stream`, an attachment; always `X-Content-Type-Options: nosniff` and
  `Content-Security-Policy: sandbox`; only `Content-Length`, `ETag`, `Cache-Control` (and a 206's `Content-Range`)
  passed back;
- 20 MB, 10 s, 4 at once per plugin. Other machines' pages (with the server's token) may use it.

`examples/hello/` has each: a doc panel, a list of answers grouped by session, a thread per session (replies go to the
session, its answer comes back live, pasted pictures through the proxy), a doc tab, a message menu.

### Shortcuts (1.3)

A `[[ui.sidebar]]` or a `[[ui.panel]]` may have a key:

```toml
shortcut = "Mod-Shift-S"   # Mod: ⌘ on macOS, Ctrl elsewhere (⌘⇧S · Ctrl+Shift+S)
shortcut = "Ctrl-Alt-S"    # ⌃⌥S, on every system
```

Only these two forms, with one letter or digit. **A left tab's key** shows that tab with the keyboard in it (the left
panel opens if hidden); pressed while the tab shows, it goes back to the tab you were on before. **A panel's key**
opens the panel in the side pane (with the keyboard), or closes it when it is the side tab shown. The key works
wherever the keyboard is: the main page, the input and the editors, the ⌘J shell, side tabs and a plugin's views. It is shown in the left tab's tooltip, beside the panel in the More menu, and in the Keyboard
shortcuts sheet (⌘/) under the plugin's name.

Checked when the plugin loads. A key that is not one of the two forms, one Orbit keeps (its own shortcuts, the app's
menu, the editors', and a few of the system's and browsers': `RESERVED_SHORTCUTS` in `orbit/plugins.py`, e.g. ⌘⇧F,
⌘⇧T, ⌘⇧K, ⌘⇧3, ⌃⌥H/J/K/L), or one an earlier plugin (in load order) or an earlier item of the same plugin took, is
**ignored with an error**; the plugin still loads, its tab or panel without a key. The error shows where a plugin's
problems show: Settings → Plugins, `orbit plugin list` / `check`, `error` (and `warnings`) in `/api/plugins`.

### Item sources: a tab in ⌘O (1.3)

⌘O's own tabs never start a process per key: Sessions, Files and Other sessions are fetched once when the tab opens
and filtered in the page; History is searched by the server, in its own process. A plugin gets the same two, with the
same costs: **items it keeps in Orbit**, and a **live search answered by its service**, which runs anyway.

```toml
[[ui.palette]]             # a tab in ⌘O, after Orbit's
id = "slack"
title = "Slack"
open = "tab"               # choosing an item: "tab" (default; the plugin's [[ui.tab]] of the item's `kind`, else this
kind = "thread"            #   `kind`, else its only one; key = the item's key) · "action" (runs [cli] with `args` and
                           #   {"source", "item"} on stdin, as an action's first line shows) · "url" (the item's url)
search = true              # (optional) its service answers live searches: true when it has one service, else its id
refresh = { args = ["palette", "slack"], every = "5m" }   # (optional) a command keeps the list (a plugin without a service)
icon = "quote"             # (optional) the items' icon when they give none (one of Orbit's line icons)
```

A bad entry (no id or title, an `open` it cannot do, an id given twice, `conversations`) is **ignored with an error**,
the plugin still loads (as a shortcut: Settings → Plugins, `orbit plugin check`, `error` and `warnings` in
`/api/plugins`). So is a `search` it cannot answer (no service; several and not named), and a `refresh` without
`[cli]`; a refresh more often than once a minute runs once a minute, with an error.

**An item**: `{"key", "label", "kind"?, "detail"?, "time"?, "match"?, "icon"?, "tag"?, "url"?, "data"?}`, and for a
list view (2.0) `unread` (true: its dot), `group` (rows under that heading) and `open` (`{"kind", "key"}`: that tab of
the plugin; `{"url"}`; else what its source opens). `key` is
unique in the source (1–300 characters); `time` (epoch seconds) puts the newest first when nothing is typed; `match`
is more text to match (a channel, people, a ticket id), not shown; `url` is https or http; `data` is yours (it comes
back to an action). Other fields are dropped. An item is 2 KB at most (its JSON): a request with a bigger one, or a
bad one, is refused as a whole with `"error"` naming it. A source keeps 10 000 items, the newest by `time`
(`"dropped"` says how many went).

**Pushed by its service** (the normal case): `PUT …/items/{source}` the whole list (at start, a resync),
`POST …/items/{source}` `{"upsert", "remove"}` as things change. Only the plugin writes its sources (its token), and
only those it declared. Orbit keeps them in memory and in `<state>/plugin-items/<plugin>.<source>.json`, there at once
after a restart; a resync that changes nothing is not told to the pages.

**Refreshed by a command** (`refresh`): Orbit runs `[cli] ARGS` in the background every `every` ("90s", "5m", "1h"
or seconds; one minute at least), and when ⌘O opens with a list older than that, one run at a time: never per key,
and the page never waits for it. Its stdout, JSON lines of items, replaces the list (a bad line is left out); a run
that fails (exits non-zero, 120 s) keeps the list it had. Failures go to
`~/.orbit/logs/plugins/<plugin>.palette.log` and `palette_errors` in `/api/plugins` (and `error` on the source).

**In ⌘O** each source is a tab after Orbit's own, its items fetched when it opens and filtered in the page with
Orbit's matcher (fuzzy on the label, then `match`, `detail` and `tag`; 4+ characters of the key). A source that
changes while it shows (`plugin-items`) is drawn again, the query and the selection kept. Actions run on this
machine's page only.

**Live search** (`search`): for things that cannot be pushed (all of Slack, a Jira query). Orbit gives each service a
port when it starts (`$ORBIT_SERVICE_PORT`) and, for 2 characters or more, 150 ms after the last key, asks it:

```
GET http://127.0.0.1:$ORBIT_SERVICE_PORT/orbit/search?source=slack&q=deploy&limit=30
X-Orbit-Plugin-Token: <the plugin's token: the request is Orbit's>
→ {"items": [Item]}
```

It has 1.5 s. One request is in flight per tab: a newer query aborts the older, and an answer to an older query is
never shown. Its items come below the source's own, under "Search in Slack", without those already shown (by key);
a bad one is left out. A service that is down, slow or answers badly leaves the tab working on its items: the line
says "not available", no toast.

`examples/hello/` has one: its service pushes every finished request as an item (choosing one opens its answer tab)
and answers live searches in the answers' text, which the items do not carry.

**An agent's conversations** (a plugin with a `[harness.*]`): the source `conversations`, declared by having a
harness, not shown in ⌘O. Items `{"key": <conversation id>, "label", "detail" (its first request), "time", "match"?,
"cwd", "harness"?}` (`harness`: which of its agents, when it has several; else its first). Once it has pushed (even
an empty list), Resume another session lists these with Claude Code's and Codex's conversations, and its
`--list-conversations` is not run. Without it, `--list-conversations` runs in the background when the dialog opens
and the list is older than 30 s; the dialog never waits for it ([harness-protocol.md](harness-protocol.md) §3).

### People (1.2)

The Calendar shows each meeting's organizer and attendees as an initial on a small square. A plugin can say who a
name is (a company directory: its photo, its chat handle), which a public Orbit cannot know:

```toml
[people]                   # needs a [cli]
args = ["people"]          # the [cli] command is run with these arguments
```

**The call.** Orbit runs the plugin's `[cli]` command with `args` (its environment and secrets, §3), the names as JSON
on stdin (never in its arguments), at most 50 names a call, 8 s for an answer:

```
stdin   {"names": ["Alice Chen", "Bob Li"]}
stdout  {"Alice Chen": {"avatar": "https://…/alice.jpg", "handle": "alice"}}
```

Leave out the names you do not know; `handle` is optional. Exit 0. Orbit keeps of each answer only:

| Field | |
|---|---|
| `avatar` | an `https:` URL (4096 characters at most; no spaces, quotes or `<>`), or a `data:image/…` URI (200 000 characters at most; base64 or not, no control characters); anything else is dropped. The page uses it as an image's `src` only |
| `handle` | a string, 64 characters at most, trimmed |

A name with neither left counts as not known by that plugin. Names it was not asked about are ignored.

**Several plugins.** Each is asked, in load order, about the names none before it knew: the first that knows a name
wins.

**Caching.** Orbit asks only about names it shows, once per name: an answer is kept for 24 h, a "not known" (no plugin
knew it) for 6 h, so a new hire shows up the same day. The cache is kept in memory and in
`~/.orbit/cache/people.json` (a restart does not ask again); it starts over when the set of `[people]` plugins
changes. Two requests for the same name at once make one call.

**Failures.** A plugin that exits non-zero, does not answer in 8 s, or prints anything but a JSON object never breaks
the page: the names it was asked about count as not known for 10 min (an answer from a later plugin is also kept only
10 min), the error goes to `~/.orbit/logs/plugins/<plugin>.people.log` and `people_error` in `/api/plugins`.
`orbit plugin check` asks it about one name and reports what it said.

**The route** (this machine only: 403 from another machine, since it runs the plugins' commands):

| | |
|---|---|
| `GET /api/people?names=Alice%20Chen,Bob%20Li` | comma-separated, URL-encoded; `names=` may also repeat (a name with a comma: POST) |
| `POST /api/people` `{"names": ["Alice Chen", …]}` | `names` not a list of strings → 400 |

Both answer `{"people": {"Alice Chen": {"avatar"?, "handle"?}}}`, known names only, keyed by the name as asked (its
spaces collapsed). Blank names, names over 200 characters and repeats are dropped; past 200 names, the rest are left
out. With no `[people]` plugin it answers `{"people": {}}` at once. A request waits for the plugins (a few seconds at
most for names not yet cached): ask in the background and draw initials meanwhile.

`examples/hello/` has a toy one that works offline: any name gets its initials on a colour derived from the name (a
`data:image/svg+xml` URI) and its first name, lower-cased, as a handle.

## 6. Versions

| API | Added |
|---|---|
| 1.0 | harnesses, services, secrets, origins, `[cli]`, `[agent] hint`, status items, panels, actions |
| 1.1 | `[[ui.sidebar]]`, `[[ui.tab]]` (+ `[[ui.tab.menu]]`), the page bridge, badges, `open-tab`, `notify` |
| 1.2 | `[people]` (who a name is: avatars and handles for the Calendar), `/api/people` |
| 1.3 | `shortcut` on `[[ui.sidebar]]` and `[[ui.panel]]`; a harness's `quota` in `--usage` (the status bar, [harness-protocol.md](harness-protocol.md) §3); `[[ui.palette]]` item sources (pushed, or refreshed by a command) and the live search through a service (`$ORBIT_SERVICE_PORT`, `/orbit/search`); `/api/palette`, `…/items/{source}`; the `conversations` source; `--list-conversations` in the background |
| 2.0 | **Changed**: no iframes. `path` pages, the page bridge (`plugin-bridge.js`, postMessage) and the serving of a plugin's `ui/` folder are gone (an entry with `path` is ignored with an error; a plugin needing 1.x still loads). **Added**: views Orbit draws: `view = "thread"` (pushed, fetched from `/orbit/thread`, replies to `/orbit/send`), `"list"` (an item source), `"doc"`; `[[ui.message.menu]]`, `schemes`, a service's `files` and the `svc:` proxy; items' `unread`, `group`, `open`; `…/threads/…`, `…/docs/…`, `plugin-thread`, `plugin-doc` |


`requires_api` is checked against `orbit.API_VERSION` (`orbit --version`). A minor version adds (old plugins keep
working); a major one changes something listed here, and CHANGELOG.md says what. (2.0 removed only the pages: a
plugin whose requirement fits 1.3 still loads, its pages ignored with an error.) Orbit's own version is separate.
