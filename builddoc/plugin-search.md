# Plugins in ⌘O and in Resume another session: item sources, no process per keystroke

Status: implemented (plugin API 1.3). Replaces a plugin author's `[[ui.palette]]` proposal, which ran the plugin's `[cli]` on every keystroke. The reference is now [plugins.md](plugins.md) §5 "Item
sources" and [harness-protocol.md](harness-protocol.md) §3; this file is the design record. What was built differs
from the text below in these points (§8).

## 1. What is wrong with a command per keystroke, and what Orbit itself does

A process per keystroke costs a cold start each time (Python or Node: 50–300 ms, more with a network login), and
the result is thrown away. Typing "deploy" with a 200 ms debounce still starts several, and an old one can answer
after a new one.

Orbit's own finder tabs never do that. They come in two kinds (`web/src/finder.js`):

| Kind | Built-in tabs | How |
|---|---|---|
| **items** | Sessions, Files, Other sessions | fetched **once** when the tab opens; every keystroke filters them **in the page** (fuzzy, by id) |
| **search** | History | the server searches its own data (in-process, an index) for the debounced query; one request in flight |

A plugin should get the same two kinds, with the same costs: **items it keeps up to date in Orbit**, and a **search
answered by a process that is already running**.

The same flaw is in **Resume another session** today: for each plugin harness, every time the dialog opens, Orbit
runs `command --list-conversations --limit 200` and waits for it (up to 20 s) before the dialog shows anything. For
Claude Code and Codex it reads their files in-process, but also only the 200 newest, filtered in the page.

## 2. Item sources

A plugin declares the sources it feeds:

```toml
[[ui.palette]]                 # a tab in ⌘O after the built-in ones
id = "slack"
title = "Slack"
open = "tab"                   # what choosing an item does: "tab" (its [[ui.tab]] kind + key, the default),
                               # "action" (runs [cli] with the item as JSON on stdin), "url" (the item's url)
search = true                  # (optional) its service also answers live searches (§4)
refresh = { args = ["palette", "slack"], every = "5m" }   # (optional) for a plugin without a service (§3)
```

An **item** is `{key, label, kind?, detail?, time?, match?, icon?, tag?, url?}`. `key` is unique in the source.
`match` is extra text to match (a channel, people, a ticket id), not shown. `time` sorts the list when nothing is
typed (newest first).

### 2.1 Pushed by the plugin's service (the normal case)

The service is already a long-lived process, and already calls Orbit's HTTP API with its token:

| Call | |
|---|---|
| `PUT /api/plugins/{name}/items/{source}` `{"items": [...]}` | the whole list (a resync) |
| `POST /api/plugins/{name}/items/{source}` `{"upsert": [...], "remove": [keys]}` | changes only |
| `GET /api/palette` | every source's items, for the page (as `/api/plugin-ui` today) |

Orbit keeps them in memory and in `<state>/plugin-items/<plugin>.<source>.json`, so they are there at once after a
restart. A source holds at most 10 000 items (the newest by `time` are kept); an item is at most 2 KB.

The Slack plugin already polls or listens to Slack for its inbox: it pushes the threads, channels and DMs it sees.
⌘O then costs nothing per keystroke, works offline, and the results are ranked together with the user's own sessions
and files by the same matcher (fuzzy, id, recent first).

### 2.2 Refreshed by a command, for a plugin without a service

`refresh = {args, every}`: Orbit runs `[cli] ARGS` in the background every `every` (at least 1 min), and when ⌘O
opens and the list is older than that. Its stdout is the whole list (JSON lines of items). It never runs per
keystroke. ⌘O shows the list it has at once, and redraws if a refresh comes in while it is open.

## 3. Live search: through the running service, not a new process

Some things cannot be indexed: a full-text search across all of Slack, or a Jira query. For those, `search = true`:

- Orbit gives each plugin service a port of its own when it starts (`$ORBIT_SERVICE_PORT`), and records it. It never
  asks the plugin to pick one.
- The search goes to that service:
  `GET http://127.0.0.1:$ORBIT_SERVICE_PORT/orbit/search?source=slack&q=…&limit=30`, with
  `X-Orbit-Plugin-Token`, so the service knows the request is from Orbit. The answer is `{"items": [...]}`.
- Orbit sends nothing for fewer than 2 characters. It waits 150 ms after the last key. It keeps one request in flight
  per source: a newer query aborts the older one, so a late answer is never shown. A request times out after 1.5 s.
- The tab shows its pushed items at once, filtered in the page. The live results come in below them ("Search in
  Slack"), without the items already shown.

The plugin keeps its process warm: logged in, connection pool, cache. Orbit pays one local HTTP request per pause in
typing.

(Considered instead: a `[query]` process Orbit keeps running and talks to over stdio JSON lines, like the Harness
Protocol. It is one more kind of process to manage. A service is already that, and plugin authors already write it.)

## 4. Resume another session on the same footing

- **Orbit's own harnesses.** The conversation list is an index the server keeps: built once, then updated from the
  files' modification times. It is not re-read on every open. The tab becomes a **search** tab: the server matches
  the whole store, not only the 200 newest. The dialog opens at once, with the newest first.
- **A plugin harness.** It feeds the same index:
  - with a service: it pushes its conversations to the source `conversations` (an item:
    `{key: <conversation>, label, detail, time, match, cwd}`);
  - without one: `--list-conversations` becomes a background refresh (when the dialog opens and the list is older than
    30 s; never waited on: the dialog shows what it has).
- `--list-conversations --query Q` (searching a store too large to list) is answered by its service through
  `/orbit/search?source=conversations`, as in §3.

## 5. Later, on the same pieces

- `[people]` (1.2) runs a command per batch of unknown names and caches the answers for a day. A plugin with a
  service could answer it through `/orbit/people`, or push the people it knows as a source.
- The phone's search, and another machine's ⌘O (federation), read the same `GET /api/palette`.

## 6. What changes for a Slack plugin

Instead of `[[ui.palette]] args = ["search"]`:

```toml
[[ui.palette]]
id = "slack"
title = "Slack"
search = true      # full-text search through the bridge service, which is running anyway
```

The bridge service:
- `PUT`s its recent threads, channels and DMs to `items/slack` at start;
- `POST`s changes as they arrive;
- answers `GET /orbit/search`.

Choosing a thread opens its `[[ui.tab]]` (kind `thread`, key = the thread's id), as the plugin's sidebar already does.

## 7. Tests

- Items: push, upsert and remove; kept across a restart; the size caps.
- ⌘O: the plugin tab, filtering in the page; choosing an item opens its tab; no process is started per keystroke
  (the test counts spawns).
- Search: debounce; an older answer dropped; the 1.5 s timeout; the token checked; a service that is down leaves the
  tab working on its items.
- Resume another session: it opens without waiting for a plugin harness; the background refresh; the full store
  searched.

## 8. As built: decisions and deviations

- **Validation.** A bad `[[ui.palette]]` (no id or title, an `open` it cannot do, an id given twice, the id
  `conversations`) is ignored with an error and the plugin loads, as a bad shortcut is. `open = "tab"` needs a
  `[[ui.tab]]`: the item's `kind`, else the entry's `kind`, else the plugin's only kind. `refresh.every` under a minute
  runs every minute (with an error). Not in §2: `kind`, `args` (for `open = "action"`) and `icon` on the entry.
- **Which service answers.** `search = true` names the plugin's only service; with several it must name one
  (`search = "bridge"`), else search is off with an error. Each service gets `$ORBIT_SERVICE_PORT` (a free port
  picked by Orbit, kept across its restarts while free; `port` in `/api/plugins` services).
- **An action** gets `{"source", "item"}` on stdin (like the other actions' context objects), not the bare item.
- **Items**: `data` (any JSON, the plugin's own) is kept; unknown fields are dropped; `time` in milliseconds is taken
  too. A request with a bad or oversized item is refused as a whole (400 naming it). Over 10 000, the oldest by `time`
  go (`"dropped"` in the answer). A resync that changes nothing is not told to the pages (a service may `PUT` its
  whole list every few seconds without making an open ⌘O fetch it again).
- **Writing** needs the plugin's own token (403 otherwise, from this machine too: the other plugin routes accept no
  token from this machine), and only to declared sources (400). Saved 1 s after the last change.
- **`GET /api/palette?plugin=&source=`** gives one source: ⌘O fetches the tab it opens, not every source; the list
  of sources (without items) comes with `/api/plugin-ui` (`palette`), so the tabs are there when ⌘O opens.
- **Refresh failures** are `palette_errors` (`{source: error}`) in `/api/plugins`, `error` on the source, and
  `<plugin>.palette.log`. A bad line is left out and counted in `error`; the list is replaced by the good ones.
- **Live search, page side**: a newer query aborts the older request and every answer is also checked against the
  query it was for, so a late answer that arrives anyway is dropped. The "Search in …" line says "searching…",
  "nothing more" (all found were shown already), "nothing found" or "not available: <why>" (no toast).
- **Conversations** (§4): `conversations` is declared by having a `[harness.*]`, never a ⌘O tab. Its items map to
  rows as `label` → title, `detail` → first request, `match` searched too; `harness` picks one of several agents.
  Once a plugin has pushed (even an empty list), its `--list-conversations` is not run.
- **The index** (`orbit/desk/external_index.py`): Claude Code and Codex stores are read file by file and again only
  when a file's mtime or size changed; an open spends at most 0.25 s reading changed files (newest first), the rest
  is read in the background and the dialog redraws (`external-sessions` event). The server reads the stores once,
  5 s after it starts. `--list-conversations` runs with `--limit 1000`. A typed query is matched by the server with a
  port of the page's matcher (fuzzy + id), best first; rows carry `score`, and the response `pending` (lists being
  read). Federation passes `q` on (`?local=1` unchanged) and matches the other machines' rows again, for servers
  from before 1.3.
- **Not built**: `--list-conversations --query Q` answered through `/orbit/search?source=conversations` (§4, last
  point): a typed query in Resume another session matches what the index has, a service is not asked. §5 is later.
