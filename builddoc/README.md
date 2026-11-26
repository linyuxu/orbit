# Orbit (道枢) rebuild spec

**The only input for an agent that re-implements Orbit from scratch.** Orbit is a local desktop IDE whose "documents"
are coding-agent sessions: you talk to many sessions in tabs, read their answers as rendered cards, and edit files and
notes next to them. Keyboard first, calm to look at, no data loss.

When this spec and the old code disagree, this spec wins. Every rule here has a reason; most of them cost a bug once.

## Reading order

| File | Content | Status |
|---|---|---|
| `README.md` (this) | Scope, build order, architecture, glossary | written |
| `ui.md` | Visual system: tokens, type, sizes, components, principles, Settings (⌘,) | written |
| `interaction.md` | Interaction contract: focus, Esc, toggles, lists, menus, dialogs, the full shortcut table | written |
| `driver.md` | Agent driver contract: one interface for Claude Code, Codex, agy (reference: one long-lived `claude -p --input-format stream-json --output-format stream-json` process per session) | to write |
| `opencode.md` | OpenCode as a built-in harness: why `opencode serve`, the HTTP and event mapping, permissions through its TUI (⌘T), hand-off, history and Resume from its store | written, built |
| `editor.md` | The one live-preview editor (input, Markdown files, notes): what renders, keys, input completions | written, built |
| `features/files.md` | The editors (Markdown live preview), notes links on rename, dropped and pasted files | written (editor part) |
| `features/{sessions,cards,notes,search,calendar,schedule}.md` | One file per module: purpose, user stories, data, API, keys, edge cases | to write (files: `features/files.md`) |
| `data.md` | Every persisted file and its schema, data root, recovery | to write |
| `agent-api.md` | `orbit-agent` CLI, HTTP `/api/*`, SSE, Orbit-for-agents (`[orbit]` line, `ctx`, `open`, `show`), display rule for agents | to write |
| `platform.md` | WebKit vs Chromium, native-menu keys, zoom math, the traps table | to write |
| `acceptance.md` | Per-feature checklists (action, expectation, engine), test isolation, perf budgets | to write |

`ui.md` and `interaction.md` are cross-cutting contracts: every feature file follows them and only states its
exceptions.

## 1. Scope

| In | Out |
|---|---|
| Many agent sessions in tabs and groups; send, read rendered answers, follow progress | Harnesses with no machine-readable stream (those go in as plugins) |
| Files (git-aware tree, CodeMirror editor), ± Review of uncommitted changes (a side tab), an Obsidian-compatible notes vault | Chat and team tools (they go in as plugins) |
| Search: find in tab, history across sessions and notes | Multi-user access, and anything public: other machines are the same user's, on a private network (§3) |
| Agent-facing CLI and Orbit-for-agents context | Plugin system, graph view, mobile, an in-app browser pane (links open in the system browser; `orbit-agent show` pages and URLs open as a side tab) |
| Schedule (agents that wake up), Calendar (read-only, optional) | Email, calendar write / sync |

Decided (2026-11-17): **no right panel**: no in-app browser pane; ± Review (⌘⇧I) is a side tab kind. **Harnesses are Claude Code, Codex and agy**, each behind one harness-neutral driver contract (`driver.md`); a new harness is a new driver, never a change to the Desk or the page. (2026-11-23: OpenCode joins them, `opencode.md`.)

Decisions still open (待裁):

| # | Question | PROPOSED |
|---|---|---|
| D3 | Terminal mirror (⌘T) | out: sessions run headless through their driver, there is no TUI to mirror. ⌘J shell stays |
| D4 | Calendar | v2, read-only (macOS EventKit), behind a setting |

## 2. Build order

Each step ends with its `acceptance.md` checklist green, in WebKit (the app's engine), before the next starts.

1. **Skeleton**: server (HTTP + SSE), the Claude Code driver (Codex and agy follow once the contract holds), one session tab: send → card (states, tool steps, answer). `ui.md`
   tokens and the global key handler (`interaction.md` §2) exist from this step on, never retrofitted.
2. **Sessions**: tabs, groups, new / close / rename / reopen, names that survive close, status bar, left panel Sessions.
3. **Files**: Files tree, file tabs (preview vs pinned), code + Markdown editors, save with conflict banner, side tab,
   ± Review side tab (diff, line comments to the agent, revert a hunk).
4. **Notes**: vault, `[[links]]`, backlinks, week note, `orbit-agent note`.
5. **Search**: ⌘F find, ⌘⇧F history.
6. **Agent API**: `orbit-agent` CLI, `[orbit]` line, `ctx`, `open`, `show`.
7. **Schedule**, then **Calendar** (D4).
8. **App shell**: Tauri packaging, native menu routing. (Run the page in WebKit from step 1; only packaging waits.)

## 3. Architecture

| Part | Tech | Role |
|---|---|---|
| Server (orbit server) | Python 3.12, stdlib HTTP server on 127.0.0.1:8770, Server-Sent Events | The backend: drivers, file / notes / search APIs, shell pty, state files; reaches other servers (federation) |
| Page | `web/index.html` + `web/styles/*.css` + `web/src/*.js` (vanilla JS, no build; plain scripts in one scope), CodeMirror 6 bundles (esbuild, vendored), markdown-it + KaTeX + highlight.js, xterm.js | All UI |
| CLI | `cli/orbit` (people: `serve` = the server, `tools install`), `cli/orbit-agent` (agents); launchers into `orbit/` | The backend without the app; Orbit's tools for every harness in any terminal |
| App shell | Tauri 2 (WKWebView); a face for any server (`$ORBIT_SERVER`, `<state>/app.json`, Connect to Server…) | Native window and app menu; routes keys the page never sees (Esc, ⌘Z, ⌘\`) |
| Sessions | Claude Code / Codex / agy processes owned by the server, one driver per harness (`driver.md`) | The agents |

- The server listens on 127.0.0.1 and rejects cross-origin POSTs (check `Host` and `Origin`). With `orbit serve
  --remote` (or `--bind ADDR`) it also serves the user's other machines on a private network (Tailscale, a LAN): their
  requests must carry the token (`<state dir>/token`, 0600: `Authorization: Bearer`, `?token=`, or the HttpOnly
  SameSite=Strict `orbit_token` cookie a browser gets by opening the URL from `orbit url`), and their writes from a page
  of another origin are refused. Loopback requests need no token.
- Reaching another machine: `orbit remote add NAME URL` (or + → Add a machine…) stores `{url, token}` in
  `<state dir>/remotes.json` (0600); the server forwards `/api/remote/NAME/<path>` there with the token (SSE
  streamed), so `orbit-agent` addresses its sessions as `NAME/session` (`send`, `steer`, `read`, `wait`, `history`,
  `new --on NAME`). The page never sees a remote's token: opening its page goes through `POST /api/remotes/NAME/open`.
- The server and its clients are decoupled: the app, a browser, a phone or `orbit-agent` reach one server by URL. The
  app starts this Mac's server when nothing listens, and otherwise only connects (a server on a Linux box runs as
  `orbit serve` in tmux). A server shows the servers it reaches as its own (`orbit/machines/federation.py`): their open sessions
  join its list as `NAME~ID` (`"remote": NAME`) in this server's own groups (where the user put them: group and star
  kept here, `<state>/remote-placement.json`; a new one goes where it is made, with `machine` naming where it runs; the
  page tags each with its machine and can filter the Sessions panel by machine), their events are relayed with those ids, and
  `/api/sessions/NAME~ID/...` is done over there. One hop (`/api/sessions?local=1` between servers), so no loops.
  An unreachable machine's sessions show `offline` until it answers again.
- Another machine's files are `/@NAME/<path>` here (displayed `NAME:~/…`); a remote session's `cwd` is written so.
  A file route (`FILE_ROUTES`: tree, file, raw, review, resolve, stats, create / rename / trash, attach…) whose query
  or body names such a path is done by that server: the prefix is stripped going out and added to path-like keys
  coming back. ⌘J for its session is its shell over there (`NAME~SHELLID`, stream proxied); a request's text has
  `/@NAME/` turned back into that machine's paths. Such a file opens only in Orbit's own editor (never an outside editor or Finder).
- Settings → Machines: "This server" switches listening beyond 127.0.0.1 at run time (`Sharing`; Tailscale's address,
  else the LAN's; remembered as `serve_remote`, applied at start; `/api/sharing`, only from the server's own machine),
  and shows the URL with its token; "Other machines" lists, adds and removes remotes.
- HTTPS (`orbit/machines/https.py`, `orbit https on|off|status`, `POST /api/https`): `tailscale serve --bg
  --https=8443 http://127.0.0.1:PORT` gives the server `https://<machine>.<tailnet>.ts.net:8443` with Tailscale's
  certificate; requests arrive from 127.0.0.1 with that Host and `X-Forwarded-Proto: https` (so they count as another
  machine's: the token). The server itself stays on 127.0.0.1 (this machine's window needs no Tailscale). While on
  (`<state>/https.json` holds the URL, read by the guard without running tailscale), a request from the network that
  did not come through it gets 308 to the HTTPS address (a POST: 421). The tailscale command: `ORBIT_TAILSCALE`, else
  `tailscale = "…"` in config.toml, else `tailscale` / the macOS app's.
- The page is read from disk on every request: ⌘⇧R reloads the UI and keeps unsent input.
- UI state lives in `~/.orbit/*.json`, user data under the data root (`--data-root` / `$ORBIT_DATA_ROOT`). Conversations
  are never copied into Orbit's state: the driver's own store is the source of truth.
- Every path (state dir, data root, vault) is overridable, so tests never touch real data.

## 4. Glossary

One term per concept; use these names in code, UI text and docs.

| Term | Meaning |
|---|---|
| session | One agent conversation with its own process, folder and model; one session tab |
| request | One message the user (or another agent, or a schedule) sends to a session; numbered `#<id>` per session |
| card | The UI unit for one request: the request bubble, its state, tool steps, the answer, follow-ups. Also a turn the agent took on its own (kind `auto`, "On its own · after a background agent finished": no request started it; id after the last request's, `#5.501`) and a note card (kind `note`, a voice call's transcript) |
| answer | The agent's final text for a request; follow-up = text the agent writes after it, before the next request, in a turn of its own that runs no tool (one that does is an `auto` card) |
| driver | The adapter between the server and one harness (create, send, progress, history, stop, close) |
| Desk | The server's harness-independent layer above the drivers: request queue, cards, groups, history, agent mail |
| group | A named set of session tabs; ⌘1…⌘9 the groups in left-panel order (another group's session beside: a side tab) |
| main tab | What the main panel shows: a session, a file, a note, Calendar |
| side tab | Another tab shown beside the main tab, inside the main panel (an embedded page). Kinds: session, file / note, review (± Review of a session's folder), web page (`orbit-agent show`: an agent's page, or a website by its URL, also typed in ⌘O / ⌘⌃O), Calendar, Schedules |
| embed | A page loaded inside a side tab (`/?embed=<session>`, `/?embedtab=<spec>`); never writes the main page's state |
| left panel | Sessions · Files · Notes · Misc, toggled with ⌘\` |
| preview tab / pinned tab | A file tab replaced by the next preview vs kept until closed |
| vault | The notes folder (`<data root>/notes`), Obsidian-compatible |
| Fleet | The built-in pool of helper agents for quick asks (⌘K) and background jobs |
| data root | Folder for personal data (notes, schedules, journals), git-ignored |

## How to verify (each feature had a bug that only showed in use)

- Unit tests for every reader with real-shaped fixtures (harness transcripts and streams): delivery, follow-ups,
  live preview, link resolution, week note, file operations, history search.
- Drive the page headless (Playwright) for every UI change, **and** check in WebKit (the app's engine): CSS zoom,
  highlights and key events differ from Chromium.
- Never let tests touch the real `~/.orbit` or the vault: every path is overridable.
- Live session tests use a cheap model session, never the session you work in.
