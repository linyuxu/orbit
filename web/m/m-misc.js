"use strict";
/* ===================================================================== Misc: Calendar and Schedules
   The desktop's Misc panel: one row each, with what is next there. Calendar: today's and the coming days' meetings
   (the macOS Calendar, read-only, through the server), the meeting now or next marked; blocks (all-day, declined,
   your own…) behind "N blocks" per day; a meeting opens on its own page (people, invitation, Join). Schedules: each
   with its next run and a switch (on / off); a tap: Run now, the last run, Remove. Both follow the live events. */
TAB_ROOT.misc = () => pushPage(miscRootPage(), "misc");
S.schedules = null; S.settings = null;
const CAL = { data: null, at: 0, blocks: new Set(), DAYS: 7 };
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
const miscPages = () => AREA.stacks.misc.filter(p => p.refresh);
function miscChanged() { for (const p of miscPages()) p.refresh(); }
async function loadMisc() {
  const [st, sc] = await Promise.all([api("GET", "/api/settings").catch(() => null), api("GET", "/api/schedules").catch(() => null)]);
  if (st) S.settings = st;
  if (sc) S.schedules = sc.schedules || [];
  miscChanged();
  loadCal();
}
async function loadCal(fresh = false) {
  if (!S.settings) return;
  if (!S.settings.calendar) { CAL.data = { enabled: false, events: [] }; miscChanged(); return; }
  if (!fresh && CAL.data && CAL.data.from === dayStart() && Date.now() - CAL.at < 60000) return;
  const from = dayStart();
  let d;
  try { d = await api("GET", `/api/calendar?from=${from}&to=${dayStart(CAL.DAYS)}${fresh ? "&fresh=1" : ""}`); }
  catch (e) { if (e instanceof NotPaired) return; d = { enabled: true, error: e.message, events: [] }; }
  CAL.data = { ...d, from }; CAL.at = Date.now();
  miscChanged();
}
setInterval(() => { if (!document.hidden && AREA.cur === "misc") { miscChanged(); loadCal(); } }, 30000);  // ("in 25m" stays true)

function miscRootPage() {
  const page = makePage({ root: true, title: "Misc" });
  page.refresh = () => {
    const now = Date.now() / 1000, c = CAL.data;
    const real = c && c.events ? c.events.filter(e => !e.noise && !e.all_day && e.end > now).sort((a, b) => a.start - b.start) : [];
    const next = real[0];
    const calHint = !S.settings ? "" : !S.settings.calendar ? "off" : !c ? "…" : c.error ? "error" : next
      ? `${next.start <= now ? "now" : next.start < dayStart(1) ? hm24(next.start) : fmtNext(next.start)} ${esc(next.title || "(no title)")}` : "none";
    const on = (S.schedules || []).filter(x => x.enabled && x.next).sort((a, b) => a.next - b.next);
    page.body.innerHTML = `<div class="rows">` +
      navRow({ attrs: `data-open="calendar"`, icon: I("calendar", 18), name: "Calendar", hint: calHint }) +
      navRow({ attrs: `data-open="schedules"`, icon: I("clock", 18), name: "Schedules", hint: S.schedules == null ? "" : on.length ? esc(fmtNext(on[0].next)) : "none" }) + `</div>`;
  };
  page.onShow = () => loadMisc();
  page.body.addEventListener("click", e => {
    const r = e.target.closest("[data-open]"); if (!r) return;
    pushPage(r.dataset.open === "calendar" ? calendarPage() : schedulesPage(), "misc");
  });
  page.refresh();
  loadMisc();
  return page;
}

/* ===================================================================== Calendar */
const calKey = ev => ev.id + ":" + ev.start;
const calRange = ev => ev.all_day ? "all day" : `${hm24(ev.start)}–${hm24(ev.end)}`;
function calAccepted(ev) {
  const g = ev.guests || [];
  return g.length ? `${g.filter(x => x.status === "accepted").length}/${g.length}` : ev.attendees ? `${ev.attendees}` : "";
}
const calDayName = d0 => d0 === dayStart() ? "Today" : d0 === dayStart(1) ? "Tomorrow" : dayLabel(d0);
function calendarPage() {
  const page = makePage({ title: "Calendar", right: navBtn("refresh", I("refresh", 20), "Refresh") });
  page.acts.refresh = () => { page.body.innerHTML = loadingHtml(); loadCal(true); };
  page.refresh = () => {
    const c = CAL.data, now = Date.now() / 1000;
    if (!c) { page.body.innerHTML = loadingHtml(); return; }
    if (c.enabled === false) {
      page.body.innerHTML = emptyBox("Calendar is off", "Turned on, Orbit shows your macOS Calendar's meetings here. Read-only: it never changes your calendars." +
        `<div class="eacts"><button class="pill" data-on>Turn on</button></div>`);
      return;
    }
    if (c.error) { page.body.innerHTML = emptyBox("Cannot read the calendar", esc(c.error) + `<div class="eacts"><button class="pill quiet" data-retry>Try again</button></div>`); return; }
    const evs = c.events || [], real = evs.filter(e => !e.noise);
    const next = real.filter(e => e.end > now).sort((a, b) => a.start - b.start)[0];
    page.evs = new Map();
    const parts = [];
    for (let i = 0; i < CAL.DAYS; i++) {
      const d0 = dayStart(i), d1 = dayStart(i + 1);
      const inDay = evs.filter(e => (e.start >= d0 && e.start < d1) || (i === 0 && e.start < d0 && e.end > d0));
      const meet = inDay.filter(e => !e.noise), blocks = inDay.filter(e => e.noise), showB = CAL.blocks.has(d0);
      if (i > 1 && !meet.length && !(showB && blocks.length)) continue;  // (a later day with nothing: no head)
      parts.push(`<div class="shead mday"><span class="gn">${esc(calDayName(d0))}</span>${blocks.length ? `<button class="mblk" data-blk="${d0}">${blocks.length} block${blocks.length === 1 ? "" : "s"} · ${showB ? "hide" : "show"}</button>` : ""}</div>`);
      const shown = (showB ? inDay : meet).slice().sort((a, b) => a.start - b.start);
      if (!shown.length) { parts.push(`<div class="mnone">No meetings</div>`); continue; }
      parts.push(`<div class="rows">` + shown.map(ev => {
        const k = calKey(ev); page.evs.set(k, ev);
        const live = ev.start <= now && now < ev.end, over = !ev.noise && meet.some(o => o !== ev && o.start < ev.end && ev.start < o.end);
        const cls = (ev.noise ? " block" : "") + (ev.end <= now ? " past" : "") + (next && calKey(next) === k ? " next" : "");
        const meta = [ev.organizer, calAccepted(ev), ev.location].filter(Boolean).join(" · ");
        return `<div class="mrow${cls}" data-ev="${esc(k)}" role="button" tabindex="0"><span class="tm">${ev.all_day ? `<span>all day</span>` : `<span>${hm24(ev.start)}</span><span class="e">${hm24(ev.end)}</span>`}</span>` +
          `<span class="tx"><span class="t1"><span class="nm">${esc(ev.title || "(no title)")}</span>${live ? `<span class="mb now">now</span>` : ""}${over ? `<span class="mb ov">overlap</span>` : ""}</span>` +
          (meta ? `<span class="t2">${esc(meta)}</span>` : "") + `</span>` +
          (ev.link ? `<a class="mjoin" href="${esc(ev.link)}" target="_blank" rel="noopener">Join</a>` : "") + `</div>`;
      }).join("") + `</div>`);
    }
    page.body.innerHTML = parts.join("");
  };
  page.body.addEventListener("click", e => {
    const t = e.target;
    if (t.closest(".mjoin")) return;  // (the link opens itself)
    if (t.closest("[data-on]")) { api("POST", "/api/settings", { calendar: true }).then(s => { S.settings = s; toast("Calendar on (read-only). The first time, the Mac asks Orbit for Calendar access."); loadCal(true); }).catch(err => toast(err.message)); return; }
    if (t.closest("[data-retry]")) { loadCal(true); return; }
    const b = t.closest("[data-blk]"); if (b) { const d = +b.dataset.blk; CAL.blocks.has(d) ? CAL.blocks.delete(d) : CAL.blocks.add(d); page.refresh(); return; }
    const r = t.closest("[data-ev]"); if (r && page.evs.has(r.dataset.ev)) pushPage(meetingPage(page.evs.get(r.dataset.ev)), page.tab);
  });
  page.refresh();
  loadCal();
  return page;
}
/* Sessions that may be about the meeting: a word of its title (3 letters or more, not "sync", "weekly"…) in the
   session's name or its last answer; four at most (the desktop's rule). */
const CAL_STOP = new Set(("the and for with sync weekly meeting meet call chat daily team standup review update one "
  + "sync-up catch catchup check checkin office hours").split(" "));
function calRelated(ev) {
  const words = (ev.title || "").toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(w => w.length >= 3 && !CAL_STOP.has(w));
  if (!words.length) return [];
  return [...S.sessions.values()].filter(s => !s.fleet && words.some(w => (s.name + " " + (s.last ? s.last.text : "")).toLowerCase().includes(w))).slice(0, 4);
}
const calLinkify = text => esc(text || "").replace(/https?:\/\/[^\s<>"')]+/g, u => `<a href="${u}" target="_blank" rel="noopener">${u}</a>`);
const calInitial = s => (String(s || "?").trim().replace(/^[^\p{L}\p{N}]+/u, "")[0] || "?").toUpperCase();
function meetingPage(ev) {
  const page = makePage({ title: ev.title || "(no title)", large: true, right: navBtn("more", MORE_SVG, "Meeting actions") });
  const guests = (ev.guests || (ev.people || []).map(name => ({ name, status: "" }))).filter(g => !g.me);
  const rel = calRelated(ev), desc = (ev.notes || "").trim();
  const STATUS = { accepted: "accepted", declined: "declined", tentative: "maybe", pending: "invited" };
  page.body.innerHTML = `<div class="meet">` +
    `<div class="mline">${esc(calDayName(new Date(ev.start * 1000).setHours(0, 0, 0, 0) / 1000) + " · " + calRange(ev))}</div>` +
    `<div class="mline2">${esc([ev.calendar, ev.location, ev.recurring ? "repeats" : ""].filter(Boolean).join(" · "))}</div>` +
    (ev.link ? `<div class="macts"><a class="pill" href="${esc(ev.link)}" target="_blank" rel="noopener">Join</a><button class="pill quiet" data-copy-link>Copy link</button></div>` : "") + `</div>` +
    (guests.length ? `<div class="shead">People<span class="cnt">${esc(calAccepted(ev))}</span></div><div class="rows">` + guests.map(g =>
      `<div class="prow2 ${esc(g.status || "")}"><span class="mav">${esc(calInitial(g.name))}</span><span class="nm">${esc(g.name)}${g.name === ev.organizer ? `<span class="org">organizer</span>` : ""}</span>` +
      `<span class="st">${esc(STATUS[g.status] || g.status || "")}</span></div>`).join("") + `</div>` : "") +
    (desc ? `<div class="shead">Invitation</div><div class="mdesc">${calLinkify(desc)}</div>` : "") +
    (rel.length ? `<div class="shead">Related sessions</div><div class="rows">` + rel.map(s => navRow({ attrs: `data-ses="${esc(s.id)}"`, icon: stateMark(s), name: s.name, sub: esc(tilde(s.cwd)) })).join("") + `</div>` : "");
  const copyLink = () => ev.link ? copyText(ev.link, "Copied the meeting link") : toast("No meeting link in this event");
  page.acts.more = () => actionSheet(ev.title || "", [
    { label: "Join", icon: "external", disabled: !ev.link, run: () => window.open(ev.link, "_blank", "noopener") },
    { label: "Copy link", icon: "link", disabled: !ev.link, run: copyLink },
    { label: "Copy details", icon: "copy", run: () => copyText(`${ev.title}\n${dayLabel(ev.start)} ${calRange(ev)}${ev.location ? "\n" + ev.location : ""}${ev.link ? "\n" + ev.link : ""}`) },
  ]);
  page.body.addEventListener("click", e => {
    if (e.target.closest("[data-copy-link]")) { copyLink(); return; }
    const s = e.target.closest("[data-ses]"); if (s) { switchTab("sessions"); openSession(s.dataset.ses); }
  });
  return page;
}

/* ===================================================================== Schedules */
function lastLine(it) {
  const l = it.last || {};
  if (!l.at) return "never ran";
  const when = ago(l.at) === "now" ? "just now" : ago(l.at) + " ago";
  if (l.error) return `<span class="err">last ${esc(when)}: ${esc(l.error)}</span>`;
  if (l.skipped) return `last ${esc(when)}: skipped`;
  return `last ${esc(when)}${l.late ? " (late)" : ""}${l.manual ? " (run now)" : ""}${l.cid ? ` · #${l.cid}` : ""}`;
}
function schedulesPage() {
  const page = makePage({ title: "Schedules" });
  page.refresh = () => {
    const xs = S.schedules;
    if (xs == null) { page.body.innerHTML = loadingHtml(); return; }
    page.setTitle("Schedules", xs.length ? `${xs.filter(x => x.enabled).length} on · ${xs.length} in all` : "");
    page.body.innerHTML = xs.length ? `<div class="rows">` + xs.map(it => {
      const st = !it.enabled ? "stopped" : it.last && it.last.error ? "err" : "ok";
      return `<div class="schrow${it.enabled ? "" : " off"}" data-sch="${esc(it.id)}" role="button" tabindex="0"><span class="sdot ${st}"></span>` +
        `<span class="tx"><span class="t1"><span class="nm">${esc(it.name)}</span></span>` +
        `<span class="t2">${it.check ? `<span class="watch">watch</span> ` : ""}${esc(it.describe)} → ${esc(it.target_name || "")}</span>` +
        `<span class="t2">${it.enabled ? `next ${esc(fmtNext(it.next) || "—")}` : "off"} · ${lastLine(it)}</span></span>` +
        `<button class="switch${it.enabled ? " on" : ""}" data-toggle="${esc(it.id)}" role="switch" aria-checked="${it.enabled}" aria-label="${it.enabled ? "Pause" : "Resume"} ${esc(it.name)}"><span></span></button></div>`;
    }).join("") + `</div>` : emptyBox("No schedules yet", "A schedule sends a request to a session at set times: every 2h, weekdays 9am. Make one in Orbit on the Mac, or ask an agent: <code>orbit-agent wake 30m \"check CI\"</code>.");
  };
  const find = id => (S.schedules || []).find(x => x.id === id);
  const toggle = async it => {
    it.enabled = !it.enabled; page.refresh();  // (at once; the server's answer and its event follow)
    try { const x = await api("POST", `/api/schedules/${encodeURIComponent(it.id)}/update`, { enabled: it.enabled }); Object.assign(it, x); toast(x.enabled ? `On: next ${fmtNext(x.next)}` : `“${x.name}” is off`); }
    catch (e) { it.enabled = !it.enabled; if (!(e instanceof NotPaired)) toast(e.message); }
    miscChanged();
  };
  page.body.addEventListener("click", e => {
    const tg = e.target.closest("[data-toggle]");
    if (tg) { const it = find(tg.dataset.toggle); if (it) toggle(it); return; }
    const r = e.target.closest("[data-sch]"), it = r && find(r.dataset.sch); if (!it) return;
    actionSheet(`${it.name} · ${it.describe}`, [
      { label: "Run now", icon: "play", run: async () => {
        try { const x = await api("POST", `/api/schedules/${encodeURIComponent(it.id)}/run`); toast(x.last && x.last.error ? "Could not run: " + x.last.error : `Sent to ${x.target_name}${x.last && x.last.cid ? " as #" + x.last.cid : ""}`); }
        catch (err) { toast(err.message); }
      } },
      { label: it.enabled ? "Pause" : "Resume", icon: it.enabled ? "pause" : "play", run: () => toggle(it) },
      { label: "Open the last run", icon: "chat", disabled: !(it.last && it.last.sid && S.sessions.has(it.last.sid)), run: () => { switchTab("sessions"); openSession(it.last.sid); } },
      { label: "Remove…", icon: "x", danger: true, run: () => actionSheet(`Delete the schedule “${it.name}”? The sessions and requests it made stay.`, [
        { label: "Delete", icon: "x", danger: true, run: () => api("POST", `/api/schedules/${encodeURIComponent(it.id)}/delete`).then(() => { S.schedules = S.schedules.filter(x => x !== it); miscChanged(); }).catch(err => toast(err.message)) },
      ]) },
    ]);
  });
  page.refresh();
  return page;
}
