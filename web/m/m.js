"use strict";
/* ===================================================================== boot, live events, the screen
   Sessions come from /api/sessions, then follow /api/events (the desktop's stream). iOS drops the stream when the
   app goes to the background or the network changes: coming back (visibilitychange, pageshow, online) opens it
   again, and the server's hello resyncs the list; the conversation on screen reloads its cards. */
let ES = null, esRetry = 0, esTimer = 0;

async function loadAll() {
  const d = await api("GET", "/api/sessions");
  setSessions(d.sessions, d.groups);
  S.machines = d.machines || [];
  renderList();
  sessionsChanged();
  if (S.current) { renderNav(); renderComposer(); }
  loadLayoutTabs();  // (the tabs every screen shares: under their groups)
}
function setSessions(list, groups) {
  S.sessions = new Map(list.filter(s => !s.closed).map(s => [s.id, s]));
  if (groups) S.groups = groups;
  for (const sid of [...S.active.keys()]) { const s = S.sessions.get(sid); if (!s || s.state !== "working") S.active.delete(sid); }
  S.loaded = true;
  fetchWorking();
}
/* A session already working when the app opens: its running card (the list's "Working: …" and elapsed time). */
function fetchWorking() {
  for (const s of S.sessions.values()) {
    if (s.state !== "working" || S.active.has(s.id) || S.cards.has(s.id) || fetchWorking.asked.has(s.id)) continue;
    fetchWorking.asked.add(s.id);
    api("GET", `/api/sessions/${encodeURIComponent(s.id)}/cards/${((s.last && s.last.id) || 0) + 1}`).then(d => {
      const cs = d.cards || [d.card];  // (one card comes as {card, final})
      const c = cs.find(x => x && ACTIVE.has(x.state)) || cs.find(x => x && x.state === "queued");
      if (c && !S.active.has(s.id)) S.active.set(s.id, c);
      if (!S.current) renderList();
    }).catch(() => {}).finally(() => setTimeout(() => fetchWorking.asked.delete(s.id), 10000));
  }
}
fetchWorking.asked = new Set();

function closeEvents() { if (ES) { ES.onmessage = ES.onerror = null; ES.close(); ES = null; } clearTimeout(esTimer); }
function reconnect() {
  if (document.body.classList.contains("unpaired")) return;
  closeEvents();
  const es = ES = new EventSource(withToken("/api/events?delta=1"), { withCredentials: true });  // (card patches: cardpatch.js)
  es.onopen = () => { esRetry = 0; setOnline(true); };
  es.onmessage = ev => { try { onEvent(JSON.parse(ev.data)); } catch (e) { console.error(e); } };
  es.onerror = () => {
    if (es !== ES) return;
    setOnline(false);
    if (es.readyState !== EventSource.CLOSED) return;  // (it retries by itself)
    // Closed for good: a 401 (not paired) or the server is away. Ask, then try again, slower each time.
    clearTimeout(esTimer);
    esTimer = setTimeout(async () => {
      try { await api("GET", "/api/version"); reconnect(); }
      catch (e) { if (!(e instanceof NotPaired)) { esRetry++; es.onerror(); } }
    }, Math.min(15000, 1000 * 2 ** esRetry));
  };
}
function setOnline(v) {
  if (S.online === v) return;
  S.online = v;
  document.body.classList.toggle("offline", !v);
}
function onEvent(m) {
  if (m.t === "hello" || m.t.startsWith("plugin-")) pviewEvent(m);  // (m-pviews.js: a plugin's thread, doc or list on screen)
  if (m.t === "hello") {
    setOnline(true);
    voiceResync();  // (a voice call: what became of it while the stream was away)
    S.machines = m.machines || [];
    setSessions(m.sessions, m.groups);
    for (const sid of [...S.cards.keys()]) if (!S.sessions.has(sid)) S.cards.delete(sid);
    if (S.current && !S.sessions.has(S.current)) { toast("This session was closed"); history.replaceState(null, "", location.pathname + location.search); closeSession(); return; }
    renderList(); sessionsChanged(); loadLayoutTabs();
    if (S.current) { renderNav(); renderComposer(); loadCards(S.current); }
  } else if (m.t === "live") {
    voiceEvent(m);
  } else if (m.t === "session") {
    const s = m.session;
    if (s.closed) { removeSession(s.id); return; }
    S.sessions.set(s.id, s);
    if (s.state === "working") fetchWorking(); else S.active.delete(s.id);
    if (s.id === S.current) { renderNav(); renderComposer(); markSeen(s.id); }
    else if (!S.current) renderList();
  } else if (m.t === "card") {
    upsertCard(m.card);
    if (!S.current) renderListSoon();
  } else if (m.t === "cardp") {
    const list = S.cards.get(m.sid), act = S.active.get(m.sid);  // (the list shows a working session's card too)
    const have = (list && list.find(x => x.id === m.id)) || (act && act.id === m.id ? act : null);
    onCardPatch(m, have, c => { upsertCard(c); if (!S.current) renderListSoon(); });
  } else if (m.t === "layout") {
    S.layout = { layout: m.layout, rev: m.rev }; if (!S.current) renderList();
  } else if (m.t === "show" || m.t === "open" || m.t === "side") {
    sideEvent(m);
  } else if (m.t === "closed") {
    removeSession(m.id);
  } else if (m.t === "order") {
    const next = new Map();
    for (const id of m.ids) if (S.sessions.has(id)) next.set(id, S.sessions.get(id));
    for (const [id, s] of S.sessions) if (!next.has(id)) next.set(id, s);
    S.sessions = next;
    if (!S.current) renderList();
  } else if (m.t === "groups") {
    S.groups = m.groups; if (!S.current) renderList();
  } else if (m.t === "machines") {
    S.machines = m.machines || []; if (!S.current) renderList();
  } else if (m.t === "schedules") {
    S.schedules = m.schedules || []; miscChanged();
  } else if (m.t === "settings") {
    const was = S.settings && !!S.settings.calendar;
    S.settings = m.settings;
    if (was !== !!S.settings.calendar) { CAL.data = null; loadCal(true); }
  }
}
/* The other areas' pages drawn from the sessions (Files' session folders): drawn again when the list comes. */
function sessionsChanged() { for (const p of AREA.stacks.files) if (p.root && p.render) p.render(); }
function renderListSoon() { if (renderListSoon.t) return; renderListSoon.t = setTimeout(() => { renderListSoon.t = 0; if (!S.current) renderList(); }, 250); }
function removeSession(sid) {
  S.sessions.delete(sid); S.cards.delete(sid); S.active.delete(sid);
  if (sid === S.current) { toast("This session was closed"); history.replaceState(null, "", location.pathname + location.search); closeSession(); }
  else if (!S.current) renderList();
}
/* Back from the background, or the network: open the stream again (hello resyncs everything); the page on screen
   in another area asks again for what it shows. */
function resume() { reconnect(); loadAll().catch(() => {}); const p = AREA.cur !== "sessions" && topPage(); if (p && p.onShow) p.onShow(); }
document.addEventListener("visibilitychange", () => { if (!document.hidden) resume(); });
addEventListener("pageshow", e => { if (e.persisted) resume(); });
addEventListener("online", resume);
addEventListener("offline", () => setOnline(false));

/* Elapsed times tick (a running card, the Working strip, the subagents strip). */
setInterval(() => {
  const now = Date.now() / 1000;
  for (const el of document.querySelectorAll(".el[data-t0]")) if (el.dataset.t0) {
    const c = el.closest(".card");
    const card = c && (S.cards.get(S.current) || []).find(x => x.id === +c.id.slice(5));
    el.textContent = card ? footText(card) : dur(now - +el.dataset.t0);
  }
}, 1000);
setInterval(() => { if (!S.current && !document.hidden) renderList(); }, 60000);  // ("5m" ages)

/* ===================================================================== screens and navigation */
function showView(name) {
  const conv = name === "conv";
  document.body.classList.toggle("in-conv", conv);
  $("#vconv").setAttribute("aria-hidden", String(!conv));
  $("#vlist").setAttribute("aria-hidden", String(conv));
}
addEventListener("popstate", e => {
  const sid = e.state && e.state.sid;
  closeSheets();
  if (sid && S.sessions.has(sid)) openSession(sid, { push: false }); else closeSession();
});
/* Swipe from the left edge: back to the list (the conversation follows the finger, as in iOS). */
const SWIPE = { x0: null, y0: 0, dx: 0, on: false };
/* (The app keeps one history entry, m-conv.js openSession: iOS's own edge swipes have nowhere to go, so this one
   is the only gesture; with entries, iOS slid the app and a stale snapshot of the list under ours.) */
$("#vconv").addEventListener("touchstart", e => {
  const t = e.touches[0];
  SWIPE.x0 = e.touches.length === 1 && t.clientX < 24 ? t.clientX : null; SWIPE.y0 = t.clientY; SWIPE.dx = 0; SWIPE.on = false;
}, { passive: true });
$("#vconv").addEventListener("touchmove", e => {
  if (SWIPE.x0 == null) return;
  const t = e.touches[0], dx = t.clientX - SWIPE.x0, dy = t.clientY - SWIPE.y0;
  if (!SWIPE.on) { if (Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx)) { SWIPE.x0 = null; return; } if (dx > 10) SWIPE.on = true; else return; }
  e.preventDefault();
  SWIPE.dx = Math.max(0, dx);
  document.body.classList.add("swiping", "swiping-conv");
  $("#vconv").style.transform = `translateX(${SWIPE.dx}px)`;
  $("#vlist").style.transform = `translateX(${-30 + SWIPE.dx / innerWidth * 30}%)`;
}, { passive: false });
$("#vconv").addEventListener("touchend", () => {
  if (SWIPE.x0 == null) return;
  SWIPE.x0 = null;
  if (!SWIPE.on) return;
  document.body.classList.remove("swiping", "swiping-conv");
  $("#vconv").style.transform = $("#vlist").style.transform = "";
  if (SWIPE.dx > innerWidth * .33) goBack();  // (in this same frame: it slides on from where the finger left it)
});

/* ===================================================================== the keyboard: the input stays above it
   iOS keeps the layout viewport when the keyboard opens and moves the visual one: the app follows the visual
   viewport (its height and offset), so the input row sits right on the keyboard and the nav bar stays in sight. */
function fitViewport() {
  const vv = window.visualViewport; if (!vv) return;
  const r = document.documentElement.style;
  r.setProperty("--vvh", vv.height + "px");
  r.setProperty("--vvtop", vv.offsetTop + "px");
  document.body.classList.toggle("kb", innerHeight - vv.height > 120);
  if (window.scrollY) window.scrollTo(0, 0);
  autosize();
}
if (window.visualViewport) { visualViewport.addEventListener("resize", fitViewport); visualViewport.addEventListener("scroll", fitViewport); }
addEventListener("resize", fitViewport);

document.addEventListener("touchstart", () => {}, { passive: true });  // (iOS shows :active pressed states only with a touch listener)

/* ===================================================================== start */
(async function start() {
  const q = new URLSearchParams(location.search);
  if (q.has("token")) { q.delete("token"); history.replaceState(null, "", location.pathname + (q.size ? "?" + q : "") + location.hash); }  // (the cookie carries it)
  fitViewport();
  // Opened on a session: #s/<id> (a reload, the back stack) or ?session=<id> (a link from the desktop); else the area
  // it was left on, at once (its pages load their own data).
  const want = decodeURIComponent((/^#s\/(.+)$/.exec(location.hash) || [])[1] || "") || new URLSearchParams(location.search).get("session");
  const tab = LS.get("tab", "sessions");
  if (!want && tab !== "sessions" && TAB_ROOT[tab]) switchTab(tab); else renderTabbar();
  if ("serviceWorker" in navigator) {
    // A new version took over (not the first install): reload once, so the new code runs now; while a text is being
    // edited, wait until the app comes back to the front with nothing unsaved.
    const had = !!navigator.serviceWorker.controller;
    let pending = false;
    const editing = () => Object.values(AREA.stacks).some(st => st.some(p => p.dirty && p.dirty()));
    const reload = () => { if (editing() || document.querySelector("textarea:focus, .cm-content:focus")) { pending = true; return; } location.reload(); };
    navigator.serviceWorker.addEventListener("controllerchange", () => { if (had) reload(); });
    document.addEventListener("visibilitychange", () => { if (pending && !document.hidden) reload(); });
    navigator.serviceWorker.register("/m/sw.js", { scope: "/m/", updateViaCache: "none" }).then(r => r.update()).catch(() => {});
    // iOS resumes a Home Screen app without loading it again: look for a new version each time it comes to the front
    // (at most once a minute); a new one takes over and the page reloads (controllerchange, above).
    let looked = Date.now();
    document.addEventListener("visibilitychange", () => {
      if (document.hidden || Date.now() - looked < 60000) return;
      looked = Date.now();
      navigator.serviceWorker.getRegistration("/m/").then(r => r && r.update()).catch(() => {});
    });
  }
  try { const d = await api("GET", "/api/harnesses"); S.harnesses = d.harnesses || []; } catch (e) { if (e instanceof NotPaired) return; }
  try { await loadAll(); }
  catch (e) { if (e instanceof NotPaired) return; setOnline(false); $("#slist").innerHTML = `<div class="empty"><h2>Can't reach Orbit</h2>The Mac may be asleep or off the network. This reconnects by itself.</div>`; }
  reconnect();
  if (want && S.sessions.has(want)) {
    history.replaceState({ sid: want }, "", "#s/" + encodeURIComponent(want));
    openSession(want, { push: false });
  } else if (location.hash) history.replaceState(null, "", location.pathname + location.search);
  requestAnimationFrame(() => document.body.classList.add("ready"));
})();
