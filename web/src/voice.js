"use strict";
/* ===================================================================== voice: a GPT Live call (builddoc/gpt-live.md) */
/* A call with the session in front (⌘⇧V or the microphone in the composer row). The microphone and the speaker go
   straight to OpenAI over WebRTC: the page makes the offer, this server creates the call with its key (the key never
   comes here) and hands back the answer. The server follows the call on its sideband and turns what the voice
   delegates into requests in the session (cards "via Voice"); its answers go back to be spoken. The button shows the
   phase (connecting · listening · speaking · standby · ended), a red dot while the microphone is open; captions run
   at the end of the conversation while the call is on, and become a note card when it ends. */
const VOICE = { call: null, sid: null, phase: "idle", starting: false, pc: null, mic: null, dc: null, caps: new Map(), endTm: 0, spTm: 0 };
const VOICE_WORD = { connecting: "Connecting…", listening: "Listening", speaking: "Speaking", standby: "Standby", ended: "Call ended" };
const voiceAudio = new Audio();
voiceAudio.autoplay = true;
$("#callbtn").innerHTML = I("mic", 15);

function toggleCall() { if (VOICE.phase === "idle" || VOICE.phase === "ended") startCall(); else hangUp(); }
function setVoicePhase(p) {
  VOICE.phase = p;
  clearTimeout(VOICE.endTm);
  if (p === "ended") VOICE.endTm = setTimeout(() => { if (VOICE.phase === "ended") { VOICE.call = null; VOICE.caps.clear(); setVoicePhase("idle"); } }, 2500);
  renderCaps();
}
/* The button and the captions (also from renderConv: a switch to another session hides this call's captions). */
function renderCaps() {
  const b = $("#callbtn"), st = $("#callst"), s = cur();
  const on = VOICE.phase !== "idle", here = on && VOICE.sid === S.current;
  const name = VOICE.sid && S.sessions.get(VOICE.sid) ? S.sessions.get(VOICE.sid).name : "";
  b.className = `ibtn sm${on ? " call-" + VOICE.phase : ""}${VOICE.mic && on && VOICE.phase !== "ended" ? " mic-open" : ""}`;
  b.disabled = !on && (!s || !!s.remote || VOICE.starting);
  b.setAttribute("aria-pressed", String(on && VOICE.phase !== "ended"));
  b.title = on ? `${VOICE_WORD[VOICE.phase]}${name ? ` with ${name}` : ""}${VOICE.phase === "ended" ? "" : " · click or ⌘⇧V to hang up"}`
    : s && s.remote ? "A voice call works with this Mac's sessions" : "Voice call with this session (⌘⇧V): talk, and it does the work";
  st.hidden = !on;
  st.textContent = on ? (here || !name ? VOICE_WORD[VOICE.phase] : `${VOICE_WORD[VOICE.phase]} · ${name}`) : "";
  st.className = `callst${on ? " call-" + VOICE.phase : ""}`;
  const box = $("#livecaps");
  const show = here && !S.file && VOICE.caps.size > 0;
  box.hidden = !show;
  if (!show) { box.innerHTML = ""; return; }
  const items = [...VOICE.caps.values()].sort((a, b) => a.start - b.start || a.seq - b.seq);
  const turns = [];
  for (const x of items) {
    const last = turns[turns.length - 1];
    if (last && last.who === x.who && x.who !== "sys") last.text += x.text; else turns.push({ who: x.who, text: x.text });
  }
  box.innerHTML = `<div class="lchead"><span class="lcdot"></span>Live captions</div>` + turns.slice(-14).map(t => t.who === "sys"
    ? `<div class="lc sys">${esc(t.text)}</div>`
    : `<div class="lc ${t.who}"><div class="lcb">${esc(t.text.trim())}</div></div>`).join("");
  stick();
}
let capSeq = 0;
function addCaption(c) {
  if (!c || !c.id || VOICE.caps.has(c.id)) return;  // (the server's copy and the data channel's: one)
  VOICE.caps.set(c.id, { who: c.who, text: c.text || "", start: +c.start || 0, seq: ++capSeq });
  renderCaps();
}
/* {"t": "live"} from the server: the call's state, captions, the requests it made, errors. */
function voiceEvent(m) {
  if (!VOICE.call && VOICE.starting && m.kind === "state" && m.state && m.state.sid === VOICE.sid) VOICE.call = m.call;
  if (!m.call || m.call !== VOICE.call) return;
  if (m.kind === "caption") addCaption(m.caption);
  else if (m.kind === "delegation") {
    const how = m.how === "edit" ? `changes request #${m.cid}` : m.how === "steer" ? `steers request #${m.cid}` : `→ request #${m.cid}`;
    VOICE.caps.set("d:" + m.delegation, { who: "sys", text: how, start: +m.offset || 0, seq: ++capSeq });
    renderCaps();
  } else if (m.kind === "error") toast("Voice: " + m.message);
  else if (m.kind === "state") {
    const st = m.state || {};
    if (st.phase === "ended") {
      endMedia();
      if (VOICE.phase !== "ended") {
        const why = { expired: "the voice session's time ran out", remote_hangup: "", close_requested: "", content: "a safety filter ended it",
          connection_lost: "the connection was lost" }[st.reason];
        if (st.error || why) toast(`Call ended: ${st.error || why}`);
      }
      setVoicePhase("ended");
    } else if (VOICE.phase !== "ended") setVoicePhase(st.phase);
  }
}
/* The data channel: the events the call lets the page have (transcripts, standby), as a quicker copy. */
function voiceChannelEvent(ev) {
  if (ev.type === "session.input_transcript.delta" || ev.type === "session.output_transcript.delta") {
    addCaption({ id: ev.event_id, who: ev.type === "session.input_transcript.delta" ? "user" : "voice", text: ev.delta, start: ev.start_ms });
    if (ev.type === "session.output_transcript.delta" && (VOICE.phase === "listening" || VOICE.phase === "speaking")) {
      setVoicePhase("speaking");  // (until the server says otherwise, or a moment without its words)
      clearTimeout(VOICE.spTm);
      VOICE.spTm = setTimeout(() => { if (VOICE.phase === "speaking") setVoicePhase("listening"); }, 1500);
    }
  } else if (ev.type === "session.paused" && VOICE.phase !== "ended") setVoicePhase("standby");
  else if (ev.type === "session.resumed" && VOICE.phase === "standby") setVoicePhase("listening");
}
function iceGathered(pc, ms = 10000) {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise(res => {
    const tm = setTimeout(done, ms);  // (whatever it has by then: host candidates come at once)
    function done() { clearTimeout(tm); pc.removeEventListener("icegatheringstatechange", on); res(); }
    function on() { if (pc.iceGatheringState === "complete") done(); }
    pc.addEventListener("icegatheringstatechange", on);
  });
}
async function startCall() {
  const s = cur();
  if (!s || VOICE.starting) return;
  if (s.remote) { toast("A voice call works with this Mac's sessions"); return; }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.RTCPeerConnection) { toast("No microphone here: this page has no WebRTC"); return; }
  VOICE.starting = true; VOICE.sid = s.id; VOICE.call = null; VOICE.caps.clear();
  setVoicePhase("connecting");
  let pc = null;
  try {
    VOICE.mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    renderCaps();
    pc = VOICE.pc = new RTCPeerConnection();
    pc.addEventListener("track", e => {
      voiceAudio.srcObject = e.streams && e.streams[0] ? e.streams[0] : new MediaStream([e.track]);
      voiceAudio.play().catch(() => toast("Click in the page to hear the voice", () => voiceAudio.play().catch(() => {})));
    });
    pc.addEventListener("connectionstatechange", () => {
      if (VOICE.pc === pc && pc.connectionState === "failed") { toast("The voice connection failed"); hangUp(); }
    });
    for (const t of VOICE.mic.getAudioTracks()) pc.addTrack(t, VOICE.mic);
    const dc = VOICE.dc = pc.createDataChannel("oai-events");  // before the offer (it receives only what the call allows)
    dc.addEventListener("message", e => { try { voiceChannelEvent(JSON.parse(e.data)); } catch {} });
    await pc.setLocalDescription(await pc.createOffer());
    await iceGathered(pc);
    const r = await api("POST", "/api/live/start", { sid: s.id, sdp: pc.localDescription.sdp });
    if (VOICE.pc !== pc) { api("POST", "/api/live/stop", { call: r.call }).catch(() => {}); return; }  // hung up meanwhile
    VOICE.call = r.call;
    await pc.setRemoteDescription({ type: "answer", sdp: r.sdp });
    if (VOICE.phase === "connecting") renderCaps();
  } catch (e) {
    const call = VOICE.call;
    endMedia();
    VOICE.call = null;
    if (call) api("POST", "/api/live/stop", { call }).catch(() => {});
    setVoicePhase("idle");
    if (e && e.data && e.data.need === "openai-key") toast("A voice call needs an OpenAI API key: Settings → General (click)", () => settingsDialog("general"));
    else if (e && (e.name === "NotAllowedError" || e.name === "SecurityError")) toast("Orbit may not use the microphone: allow it in System Settings → Privacy & Security → Microphone");
    else if (e && e.name === "NotFoundError") toast("No microphone found");
    else toast("Voice call failed: " + (e && e.message || e));
  } finally {
    VOICE.starting = false;
    renderCaps();
  }
}
/* Hang up: the microphone goes quiet at once; the server closes the call (session.close, then session.closed) and
   keeps its transcript as a note card; then the connection closes. */
async function hangUp() {
  const call = VOICE.call;
  if (VOICE.mic) for (const t of VOICE.mic.getTracks()) t.enabled = false;
  setVoicePhase("ended");
  try { if (call) await api("POST", "/api/live/stop", { call }); }
  catch (e) { if (e.status !== 404) toast("Hang-up: " + e.message); }
  finally { endMedia(); renderCaps(); }
}
function endMedia() {
  if (VOICE.mic) for (const t of VOICE.mic.getTracks()) t.stop();
  try { if (VOICE.dc) VOICE.dc.close(); } catch {}
  try { if (VOICE.pc) VOICE.pc.close(); } catch {}
  voiceAudio.srcObject = null;
  VOICE.mic = VOICE.dc = VOICE.pc = null;
}
$("#callbtn").addEventListener("mousedown", e => e.preventDefault());  // (the input keeps the keyboard)
$("#callbtn").addEventListener("click", toggleCall);
addEventListener("pagehide", () => { if (VOICE.call && VOICE.phase !== "ended") api("POST", "/api/live/stop", { call: VOICE.call }, { keepalive: true }).catch(() => {}); });
