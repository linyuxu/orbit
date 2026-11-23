# Native plugin views: a plugin's thread and inbox drawn by Orbit, not in an iframe

Status: implemented, as plugin API **2.0** (§9: what changed on the way, including no iframes at all). From a plugin
author's report: a Slack thread tab in an iframe looked and behaved unlike a conversation tab.

## 1. Why

A `[[ui.tab]]` or `[[ui.sidebar]]` with `path` is the plugin's page in an iframe. However carefully it loads Orbit's
stylesheets, it is a second document:
- text renders differently (another font context: thinner, lighter);
- Markdown is not the conversation's (`renderMd`: markdown-it, highlight.js, KaTeX, file links, images);
- the input is a textarea, not Orbit's editor (live preview, pasted images);
- there is no context menu, no image viewer, no ⌘F, no drag and drop, and no phone.

The fix: the plugin gives **data**, and Orbit draws it with the conversation's own code in the main page. An iframe
page stays for what is not a list or a thread (a dashboard, a form).

## 2. Declaring a native view

```toml
[[ui.tab]]
kind = "thread"
title = "Slack thread"
icon = "chat"
view = "thread"            # Orbit draws it (§3); `path` (an iframe page) and `view` are exclusive

[[ui.sidebar]]
id = "inbox"
title = "Slack"
view = "list"              # Orbit draws it (§4) from an item source
source = "inbox"           # one of the plugin's [[ui.palette]] sources (pushed items, 1.3)
shortcut = "Mod-Shift-S"
```

`[[ui.tab.menu]]` items keep working: in the ⌘E menu of a native tab too.

## 3. A thread (`view = "thread"`)

### 3.1 Data

```json
{"title": "#infra · deploy failed", "sub": "Slack · 14 replies", "url": "https://…",
 "messages": [{"id": "1732…", "author": {"name": "Ana Li", "me": false, "avatar": "https://…"},
               "time": 1732300000, "markdown": "It failed at **step 3**: …",
               "files": [{"name": "log.png", "url": "svc:/files/F123", "image": true}],
               "reactions": [{"emoji": "👍", "count": 2, "me": true}], "edited": false,
               "replies": {"count": 3, "kind": "thread", "key": "C1/1732…"}}],
 "composer": {"placeholder": "Reply in #infra", "enabled": true},
 "more": true}
```

- `markdown` is drawn by `renderMd`, as an answer is.
- An `author` without an avatar gets one from `[people]` (1.2), then initials.
- `files[].url` is `https:` or `svc:/path`. A `svc:` URL is fetched through Orbit:
  `/api/plugins/{name}/svc/path` → the plugin's service (`$ORBIT_SERVICE_PORT`, its token). Private files (Slack's)
  load with the service's login, and the page never sees the token.
- `replies` opens another of the plugin's tabs.
- `more: true` means there are older messages (§3.3).

### 3.2 Getting it to Orbit

Two ways, as with item sources (1.3); never a process per keystroke or per scroll.

**The service pushes** (the normal case):

| Call | Does |
|---|---|
| `PUT /api/plugins/{name}/threads/{kind}/{key}` | the whole thread (title, messages, composer) |
| `POST …` `{"append": [...], "update": [...], "remove": [ids], "title"?, "sub"?, "composer"?}` | changes |

Orbit keeps the thread in memory and on disk (`<state>/plugin-threads/`). Caps: the newest 2 000 messages of a
thread, and 300 threads per plugin (least recently opened dropped). An open tab redraws live (an `/api/events`
message): a new message comes in like a new card. It is stuck to the end if the user was at the end, else
"Latest ↓".

**Orbit asks** when a tab opens and has no copy (or one older than 60 s):
`GET http://127.0.0.1:$ORBIT_SERVICE_PORT/orbit/thread?kind=&key=` → the thread. Without a service: `[cli] thread
KIND KEY`, run once in the background. The tab shows what it has at once ("Loading…" only when it has nothing).

### 3.3 Older messages

Scrolled to the top with `more: true`, Orbit asks `GET /orbit/thread?kind=&key=&before=<oldest id>` (service only).
The answer's messages go before the others; the view keeps its place.

### 3.4 Sending

The input at the foot is **Orbit's input editor**: the same component and keys as a conversation's, with live
preview and pasted or dropped images (uploaded to this machine as in a conversation).

On send:
1. A message appears at once, marked "Sending…" (`client_id` set by Orbit).
2. Orbit calls `POST http://127.0.0.1:$ORBIT_SERVICE_PORT/orbit/send`
   `{"kind", "key", "markdown", "files": [{"name", "path"}], "client_id"}`. Without a service: `[cli] send`, with
   that on stdin.
3. When the plugin pushes the real message with the same `client_id`, it replaces the pending one.
4. An error marks it "Not sent", with Retry and Edit.

`composer.enabled: false` hides the input (a read-only view).

### 3.5 Drawn like a conversation

- **Others' messages** are drawn as an answer: an author line (avatar, name, time; the name in the answer header's
  style) over the Markdown body in the answer's typography.
- **The user's own messages** (`me`) are drawn as a request, in a bubble.
- Consecutive messages by one author within 5 minutes share one author line.
- The same `.md` styles, code blocks with Copy, KaTeX, file links, images through the image viewer, `⌘F` find,
  scrolling keys, "Latest ↓" and the selection quote menu all work.

**Context menu of a message:**
- Copy, Copy as Markdown, Open at the source (`url`);
- **Quote in a session…**: into the current session's input, as a selection quote is;
- **Send to a session…**: the message and its thread as a new request;
- the plugin's own `[[ui.message.menu]]` items (run `[cli]` with `{"kind", "key", "message"}` on stdin).

Reactions are shown, not editable, in 1.4.

## 4. An inbox (`[[ui.sidebar]] view = "list"`)

The left tab lists the source's items (1.3), drawn like the Sessions list:
- rows of label, detail, time, tag, an unread dot (`unread: true` on an item) and the plugin's badge;
- grouped by an item's `group` (optional);
- filtered by the left panel's filter box;
- a click opens the item (its `open`, usually a native thread tab).

Its rows have the Sessions list's keys and context menu (Open, Open in the side tab, the plugin's
`[[ui.tab.menu]]`).

## 5. Phone

Done (§9.11). The phone app gets the same views from the same API: Misc → the plugin's inbox → a thread. It has the
phone's input editor and image viewer, and replies from there.

## 6. Compatibility

- `path` pages keep working.
- `requires_api = "1.4"` for `view`.
- A plugin can move one tab at a time.
- `examples/hello` gets a native thread (its Answers as a thread per session) and a native inbox.

## 7. Tests

- Store: push, append, update, remove; persistence across restarts; the caps.
- Auth: another plugin's token is refused.
- Pull on open: from the service, then from the command, without blocking the page.
- Paging older messages.
- `svc:` proxy: its token, its timeout, and nothing outside the service reachable.
- Send: the pending message, then replaced; failure, then Retry.
- Browser (Chromium and WebKit):
  - The native thread uses the same computed font, size and colour as a conversation answer: measure it.
  - Markdown, code and KaTeX render.
  - A pasted image uploads.
  - The image viewer and ⌘F work.
  - The context menu items work.
  - A live append keeps the view at the end.
  - The inbox list filters and opens.

## 8. After review: decisions (these override §1–§7 where they differ)

1. **The `svc:` proxy serves only inert content in Orbit's origin.** The Content-Type is never passed through:
   - png, jpeg, gif and webp are served as images, with the sniffed type checked against the bytes;
   - SVG is allowed only as an `<img>` source (§9.12: always SVG, sandboxed, instead);
   - anything else is `application/octet-stream` with `Content-Disposition: attachment`.

   Always sent: `X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox` (and the same `nosniff` is
   added to `/api/raw`).
2. **The proxy is not a gateway.**
   - GET and HEAD only, only under the prefix the plugin declares (`files = "/files/"` on the view's service), never
     `/orbit/`.
   - Built with `http.client` to 127.0.0.1:port. The path must start with `/`; CR/LF and encoded `.`/`/` are
     refused; redirects are not followed.
   - No request headers are forwarded but `Range`, and Orbit adds `X-Orbit-Proxy: 1` (the service can refuse what it
     never serves to a proxy).
   - Response headers back: only Content-Length, ETag and Cache-Control.
   - Limits: 20 MB, 10 s, streamed, 4 at once per plugin.
   - Phone and remote clients (token-checked as all the API is) may use it for images.
3. **The thread view is its own module with its own editor**, not the conversation's globals.
   - The editor is a second `CM.createEditor`.
   - Drafts are keyed by the tab's path. Session completions (@, @@, /) are off; ⌘⏎ and ⏎ send by the user's send
     setting.
   - Uploads always go to this machine. A pasted image becomes an entry of `files[]` shown as a chip above the input,
     not a link in the text.
   - A shared "message list view" is factored out of the conversation (scroll box, at-end state, anchors keyed by
     view and message id as strings, Latest ↓, find target, quote source, delegated clicks for Copy, the image viewer
     and links), and both the conversation and the thread use it. It renders messages with `renderMd`, styles from
     conversation.css; the user's own messages are a Markdown bubble (the request bubble's box, Markdown inside).
   - ⌘F, the selection quote menu, focus, and the scroll keys know a native plugin tab as a target.
4. **API.**
   - Page side:
     - `GET /api/plugins/{name}/threads/{kind}/{key}` (from the store; asks the service in the background when
       missing or stale);
     - `GET …?before=<id>` (older, through the service);
     - `POST /api/plugins/{name}/threads/{kind}/{key}/send` `{markdown, files, client_id}` (this machine only, like
       plugin commands).
   - Plugin side (its token): `PUT …/threads/{kind}/{key}` and `PATCH …` `{append, update, remove, title, sub,
     composer, unread}`.
   - `[[ui.tab]]` `view = "thread"` names its `service` when the plugin has several. `path` or `view` is required.
   - `item_of` keeps `unread`, `group` and `open`.
   - The sidebar badge of a native list = its unread items unless the plugin sets one.
5. **Pending messages live in the server's store.**
   - `/orbit/send` returns `{id}`; a push matching the id or the `client_id` replaces the pending one.
   - A full PUT keeps pending ones.
   - Retry reuses the same `client_id`, so the service can de-duplicate.
   - "Sending…" survives a reload and shows in every window.
6. **Plugin Markdown is rendered in plugin mode:**
   - no local paths (no `/api/raw`, no file links, no `~/`);
   - images only `https:` and `svc:`;
   - links only `https:`, `http:`, `mailto:` and the plugin's declared schemes (`schemes = ["slack"]`).

   The server checks `url`, `avatar` and `files[].url` as `item_of` does.
7. **Caps and live updates.**
   - A body is capped (1 MB), a message 32 KB, a thread 2 000 messages.
   - Events carry a `rev` and the change only; a gap or a `hello` refetches.
   - Messages paged in from before the cap stay in the view only.
   - One `[cli] thread` runs per key at a time.
8. **Unread, title, notifications, side tab, reload.**
   - The thread's `unread` (or a pushed message while the tab is not in view) puts the tab's unread dot; opening it
     clears it, and that plugin's notices for that tab.
   - `title` goes into the tab's saved title.
   - A native thread can be a side tab (a new embed kind).
   - Reload refetches.
   - When plugins reload, iframes for a kind that is now native are dropped.
9. **Menus.**
   - `[[ui.message.menu]]` (parsed like `[[ui.tab.menu]]`) runs through `runPluginAction`, this machine only.
   - "Quote in a session…" uses `quoteTo` with the thread's title as the reference.
   - "Send to a session…" is a new session picker plus `/send` with `origin` (`{plugin, label: thread title, url}`):
     the message and the thread's last 30 messages, capped at 20 KB.
10. **Phone** (§5): built after 2.0 (§9.11). Reading a thread and proxied images work remotely, and so does sending
    (decided then: a client with the server's token is the user).

## 9. As built (where it differs from §1–§8)

1. **No iframes at all, and so API 2.0** (decided after §8, with the user). A plugin's `path` pages on `[[ui.tab]]`,
   `[[ui.sidebar]]` and `[[ui.panel]]`, the page bridge (`web/src/plugin-bridge.js`, its postMessage messages, key
   forwarding into frames, theme messages), the serving of a plugin's `ui/` folder and the plugin side tab are gone.
   An entry with `path` is ignored with an error ("iframe pages are gone in plugin API 2.0: use view = …"); the plugin
   loads. A plugin whose `requires_api` fits 1.3 (`">=1,<2"`) still loads. The only plugin file still served is a
   harness's declared `icon` (an image type, `nosniff`, `sandbox`). There is no 1.4: `view` needs 2.0.
2. **A third view, `view = "doc"`**: Markdown a plugin pushes (`PUT …/docs/{id}` `{title, markdown, rev}`; a `rev`
   older than the one kept is refused, 409) or its service answers (`/orbit/doc?id=`, `[cli] doc ID`), live. On a
   `[[ui.tab]]` the tab's key is the doc's id; on a `[[ui.sidebar]]` or `[[ui.panel]]` its `doc` (else its id). A
   `[[ui.panel]]` is a doc, a list or a thread (`kind` + `key`); a status item's `panel` opens it. What the bridge did
   is over HTTP: `open-tab`, `badge`, `notify`, `status` as before, a thread's `unread` and `title` in its data;
   toast and quote from a page are dropped (there is no page).
3. **Routes.** The plugin's changes are `PATCH` only (§8.4), not `POST` (§3.2): under a thread's path `POST` is the
   page's `…/send`, and `…/discard {client_id}` (added: Edit of a message not sent takes the pending one away). The
   key is one URL-encoded path segment (a `/` as `%2F`).
4. **Pending messages** go `sending` → `sent` (the service took it; shown as an ordinary own message until the push
   replaces it) → replaced; or `failed` with the error. One that was `sending` when the server stopped comes back as
   "Not sent". Retry sends the stored message again (its markdown and files), with the same `client_id`, whatever the
   page sends. A reply's `files[].path` may be any file of this machine (a drop in the app gives real paths), not only
   an upload; from another machine (§9.11), only an upload.
5. **Events** carry `rev` and the change: `append`, `update`, `remove`, `replace` (the real message in a pending one's
   place), the thread's fields, `error`; `whole` for a PUT or a fetch (the page fetches it). `plugin-doc` carries only
   `rev` (the page fetches). Older messages are never kept by the server (all of them stay in the page).
6. **The proxy**: the service is the one whose `files` prefix the path starts with (the URL names none); Orbit sends
   the plugin's token besides `X-Orbit-Proxy: 1`; a 206 also passes `Content-Range` (Range is forwarded: a partial
   answer needs it); SVG is SVG, sandboxed, whatever the browser asks for (§9.12; it was only for
   `Sec-Fetch-Dest: image`); a HEAD answers the declared image type (no bytes to check) else octet-stream; the
   answer is streamed when the service gives a length, else read up to the cap first. A request over 1 MB to the push
   routes is 413.
7. **Links**: a thread's and a message's `url` may also use the plugin's `schemes` (Open at the source in Slack), not
   only https and http as items'.
8. **Store**: `<state>/plugin-threads/<plugin>/` holds an index and a file per thread or doc; 64 views stay in memory,
   the others are read when asked for. Docs have their own cap of 300 per plugin.
9. **Page**: the shared view is `web/src/msglist.js` (`MsgList`, `mdClick`, `scrollBox`); the conversation uses it
   with its old anchor store, now keyed by string ids, and measures the same (computed styles, boxes and screenshots
   before and after, Chromium and WebKit). The native views are `web/src/pviews.js`. "Quote in a session…" and "Send to
   a session…" are submenus of the open sessions (the current first; type to filter), not a dialog. An author's avatar
   is the message's, else the `[people]` plugins' (this machine only), else initials on a colour. A selection under
   the pointer gets the browser's menu (Copy), as an answer does. Drafts live in `localStorage` (`orbit.threadDrafts`,
   by tab path), shared by the main page and side tabs. A side tab of a view is the embed kind `pview` with the view's
   path; a panel's doc or list has the path `plugin:<p>/~doc/<id>` or `…/~list/<source>`. In the left panel a list's
   detail shows in the strip under the list, as a session's preview does. Another machine's page showed the thread
   without the editor; since §9.11 it has the editor too.
10. **Manifest**: a view whose service is ambiguous (several services, none named) is a warning, and `[cli]` is used.
11. **The phone** (§5, after 2.0; `web/m/m-pviews.js`), no new API:
    - Misc lists each plugin's left tabs and panels it can draw (lists, docs, threads with a key), under the plugin's
      name; a list's row counts its unread items.
    - A list is a page of the Sessions list's rows (label, tag, an unread dot, time; the detail under it), grouped by
      `group`; pull to refresh when its source has a `refresh` command (`/api/palette?…&fresh=1` runs it now, one run
      at a time). An item opens its `open` (a thread or doc page; a URL in Safari), else its source's tab kind.
    - A thread is a page over the tab bar, drawn with the conversation's classes (`.md`, `.ans .head`, `.req .bubble`,
      the foot): the same computed type as an answer (measured, WebKit and Chromium). Its scroll rule is the
      conversation's (m-conv.js: a scroll the page made is told apart by where it lands), not `MsgList` (its
      wheel-and-touch timing loses iOS momentum). Older messages page in at the top, the message in view kept.
    - Replies: a `CM.createEditor` per thread page (a draft per thread in `orbit.m.threadDrafts`), photos and pasted
      images shrunk as the conversation's, uploaded to the server's machine and sent as `files[]` chips; Sending… /
      Not sent with Retry and Edit as on the desktop. No Steer, Queue or Stop.
    - The server takes `…/send` and `…/discard` from any client with its token (`_guard` checks every remote
      request): that is the user. Another machine's `files[].path` must be one of the server's uploads (it has no
      other path there; 400 otherwise). `[[ui.message.menu]]` and every `[cli]` action stay this machine's (403).
      Another machine's desktop page shows the editor too.
    - Unread: an item opened, and every item that opens a thread while that thread was on screen, is read on this
      phone (until the plugin changes its time). Avatars are the message's or initials (`/api/people` runs the
      plugins' commands: this machine only).
    - The phone has no notifications (its service worker's push handler is a stub), so a plugin's `notify` /
      `open-tab` do not open a thread there.
    - One history entry is kept (replaceState only); back is the page stack's (the chevron, the edge swipe, which
      never reveals the conversation layer).
12. **SVG through the proxy** (after 2.0): `Sec-Fetch-Dest` is not relied on (a browser that does not send it showed
    SVG broken). An SVG by its bytes (`<svg` first, after an optional BOM, XML prolog, comments, whitespace and a
    DOCTYPE without an internal subset), or by the service's type when the bytes are not another document, is always
    `image/svg+xml` with `Content-Security-Policy: sandbox; default-src 'none'; style-src 'unsafe-inline'` and
    `nosniff`: opened as a page it runs no script and has an opaque origin (as `/api/raw`'s SVG). HTML, XHTML, XML
    and anything else stay `application/octet-stream` attachments. The proxy reads the first 1 KB to tell.
