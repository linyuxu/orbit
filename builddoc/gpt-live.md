# GPT Live voice in Orbit

Status: phase 1 built (§11). Talk to a session by voice, as in Codex's realtime voice: GPT Live holds the conversation and decides
when work is needed; Orbit sends that work to a session as a request and brings the result back to be spoken.

Sources: OpenAI's GPT Live guides (live-conversations, live-delegation, voice-webrtc, voice-server-controls,
live-prompting, voice-latency-cost, the live API reference) and Codex `rust-v0.167.2`
(`codex-rs/core/src/realtime_conversation.rs`, `realtime_context.rs`, `tui/src/app_server_session/realtime.rs`,
`prompts/templates/realtime/backend_prompt.md`).

## 1. Shape

| Part | Does |
|---|---|
| **The page** (desktop app first) | microphone and speaker over WebRTC, straight to OpenAI; the call button and its state; transcripts shown live |
| **The Orbit server** | holds the OpenAI key (Settings → Voice); creates the GPT Live session (`POST /v1/live/sessions` with the page's SDP offer, returns the answer); attaches the **sideband** websocket (`/v1/live/sessions/{id}/attach`) and does everything else there: transcripts, delegations, context, results |
| **The bound session** | an ordinary Orbit session: a delegation becomes a request in it (a card), its answer comes back to the call |

One call is bound to one Orbit session (the one in front when the call starts; switching is phase 2). The page's
data channel is restricted (`client.data_channel.allowed_client_events`: none it does not need), so the page can
neither delegate nor inject context: only the server can.

There is no ephemeral key for GPT Live: the key never leaves the server. The server needs a websocket client: a
small stdlib one (RFC 6455 client, TLS through `ssl`) in `orbit/services/live/ws.py`.

## 2. The session (created once per call; most of it cannot change later)

| Field | Value |
|---|---|
| `model` | `gpt-live-1.1` (the key has it; `gpt-live-1` as a setting) |
| `instructions` | §3 (≤ 16 384 tokens; only appendable later) |
| `input` | the startup context (§4: ≤ 128 messages, ≤ 8 192 tokens) |
| `audio.output.voice` | `marin` (a setting later) |
| `delegation` | `{type: "client", acknowledgement_delay_ms: 800}` (§5: we suppress the automatic filler) |
| `wake_policy` / `idle_timeout_s` | `{type: "speech", vad_threshold: 0.5}` / `30`: after 30 s with no transcript either way the call goes to standby ($0.02/min instead of $0.05/min) and wakes on speech; `session.paused` / `session.resumed` shown on the call button |
| `store` | `false` (no fork in phase 1) |
| `client.data_channel.allowed_client_events` | none beyond what WebRTC needs; `allowed_server_events`: the transcripts and session state (for the page's live captions) |

Cost: $0.05/min active (speaking, listening or silent alike), $0.02/min standby, per second; an hour open is $3.00
active or $1.20 standby, backend separate. Creating a WebRTC session bills 15 s up front.

### Settings → Voice

What the user may change (desk settings `live_*`, `orbit/services/live/config.py`), read as a call starts:

| Setting | Values | Goes to |
|---|---|---|
| `live_voice` | the 38 built-in voices (`marin`) | `audio.output.voice` |
| `live_model` | `gpt-live-1.1`, `gpt-live-1` | `model` |
| `live_idle_s` | 5–60, or 0 (never) (30) | `idle_timeout_s` (left out for 0: active until hang-up) |
| `live_wake` | 0.3, 0.5, 0.7 (0.5) | `wake_policy.vad_threshold` |
| `live_context` | on/off (on) | `input`: the session's facts, plus its last three requests when on |
| `live_language` | auto, zh, en, ja, ko, es, fr, de, pt | a line in the instructions (the API has no language field) |
| `live_detail` | brief, detailed | the result line in the instructions |
| `live_ack` | on/off (off) | off: the empty `thinking.append` at each delegation cancels the automatic acknowledgement |
| `live_prompt` | up to 4 000 characters | after Orbit's instructions; they win over style, never over delegation and grounding |

A voice's preview (`orbit/services/live/preview.py`): the WebSocket transport (`wss://…/v1/live/sessions`,
`session.start` first, PCM 24 kHz), a developer message to greet with one line naming the voice, silence as input.
The output streams continuously (silence too, and no "audio done" event), so speech is found by loudness (RMS of
0.1 s windows) and the recording ends after 1.2 s of quiet; measured: about 9 s to make, 5 s of speech.

## 3. Instructions

Short, in the style of Codex's shipped prompt (`backend_prompt.md`): it delegates every action, answers
self-contained questions itself, and avoids narration. Plus what Codex's prompt lacks, the noise rule:

```
You are the voice of Orbit, an IDE where the user works with coding agents. You talk with the user; an agent
session named "{name}" (folder {cwd}) does the work. For any action or task, or anything that needs the session's
files, tools, current state or history, delegate. Answer self-contained questions yourself, and questions about
what was already said or reported in this call.
Keep listening through coughs, throat-clears, sighs, a brief "mm-hm", music, other people talking, and thinking
pauses: none of them is a request. Respond to a clear request, a question, or being addressed. After you answer,
wait for the user; do not follow up on your own unless a result, a blocker or a missing detail arrives.
Avoid unnecessary narration: no repeated confirmations, filler, re-acknowledgements or play-by-play. When a result
arrives, give the takeaway in one to three short sentences; code, tables and logs stay in Orbit.
When a result arrives, say only what it says. Never add facts, names or details that are not in the result or in
this call; if the result does not answer what the user asked, say so plainly and offer to ask the agent again.
```

The grounding rule came from the first real call: the agent's answer said nothing about the README, and the voice
described a "TypeScript Node project using ESLint" that does not exist. The result is also framed as a quote
(`Request #1 is done. The agent's answer: “…”`), so the voice relays it rather than completing it.

The noise rule is steering, not a guarantee (GPT Live decides when to speak; there is no turn-detection setting).
It is checked against real calls (§9), including the recorded throat-clear follow-up.

## 4. Startup context (`session.input`)

Small and relevant, as text messages (developer role for facts, user/assistant for the last exchanges):
- the session: name, harness, folder, mode, state (working / ready), a running request's first line;
- its last 3 requests and answers, each answer cut to its first 600 characters;
- requests of this session still running or queued (card ids);
- `observed_at` on each.

Codex builds a larger block (current thread, recent work, a workspace map) but its own client turns it off; we keep
ours to about 2 000 tokens.

## 5. Delegation → a request in the session

On `session.delegation.created` (`delegation.id`, `offset_ms`; it carries **no task text**):
1. At once: `session.thinking.append {delegation_id, content: ""}`: cancels the automatic spoken acknowledgement
   (we set `acknowledgement_delay_ms: 800`). GPT Live may still speak on its own.
2. Assemble the request from the transcripts: input and output transcript deltas, ordered by `start_ms` (never by
   arrival), the user's words from the last delegation (or the call's start) up to `offset_ms`, plus any fragment
   that straddles it. Wait a grace window (400 ms, tuned from traces) for late fragments. Keep the text exactly.
3. Send it to the bound session: `POST /api/sessions/{sid}/send` with
   `{text, origin: {label: "Voice", data: {live: session_id, delegation: id}}, client_id: "live:<session>:<delegation>"}`.
   **New in Orbit:** `client_id` on send: the same `client_id` again returns the existing card (an at-most-once
   send: a duplicate delegation or a retried request makes no second card).
4. The text sent: the user's words, then (as context, not instruction) the assistant's last spoken reply when the
   request refers to it ("that", "the second one"). Codex wraps it as `<realtime_delegation><input>…</input>
   <transcript_delta>…</transcript_delta></realtime_delegation>`; we send the words plainly with a one-line
   `[orbit] voice` prefix the history strips.

Corrections: a delegation while the previous one's card is still queued edits that card instead (Orbit's `edit`);
while it runs and the harness steers (Claude Code), it steers it; otherwise a new request.

- **What became of it, told silently.** After each delegation the voice gets one `thinking.append` with the facts:
  sent and running; queued behind request #N ("tell the user in a few words that it is queued"); words joined to
  the waiting request #N ("one request, not several"); or added to the running one. Found in a real call: with the
  session busy, three things the user said became one queued request, and the voice, told nothing, invented "I sent
  several requests, handled one by one". The instructions also forbid describing Orbit's handling beyond what it is
  told. With the spoken acknowledgement on, the status waits until after its moment (any append cancels it).

## 6. Results → spoken

Following the card (`/api/events`, by session and card id):
- progress: at most one `session.thinking.append` per 10 s with a factual line (the step running), silent;
- the end: one `session.commentary.append {delegation_id, content}`: the answer's takeaway, ≤ 500 tokens. A long
  answer is cut to its first paragraphs plus "the rest is in Orbit"; code blocks and tables are left out;
- a failure, Stop or pause: one commentary saying so;
- each result is sent once (kept by delegation id); `.appended` acks are logged (context delivered, not played).

GPT Live paraphrases commentary; that is fine. A result arriving during standby still goes in (check the wake
behaviour in §9).

## 7. Interruption, hang-up, expiry

- The user talking over it: GPT Live stops itself; Orbit does nothing. Backend work goes on.
- "Stop that" as a request: delegated like any other; Orbit's Stop on the card only when the request says so.
- Hang-up (the button, or the page closing): the server sends `session.close`, closes the sideband, and adds the
  call's transcript to the session as a **note card** (no model call), with the delegations it made.
- `expires_at` / `session.closed {reason}`: shown; a new call starts fresh with the startup context (no reconnect to
  the same GPT Live session; fork is phase 2).
- The sideband drops: reattach with backoff (200 ms → 5 s, as Codex), dedupe replayed events by `event_id`.

## 8. The page

- A call button in the composer row (a microphone), ⌘⇧V; states: connecting · listening · speaking · standby ·
  ended; a red dot while the mic is open.
- Live captions in the conversation while the call is on (the user's and the voice's words, by timestamp), folded
  into the note card at hang-up.
- The delegations show as normal cards (their origin "Voice").
- macOS asks for the microphone once (Info.plist `NSMicrophoneUsageDescription`; WKWebView's request is granted
  by wry; `http://127.0.0.1` is a secure context).

## 9. Tests and acceptance

Automated (a fake GPT Live: an HTTP + websocket server speaking the documented events): session creation body,
sideband attach and replay dedupe, transcript assembly around `offset_ms` with late and straddling fragments,
`client_id` idempotent send, edit/steer on a correction, result → one commentary, standby events, hang-up note card,
the restricted data channel.

Real calls (logged: each stage's time and ids):

| Scene | Expected |
|---|---|
| "What did that result mean?" | answered directly, 0 new cards |
| "How is the nightly build doing now?" | one card; the call goes on meanwhile; the answer spoken once, traceable to the card |
| "Check the staging build… no, production" | the request carries the correction; no second card |
| an answer, then a throat-clear and 2 minutes of quiet | silence, 0 cards; standby after 30 s |
| a request running, hang up, call again | the card goes on and is reported in the next call's context; run once |

Measured: end of speech → first audio; barge-in → silence; delegation → card; card done → spoken.

## 10. Phases

1. This document, desktop app, one bound session.
2. Switching the bound session in a call; the phone (Safari: WebRTC + getUserMedia over HTTPS); fork to resume.
3. App-controlled playback (guaranteed silence) if the prompt is not enough.

## 11. Implementation notes (phase 1)

Built: `orbit/services/live/` (`session.py` the call, `ws.py` the websocket client), `/api/live/start · stop`,
`GET /api/live`, `{"t": "live"}` events, `client_id` on send and note cards in `orbit/desk/`, `web/src/voice.js`,
`tests/test_live.py` with `tests/fake_live.py`. Where the docs (OpenAI's GPT Live guides and API reference, read
2026-11-23) and this design differ, the docs won:

- **Creating the call.** `POST /v1/live/sessions` takes JSON `{"session": {…}, "transport": {"type": "webrtc",
  "sdp": OFFER}}` and answers 201 `{"session": {"id"}, "transport": {"type": "webrtc", "sdp": ANSWER}}`; no Location
  header, no `expires_at` (that comes in `session.started` / `session.updated`, when a replay carries one, else
  `session.closed {reason: "expired"}` says so). Codex rust-v0.167.2 still posts the older shape (multipart or
  `application/sdp`, the call id from Location) and parses older event names (`delegation.created`,
  `input_transcript.added`, `turn.done`): only its backoff and its "404 / 410 on reattach = the session ended" are
  taken from it.
- **`input`** items are `{"type": "message", "role", "content": [one part]}`: `input_text` for developer and user,
  `output_text` for assistant. `audio.format` is left out (WebRTC negotiates it).
- **Standby.** `idle_timeout_s` (5–60) only applies with `wake_policy {type: "speech"}`, and inference then waits for
  the first speech too: the button says "listening" as soon as the sideband is attached (`session.resumed` comes on
  the first wake, `session.paused` on idle).
- **The data channel.** `allowed_client_events: []` (an empty list allows none; leaving it out would allow all);
  `allowed_server_events` is a list of selectors (`{"type": …}`), here `session.started`, both transcript deltas,
  `session.paused`, `session.resumed`, `session.closed`. The page sends nothing: hang-up is the server's
  `session.close` on the sideband. The page takes captions from both the data channel and `/api/events` (one per
  `event_id`); the server's copy is the one kept (the note card, delegations).
- **Instructions** (§3) are rewritten in the prompting guide's template for `gpt-live-1.1`: "Backchannel policy:",
  "Interruption policy:" (the noise rule lives there) and "Delegation policy:" (backend tools; "Delegate to the
  backend when / Do not delegate when"). For `gpt-live-1` the guide's words become `Generate <|SpawnThinking|>` /
  `Do not generate <|SpawnThinking|>`. The model is the setting `live_model` (`POST /api/settings`; no UI yet).
- **Appends.** `session.thinking.append` / `commentary.append` need `delegation_id` (nullable) and take a plain-string
  `content` of at most 500 tokens; a commentary is kept to about 450 by an estimate (a CJK character one token, else
  four characters). Each carries an `event_id` (`quiet_<del>`, `progress_<del>_<n>`, `result_<del>`); the
  `….appended` acks (`client_event_id`) are logged. Any append cancels every pending automatic acknowledgement, so
  the empty one at delegation time does its job for all of them.
- **The sideband.** Each attach replays the last 3 s: events are kept by `event_id` and a replayed one is dropped
  (logged `replayed`); reflected audio (`session.output_audio.delta`, `session.input_audio.append`, no `event_id`) is
  ignored. A client event sent while it is down waits for the next attach. Reattach 200 ms → 5 s (doubling; reset after
  30 s connected); 404 / 410 after an attach, or 401 / 403, ends the call; never attached within 30 s ends it too.
- **Transcripts.** `start_ms` inclusive, `end_ms` exclusive, ms from the session's start. A delegation takes the
  user's fragments not taken before that start before its `offset_ms` (a straddling one included) and end after the
  previous delegation's offset, joined exactly as received, after the 400 ms grace. Words that come after a
  delegation's offset belong to the next one. The voice's last reply (its last run of fragments, pauses under 1.5 s)
  goes along, marked as context, only when the words refer to something (that, it, this, the second one …).
- **Sending.** In-process, `desk.send(sid, text, origin, client_id)`: the same code as the route. The card shows the
  words as said; the `[orbit] voice` marker is Orbit's existing `[orbit]` line, which for a `Voice` origin tells the
  agent that the request was spoken (it may be misheard) and that the start of its answer is read back. A correction
  edits the queued card (its text, a blank line, the new words) or steers the running one (Claude Code); the result is
  spoken once, for the latest delegation on that card. A delegation with no words makes no card: the voice is told.
- **Results.** Followed on the desk's own event stream (the `/api/events` feed). Progress only when the card changes
  (a long silent tool gets none); a paused card that retries waits; a result after the call ended is dropped (the next
  call's startup context lists the card). The commentary leaves out fenced code, tables and images, keeps whole
  paragraphs while they fit, and ends with "The rest is in Orbit." when it cut something.
- **Hang-up.** The page mutes its microphone at once and calls `/api/live/stop`; the server sends `session.close`,
  waits up to 5 s for `session.closed` (reasons `close_requested`, `expired`, `content`, `remote_hangup`,
  `connection_lost`), closes the sideband, then the page closes its peer connection. A call that ends over there
  (expiry, the page gone) finishes the same way. A new call hangs up the last one (one call per server).
- **Note cards.** `kind: "note"`, `state: "note"`, kept in `sessions.json` (`notes`, the last 200); its id sits after
  the last request (`4.001`, `4.002`) so request ids stay the harness's history numbers. Title "Voice call · 15:02 ·
  3 min", the transcript by turn with its time in the call, the requests it made, why it ended if not hung up. A call
  with no words and no request leaves none. The phone folds it as the desktop does.
- **`client_id`** (1–200 characters) is kept per session (`client_ids`, the last 500) and put back on the cards when
  they are rebuilt; a send with a known one returns that card with `"duplicate": true`.
- **The log**, `<state>/live/<call>.jsonl`: `create` / `created` (`ms_create`), `attached` (`ms_attach`, `n`),
  `transcript`, `delegation`, `request` (`ms_delegation_to_card`, how), `sent`, `event` (acks, standby), `result`,
  `close` / `close_done` / `ended` (reason, usage, replayed, attaches). Transcripts are in it (local); the key and
  audio never. End of speech → first audio and barge-in → silence need the audio and are not measured.
- **⌘⇧V** is in `plugins.RESERVED_SHORTCUTS` and ⌘/. The app's Edit menu has no Paste and Match Style, so nothing
  else holds it.
- **The phone** (`web/m/m-voice.js`, phase 2's phone part). The call itself is `web/src/voice-core.js`, shared with
  the desktop's `voice.js`: state, phases, captions, `/api/live/start · stop`, the data channel, the sound level, the
  gyroscope; each page fills in `VOICE_UI` (render, toast, failed, blocked, ended). The phone holds a call with its
  token like any client; no settings or key there. In a call the gyroscope takes the input's place (Type · End · Mute);
  elsewhere a bar ("in a call with X"). iOS needs the tap for `getUserMedia`, the `AudioContext` and playback (all
  before `startCall`'s first await); the background may end the microphone track (Mic puts a new one on the same
  sender: `replaceTrack`, no new call) and drop `/api/events` (its `hello` calls `voiceResync`: `GET /api/live`, the
  captions missed, or the call ended meanwhile). `pagehide` hangs up with `keepalive`.
- **Browsers.** Checked with Playwright on a temp server against the fake: Chromium (with
  `--use-fake-device-for-media-stream --use-fake-ui-for-media-stream`) and WebKit (its mock microphone and the
  `microphone` permission need no flags) both make the call, connect the peer (a second peer connection answers
  for the fake), open `oai-events`, show captions, turn a delegation into a card and its answer into one commentary,
  show standby, and hang up into a note card.
