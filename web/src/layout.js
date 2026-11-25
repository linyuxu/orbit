"use strict";
/* ===================================================================== the tab layout, shared (orbit/desk/layout.py)
   The file tabs of each group (files, notes, plugin views, Schedules, Calendar), the tab row's order, and a file tab's
   side tabs are the server's, like the sessions: every page shows the same. A change here goes there whole, a moment
   after (saveUi calls layoutChanged; the revision it started from goes with it: a page behind the others takes the
   current layout instead); a change anywhere comes back as a "layout" event. Which tab this page shows, a side panel
   put away here, the width: this page's own. A session's side tabs are sides.py's (side.js). */
const LAYOUT = { rev: null, sent: "", tm: 0, ready: false, base: null };  // (base: the layout as the server last had it)
function layoutNow() {
  const sides = {};
  for (const [k, e] of Object.entries(S.sides)) if (k.startsWith("f:") && e && e.list && e.list.length)
    sides[k] = { list: e.list.map(x => ({ ...x })), i: e.i | 0 };
  return { ftabs: S.ftabs, order: S.tabOrder, sides };
}
/* The server's layout, shown: file tabs gone close here too (their frames dropped), a file shown here that is no
   longer a tab gives way to the session. */
function applyLayout(L, rev) {
  if (!L) return;
  LAYOUT.rev = rev; LAYOUT.ready = true; LAYOUT.base = JSON.parse(JSON.stringify(L));
  S.ftabs = L.ftabs && typeof L.ftabs === "object" ? L.ftabs : {};
  S.tabOrder = L.order && typeof L.order === "object" ? L.order : {};
  const sides = L.sides && typeof L.sides === "object" ? L.sides : {};
  for (const k of Object.keys(S.sides)) {
    if (!k.startsWith("f:")) continue;
    const old = S.sides[k], now = sides[k], ids = new Set(now ? now.list.map(x => x.id) : []);
    for (const x of (old && old.list) || []) if (!ids.has(x.id)) dropFrame(x.id);
    if (!now) delete S.sides[k];
  }
  for (const [k, e] of Object.entries(sides)) {
    if (!e || !Array.isArray(e.list) || !e.list.length) continue;
    const old = S.sides[k];
    S.sides[k] = { list: e.list.map(x => ({ ...x })), i: Math.max(0, Math.min(e.i | 0, e.list.length - 1)), hidden: !!(old && old.hidden) };
  }
  LAYOUT.sent = JSON.stringify(layoutNow());
  LS.set("ftabs", S.ftabs); LS.set("tabOrder", S.tabOrder); LS.set("sides", S.sides);
  if (S.file && S.file !== HOME && !fileTabs().some(t => t.path === S.file)) { S.file = null; showCurrent(false); }
  else { renderTabs(); renderSide(); renderReviewBadge(); }
}
async function loadLayout() {
  if (EMBED) return;
  try {
    const d = await api("GET", "/api/layout");
    const mine = layoutNow(), had = Object.values(mine.ftabs).some(l => l && l.length) || Object.keys(mine.order).length;
    if (!d.rev && had && !LS.get("layoutMoved", false)) {  // the tabs this page had before the server kept them: they go there once
      LS.set("layoutMoved", true);
      LAYOUT.rev = 0; LAYOUT.ready = true;
      return layoutPush();
    }
    LS.set("layoutMoved", true);
    applyLayout(d.layout, d.rev);
  } catch {}
}
/* saveUi: what changed goes to the server, a moment after (several changes at once: one write). */
function layoutChanged() {
  if (EMBED || !LAYOUT.ready) return;
  clearTimeout(LAYOUT.tm);
  LAYOUT.tm = setTimeout(layoutPush, 250);
}
async function layoutPush() {
  const now = layoutNow(), text = JSON.stringify(now);
  if (text === LAYOUT.sent) return;
  LAYOUT.sent = text;
  try {
    const d = await api("POST", "/api/layout", { layout: now, base: LAYOUT.rev });
    LAYOUT.rev = d.rev; LAYOUT.base = JSON.parse(text);
  } catch (e) {
    if (e.status === 409 && e.data && e.data.layout) {  // another page changed them first: what this page changed, on top of theirs
      const merged = mergeLayout(LAYOUT.base || { ftabs: {}, order: {}, sides: {} }, now, e.data.layout);
      applyLayout(e.data.layout, e.data.rev);
      applyLayout(merged, e.data.rev); LAYOUT.base = JSON.parse(JSON.stringify(e.data.layout));
      LAYOUT.sent = "";
      if ((layoutPush.tries = (layoutPush.tries || 0) + 1) <= 3) return layoutPush();
    } else LAYOUT.sent = "";  // (not sent: the next change tries again)
  } finally { if (!LAYOUT.tm) layoutPush.tries = 0; }
}
/* A three-way merge: the tabs this page added since base, and not those it closed, put on the server's (theirs). */
function mergeLayout(base, mine, theirs) {
  const out = { ftabs: {}, order: {}, sides: { ...(theirs.sides || {}) } };
  const groups = new Set([...Object.keys(theirs.ftabs || {}), ...Object.keys(mine.ftabs || {})]);
  for (const g of groups) {
    const b = new Set(((base.ftabs || {})[g] || []).map(t => t.path)), m = (mine.ftabs || {})[g] || [];
    const mp = new Set(m.map(t => t.path)), closed = [...b].filter(p => !mp.has(p));
    const list = ((theirs.ftabs || {})[g] || []).filter(t => !closed.includes(t.path));
    for (const t of m) if (!b.has(t.path) && !list.some(x => x.path === t.path)) list.push(t);
    out.ftabs[g] = list;
  }
  const og = new Set([...Object.keys(theirs.order || {}), ...Object.keys(mine.order || {})]);
  for (const g of og) {
    const bo = (base.order || {})[g] || [], mo = (mine.order || {})[g] || [], to = (theirs.order || {})[g] || [];
    const moved = JSON.stringify(bo) !== JSON.stringify(mo);
    out.order[g] = moved ? mo : to;  // (this page arranged the row: its order; else theirs)
  }
  for (const [k, e] of Object.entries(mine.sides || {})) if (JSON.stringify(e) !== JSON.stringify((base.sides || {})[k])) out.sides[k] = e;
  for (const k of Object.keys(base.sides || {})) if (!(mine.sides || {})[k]) delete out.sides[k];
  return out;
}
/* The live stream: the layout changed (here, or on another page). */
function layoutEvent(m) {
  if (EMBED || (LAYOUT.rev != null && m.rev <= LAYOUT.rev)) return;
  if (JSON.stringify(m.layout) === LAYOUT.sent) { LAYOUT.rev = m.rev; return; }  // (this page's own, back: a change made since stays)
  applyLayout(m.layout, m.rev);
}
