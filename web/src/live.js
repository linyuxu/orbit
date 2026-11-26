"use strict";
/* ===================================================================== live updates (SSE) */
/* A session's record comes again with its every step (its preview, its time): the tabs and the panels it shows in are
   drawn from it at most every 250 ms. Drawn on each event (twenty a second with two agents at work), the rows were
   replaced under the pointer: a click hit nothing, a hover's tip flickered. */
const LIVE_DRAW = { fns: new Set(), t: 0, last: 0 };
function drawSoon(...fns) {
  fns.forEach(f => LIVE_DRAW.fns.add(f));
  if (LIVE_DRAW.t) return;
  LIVE_DRAW.t = setTimeout(() => {
    LIVE_DRAW.t = 0; LIVE_DRAW.last = Date.now();
    const f = [...LIVE_DRAW.fns]; LIVE_DRAW.fns.clear(); f.forEach(x => x());
  }, Math.max(0, LIVE_DRAW.last + 250 - Date.now()));
}
function connect() {
  const es = new EventSource("/api/events?delta=1");  // (a streaming card's changes as patches: cardpatch.js)
  es.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (typeof homeSoon === "function" && HOME_EVENTS.has(m.t)) homeSoon();  // (a group's home in front: drawn again, soon)
    if (m.t === "machines") { S.machines = m.machines || []; renderLeft(); renderTabs(); return; }
    if (m.t === "live") { if (!EMBED) voiceEvent(m); return; }
    if (m.t === "hello") {
      const had = S.current;
      if (m.home) HOME_DIR = m.home;
      if (typeof SIDE_SRV !== "undefined") { SIDE_SRV.loaded.clear(); SIDE_SRV.rev = {}; }  // (what changed meanwhile: fetched again)
      if (!EMBED) loadLayout();  // (the tabs too)
      S.machines = m.machines || [];
      setSessions(m.sessions, m.groups);
      for (const sid of [...S.cards.keys()]) if (!S.sessions.has(sid)) S.cards.delete(sid);
      renderAll(); renderSide();
      if (typeof voiceResync === "function") voiceResync();  // (a voice call: what became of it while the stream was away)
      if (!EMBED) { loadPluginUi(); loadHosting(); }  // (also after a server restart: plugins, hosting may have changed)
      else pviewsReloaded();  // (a side tab's view: what it missed)
      if (EMBED_REVIEW) { renderReview(); return; }
      if (EMBED_X) { renderX(); return; }
      if (S.current && !S.file) { if (S.current !== had) { input.value = S.drafts[S.current] || ""; autosize(); updateSend(); } loadCards(S.current); }
      else if (!S.file) renderConv();
    } else if (m.t === "session") {
      const s = m.session;
      if (s.closed) return;
      const old = S.sessions.get(s.id);
      S.sessions.set(s.id, s);
      if (EMBED) { renderStatus(); return; }
      if ((old ? old.attention || "" : "") !== (s.attention || "")) attentionChanged(s);
      if (s.id === S.current && old && !inView(s)) { removeFromView(s.id, old); return; }
      if (!S.current && inView(s) && !S.file) { switchTo(s.id, { focus: false }); return; }
      drawSoon(renderTabs, renderSide);
      if (leftShowsSession(old, s)) drawSoon(renderLeft);
      if (s.id === S.current) { renderStatus(); if (old && old.cwd !== s.cwd) pollReview(); }
    } else if (m.t === "order") {
      const next = new Map();
      for (const id of m.ids) if (S.sessions.has(id)) next.set(id, S.sessions.get(id));
      for (const [id, s] of S.sessions) if (!next.has(id)) next.set(id, s);
      S.sessions = next;
      renderTabs(); renderLeft();
    } else if (m.t === "groups") {
      S.groups = m.groups;
      if (EMBED) return;
      if (!S.groups.some(g => g.id === S.group)) switchGroup(S.groups[0].id, { quiet: true, focus: false });
      else renderAll();
    } else if (m.t === "card") {
      upsertCard(m.card);
    } else if (m.t === "layout") {  // the tabs changed (here or on another page): as the server has them
      layoutEvent(m);
    } else if (m.t === "side") {  // a session's side tabs changed (here or on another screen): as the server has them
      if (!EMBED && m.sid) applySideState(m.sid, m.side);
    } else if (m.t === "cardp") {
      const list = S.cards.get(m.sid);
      onCardPatch(m, list && list.find(x => x.id === m.id), upsertCard);
    } else if (m.t === "schedules") {
      S.schedules = m.schedules; if (m.archived) S.schedArchived = m.archived; schedulesChanged();
    } else if (m.t === "plugin-badges") {
      PUI.badges = m.badges || {}; renderLeft();
    } else if (m.t === "plugin-open-tab") {
      if (!EMBED && PUI.local) openPluginTab(m.plugin, m.kind, m.key, { title: m.title, focus: m.focus });
    } else if (m.t === "plugin-notify") {
      if (!EMBED) pluginNotified(m.note);
    } else if (m.t === "plugin-status") {
      PUI.status = m.status || []; renderPluginStatus();
    } else if (m.t === "plugin-items" || m.t === "external-sessions") {  // (1.3) an open ⌘O on it redraws
      if (modal && modal.kind === "finder" && modal.changed) modal.changed(m);
      if (m.t === "plugin-items") pviewItemsEvent(m);  // (2.0) a list showing it
    } else if (m.t === "plugin-thread") {  // (2.0) a thread a view shows changed
      pviewThreadEvent(m);
    } else if (m.t === "plugin-doc") {
      pviewDocEvent(m);
    } else if (m.t === "settings") {
      const was = !!S.settings.calendar, notesWere = S.settings.meeting_notes;
      S.settings = m.settings;
      if (notesWere !== S.settings.meeting_notes && typeof XN !== "undefined" && !XN.editing) { XN.cache.clear(); XN.open = null; if (xKind() === "calendar") renderX(); }
      if (was !== !!S.settings.calendar) { S.calCache.clear(); S.calToday = null; loadCalToday(); if (xOn()) loadX(); }
    } else if (m.t === "note_append") {
      noteAppended(m);
    } else if (m.t === "prep") {
      prepFailed(m);
    } else if (m.t === "remind") {
      showReminder(m);
    } else if (m.t === "open" || m.t === "show") {
      if (!EMBED) agentOpened(m);
    } else if (m.t === "closed") {
      if (EMBED) { S.sessions.delete(m.id); if (m.id === S.current && !EMBED_REVIEW && !S.file) renderConv(); return; }
      if (S.sessions.has(m.id)) removeSession(m.id);
    }
  };
  es.onerror = () => { if (!EMBED) $("#sb-state").innerHTML = `<span class="dot notready"></span><span>server unreachable — retrying</span>`; };
}
/* An agent ran `orbit-agent open` / `orbit-agent show`: add the tab, never move the keyboard (interaction.md rule 5). */
function agentOpened(m) {
  const who = m.by ? m.by + " " : "An agent ";
  if (m.t === "show" || m.side) {
    // beside the session that asked (its tab's side tabs), not whatever is in front; elsewhere: a toast to go there
    const s = m.sid && S.sessions.get(m.sid), key = s ? "s:" + s.id : sideKey(), here = key === sideKey();
    const spec = m.t === "show" ? { kind: "web", url: m.url, title: m.title } : { kind: "file", path: m.path, line: m.line || undefined };
    if (!s) openSide(spec, { focus: false, key });  // (a session's: the server put it there; its side event shows it)
    const what = m.t === "show" ? `shows “${m.title}”` : `opened ${basename(m.path)}`;
    if (here) toast(`${who}${what} in the side tab`);
    else toast(`${who}${what} in its side tab — click to go there`, () => switchTo(s.id));
    return;
  }
  if (m.line) S.reveal = { path: m.path, line: m.line };
  const ft = fileTabs();
  const t = ft.find(x => x.path === m.path);
  if (t) t.pinned = true; else ft.push({ path: m.path, pinned: true });
  saveUi(); renderTabs();
  if (S.file === m.path) { if (m.line) applyReveal(m.path); }
  const go = () => { if (m.line) S.reveal = { path: m.path, line: m.line }; openFile(m.path, { pinned: true }); };
  toast(`${who}opened ${basename(m.path)}${m.line ? ":" + m.line : ""} — click to show`, go);
}

/* ===================================================================== context for agents (orbit-agent ctx) */
function ctxSelection() {
  if (S.file) {
    const ed = EDITORS.get(S.file);
    if (ed && ed.ed) { const v = ed.ed.view, r = v.state.selection.main; return v.state.sliceDoc(r.from, r.to); }
    return "";
  }
  const sel = window.getSelection();
  return sel && !sel.isCollapsed ? sel.toString() : "";
}
function reportCtx() {
  if (EMBED) return;
  clearTimeout(reportCtx.tm);
  reportCtx.tm = setTimeout(() => {
    const s = cur();
    const ctx = {
      session: s ? { id: s.id, name: s.name, cwd: s.cwd } : null,
      group: S.group ? groupPath(S.group) : "",
      files: fileTabs().map(t => t.path),
      side: S.side ? sideTitle(S.side) + (S.side.path ? ` (${S.side.path})` : S.side.url ? ` (${S.side.url})` : "") : "",
      selection: ctxSelection().slice(0, 20000),
    };
    if (S.file) {
      const ed = EDITORS.get(S.file);
      const tab = { kind: "file", path: S.file, dirty: !!(ed && ed.dirty) };
      if (ed && ed.ed) { const v = ed.ed.view; tab.line = v.state.doc.lineAt(v.state.selection.main.head).number; }
      ctx.tab = tab;
    } else ctx.tab = { kind: "session" };
    const key = JSON.stringify(ctx);
    if (key === reportCtx.last) return;
    reportCtx.last = key;
    api("POST", "/api/ctx", ctx).catch(() => { reportCtx.last = null; });
  }, 250);
}
document.addEventListener("selectionchange", () => reportCtx());
addEventListener("focus", () => { reportCtx.last = null; reportCtx(); });

function removeFromView(sid, old) {
  const ids = [...S.sessions.values()].filter(s => s.id === sid ? inView(old) : inView(s)).map(s => s.id);
  const i = ids.indexOf(sid);
  const rest = ids.filter(x => x !== sid);
  S.current = rest[Math.min(Math.max(i, 0), rest.length - 1)] || null;
  if (!S.file) S.lastTab[S.group] = S.current ? "s:" + S.current : null;
  showCurrent(document.activeElement === input);
}
setInterval(() => {
  for (const c of S.cards.get(S.current) || []) {
    if (!ACTIVE.has(c.state)) continue;
    const card = document.getElementById("card-" + c.id), el = card && card.querySelector("[data-foot]");  // (an id may hold a dot: 356.501)
    if (el) el.textContent = footHtml(c);
  }
}, 1000);
