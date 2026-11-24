"use strict";
/* ===================================================================== voice: a GPT Live call (builddoc/gpt-live.md) */
/* A call with the session in front (⌘⇧V or the voice button in the composer row): the call itself is voice-core.js
   (shared with the phone); here the desktop draws it. The button shows the phase (connecting · listening · speaking ·
   standby · ended), a red dot while the microphone is open; captions run at the end of the conversation while the
   call is on, and become a note card when it ends. */
/* The button: five blue bars (a voice), as big as Send's arrow; in a call the bars follow the sound (the microphone
   while you talk, the voice while it speaks), and a hover shows the stop. */
$("#callbtn").innerHTML = waveSvg() + `<svg class="hang" width="16" height="16" viewBox="0 0 16 16"><rect x="4.5" y="4.5" width="7" height="7" rx="1.5" fill="currentColor"/></svg>`;
VOICE_UI.bars = [...$("#callbtn").querySelectorAll(".wb")];
VOICE_UI.render = renderCaps;
const GYRO = makeGyro($("#voiceorb .vorb"));  // (the orb in a call: voice-core.js draws it)
VOICE_UI.toast = msg => toast(msg);
VOICE_UI.blocked = () => toast("Click in the page to hear the voice", () => voiceAudio.play().catch(() => {}));
VOICE_UI.ended = why => { if (why) toast(`Call ended: ${why}`); };
VOICE_UI.failed = e => {
  if (e && e.name === "NoWebRTC") toast("No microphone here: this page has no WebRTC");
  else if (e && e.data && e.data.need === "openai-key") toast("A voice call needs an OpenAI API key: Settings → Voice (click)", () => settingsDialog("voice"));
  else if (e && (e.name === "NotAllowedError" || e.name === "SecurityError")) toast("Orbit may not use the microphone: allow it in System Settings → Privacy & Security → Microphone");
  else if (e && e.name === "NotFoundError") toast("No microphone found");
  else toast("Voice call failed: " + (e && e.message || e));
};

function toggleCall() { if (VOICE.phase === "idle" || VOICE.phase === "ended") { const s = cur(); if (s) startCall(s.id); } else hangUp(); }
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
  const turns = voiceTurns();
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
$("#callbtn").addEventListener("mousedown", e => e.preventDefault());  // (the input keeps the keyboard)
$("#callbtn").addEventListener("click", toggleCall);
