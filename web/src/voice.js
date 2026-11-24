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
/* The button: five blue bars (a voice), as big as Send's arrow; in a call the bars follow the sound (the microphone
   while you talk, the voice while it speaks), and a hover shows the stop. */
const WAVE_H = [1.6, 3.4, 5.2, 3.4, 1.6];  // half-heights: short, tall, tallest, tall, short
$("#callbtn").innerHTML = `<svg class="wave" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.55" stroke-linecap="round">${
  WAVE_H.map((h, i) => `<line class="wb" style="--i:${i}" x1="${2.4 + i * 2.8}" x2="${2.4 + i * 2.8}" y1="${8 - h}" y2="${8 + h}"/>`).join("")
}</svg><svg class="hang" width="16" height="16" viewBox="0 0 16 16"><rect x="4.5" y="4.5" width="7" height="7" rx="1.5" fill="currentColor"/></svg>`;
const WAVE_BARS = [...$("#callbtn").querySelectorAll(".wb")];

function toggleCall() { if (VOICE.phase === "idle" || VOICE.phase === "ended") startCall(); else hangUp(); }
function setVoicePhase(p) {
  VOICE.phase = p;
  clearTimeout(VOICE.endTm);
  if (p === "ended") VOICE.endTm = setTimeout(() => { if (VOICE.phase === "ended") { VOICE.call = null; VOICE.caps.clear(); setVoicePhase("idle"); } }, 2500);
  renderCaps();
}
/* The button and the captions (also from renderConv: a switch to another session hides this call's captions). */
function renderCallBtn() {
  const b = $("#callbtn"), st = $("#callst"), s = cur();
  const on = VOICE.phase !== "idle", here = on && VOICE.sid === S.current;
  const name = VOICE.sid && S.sessions.get(VOICE.sid) ? S.sessions.get(VOICE.sid).name : "";
  b.disabled = !on && (!s || VOICE.starting);
  b.className = `ibtn sm${on ? " call-" + VOICE.phase : ""}${VOICE.mic && on && VOICE.phase !== "ended" ? " mic-open" : ""}`;
  b.setAttribute("aria-pressed", String(on && VOICE.phase !== "ended"));
  b.title = on ? `${VOICE_WORD[VOICE.phase]}${name ? ` with ${name}` : ""}${VOICE.phase === "ended" ? "" : " · click or ⌘⇧V to hang up"}`
    : "Voice call with this session (⌘⇧V): talk, and it does the work";
  /* In a call with the session in front: the orb in the input's place (unless typing instead); the state under it. */
  const orb = on && here && !S.file && !VOICE.typing, ob = $("#voiceorb");
  ob.hidden = !orb;
  GYRO.run(orb);
  ob.className = on ? "call-" + VOICE.phase : "";
  $("#inputhost").style.display = orb ? "none" : "";
  ob.querySelector(".vorbst").textContent = orb ? VOICE_WORD[VOICE.phase] + (VOICE.phase === "standby" ? " · speak to wake it" : "") : "";
  st.hidden = !on || orb;
  st.textContent = on ? (here || !name ? VOICE_WORD[VOICE.phase] : `${VOICE_WORD[VOICE.phase]} · ${name}`) : "";
  st.className = `callst${on ? " call-" + VOICE.phase : ""}${on && here && VOICE.typing ? " back" : ""}`;
  st.title = on && here && VOICE.typing ? "Show the voice again" : "";
}
$("#voiceorb .vtype").innerHTML = I("keyboard", 15);
$("#voiceorb .vtype").addEventListener("click", () => { VOICE.typing = true; renderCallBtn(); input.focus(); });
$("#callst").addEventListener("click", () => { if (VOICE.typing && VOICE.phase !== "idle") { VOICE.typing = false; renderCallBtn(); } });
function renderCaps() {
  renderCallBtn();
  const on = VOICE.phase !== "idle", here = on && VOICE.sid === S.current;
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
  /* Folded (a click on the head; kept): one line, the latest words, so the agent's answers have the room. */
  const folded = !!LS.get("capsFolded", false);
  const said = turns.filter(t => t.who !== "sys");
  const lastT = said[said.length - 1];
  box.classList.toggle("folded", folded);
  box.innerHTML = `<button type="button" class="lchead" aria-expanded="${!folded}" title="${folded ? "Show the captions" : "Fold the captions to one line"}">` +
    `${I("chev", 11)}<span class="lcdot"></span><span class="lctitle">Live captions</span>` +
    (folded && lastT ? `<span class="lclast"><b>${lastT.who === "user" ? "You" : "Voice"}</b> ${esc(lastT.text.trim())}</span>` : "") + `</button>` +
    (folded ? "" : turns.slice(-14).map(t => t.who === "sys"
      ? `<div class="lc sys">${esc(t.text)}</div>`
      : `<div class="lc ${t.who}"><div class="lcb">${esc(t.text.trim())}</div></div>`).join(""));
  stick();
}
$("#livecaps").addEventListener("mousedown", e => { if (e.target.closest(".lchead")) e.preventDefault(); });  // (the input keeps the keyboard)
$("#livecaps").addEventListener("click", e => {
  if (!e.target.closest(".lchead")) return;
  LS.set("capsFolded", !LS.get("capsFolded", false));
  renderCaps();
});
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
/* The bars follow the sound: an analyser on the microphone and one on the voice; the louder of the two, smoothed. */
const METER = { ctx: null, taps: [], raf: 0, v: 0 };
function meterTap(stream) {
  try {
    if (!METER.ctx) return;
    const a = METER.ctx.createAnalyser();
    a.fftSize = 512;
    METER.ctx.createMediaStreamSource(stream).connect(a);
    METER.taps.push({ a, buf: new Uint8Array(a.fftSize) });
    if (!METER.raf) METER.raf = requestAnimationFrame(meterTick);
  } catch {}
}
function meterTick() {
  let rms = 0;
  for (const t of METER.taps) {
    t.a.getByteTimeDomainData(t.buf);
    let sum = 0;
    for (const x of t.buf) sum += (x - 128) * (x - 128);
    rms = Math.max(rms, Math.sqrt(sum / t.buf.length) / 128);
  }
  const lv = Math.min(1, rms * 5);  // speech sits near 0.05–0.2 RMS
  METER.v = lv > METER.v ? lv : METER.v * 0.86;  // quick up, slow down
  const tm = performance.now() / 1000;
  WAVE_BARS.forEach((el, i) => {
    const jig = 0.75 + 0.25 * Math.sin(tm * (7 + i * 1.7) + i * 2.1);  // so the bars do not move as one
    el.style.transform = `scaleY(${(0.42 + 0.58 * Math.min(1, METER.v * jig * (i === 2 ? 1 : 1.15))).toFixed(3)})`;
  });
  METER.raf = requestAnimationFrame(meterTick);
}
/* The orb in a call: a gyroscope. A cloud core (it swells with the sound) and two rings tumbling around it in 3D:
   each ring's near half passes in front of the core, its far half behind. Faster while the voice speaks, nearly
   still in standby. Its own frame loop runs while it is shown. */
const GYRO = (() => {
  const svg = $("#voiceorb .vorb");
  svg.innerHTML = `<defs>
      <radialGradient id="vgN" cx=".38" cy=".32" r=".75"><stop offset="0" stop-color="#dff0ff"/><stop offset=".35" stop-color="var(--voice-1)"/><stop offset=".75" stop-color="var(--voice-2)"/><stop offset="1" stop-color="#1a3596"/></radialGradient>
      <radialGradient id="vgG"><stop offset="0" stop-color="var(--voice-1)" stop-opacity=".5"/><stop offset="1" stop-color="var(--voice-1)" stop-opacity="0"/></radialGradient>
      <filter id="vgB" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="1.6"/></filter>
      <clipPath id="vgC"><circle cx="50" cy="50" r="17"/></clipPath></defs>
    <g class="gback"></g>
    <g class="gcore"><circle cx="50" cy="50" r="31" fill="url(#vgG)"/><circle cx="50" cy="50" r="17" fill="url(#vgN)"/>
      <g clip-path="url(#vgC)" style="mix-blend-mode:screen">
        <ellipse class="gcl" cx="44" cy="46" rx="9" ry="6.5" fill="#fff" opacity=".75" filter="url(#vgB)"/>
        <ellipse class="gcl" cx="55" cy="55" rx="7.5" ry="7" fill="#a5f3fc" opacity=".75" filter="url(#vgB)"/>
        <ellipse class="gcl" cx="48" cy="58" rx="6.5" ry="5" fill="#c7d2fe" opacity=".75" filter="url(#vgB)"/></g></g>
    <g class="gfront"></g>`;
  const back = svg.querySelector(".gback"), front = svg.querySelector(".gfront"), core = svg.querySelector(".gcore");
  const clouds = [...svg.querySelectorAll(".gcl")];
  const path = () => {
    const e = document.createElementNS("http://www.w3.org/2000/svg", "path");
    e.setAttribute("fill", "none"); e.setAttribute("stroke", "var(--voice-1)"); e.setAttribute("stroke-linecap", "butt");  // (round ends would overlap where the halves meet)
    return e;
  };
  const rings = [0, 1].map(() => ({ lower: path(), upper: path() }));  // each ring as two halves (A40 ry, sweep 0 · 1)
  let ph = 0, lv = 0, raf = 0, last = 0;
  const REDUCED = matchMedia("(prefers-reduced-motion: reduce)");  // (slower, not still: it still says the call is live)
  const frame = now => {
    const dt = Math.min(.05, (now - (last || now)) / 1000); last = now;
    const p = VOICE.phase, T = now / 1000;
    lv += ((p === "listening" || p === "speaking" ? METER.v : 0) - lv) * .3;
    const speed = { speaking: 1.9, listening: 1, connecting: .6, standby: .12, ended: 0 }[p] ?? .5;
    ph += dt * speed * (1 + lv) * (REDUCED.matches ? .3 : 1.6);
    core.setAttribute("transform", `translate(50 50) scale(${(1 + lv * .14).toFixed(3)}) translate(-50 -50)`);
    clouds.forEach((c, i) => c.setAttribute("transform", `translate(${(Math.sin(ph * (.5 + i * .2) + i) * 3.5).toFixed(2)} ${(Math.cos(ph * (.45 + i * .17) + i * 2) * 3.5).toFixed(2)})`));
    rings.forEach((r, i) => {
      const q = ph + i * 1.7, c = Math.cos(q * (i ? .8 : 1)), ry = Math.abs(c) * 40 + 3, rot = (q * 30 + i * 90) % 360;
      const nearLower = c > 0;  // (the half towards the viewer flips as the ring turns through edge-on)
      for (const [k, sw] of [["lower", 0], ["upper", 1]]) {
        const el = r[k], near = (k === "lower") === nearLower, g = near ? front : back;
        if (el.parentNode !== g) g.appendChild(el);
        el.setAttribute("d", `M10 50 A40 ${ry.toFixed(2)} 0 0 ${sw} 90 50`);
        el.setAttribute("transform", `rotate(${rot.toFixed(1)} 50 50)`);
        el.setAttribute("stroke-width", near ? 1.5 : 1.1);
        el.setAttribute("opacity", near ? .85 : .35);
      }
    });
    raf = requestAnimationFrame(frame);
  };
  return {
    run(on) {
      if (on && !raf) { last = 0; raf = requestAnimationFrame(frame); }
      else if (!on && raf) { cancelAnimationFrame(raf); raf = 0; }
    } };
})();
function meterStop() {
  cancelAnimationFrame(METER.raf);
  METER.raf = 0; METER.taps = []; METER.v = 0;
  if (METER.ctx) METER.ctx.close().catch(() => {});
  METER.ctx = null;
  for (const el of WAVE_BARS) el.style.transform = "";
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
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.RTCPeerConnection) { toast("No microphone here: this page has no WebRTC"); return; }
  VOICE.starting = true; VOICE.sid = s.id; VOICE.call = null; VOICE.caps.clear(); VOICE.typing = false;
  try { METER.ctx = new AudioContext(); } catch { METER.ctx = null; }  // (made in the click: Safari lets it play)
  setVoicePhase("connecting");
  let pc = null;
  try {
    VOICE.mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    meterTap(VOICE.mic);
    renderCaps();
    pc = VOICE.pc = new RTCPeerConnection();
    pc.addEventListener("track", e => {
      voiceAudio.srcObject = e.streams && e.streams[0] ? e.streams[0] : new MediaStream([e.track]);
      meterTap(voiceAudio.srcObject);
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
    if (e && e.data && e.data.need === "openai-key") toast("A voice call needs an OpenAI API key: Settings → Voice (click)", () => settingsDialog("voice"));
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
  meterStop();
}
$("#callbtn").addEventListener("mousedown", e => e.preventDefault());  // (the input keeps the keyboard)
$("#callbtn").addEventListener("click", toggleCall);
addEventListener("pagehide", () => { if (VOICE.call && VOICE.phase !== "ended") api("POST", "/api/live/stop", { call: VOICE.call }, { keepalive: true }).catch(() => {}); });
