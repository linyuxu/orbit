"use strict";
/* ===================================================================== the Calendar view: today and tomorrow */
/* A main tab (⌘⌃C) or a side tab: today's and tomorrow's meetings (W: a week, paged), one 44px row each — the time,
   the organizer's initial, the title, `now` / `overlap`, "organizer · accepted/total", Join. Blocks (all day,
   declined, canceled, free, untitled, placeholders, your own) hide behind a per-day "N blocks". A meeting opens in
   place (→, a click; several can be open): its people, the invitation, related sessions, Prep / Copy link; the open
   meeting the cursor is on shows its note under it (calnote.js). The side pane stays free for a session. */
XV.span = LS.get("calSpan", "days");  // "days": today + tomorrow · "week": 7 days from the paged week's start
XV.opened = new Set();                // meetings opened in place
XV.blocks = new Set();                // days (their start) whose blocks are shown
const CAL_STOP = new Set(("the and for with sync weekly meeting meet call chat daily team standup review update one "
  + "sync-up catch catchup check checkin office hours").split(" "));
const calKey = ev => ev.id + ":" + ev.start;
const calRange = ev => ev.all_day ? "all day" : `${hm24(ev.start)}–${hm24(ev.end)}`;
const calInitial = s => (String(s || "?").trim().replace(/^[^\p{L}\p{N}]+/u, "")[0] || "?").toUpperCase();
/* A person's square: their photo when a plugin says who they are (Plugin API 1.2, [people]: GET /api/people), else the
   initial. Names are asked for once, in a batch, after the draw; what comes back redraws what shows them. */
const PEOPLE = new Map(), PEOPLE_ASK = new Set();  // normalised name -> {avatar, handle} | null (not known)
let PEOPLE_OFF = false;
const personKey = n => String(n || "").split(/\s+/).filter(Boolean).join(" ");
function calAvatar(name, cls = "") {
  const k = personKey(name), p = PEOPLE.get(k);
  if (k && !PEOPLE.has(k) && !PEOPLE_OFF) { PEOPLE_ASK.add(k); clearTimeout(calAvatar.t); calAvatar.t = setTimeout(askPeople, 60); }
  const tip = esc(name || "") + (p && p.handle ? ` (@${esc(p.handle)})` : "");
  return p && p.avatar ? `<span class="mav${cls}" title="${tip}"><img src="${esc(p.avatar)}" alt="" loading="lazy" referrerpolicy="no-referrer"></span>`
    : `<span class="mav${cls}" title="${tip}">${esc(calInitial(name))}</span>`;
}
async function askPeople() {
  const names = [...PEOPLE_ASK]; PEOPLE_ASK.clear();
  if (!names.length || PEOPLE_OFF) return;
  names.forEach(n => PEOPLE.set(n, null));  // (asked: not again while the answer is on its way)
  let got;
  try { got = (await api("POST", "/api/people", { names })).people || {}; }
  catch { PEOPLE_OFF = true; return; }  // (another machine's page: 403; initials stay)
  let any = false;
  for (const [n, v] of Object.entries(got)) if (v && (v.avatar || v.handle)) { PEOPLE.set(personKey(n), v); any = true; }
  if (!any) return;
  if (typeof xKind === "function" && xKind() === "calendar") renderX();
  const pop = document.getElementById("meetpop"); if (pop && modal && modal.kind === "meetpop" && modal.redraw) modal.redraw();
}
function calAccepted(ev) {
  const g = ev.guests || [];
  return g.length ? `${g.filter(x => x.status === "accepted").length}/${g.length}` : ev.attendees ? `${ev.attendees}` : "";
}
/* Sessions that may be about the meeting: a word of its title (3 letters or more, not "sync", "weekly"…) in the
   session's name or its last answer; four at most. */
function calRelated(ev) {
  const words = (ev.title || "").toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(w => w.length >= 3 && !CAL_STOP.has(w));
  if (!words.length) return [];
  return [...S.sessions.values()].filter(s => !s.fleet && !s.closed && words.some(w =>
    (s.name + " " + (s.last ? s.last.text : "")).toLowerCase().includes(w))).slice(0, 4);
}
function calLinkify(text) {
  return esc(text || "").replace(/https?:\/\/[^\s<>"')]+/g, u => `<a href="${u}" data-url="${u}">${u}</a>`);
}
/* The range on screen. */
function calFrom() { return XV.span === "week" ? dayStart(XV.week * 7) : dayStart(); }
function calDays() { return XV.span === "week" ? 7 : 2; }
function calDayName(d0) {
  const today = dayStart();
  return d0 === today ? "Today" : d0 === dayStart(1) ? "Tomorrow" : dayLabel(d0);
}

function renderCalendarX() {
  const c = XV.cal || {}, now = Date.now() / 1000;
  const evs = c.events || [], real = evs.filter(e => !e.noise);
  const n0 = real.filter(e => e.start >= dayStart() && e.start < dayStart(1)).length;
  const n1 = real.filter(e => e.start >= dayStart(1) && e.start < dayStart(2)).length;
  const title = XV.span === "week" ? `${esc(dayLabel(calFrom()))} – ${esc(dayLabel(dayStart(XV.week * 7 + 6)))}`
    : `Calendar · today ${n0} · tomorrow ${n1}`;
  const head = `<span class="t">${I("calendar", 14)} ${title}</span>` +
    `<span class="acts">${XV.span === "week" ? `<button class="ibtn sm" data-x="prev" title="Previous week (⌥←)"><span style="transform:rotate(180deg);display:grid">${I("chev", 12)}</span></button><button class="qbtn" data-x="today" title="This week (T)">Today</button><button class="ibtn sm" data-x="next" title="Next week (⌥→)">${I("chev", 12)}</button>` : ""}` +
    `${EMBED ? "" : `<button class="qbtn" data-x="attach" title="A session beside the Calendar, in the side pane">Attach session ${I("caret", 12)}</button>`}` +
    `<button class="qbtn" data-x="span" title="Today and tomorrow, or the week (W)">${XV.span === "week" ? "2 days" : "Week"}</button>` +
    `<button class="ibtn sm" data-x="app" title="Open Calendar.app">${I("external", 14)}</button><button class="ibtn sm" data-x="retry" title="Refresh (R)">${I("refresh", 14)}</button></span>`;
  XV.rows = [];
  let body;
  if (c.enabled === false) body = `<div class="xempty"><h3>Calendar is off</h3>Turned on, it shows your macOS Calendar events here, in Misc, and in ⌘⇧C (today's meetings: join, or a meeting note). Read-only: Orbit never changes your calendars.<br><br><button class="btn" data-x="on">Turn on</button></div>`;
  else if (c.loading) body = `<div class="xempty">Loading… (the first time, macOS asks Orbit for Calendar access)</div>`;
  else if (c.error) body = `<div class="xempty"><h3>Cannot read the calendar</h3>${esc(c.error)}<br><br><button class="btn" data-x="retry">Try again</button></div>`;
  else {
    const next = real.filter(e => e.end > now).sort((a, b) => a.start - b.start)[0];  // the current or next meeting
    const parts = [];
    for (let i = 0; i < calDays(); i++) {
      const off = (XV.span === "week" ? XV.week * 7 : 0) + i, d0 = dayStart(off), d1 = dayStart(off + 1);
      const inDay = evs.filter(e => (e.start >= d0 && e.start < d1) || (i === 0 && e.start < d0 && e.end > d0));
      const meet = inDay.filter(e => !e.noise), blocks = inDay.filter(e => e.noise), showB = XV.blocks.has(d0) || XV.all;
      parts.push(`<div class="mday">${esc(calDayName(d0).toUpperCase())}${blocks.length ? `<span class="mblk" data-blk="${d0}" title="Busy, all-day, declined, canceled, untitled and your own blocks (B)">${blocks.length} block${blocks.length === 1 ? "" : "s"} · ${showB ? "hide" : "show"}</span>` : ""}</div>`);
      const shown = (showB ? inDay : meet).slice().sort((a, b) => a.start - b.start);
      if (!shown.length) parts.push(`<div class="mnone">No meetings</div>`);
      for (const ev of shown) {
        const k = calKey(ev);
        XV.rows.push({ k, ev });
        const live = ev.start <= now && now < ev.end, over = !ev.noise && meet.some(o => o !== ev && o.start < ev.end && ev.start < o.end);
        const cls = (ev.noise ? " block" : "") + (ev.end <= now ? " past" : "") + (next && calKey(next) === k ? " next" : "")
          + (k === XV.cur ? " kc" : "") + (XV.opened.has(k) ? " open" : "");
        const meta = [ev.organizer, calAccepted(ev)].filter(Boolean).join(" · ");
        parts.push(`<div class="xrow mrow${cls}" data-xk="${esc(k)}"><span class="tm">${esc(calRange(ev))}</span>${calAvatar(ev.organizer || ev.title)}` +
          `<span class="nm">${esc(ev.title || "(no title)")}</span>${live ? `<span class="mb now">now</span>` : ""}${over ? `<span class="mb ov">overlap</span>` : ""}` +
          `<span class="mt">${esc(meta)}</span>${ev.link ? `<button class="btn sm quiet mjoin" data-join="${esc(k)}" title="Join (J)">Join</button>` : `<span class="mjoin0"></span>`}` +
          `<span class="racts"><button class="ibtn sm" data-cmore="${esc(k)}" title="Prep, copy link, the note… (⌘E)">${I("more", 13)}</button></span></div>`);
        if (XV.opened.has(k)) parts.push(calOpenHtml(ev, k));
      }
    }
    body = parts.join("");
    if (XV.pending) {  // opened from the status bar / ⌘⇧C: that meeting
      const hit = XV.rows.find(r => r.k === XV.pending);
      if (hit) { XV.cur = hit.k; XV.opened.add(hit.k); XV.pending = null; return renderCalendarX(); }
    }
    if (XV.rows.length && !XV.rows.some(r => r.k === XV.cur)) {
      XV.cur = (next && XV.rows.find(r => r.k === calKey(next)) || XV.rows[0]).k;
      return renderCalendarX();
    }
  }
  drawX(head, body, "→ open · ⏎ its note · J join · ⌘E more");
  showCalNote();
}
/* An opened meeting: who (accepted: a green ring; declined: struck through), the invitation, related sessions, actions. */
function calOpenHtml(ev, k) {
  const guests = (ev.guests || (ev.people || []).map(name => ({ name, status: "" }))).filter(g => !g.me);
  const people = guests.length ? `<div class="mpeople">${guests.map(g => `<span class="mchip ${esc(g.status || "")}" title="${esc(g.name)}${g.status ? " · " + esc(g.status) : ""}">${calAvatar(g.name, " sm")}${esc(g.name)}</span>`).join("")}</div>` : "";
  const desc = (ev.notes || "").trim();
  const rel = calRelated(ev);
  return `<div class="mopen" data-for="${esc(k)}">` +
    `<div class="mline">${esc([dayLabel(ev.start), calRange(ev), ev.calendar, ev.location, ev.recurring ? "repeats" : ""].filter(Boolean).join(" · "))}</div>` +
    people +
    (desc ? `<details class="mdesc"><summary>Invitation</summary><div>${calLinkify(desc)}</div></details>` : "") +
    (rel.length ? `<div class="mrel"><span class="ml">Related</span>${rel.map(s => `<span class="mses" data-ses="${esc(s.id)}" title="Open beside (the side pane)">${stateMark(s)}${esc(s.name)}</span>`).join("")}</div>` : "") +
    `<div class="macts"><button class="btn sm" data-ma="prep" title="An agent prepares you, into the note (P)">Prep</button>` +
    `${ev.link ? `<button class="btn sm" data-ma="copy" title="Copy the meeting link (C)">Copy link</button>` : ""}</div></div>`;
}
const calRow = () => XV.rows.find(x => x.k === XV.cur);
function calOpen(k, on = !XV.opened.has(k)) {
  if (on) XV.opened.add(k);
  else { if (XN.editing && XN.open === k) leaveCalNote(false); XV.opened.delete(k); }
  XV.cur = k; renderX();
}
function calCopyLink(ev) { if (ev.link) copyText(ev.link).then(() => toast("Copied the meeting link")); else toast("No meeting link in this event"); }
function calSpan(span) { XV.span = span; LS.set("calSpan", span); XV.week = 0; XV.cur = null; loadX(); }
function calAttach(where) {
  const ss = [...S.sessions.values()].filter(s => !s.fleet && !s.closed);
  menu(ss.length ? ss.map(s => ({ label: s.name, key: basename(s.cwd), run: () => openSide({ kind: "session", sid: s.id }, { focus: true }) }))
    : [{ label: "No session open", disabled: true }], where);
}
function calMenu(r, where) {
  const ev = r.ev, k = r.k;
  menu([
    { label: "Open and write notes", key: "⏎", run: () => { XV.opened.add(k); XV.cur = k; renderX(); editCalNote(); } },
    { label: XV.opened.has(k) ? "Close" : "Open", key: XV.opened.has(k) ? "←" : "→", run: () => calOpen(k) },
    { label: "Open the note as a file", key: "N", run: () => meetingNote(ev) },
    { label: "Join", key: "J", disabled: !ev.link, run: () => openUrl(ev.link) },
    { label: "Copy link", key: "C", disabled: !ev.link, run: () => calCopyLink(ev) },
    { label: "Prep: an agent prepares me, into the note", key: "P", run: () => { XV.opened.add(k); renderX(); prepMeeting(ev); } },
    { label: "Refresh", key: "R", run: () => { S.calCache.clear(); loadX(true); } },
  ], where);
}
/* Keys in the Calendar (the list has the keyboard; the note's editor keeps its own). */
function calKeyDown(combo) {
  const r = calRow();
  if (combo === "KeyR") { S.calCache.clear(); loadX(true); return true; }
  if (combo === "KeyW") { calSpan(XV.span === "week" ? "days" : "week"); return true; }
  if (XV.span === "week" && (combo === "A:ArrowLeft" || combo === "A:ArrowRight")) { xWeek(combo === "A:ArrowLeft" ? -1 : 1); return true; }
  if (XV.span === "week" && combo === "KeyT") { xWeek(0); return true; }
  if (combo === "KeyA") { XV.all = !XV.all; renderX(); return true; }
  if (!r) { if (combo === "Enter" && XV.cal && XV.cal.enabled === false) { setCalendar(true); return true; } return false; }
  if (combo === "KeyB") { const d0 = new Date(r.ev.start * 1000).setHours(0, 0, 0, 0) / 1000; if (XV.blocks.has(d0)) XV.blocks.delete(d0); else XV.blocks.add(d0); renderX(); return true; }
  if (combo === "ArrowRight") { calOpen(r.k, true); return true; }
  if (combo === "ArrowLeft") { calOpen(r.k, false); return true; }
  if (combo === "Space") { calOpen(r.k); return true; }
  if (combo === "Enter") { XV.opened.add(r.k); renderX(); editCalNote(); return true; }
  if (combo === "KeyP") { XV.opened.add(r.k); renderX(); prepMeeting(r.ev); return true; }
  if (combo === "KeyJ") { if (r.ev.link) openUrl(r.ev.link); else toast("No meeting link in this event"); return true; }
  if (combo === "KeyC") { calCopyLink(r.ev); return true; }
  if (combo === "KeyN") { meetingNote(r.ev); return true; }
  return false;
}
/* Clicks in the Calendar: a row opens / closes; Join; a day's blocks; a related session (beside); the actions. */
$("#xview").addEventListener("click", e => {
  if (xKind() !== "calendar") return;
  const t = e.target, done = () => { e.stopPropagation(); e.preventDefault(); };
  const row = t.closest(".mrow[data-xk]");
  const more = t.closest("[data-cmore]");  // a row's ⋯ (U11): its menu
  if (more) { done(); XV.cur = more.dataset.cmore; renderX(); xMenu({ anchor: $("#xbody .xrow.kc [data-cmore]") || more }); return; }
  if (row && !t.closest("[data-join]")) { done(); calOpen(row.dataset.xk); $("#xbody").focus({ preventScroll: true }); return; }
  const join = t.closest("[data-join]"); if (join) { done(); const r = XV.rows.find(x => x.k === join.dataset.join); if (r) openUrl(r.ev.link); return; }
  const blk = t.closest("[data-blk]"); if (blk) { done(); const d = +blk.dataset.blk; if (XV.blocks.has(d)) XV.blocks.delete(d); else XV.blocks.add(d); renderX(); return; }
  const ses = t.closest("[data-ses]"); if (ses) { done(); openSide({ kind: "session", sid: ses.dataset.ses }, { focus: true }); return; }
  const url = t.closest("[data-url]"); if (url) { done(); openUrl(url.dataset.url); return; }
  const act = t.closest("[data-ma]");
  if (act) {
    done();
    const k = act.closest(".mopen").dataset.for, r = XV.rows.find(x => x.k === k); if (!r) return;
    XV.cur = k;
    if (act.dataset.ma === "prep") { renderX(); prepMeeting(r.ev); }
    else if (act.dataset.ma === "copy") calCopyLink(r.ev);
    else if (act.dataset.ma === "edit") { renderX(); editCalNote(); }
    return;
  }
  const b = t.closest("[data-x]");
  if (b && b.dataset.x === "attach") { done(); calAttach({ anchor: b }); return; }
  if (b && b.dataset.x === "span") { done(); calSpan(XV.span === "week" ? "days" : "week"); return; }
  if (b && b.dataset.x === "app") { done(); api("POST", "/api/calendar/open-app", {}).catch(err => toast(err.message)); return; }
}, true);

/* ---------- the status bar's meeting: the current / next one, a countdown chip; a click lists today's and tomorrow's */
function renderMeet() {
  const b = $("#sbmeet"); if (!b || EMBED) return;
  const now = Date.now() / 1000;
  const evs = S.settings.calendar && S.calToday && S.calToday.events ? S.calToday.events.filter(e => !e.noise && e.end > now) : [];
  const ev = evs.sort((a, c) => a.start - c.start)[0];
  b.hidden = !ev;
  if (!ev) return;
  const live = ev.start <= now, mins = Math.ceil((ev.start - now) / 60);
  const chip = live ? `<span class="mchipc now">now</span>` : mins <= 10 ? `<span class="mchipc soon">soon</span>`
    : `<span class="mchipc">${mins < 60 ? mins + "m" : Math.floor(mins / 60) + "h" + (mins % 60 ? " " + (mins % 60) + "m" : "")}</span>`;
  b.classList.toggle("now", live);
  b.innerHTML = `${I("calendar", 13)}<span class="tt">${ev.start >= dayStart(1) ? "tomorrow " : ""}${hm24(ev.start)} ${esc(ev.title || "(no title)")}</span>${chip}`;
  b.title = `${ev.title || ""} · ${eventWhen(ev)}${ev.organizer ? " · " + ev.organizer : ""}${ev.link ? " · has a meeting link" : ""} (⌘⇧C)`;
}
/* The Calendar tab, on a meeting (opened). */
function openCalendarAt(ev) {
  if (EMBED) { window.parent.postMessage({ orbit: "open-file", path: "orbit:calendar", pinned: true }, location.origin); return; }
  XV.pending = calKey(ev);
  if (XV.span === "week") XV.week = Math.floor((ev.start - dayStart()) / (7 * 86400));
  openFile("orbit:calendar", { pinned: true });
}
/* ⌘⇧C, a click on the status bar's meeting: today's and tomorrow's meetings in a popover right above it (not a
   dialog in the middle): the Calendar's own rows, compact. A click (⏎) opens that meeting in the Calendar tab; Join
   (⌘⏎) joins; right-click: its menu; Esc, ⌘⇧C or a click elsewhere closes it. */
async function meetingsMenu() {
  if (!S.settings.calendar) { toast("Calendar is off: ⌘, turns it on (read-only)"); return; }
  if (modal && modal.kind === "meetpop") { closeModal(); return; }
  const anchor = $("#sbmeet") && !$("#sbmeet").hidden ? $("#sbmeet") : $("#statusbar .right") || $("#statusbar");
  const el = document.createElement("div"); el.id = "meetpop"; el.setAttribute("role", "listbox");
  let rows = [], i = 0, state = "loading", err = "";
  const place = () => {
    const k = rectScale(), r = anchor.getBoundingClientRect(), vw = innerWidth / S.zoom, vh = innerHeight / S.zoom;
    el.style.left = Math.max(8, Math.min(vw - el.offsetWidth - 8, r.left / k)) + "px";
    el.style.bottom = (vh - r.top / k + 6) + "px";
  };
  const draw = () => {
    const now = Date.now() / 1000;
    let body = "";
    if (state === "loading") body = `<div class="mpnone">Loading…</div>`;
    else if (state === "error") body = `<div class="mpnone">${esc(err)}</div>`;
    else if (!rows.length) body = `<div class="mpnone">No meetings today or tomorrow.</div>`;
    else {
      let day = null;
      body = rows.map((ev, k) => {
        const d0 = ev.start >= dayStart(1) ? dayStart(1) : dayStart();
        const head = d0 !== day ? `<div class="mphead">${d0 === dayStart() ? "TODAY" : "TOMORROW"}</div>` : "";
        day = d0;
        const live = ev.start <= now && now < ev.end, mins = Math.ceil((ev.start - now) / 60);
        const when = live ? `<span class="mb now">now</span>` : mins > 0 && mins < 60 ? `<span class="mpin">in ${mins}m</span>` : "";
        return head + `<div class="mprow${k === i ? " sel" : ""}${ev.end <= now ? " past" : ""}" data-i="${k}" title="${esc(ev.title || "")} · ${esc(eventWhen(ev))}${ev.organizer ? " · " + esc(ev.organizer) : ""}">` +
          `<span class="tm">${esc(calRange(ev))}</span>${calAvatar(ev.organizer || ev.title)}<span class="nm">${esc(ev.title || "(no title)")}</span>${when}` +
          `<span class="mt">${esc([ev.organizer, calAccepted(ev)].filter(Boolean).join(" · "))}</span>` +
          (ev.link ? `<button class="btn sm quiet" data-join="${k}" title="Join (⌘⏎)">Join</button>` : `<span class="mj0"></span>`) + `</div>`;
      }).join("");
    }
    el.innerHTML = `<div class="mplist">${body}</div><div class="mpfoot"><button class="qbtn" data-mpopen>${I("calendar", 13)}Open the Calendar</button><span class="k">⏎ open · ⌘⏎ join · ⌘⌃C</span></div>`;
    const sel = el.querySelector(".mprow.sel"); if (sel) sel.scrollIntoView({ block: "nearest" });
    place();
  };
  const go = k => { const ev = rows[k]; if (!ev) return; closeModal(); openCalendarAt(ev); };
  const join = k => { const ev = rows[k]; if (!ev) return; closeModal(); ev.link ? openUrl(ev.link) : toast("No meeting link in this event"); };
  const outside = e => { if (!el.contains(e.target) && !anchor.contains(e.target)) closeModal(); };
  el.addEventListener("mousedown", e => { if (!e.target.closest("button")) e.preventDefault(); });
  el.addEventListener("mousemove", e => { const r = e.target.closest(".mprow"); if (r && +r.dataset.i !== i) { i = +r.dataset.i; el.querySelectorAll(".mprow").forEach(x => x.classList.toggle("sel", +x.dataset.i === i)); } });
  el.addEventListener("click", e => {
    if (e.target.closest("[data-mpopen]")) { closeModal(); openFile("orbit:calendar", { pinned: true }); return; }
    const j = e.target.closest("[data-join]"); if (j) { join(+j.dataset.join); return; }
    const r = e.target.closest(".mprow"); if (r) go(+r.dataset.i);
  });
  el.addEventListener("contextmenu", e => {
    const r = e.target.closest(".mprow"); if (!r) return;
    e.preventDefault(); const ev = rows[+r.dataset.i], k = rectScale();
    closeModal(); eventMenu(ev, { at: { x: e.clientX / k, y: e.clientY / k } });
  });
  const m = {
    kind: "meetpop", redraw: () => draw(),
    teardown: () => { document.removeEventListener("mousedown", outside, true); el.remove(); $("#sbmeet").classList.remove("on"); },
    close: () => closeModal(),
    onKey(e, combo) {
      if (combo === "Escape" || combo === "MS:KeyC") closeModal();
      else if (combo === "ArrowDown" || combo === "C:KeyN") { i = Math.min(rows.length - 1, i + 1); draw(); }
      else if (combo === "ArrowUp" || combo === "C:KeyP") { i = Math.max(0, i - 1); draw(); }
      else if (combo === "Enter") go(i);
      else if (combo === "M:Enter") join(i);
      else return false;
      return true;
    },
  };
  openModal(m);
  document.body.appendChild(el);
  $("#sbmeet").classList.add("on");
  document.addEventListener("mousedown", outside, true);
  draw();
  try {
    const d = await calEvents(dayStart(), dayStart(2));
    if (d.error) throw new Error(d.error);
    const now = Date.now() / 1000;
    rows = (d.events || []).filter(e => !e.noise && e.end > now - 600).sort((a, b) => a.start - b.start);
    const live = rows.findIndex(e => e.end > now);  // the cursor starts on the current (or next) meeting
    i = Math.max(0, live); state = "ok";
  } catch (e) { state = "error"; err = e.message; }
  if (modal === m) draw();
}

/* ---------- the reminder: 10 minutes before, a floating note (Join · Prep · ×), gone after 10 minutes */
function showReminder(m) {
  if (EMBED) return;
  const ev = m.event;
  let el = $("#remind");
  if (!el) { el = document.createElement("div"); el.id = "remind"; document.body.appendChild(el); }
  el.innerHTML = `<span class="ic">${I("calendar", 14)}</span><span class="tx"><b>${hm24(ev.start)}</b> ${esc(ev.title || "a meeting")}<span class="in">in ${m.minutes} min</span></span>` +
    `${ev.link ? `<button class="btn sm primary" data-r="join">Join</button>` : ""}<button class="btn sm" data-r="prep">Prep</button><button class="btn sm" data-r="open" title="Open it in the Calendar">Open</button><button class="x" data-r="x" title="Dismiss">${I("x", 12)}</button>`;
  el.hidden = false;
  el.onclick = e => {
    const a = e.target.closest("[data-r]"); if (!a) return;
    if (a.dataset.r === "join") openUrl(ev.link);
    else if (a.dataset.r === "prep") prepMeeting(ev);
    else if (a.dataset.r === "open") openCalendarAt(ev);
    if (a.dataset.r !== "prep") el.hidden = true;
  };
  clearTimeout(showReminder.t);
  showReminder.t = setTimeout(() => { el.hidden = true; }, 10 * 60000);
}
