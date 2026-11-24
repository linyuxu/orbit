"use strict";
/* ===================================================================== the Calendar tab's meeting note */
/* A pane pinned under the list (the list scrolls, the pane stays): the note of the meeting the cursor is on, read-only
   while you move; ⏎ edits it (the Markdown editor, autosaved), Esc saves and goes back to the list. Where a note is
   comes from the `meeting_notes` setting (the server: POST /api/calendar/note). P asks a Fleet helper to prepare you
   for the meeting: its brief is appended to the note (`## Prep · …`) without disturbing what you type. Its top edge
   drags its height. The width stays the list's: the side pane beside the Calendar is free for a session. */
const XN = { open: null, path: null, ev: null, editing: false, seq: 0, text: null, timer: 0, cache: new Map(), prepping: new Map() };
const XNBOX = document.getElementById("xnote");  // one box, under the list
const xnote = () => XNBOX;
const calNoteOn = () => xKind() === "calendar";
const inCalNote = () => !!(document.activeElement && document.activeElement.closest && document.activeElement.closest("#xnote"));

/* The meeting's note, made once from the event (the server writes its header: when, where, who, the link; ## Notes). */
async function ensureMeetingNote(ev) {
  const r = await api("POST", "/api/calendar/note", { event: ev, create: true });
  XN.cache.set(calKey(ev), { path: r.path, text: r.text || "" });
  return r.path;
}
/* P (or Prep in a menu): a Fleet helper prepares you; its brief lands in the note. */
async function prepMeeting(ev) {
  try {
    const path = await ensureMeetingNote(ev);
    if (XN.prepping.has(path)) { toast(`${XN.prepping.get(path)} is already preparing “${ev.title || "the meeting"}”`); return; }
    const r = await api("POST", "/api/calendar/prep", { event: ev, path });
    XN.prepping.set(path, r.session.name);
    if (!EMBED) openSide({ kind: "session", sid: r.session.id }, { focus: false });  // the helper at work, beside
    toast(`${r.session.name} is preparing “${ev.title || "the meeting"}”: the brief goes into its note`);
    if (calNoteOn() && XN.open === calKey(ev)) { XN.path = path; if (XN.text == null) XN.text = (XN.cache.get(calKey(ev)) || {}).text || ""; drawCalNote(); }
  } catch (e) { toast(e.message); }
}

/* The pane shows the cursor's meeting; hidden in Schedules, with no meeting, or with the Calendar off. */
function calNoteEvent() { const r = XV.rows.find(x => x.k === XN.open); return r && r.ev ? r.ev : null; }
function calNoteHide() {
  const box = xnote(); if (!box) return;
  if (XN.editing) leaveCalNote(false);
  box.hidden = true; XN.open = null;
  $("#xview").classList.remove("xpin");
}
/* After the list is drawn: the cursor's meeting's note (fetched after a short pause while the cursor moves). */
async function showCalNote() {
  const box = xnote();
  if (!box) return;
  const r = calNoteOn() && XV.rows.find(x => x.k === XV.cur), ev = r && r.ev;
  if (!ev) return calNoteHide();
  box.hidden = false;
  $("#xview").classList.add("xpin");
  if (!box.style.height) { const h = LS.get("calNoteH", 0); if (h) box.style.height = h + "px"; }
  if (XN.open === r.k) { XN.ev = ev; return; }  // the same meeting: as it is (an editor stays open)
  if (XN.editing) await leaveCalNote(false);
  const hit = XN.cache.get(r.k);
  XN.open = r.k; XN.ev = ev; XN.path = hit ? hit.path : null; XN.text = hit ? hit.text : null;
  drawCalNote();
  const my = ++XN.seq;
  clearTimeout(XN.timer);
  XN.timer = setTimeout(async () => {
    let d;
    try { d = await api("POST", "/api/calendar/note", { event: ev }); } catch (e) { d = { error: e.message }; }
    if (my !== XN.seq || XN.editing) return;
    if (d.error) { XN.text = null; XN.err = d.error; }
    else { XN.err = null; XN.path = d.path; XN.text = d.exists ? d.text || "" : ""; XN.cache.set(r.k, { path: d.path, text: XN.text }); }
    drawCalNote();
  }, hit ? 250 : 90);
}
function drawCalNote() {
  const box = xnote(), ev = XN.ev;
  if (!box || !ev) return;
  const prep = XN.path && XN.prepping.get(XN.path);
  const what = `<b>${esc(ev.title || "(no title)")}</b> · ${esc(calRange(ev))}`;
  const head = `<div class="xngrip" title="Drag: the pane's height"></div><div class="xnhead">` +
    `<span class="s">${what} · ${prep ? `${esc(prep)} is preparing…` : XN.editing ? "editing, autosaved · Esc: back to the list" : "⏎ to write"}</span>` +
    `<span class="acts"><button class="btn sm" data-xn="edit" title="Edit the note (⏎)">${XN.editing ? "Done" : "Edit"}</button>` +
    `<button class="btn sm" data-xn="tab" title="Open the note as a file tab (N)">Open as file</button></span></div>`;
  if (XN.editing) { const h = box.querySelector(".xnhead"); if (h) h.outerHTML = head.replace(/^<div class="xngrip"[^>]*><\/div>/, ""); box.querySelector(".xnbody").classList.add("editing"); return; }
  const body = XN.err ? `<div class="xnempty">${esc(XN.err)}</div>`
    : XN.text == null ? `<div class="xnempty">Loading…</div>`
    : XN.text.trim() ? `<div class="xnview md">${renderMd(XN.text)}</div>`
    : `<div class="xnempty">No note yet. ⏎ starts one (when, who, the link) · P has an agent prepare you, into the note.</div>`;
  box.innerHTML = head + `<div class="xnbody">${body}</div>`;
}
/* ⏎: the note in the Markdown editor (made first if it is not there yet). */
async function editCalNote() {
  const ev = calNoteEvent(); if (!ev) return;
  let path;
  try { path = await ensureMeetingNote(ev); } catch (e) { toast(e.message); return; }
  XN.path = path; XN.ev = ev; XN.editing = true;
  drawCalNote();
  const box = xnote(), body = box.querySelector(".xnbody");
  body.innerHTML = ""; body.classList.add("editing");
  let ed = EDITORS.get(path);
  if (!ed) {
    ed = { kind: "loading", host: document.createElement("div"), dirty: false };
    ed.host.className = "host md";
    EDITORS.set(path, ed);
    try { mountFile(path, ed, await api("GET", "/api/file?path=" + encodeURIComponent(path))); }
    catch (e) { EDITORS.delete(path); XN.editing = false; toast(e.message); return; }
  }
  body.appendChild(ed.host);
  ed.host.hidden = false;
  if (ed.ed) {  // where you write: the end of the note (under ## Notes in a fresh one)
    const v = ed.ed.view;
    v.dispatch({ selection: { anchor: v.state.doc.length }, scrollIntoView: true });
    ed.ed.focus();
  }
}
/* Esc (or Done): save, back to the read-only note and the list. */
async function leaveCalNote(focusList = true) {
  const path = XN.path, ed = EDITORS.get(path);
  XN.editing = false;
  if (ed) {
    if (ed.dirty && !(await saveFile(path))) {  // could not save (the file changed under it): resync, then save
      await resyncNote(path);
      if (ed.dirty) await saveFile(path);
    }
    XN.text = ed.ed ? ed.ed.getDoc() : XN.text;
    disposeEditor(path);
  }
  if (XN.open && path) XN.cache.set(XN.open, { path, text: XN.text || "" });
  drawCalNote();
  if (focusList) $("#xbody").focus({ preventScroll: true });
}
/* The agent's brief arrived (or any append to a note): an open editor takes it at the end, where you are stays. */
function noteAppended(m) {
  XN.prepping.delete(m.path);
  for (const [k, c] of XN.cache) if (c.path === m.path) XN.cache.set(k, { ...c, text: (c.text || "") + m.text });
  const ed = EDITORS.get(m.path);
  if (ed && ed.ed) {
    const v = ed.ed.view, end = v.state.doc.length;
    ed.applying = true;
    v.dispatch({ changes: { from: end, insert: m.text } });  // the selection maps: the cursor stays where it was
    ed.applying = false;
    resyncNote(m.path);
  }
  if (calNoteOn() && XN.path === m.path) {
    if (!XN.editing) { XN.text = (XN.cache.get(XN.open) || {}).text ?? (XN.text || "") + m.text; }
    drawCalNote();
  }
}
/* After an append: once a save in flight settles, the file on disk is the editor's base (its text has the append now);
   whatever the editor has beyond it is saved on top. A save that crossed the append (409) is resolved the same way. */
async function resyncNote(path) {
  const ed = EDITORS.get(path);
  if (!ed || !ed.ed) return;
  for (let i = 0; ed.saving && i < 200; i++) await new Promise(r => setTimeout(r, 50));
  let f;
  try { f = await api("GET", "/api/file?path=" + encodeURIComponent(path)); } catch { return; }
  if (EDITORS.get(path) !== ed || !ed.ed) return;
  ed.hash = f.hash; ed.mtime = f.mtime; ed.size = f.size; ed.conflict = null;
  if (ed.ed.getDoc() === f.text) { ed.dirty = false; clearTimeout(ed.timer); }
  else { ed.dirty = true; clearTimeout(ed.timer); ed.timer = setTimeout(() => saveFile(path), 300); }
}
function prepFailed(m) {
  XN.prepping.delete(m.path);
  if (calNoteOn() && XN.path === m.path) drawCalNote();
  if (!EMBED) toast(`Prep did not finish: ${m.error}`);
}
{
  xnote().addEventListener("click", e => {
    const b = e.target.closest("[data-xn]"); if (!b) return;
    const ev = calNoteEvent(); if (!ev) return;
    if (b.dataset.xn === "edit") { if (XN.editing) leaveCalNote(); else editCalNote(); }
    else if (b.dataset.xn === "prep") prepMeeting(ev);
    else if (b.dataset.xn === "tab") meetingNote(ev);
  });
  xnote().addEventListener("dblclick", e => { if (!XN.editing && e.target.closest(".xnbody")) editCalNote(); });
  xnote().addEventListener("pointerdown", e => {  // its top edge: the pane's height (kept on this screen)
    if (!e.target.closest(".xngrip")) return;
    e.preventDefault();
    const box = xnote(), y0 = e.clientY, h0 = box.getBoundingClientRect().height, k = rectScale();
    const max = () => $("#xview").getBoundingClientRect().height / k - 140;
    const move = ev => { box.style.height = Math.round(Math.max(120, Math.min(max(), h0 / k + (y0 - ev.clientY) / k))) + "px"; };
    const up = () => { removeEventListener("pointermove", move); removeEventListener("pointerup", up); LS.set("calNoteH", parseInt(box.style.height) || 0); };
    addEventListener("pointermove", move); addEventListener("pointerup", up);
  });
}
