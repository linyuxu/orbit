"use strict";
/* ===================================================================== composer + drafts */
/* The input is the same live-preview editor as notes (editor.md): images, links and math render in place. `input` is
   its content element with the textarea calls the page uses (value, selectionStart / End, setSelectionRange); "input"
   listeners hear edits only after the editor applied them (a contenteditable's own input event comes too early). */
const inputEd = CM.createEditor({
  parent: $("#inputhost"), doc: "", path: "input.md",
  composer: { placeholder: "Message the agent…" },
  preview: { resolve: src => previewSrc(src, false, (cur() ? cur().cwd : "") + "/input.md"), inline: s => md.renderInline(s) },
  zoom: url => openLightbox(url),
  onOpenLink: url => openUrl(url),
  onFiles: files => attachFiles(files),
  onChange: () => { if (!inputSetting) inputListeners.forEach(fn => { try { fn(); } catch (e) { console.error(e); } }); },
});
let inputSetting = false;
const inputListeners = [];
const input = inputEd ? inputEd.view.contentDOM : document.createElement("textarea");
if (inputEd) {
  const view = inputEd.view, nativeAdd = input.addEventListener.bind(input);
  const sel = (a, b = a) => view.dispatch({ selection: { anchor: Math.min(a, view.state.doc.length), head: Math.min(b, view.state.doc.length) }, scrollIntoView: true });
  input.addEventListener = (type, fn, o) => type === "input" ? inputListeners.push(fn) : nativeAdd(type, fn, o);
  Object.defineProperties(input, {
    value: { get: () => view.state.doc.toString(), set: v => {
      inputSetting = true;
      try { view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: String(v ?? "") }, selection: { anchor: String(v ?? "").length } }); }
      finally { inputSetting = false; }
    } },
    selectionStart: { get: () => view.state.selection.main.from, set: v => sel(v, view.state.selection.main.to < v ? v : view.state.selection.main.to) },
    selectionEnd: { get: () => view.state.selection.main.to, set: v => sel(view.state.selection.main.from, v) },
  });
  input.setSelectionRange = (a, b) => sel(a, b);
  input.insertAt = (text, at = view.state.selection.main.head) => {  // typed in: undoable, listeners hear it
    view.dispatch({ changes: { from: at, insert: text }, selection: { anchor: at + text.length }, userEvent: "input", scrollIntoView: true });
  };
}
function autosize() {}  // the editor grows by itself (up to 260px, then it scrolls)
function updateSend() { $("#send").disabled = !input.value.trim() || !cur(); if (typeof renderCallBtn === "function") renderCallBtn(); }
function saveDraft() {
  if (!S.current || S.file) return;
  if (input.value) S.drafts[S.current] = input.value; else delete S.drafts[S.current];
  LS.set("drafts", S.drafts);
}
input.addEventListener("input", () => { autosize(); updateSend(); saveDraft(); updateMention(); });
/* Completions in the input (editor.md §5): @@ the other sessions (title, id, group) · @ files and folders of the
   session's folder (git-tracked + untracked, fuzzy; ../ ~/ / browse outside it) · / at a line start the skills and commands an agent there can run.
   ↑↓ (⌃N ⌃P) move, ⏎ / Tab insert, Esc closes the list only. */
const MENTION = { open: false, rows: [], i: 0, from: 0, kind: "", q: "" };
const COMPLETE_CACHE = new Map();  // "files:<cwd>" / "skills:<cwd>" → { at, items }
async function completeSource(kind, s) {
  const key = `${kind}:${s.cwd}`, hit = COMPLETE_CACHE.get(key);
  if (hit && Date.now() - hit.at < 15000) return hit.items;
  let items = [];
  try {
    if (kind === "files") {
      const d = await api("GET", "/api/tree?root=" + encodeURIComponent(s.cwd));
      const dirs = new Set();
      for (const f of d.files || []) { const parts = (f.path || f).split("/"); for (let k = 1; k < parts.length; k++) dirs.add(parts.slice(0, k).join("/") + "/"); }
      items = [...[...dirs].map(p => ({ path: p, dir: true })), ...(d.files || []).map(f => ({ path: f.path || f, dir: false }))];
    } else {
      items = (await api("GET", `/api/skills?cwd=${encodeURIComponent(s.cwd)}&harness=${encodeURIComponent(s.harness || "claude")}`)).skills || [];

    }
  } catch (e) { items = []; }
  COMPLETE_CACHE.set(key, { at: Date.now(), items });
  return items;
}
/* `/`: Orbit's commands, then the folder's / your skills, then the harness's own commands (as its process reported
   them; before it has run, the usual ones). */
const NATIVE = {
  claude: { compact: "summarize the conversation to free context", context: "what fills the context window", clear: "start a fresh conversation",
    usage: "plan usage and limits", init: "write a CLAUDE.md for this folder", review: "review a pull request", recap: "a recap of this session",
    "security-review": "security review of the pending changes", agents: "subagents", mcp: "MCP servers", config: "settings", insights: "usage insights" },
};
/* @ beyond the session's folder: "../", "~/" or "/" browses folders (the session's machine's: a remote session's
   paths and ~ are that machine's), the part after the last "/" filtering the entries; a folder picked goes on into it. */
const OUTSIDE = /^(\.\.\/|~\/|\/)/;
function outsideDir(s, q) {  // → [the folder to list, as its machine writes it; the typed part before the name]
  const typed = q.slice(0, q.lastIndexOf("/") + 1), cwd = s.remote ? s.cwd.replace(/^\/@[^/]+/, "") : s.cwd;
  if (!typed.startsWith("../")) return [typed, typed];
  const parts = cwd.split("/");
  for (const seg of typed.split("/")) {
    if (seg === "..") { if (parts.length > 1) parts.pop(); } else if (seg && seg !== ".") parts.push(seg);
  }
  return [(parts.join("/") || "/") + "/", typed];
}
async function outsideItems(s, q) {
  const [dir, typed] = outsideDir(s, q), name = q.slice(typed.length).toLowerCase();
  const key = `dir:${s.remote || ""}:${dir}`, hit = COMPLETE_CACHE.get(key);
  let entries = hit && Date.now() - hit.at < 15000 ? hit.items : null;
  if (!entries) {
    try { entries = (await api("GET", (s.remote ? `/api/remote/${s.remote}` : "") + "/api/dirlist?path=" + encodeURIComponent(dir))).entries || []; }
    catch { entries = []; }
    COMPLETE_CACHE.set(key, { at: Date.now(), items: entries });
  }
  return entries.filter(e => name.startsWith(".") || !e.name.startsWith("."))  // (hidden ones when asked for)
    .map((e, k) => ({ x: { path: typed + e.name + (e.dir ? "/" : ""), dir: e.dir },
                      score: !name ? 1e6 - k : e.name.toLowerCase().startsWith(name) ? 1e5 - k : fuzzy(name, e.name) }));  // (folder order; then prefix, then fuzzy)
}
function slashItems(s, skills) {
  const label = harnessLabel(s.harness), names = new Set(ORBIT_CMDS.map(x => x.name));
  const out = ORBIT_CMDS.map(x => ({ ...x, source: "orbit", kind: "command" }));
  for (const x of skills) if (!names.has(x.name)) { names.add(x.name); out.push(x); }
  const desc = NATIVE[s.harness] || {};
  for (const n of (s.commands && s.commands.length ? s.commands : Object.keys(desc))) {
    if (names.has(n)) continue;
    names.add(n);
    out.push({ name: n, description: desc[n] || `${label} command`, source: label, kind: "native" });
  }
  return out;
}
async function updateMention() {
  const at = input.selectionStart, before = input.value.slice(0, at), s = cur();
  let m, kind = "", q = "";
  if ((m = /(^|[^\w@])@@([\w.-]*)$/.exec(before))) { kind = "session"; q = m[2]; }
  else if ((m = /(^|\s)@([^\s@]*)$/.exec(before))) { kind = "file"; q = m[2]; }
  else if ((m = /(^|\n)\s*\/([\w:.-]*)$/.exec(before))) { kind = "skill"; q = m[2]; }
  if (!kind || !s) { closeMention(); return; }
  const from = at - q.length - (kind === "session" ? 2 : 1);
  const my = (updateMention.seq = (updateMention.seq || 0) + 1);
  let rows = [];
  if (kind === "session") {
    rows = [...S.sessions.values()].filter(x => x.id !== S.current && !x.fleet)
      .map(x => ({ x, score: q ? Math.max(fuzzy(q, x.name), fuzzy(q, mentionTag(x.name)), fuzzy(q, x.id), fuzzy(q, groupPath(x.group))) : 0 }));
  } else if (kind === "file" && OUTSIDE.test(q)) {
    rows = await outsideItems(s, q);
  } else if (kind === "file") {
    const items = await completeSource("files", s);
    rows = items.map(x => ({ x, score: q ? fuzzy(q, x.path) : -x.path.split("/").length - (x.dir ? 0 : 0.5) }));
    if (["", ".", ".."].includes(q)) rows.push({ x: { path: "../", dir: true, out: true }, score: q ? 2000 : 0.5 });  // (the way out)
    if (q === "~") rows.push({ x: { path: "~/", dir: true, out: true }, score: 2000 });
  } else {
    const items = slashItems(s, await completeSource("skills", s));
    rows = items.map((x, k) => ({ x, score: q ? Math.max(fuzzy(q, x.name) + 1000, fuzzy(q, x.description || "")) : -k }));  // name first, then description
  }
  if (my !== updateMention.seq) return;  // typed on meanwhile
  MENTION.rows = rows.filter(r => r.score >= 0 || (!q && kind !== "session")).sort((a, b) => b.score - a.score).slice(0, kind === "session" ? 12 : 40).map(r => r.x);
  if (kind !== MENTION.kind || q !== MENTION.q) MENTION.i = 0;
  Object.assign(MENTION, { open: true, kind, q, from });
  MENTION.i = Math.min(MENTION.i, Math.max(0, MENTION.rows.length - 1));
  renderMention();
}
function renderMention() {
  const pop = $("#mentionpop");
  pop.hidden = !MENTION.open;
  if (!MENTION.open) return;
  const head = MENTION.kind === "file" && OUTSIDE.test(MENTION.q) ? `In ${esc(MENTION.q.slice(0, MENTION.q.lastIndexOf("/") + 1))}`
    : { session: "Mention a session", file: "A file or folder of this session's folder (../ or ~/: elsewhere)", skill: "A skill or command" }[MENTION.kind];
  const row = (x, k) => {
    const cls = `row${k === MENTION.i ? " sel" : ""}`;
    if (MENTION.kind === "session") return `<div class="${cls}" data-mi="${k}" role="option">${sessionMark(x, 13)}<span>${esc(mentionTag(x.name))}</span><span class="sub">${esc(groupPath(x.group))} · ${esc(x.id.slice(0, 8))}</span></div>`;
    if (MENTION.kind === "file" && x.out) return `<div class="${cls}" data-mi="${k}" role="option"><span class="ic">${I("folder", 13)}</span><span>${esc(x.path)}</span><span class="sub">${x.path === "~/" ? "home folder" : "parent folder"}</span></div>`;
    if (MENTION.kind === "file") return `<div class="${cls}" data-mi="${k}" role="option"><span class="ic">${I(x.dir ? "folder" : "file", 13)}</span><span>${esc(basename(x.path))}${x.dir ? "/" : ""}</span><span class="sub">${OUTSIDE.test(MENTION.q) ? "" : esc(dirname("/" + x.path.replace(/\/$/, "")).slice(1))}</span></div>`;
    return `<div class="${cls}" data-mi="${k}" role="option"><span>/${esc(x.name)}</span><span class="desc">${esc(x.description || "")}</span><span class="sub">${esc(x.source)}</span></div>`;
  };
  pop.innerHTML = `<div class="q">${head} · ↑↓ ⏎ / Tab · Esc</div>` + (MENTION.rows.length ? MENTION.rows.map(row).join("")
    : `<div class="q">${MENTION.kind === "skill" ? "No skill matches" : MENTION.kind === "file" ? "No file matches" : "No other session matches"}</div>`);
  const sel = pop.querySelector(".row.sel"); if (sel) sel.scrollIntoView({ block: "nearest" });
}
function closeMention() { if (!MENTION.open) return; MENTION.open = false; MENTION.i = 0; MENTION.kind = ""; renderMention(); }
function pickMention(k = MENTION.i) {
  const x = MENTION.rows[k]; if (!x) { closeMention(); return; }
  const at = input.selectionStart;
  const text = MENTION.kind === "session" ? mentionTag(x.name) + " "
    : MENTION.kind === "file" ? "@" + (/\s/.test(x.path) ? `"${x.path}"` : x.path) + (x.dir ? "" : " ")
    : "/" + x.name + " ";
  const view = inputEd.view;
  view.dispatch({ changes: { from: MENTION.from, to: at, insert: text }, selection: { anchor: MENTION.from + text.length }, userEvent: "input.complete" });
  const keepOpen = MENTION.kind === "file" && x.dir;  // a folder: go on into it
  closeMention(); updateSend(); saveDraft();
  if (keepOpen) updateMention();
}
function mentionKey(combo) {  // from the key handler while the picker is open
  const mv = { ArrowDown: 1, ArrowUp: -1, "C:KeyN": 1, "C:KeyP": -1 }[combo];
  if (mv) { if (MENTION.rows.length) MENTION.i = (MENTION.i + mv + MENTION.rows.length) % MENTION.rows.length; renderMention(); return true; }
  if (combo === "Enter" || combo === "Tab") { if (!MENTION.rows.length) { closeMention(); return false; } pickMention(); return true; }
  if (combo === "Escape") { closeMention(); return true; }
  return false;
}
/* ↑ in the empty input: this session's earlier requests, newest first; typing filters, ⏎ puts one back. */
function inputHistory() {
  const seen = new Set();
  const texts = (S.cards.get(S.current) || []).filter(c => c.kind !== "note" && c.kind !== "auto").reverse().map(c => c.text).filter(t => t && !seen.has(t) && seen.add(t));
  if (!texts.length) { toast("No earlier request in this session"); return; }
  menu(texts.slice(0, 200).map(t => ({ label: t.replace(/\s+/g, " ").slice(0, 120), run: () => { input.value = t; input.focus(); updateSend(); saveDraft(); } })),
    { anchor: $("#composer") }, "bell");
}
$("#mentionpop").addEventListener("mousedown", e => { const r = e.target.closest("[data-mi]"); e.preventDefault(); if (r) pickMention(+r.dataset.mi); });
input.addEventListener("blur", () => setTimeout(closeMention, 100));
input.addEventListener("click", updateMention);
new ResizeObserver(() => autosize()).observe($("#composer"));
async function sendText(sid, text) { return api("POST", `/api/sessions/${sid}/send`, { text }); }
/* ✎ Edit a request: its text goes back into the input; on send a queued one changes in place, a finished one is
   replaced by the new request. Esc in an empty input, or "cancel", stops editing. */
function startEdit(sid, c) {
  S.editing = { sid, cid: c.id };
  input.value = c.text; autosize(); updateSend(); saveDraft();
  renderEditing();
  input.focus(); input.setSelectionRange(input.value.length, input.value.length);
}
function stopEdit() { S.editing = null; renderEditing(); }
function renderEditing() {
  const e = S.editing && S.editing.sid === S.current && !S.file ? S.editing : null;
  const box = $("#editing");
  box.hidden = !e;
  if (e) box.innerHTML = `${I("pencil", 12)}<span>Editing #${e.cid}: sending replaces it</span><button data-canceledit>cancel</button>`;
}
$("#editing").addEventListener("mousedown", e => { if (e.target.closest("[data-canceledit]")) { e.preventDefault(); stopEdit(); } });
/* @@Title-with-dashes names another open session (interaction.md §11). */
const mentionTag = name => "@@" + String(name).trim().replace(/\s+/g, "-");
function mentionTargets(text, self) {
  const out = [];
  for (const m of text.matchAll(/(^|[^\w@])@@([\w.-]*\w)/g)) {
    const want = m[2].toLowerCase();
    const s = [...S.sessions.values()].find(x => x.id !== self && !x.fleet && mentionTag(x.name).slice(2).toLowerCase() === want);
    if (s && !out.includes(s)) out.push(s);
  }
  return out;
}
/* `/mode NAME` in the input switches the permission mode (the agent restarts on the same conversation). */
const MODE_ALIAS = { bypass: "bypassPermissions", bypasspermissions: "bypassPermissions", accept: "acceptEdits", "accept-edits": "acceptEdits",
  acceptedits: "acceptEdits", edits: "acceptEdits", auto: "auto", plan: "plan", ask: "default", default: "default" };
async function setMode(s, name) {
  const mode = MODE_ALIAS[String(name || "").toLowerCase()];
  if (!mode || !modesFor(s.harness).includes(mode)) { toast(`Modes for ${harnessLabel(s.harness)}: ${modesFor(s.harness).map(m => MODE_SHORT[m]).join(" · ")}`); return false; }
  try { const r = await api("POST", `/api/sessions/${s.id}/mode`, { mode }); S.sessions.set(s.id, { ...s, ...r }); renderStatus(); toast(`Mode: ${modeLabel(s.harness, mode)}`); return true; }
  catch (e) { toast(e.message); return false; }
}
function modeMenu(s) {
  menu(modesFor(s.harness).map(m => ({ label: modeLabel(s.harness, m), key: s.mode === m ? "current" : "", run: () => setMode(s, m) })), { anchor: $("#modechip") });
}
/* Steering: a message for the request that is running joins that turn (Claude Code; others queue it). */
const canSteer = sid => { const s = S.sessions.get(sid); return !!(s && s.steers && s.state === "working"); };
/* Orbit's own slash commands (the rest go to the agent as typed: its native commands and skills). */
const ORBIT_CMDS = [
  { name: "model", description: "switch the model, kept for this session: /model opus · /model claude-sonnet-5-5 high" },
  { name: "effort", description: "reasoning effort: low · medium · high · xhigh · max" },
  { name: "mode", description: "permission mode: bypass · accept edits · plan · ask" },
  { name: "status", description: "this session: model, effort, mode, context, folder" },
  { name: "fork-session", description: "fork this session: a new one in this group continues the conversation, same settings · /fork-session NAME" },
];
const clearInput = () => { input.value = ""; updateSend(); saveDraft(); closeMention(); };
async function orbitCommand(s, text) {
  const m = /^\s*\/(mode|model|effort|status|fork-session)(?:\s+([\s\S]*?))?\s*$/.exec(text);
  if (!m) return false;
  const arg = (m[2] || "").trim();
  if (m[1] === "fork-session") { clearInput(); forkSession(s.id, arg); return true; }
  if (m[1] === "status") { clearInput(); statusDialog(s); return true; }
  if (m[1] === "mode") { if (!arg) modeMenu(s); else if (await setMode(s, arg)) clearInput(); return true; }
  if (m[1] === "effort") { if (!arg) effortMenu(s); else if (await setModel(s, { effort: arg === "default" ? "" : arg.toLowerCase() })) clearInput(); return true; }
  if (!arg) { modelMenu(s); return true; }
  const [name, eff] = arg.split(/\s+/);
  const body = { model: name === "default" ? "" : name };
  if (eff) body.effort = eff.toLowerCase();
  if (await setModel(s, body)) clearInput();
  return true;
}
async function setModel(s, body) {
  try {
    const r = await api("POST", `/api/sessions/${s.id}/model`, body);
    S.sessions.set(s.id, { ...S.sessions.get(s.id), ...r }); renderStatus();
    toast(`Now ${modelName(r.model) || "the default model"}${r.effort ? " · " + r.effort : ""} (restarted on the same conversation)`);
    return true;
  } catch (e) { toast(e.message); return false; }
}
const harnessOf = s => S.harnesses.find(h => h.id === s.harness) || { models: [], efforts: [] };
function modelMenu(s) {
  const h = harnessOf(s);
  const items = [{ value: "", label: defaultModelLabel(h) }, ...h.models];
  menu(items.map(m => ({ label: m.label, key: (s.model || "") === m.value ? "current" : "", run: () => setModel(s, { model: m.value }) })), { anchor: $("#runon") });
}
function effortMenu(s) {
  const h = harnessOf(s);
  if (!h.efforts || !h.efforts.length) { toast(`${harnessLabel(s.harness)} has no effort setting`); return; }
  menu(["", ...h.efforts].map(e => ({ label: e || "Default (the harness's own setting)", key: (s.effort || "") === e ? "current" : "", run: () => setModel(s, { effort: e }) })), { anchor: $("#runon") });
}
function statusDialog(s) {
  const lines = [
    ["Harness", harnessLabel(s.harness)],
    ["Model", `${s.model ? s.model : "default"}${s.model_seen ? ` → ${modelName(s.model_seen)} (${s.model_seen})` : ""}`],
    ["Effort", s.effort || "default"],
    ["Mode", modeLabel(s.harness, s.mode)],
    ["Folder", tilde(s.cwd)],
    ["Context", s.context ? `${kfmt(s.context)} tokens (the last model call)` : "—"],
    ["Compactions", String(s.compactions || 0)],
    ["Process", s.state + (s.queued ? ` · ${s.queued} queued` : "") + (s.error ? ` · ${s.error}` : "")],
    ["Session", s.id],
  ];
  dialog({ title: `${s.name}: status`, body: lines.map(([k, v]) => `${k}: ${v}`).join("\n"), ok: "", cancel: "Close", wide: "settings" });
}
async function send({ steer = false } = {}) {
  const s = cur(), text = input.value;
  if (!s || !text.trim()) return;
  if (await orbitCommand(s, text)) return;
  if (steer && canSteer(s.id)) {
    clearInput();
    try { const c = await api("POST", `/api/sessions/${s.id}/steer`, { text }); upsertCard(c); toast(c.steered ? `Steered #${c.id}: it joins the running request` : `Sent as #${c.id}`); }
    catch (e) { input.value = text; updateSend(); toast("Steer failed: " + e.message); }
    return;
  }
  input.value = ""; autosize(); updateSend(); saveDraft(); closeMention();
  const editing = S.editing && S.editing.sid === s.id ? S.editing : null;
  try {
    let c;
    if (editing) { c = await api("POST", `/api/sessions/${s.id}/edit`, { cid: editing.cid, text }); stopEdit(); }
    else {
      // @@ mentions: every mentioned session gets the request with this session's context; this one hears who else did.
      const to = mentionTargets(text, s.id);
      for (const t of to) api("POST", `/api/sessions/${t.id}/mention`, { from: s.id, text }).catch(e => toast(`${mentionTag(t.name)}: ${e.message}`));
      c = await sendText(s.id, to.length ? `${text}\n\n(also sent to ${to.map(t => mentionTag(t.name)).join(", ")})` : text);
      if (to.length) toast(`Also sent to ${to.map(t => mentionTag(t.name)).join(", ")}`);
    }
    setAtEnd(true);  // a request sent: follow it
    upsertCard(c);
  } catch (e) {
    // Never lose input: back into that session's draft (even after a tab switch).
    if (S.current === s.id && !S.file) { if (!input.value) { input.value = text; autosize(); updateSend(); saveDraft(); } }
    else { const d = S.drafts[s.id] || ""; S.drafts[s.id] = d ? text + "\n\n" + d : text; LS.set("drafts", S.drafts); }
    toast("Send failed: " + e.message);
  }
}
$("#send").addEventListener("click", send);
/* Send to another session (the send button's right-click, ⌘⌥⏎): what is in this input, as you typed it, as a request
   there: your message (no "(from session …)"), as if typed in that tab, which stays where it is. Its card says it
   came from your input here. The input empties once it is there; a failure keeps it. */
function sendInputTo(where) {
  const text = input.value;
  if (!text.trim()) { toast("Type what to send first"); return; }
  const here = cur();
  const ss = [...S.sessions.values()].filter(x => !x.fleet && (!here || x.id !== here.id));
  if (!ss.length) { toast("No other session to send to"); return; }
  const go = async x => {
    try {
      await api("POST", `/api/sessions/${encodeURIComponent(x.id)}/send`, { text, origin: { label: here ? `You, from ${here.name}` : "You" },
        client_id: "ui:" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8) });
      if (input.value === text) { input.value = ""; autosize(); }
      saveDraft(); updateSend();
      toast(`Sent to ${x.name}, as you typed it`);
    } catch (e) { toast(`Not sent to ${x.name}: ${e.message}`); }
  };
  menu(ss.map(x => ({ label: x.name, key: x.remote || (S.groups.find(g => g.id === x.group) || {}).name || "", run: () => go(x) })),
       where || { anchor: $("#send") });
}
$("#send").addEventListener("contextmenu", e => { e.preventDefault(); sendInputTo({ at: { x: e.clientX, y: e.clientY } }); });
