"use strict";
/* ===================================================================== Schedules and Calendar (build step 7) */
/* Schedules send a request to a session at set times (orbit/schedule.py). Calendar (D4) is read-only macOS Calendar,
   off until turned on in Settings. Both are main tabs (⌘⌃S, ⌘⌃C; the Misc panel lists them) and can sit in the side pane;
   ⌘⇧C lists today's meetings. */
S.schedules = []; S.settings = {}; S.calCache = new Map(); S.calToday = null;
const XV = { cur: null, week: 0, cal: null, rows: [] };  // the Schedules / Calendar side tab
const pad2 = n => String(n).padStart(2, "0");
const hm24 = t => { const d = new Date(t * 1000); return pad2(d.getHours()) + ":" + pad2(d.getMinutes()); };
function dayStart(n = 0) { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n).getTime() / 1000; }
const eventWhen = ev => ev.all_day ? "all day" : `${hm24(ev.start)}–${hm24(ev.end)}`;  // a meeting's time of day
function dayLabel(t) { const d = new Date(t * 1000); return d.toLocaleDateString("en-US", { weekday: "short" }) + " " + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()); }
function fmtNext(t) {
  if (!t) return "";
  const s = t - Date.now() / 1000;
  if (s < 0) return "due";
  if (s < 3600) return `in ${Math.max(1, Math.round(s / 60))}m`;
  const dd = Math.round((new Date(t * 1000).setHours(0, 0, 0, 0) / 1000 - dayStart()) / 86400);
  if (dd === 0) return "today " + hm24(t);
  if (dd === 1) return "tomorrow " + hm24(t);
  if (dd < 7) return new Date(t * 1000).toLocaleDateString("en-US", { weekday: "short" }) + " " + hm24(t);
  const d = new Date(t * 1000);
  return `${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${hm24(t)}`;
}
const schedState = it => !it.enabled ? "stopped" : it.last && it.last.error ? "notready" : "ready";
function lastLine(it) {
  const l = it.last || {};
  if (!l.at) return "never ran";
  const when = ago(l.at) === "now" ? "just now" : ago(l.at) + " ago";
  if (l.error) return `<span class="err">last ${esc(when)}: ${esc(l.error)}</span>`;
  if (l.skipped) return `last ${esc(when)}: skipped (${esc(l.skipped === true ? "a guard" : l.skipped)})`;
  return `last ${esc(when)}${l.late ? " (late)" : ""}${l.manual ? " (run now)" : ""}${l.cid ? ` · #${l.cid}` : ""}`;
}
function schedulesChanged() {
  if (xKind() === "schedules") renderX();
  else if (!EMBED && S.left.shown && S.left.tab === "misc") renderLeft();
}
setInterval(() => {  // "in 25m" stays true
  if (document.hidden) return;
  if (xOn()) renderX();
  if (!EMBED_X && S.left.shown && S.left.tab === "misc" && !S.filter) renderLeft();
}, 30000);

/* ---------- schedules: dialog and actions */
async function scheduleDialog(it = null, preset = {}) {
  const sessions = [...S.sessions.values()];
  const defSid = preset.sid || (cur() ? cur().id : "");
  // U9: where it goes is one choice (a session · a new session each run · the Fleet); only that choice's field shows
  const targets = sessions.filter(s => !s.fleet).map(s => ({ value: s.id, label: `${s.name} · ${basename(s.cwd)}` }));
  if (it && it.sid && !S.sessions.has(it.sid)) targets.unshift({ value: it.sid, label: it.target_name });
  if (!targets.length) targets.push({ value: "", label: "No session open" });
  let mode = it ? (it.fleet ? "__fleet" : it.sid ? "session" : "__folder") : preset.fleet ? "__fleet" : (defSid && S.sessions.has(defSid) ? "session" : "__folder");
  const target = it && it.sid ? it.sid : (defSid && S.sessions.has(defSid) ? defSid : targets[0].value);
  const v = await dialog({
    title: it ? "Edit schedule" : "New schedule", ok: it ? "Save" : "Create", wide: "mid",
    fields: [
      { key: "when", label: "When: every 2h · daily 09:00 · weekdays 9am · mon,wed 18:00 · in 30m · tomorrow 9am · cron 0 9 * * 1-5", value: it ? it.when : preset.when || "", placeholder: "weekdays 9am" },
      { key: "text", label: "Ask the agent", value: it ? it.text : preset.text || "", placeholder: "Check last night's CI runs and summarize the failures" },
      { key: "target", label: "Session", type: "dropdown", value: target, options: targets },
      { key: "cwd", label: "Folder", value: it && it.cwd ? tilde(it.cwd) + "/" : cur() ? tilde(cur().cwd) + "/" : "~/",
        complete: q => api("GET", "/api/complete-dir?q=" + encodeURIComponent(q)).then(d => d.dirs) },
      { key: "name", label: "Name (optional)", value: it ? it.name : preset.name || "", placeholder: "the request's first words" },
      { key: "check", label: "Watch (optional): a command run at each time with no agent; each new line it prints wakes the agent with it", value: it ? it.check || "" : "", placeholder: "gh pr list --json number,title --jq '.[] | {id: .number, title}'" },
      { key: "max", label: "At most this many runs a day", value: String(it && it.max ? it.max : 24) },
      { key: "until", label: "None after (HH:MM, optional)", value: it ? it.until || "" : "", placeholder: "18:00" },
    ],
    render(fbox, ctl) {
      const field = k => ctl[k].focusEl.closest(".field");
      // Send to: a segmented choice above its one field
      const seg = document.createElement("div"); seg.className = "field";
      seg.innerHTML = `<label>Send to</label><div class="segc" role="radiogroup">${[["session", "A session"], ["__folder", "A new session each run"], ["__fleet", "A Fleet agent"]]
        .map(([v, l]) => `<button type="button" data-mode="${v}"${v === "session" && !targets[0].value ? " disabled" : ""}>${l}</button>`).join("")}</div>`;
      field("target").before(seg);
      const drawMode = () => {
        seg.querySelectorAll("[data-mode]").forEach(b => b.classList.toggle("on", b.dataset.mode === mode));
        field("target").hidden = mode !== "session"; field("cwd").hidden = mode === "session";
        field("cwd").querySelector("label").textContent = mode === "__fleet" ? "Folder (the Fleet agent works there)" : "Folder (a new session there for each run)";
      };
      seg.addEventListener("click", e => { const b = e.target.closest("[data-mode]"); if (b && !b.disabled) { mode = b.dataset.mode; drawMode(); } });
      drawMode();
      // Advanced: the watch, the daily cap, the end time (open when one is set)
      const adv = ["check", "max", "until"].map(field);
      const set = it && (it.check || (it.max && it.max !== 24) || it.until);
      const tog = document.createElement("button"); tog.type = "button"; tog.className = "advtog";
      const drawAdv = open => { adv.forEach(f => f.hidden = !open); tog.innerHTML = `<span style="display:grid;transform:rotate(${open ? 90 : 0}deg)">${I("chev", 11)}</span>Advanced: watch, runs a day, end time`; tog.dataset.open = open ? "1" : ""; };
      tog.addEventListener("click", () => drawAdv(!tog.dataset.open));
      adv[0].before(tog); drawAdv(!!set);
      const hint = document.createElement("div"); hint.className = "hint";
      ctl.when.focusEl.closest(".field").appendChild(hint);
      let seq = 0;
      const check = async () => {
        const my = ++seq, w = ctl.when.get().trim();
        if (!w) { hint.textContent = ""; return; }
        try {
          const r = await api("POST", "/api/schedules/parse", { when: w });
          if (my !== seq) return;
          hint.classList.toggle("bad", !r.next);
          hint.textContent = r.describe + (r.next ? " · next " + fmtNext(r.next) : " · that time has passed");
        } catch (e) { if (my === seq) { hint.classList.add("bad"); hint.textContent = e.message; } }
      };
      ctl.when.focusEl.addEventListener("input", () => { clearTimeout(check.tm); check.tm = setTimeout(check, 200); });
      check();
    },
    validate: async v => {
      const body = { when: v.when, text: v.text, name: v.name, check: v.check.trim(), max: +v.max || 24, until: v.until.trim() };
      if (mode !== "session") { if (!v.cwd.trim()) return "Pick a folder."; body.cwd = v.cwd.trim(); body.sid = ""; body.fleet = mode === "__fleet"; }
      else { body.sid = v.target; body.cwd = ""; body.fleet = false; }
      if (it && it.check && body.check === it.check) delete body.check;  // (unchanged: it keeps what it has seen)
      try { scheduleDialog.saved = await api("POST", it ? `/api/schedules/${it.id}/update` : "/api/schedules", body); }
      catch (e) { return e.message; }
      return null;
    },
  });
  if (v) { const x = scheduleDialog.saved; toast(`${it ? "Saved" : "Scheduled"}: ${x.describe} → ${x.target_name}${x.next ? ", next " + fmtNext(x.next) : ""}`); }
}
function scheduleMenu(it, where) {
  menu([
    { label: "Edit…", run: () => scheduleDialog(it) },
    { label: "Run now", run: () => runSchedule(it) },
    { label: it.enabled ? "Pause" : "Resume", key: "Space", run: () => toggleSchedule(it) },
    { label: "Journal", key: "O", disabled: !it.journal, run: () => openJournal(it) },
    { label: "Open the last run", key: "L", disabled: !(it.last && it.last.sid), run: () => openLastRun(it) },
    { sep: true },
    { label: "Remove…", key: "⌫", danger: true, run: () => deleteSchedule(it) },
  ], where);
}
async function runSchedule(it) {
  try {
    const x = await api("POST", `/api/schedules/${it.id}/run`);
    if (x.last.error) toast("Could not run: " + x.last.error);
    else toast(`Sent to ${x.target_name} as #${x.last.cid} — click to show`, () => openLastRun(x));
  } catch (e) { toast(e.message); }
}
async function toggleSchedule(it) {
  try { const x = await api("POST", `/api/schedules/${it.id}/update`, { enabled: !it.enabled }); toast(x.enabled ? `On: next ${fmtNext(x.next)}` : `“${x.name}” is off`); }
  catch (e) { toast(e.message); }
}
/* A job's journal (<data root>/jobs/<id>.md): each run reads it first and adds what it did. */
function openJournal(it) { if (it.journal) openFile(it.journal, { pinned: true }); else toast("No journal yet: it starts with the first run"); }
function openLastRun(it) { const l = it.last || {}; if (l.sid) openUrl(`orbit://session/${l.sid}${l.cid ? "#" + l.cid : ""}`); else toast("It has not run yet"); }
async function deleteSchedule(it) {
  const ok = await confirmDialog({ title: `Delete the schedule “${it.name}”?`, body: `${it.describe} → ${it.target_name}. The sessions and requests it made stay.`, ok: "Delete", danger: true });
  if (ok) try { await api("POST", `/api/schedules/${it.id}/delete`); } catch (e) { toast(e.message); }
}

/* ---------- calendar: data, settings, meetings */
async function calEvents(from, to, fresh = false) {
  const key = `${from}-${to}`, hit = S.calCache.get(key);
  if (!fresh && hit && Date.now() - hit.t < 60000) return hit.d;
  const d = await api("GET", `/api/calendar?from=${from}&to=${to}${fresh ? "&fresh=1" : ""}`);
  if (!d.error) S.calCache.set(key, { t: Date.now(), d });
  return d;
}
async function loadCalToday(fresh = false) {
  if (EMBED) return;
  if (!S.settings.calendar) S.calToday = { enabled: false, events: [] };
  else {
    if (!S.calToday || S.calToday.enabled === false) S.calToday = null;  // "Loading…"
    // today and tomorrow (the next meeting may be tomorrow)
    try { S.calToday = await calEvents(dayStart(), dayStart(2), fresh); } catch (e) { S.calToday = { enabled: true, error: e.message, events: [] }; }
  }
  if (S.left.shown && S.left.tab === "misc") renderLeft();
  renderMeet();
}
setInterval(() => { if (!document.hidden && !EMBED && S.settings.calendar) loadCalToday(); }, 5 * 60000);
/* Status bar: the meeting now or next today (Calendar on). Click or ⌘⇧C: join it or open its note. */
setInterval(() => { if (!EMBED && !document.hidden) renderMeet(); }, 30000);
async function setCalendar(on) {
  try { S.settings = await api("POST", "/api/settings", { calendar: on }); } catch (e) { toast(e.message); return; }
  if (on) toast("Calendar on (read-only). The first time, macOS asks Orbit for Calendar access.");
}
function eventItems(ev) {
  return [
    { label: "Join", key: ev.link ? "J" : "", disabled: !ev.link, run: () => openUrl(ev.link) },
    { label: "Meeting note", key: "N", run: () => meetingNote(ev) },
    { label: "Prep: an agent prepares me, into the note", key: "P", run: () => prepMeeting(ev) },
    { label: "Copy details", run: () => copyText(`${ev.title}\n${dayLabel(ev.start)} ${eventWhen(ev)}${ev.location ? "\n" + ev.location : ""}${ev.link ? "\n" + ev.link : ""}`) },
    /^https?:/i.test(ev.url || "") && ev.url !== ev.link ? { label: "Open the event's link", run: () => openUrl(ev.url) } : null,
  ].filter(Boolean);
}
function eventMenu(ev, where) { menu(eventItems(ev), where); }
/* A note per meeting: Meetings/<date> <title>.md in the vault, made once from the event. */
async function meetingNote(ev) {  // as a tab (the Calendar tab shows it under the list too: calnote.js)
  try { openNoteAt(await ensureMeetingNote(ev), "## Notes"); } catch (e) { toast(e.message); }
}

/* ---------- side tabs: Schedules and Calendar */
function toggleSideKind(kind, { focus = true } = {}) {
  if (EMBED) { window.parent.postMessage({ orbit: "open-side", spec: { kind } }, location.origin); return; }
  toggleSideOf(kind, { kind }, { focus });
}
async function loadX(fresh = false) {
  if (!xOn()) return;
  if (xKind() !== "calendar") { renderX(); return; }
  if (!S.settings.calendar) { XV.cal = { enabled: false }; renderX(); return; }
  const from = calFrom(), days = calDays();
  if (!XV.cal || XV.cal.from !== from) { XV.cal = { loading: true, from }; renderX(); }
  let d;
  try { d = await calEvents(from, from + days * 86400 + 7200, fresh); } catch (e) { d = { error: e.message }; }
  if (from !== calFrom()) return;  // the user moved on
  XV.cal = { ...d, from };
  renderX();
}
setInterval(() => { if (!document.hidden && xKind() === "calendar") loadX(); }, 5 * 60000);
function renderX() {
  if (!xOn()) return;
  if (xKind() === "home") return renderHome();  // (a group's home: home.js)
  if (typeof homeUnshown === "function") homeUnshown();
  if (xKind() === "schedules") renderSchedulesX(); else renderCalendarX();
}
function drawX(head, body, foot) {
  if (xKind() !== "calendar" && typeof calNoteHide === "function") calNoteHide();  // (the meeting note: the Calendar's)
  $("#xhead").innerHTML = head; $("#xbody").innerHTML = body; $("#xfoot").textContent = foot;
  const el = $("#xbody .xrow.kc"); if (el) el.scrollIntoView({ block: "nearest" });
}
function renderSchedulesX() {
  const xs = S.schedules;
  XV.rows = xs.map(it => ({ k: it.id, it }));
  if (!XV.rows.some(r => r.k === XV.cur)) XV.cur = XV.rows.length ? XV.rows[0].k : null;
  const head = `<span class="s">${xs.length ? `${xs.filter(x => x.enabled).length} on · ${xs.length} in all` : ""}</span><span class="acts"><button class="qbtn" data-x="new" title="New schedule (N)">${I("plus", 13)}New</button></span>`;
  const body = xs.length ? XV.rows.map(({ k, it }) =>
    `<div class="xrow${k === XV.cur ? " kc" : ""}${it.enabled ? "" : " off"}" data-xk="${esc(k)}" title="${esc(it.text)}"><span class="dot ${schedState(it)}"></span><div class="m">` +
    `<div class="l1"><span class="nm">${esc(it.name)}</span><span class="r">${it.enabled ? "next " + esc(fmtNext(it.next)) : "off"}</span>` +
    `<span class="racts"><button class="ibtn sm" data-sa="run" title="Run now (R)">${I("play", 13)}</button><button class="ibtn sm" data-sa="toggle" title="${it.enabled ? "Pause" : "Resume"} (Space)">${I(it.enabled ? "pause" : "play", 13)}</button><button class="ibtn sm" data-sa="edit" title="Edit (⏎)">${I("pencil", 13)}</button><button class="ibtn sm" data-sa="more" title="More: journal, last run, remove (⌘E)">${I("more", 13)}</button></span></div>` +
    `<div class="l2">${it.check ? `<span class="watch" title="A watch: ${esc(it.check)}">watch</span> ` : ""}${esc(it.describe)} → ${esc(it.target_name)}${it.max && it.max !== 24 ? ` · ≤${it.max}/day` : ""}${it.until ? ` · until ${esc(it.until)}` : ""} · ${lastLine(it)}${it.journal ? ` · <span class="jl" title="O opens it">journal</span>` : ""}</div></div></div>`).join("")
    : `<div class="xempty"><h3>No schedules yet</h3>A schedule sends a request to a session at set times: every 2h, weekdays 9am, tomorrow 9am.<br>N makes one. Agents can wake themselves up: <code>orbit-agent wake 30m "check CI"</code>.</div>`;
  drawX(head, body, "⏎ edit · Space pause · R run now · ⌘E more");
}
function xMove(how) {
  if (!XV.rows.length) return;
  let i = XV.rows.findIndex(r => r.k === XV.cur);
  i = how === "home" ? 0 : how === "end" ? XV.rows.length - 1 : Math.max(0, Math.min(XV.rows.length - 1, i + how));
  XV.cur = XV.rows[i].k;
  renderX();
}
function xMenu(where) {
  const r = XV.rows.find(x => x.k === XV.cur); if (!r) return;
  where = where || { anchor: $("#xbody .xrow.kc [data-cmore]") || $("#xbody .xrow.kc") };  // (a meeting: by its ⋯)
  if (r.it) scheduleMenu(r.it, where); else calMenu(r, where);
}
function xWeek(d) { XV.week = d === 0 ? 0 : XV.week + d; XV.cur = null; loadX(); }
/* Keys in the Schedules / Calendar side tab. */
function xKey(e, combo) {
  if (xKind() === "home") return homeKey(e, combo);  // (home.js: its widgets, not a list)
  const pg = Math.max(1, Math.floor(($("#xbody").clientHeight || 400) / 44) - 1);
  const mv = { ArrowDown: 1, ArrowUp: -1, "C:KeyN": 1, "C:KeyP": -1, Home: "home", End: "end", "AS:Comma": "home", "AS:Period": "end",
               PageDown: pg, PageUp: -pg, "C:KeyV": pg, "A:KeyV": -pg }[combo];
  if (mv !== undefined) { xMove(mv); return true; }
  if (combo === "C:KeyL") { recenter($("#xbody"), $("#xbody .xrow.kc")); return true; }
  const r = XV.rows.find(x => x.k === XV.cur);
  if (xKind() === "calendar") return calKeyDown(combo);  // calview.js
  if (combo === "KeyN") { scheduleDialog(); return true; }
  if (!r) return false;
  if (combo === "Enter") { scheduleDialog(r.it); return true; }
  if (combo === "Space") { toggleSchedule(r.it); return true; }
  if (combo === "KeyR") { runSchedule(r.it); return true; }
  if (combo === "KeyO") { openJournal(r.it); return true; }
  if (combo === "KeyL") { openLastRun(r.it); return true; }
  if (combo === "Backspace" || combo === "Delete") { deleteSchedule(r.it); return true; }
  return false;
}
$("#xview").addEventListener("click", e => {
  const b = e.target.closest("[data-x]");
  if (b) {
    const a = b.dataset.x;
    if (a === "new") scheduleDialog();
    else if (a === "on") setCalendar(true);
    else if (a === "retry") { S.calCache.clear(); loadX(true); }
    else if (a in { prev: 1, next: 1, today: 1 }) xWeek({ prev: -1, next: 1, today: 0 }[a]);
    return;
  }
  const row = e.target.closest("[data-xk]");
  const sa = e.target.closest("[data-sa]");  // a row's own buttons (U11)
  if (sa && row) {
    XV.cur = row.dataset.xk; const it = (XV.rows.find(r => r.k === XV.cur) || {}).it; if (!it) return;
    const a = sa.dataset.sa;
    if (a === "run") runSchedule(it); else if (a === "toggle") toggleSchedule(it); else if (a === "edit") scheduleDialog(it);
    else { renderX(); scheduleMenu(it, { anchor: $("#xbody .xrow.kc [data-sa=more]") || sa }); }
    return;
  }
  if (row) { XV.cur = row.dataset.xk; renderX(); $("#xbody").focus({ preventScroll: true }); }
});
$("#xview").addEventListener("dblclick", e => { if (xKind() !== "calendar" && e.target.closest("[data-xk]")) xKey(e, "Enter"); });
$("#xview").addEventListener("contextmenu", e => {
  const row = e.target.closest("[data-xk]"); if (!row) return;
  e.preventDefault(); XV.cur = row.dataset.xk; renderX(); xMenu({ at: { x: e.clientX, y: e.clientY } });
});

/* ---------- the Misc panel: schedules, then today's meetings */
function miscRows() {  // one row per tab: its icon, its name, a hint (what is next there)
  const q = S.filter, now = Date.now() / 1000;
  const cal = S.calToday, real = cal && cal.events ? cal.events.filter(e => !e.noise && !e.all_day && e.end > now) : [];
  const next = real.sort((a, b) => a.start - b.start)[0];
  const calHint = !S.settings.calendar ? "off" : !cal ? "…" : cal.error ? "error" : next ? `${next.start <= now ? "now" : next.start < dayStart(1) ? hm24(next.start) : "tomorrow " + hm24(next.start)} ${next.title || "(no title)"}` : "none";
  const on = S.schedules.filter(x => x.enabled && x.next).sort((a, b) => a.next - b.next);
  const rows = [
    { kind: "tab", k: "t:calendar", name: "Calendar", icon: "calendar", path: "orbit:calendar", key: "⌘⌃C", hint: calHint },
    { kind: "tab", k: "t:schedules", name: "Schedules", icon: "clock", path: "orbit:schedules", key: "⌘⌃S", hint: on.length ? fmtNext(on[0].next) : "none" },
  ];
  return rows.filter(r => !q || fuzzy(q, r.name) >= 0);
}
function renderMiscPanel() {
  if (S.calToday == null && S.settings.calendar && !renderMiscPanel.asked) { renderMiscPanel.asked = true; loadCalToday(); }
  PROWS = miscRows();
  if (!PROWS.some(r => r.k === S.pcursor)) S.pcursor = (PROWS[0] || {}).k || null;
  plist.innerHTML = PROWS.length ? PROWS.map(r => `<div class="prow misc${r.k === S.pcursor ? " kc" : ""}${S.file === r.path ? " cur" : ""}" data-k="${esc(r.k)}" title="${esc(r.name)} (${esc(r.key)}): ⏎ opens it as a tab">` +
    `<span class="mi">${I(r.icon, 13)}</span><span class="nm">${esc(r.name)}</span><span class="fd">${esc(r.hint)}</span></div>`).join("")
    : `<div class="pempty">Nothing matches “${esc(S.filter)}”.</div>`;
  revealCursor();
}
function openMiscRow(r) {
  if (r.kind === "tab") openFile(r.path, { pinned: true });
}
