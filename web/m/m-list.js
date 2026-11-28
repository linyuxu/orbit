"use strict";
/* ===================================================================== Sessions (home)
   The desktop's left panel for a phone: the user's groups in their order (the Fleet last), each
   session a two-line row (mark · agent · name · machine · when; then what it last said or is doing). A "Working now"
   strip on top while any session works; pull down to refresh; a dot for an answer not seen yet. */

/* The unread dot: a final answer newer than the last one seen here. A session seen for the first time is "seen". */
function seenInit() {
  if (S.seen) return;
  S.seen = {};
  for (const s of S.sessions.values()) S.seen[s.id] = (s.last && s.last.time) || 0;
  LS.set("seen", S.seen);
}
function isUnread(s) {
  if (!(s.id in S.seen)) { S.seen[s.id] = (s.last && s.last.time) || 0; LS.set("seen", S.seen); return false; }
  return !!(s.last && s.last.time && s.last.time > S.seen[s.id] + 0.001 && s.id !== S.current);
}
function markSeen(sid) {
  const s = S.sessions.get(sid); if (!s) return;
  const t = (s.last && s.last.time) || 0;
  if (S.seen[sid] !== t) { S.seen[sid] = t; LS.set("seen", S.seen); }
}
function lastMoved(s) { return isMoving(s) ? "now" : ago((s.last && s.last.time) || s.created); }
function sessionPreview(s) {
  if (s.state === "working") {
    const c = S.active.get(s.id);
    return { text: c ? "Working: " + plain(c.text) : "Working…" };
  }
  if (s.agents > 0) return { text: `${agentsWord(s.agents)} working` + (s.last && s.last.text ? ": " + plain(s.last.text) : "") };
  if (s.last && s.last.text) return { text: (s.last.state === "error" ? "Failed: " : "") + plain(s.last.text) };
  return { text: tilde(s.cwd), dim: true };
}
const elapsedOf = sid => { const c = S.active.get(sid); return c && c.started ? c.started : 0; };

function listViews() {
  const all = [...S.sessions.values()];
  const views = [];
  for (const g of groupKids("")) {  // the top groups; a subgroup is a row in its parent, opened as a page of its own
    const ss = all.filter(s => s.group === g.id && !s.fleet);
    views.push({ gid: g.id, name: g.name, ss, kids: groupKids(g.id) });  // (an empty group too: it is the user's)
  }
  const known = new Set(S.groups.map(g => g.id));
  const lost = all.filter(s => !s.fleet && !known.has(s.group));  // (a group this server does not list: still shown)
  if (lost.length) views.push({ gid: "other", name: "Other", ss: lost });
  const fleet = all.filter(s => s.fleet);
  if (fleet.length) views.push({ gid: "fleet", name: "Fleet", fleet: true, ss: fleet });
  return views;
}
/* A subgroup: one row (a folder, its name, working/all of its whole subtree, a chevron); a tap opens it as a page. */
function subRow(g) {
  const ids = new Set(groupSubtree(g.id)), ss = [...S.sessions.values()].filter(s => !s.fleet && ids.has(s.group));
  const working = ss.filter(isMoving).length;
  return `<button class="frow grow" data-sub="${esc(g.id)}" style="${g.color ? `--gc:${esc(g.color)}` : ""}"><span class="fi">${groupIcon(g, 16) || I("folder", 15)}</span><span class="nm">${esc(g.name)}</span>` +
    `<span class="wh">${working ? `${working}/` : ""}${ss.length}</span><span class="chv">${I("chev", 12)}</span></button>`;
}
/* What a group holds, as rows: its sessions, its other tabs, then its subgroups. */
const groupRowsHtml = (ss, gid) => ss.map(rowHtml).join("") + groupTabs(gid).map(ftabRow).join("") + groupKids(gid).map(subRow).join("");
/* A group's home (pushed over the list; Back, or a swipe from the left edge, comes back), as the Mac's (home.js): its
   head (cover, colour, icon, path, what it holds), then its widgets in their order, one column: its sessions, its
   subgroups, its tabs, what was pinned, its notes (their checkboxes tick here), its schedules, what happened lately. */
const GROUP_PAGES = new Set();
const M_HOME = [{ id: "stats", kind: "stats" }, { id: "heatmap", kind: "heatmap" }, { id: "sessions", kind: "sessions" }, { id: "groups", kind: "groups" }, { id: "pins", kind: "pins" },
                { id: "tabs", kind: "tabs" }, { id: "schedules", kind: "schedules" }, { id: "activity", kind: "activity" }];
const M_TITLE = { sessions: "Sessions", groups: "Subgroups", tabs: "Files and views", schedules: "Schedules", activity: "Lately", pins: "Pinned" };
const MD_FILES = new Map();  // path → {text, hash} | {error}
async function loadMdFile(path, page) {
  try { const f = await api("GET", "/api/file?path=" + encodeURIComponent(path)); MD_FILES.set(path, { text: f.text || "", hash: f.hash }); }
  catch (e) { if (!(e instanceof NotPaired)) MD_FILES.set(path, { error: e.message }); }
  if (page) page.refresh();
}
const groupIcon = (g, size = 16) => !g || !g.icon ? "" : g.icon.startsWith("img:") ? `<img class="gicon" src="${esc(withToken("/api/raw?path=" + encodeURIComponent(g.icon.slice(4))))}" alt="" style="width:${size}px;height:${size}px">`
  : `<span class="gicon" style="font-size:${Math.round(size * 0.9)}px">${esc(g.icon)}</span>`;
function mParsePins(text) {
  const out = [], re = /<!-- pin:([0-9a-f]{10}) -->\n## ([^\n]*)\n([\s\S]*?)(?=<!-- pin:[0-9a-f]{10} -->\n|$)/g;
  let m; while ((m = re.exec(text || ""))) out.push({ id: m[1], head: m[2].replace(/\s*·\s*\[source\]\([^)]*\)/, ""), src: (/\((orbit:\/\/session\/[^)]+)\)/.exec(m[2]) || [])[1] || "", body: m[3].trim() });
  return out;
}
function withTasksM(html) { let n = 0; return html.replace(/<li>(\s*<p>)?\[( |x|X)\]\s/g, (_, p, x) => `<li class="task">${p || ""}<input type="checkbox" data-task="${n++}"${x !== " " ? " checked" : ""}> `); }
function mWidgetHtml(gid, w, page) {
  const ss = [...S.sessions.values()].filter(s => s.group === gid && !s.fleet);
  // (a widget's own look, as on the Mac: its icon before its title, its colour's tint, a picture behind it)
  const look = `${w.color ? " wtint" : ""}${w.bg ? " wpic" : ""}" style="${w.color ? `--wc:${esc(w.color)};` : ""}${w.bg ? `--wbg:url('${esc(withToken("/api/raw?path=" + encodeURIComponent(w.bg)))}')` : ""}`;
  const head = title => `<div class="hst">${groupIcon(w.icon ? { icon: w.icon } : null, 14)}${esc(w.title || title)}</div>`;
  const sec = (title, body) => body ? `<section class="hsec${look}" data-w="${esc(w.id)}">${head(title)}<div class="rows glist">${body}</div></section>` : "";
  if (w.kind === "sessions") return sec("Sessions", ss.map(rowHtml).join(""));
  if (w.kind === "groups") return sec("Subgroups", groupKids(gid).map(subRow).join(""));
  if (w.kind === "tabs") return sec("Files and views", groupTabs(gid).map(ftabRow).join(""));
  if (w.kind === "schedules") {
    const ids = new Set(groupSubtree(gid)), sids = new Set([...S.sessions.values()].filter(s => ids.has(s.group)).map(s => s.id));
    const xs = (S.schedules || []).filter(x => sids.has(x.sid));
    return sec("Schedules", xs.map(x => `<div class="frow"><span class="fi">${I("clock", 15)}</span><span class="nm">${esc(x.name)}</span><span class="wh">${x.enabled ? "next " + esc(fmtNext(x.next) || "—") : "off"}</span></div>`).join(""));
  }
  if (w.kind === "activity") {
    const ids = new Set(groupSubtree(gid));
    const xs = [...S.sessions.values()].filter(s => !s.fleet && ids.has(s.group) && s.last && s.last.time).sort((a, b) => b.last.time - a.last.time).slice(0, 5);
    return sec("Lately", xs.map(rowHtml).join(""));
  }
  if (w.kind === "pins") {
    const g = S.groups.find(x => x.id === gid), path = g && g.pins;
    if (!path) return "";
    const c = MD_FILES.get(path);
    if (!c) { loadMdFile(path, page); return ""; }
    const ps = c.error ? [] : mParsePins(c.text);
    return ps.length ? `<section class="hsec${look}">${head("Pinned")}${ps.map(p => `<div class="hpin"><div class="ph">${esc(p.head)}${p.src ? ` · <a data-src="${esc(p.src)}">source</a>` : ""}</div><div class="pb md">${renderMd(p.body)}</div></div>`).join("")}</section>` : "";
  }
  if (w.kind === "stats" || w.kind === "heatmap") {  // (homestats.js, as on the Mac; Agent time's 14 bars: the Mac's only)
    return `<section class="hsec hstats${look}" data-w="${esc(w.id)}">${head(w.kind === "stats" ? "This week" : "Activity")}` +
      `<div class="hsbody">${statsBody(gid, w, () => { if (page.gid === gid) page.refresh(); })}</div></section>`;
  }
  if (w.kind === "md") {
    const c = MD_FILES.get(w.path);
    if (!c) { loadMdFile(w.path, page); return `<section class="hsec${look}">${head(basename(w.path))}${loadingHtml()}</section>`; }
    return `<section class="hsec${look}" data-md="${esc(w.path)}">${head(basename(w.path).replace(/\.md$/i, ""))}` +
      (c.error ? `<div class="hmd dim">${esc(c.error)}</div>` : `<div class="hmd md">${withTasksM(renderMd(c.text))}</div>`) + `</section>`;
  }
  return "";
}
function groupPage(gid) {
  const page = makePage({ title: groupName(gid) });
  page.gid = gid;
  page.refresh = () => {
    const g = S.groups.find(x => x.id === gid);
    if (!g) { page.body.innerHTML = emptyBox("That group is gone", "It was deleted, on this screen or another."); return; }
    page.setTitle(g.name, g.parent ? groupPath(g.parent) : "");
    const all = [...S.sessions.values()].filter(s => !s.fleet && groupSubtree(gid).includes(s.group)), w = all.filter(isMoving).length;
    const head = `<div class="hhead${g.cover ? " cover" : ""}" style="${g.color ? `--gc:${esc(g.color)};` : ""}${g.cover ? `--cover:url('${esc(withToken("/api/raw?path=" + encodeURIComponent(g.cover)))}')` : ""}">` +
      `<div class="htitle">${groupIcon(g, 26)}<h1>${esc(g.name)}</h1></div><div class="hsum">${all.length} session${all.length === 1 ? "" : "s"}${w ? ` · ${w} working` : ""}${groupKids(gid).length ? ` · ${groupKids(gid).length} subgroup${groupKids(gid).length === 1 ? "" : "s"}` : ""}</div></div>`;
    const ws = (g.home && Array.isArray(g.home.widgets) ? g.home.widgets : M_HOME).filter(x => !x.hidden);
    const body = ws.map(x => mWidgetHtml(gid, x, page)).join("");
    page.body.innerHTML = head + (body || emptyBox("Nothing here yet", "Its sessions and tabs show here. Start a session in it with <b>+</b> on the Groups list."));
  };
  page.body.addEventListener("click", async e => {
    const cb = e.target.closest("input[data-task]");
    if (cb) {
      const path = cb.closest("[data-md]").dataset.md, c = MD_FILES.get(path); if (!c || c.error) return;
      let i = -1; const n = +cb.dataset.task, on = cb.checked;
      const text = c.text.replace(/^(\s*[-*+]\s+)\[( |x|X)\]/gm, (all, pre) => (++i === n ? `${pre}[${on ? "x" : " "}]` : all));
      try { const r = await api("POST", "/api/file", { path, text, base_hash: c.hash }); MD_FILES.set(path, { text, hash: r.hash }); }
      catch (err) { if (!(err instanceof NotPaired)) toast(err.message); MD_FILES.delete(path); }
      page.refresh(); return;
    }
    const src = e.target.closest("[data-src]"); if (src) { const m = /orbit:\/\/session\/([^#)]+)/.exec(src.dataset.src); if (m && S.sessions.has(m[1])) openSession(m[1]); return; }
    const o = e.target.closest("[data-open]"); if (o) { openSession(o.dataset.open); return; }
    const ft = e.target.closest("[data-ftab]"); if (ft) { openFtab(ft.dataset.ftab); return; }
    const sb = e.target.closest("[data-sub]"); if (sb) openGroupPage(sb.dataset.sub);
  });
  page.onClose = () => GROUP_PAGES.delete(page);
  GROUP_PAGES.add(page);
  page.refresh();
  return page;
}
function openGroupPage(gid) { MD_FILES.clear(); pushPage(groupPage(gid), "sessions"); }  // (its notes and pins read fresh)
function rowHtml(s) {
  const pv = sessionPreview(s), unread = isUnread(s), stopped = s.state === "stopped";
  return `<button class="srow${unread ? " unread" : ""}${stopped ? " stopped" : ""}" data-open="${esc(s.id)}">` +
    `<span class="c-icon">${sessionMark(s, 16)}</span>` +
    `<span class="l1"><span class="nm">${esc(sessionTitle(s))}</span>${machineTag(s)}<span class="tm">${esc(lastMoved(s))}</span></span>` +
    `<span class="l2${pv.dim ? " dim" : ""}">${esc(pv.text)}</span></button>`;
}
/* ---- the tab layout every screen shares (orbit/desk/layout.py): a group's file tabs (files, notes, a plugin's views,
   Schedules, Calendar), in its tab row's order, under its sessions; a tap opens one where the app keeps that kind. */
async function loadLayoutTabs() {
  try { S.layout = await api("GET", "/api/layout"); renderList(); } catch {}
}
function groupTabs(gid) {
  const L = S.layout && S.layout.layout; if (!L) return [];
  const ord = ((L.order || {})[gid]) || [], pos = p => { const k = ord.indexOf("f:" + p); return k < 0 ? 1e9 : k; };
  return [...(((L.ftabs || {})[gid]) || [])].filter(t => t && t.path).sort((a, b) => pos(a.path) - pos(b.path));
}
const parsePluginTab = p => { const m = /^plugin:([a-z0-9_-]+)\/(~doc|~list|[a-z0-9_-]+)\/(.*)$/.exec(p || ""); try { return m && { plugin: m[1], kind: m[2], key: decodeURIComponent(m[3]) }; } catch { return null; } };
function ftabInfo(t) {
  const p = t.path;
  if (p === "orbit:calendar") return { icon: "calendar", title: "Calendar", sub: "" };
  if (p === "orbit:schedules") return { icon: "clock", title: "Schedules", sub: "" };
  const pv = parsePluginTab(p);
  if (pv) return { icon: "plug", title: t.title || pv.key || pv.kind, sub: pv.plugin };
  const m = /^\/@([\w-]+)(\/.*)$/.exec(p);
  return { icon: isNotePath(p) ? "note" : "file", title: basename(p), sub: m ? `${m[1]}: ${tilde(dirname(m[2]))}` : tilde(dirname(p)) };
}
function ftabRow(t) {  // (one quiet line: the sessions are what the list is for)
  const f = ftabInfo(t);
  return `<button class="frow" data-ftab="${esc(t.path)}"><span class="fi">${I(f.icon, 15)}</span><span class="nm">${esc(f.title)}</span>` +
    (f.sub ? `<span class="wh">${esc(f.sub)}</span>` : "") + `</button>`;
}
function openFtab(path) {
  const t = Object.values((S.layout && S.layout.layout.ftabs) || {}).flat().find(x => x && x.path === path) || { path };
  const go = (area, page) => { pushPage(page, "sessions"); if (page.refresh) page.refresh(); };  // (here, over the list: Back comes back to it)
  if (path === "orbit:calendar") { go("misc", calendarPage()); loadMisc(); return; }
  if (path === "orbit:schedules") { go("misc", schedulesPage()); loadMisc(); return; }
  const pv = parsePluginTab(path);
  if (pv) {
    go("misc", pv.kind === "~doc" ? pdocPage(pv.plugin, pv.key, t.title) : pv.kind === "~list" ? plistPage({ plugin: pv.plugin, source: pv.key, title: t.title || pv.key })
      : pthreadPage(pv.plugin, pv.kind, pv.key, t.title));
    return;
  }
  if (isNotePath(path)) go("notes", notePage(path)); else go("files", filePage(path));
}
function workingStripHtml() {
  const ws = [...S.sessions.values()].filter(s => isMoving(s) || s.attention);
  if (!ws.length) return "";
  return `<section class="wstrip"><div class="shead"><span class="gn">Working now</span><span class="cnt">${ws.length}</span></div>` +
    `<div class="wrow">${ws.map(s => {
      const t0 = s.state === "working" ? elapsedOf(s.id) : 0;  // (only subagents at work: how many, no request's time)
      return `<button class="wchip" data-open="${esc(s.id)}">${sessionMark(s, 16)}<span class="nm">${esc(sessionTitle(s))}</span>` +
        `${t0 ? `<span class="el" data-t0="${t0}">${dur(Date.now() / 1000 - t0)}</span>` : s.state !== "working" && s.agents > 0 ? `<span class="el">${agentsWord(s.agents)}</span>` : ""}</button>`;
    }).join("")}</div></section>`;
}
function renderList() {
  if (!S.loaded) return;
  seenInit();
  const views = listViews();
  const box = $("#slist");
  const none = !views.some(v => v.ss.length);  // (the groups show anyway, empty ones too)
  box.innerHTML = workingStripHtml() + views.map(v => {
    const shut = S.folded.has(v.gid);
    const all = v.kids ? [...S.sessions.values()].filter(s => !s.fleet && groupSubtree(v.gid).includes(s.group)) : v.ss;  // (its count: its whole subtree)
    const working = all.filter(isMoving).length;
    return `<section class="grp${shut ? " shut" : ""}"><button class="shead" data-fold="${esc(v.gid)}" aria-expanded="${!shut}">` +
      `${v.fleet ? `<span class="gstar">${I("zap", 12)}</span>` : groupIcon(S.groups.find(g => g.id === v.gid), 14)}<span class="gn"${v.kids ? ` data-home="${esc(v.gid)}"` : ""}>${esc(v.name)}</span>` +
      `<span class="chev">${I("chevd", 12)}</span><span class="cnt">${working ? `${working}/` : ""}${all.length}</span></button>` +
      (shut ? "" : `<div class="rows">${groupRowsHtml(v.ss, v.gid)}</div>`) + `</section>`;
  }).join("") + (none ? `<div class="empty"><h2>No session yet</h2>Start one with <b>+</b> above: pick a folder, an agent and what to ask.</div>` : "");
  updateTitleBar();
  renderTabbar();  // (its count of answers not seen yet)
  for (const p of GROUP_PAGES) p.refresh();  // (a subgroup's page open over it: the same news)
}
/* The big title gives way to the small one in the bar when it scrolls under it (iOS's large title). */
function updateTitleBar() {
  const sc = $("#lscroll"), big = $("#bigtitle");
  const under = big ? sc.scrollTop > big.offsetTop + big.offsetHeight - 8 : false;
  $("#vlist .nav").classList.toggle("scrolled", under);
}
$("#lscroll").addEventListener("scroll", updateTitleBar, { passive: true });
/* A long-press on a session: its menu (rename, close, …) without opening it. The click that ends the press does not open it. */
const PRESS = { tm: 0, x: 0, y: 0, fired: false };
$("#slist").addEventListener("pointerdown", e => {
  const o = e.target.closest("[data-open]"); if (!o || e.button > 0) return;
  PRESS.fired = false; PRESS.x = e.clientX; PRESS.y = e.clientY;
  clearTimeout(PRESS.tm);
  PRESS.tm = setTimeout(() => { PRESS.fired = true; if (navigator.vibrate) navigator.vibrate(8); sessionMenu(o.dataset.open); }, 520);
});
$("#slist").addEventListener("pointermove", e => { if (Math.hypot(e.clientX - PRESS.x, e.clientY - PRESS.y) > 10) clearTimeout(PRESS.tm); });
for (const ev of ["pointerup", "pointercancel", "pointerleave"]) $("#slist").addEventListener(ev, () => clearTimeout(PRESS.tm));
$("#slist").addEventListener("contextmenu", e => { if (e.target.closest("[data-open]")) e.preventDefault(); });
$("#slist").addEventListener("click", e => {
  if (PRESS.fired) { PRESS.fired = false; e.preventDefault(); return; }
  const hm = e.target.closest("[data-home]");  // a group's name: its home page (the chevron folds it)
  if (hm) { openGroupPage(hm.dataset.home); return; }
  const f = e.target.closest("[data-fold]");
  if (f) { const g = f.dataset.fold; S.folded.has(g) ? S.folded.delete(g) : S.folded.add(g); LS.set("folded", [...S.folded]); renderList(); return; }
  const o = e.target.closest("[data-open]");
  if (o) { openSession(o.dataset.open); return; }
  const ft = e.target.closest("[data-ftab]");
  if (ft) { openFtab(ft.dataset.ftab); return; }
  const sb = e.target.closest("[data-sub]");
  if (sb) openGroupPage(sb.dataset.sub);
});

/* ===================================================================== pull to refresh
   A list's own (the Sessions list's, a plugin's list): the native bounce is off (overscroll-behavior), so a pull from
   the top moves the list down after the finger (damped); past the line, letting go runs `refresh` (the spinner stays
   until it is done, half a second at least). */
const PULL_SVG = '<svg width="22" height="22" viewBox="0 0 16 16"><circle cx="8" cy="8" r="5.25" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-dasharray="24 9"/></svg>';
function addPull(sc, refresh) {
  const P = { y0: null, d: 0, busy: false, LINE: 64, HOLD: 52 };
  if (!$(".pull", sc)) sc.insertAdjacentHTML("afterbegin", `<div class="pull" aria-hidden="true">${PULL_SVG}</div>`);
  sc.classList.add("haspull");
  const ind = $(".pull", sc), inner = $(".inner", sc);
  const set = (d, anim) => {
    P.d = d;
    inner.style.transition = ind.style.transition = anim ? "transform .25s ease" : "none";
    inner.style.transform = d ? `translateY(${d}px)` : "";
    ind.style.transform = `translateY(${Math.min(d, P.HOLD + 12) - 40}px)`;
    ind.style.opacity = Math.min(1, d / P.LINE);
    ind.querySelector("svg").style.transform = P.busy ? "" : `rotate(${Math.round(d / P.LINE * 270)}deg)`;
    ind.classList.toggle("armed", d >= P.LINE || P.busy);
  };
  sc.addEventListener("touchstart", e => {
    P.y0 = !P.busy && sc.scrollTop <= 0 && e.touches.length === 1 ? e.touches[0].clientY : null;
  }, { passive: true });
  sc.addEventListener("touchmove", e => {
    if (P.y0 == null) return;
    const dy = e.touches[0].clientY - P.y0;
    if (dy <= 0) { if (P.d) set(0); return; }
    if (sc.scrollTop > 0) { P.y0 = null; return; }
    e.preventDefault();
    set(Math.round(dy < 40 ? dy * .6 : 24 + (dy - 40) * .4));
  }, { passive: false });
  sc.addEventListener("touchend", async () => {
    if (P.y0 == null) return;
    P.y0 = null;
    if (P.d < P.LINE) { set(0, true); return; }
    P.busy = true;
    ind.classList.add("busy");
    set(P.HOLD, true);
    const t0 = Date.now();
    try { await refresh(); } catch {}
    await new Promise(r => setTimeout(r, Math.max(0, 500 - (Date.now() - t0))));  // (long enough to be seen)
    P.busy = false;
    ind.classList.remove("busy");
    set(0, true);
  });
}
addPull($("#lscroll"), async () => { await loadAll(); reconnect(); });
