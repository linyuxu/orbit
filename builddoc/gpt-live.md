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
| `live_ack` | on/off (on) | off: the empty `thinking.append` at each delegation cancels the automatic acknowledgement |
| `live_chime_sound` | wood, bell, single, bowl, temple (single) | which sound (`web/src/voice-core.js` CHIMES: low struck notes, ~0.5 s: a marimba's knock, a low bell F3 → C4, one strike, a shimmering bowl, a bell with a wooden tap); Settings plays each on a click |
| `live_chime` | on/off (on) | the page's own sounds (`/api/live/start` answers `chime`: the sound's name, or false): a rising two-note ding once the call is connected and listening (the WebRTC connection up and the phase past connecting), a falling one at the end |
| `live_prompt` | up to 4 000 characters | after Orbit's instructions; they win over style, never over delegation and grounding |

A voice's preview (`orbit/services/live/preview.py`): the WebSocket transport (`wss://…/v1/live/sessions`,
`session.start` first, PCM 24 kHz), a developer message to greet with one line naming the voice, silence as input.
The output streams continuously (silence too, and no "audio done" event), so speech is found by loudness (RMS of
0.1 s windows) and the recording ends after 1.2 s of quiet; measured: about 9 s to make, 5 s of speech.

## 3. Instructions

Short, in the structure of OpenAI's live prompting template (§11) and the style of Codex's shipped prompt
(`backend_prompt.md`): it delegates every action, answers self-contained and status questions itself, and avoids
narration. Plus what Codex's prompt lacks, the noise rule. The delegation and grounding part (`instructions()`):

```
Delegate to the backend when:
- The user asks for any action or task.
- The answer needs the session's files, tools or history.
- A correction changes the work already requested.
- The user wants to stop, add to, withdraw or cancel a request.
What Orbit can do with requests (answer questions about it yourself): they run one at a time in this session; to stop
the running request, add something to it while it runs, or take back one that has not started, delegate the user's
words: Orbit does it and tells you which.
Do not delegate when:
- The question is self-contained, or about what was already said or reported in this call.
- The user asks about status, progress, what is running or queued, or how long something has taken: answer at once
from the latest Session state. Give elapsed times; never invent an estimate. If the state does not answer it, say what
you know and offer to ask the agent.
- You need a brief clarification to understand the request.
Use a previous result when it still answers the question (more of an answer may have come with it as silent context);
delegate again if it is missing, out of date, or the user asks to check again.
Delegate to the backend before giving an answer that depends on backend work. Do not guess the result while waiting.
Never describe how Orbit or the agent handles requests beyond what Orbit tells you in this call.
Results and Orbit's facts are authoritative: do not add to them or contradict them. Say the key takeaway, status or
next step in your own words. Never invent facts, names, numbers or details that are not in a result, in Orbit's facts
or in this call; if the result does not answer what the user asked, say so plainly and offer to ask the agent again.

Orbit sends you background facts (session state, progress) silently. Do not announce them, and do not say you are
still working, unless the user asks or a meaningful change happened (a step that matters finished, a delay that
matters, or the user must answer something).
Avoid unnecessary narration: no repeated confirmations, filler, re-acknowledgements or play-by-play. When a result
arrives, give the takeaway in one to three short sentences; code, tables and logs stay in Orbit.
```

With `live_language: auto` the language line is "Speak the language the user speaks; for Chinese, speak Mandarin and
use Simplified characters" (a real call drifted into Traditional characters mid-answer).

The grounding rule came from the first real call: the agent's answer said nothing about the README, and the voice
described a "TypeScript Node project using ESLint" that does not exist. It used to be "say only what it says", with
the result framed as a quote (`Request #1 is done. The agent's answer: “…”`); that made the voice read reports aloud
(a 23 s Markdown monologue). It is now Codex's "authoritative" rule plus an explicit "never invent", and the result is
facts to say (§6). Re-check the README scene in real calls (§9).

The noise rule is steering, not a guarantee (GPT Live decides when to speak; there is no turn-detection setting).
It is checked against real calls (§9), including the recorded throat-clear follow-up.

### Naturalness (2026-11-23)

A study of Codex's voice against 27 of Orbit's call logs found that Orbit itself caused much of the unnatural talk:
- a 10 s "Request #N is still running: Bash sed -n … Nothing to say yet." feed: 9 of 13 such appends in one call were
  followed within 4 s by "还在看，稍等一下" with no user speech before; Codex's TUI sends nothing mid-task;
- "is it done / how long" was delegated (the prompt said to delegate "current state") and queued behind the work it
  asked about; when the voice had the facts (startup context) it answered in about 2 s;
- results were sent the moment the card finished, three times over the user's speech;
- the voice said "能用" while waiting, and the result said "还不能";
- results were read as quoted reports, Markdown and all.

So: the session's state as silent snapshots, status answered from them (§6); no progress lines; plain-fact statuses
("nothing else runs before it" was read back as "not queued"; "tell the user it is queued" only when the wait is
real, > 30 s); "Don't state the answer until the result arrives" at each delegation; results held while the user
talks; results as short facts to say, the rest as silent detail. A replay of the logs through the new code: 20
progress appends gone; spoken result text 6 181 → 1 573 tokens (estimated) and 0 of 21 with Markdown (was 18); 2 results
would have waited 2.2 s and 5.5 s for the user to finish. Next (not built): a warmer one-assistant persona and one
next-step offer, results not yet told at the next call's start, the conversation sent to the agent with each request.

## 4. Startup context (`session.input`)

Small and relevant, as text messages (developer role for facts, user/assistant for the last exchanges):
- the session: name, harness, folder, mode, and its state as the snapshot of §6 (what runs and for how long, the
  agent's latest words, what is queued, the last finished request);
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

- **What became of it, told silently.** After each delegation the voice gets one `thinking.append` with the facts,
  each ending "Don't state the answer until the result arrives.": sent ("the agent started on it"); queued behind
  request #N (and only when that one has run over 30 s: "tell the user in a few words that it is queued", held while
  the user talks like a result); words joined to the waiting request #N ("one request, not several"); added to the
  running one; or, asked to add to the running one with a harness that cannot, queued as #N and why. Found in a real
  call: with the session busy, three things the user said became one queued request, and the voice, told nothing,
  invented "I sent several requests, handled one by one". With the spoken acknowledgement on, the status waits until
  after its moment (any append cancels it).
- **Operating on requests.** Words that only take a request back ("撤回", "算了", "停止当前在跑的任务", "stop the
  running task"; not "stop the server" or "取消那个定时任务") are done by Orbit, not sent: words naming the running one
  (在跑 · 正在 · 当前 · 运行 · running · current, or a stop verb) stop the session's running request, whoever made it,
  and leave the queue alone; a withdraw verb (撤回 · 收回 · withdraw) withdraws the call's waiting request, else the
  session's latest; otherwise the call's last request is withdrawn or stopped. A steer cue ("插一句 / 补充 / 告诉它 /
  顺便 / tell it to / add this to the running task …") sends the words after it into the session's running request
  (how `steer`; nothing running: an ordinary request). "插进去 / steer it in" with nothing else withdraws the waiting
  request and steers its words into the running one. The voice is told what was done (a commentary: the user asked).

## 6. Session state and results

Following the desk's event stream (`/api/events`; another machine's session's cards arrive there too, as `name~sid`):
- **The session's state** (`state_snapshot()`): every card of the bound session, whoever made it (typed, a plugin, a
  follow-up), updates one short factual block sent as `session.thinking.append {delegation_id: null}` (the guide's
  "background context update"), under ~150 tokens: `Session state at 18:33:10: "IDE Dev" is working on request #250
  ("…") for 6 min 12 s, 14 steps so far; its latest words (1 min 40 s ago): "…". Queued after it: #251 ("…"). Last
  finished: request #249 ("…") at 18:20, done: "<gist of its answer>".` The agent's latest words are the card's last
  `kind: "note"` step (its interim text; never a tool command). Changes within 2 s go out as one; a snapshot that would
  say the same (only the clock moved) is skipped; one that only moves the step on waits until 30 s after the last;
  started, finished, failed or queued go out after the 2 s. None while a result waits to be said. The cards are read
  once at the call's start (one request for another machine's session), then kept from the events. The state at
  the start is in the startup input (§4), not sent again: an append at connect is one more chance for the voice to
  speak, and the startup input is the documented place for it. The voice answers status from it (§3).
- **No progress lines** on a request's delegation (the 10 s feed of tool commands is gone).
- **The end:** one `session.commentary.append {delegation_id}`: facts to say, not a report to read. The request is
  named by a gist of its words (`Request #252 ("research why …") is done: …`), then the answer's opening in plain
  words (Markdown marks, headings, fenced code, tables and images removed; a list read as one sentence), whole
  paragraphs within ~100 tokens (`brief`, ~75 words) or ~200 (`detailed`), a question the answer ends with ("It ends
  by asking: …"), and "The full answer is in Orbit." when something stays only there. Just before it, the next ~300
  tokens of the answer go as a silent `thinking.append` on the same delegation, so "what were the other commits?" is
  answered without a new request.
- **Never over the user:** a result waits while the user is talking (a fragment of theirs came within 1.2 s; they
  arrive every ~200 ms while someone speaks), released by the clock when they pause, or after 8 s.
- a failure, Stop or pause: one commentary saying so;
- each result is sent once (kept by delegation id); `.appended` acks are logged (context delivered, not played).

**Standby and a task in flight** (tested against GPT Live over WebRTC, 2026-11-23, `idle_timeout_s` 5): while a
delegation has no result yet the session does not go to standby (60 s of silence, no `session.paused`); the result's
`commentary.append` is spoken at once (~0.8 s), and standby comes ~5 s after it finished speaking. So a task that
takes minutes is still said when it ends, and that wait is billed as active. (The WebSocket transport refuses
`idle_timeout_s`: "requires a session with wake policy support".) Muting only disables the page's microphone track:
GPT Live sees silence, so standby follows the same rule.
In standby a `commentary.append` **waits**: no `.appended` while the session is paused (the docs:
"If the session timeline stops, the acknowledgment can remain pending"); when the user's speech wakes it
(`session.resumed`), it is acknowledged and goes in, and the voice says it with its answer (tested: a follow-up sent
in standby, then "喂，你还在吗？" 34 s later → "在呢。还有刚补充说测试全过了，四百一十二个，没有失败。"). So nothing
is lost, but nothing wakes it either: only speech does (no client wake event is documented). An earlier test that
never woke the session took the waiting append for a dropped one. While active, a second commentary on a finished
delegation is spoken (~0.9 s). Orbit speaks only on open delegations (results, failures, take-back / move-in
facts), which keep the session active, so its words are heard when they come; silent state snapshots sent in
standby should go in at the next wake by the same rule (`thinking.append` in standby: not tested).

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
`client_id` idempotent send, edit/steer on a correction, result → one commentary (its shape, the silent detail, held
while the user talks), session snapshots (a card the call did not make, coalesced, unchanged skipped, no progress
lines), take-back / stop / steer / move-in, standby events, hang-up note card, the restricted data channel.

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
  four characters). Each carries an `event_id` (`quiet_<del>`, `status_<del>`, `detail_<del>`, `result_<del>`,
  `state_<n>` for the session snapshots); the
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
- **Results.** Followed on the desk's own event stream (the `/api/events` feed); progress is the session snapshot
  (§6); a paused card that retries waits; a result after the call ended is dropped (the next call's startup context
  lists the card). The log has `held` (a result waiting for the user) and `held_ms` on its `result` row.
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

## 12. The watch (server side; the watchOS app comes next)

An Apple Watch has no browser that could hold a WebRTC call, and watchOS keeps low-level networking (WebSocket, the
Network framework) from most apps; URLSession's HTTP requests always work, over Wi-Fi or through the iPhone by
Bluetooth. So for the watch this server holds the whole GPT Live session, over its **WebSocket transport** (as the
voice preview does), and relays the audio over plain HTTP (`orbit/server/watch.py`):

| Route | Does |
|---|---|
| `POST /api/live/watch/start {sid}` | the call: `{call, live, rate: 24000, format: "pcm16", chime}` once `session.started` came (≤ 15 s); 400 no key (`need: "openai-key"`), 404 no such session, 409 a call already (`busy`), 502 OpenAI refused (its message) |
| `POST /api/live/watch/<call>/up` | body: raw PCM 16-bit mono 24 kHz little-endian, ~100–200 ms a chunk (at most 256 KB) → 204, `Connection: keep-alive` (the next chunk on the same connection); 404 once the call ended |
| `GET /api/live/watch/<call>/down` | a stream of frames until the call ends (no length: it ends with the connection): 1 byte type, 4 bytes length (big-endian), the payload. `a`: the voice's audio, PCM like the upload (silent chunks are not sent: the output streams silence too); `j`: JSON, the `{"t": "live"}` events the pages get (`kind`: `state` first and at each phase, `caption`, `delegation`, `error`), and `{"t": "ping"}` every 10 s. The last `state` has `phase: "ended"` (with `reason`, `error`, `note`), then the stream closes |
| `POST /api/live/watch/<call>/stop` | hang-up (as `/api/live/stop`) |

The token as the phone's: `Authorization: Bearer <token>` (the `?token=` of the phone's link), checked by the
handler's guard; from the network, HTTPS once it is on.

The call is the same `Call` (delegations, corrections, the session's state, results held while the user talks, the
note card): `transport: "watch"` in `/api/live`, so the Mac's and the phone's pages show it as a call held elsewhere
(their bar, and ⌘⇧V hangs it up). What differs:
- `session.start` (`ws_session()`): the session of §2 with `audio.format {type: "audio/pcm", rate: 24000}`, without
  `wake_policy` / `idle_timeout_s` (the WebSocket transport refuses them: "requires a session with wake policy
  support") and the page's data channel. So **no standby**: the watch's call is billed active ($0.05/min) until
  hang-up; the watch should hang up when its user is done.
- The session lives as long as the socket (no reattach on this transport): when it goes, the call ends
  (`connection_lost`). Events need no replay dedupe; `session.started` carries the session's id.
- Input audio: each upload goes in as `session.input_audio.append` (at most 1 s each); when the watch sends nothing for
  0.6 s, 0.25 s of silence goes in each 0.25 s (the docs: keep input audio running), so a pause in the uploads (the
  Bluetooth link, the wrist down) does not stop the session's timeline.
- Audio is never logged; the call's log has `audio {up_bytes, down_bytes, quiet_chunks}` at its end.
- A down stream that stops reading keeps at most 400 frames (the oldest go); a new `down` may be opened at any time
  (after a network change): it starts with the call's state.

Tested (`tests/test_live_watch.py`, the fake's WebSocket call mode): the session.start, uploads on one kept-alive
connection, a loud chunk relayed and a silent one not, a delegation into a card and its captions on the stream, the
result said, hang-up ending the stream with the note card, silence filling a pause, the socket lost, the errors, and
the token from another machine.

