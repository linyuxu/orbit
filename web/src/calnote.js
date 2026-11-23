"use strict";
/* ===================================================================== the Calendar tab's meeting note */
/* A meeting opens (a click, or ⏎) to show its note right under it (Meetings/<date> <title>.md in the vault), read-only;
   ⏎ again edits it (the Markdown editor, autosaved), Esc stops editing, Esc again closes it. P asks a Fleet helper to
   prepare you for the meeting: its brief is appended to the note (`## Prep · …`) without disturbing what you type.
   The width stays the list's: the side pane beside the Calendar is free for a session. */
const XN = { open: null, path: null, ev: null, editing: false, seq: 0, text: null, prepping: new Map() };
const XNBOX = document.getElementById("xnote");  // one box, moved under the open meeting (an open editor goes with it)
const xnote = () => XNBOX;
const calNoteOn = () => xKind() === "calendar";
const inCalNote = () => !!(document.activeElement && document.activeElement.closest && document.activeElement.closest("#xnote"));

function meetingNoteName(ev) {
  const d = new Date(ev.start * 1000);
  const ymd = `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  const title = (ev.title || "Meeting").replace(/[\/\\:*?"<>|#^\[\]]+/g, " ").replace(/\s+/g, " ").trim() || "Meeting";
  return `Meetings/${ymd} ${title}`;
}
const meetingNotePath = ev => S.vault ? `${S.vault}/${meetingNoteName(ev)}.md` : null;
/* The meeting's note, made once from the event (a header: when, where, who, the link; then ## Notes). */
async function ensureMeetingNote(ev) {
  const r = await api("POST", "/api/notes/create", { name: meetingNoteName(ev) });
  if (r.created) {
    const where = [ev.calendar, ev.location].filter(Boolean).join(" · ");
    const lines = [`# ${ev.title || "Meeting"}`, "", `- **When** ${dayLabel(ev.start)} ${eventWhen(ev)}${where ? " · " + where : ""}`];
    if (ev.organizer) lines.push(`- **Organizer** ${ev.organizer}`);
    if (ev.people && ev.people.length) lines.push(`- **With** ${ev.people.join(", ")}`);
    else if (ev.attendees) lines.push(`- **With** ${ev.attendees} people`);
    if (ev.link) lines.push(`- **Join** ${ev.link}`);
    lines.push("", "## Notes", "", "");
    await api("POST", "/api/file", { path: r.path, text: lines.join("\n"), force: true });
  }
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
    if (calNoteOn() && XN.path === path) drawCalNote();
  } catch (e) { toast(e.message); }
}

/* The note shows under the opened meeting the cursor is on (it follows the cursor among the opened ones). */
const calNoteKey = () => XV.opened && XV.opened.has(XV.cur) ? XV.cur : null;
function calNoteEvent() { const r = XV.rows.find(x => x.k === calNoteKey()); return r && r.ev ? r.ev : null; }
/* After the list is drawn: the open meeting's note goes under its row (its box keeps an editor that is open). */
async function showCalNote() {
  const box = xnote();
  if (!calNoteOn() || !box) return;
  XN.open = calNoteKey();
  const ev = calNoteEvent(), row = ev && ($(`#xbody .mopen[data-for="${CSS.escape(XN.open)}"]`) || $(`#xbody .xrow[data-xk="${CSS.escape(XN.open)}"]`));
  box.hidden = !row;
  if (!row) { if (XN.editing) leaveCalNote(false); $("#xview").appendChild(box); return; }
  row.after(box);
  const path = meetingNotePath(ev);
  if (XN.editing && XN.path === path) return;  // being edited: leave it be
  if (XN.editing) await leaveCalNote(false);
  if (XN.path === path && XN.text != null) return drawCalNote();
  XN.path = path; XN.ev = ev; XN.text = null;
  const my = ++XN.seq;
  drawCalNote();
  let text = "";
  try { text = (await api("GET", "/api/file?path=" + encodeURIComponent(path))).text || ""; } catch { text = ""; }
  if (my !== XN.seq) return;
  XN.text = text;
  drawCalNote();
}
function drawCalNote() {
  const box = xnote(), ev = XN.ev;
  if (!box || !ev) return;
  const prep = XN.prepping.get(XN.path);
  const head = `<div class="xnhead">` +
    `<span class="s">${prep ? `${esc(prep)} is preparing…` : XN.editing ? "Notes · editing, autosaved · Esc: back to the list" : "Notes · ⏎ to write"}</span>` +
    `<span class="acts"><button class="btn sm" data-xn="edit" title="Edit the note (⏎)">${XN.editing ? "Done" : "Edit"}</button>` +
    `<button class="btn sm" data-xn="tab" title="Open the note as a file tab (N)">Open as file</button></span></div>`;
  if (XN.editing) { const h = box.querySelector(".xnhead"); if (h) h.outerHTML = head; box.querySelector(".xnbody").classList.add("editing"); return; }
  const body = XN.text == null ? `<div class="xnempty">Loading…</div>`
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
  drawCalNote();
  if (focusList) $("#xbody").focus({ preventScroll: true });
}
/* The agent's brief arrived (or any append to a note): an open editor takes it at the end, where you are stays. */
function noteAppended(m) {
  XN.prepping.delete(m.path);
  const ed = EDITORS.get(m.path);
  if (ed && ed.ed) {
    const v = ed.ed.view, end = v.state.doc.length;
    ed.applying = true;
    v.dispatch({ changes: { from: end, insert: m.text } });  // the selection maps: the cursor stays where it was
    ed.applying = false;
    resyncNote(m.path);
  }
  if (calNoteOn() && XN.path === m.path) {
    if (!XN.editing) { XN.text = (XN.text || "") + m.text; }
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
}
