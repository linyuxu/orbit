"use strict";
/* ===================================================================== voice-core: a GPT Live call, what both pages share */
/* The call itself (builddoc/gpt-live.md), for the desktop's page (voice.js) and the phone's (m/m-voice.js), each of
   which draws it its own way. The microphone and the speaker go straight to OpenAI over WebRTC: the page makes the
   offer, the server creates the call with its key (the key never comes to a page) and hands back the answer. The
   server follows the call on its sideband and broadcasts {"t":"live"} events (the phase, captions, the requests it
   made, errors); the data channel gives a quicker copy of the captions. Here: that state (VOICE), the phase, the
   captions, the sound level (METER), the gyroscope's drawing, start and hang-up. The page fills in VOICE_UI (how it
   draws and speaks) and provides api(method, path, body, {keepalive}). Nothing here touches the page at load. */
const VOICE = { call: null, sid: null, phase: "idle", starting: false, pc: null, mic: null, dc: null, caps: new Map(), endTm: 0, spTm: 0, typing: false, muted: false,
                away: null };  // away: a call another page or device holds ({call, sid, phase}): one at a time, everywhere
const VOICE_WORD = { connecting: "Connecting…", listening: "Listening", speaking: "Speaking", standby: "Standby", ended: "Call ended" };
const VOICE_UI = {
  render() {},           // the phase, the captions or the microphone changed: draw the call
  bars: [],              // the elements the sound level drives (scaleY): the voice button's bars
  toast(msg) {},
  failed(e) {},          // the call did not start: e (e.data.need "openai-key", e.name "NotAllowedError", …)
  blocked() {},          // the voice cannot play yet (no gesture to allow it): ask for a tap, then voiceAudio.play()
  ended(why) {},         // the call ended over there (expiry, a filter, the connection): why, or "" for a plain hang-up
};
const voiceAudio = new Audio();
voiceAudio.autoplay = true;
voiceAudio.setAttribute("playsinline", "");  // (iOS: never its full-screen player)

function setVoicePhase(p) {
  const was = VOICE.phase;
  VOICE.phase = p;
  if (p === "ended" && was !== "ended" && was !== "idle" && VOICE.chimed) chime("end");
  if (p === "ended" && was !== "ended" && typeof loadUsage === "function") setTimeout(loadUsage, 4000);  // (the status bar's OpenAI cost)
  chimeIfReady();
  clearTimeout(VOICE.endTm);
  if (p === "ended") VOICE.endTm = setTimeout(() => { if (VOICE.phase === "ended") { VOICE.call = null; VOICE.caps.clear(); setVoicePhase("idle"); } }, 2500);
  VOICE_UI.render();
}
let capSeq = 0;
function addCaption(c) {
  if (!c || !c.id || VOICE.caps.has(c.id)) return;  // (the server's copy and the data channel's: one)
  VOICE.caps.set(c.id, { who: c.who, text: c.text || "", start: +c.start || 0, seq: ++capSeq });
  VOICE_UI.render();
}
/* The captions as turns (a run of one speaker's fragments, by their time in the call), for either page to draw. */
function voiceTurns() {
  const items = [...VOICE.caps.values()].sort((a, b) => a.start - b.start || a.seq - b.seq);
  const turns = [];
  for (const x of items) {
    const last = turns[turns.length - 1];
    if (last && last.who === x.who && x.who !== "sys") last.text += x.text; else turns.push({ who: x.who, text: x.text });
  }
  return turns;
}
const VOICE_WHY = { expired: "the voice session's time ran out", remote_hangup: "", close_requested: "", content: "a safety filter ended it",
  connection_lost: "the connection was lost" };
/* {"t": "live"} from the server: the call's state, captions, the requests it made, errors. */
function voiceEvent(m) {
  // (the new call's state may come before /api/live/start answers; the last call on this session ending is not it)
  if (!VOICE.call && VOICE.starting && m.kind === "state" && m.state && m.state.sid === VOICE.sid && m.state.phase !== "ended") VOICE.call = m.call;
  if (m.call && m.call !== VOICE.call) { if (m.kind === "state") voiceAwayState(m.call, m.state || {}); return; }
  if (!m.call) return;
  if (m.kind === "caption") addCaption(m.caption);
  else if (m.kind === "delegation") {
    const how = m.how === "edit" ? `changes request #${m.cid}` : m.how === "steer" ? `steers request #${m.cid}`
      : m.how === "withdraw" ? `withdrew request #${m.cid}` : m.how === "stop" ? `stopped request #${m.cid}`
      : m.how === "urgent" ? `→ request #${m.cid}, first` : `→ request #${m.cid}`;
    VOICE.caps.set("d:" + m.delegation, { who: "sys", text: how, start: +m.offset || 0, seq: ++capSeq });
    VOICE_UI.render();
  } else if (m.kind === "error") VOICE_UI.toast("Voice: " + m.message);
  else if (m.kind === "state") voiceState(m.state || {});
}
/* A call this page does not hold (the phone's, another window's): only one at a time, so this page's voice buttons
   wait, and ⌘⇧V (or the pill) hangs it up. */
function voiceAwayState(call, st) {
  if (st.phase === "ended" || !st.phase) { if (VOICE.away && VOICE.away.call === call) VOICE.away = null; }
  else VOICE.away = { call, sid: st.sid, phase: st.phase };
  VOICE_UI.render();
}
const voiceAway = () => !!(VOICE.away && VOICE.away.phase !== "ended");
function voiceState(st) {
  if (st.phase === "ended") {
    endMedia();
    if (VOICE.phase !== "ended") VOICE_UI.ended(st.error || VOICE_WHY[st.reason] || "");
    setVoicePhase("ended");
  } else if (VOICE.phase !== "ended" && st.phase) setVoicePhase(st.phase);
}
/* After the event stream was away (the phone in the background, a network change): what the server holds now. The
   call ended meanwhile (missed its last event), or goes on: its phase and the captions missed. */
async function voiceResync() {
  const call = VOICE.call;
  if (!call || VOICE.phase === "ended" || VOICE.phase === "idle") {  // (none here: is there one elsewhere?)
    let st;
    try { st = await api("GET", "/api/live"); } catch { return; }
    if (VOICE.call || VOICE.starting) return;
    const was = VOICE.away && VOICE.away.call;
    if (st.call && st.phase && st.phase !== "ended") voiceAwayState(st.call, st);
    else if (was) voiceAwayState(was, { phase: "ended" });
    return;
  }
  let st;
  try { st = await api("GET", "/api/live"); } catch { return; }
  if (VOICE.call !== call) return;
  if (st.call !== call) voiceState({ phase: "ended", reason: "" });
  else {
    for (const c of st.transcript || []) addCaption(c);
    voiceState(st);
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
  VOICE_UI.bars.forEach((el, k) => {
    const i = k % 5;  // (five bars to a button; a page may have more than one)
    const jig = 0.75 + 0.25 * Math.sin(tm * (7 + i * 1.7) + i * 2.1);  // so the bars do not move as one
    el.style.transform = `scaleY(${(0.42 + 0.58 * Math.min(1, METER.v * jig * (i === 2 ? 1 : 1.15))).toFixed(3)})`;
  });
  METER.raf = requestAnimationFrame(meterTick);
}
function meterStop() {
  cancelAnimationFrame(METER.raf);
  METER.raf = 0; METER.taps = []; METER.v = 0;
  if (METER.ctx) METER.ctx.close().catch(() => {});
  METER.ctx = null;
  for (const el of VOICE_UI.bars) el.style.transform = "";
}
/* The five bars of a voice button (a voice), as big as Send's arrow: short, tall, tallest, tall, short. */
const WAVE_H = [1.6, 3.4, 5.2, 3.4, 1.6];
const waveSvg = (cls = "wave") => `<svg class="${cls}" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.55" stroke-linecap="round">${
  WAVE_H.map((h, i) => `<line class="wb" style="--i:${i}" x1="${2.4 + i * 2.8}" x2="${2.4 + i * 2.8}" y1="${8 - h}" y2="${8 + h}"/>`).join("")}</svg>`;
/* The image of a call: a gyroscope. A cloud core (it swells with the sound) and two rings tumbling around it in 3D:
   each ring's near half passes in front of the core, its far half behind. Faster while the voice speaks, nearly
   still in standby. Its own frame loop runs while it is shown (run(true)). One per page, or more with a suffix k for their gradients' ids. */
function makeGyro(svg, k = "") {  // (k: a suffix for its ids, when a page draws two)
  svg.innerHTML = `<defs>
      <radialGradient id="vgN${k}" cx=".38" cy=".32" r=".75"><stop offset="0" stop-color="#dff0ff"/><stop offset=".35" stop-color="var(--voice-1)"/><stop offset=".75" stop-color="var(--voice-2)"/><stop offset="1" stop-color="#1a3596"/></radialGradient>
      <radialGradient id="vgG${k}"><stop offset="0" stop-color="var(--voice-1)" stop-opacity=".5"/><stop offset="1" stop-color="var(--voice-1)" stop-opacity="0"/></radialGradient>
      <filter id="vgB${k}" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="1.6"/></filter>
      <clipPath id="vgC${k}"><circle cx="50" cy="50" r="17"/></clipPath></defs>
    <g class="gback"></g>
    <g class="gcore"><circle cx="50" cy="50" r="31" fill="url(#vgG${k})"/><circle cx="50" cy="50" r="17" fill="url(#vgN${k})"/>
      <g clip-path="url(#vgC${k})" style="mix-blend-mode:screen">
        <ellipse class="gcl" cx="44" cy="46" rx="9" ry="6.5" fill="#fff" opacity=".75" filter="url(#vgB${k})"/>
        <ellipse class="gcl" cx="55" cy="55" rx="7.5" ry="7" fill="#a5f3fc" opacity=".75" filter="url(#vgB${k})"/>
        <ellipse class="gcl" cx="48" cy="58" rx="6.5" ry="5" fill="#c7d2fe" opacity=".75" filter="url(#vgB${k})"/></g></g>
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
  const draw = dt => {
    const p = VOICE.phase;
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
  };
  const frame = now => {
    draw(Math.min(.05, (now - (last || now)) / 1000)); last = now;
    raf = requestAnimationFrame(frame);
  };
  draw(0);  // (drawn at once: the first frame may come late, or never in a page in the background)
  return {
    run(on) {
      if (on && !raf) { last = 0; raf = requestAnimationFrame(frame); }
      else if (!on && raf) { cancelAnimationFrame(raf); raf = 0; }
    } };
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
/* The call's sounds (Settings → Voice → Sounds; made here, no files): a soft rising ding once it is connected and
   listening (the moment to speak), a falling one when it ends. The AudioContext is made in the click that starts
   the call (Safari lets it play then) and kept for the next call. */
const CHIME = { ctx: null };
function chimeReady() {
  try {
    if (!CHIME.ctx) CHIME.ctx = new AudioContext();
    if (CHIME.ctx.state === "suspended") CHIME.ctx.resume().catch(() => {});
  } catch { CHIME.ctx = null; }
}
function chime(kind) {
  const c = CHIME.ctx;
  if (!c || VOICE.chime === false) return;
  const notes = kind === "end" ? [[987.77, 0], [659.25, 0.11]] : [[659.25, 0], [987.77, 0.1]];  // (E5 → B5, or back)
  const t0 = c.currentTime + 0.02, peak = kind === "end" ? 0.07 : 0.1;
  for (const [f, at] of notes) {
    const o = c.createOscillator(), o2 = c.createOscillator(), g = c.createGain();
    o.type = "sine"; o.frequency.value = f;
    o2.type = "sine"; o2.frequency.value = f * 2;  // (a quiet octave: a bell, not a beep)
    const g2 = c.createGain(); g2.gain.value = 0.18;
    o.connect(g); o2.connect(g2).connect(g); g.connect(c.destination);
    const s = t0 + at;
    g.gain.setValueAtTime(0, s);
    g.gain.linearRampToValueAtTime(peak, s + 0.012);
    g.gain.exponentialRampToValueAtTime(0.0005, s + 0.42);
    o.start(s); o2.start(s); o.stop(s + 0.45); o2.stop(s + 0.45);
  }
}
/* Connected: the phase is past connecting (the server attached) and the WebRTC connection is up — whichever is last. */
function chimeIfReady() {
  const p = VOICE.phase;
  if (VOICE.chimed || !VOICE.pc || VOICE.pc.connectionState !== "connected" || !["listening", "speaking", "standby"].includes(p)) return;
  VOICE.chimed = true;
  chime("start");
}
/* Start a call with session sid. Call it from the tap or click itself: what must happen inside the user's gesture
   (Safari: the AudioContext, and the microphone's request) happens before the first await. */
async function startCall(sid) {
  if (!sid || VOICE.starting) return;
  if (voiceBusy()) { VOICE_UI.toast("A voice call is on already: one at a time (⌘⇧V hangs it up)"); return; }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.RTCPeerConnection) { VOICE_UI.failed({ name: "NoWebRTC" }); return; }
  VOICE.starting = true; VOICE.sid = sid; VOICE.call = null; VOICE.caps.clear(); VOICE.typing = false; VOICE.muted = false;
  VOICE.chimed = false; VOICE.chime = undefined;
  chimeReady();
  try { METER.ctx = new AudioContext(); if (METER.ctx.state === "suspended") METER.ctx.resume().catch(() => {}); } catch { METER.ctx = null; }  // (made in the click: Safari lets it play)
  voiceAudio.play().catch(() => {});  // (an empty play in the gesture: the element may play the voice later, iOS)
  setVoicePhase("connecting");
  let pc = null;
  try {
    VOICE.mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    meterTap(VOICE.mic);
    VOICE_UI.render();
    pc = VOICE.pc = new RTCPeerConnection();
    pc.addEventListener("track", e => {
      voiceAudio.srcObject = e.streams && e.streams[0] ? e.streams[0] : new MediaStream([e.track]);
      meterTap(voiceAudio.srcObject);
      voiceAudio.play().catch(() => VOICE_UI.blocked());
    });
    pc.addEventListener("connectionstatechange", () => {
      if (VOICE.pc === pc && pc.connectionState === "failed") { VOICE_UI.toast("The voice connection failed"); hangUp(); }
      if (VOICE.pc === pc) chimeIfReady();
    });
    for (const t of VOICE.mic.getAudioTracks()) pc.addTrack(t, VOICE.mic);
    const dc = VOICE.dc = pc.createDataChannel("oai-events");  // before the offer (it receives only what the call allows)
    dc.addEventListener("message", e => { try { voiceChannelEvent(JSON.parse(e.data)); } catch {} });
    await pc.setLocalDescription(await pc.createOffer());
    await iceGathered(pc);
    const r = await api("POST", "/api/live/start", { sid, sdp: pc.localDescription.sdp });
    if (VOICE.pc !== pc) { api("POST", "/api/live/stop", { call: r.call }).catch(() => {}); return; }  // hung up meanwhile
    VOICE.call = r.call; VOICE.chime = r.chime;
    await pc.setRemoteDescription({ type: "answer", sdp: r.sdp });
    if (VOICE.phase === "connecting") VOICE_UI.render();
  } catch (e) {
    const call = VOICE.call;
    endMedia();
    VOICE.call = null;
    if (call) api("POST", "/api/live/stop", { call }).catch(() => {});
    setVoicePhase("idle");
    if (e && e.status === 409 && e.data && e.data.busy) VOICE.away = { ...e.data.busy, phase: "listening" };  // (one began elsewhere)
    VOICE_UI.failed(e);
  } finally {
    VOICE.starting = false;
    VOICE_UI.render();
  }
}
/* Mute: the microphone's track sends silence (the call goes on; GPT Live hears nothing, and may rest in standby). */
const voiceLive = () => VOICE.phase !== "idle" && VOICE.phase !== "ended";
const voiceBusy = () => voiceLive() || voiceAway();  // (a call here, or anywhere else)
/* Hang up whichever call is on: this page's, or the one another page or device holds. */
function voiceStop() {
  if (voiceLive()) return hangUp();
  if (!voiceAway()) return;
  const call = VOICE.away.call;
  VOICE.away = { ...VOICE.away, phase: "ended" };
  VOICE_UI.render();
  api("POST", "/api/live/stop", { call }).catch(e => { if (e.status !== 404) VOICE_UI.toast("Hang-up: " + e.message); });
}
function voiceMute(on = !VOICE.muted) {
  if (!voiceLive()) return;
  VOICE.muted = !!on;
  if (VOICE.mic) for (const t of VOICE.mic.getAudioTracks()) t.enabled = !VOICE.muted;
  VOICE_UI.render();
}
/* Hang up: the microphone goes quiet at once; the server closes the call (session.close, then session.closed) and
   keeps its transcript as a note card; then the connection closes. */
async function hangUp() {
  const call = VOICE.call;
  if (VOICE.mic) for (const t of VOICE.mic.getTracks()) t.enabled = false;
  setVoicePhase("ended");
  try { if (call) await api("POST", "/api/live/stop", { call }); }
  catch (e) { if (e.status !== 404) VOICE_UI.toast("Hang-up: " + e.message); }
  finally { endMedia(); VOICE_UI.render(); }
}
function endMedia() {
  if (VOICE.mic) for (const t of VOICE.mic.getTracks()) t.stop();
  try { if (VOICE.dc) VOICE.dc.close(); } catch {}
  try { if (VOICE.pc) VOICE.pc.close(); } catch {}
  voiceAudio.srcObject = null;
  VOICE.mic = VOICE.dc = VOICE.pc = null;
  meterStop();
}
/* The page closing hangs up (the request outlives the page). */
addEventListener("pagehide", () => { if (VOICE.call && VOICE.phase !== "ended") api("POST", "/api/live/stop", { call: VOICE.call }, { keepalive: true }).catch(() => {}); });
