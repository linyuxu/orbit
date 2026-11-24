"use strict";
/* ===================================================================== Settings (⌘,) */
/* A sidebar of sections and a pane of rows (label · what it does · its control), like ChatGPT's and Codex's settings.
   Every change applies at once; × or Esc closes. Appearance is per device (localStorage; side tabs follow it);
   General's "Open files in" and Calendar, and Machines, are the server's. */

/* ---------- appearance prefs (per device) */
/* The defaults are the built-in fonts, so Orbit looks the same on every machine. PREFS_V: a change of the defaults
   that everyone should get drops the Appearance prefs saved before it, once. */
const PREFS_V = 2;
const PREFS_DEFAULT = { v: PREFS_V, weight: "light", readSize: 13.5, noteFont: "Inconsolata", noteSize: 15,
                        uiFont: "Inter", uiSize: 12, readFont: "", codeFont: "Inconsolata", codeSize: 14 };  // (Inconsolata 14 ≈ Consolas 13)
/* A font missing on this machine (SF Pro and SF Mono are Apple's, Consolas Microsoft's: neither may be shipped) falls
   back to the one Orbit ships (base.css: "Orbit Sans" is Inter, "Orbit Mono" Inconsolata, after Consolas); a system
   font's Chinese is the system's (PingFang, YaHei, Noto CJK), else Orbit's ("Orbit CJK", vendor/fonts/orbit-cjk.css). */
const CJK_STACK = '"PingFang SC","Hiragino Sans GB","Microsoft YaHei","Noto Sans CJK SC","Noto Sans SC","Orbit CJK"';
/* Built in: shipped with Orbit, Chinese included (Noto Sans SC), so they look the same everywhere; listed first in every
   font list. */
const BUILTIN_FONTS = { "Inter": { stack: `"Orbit Sans","Orbit CJK",sans-serif`, mono: false },
                        "Inconsolata": { stack: `"Orbit Mono","Orbit CJK",monospace`, mono: true } };
const FONT_PRESETS = {
  "Inter": BUILTIN_FONTS.Inter.stack,
  "SF Pro": `-apple-system,BlinkMacSystemFont,"SF Pro Text","Orbit Sans",${CJK_STACK},sans-serif`,
  "New York": 'ui-serif,"New York","Songti SC",Georgia,serif',
  "Avenir Next": `"Avenir Next","Orbit Sans",${CJK_STACK},sans-serif`,
  "Helvetica Neue": `"Helvetica Neue","Orbit Sans",${CJK_STACK},sans-serif`,
};
const MONO_PRESETS = {
  "Inconsolata": BUILTIN_FONTS.Inconsolata.stack,
  "SF Mono": `"SF Mono",ui-monospace,Menlo,"Orbit Mono",${CJK_STACK},monospace`,
  "Menlo": `Menlo,"Orbit Mono",${CJK_STACK},monospace`,
  "Monaco": `Monaco,"Orbit Mono",${CJK_STACK},monospace`,
  "Consolas": `Consolas,"Orbit Mono",${CJK_STACK},monospace`,
};
const FONT_LABELS = { "New York": "New York (serif)", "Inter": "Inter · built in", "Inconsolata": "Inconsolata · built in" };
const quoteFont = name => `"${String(name).replace(/["\\]/g, "")}"`;
const fontStack = name => FONT_PRESETS[name] || MONO_PRESETS[name] || `${quoteFont(name)},"Orbit Sans",${CJK_STACK},sans-serif`;
const monoStack = name => MONO_PRESETS[name] || `${quoteFont(name)},"Orbit Mono",${CJK_STACK},monospace`;
const clampSize = (v, lo = 8, hi = 40) => Math.round(Math.max(lo, Math.min(hi, +v || 0)) * 2) / 2;
const UI_SIZE = [10, 15];  // the interface size's range: menus, rows and bars are laid out for about these
function readPrefs() {
  let p = LS.get("prefs", null);
  if (p && typeof p === "object" && p.v !== PREFS_V) { p = null; LS.set("prefs", { v: PREFS_V }); }  // (saved before the defaults changed)
  return { ...PREFS_DEFAULT, ...(p && typeof p === "object" ? p : {}), v: PREFS_V };
}
function applyPrefs(p = readPrefs(), tell = true) {
  document.body.classList.toggle("light-text", p.weight === "light");
  const r = document.documentElement.style;
  r.setProperty("--font", fontStack(p.uiFont));
  r.setProperty("--read-font", p.readFont ? fontStack(p.readFont) : "var(--font)");
  r.setProperty("--mono", monoStack(p.codeFont));
  r.setProperty("--code-size", clampSize(p.codeSize) + "px");
  r.setProperty("--ui-k", String(clampSize(p.uiSize, ...UI_SIZE) / 12));  // every interface font size is written for 12
  r.setProperty("--read-size", clampSize(p.readSize) + "px");
  r.setProperty("--note-font", fontStack(p.noteFont));
  r.setProperty("--note-size", clampSize(p.noteSize) + "px");
  r.setProperty("--col", p.readWidth === "full" ? "min(100%, 2000px)" : "min(100%, 900px)");  // U8: a reading column (or the whole pane)
  if (typeof SH !== "undefined" && SH.term) {  // the ⌘J terminal: the code font and size
    SH.term.options.fontFamily = monoStack(p.codeFont);
    SH.term.options.fontSize = clampSize(p.codeSize);
    fitShell();
  }
  if (tell && !EMBED) eachFrame(w => w.postMessage({ orbit: "prefs", p }, location.origin));
}
function setPref(k, v) { const p = { ...readPrefs(), [k]: v }; LS.set("prefs", p); applyPrefs(p); }
applyPrefs(readPrefs(), false);
let FONTS = null;

/* ---------- controls */
/* A segmented control: one button per option, ←→ move while it has the keyboard. */
function segmented(wrap, { options, value, onChange }) {
  const el = document.createElement("div"); el.className = "segc"; el.setAttribute("role", "radiogroup");
  const draw = () => {
    el.innerHTML = options.map(o => `<button type="button" role="radio" aria-checked="${o.value === value}" class="${o.value === value ? "on" : ""}" data-v="${esc(o.value)}" tabindex="${o.value === value ? 0 : -1}"${o.title ? ` title="${esc(o.title)}"` : ""}>${esc(o.label)}</button>`).join("");
  };
  const set = (v, focus) => {
    if (v === value) return;
    value = v; draw(); if (onChange) onChange(v);
    if (focus) el.querySelector("button.on").focus();
  };
  el.addEventListener("click", e => { const b = e.target.closest("[data-v]"); if (b) set(b.dataset.v, true); });
  el.addEventListener("keydown", e => {
    const d = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
    if (!d) return;
    e.preventDefault(); e.stopPropagation();
    const i = options.findIndex(o => o.value === value);
    set(options[Math.max(0, Math.min(options.length - 1, i + d))].value, true);
  });
  draw();
  wrap.appendChild(el);
  return { get: () => value, set: v => { value = v; draw(); }, get focusEl() { return el.querySelector("button.on"); }, isOpen: () => false };
}
/* An on/off switch (Space or ⏎ flips it). */
function toggleSwitch(wrap, { on, onChange, label = "" }) {
  const b = document.createElement("button");
  b.type = "button"; b.className = "switch"; b.setAttribute("role", "switch");
  if (label) b.setAttribute("aria-label", label);
  const draw = () => { b.classList.toggle("on", on); b.setAttribute("aria-checked", String(on)); };
  b.addEventListener("click", async () => {
    const next = !on;
    on = next; draw();
    try { if (onChange) await onChange(next); } catch (e) { on = !next; draw(); toast(e.message); }
  });
  draw(); wrap.appendChild(b);
  return { get: () => on, set: v => { on = v; draw(); }, el: b };
}
/* A size: a slider and a number field (8–40 px, step 0.5; the slider over 10–28), or within [lo, hi]. */
function sizeControl(wrap, { value, dflt, onChange, lo = 8, hi = 40 }) {
  const rlo = Math.max(lo, 10), rhi = Math.min(hi, 28);
  const box = document.createElement("div"); box.className = "size";
  box.title = `Default ${dflt} px; type ${lo}–${hi}, ↑↓ step 0.5`;
  box.innerHTML = `<input type="range" min="${rlo}" max="${rhi}" step="0.5"><input type="number" min="${lo}" max="${hi}" step="0.5" class="fin"><span class="px">px</span>`;
  const [rng, num] = box.querySelectorAll("input");
  const set = (v, from) => { v = clampSize(v, lo, hi); if (from !== rng) rng.value = Math.max(rlo, Math.min(rhi, v)); if (from !== num) num.value = v; onChange(v); };
  rng.value = Math.max(rlo, Math.min(rhi, value)); num.value = value;
  rng.addEventListener("input", () => set(rng.value, rng));
  num.addEventListener("input", () => { if (num.value !== "" && +num.value >= lo && +num.value <= hi) set(num.value, num); });
  num.addEventListener("change", () => set(num.value));
  wrap.appendChild(box);
}
/* A row: label and what it does on the left, the control on the right (stack: the control below, full width). */
function setRow(pane, label, desc = "", { stack = false } = {}) {
  const row = document.createElement("div"); row.className = "set-row" + (stack ? " stack" : "");
  row.innerHTML = `<div class="set-text"><div class="set-label">${esc(label)}</div>${desc ? `<div class="set-desc">${desc}</div>` : ""}</div><div class="set-ctl"></div>`;
  pane.appendChild(row);
  return row.querySelector(".set-ctl");
}
function setHeading(pane, text) {
  const h = document.createElement("div"); h.className = "set-sub"; h.textContent = text; pane.appendChild(h);
}

/* ---------- sections */
const SETTINGS_SECTIONS = [
  { id: "general", label: "General", icon: "gear", render: settingsGeneral },
  { id: "appearance", label: "Appearance", icon: "type", render: settingsAppearance },
  { id: "voice", label: "Voice", icon: "mic", render: settingsVoice },
  { id: "machines", label: "Machines", icon: "server", render: (pane, keys) => machinesPane(pane, keys) },
  { id: "plugins", label: "Plugins", icon: "plug", render: settingsPlugins },
  { id: "about", label: "About", icon: "info", render: settingsAbout },
];
const OPEN_IN_OPTIONS = [
  { value: "jetbrains", label: "JetBrains", title: "IntelliJ IDEA for Java / Kotlin / Scala / Groovy / Gradle, PyCharm otherwise, at the line" },
  { value: "vscode", label: "VS Code", title: "code -g file:line" },
  { value: "side", label: "Side tab", title: "Orbit's side tab; a file of another repo opens with that repo as its folder" },
  { value: "default", label: "Default app", title: "The file's default app ($ORBIT_EDITOR if set)" },
];
/* One of Orbit's own API keys (GET/POST /api/keys): write-only, like a plugin's secrets. Shows whether it is set and
   its last four characters; Save, Check (asks the provider whether it works) and Remove. This machine only. */
async function apiKeyRow(pane, name, desc) {
  const ctl = setRow(pane, "OpenAI API key", desc, { stack: true });  // (its status, field and three buttons: a line of their own)
  ctl.classList.add("keyrow");
  const draw = async () => {
    let k;
    try { k = (await api("GET", "/api/keys")).keys[name]; }
    catch (e) { ctl.innerHTML = `<span class="set-desc">${esc(e.message)}</span>`; return; }
    ctl.innerHTML = `<span class="keyst${k.set ? " on" : ""}">${k.set ? esc(k.hint) : "Not set"}</span>` +
      `<input type="password" class="fin keyin" placeholder="${k.set ? "Replace…" : "sk-…"}" autocomplete="off" spellcheck="false">` +
      `<button type="button" class="btn sm" data-a="save">Save</button>` +
      (k.set ? `<button type="button" class="btn sm" data-a="check">Check</button><button type="button" class="btn sm" data-a="clear">Remove</button>` : "");
  };
  const put = async value => {
    try { await api("POST", "/api/keys", { name, value }); toast(value ? "API key saved" : "API key removed"); draw(); }
    catch (e) { toast(e.message); }
  };
  ctl.addEventListener("click", async e => {
    const a = e.target.closest("[data-a]"); if (!a) return;
    const inp = ctl.querySelector(".keyin");
    if (a.dataset.a === "save") { if (inp.value.trim()) put(inp.value.trim()); else inp.focus(); }
    else if (a.dataset.a === "clear") {  // asked in place (a dialog would close Settings): a second click within 4 s removes it
      if (a.dataset.sure) { put(null); return; }
      a.dataset.sure = "1"; a.textContent = "Remove?"; a.classList.add("danger");
      setTimeout(() => { if (a.isConnected) { delete a.dataset.sure; a.textContent = "Remove"; a.classList.remove("danger"); } }, 4000);
    } else if (a.dataset.a === "check") {
      a.disabled = true; a.textContent = "Checking…";
      try { const r = await api("POST", "/api/keys/check", { name }); toast(r.ok ? "The OpenAI API key works" : r.error); }
      catch (x) { toast(x.message); }
      a.disabled = false; a.textContent = "Check";
    }
  });
  ctl.addEventListener("keydown", e => {
    const inp = e.target.closest(".keyin");
    if (inp && e.key === "Enter" && inp.value.trim()) { e.preventDefault(); e.stopPropagation(); put(inp.value.trim()); }
  });
  draw();
}
function settingsGeneral(pane, keys) {
  const dd = dropdown(setRow(pane, "Open files in", "Where a click on a file name in an answer, ↗ and “Open in …” open a file of this machine (with Default app, a click keeps it in Orbit's side tab; ⌘-click: an Orbit tab). Another machine's files always open in Orbit."), {
    value: (S.settings && S.settings.open_in) || "default", options: OPEN_IN_OPTIONS,
    onChange: v => api("POST", "/api/settings", { open_in: v }).then(st => { S.settings = st; renderFileHead(); }).catch(e => toast(e.message)) });
  keys.push(dd);
  toggleSwitch(setRow(pane, "Calendar", "Read macOS Calendar (read-only): today's meetings in Misc and the status bar, ⌘⇧C to join."), {
    on: !!(S.settings && S.settings.calendar), label: "Calendar", onChange: on => setCalendar(on) });
  const kb = setRow(pane, "Keyboard shortcuts", "Every key Orbit uses, searchable (⌘/).");
  kb.innerHTML = `<button type="button" class="btn sm" data-act="keys">Show all</button>`;
  kb.querySelector("button").addEventListener("click", () => { closeModal(); deskKey("M:Slash"); });
}
/* Voice (GPT Live): the key, then how the voice sounds and behaves. Each is a desk setting (live_*), read when a call
   starts: a change applies to the next call. */
const VOICE_LANG_LABEL = { auto: "The language I speak", zh: "中文 (Mandarin)", en: "English", ja: "日本語", ko: "한국어",
  es: "Español", fr: "Français", de: "Deutsch", pt: "Português" };
/* A voice's preview (GET /api/live/preview: made by the voice itself the first time, a few seconds, then kept):
   played on hover after a short pause; a newer hover or the list closing stops it. */
const VPREV = { audio: new Audio(), seq: 0, tm: 0, status: null, urls: new Map() };
function voicePreview(voice, delay = 0, keep = false) {
  clearTimeout(VPREV.tm);
  const my = ++VPREV.seq;
  const say = t => { if (VPREV.status && VPREV.status.isConnected) VPREV.status.textContent = t; };
  if (!voice) { if (!VPREV.keep) VPREV.audio.pause(); say(""); return; }
  VPREV.keep = keep;  // (▶: the list closing does not stop it)
  VPREV.audio.pause();
  VPREV.tm = setTimeout(async () => {
    let url = VPREV.urls.get(voice);
    if (!url) {
      say(`Making a preview of ${voice}…`);
      try {
        const r = await fetch(`/api/live/preview?voice=${encodeURIComponent(voice)}`);
        if (!r.ok) { const e = await r.json().catch(() => ({})); if (my === VPREV.seq) say(e.error || `no preview (${r.status})`); return; }
        url = URL.createObjectURL(await r.blob());
        VPREV.urls.set(voice, url);
      } catch (e) { if (my === VPREV.seq) say(String(e.message || e)); return; }
    }
    if (my !== VPREV.seq) return;  // (moved on meanwhile)
    say("");
    VPREV.audio.src = url;
    VPREV.audio.play().catch(() => say("Click ▶ to hear it"));
  }, delay);
}
/* What the calls cost (GET /api/live/usage): this month's, from each call's usage as GPT Live reported it; and, when
   the user says what is left on their OpenAI account (an API key cannot read it), the time that buys. */
const vdur = s => { s = Math.round(s); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60); return h ? `${h} h ${m} min` : m ? `${m} min${s % 60 && m < 10 ? ` ${s % 60} s` : ""}` : `${s} s`; };
const vusd = v => "$" + v.toFixed(2);
async function voiceUsage(pane) {
  setHeading(pane, "Usage");
  const mrow = setRow(pane, "This month", "What Orbit's calls cost: talking $0.05 a minute, standby $0.02, by the second.");
  const brow = setRow(pane, "Balance left", "What is left on your OpenAI account: an API key cannot read it, so copy it from platform.openai.com → Settings → Billing. Orbit then takes off what its calls cost.");
  const tip = document.createElement("div"); tip.className = "set-desc vleft"; brow.closest(".set-row").querySelector(".set-text").appendChild(tip);
  brow.innerHTML = `<span class="vusd">$</span><input type="number" class="fin vbudget" min="0" max="100000" step="0.01" placeholder="e.g. 20"><button type="button" class="btn sm">Save</button>`;
  const inp = brow.querySelector("input"), save = brow.querySelector("button");
  const draw = async () => {
    let u;
    try { u = await api("GET", "/api/live/usage"); } catch (e) { mrow.textContent = e.message; return; }
    const m = u.month;
    mrow.innerHTML = `<span class="vmon">${m.calls ? `${m.estimated ? "≈ " : ""}<b>${vusd(m.cost)}</b> · ${m.calls} call${m.calls > 1 ? "s" : ""} · ${vdur(m.active_s)} talking${m.standby_s ? ` · ${vdur(m.standby_s)} standby` : ""}` : "No calls yet"}</span>`;
    const b = u.budget;
    inp.placeholder = b ? `${b.given} given` : "e.g. 20";
    tip.innerHTML = b ? `<b>≈ ${vdur(b.active_min * 60)} of talking left</b> (or ${vdur(b.standby_min * 60)} on standby): ${vusd(b.left)} of the ${vusd(b.given)} you gave on ${new Date(b.at * 1000).toLocaleDateString()}, after ${vusd(b.spent)} of calls.` : "";
  };
  const put = async () => {
    const v = parseFloat(inp.value);
    if (!(v >= 0)) { inp.focus(); return; }
    try { S.settings = await api("POST", "/api/settings", { live_budget: v }); inp.value = ""; toast(v ? "Balance saved: Orbit counts down from it" : "Balance cleared"); draw(); }
    catch (e) { toast(e.message); }
  };
  save.addEventListener("click", put);
  inp.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); put(); } });
  draw();
}
async function settingsVoice(pane, keys) {
  const note = document.createElement("div"); note.className = "set-desc set-lead";
  note.textContent = "Talk with a session (⌘⇧V or the blue bars by Send): what you ask becomes a request, and the answer is spoken back. A change here applies to the next call.";
  pane.appendChild(note);
  apiKeyRow(pane, "openai", "Kept on this machine (a 0600 file in Orbit's state folder), never shown again, sent only to OpenAI.");
  voiceUsage(pane);
  let o;
  try { o = await api("GET", "/api/live/options"); }
  catch (e) { const d = document.createElement("div"); d.className = "set-desc"; d.textContent = e.message; pane.appendChild(d); return; }
  if (!pane.isConnected) return;
  const st = () => S.settings || {};
  const put = (k, v) => api("POST", "/api/settings", { [k]: v }).then(x => { S.settings = x; }).catch(e => toast(e.message));
  const cap = w => w[0].toUpperCase() + w.slice(1);
  setHeading(pane, "Voice");
  const vrow = setRow(pane, "Voice", "How it sounds: hover a voice in the list (or ↑↓) to hear it; ▶ plays the one chosen. The first ten speak many languages; the others have the language and accent shown. A preview is made once, then kept.");
  const vdd = dropdown(vrow, {
    value: st().live_voice || "marin", onChange: v => put("live_voice", v),
    onSel: v => voicePreview(v, 350), onClose: () => voicePreview(null),
    options: o.voices.map(v => ({ value: v.name, label: cap(v.name) + (v.about ? ` · ${v.about}` : "") })) });
  keys.push(vdd);
  const pv = document.createElement("button");
  pv.type = "button"; pv.className = "ibtn sm vplay"; pv.title = "Hear this voice"; pv.innerHTML = I("play", 13);
  pv.addEventListener("click", () => voicePreview(vdd.get(), 0, true));
  vrow.prepend(pv);
  const pst = document.createElement("span"); pst.className = "vpst set-desc"; vrow.prepend(pst);
  VPREV.status = pst;
  keys.push(dropdown(setRow(pane, "Language", "What it speaks. “The language I speak” follows you."), {
    value: st().live_language || "auto", onChange: v => put("live_language", v),
    options: Object.keys(o.languages).map(k => ({ value: k, label: VOICE_LANG_LABEL[k] || o.languages[k] })) }));
  keys.push(segmented(setRow(pane, "Results", "How much of an agent's answer it says. The full answer is always in the card."), {
    value: st().live_detail || "brief", onChange: v => put("live_detail", v),
    options: [{ value: "brief", label: "Brief", title: "The takeaway in one to three sentences" },
              { value: "detailed", label: "Detailed", title: "A few sentences with the key details" }] }));
  toggleSwitch(setRow(pane, "Say when a task starts", "A short “on it” when it hands your request to the agent. Off: quiet until there is something to say."), {
    on: !!st().live_ack, label: "Say when a task starts", onChange: on => put("live_ack", on) });
  setHeading(pane, "Listening and cost");
  keys.push(dropdown(setRow(pane, "Standby after", "With nothing said either way for this long, it rests: $0.02 a minute instead of $0.05. Speaking wakes it (the first reply after is a little slower)."), {
    value: String(st().live_idle_s ?? 30), onChange: v => put("live_idle_s", +v),
    options: [10, 20, 30, 45, 60].map(n => ({ value: String(n), label: `${n} seconds${n === 30 ? " (default)" : ""}` }))
      .concat([{ value: "0", label: "Never (always active)" }]) }));
  keys.push(segmented(setRow(pane, "Wake on", "How clear speech must be to wake it from standby. “Clear speech” ignores more background noise."), {
    value: String(st().live_wake ?? 0.5), onChange: v => put("live_wake", +v),
    options: [{ value: "0.3", label: "Quiet speech" }, { value: "0.5", label: "Normal" }, { value: "0.7", label: "Clear speech" }] }));
  setHeading(pane, "Context and model");
  toggleSwitch(setRow(pane, "Start with the session's recent work", "Its last three requests and answers, so you can ask about them right away."), {
    on: st().live_context !== false, label: "Start with the session's recent work", onChange: on => put("live_context", on) });
  keys.push(segmented(setRow(pane, "Model", ""), {
    value: st().live_model || o.models[0], onChange: v => put("live_model", v),
    options: o.models.map(m => ({ value: m, label: m })) }));
  const ctl = setRow(pane, "Your instructions", "Added after Orbit's own: a manner, words to use or avoid, what to call you. They cannot turn off handing work to the agent.", { stack: true });
  ctl.innerHTML = `<textarea class="fin vprompt" rows="4" spellcheck="false" maxlength="${o.prompt_max}" placeholder="e.g. Keep it casual. Spell out file names. Call me by my first name."></textarea><div class="vcount set-desc"></div>`;
  const ta = ctl.querySelector("textarea"), cnt = ctl.querySelector(".vcount");
  ta.value = st().live_prompt || "";
  let saved = ta.value, tm = 0;
  const count = () => { cnt.textContent = `${ta.value.length} / ${o.prompt_max}${ta.value !== saved ? " · saving…" : ""}`; };
  const save = () => { clearTimeout(tm); if (ta.value === saved) return; const v = ta.value; put("live_prompt", v).then(() => { saved = v; count(); }); };
  ta.addEventListener("input", () => { count(); clearTimeout(tm); tm = setTimeout(save, 800); });
  ta.addEventListener("blur", save);
  ta.addEventListener("keydown", e => { if (e.key !== "Escape" && e.key !== "Tab") e.stopPropagation(); });  // (typing here is not a Settings shortcut)
  count();
}
/* A font row: the font in use (shown in itself); a click opens the list under the row: presets, then every installed
   family, each shown in itself; typing filters, ↑↓ (^N ^P) pick, ⏎ or Esc closes. One list open at a time. */
function fontPicker(pane, keys, { label, desc, key, mono = false, inherit = "" }) {
  const stack = mono ? monoStack : fontStack, presets = mono ? MONO_PRESETS : FONT_PRESETS;
  const ctl = setRow(pane, label, desc);
  const row = ctl.closest(".set-row");
  let cur = readPrefs()[key], rows = [];
  const shown = n => n === "" ? inherit : (FONT_LABELS[n] || n);
  ctl.innerHTML = `<button type="button" class="dd fontbtn"></button>`;
  const btn = ctl.querySelector("button");
  const drawBtn = () => {
    btn.innerHTML = `<span class="ddv">${esc(shown(cur))}</span>${I("caret", 12)}`;
    btn.style.fontFamily = cur === "" ? "var(--font)" : stack(cur);
  };
  const box = document.createElement("div"); box.className = "fontbox"; box.hidden = true;
  box.innerHTML = `<input class="fin fontq" placeholder="Filter fonts…" spellcheck="false" autocomplete="off"><div class="fontlist" tabindex="-1"></div>`;
  row.after(box);
  const q = box.querySelector("input"), list = box.querySelector(".fontlist");
  const draw = (scroll = true) => {
    const f = q.value.trim();
    const builtin = Object.keys(BUILTIN_FONTS).filter(n => !mono || BUILTIN_FONTS[n].mono);  // (a text font may be monospaced: notes)
    const names = [...(inherit ? [""] : []), ...builtin, ...Object.keys(presets).filter(n => !BUILTIN_FONTS[n]), null,
                   ...(FONTS || []).filter(n => !presets[n] && !BUILTIN_FONTS[n] && (!mono || !FONT_PRESETS[n]))];
    rows = names.filter(n => n === null ? !f : !f || fuzzy(f, shown(n)) >= 0);
    list.innerHTML = rows.map((n, i) => n === null ? `<div class="sep"></div>` :
      `<div class="fr${n === cur ? " sel" : ""}" data-i="${i}" style="font-family:${esc(n === "" ? "var(--font)" : stack(n))}">${esc(shown(n))}</div>`).join("")
      + (FONTS === null ? `<div class="fr muted">Loading the installed fonts…</div>` : "");
    const sel = list.querySelector(".fr.sel");
    if (sel && scroll) sel.scrollIntoView({ block: "nearest" });
  };
  const pick = n => { if (n === null || n === undefined) return; cur = n; setPref(key, n); drawBtn(); draw(); };
  const open = on => {
    if (on) document.querySelectorAll(".set-pane .fontbox").forEach(b => { if (b !== box) b.hidden = true; });
    box.hidden = !on;
    if (on) {
      q.value = ""; draw(); q.focus();
      if (!FONTS) api("GET", "/api/fonts").then(d => { FONTS = d.families || []; if (!box.hidden) draw(); }).catch(() => { FONTS = []; draw(); });
    }
  };
  btn.addEventListener("click", () => open(box.hidden));
  list.addEventListener("mousedown", e => { const r = e.target.closest(".fr[data-i]"); if (r) { e.preventDefault(); pick(rows[+r.dataset.i]); } });
  q.addEventListener("input", () => { draw(); const first = rows.find(n => n !== null); if (q.value.trim() && first !== undefined) pick(first); });
  const move = d => {
    const r = rows.filter(n => n !== null); if (!r.length) return;
    let k = r.indexOf(cur);
    k = d === "home" ? 0 : d === "end" ? r.length - 1 : Math.max(0, Math.min(r.length - 1, (k < 0 ? 0 : k) + d));
    pick(r[k]);
  };
  keys.push({ isOpen: () => document.activeElement === q, onKey: (e, c) => {
    if (c === "Escape" || c === "Enter") { open(false); btn.focus(); return true; }
    const m = { ArrowDown: 1, ArrowUp: -1, "C:KeyN": 1, "C:KeyP": -1, PageDown: 6, PageUp: -6, "AS:Comma": "home", "AS:Period": "end" }[c];
    if (m === undefined) return false;
    move(m); return true;
  } });
  drawBtn();
}
function settingsAppearance(pane, keys) {
  const p = readPrefs();
  segmented(setRow(pane, "Text weight", "The whole app: system smoothing, or thinner antialiased strokes."), {
    options: [{ value: "full", label: "Full" }, { value: "light", label: "Light" }], value: p.weight, onChange: v => setPref("weight", v) });
  segmented(setRow(pane, "Reading width", "The conversation and its input: a centred column of a comfortable line length, or the whole pane."), {
    options: [{ value: "", label: "Comfortable" }, { value: "full", label: "Full" }], value: p.readWidth || "", onChange: v => setPref("readWidth", v) });
  setHeading(pane, "Fonts");
  fontPicker(pane, keys, { label: "Interface font", desc: "Menus, lists, tabs, the status bar.", key: "uiFont" });
  sizeControl(setRow(pane, "Interface size", "Menus, lists, tabs, the status bar, dialogs, Settings."), { value: p.uiSize, dflt: PREFS_DEFAULT.uiSize, lo: UI_SIZE[0], hi: UI_SIZE[1], onChange: v => setPref("uiSize", v) });
  fontPicker(pane, keys, { label: "Conversation font", desc: "Answers, requests and the input.", key: "readFont", inherit: "Same as interface" });
  sizeControl(setRow(pane, "Conversation size", "Answers and requests."), { value: p.readSize, dflt: PREFS_DEFAULT.readSize, onChange: v => setPref("readSize", v) });
  fontPicker(pane, keys, { label: "Note font", desc: "Notes and Markdown files.", key: "noteFont" });
  sizeControl(setRow(pane, "Note size", "Notes and Markdown files."), { value: p.noteSize, dflt: PREFS_DEFAULT.noteSize, onChange: v => setPref("noteSize", v) });
  fontPicker(pane, keys, { label: "Code font", desc: "Code files, code in answers and notes, the terminal.", key: "codeFont", mono: true });
  sizeControl(setRow(pane, "Code size", "Code files and the terminal."), { value: p.codeSize, dflt: PREFS_DEFAULT.codeSize, onChange: v => setPref("codeSize", v) });
  const reset = setRow(pane, "Defaults", "Every setting above back to Orbit's: the built-in fonts, the same on every machine.");
  reset.innerHTML = `<button type="button" class="btn sm">Reset</button>`;
  reset.querySelector("button").addEventListener("click", () => {
    LS.set("prefs", { v: PREFS_V }); applyPrefs(); toast("Appearance: Orbit's defaults");
    const nav = pane.closest(".settings-modal"); if (nav) nav.querySelector('[data-sec="appearance"]').click();  // (drawn again)
  });
  const pv = document.createElement("div"); pv.className = "set-preview";
  pv.innerHTML = `Highlight Tracking — the quick brown fox jumps · 中文混排：本周重点 3 件事 · <code>CAP = 1.10</code>`;
  pane.appendChild(pv);
}
/* Plugins (builddoc/plugins.md): what was found, loaded or why not; each one's harnesses, services (state, restart,
   log), secrets (set here: write-only, this machine only) and command. */
async function settingsPlugins(pane) {
  pane.innerHTML = `<div class="set-desc">Loading…</div>`;
  let d;
  try { d = await api("GET", "/api/plugins"); } catch (e) { pane.textContent = e.message; return; }
  pane.innerHTML = "";
  const redraw = () => settingsPlugins(pane);
  const top = setRow(pane, "Plugins", `Folders in <code>~/.orbit/plugins/</code> or listed in <code>plugin_paths</code> (<code>~/.orbit/config.toml</code>); a change needs a server restart. Plugin API ${esc(d.api)}.`);
  top.innerHTML = `<button type="button" class="btn sm">Refresh</button>`;
  top.querySelector("button").addEventListener("click", redraw);
  if (!d.plugins.length) {
    setRow(pane, "No plugins", "A plugin adds agents (harnesses), services that run beside Orbit (a chat bridge, a webhook), commands for agents. Orbit's <code>examples/</code> has two; <code>builddoc/plugins.md</code> says how to write one.");
    return;
  }
  for (const p of d.plugins) {
    setHeading(pane, `${p.name}${p.version ? " " + p.version : ""}`);
    const st = setRow(pane, p.ok ? "Loaded" : (p.enabled ? "Not loaded" : "Disabled"),
      (p.ok ? esc(p.description || "") + (p.error ? `${p.description ? "<br>" : ""}<span style="color:var(--err)">${esc(p.error)}</span>` : "")  // (loaded, with something ignored: a shortcut)
        : esc(p.error || p.description || "")) + `<br><span class="mono">${esc(tilde(p.path))}</span>`);
    st.innerHTML = `<span class="pl-dot ${p.ok ? "ok" : p.enabled ? "bad" : ""}"></span>`;
    if (!p.ok) continue;
    if (p.harnesses.length) setRow(pane, "Agents", esc(p.harnesses.join(" · ")) + " — in ⌘N");
    for (const s of p.services || []) {
      const what = { running: "running", "needs secrets": "waiting for its secrets", starting: "starting", restarting: `restarting (${s.restarts})`, failed: "failed", exited: "exited", stopped: "stopped" }[s.state] || s.state;
      const ctl = setRow(pane, `Service ${s.id}`, `<span class="pl-dot ${s.state === "running" ? "ok" : s.state === "failed" ? "bad" : ""}"></span>${esc(what)}`
        + (s.since && s.state === "running" ? ` since ${esc(dayTime(s.since))}` : "") + (s.error ? ` · ${esc(s.error)}` : ""));
      if (!d.local) continue;
      ctl.innerHTML = `<button type="button" class="btn sm" data-a="log">Log</button><button type="button" class="btn sm" data-a="restart">Restart</button>`;
      const log = document.createElement("pre"); log.className = "pl-log"; log.hidden = true;
      ctl.closest(".set-row").after(log);
      ctl.addEventListener("click", async e => {
        const a = e.target.closest("[data-a]"); if (!a) return;
        if (a.dataset.a === "restart") {
          try { await api("POST", `/api/plugins/${p.name}/restart`, { service: s.id }); toast(`${p.name}.${s.id} restarted`); } catch (x) { toast(x.message); }
          return redraw();
        }
        if (!log.hidden) { log.hidden = true; return; }
        try { log.textContent = (await api("GET", `/api/plugins/${p.name}/log?service=${encodeURIComponent(s.id)}&lines=200`)).log || "(empty)"; }
        catch (x) { log.textContent = x.message; }
        log.hidden = false; log.scrollTop = log.scrollHeight;
      });
    }
    for (const sec of p.secrets || []) {
      const ctl = setRow(pane, sec.key, sec.set ? "Set (kept on this machine, 0600; never shown)" : "<b>Not set</b>");
      if (!d.local) continue;
      ctl.innerHTML = `<input type="password" class="fin pl-sec" placeholder="${sec.set ? "Replace…" : "Value"}" autocomplete="off"><button type="button" class="btn sm" data-a="save">Save</button>${sec.set ? `<button type="button" class="btn sm" data-a="clear">Clear</button>` : ""}`;
      const inp = ctl.querySelector("input");
      const put = async value => {
        try { await api("POST", `/api/plugins/${p.name}/secret`, { key: sec.key, value }); toast(`${sec.key} ${value === null ? "cleared" : "saved"}; ${p.name}'s services restarted`); redraw(); }
        catch (x) { toast(x.message); }
      };
      ctl.addEventListener("click", e => { const a = e.target.closest("[data-a]"); if (!a) return; if (a.dataset.a === "clear") put(null); else if (inp.value) put(inp.value); });
      inp.addEventListener("keydown", e => { if (e.key === "Enter" && inp.value) { e.preventDefault(); e.stopPropagation(); put(inp.value); } });
    }
    if (p.cli || p.hint) setRow(pane, "For agents", (p.cli ? `<code>orbit-agent p ${esc(p.name)} …</code>` : "") + (p.hint ? ` ${esc(p.hint)}` : ""));
  }
  if (!d.local) setRow(pane, "", "Services, secrets and logs are managed on the server's own machine.");
}
function settingsAbout(pane) {
  const here = ["127.0.0.1", "localhost"].includes(location.hostname) ? `this Mac (${location.host})` : location.host;
  const sv = setRow(pane, "Orbit server", `The backend this window shows: ${esc(here)}. Agents keep running when the app closes.`);
  api("GET", "/api/version").then(v => { sv.textContent = `${v.version} · plugin API ${v.api}`; }).catch(() => {});
  if (inApp()) {
    const c = setRow(pane, "Connect to another server", "Show an orbit server on another machine (its URL, from Copy URL there), or this Mac's again.");
    c.innerHTML = `<button type="button" class="btn sm">Connect…</button>`;
    c.querySelector("button").addEventListener("click", () => { closeModal(); connectServer(); });
  }
  setRow(pane, "Notes folder", S.vault ? esc(tilde(S.vault)) : "Not set.");
  setRow(pane, "Agents", esc((S.harnesses || []).map(h => h.label).join(" · ") || "—"));
}

/* ---------- the dialog */
function settingsDialog(section = LS.get("settingsSection", "general")) {
  if (modal && modal.kind === "settings") { closeModal(); return; }
  const prev = document.activeElement;
  const back = document.createElement("div"); back.className = "backdrop";
  const box = document.createElement("div"); box.className = "settings-modal"; box.setAttribute("role", "dialog"); box.setAttribute("aria-label", "Settings");
  box.innerHTML = `<nav class="set-nav"><button type="button" class="set-close" data-close title="Close (Esc)">${I("x", 14)}</button>
      ${SETTINGS_SECTIONS.map(s => `<button type="button" class="set-item" data-sec="${s.id}">${I(s.icon, 15)}<span>${esc(s.label)}</span></button>`).join("")}</nav>
    <section class="set-main"><h2 class="set-title"></h2><div class="set-pane"></div></section>`;
  const pane = box.querySelector(".set-pane");
  let keys = [], cur = null;
  const show = (id, focusNav = false) => {
    const s = SETTINGS_SECTIONS.find(x => x.id === id) || SETTINGS_SECTIONS[0];
    keys.forEach(k => k.closeList && k.closeList());
    cur = s.id; keys = []; LS.set("settingsSection", cur);
    box.querySelectorAll(".set-item").forEach(b => { const on = b.dataset.sec === cur; b.classList.toggle("on", on); b.tabIndex = on ? 0 : -1; });
    box.querySelector(".set-title").textContent = s.label;
    pane.innerHTML = ""; pane.scrollTop = 0;
    s.render(pane, keys);
    if (focusNav) box.querySelector(".set-item.on").focus();
  };
  box.addEventListener("click", e => {
    const it = e.target.closest("[data-sec]");
    if (it) show(it.dataset.sec, true);
    else if (e.target.closest("[data-close]")) closeModal();
  });
  back.addEventListener("mousedown", e => { e.preventDefault(); closeModal(); });
  const focusables = () => [...box.querySelectorAll("input, button, [tabindex='0']")].filter(el => el.offsetParent !== null && el.tabIndex !== -1);
  const trap = e => { if (!box.contains(e.target) && !e.target.closest(".menu")) { e.stopPropagation(); (focusables()[0] || box).focus(); } };
  document.addEventListener("focusin", trap, true);
  document.body.append(back, box);
  openModal({
    kind: "settings",
    restore: () => { if (prev && prev.focus && document.contains(prev)) prev.focus({ preventScroll: true }); },
    teardown: () => { document.removeEventListener("focusin", trap, true); keys.forEach(k => k.closeList && k.closeList()); back.remove(); box.remove(); },
    close: () => closeModal(),
    onKey(e, combo) {
      const open = keys.find(k => k.isOpen && k.isOpen());
      if (open && open.onKey && open.onKey(e, combo)) return true;
      if (combo === "Escape" || combo === "M:Comma") { closeModal(); return true; }
      const ae = document.activeElement;
      if (ae && ae.classList.contains("set-item")) {  // the sidebar: ↑↓ (⌃N / ⌃P) pick a section
        const d = { ArrowDown: 1, ArrowUp: -1, "C:KeyN": 1, "C:KeyP": -1 }[combo];
        if (d) { const i = SETTINGS_SECTIONS.findIndex(s => s.id === cur); show(SETTINGS_SECTIONS[(i + d + SETTINGS_SECTIONS.length) % SETTINGS_SECTIONS.length].id, true); return true; }
        if (combo === "Enter" || combo === "ArrowRight") { (focusables().find(el => pane.contains(el)) || ae).focus(); return true; }
      }
      if (combo === "Tab" || combo === "S:Tab") {
        const fs = focusables(); if (!fs.length) return true;
        const i = fs.indexOf(ae);
        fs[(i + (combo === "Tab" ? 1 : -1) + fs.length) % fs.length].focus();
        return true;
      }
      return false;
    },
  });
  show(section, true);
}
