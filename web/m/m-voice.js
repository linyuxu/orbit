"use strict";
/* ===================================================================== a voice call (GPT Live, builddoc/gpt-live.md)
   The call itself is the desktop's (/src/voice-core.js: the microphone, WebRTC, the phase, the captions, the
   gyroscope); the phone draws it. The voice button (five blue bars) sits beside Send. In a call with the session on
   screen the call view takes the input's place: the gyroscope, the state word, and three big controls (type instead ·
   hang up · mute); captions run at the end of the conversation (only the call's session's). Anywhere else (another
   session, the list, another area) a bar says "in a call with X": a tap goes back to it, its red button hangs up.
   iOS: the microphone, the AudioContext and the voice's playback start in the tap itself (startCall, before its first
   await); coming back from the background resumes them (iOS may have ended the microphone: one tap brings it back),
   and the event stream's hello re-syncs the call with the server (voiceResync). The OpenAI key and the voice's
   settings stay on the Mac. */
const VC = { muted: false, micLost: false, blocked: false, mic: null };
$("#callbtn").innerHTML = waveSvg();
$("#callbar").innerHTML = `<button type="button" class="vcgo">${waveSvg()}<span class="vct"></span></button>` +
  `<button type="button" class="vchang" aria-label="Hang up"><span class="disc">${I("hangup", 18)}</span></button>`;
$("#vctype .disc").innerHTML = I("keyboard", 24);
$("#vcend .disc").innerHTML = I("hangup", 28);
VOICE_UI.bars = [...$$("#callbtn .wb"), ...$$("#callbar .wb")];
const GYRO = makeGyro($("#callview .vorb"));
VOICE_UI.render = renderVoice;
VOICE_UI.toast = msg => toast(msg, 3200);
VOICE_UI.ended = why => { if (why) toast(`Call ended: ${why}`, 4000); };
VOICE_UI.blocked = () => { VC.blocked = true; renderVoice(); };  // (the next tap anywhere plays it)
VOICE_UI.failed = e => toast(micWhy(e), 6000);
function micWhy(e) {
  if (e && e.data && e.data.need === "openai-key") return "A voice call needs an OpenAI API key: on the Mac, Settings → Voice";
  if (e && e.name === "NoWebRTC" || e && e.name === "SecurityError" || !window.isSecureContext)
    return window.isSecureContext ? "No microphone here: this browser has no WebRTC" : "The microphone needs Orbit over HTTPS (on the Mac: orbit https on), then open the phone's link again";
  if (e && e.name === "NotAllowedError") return "The microphone was refused: allow it in Safari's aA → Website Settings, or Settings → Apps → Safari → Microphone";
  if (e && e.name === "NotFoundError") return "No microphone found";
  if (e && (e.name === "NotReadableError" || e.name === "AbortError")) return "The microphone is busy (a phone call, or another app has it)";
  if (e instanceof NotPaired) return "Not paired with Orbit";
  return "Voice call failed: " + (e && e.message || e);
}

/* ===================================================================== drawing */
const callName = () => { const s = S.sessions.get(VOICE.sid); return s ? sessionTitle(s) : "the session"; };
function renderVoice() {
  const on = VOICE.phase !== "idle", live = on && VOICE.phase !== "ended", here = on && VOICE.sid === S.current;
  watchMic();
  /* The voice button: this session's call (its bars follow the sound; a tap shows the call view), else a new call. */
  const b = $("#callbtn");
  b.className = `cbtn${here ? " call-" + VOICE.phase : ""}`;
  b.disabled = VOICE.starting && !here;
  b.setAttribute("aria-label", here && live ? "Show the call" : "Voice call with this session");
  b.setAttribute("aria-pressed", String(here && live));
  /* The call view: in the input's place, unless typing instead. */
  const view = here && !VOICE.typing, cv = $("#callview");
  if (view && cv.hidden && document.activeElement === ta) ta.blur();  // (the keyboard goes down for the call)
  cv.hidden = !view;
  cv.className = on ? "call-" + VOICE.phase : "";
  $("#foot").classList.toggle("vcall", view);
  document.body.classList.toggle("vcall", view);
  GYRO.run(view && !document.hidden);
  const word = on ? VOICE_WORD[VOICE.phase] : "";
  $("#callview .vcst").innerHTML = !view ? "" : !live ? esc(word)
    : VC.micLost ? `<span>Microphone off</span><span class="sub"> · iOS stopped it; tap Mic</span>`
    : VC.blocked ? `<span>${esc(word)}</span><span class="sub"> · tap to hear the voice</span>`
    : `<span>${esc(word)}</span>${VC.muted ? `<span class="sub"> · muted</span>` : VOICE.phase === "standby" ? `<span class="sub"> · speak to wake it</span>` : ""}`;
  $("#vcend").disabled = !live;
  const m = $("#vcmute"), off = VC.muted || VC.micLost;
  m.disabled = !live || !VOICE.mic && !VC.micLost;
  m.classList.toggle("on", VC.muted);
  m.classList.toggle("lost", VC.micLost);
  m.querySelector(".disc").innerHTML = I(off ? "micoff" : "mic", 24);
  m.querySelector(".lbl").textContent = VC.micLost ? "Mic" : VC.muted ? "Unmute" : "Mute";
  m.setAttribute("aria-label", VC.micLost ? "Turn the microphone back on" : VC.muted ? "Unmute the microphone" : "Mute the microphone");
  m.setAttribute("aria-pressed", String(VC.muted));
  /* The bar: a call the call view does not show. In the conversation it sits on the input; elsewhere above the tab bar. */
  const bar = $("#callbar"), showBar = on && !view;
  bar.hidden = !showBar;
  const inConv = !!S.current;
  if (inConv && bar.parentNode !== $("#foot")) $("#foot").insertBefore(bar, $("#callview"));
  else if (!inConv && bar.parentNode !== $("#app")) $("#app").appendChild(bar);
  document.body.classList.toggle("callbar", showBar && !inConv);
  bar.className = on ? "call-" + VOICE.phase : "";
  bar.querySelector(".vct").innerHTML = !showBar ? "" : here
    ? `<b>${esc(word)}</b><span class="vcs">${live ? "Show the call" : ""}</span>`
    : `<b>${esc(word)}</b><span class="vcs">${live ? "with " : ""}${esc(callName())}</span>`;
  bar.querySelector(".vcgo").setAttribute("aria-label", here ? "Show the call" : `Back to the call with ${callName()}`);
  bar.querySelector(".vchang").disabled = !live;
  renderCaps();
}
/* Captions while the call is on, at the end of the call's conversation: the user's words as a request bubble, the
   voice's as plain text, the requests it made in a small line between. Folded (a tap on the head; kept): one line,
   the latest words, so the agent's answers have the room. */
function renderCaps() {
  const box = $("#livecaps");
  const show = VOICE.phase !== "idle" && VOICE.sid === S.current && VOICE.caps.size > 0;
  box.hidden = !show;
  if (!show) { box.innerHTML = ""; return; }
  const turns = voiceTurns(), folded = !!LS.get("capsFolded", false);
  const said = turns.filter(t => t.who !== "sys"), lastT = said[said.length - 1];
  box.classList.toggle("folded", folded);
  box.innerHTML = `<button type="button" class="lchead" aria-expanded="${!folded}">${I("chev", 14)}<span class="lcdot"></span><span class="lctitle">Live captions</span>` +
    (folded && lastT ? `<span class="lclast"><b>${lastT.who === "user" ? "You" : "Voice"}</b> ${esc(lastT.text.trim())}</span>` : "") + `</button>` +
    (folded ? "" : turns.slice(-14).map(t => t.who === "sys"
      ? `<div class="lc sys">${esc(t.text)}</div>`
      : `<div class="lc ${t.who}"><div class="lcb">${esc(t.text.trim())}</div></div>`).join(""));
  stick();
}
$("#livecaps").addEventListener("click", e => {
  if (!e.target.closest(".lchead")) return;
  LS.set("capsFolded", !LS.get("capsFolded", false));
  renderCaps();
});

/* ===================================================================== taps */
function callHere(sid) {  // (inside the tap: startCall asks for the microphone before its first await)
  VC.muted = VC.micLost = VC.blocked = false;
  startCall(sid);
}
$("#callbtn").addEventListener("pointerdown", e => { if (document.activeElement === ta) e.preventDefault(); });  // (the keyboard stays until the call shows)
$("#callbtn").addEventListener("click", () => {
  const sid = S.current; if (!sid || VOICE.starting) return;
  const live = VOICE.phase !== "idle" && VOICE.phase !== "ended";
  if (live && VOICE.sid === sid) { VOICE.typing = false; renderVoice(); return; }
  if (live) {
    const other = callName(), mine = S.sessions.get(sid);
    actionSheet(`In a call with ${other}`, [
      { label: `Call ${mine ? sessionTitle(mine) : "this session"} instead`, sub: `Hangs up the call with ${other}`, icon: "mic", run: () => {
        if (VOICE.call) api("POST", "/api/live/stop", { call: VOICE.call }).catch(() => {});
        endMedia(); VOICE.call = null;
        callHere(sid);
      } },
      { label: `Back to the call with ${other}`, icon: "back", run: () => { VOICE.typing = false; openSession(VOICE.sid); } },
    ]);
    return;
  }
  callHere(sid);
});
$("#vctype").addEventListener("click", () => { VOICE.typing = true; renderVoice(); inputEd.focus(); });
$("#vcend").addEventListener("click", () => hangUp());
$("#vcmute").addEventListener("click", () => {
  if (VC.micLost) { micAgain(); return; }
  if (!VOICE.mic) return;
  VC.muted = !VC.muted;
  for (const t of VOICE.mic.getAudioTracks()) t.enabled = !VC.muted;
  renderVoice();
});
$("#callbar .vcgo").addEventListener("click", () => {
  VOICE.typing = false;
  if (VOICE.sid !== S.current && S.sessions.has(VOICE.sid)) openSession(VOICE.sid); else renderVoice();
});
$("#callbar .vchang").addEventListener("click", () => hangUp());
/* The voice could not play (no gesture allowed it yet): the next tap anywhere does. */
document.addEventListener("pointerdown", () => {
  if (!VC.blocked) return;
  VC.blocked = false;
  voiceAudio.play().then(renderVoice, () => { VC.blocked = true; renderVoice(); });
}, true);

/* ===================================================================== the phone's realities */
/* iOS may end the microphone (the app in the background, a phone call, Siri): the call goes on, the state says so, and
   Mic takes it back (a new track on the same connection: no new call). */
function watchMic() {
  if (VOICE.mic === VC.mic) return;
  VC.mic = VOICE.mic;
  if (!VOICE.mic) { VC.micLost = false; return; }
  const stream = VOICE.mic;
  for (const t of stream.getAudioTracks()) t.addEventListener("ended", () => {  // (not fired by our own stop())
    if (VOICE.mic === stream && VOICE.phase !== "idle" && VOICE.phase !== "ended") { VC.micLost = true; renderVoice(); }
  });
}
async function micAgain() {
  if (METER.ctx && METER.ctx.state !== "running") METER.ctx.resume().catch(() => {});
  try {
    const m = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    const pc = VOICE.pc, snd = pc && pc.getSenders().find(x => !x.track || x.track.kind === "audio");
    if (!snd || VOICE.phase === "idle" || VOICE.phase === "ended") { for (const t of m.getTracks()) t.stop(); return; }
    await snd.replaceTrack(m.getAudioTracks()[0]);
    if (VOICE.mic) for (const t of VOICE.mic.getTracks()) t.stop();
    VOICE.mic = m;
    meterTap(m);
    VC.micLost = VC.muted = false;
    renderVoice();
  } catch (e) { toast(micWhy(e), 6000); }
}
/* Back to the front: the sound may have been suspended with the page (the AudioContext "interrupted", the voice
   paused); the microphone may be gone. The call's state itself comes with the event stream's hello (m.js). */
document.addEventListener("visibilitychange", () => {
  if (VOICE.phase === "idle") return;
  if (!document.hidden) {
    if (METER.ctx && METER.ctx.state !== "running") METER.ctx.resume().catch(() => {});
    if (voiceAudio.srcObject && voiceAudio.paused) voiceAudio.play().catch(() => VOICE_UI.blocked());
    if (VOICE.mic && VOICE.phase !== "ended" && VOICE.mic.getAudioTracks().some(t => t.readyState === "ended")) VC.micLost = true;
  }
  renderVoice();
});
renderVoice();
