"use strict";
/* ===================================================================== notes: vault, [[links]], backlinks, the journal */
/* The journal (⌘⇧D): Obsidian's daily note when the vault sets one up (.obsidian/daily-notes.json), else this week's. */
const JOURNAL = { kind: "week", label: "This week" };
const journalName = () => JOURNAL.kind === "day" ? "today's note" : "this week's note";
async function loadVault() {
  try { const d = await api("GET", "/api/notes/info"); S.vault = d.root; if (d.journal) Object.assign(JOURNAL, d.journal); } catch {}
  loadNoteNames();
}
async function loadNoteNames() {
  try {
    const d = await api("GET", "/api/notes/list");
    S.noteNames = [...new Set(d.notes.map(noteTitle))].sort((a, b) => a.localeCompare(b));
  } catch {}
}
/* Follow a [[link]]: the note it names (Obsidian rules, resolved by the server), created when missing. */
async function openWiki(name, from) {
  try {
    const r = await api("GET", `/api/notes/resolve?name=${encodeURIComponent(name)}&from=${encodeURIComponent(from || "")}`);
    let path = r.path;
    if (!path) {
      path = (await api("POST", "/api/notes/create", { name })).path;
      loadNoteNames(); refreshTree(S.vault, true);
      toast(`Created the note “${name}”`);
    }
    openFile(path);
  } catch (e) { toast(e.message); }
}
async function newNote(folder = "") {
  const v = await dialog({ title: "New note", ok: "Create",
    fields: [{ key: "name", label: folder ? `In ${folder} (a/b makes folders)` : "Name (a/b makes folders)" }],
    validate: v => v.name.trim() ? null : "Give the note a name." });
  if (!v) return;
  try {
    const r = await api("POST", "/api/notes/create", { name: (folder ? folder + "/" : "") + v.name.trim() });
    loadNoteNames(); refreshTree(S.vault, true);
    openFile(r.path, { pinned: true });
  } catch (e) { toast(e.message); }
}
function applyReveal(path) {
  if (!S.reveal || S.reveal.path !== path) return;
  const ed = EDITORS.get(path);
  if (!ed || !ed.ed) return;
  if (typeof S.reveal.line === "number") {  // orbit-agent open PATH:LINE: place the cursor, never take the keyboard
    const v = ed.ed.view, n = Math.max(1, Math.min(S.reveal.line, v.state.doc.lines));
    v.dispatch({ selection: { anchor: v.state.doc.line(n).from }, scrollIntoView: true });
    S.reveal = null;
    return;
  }
  if (ed.ed.revealLine(S.reveal.line)) { S.reveal = null; if (S.file === path && !modal && mayFocus()) ed.ed.focus(); }
}
function selectedCardText() {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || !S.current || S.file) return null;
  const text = sel.toString().trim();
  const node = sel.anchorNode && (sel.anchorNode.nodeType === 1 ? sel.anchorNode : sel.anchorNode.parentElement);
  const card = node && node.closest && node.closest(".card");
  if (!text || !card) return null;
  const s = cur();
  return { text, cid: card.id.replace("card-", ""), sid: S.current, name: s ? s.name : "session" };
}
/* ❝ Quote (editor.md §5, §6): a selection in an answer, a file or a side tab goes into the input as "> where" and
   the quoted lines, at its end. A file says path:lines; an answer says session #id. From a side tab it goes to the
   main page's input. The button floats at the selection; ⌘E offers the same plus Quote to another session. */
function quoteSource() {
  const pq = pviewQuote();  // a plugin's thread or doc (pviews.js)
  if (pq !== undefined) return pq;
  if (S.file) {
    const ed = EDITORS.get(S.file);
    if (!ed || !ed.ed) return null;
    const v = ed.ed.view, r = v.state.selection.main;
    if (r.empty || !v.hasFocus) return null;
    const a = v.state.doc.lineAt(r.from).number, b = v.state.doc.lineAt(r.to).number;
    return { text: v.state.sliceDoc(r.from, r.to), ref: `${agentPath(S.file)}:${a}${b > a ? "-" + b : ""}`, at: () => v.coordsAtPos(r.to) };
  }
  const c = selectedCardText();
  if (!c) return null;
  const range = getSelection().getRangeAt(0);
  return { text: c.text, ref: `${c.name} #${c.cid}`, at: () => { const rs = range.getClientRects(); return rs[rs.length - 1] || range.getBoundingClientRect(); } };
}
const quoteBlock = q => `> ${q.ref}\n` + q.text.replace(/\s+$/, "").split("\n").map(l => "> " + l).join("\n") + "\n\n";
function quoteTo(q, sid = S.current) {
  hideQuoteBtn();
  if (EMBED) { window.parent.postMessage({ orbit: "quote", text: q.text, ref: q.ref }, location.origin); return; }
  if (!sid) { toast("No session to quote into"); return; }
  const block = quoteBlock(q);
  if (sid !== S.current) {  // another session: its draft gets the quote, and it comes to the front
    saveDraft();
    const d = S.drafts[sid] || "";
    S.drafts[sid] = d + (d && !d.endsWith("\n") ? "\n\n" : "") + block;
    LS.set("drafts", S.drafts);
    switchTo(sid);
  } else {
    if (S.file) switchTo(sid);  // back to the conversation; the input keeps its draft
    const v = input.value;
    input.focus();
    input.insertAt((v && !v.endsWith("\n") ? "\n\n" : "") + block, v.length);
  }
  setTimeout(() => { input.focus(); input.setSelectionRange(input.value.length, input.value.length); updateSend(); saveDraft(); }, 0);
}
function quoteMenu(q) {
  const others = [...S.sessions.values()].filter(x => !x.fleet && x.id !== S.current);
  menu([
    { label: EMBED ? "Quote into the main input" : `Quote into ${cur() ? cur().name : "the input"}`, key: "Q", run: () => quoteTo(q) },
    EMBED || !others.length ? null : { label: "Quote to", sub: () => others.map(x => ({ label: x.name, run: () => quoteTo(q, x.id) })) },
    S.file ? { label: "Copy path:lines", run: () => copyText(q.ref) } : null,
    { label: "Copy", run: () => copyText(q.text) },
  ].filter(Boolean), { at: (() => { const r = q.at(), k = rectScale(); return r ? { x: r.left / k, y: r.bottom / k } : { x: 200, y: 200 }; })() });
}
const quoteBtn = $("#quotebtn");
quoteBtn.innerHTML = `${I("quote", 13)}<span>Quote</span>`;
let quoteNow = null;
function hideQuoteBtn() { quoteBtn.classList.remove("show"); quoteNow = null; }
function placeQuoteBtn() {
  const q = !modal && quoteSource();
  if (!q || !q.text.trim()) { hideQuoteBtn(); return; }
  const r = q.at(); if (!r) { hideQuoteBtn(); return; }
  const k = rectScale(), vw = innerWidth / S.zoom;
  quoteNow = q;
  quoteBtn.style.left = Math.min(vw - 90, Math.max(8, r.right / k + 6)) + "px";
  quoteBtn.style.top = Math.max(8, r.top / k - 32) + "px";
  quoteBtn.classList.add("show");
}
const schedQuote = () => { clearTimeout(placeQuoteBtn.t); placeQuoteBtn.t = setTimeout(placeQuoteBtn, 120); };
document.addEventListener("selectionchange", schedQuote);
document.addEventListener("mouseup", schedQuote);
document.addEventListener("keyup", schedQuote);
document.addEventListener("scroll", hideQuoteBtn, true);
quoteBtn.addEventListener("mousedown", e => { e.preventDefault(); if (quoteNow) quoteTo(quoteNow); });

/* ⌘⇧D: the journal, at today. Selected answer text goes under today, linked to its card. */
async function weekNote() {
  const sel = selectedCardText();
  let info;
  try {
    if (sel) {
      const wk = await api("POST", "/api/notes/week");
      const ed = EDITORS.get(wk.path);
      if (ed && ed.dirty) await saveFile(wk.path);  // the server writes the file next
      info = await api("POST", "/api/notes/today", {
        text: sel.text.split("\n").map(l => "> " + l).join("\n"),
        source: `[${sel.name} #${sel.cid}](orbit://session/${sel.sid}#${sel.cid})` });
      window.getSelection().removeAllRanges();
      toast(`Added to ${journalName()}`);
    } else info = await api("POST", "/api/notes/week");
  } catch (e) { toast(e.message); return; }
  openNoteAt(info.path, info.heading);
}
function openNoteAt(path, line) {
  if (EMBED) { window.parent.postMessage({ orbit: "open-note-at", path, line }, location.origin); return; }
  S.reveal = { path, line };
  const ed = EDITORS.get(path);
  if (ed && !ed.dirty) reloadFromDisk(path);  // show what was just added; the reveal follows the reload
  openFile(path, { pinned: true });
  refreshTree(S.vault, true);
}
let blSeq = 0;
async function loadBacklinks(path) {
  const box = $("#fback");
  if (!isNote(path)) { box.hidden = true; return; }
  const my = ++blSeq;
  let links = [];
  try { links = (await api("GET", "/api/notes/backlinks?path=" + encodeURIComponent(path))).links; } catch {}
  if (my !== blSeq || S.file !== path) return;
  box.hidden = false;
  box.classList.toggle("open", LS.get("backlinksOpen", true) && links.length > 0);
  box.innerHTML = `<button class="bh">${I("chev", 12)}${links.length ? `${links.length} backlink${links.length === 1 ? "" : "s"}` : "No backlinks"}</button>
    <ul>${links.map(l => `<li data-p="${esc(l.path)}" title="${esc(relTo(S.vault, l.path))}:${l.line}"><span class="n">${esc(noteTitle(l.path))}</span><span class="t">${esc(l.text)}</span></li>`).join("")}</ul>`;
}
$("#fback").addEventListener("click", e => {
  if (e.target.closest(".bh")) { const b = $("#fback"); b.classList.toggle("open"); if (!EMBED) LS.set("backlinksOpen", b.classList.contains("open")); return; }
  const li = e.target.closest("li[data-p]");
  if (li) openFile(li.dataset.p);
});
