"use strict";
/* ===================================================================== ⌘J shell (a pty the server owns) */
/* One login shell per session, in its folder. The pane follows the current session; the shell outlives the page.
   Keys go to the terminal except ⌘ shortcuts and ⌃⌥HJKL; Esc goes to the shell, Esc Esc leaves it. */
const SH = { shown: LS.get("shellShown", false), h: LS.get("shellH", 0.38), term: null, fit: null, id: null, key: null, mode: "shell",
             es: null, dead: false, escAt: 0, out: Promise.resolve(), loading: null, info: null };
const shellEl = $("#shell");
const shellFocused = () => !EMBED && SH.shown && shellEl.contains(document.activeElement);
function loadXterm() {
  if (window.Terminal && window.FitAddon) return Promise.resolve();
  if (SH.loading) return SH.loading;
  const css = document.createElement("link"); css.rel = "stylesheet"; css.href = "/vendor/xterm.css"; document.head.appendChild(css);
  const js = src => new Promise((ok, bad) => { const e = document.createElement("script"); e.src = src; e.onload = ok; e.onerror = () => bad(new Error("cannot load " + src)); document.head.appendChild(e); });
  SH.loading = js("/vendor/xterm.js").then(() => js("/vendor/xterm-fit.js"));
  return SH.loading;
}
function termTheme() {
  const v = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  return { background: v("--note-bg"), foreground: v("--note-ink"), cursor: v("--ink"), cursorAccent: v("--note-bg"),
           selectionBackground: v("--sel-focus") };
}
function b64bytes(s) { const bin = atob(s); const a = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i); return a; }
async function ensureTerm() {
  await loadXterm();
  if (SH.term) return SH.term;
  const family = getComputedStyle(document.documentElement).getPropertyValue("--mono").trim() || "Menlo, monospace";
  try { await document.fonts.load(`${clampSize(readPrefs().codeSize)}px ${family}`); } catch {}  // (a shipped font: loaded before the terminal measures it)
  const t = new Terminal({ fontFamily: family,
                           fontSize: clampSize(readPrefs().codeSize), lineHeight: 1.2, cursorBlink: true, scrollback: 5000, allowProposedApi: false,
                           macOptionIsMeta: true, theme: termTheme() });
  SH.fit = new FitAddon.FitAddon(); t.loadAddon(SH.fit);
  t.open($("#term"));
  t.onData(d => {
    if (SH.dead) { if (d === "\r") restartShell(); return; }
    shellInput(d);
  });
  t.onResize(({ cols, rows }) => { if (SH.id) api("POST", `/api/shell/${SH.id}/resize`, { cols, rows }).catch(() => {}); });
  new ResizeObserver(() => fitShell()).observe($("#term"));
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { t.options.theme = termTheme(); });
  SH.term = t;
  return t;
}
function fitShell() { if (SH.term && SH.shown && SH.fit) { try { SH.fit.fit(); } catch {} } }
function shellInput(d) {  // in order, one request at a time
  const id = SH.id;
  if (!id) return;
  SH.out = SH.out.then(() => api("POST", `/api/shell/${id}/input`, { data: d })).catch(e => toast("Shell: " + e.message));
}
function applyShell() {
  shellEl.hidden = $("#hsplit").hidden = !SH.shown;
  document.documentElement.style.setProperty("--shellh", Math.round(SH.h * 1000) / 10 + "%");
  LS.set("shellShown", SH.shown); LS.set("shellH", SH.h);
  if (SH.shown) requestAnimationFrame(fitShell);
}
function shellTitle() {
  const i = SH.info, s = S.sessions.get(SH.key);
  $("#shterm").hidden = !!(s && s.remote) || SH.mode === "term";  // another machine's shell lives in ⌘J (a terminal app here cannot reach it)
  $("#shkill").innerHTML = I("power", 13) + (SH.mode === "term" ? "Detach" : "Kill");
  $("#shkill").title = SH.mode === "term" ? "Detach from the agent's terminal (the agent runs on; ⏎ attaches again)" : "Kill the shell (it restarts on ⏎)";
  $("#shtitle").textContent = !i ? "shell" : SH.mode === "term" ? `the agent's terminal · ${s ? s.name : i.title || ""} (${i.shell})${SH.dead ? " · detached" : ""}`
    : `${i.shell || "shell"} · ${s ? s.name : i.title || ""} · ${tilde(i.cwd)}${SH.dead ? " · exited" : ""}`;
}
async function showShell(focus = true) {
  const s = cur();
  if (!s) { toast("Open a session first: the shell runs in its folder"); return; }
  SH.shown = true; applyShell();
  try {
    const t = await ensureTerm();
    await connectShell(s.id);
    fitShell();
    if (focus) t.focus();
  } catch (e) { toast("Shell: " + e.message); }
}
function hideShell() {
  const had = shellFocused();
  SH.shown = false; applyShell();
  if (had) focusMain();
}
function toggleShell() {  // three states: hidden → show + focus; shown, not focused → focus; focused → hide
  if (SH.mode === "term") { SH.mode = "shell"; return showShell(true); }  // the agent's terminal was shown: the shell instead
  if (!SH.shown) return showShell(true);
  if (!shellFocused()) { if (SH.key !== S.current) return showShell(true); SH.term ? SH.term.focus() : showShell(true); return; }
  hideShell();
}
/* ⌘T: the agent's own terminal (a harness that declares one, Harness Protocol 1.1) in the same pane, attached on a pty
   the server owns. Closing the pane only detaches; the agent runs on. Same three states as ⌘J. */
function toggleTerminal() {
  const s = cur();
  if (!s) { toast("Open a session first"); return; }
  if (!s.terminal) { toast(`${s.name}'s agent has no terminal (⌘J is a shell in its folder)`); return; }
  if (SH.shown && SH.mode === "term" && SH.key === s.id) { if (!shellFocused()) { if (SH.term) SH.term.focus(); return; } hideShell(); return; }
  SH.mode = "term";
  showShell(true);
}
async function connectShell(sid, { force = false } = {}) {
  const s = S.sessions.get(sid), mode = SH.mode === "term" && s && s.terminal ? "term" : "shell";
  if (!force && SH.key === sid && SH.connected === mode && SH.es && SH.es.readyState !== 2) return;
  SH.mode = mode;
  const info = await api("POST", "/api/shell", mode === "term" ? { key: sid, terminal: true } : { key: sid });
  SH.connected = mode;
  if (SH.es) SH.es.close();
  SH.id = info.id; SH.key = sid; SH.info = info; SH.dead = !info.alive;
  SH.term.reset();
  const es = SH.es = new EventSource(`/api/shell/${info.id}/stream`);
  es.onmessage = ev => {
    if (es !== SH.es) return;
    const m = JSON.parse(ev.data);
    if (m.t === "screen") { SH.term.reset(); SH.term.write(b64bytes(m.d)); SH.info = m; SH.dead = !m.alive; shellTitle();
                           const { cols, rows } = SH.term; api("POST", `/api/shell/${info.id}/resize`, { cols, rows }).catch(() => {}); }
    else if (m.t === "data") SH.term.write(b64bytes(m.d));
    else if (m.t === "reset") SH.term.reset();
    else if (m.t === "exit") {
      SH.dead = true; shellTitle();
      SH.term.write(SH.mode === "term" ? `\r\n\x1b[2m[detached from the agent's terminal: ⏎ attaches again]\x1b[0m\r\n`
        : `\r\n\x1b[2m[the shell exited${m.code != null ? " with code " + m.code : ""}: ⏎ starts a new one]\x1b[0m\r\n`);
    }
  };
  shellTitle();
}
async function restartShell() {
  if (!SH.id) return;
  SH.dead = false;
  try { await api("POST", `/api/shell/${SH.id}/restart`); SH.info = { ...SH.info, alive: true }; shellTitle(); } catch (e) { toast("Shell: " + e.message); }
}
function shellFollow() {  // the pane shows the current session's shell (or its agent's terminal, when it has one)
  if (!SH.shown || EMBED || !SH.term || !S.current || SH.key === S.current) return;
  connectShell(S.current).then(fitShell).catch(e => toast("Shell: " + e.message));
}
function shellHere(sid = S.current) {  // ⌘E → Shell here
  if (sid !== S.current) switchTo(sid, { focus: false });
  showShell(true);
}
function shellEsc() {
  if (Date.now() - SH.escAt < 600) { SH.escAt = 0; focusMain(); return; }  // Esc Esc: back to the input
  SH.escAt = Date.now();
  if (!SH.dead) shellInput("\x1b");
}
async function killShell() {
  if (!SH.id) return;
  try { await api("POST", `/api/shell/${SH.id}/kill`); } catch (e) { toast("Shell: " + e.message); }
}
async function shellInTerminal() {
  const s = cur();
  try {
    if (!s) return;
    const id = SH.key === s.id && SH.id ? SH.id : (await api("POST", "/api/shell", { key: s.id })).id;
    const r = await api("POST", `/api/shell/${id}/terminal`);
    toast(`Opened ${tilde(s.cwd)} in ${r.app}`);
  } catch (e) { toast("Shell: " + e.message); }
}
function nudgeShell(d) {  // ⌃⌥K / ⌃⌥J: the shell's top edge up / down
  if (!SH.shown) return false;
  const col = $("#work").clientHeight || 600;
  SH.h = Math.max(0.05, Math.min(0.95, SH.h + d / col)); applyShell();
  return true;
}
$("#hsplit").addEventListener("mousedown", e => {
  e.preventDefault();
  const r = $("#work").getBoundingClientRect(), sb = $("#statusbar").getBoundingClientRect().height;  // (the status bar is under the shell)
  const mv = ev => { SH.h = Math.max(0.05, Math.min(0.95, (r.bottom - sb - ev.clientY) / r.height)); applyShell(); };
  const up = () => { document.removeEventListener("mousemove", mv); document.removeEventListener("mouseup", up); };
  document.addEventListener("mousemove", mv); document.addEventListener("mouseup", up);
});
$("#shkill").innerHTML = I("power", 13) + "Kill";
$("#shterm").innerHTML = "Open in iTerm";
$("#shclose").innerHTML = I("x", 13);
$("#shkill").addEventListener("click", killShell);
$("#shterm").addEventListener("click", shellInTerminal);
$("#shclose").addEventListener("click", hideShell);
if (!EMBED) { applyShell(); if (SH.shown) setTimeout(() => showShell(false), 0); }

/* ===================================================================== ⌘K quick ask (Fleet) */
/* A one-off request: to a Fleet helper (a background agent in the current folder, no tab; its answer shows in the side
   tab without taking the keyboard) or to any open session. */
async function quickAsk() {
  const root = ctxRoot();
  const targets = [{ value: "__fleet", label: `Fleet helper · ${root ? tilde(root) : "~"}` },
                   ...[...S.sessions.values()].filter(s => !s.fleet).map(s => ({ value: s.id, label: `${s.name} · ${basename(s.cwd)}` }))];
  const last = LS.get("quickTo", "__fleet");
  const v = await dialog({ title: "Quick ask", ok: "Ask", fields: [
    { key: "text", label: "Request", placeholder: "One question or task; ⏎ asks" },
    { key: "to", label: "To", type: "dropdown", value: targets.some(t => t.value === last) ? last : "__fleet", options: targets },
  ], validate: x => x.text.trim() ? null : "Type the request first" });
  if (!v) return;
  LS.set("quickTo", v.to);
  try {
    if (v.to === "__fleet") {
      const r = await api("POST", "/api/fleet/ask", { text: v.text, cwd: root || "" });
      S.sessions.set(r.session.id, r.session);
      openSide({ kind: "session", sid: r.session.id }, { focus: false });
      toast(`${r.session.name} is on it (#${r.card.id}): the answer shows in the side tab`);
    } else {
      const c = await api("POST", `/api/sessions/${v.to}/send`, { text: v.text });
      const s = S.sessions.get(v.to);
      toast(`Sent to ${s ? s.name : "the session"} as #${c.id}`, () => openCard(v.to, c.id));
    }
  } catch (e) { toast(e.message); }
}
