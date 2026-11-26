"use strict";
/* ===================================================================== Files tree (left panel) */
async function refreshTree(root, force = false) {
  if (!root || EMBED) return;
  const t = S.trees.get(root);
  if (t && !force && Date.now() - t.at < 1500) return;
  try {
    const d = await api("GET", "/api/tree?root=" + encodeURIComponent(root));
    d.at = Date.now();
    // Unchanged (the 5-second refresh, mostly): not drawn again. A redraw replaces the rows under the pointer.
    d.sig = JSON.stringify([d.files, d.dirs, d.status, d.branch, d.git, d.truncated]);
    if (!force && t && !t.error && t.sig === d.sig) { t.at = d.at; return; }
    d.index = buildIndex(d.files, d.dirs || []);  // (the empty folders too: no file lists them)
    d.notes = d.files.filter(f => /\.md$/i.test(f));
    d.noteIndex = buildIndex(d.notes);
    d.dirty = new Set();
    for (const p of Object.keys(d.status)) { const parts = p.split("/"); for (let i = 1; i < parts.length; i++) d.dirty.add(parts.slice(0, i).join("/")); }
    S.trees.set(root, d);
  } catch (e) {
    S.trees.set(root, { error: e.message, at: Date.now(), files: [], index: buildIndex([]), status: {}, dirty: new Set() });
  }
  // (drawn outside the try: a drawing error is no failure of this folder's listing)
  if (S.left.shown && S.left.tab !== "sessions" && (panelRoot() === root || S.left.tab === "files")) renderLeft();
}
function buildIndex(files, emptyDirs = []) {
  const kids = new Map([["", { dirs: new Set(), files: [] }]]);
  for (const d of emptyDirs) {  // a folder with nothing in it: its own entry, under its parents
    const parts = d.split("/");
    let parent = "";
    for (let i = 0; i < parts.length; i++) {
      const dir = parts.slice(0, i + 1).join("/");
      if (!kids.has(dir)) kids.set(dir, { dirs: new Set(), files: [] });
      kids.get(parent).dirs.add(dir);
      parent = dir;
    }
  }
  for (const f of files) {
    const parts = f.split("/");
    let parent = "";
    for (let i = 0; i < parts.length - 1; i++) {
      const dir = parts.slice(0, i + 1).join("/");
      if (!kids.has(dir)) kids.set(dir, { dirs: new Set(), files: [] });
      kids.get(parent).dirs.add(dir);
      parent = dir;
    }
    kids.get(parent).files.push(f);
  }
  return kids;
}
const expandedSet = root => new Set(S.expanded[root] || []);
function setExpanded(root, set) { S.expanded[root] = [...set]; saveUi(); }
/* A tree's rows. keyed (the Files tab, several roots): each row says its root, its key has it, and it sits a level
   under the root's own row. */
function fileRows(root, notesOnly = false, keyed = false) {
  const t = S.trees.get(root);
  if (!t) return [];
  const list = notesOnly ? t.notes || [] : t.files, index = notesOnly ? t.noteIndex : t.index;
  const K = keyed ? root + "|" : "", d0 = keyed ? 1 : 0, at = keyed ? { root } : {};
  if (S.filter) return filteredRows(root, list, index, notesOnly, K, d0, at);
  const rows = [];
  const open = expandedSet(root);
  const walk = (dir, depth) => {
    const node = index.get(dir);
    if (!node) return;
    for (const d of [...node.dirs].sort((a, b) => a.localeCompare(b))) {
      rows.push({ kind: "dir", rel: d, depth, open: open.has(d), k: "d:" + K + d, ...at });
      if (open.has(d)) walk(d, depth + 1);
    }
    for (const f of [...node.files].sort((a, b) => a.localeCompare(b))) rows.push({ kind: "file", rel: f, depth, k: "f:" + K + f, ...at });
  };
  walk("", d0);
  return rows;
}
/* A filtered tree is still a tree: the files whose path matches (fuzzy, the best 300) and the folders whose name holds
   the text, under their folders, each folder on the way open; a folder matched by its name stays folded (opened: all
   it holds). Folded or opened by hand while filtering: FILTERED (forgotten when the filter changes). The cursor
   starts on the best match, so ⏎ opens it. */
const FILTERED = { q: "", shut: new Set(), open: new Set(), state: new Map(), cursorFor: "" };
function filteredRows(root, list, index, notesOnly, K, d0, at) {
  if (FILTERED.q !== S.filter) { FILTERED.q = S.filter; FILTERED.shut.clear(); FILTERED.open.clear(); FILTERED.state.clear(); }
  const q = S.filter, ql = q.toLowerCase().trim();
  const hits = list.map(f => ({ f, sc: fuzzy(q, notesOnly ? f.replace(/\.md$/i, "") : f) })).filter(x => x.sc >= 0).sort((a, b) => b.sc - a.sc).slice(0, 300);
  const score = new Map(hits.map(h => [h.f, h.sc])), anc = new Set(), dirHit = new Set();
  const up = p => { const parts = p.split("/"); for (let i = 1; i < parts.length; i++) anc.add(parts.slice(0, i).join("/")); };
  for (const { f } of hits) up(f);
  if (ql.length >= 2 && !/\s/.test(ql)) for (const d of index.keys()) if (d && basename(d).toLowerCase().includes(ql) && dirHit.size < 50) { dirHit.add(d); up(d); }
  const rows = [];
  const walk = (dir, depth, all) => {  // all: every child (a folder opened by hand, matched by its name)
    const node = index.get(dir);
    if (!node) return;
    for (const d of [...node.dirs].sort((a, b) => a.localeCompare(b))) {
      if (!all && !anc.has(d) && !dirHit.has(d)) continue;
      const fk = root + "|" + d, def = !all && anc.has(d);
      const open = FILTERED.shut.has(fk) ? false : FILTERED.open.has(fk) || def;
      FILTERED.state.set(fk, open);
      rows.push({ kind: "dir", rel: d, depth, open, k: "d:" + K + d, hit: dirHit.has(d), ...at });
      if (open) walk(d, depth + 1, all || !anc.has(d));
    }
    for (const f of [...node.files].sort((a, b) => a.localeCompare(b))) {
      if (!all && !score.has(f)) continue;
      rows.push({ kind: "file", rel: f, depth, k: "f:" + K + f, hit: score.has(f), sc: score.get(f) ?? -1, ...at });
    }
  };
  walk("", d0, false);
  return rows;
}
/* Folded or opened while filtering (← →, a click): that filtered tree only. */
function toggleFilteredDir(rel, open, root) {
  const fk = root + "|" + rel, want = open ?? !FILTERED.state.get(fk);
  if (want) { FILTERED.open.add(fk); FILTERED.shut.delete(fk); } else { FILTERED.shut.add(fk); FILTERED.open.delete(fk); }
  renderLeft();
}
/* The cursor on the best match when the filter text changed (a tree's first row is a folder). */
function cursorToBest() {
  if (!S.filter) { FILTERED.cursorFor = ""; return; }
  if (FILTERED.cursorFor === S.filter) return;
  FILTERED.cursorFor = S.filter;
  const q = S.filter.toLowerCase().trim(), stem = r => basename(r.rel).toLowerCase().replace(/\.[^.]*$/, "");
  const rank = r => (stem(r) === q ? 2 : basename(r.rel).toLowerCase().startsWith(q) ? 1 : 0);  // (its very name first: "files" → files.js)
  const best = PROWS.filter(r => r.kind === "file" && r.hit).sort((a, b) => rank(b) - rank(a) || b.sc - a.sc)[0];
  if (best) S.pcursor = best.k;
}
const hitName = (r, name) => r.hit && S.filter && typeof boldTerms === "function" ? boldTerms(name, S.filter.split(/\s+/)) : esc(name);
const rowRoot = r => (r && r.root) || panelRoot();
const rowPath = r => r.path || rowRoot(r) + "/" + r.rel;
function revealFile(path) {
  if (isView(path)) return;
  const files = S.left.tab === "files";
  const R = files ? filesRoots() : null;  // (the session folder nearest to it; else the context's own root)
  const root = files ? [...R.all.map(f => f.path).sort((a, b) => b.length - a.length), focusCtx().root, ctxRoot()].find(r => r && path.startsWith(r + "/")) : panelRoot();
  if (!root || !path.startsWith(root + "/")) return;
  const rel = relTo(root, path), parts = rel.split("/");
  const open = expandedSet(root);
  for (let i = 1; i < parts.length; i++) open.add(parts.slice(0, i).join("/"));
  setExpanded(root, open);
  if (files) { setRootOpen(root, true); refreshTree(root); }
  S.pcursor = "f:" + (files ? root + "|" : "") + rel;
  if (files) FILES.want = { k: S.pcursor, t: Date.now() };  // (its root's tree may still be on its way: the cursor goes there when it comes)
}

/* ===================================================================== left panel: Sessions · Files · Notes · Misc */
const plist = $("#plist");
const PTABS = [["sessions", "Groups", 10], ["files", "Files", 20], ["notes", "Notes", 30], ["misc", "Misc", 40]];
const ptabs = () => [...PTABS, ...PUI.sidebars.map(x => [`p:${x.plugin}:${x.id}`, x.title, x.order])].sort((a, b) => a[2] - b[2]);
const panelFocused = () => !EMBED && !$("#left").hidden && $("#left").contains(document.activeElement);
function applyLeft() {
  if (EMBED) return;
  $("#left").hidden = !S.left.shown;
  $("#lsplit").hidden = !S.left.shown;
  document.documentElement.style.setProperty("--leftw", clampLeft(S.left.w) + "px");
  $("#lefttog").classList.toggle("on", S.left.shown);
  if (S.left.shown && treeTab()) refreshTree(panelRoot());
  renderLeft();
}
const treeTab = (tab = S.left.tab) => tab === "files" || tab === "notes";
/* A plugin's left tab that is a list (an item source, plugin API 2.0): drawn here like the Sessions list. */
const plistTab = (tab = S.left.tab) => { const sb = tab.startsWith("p:") && pluginSidebar(tab); return sb && sb.view === "list" ? sb : null; };
function clampLeft(w) { const vw = innerWidth / S.zoom; return Math.round(Math.max(Math.min(120, vw * 0.05), Math.min(w, vw * 0.95 - 120))); }
function toggleLeft() {  // three states: hidden → show + focus; shown, not focused → focus; focused → hide
  if (!S.left.shown) { S.left.shown = true; applyLeft(); focusPanel(); }
  else if (!panelFocused()) focusPanel();
  else { S.left.shown = false; S.filter = ""; applyLeft(); focusMain(); }
  saveUi();
}
function onlyMain() {  // ⌃X 1
  if (S.left.shown) { S.left.shown = false; S.filter = ""; applyLeft(); }
  if (SH.shown) { SH.shown = false; applyShell(); }
  closeAllSides();
  saveUi();
  focusMain();
}
function panelTab(d) {
  const all = ptabs(), i = all.findIndex(t => t[0] === S.left.tab), had = panelFocused();
  setPanelTab(all[(i + d + all.length) % all.length][0]);
  if (had) focusPanel();
}
function setPanelTab(tab) {
  if (tab !== S.left.tab) S.left.prev = S.left.tab;  // (a plugin tab's shortcut goes back to it)
  const same = tab === S.left.tab;  // (the tab in front clicked again: its cursor stays)
  S.left.tab = tab; S.filter = ""; if (!same) S.pcursor = null; saveUi();
  if (tab === "misc") loadCalToday();
  else if (plistTab(tab)) loadPluginList(plistTab(tab).plugin, plistTab(tab).source);
  else if (tab.startsWith("p:")) { /* a plugin's doc: it keeps itself */ }
  else if (tab !== "sessions") {  // (the file where the keyboard is: a side tab's too)
    refreshTree(panelRoot());
    const f = focusCtx().path;
    if (f && !same && (tab === "files" || isNote(f))) revealFile(f);
  }
  renderLeft();
}
/* Folded groups (their sessions hidden; a filter looks inside them anyway): kept in this browser. */
const FOLDED = new Set(LS.get("folded", []));
function setFolded(gid, shut) {
  if (shut === FOLDED.has(gid)) return;
  shut ? FOLDED.add(gid) : FOLDED.delete(gid);
  LS.set("folded", [...FOLDED]);
  S.pcursor = "g:" + gid;  // (the cursor stays on the group: its sessions may have gone)
  renderLeft();
}
function sessionRows() {
  const rows = [];
  const q = S.filter;
  /* A group and what it holds: its sessions and its other tabs, then its subgroups (each with what it holds).
     Its count is its whole subtree's (working/all), so a folded group still says when something works inside. A
     filter shows a group when anything inside it matches. */
  const block = (v, depth) => {
    const ss = [...S.sessions.values()].filter(s => inView(s, v.gid) && machineOk(s));
    const scored = ss.map(s => ({ s, sc: q ? Math.max(fuzzy(q, s.name), fuzzy(q, tilde(s.cwd)) - 1, fuzzy(q, v.path) - 2) : 0 })).filter(x => x.sc >= 0);
    // its other tabs (files, notes, views; the server's layout, every screen's), smaller, after its sessions, in the tab row's order
    const tabs = groupFileTabs(v.gid).filter(t => !q || fuzzy(q, ftabTitle(t.path)) >= 0 || fuzzy(q, tilde(t.path)) - 1 >= 0);
    const kids = v.gid === FAV ? [] : groupKids(v.gid).map(g => block({ gid: g.id, name: g.name, path: groupPath(g.id), ix: "", key: "" }, depth + 1));
    const working = ss.filter(s => s.state === "working" || s.agents > 0).length + kids.reduce((n, k) => n + k.working, 0);  // (subagents at work count)
    const total = ss.length + kids.reduce((n, k) => n + k.total, 0);
    const hit = !q || scored.length > 0 || tabs.length > 0 || kids.some(k => k.hit);
    const shut = !q && FOLDED.has(v.gid);
    const out = [{ kind: "group", gid: v.gid, name: v.name, ix: v.ix, key: v.key, depth, count: `${working}/${total}`, k: "g:" + v.gid, shut,
                   fold: ss.length + tabs.length + kids.length > 0 }];
    if (!shut) {
      if (q) scored.sort((a, b) => b.sc - a.sc);
      for (const { s } of scored) out.push({ kind: "session", gid: v.gid, sid: s.id, depth, k: `s:${v.gid}:${s.id}` });
      for (const t of tabs) out.push({ kind: "ftab", gid: v.gid, path: t.path, depth, k: `f:${v.gid}:${t.path}` });
      for (const k of kids) if (k.hit) out.push(...k.rows);  // (after its own rows: those stay right under its head)
    }
    return { rows: out, hit, working, total };
  };
  const tops = topGroups();
  const views = [{ gid: FAV, name: "Favorites", path: "Favorites", ix: "★", key: "⌘1" },
                 ...tops.map((g, i) => ({ gid: g.id, name: g.name, path: g.name, ix: i < 8 ? String(i + 2) : "", key: i < 8 ? `⌘${i + 2}` : "" }))];
  for (const v of views) { const b = block(v, 0); if (b.hit) rows.push(...b.rows); }
  // The Fleet: helpers (⌘K quick asks, Prep, `--fleet` schedules, orbit-agent ask). No tab: one opens in the side pane.
  const fleet = [...S.sessions.values()].filter(s => s.fleet && machineOk(s))
    .map(s => ({ s, sc: q ? Math.max(fuzzy(q, s.name), fuzzy(q, tilde(s.cwd)) - 1, fuzzy(q, "fleet") - 2) : 0 })).filter(x => x.sc >= 0);
  if (fleet.length) {
    const working = fleet.filter(x => x.s.state === "working").length;
    const shut = !q && FOLDED.has("fleet");
    rows.push({ kind: "fleetgrp", gid: "fleet", k: "g:fleet", name: "Fleet", count: working ? `${working}/${fleet.length}` : String(fleet.length), shut, fold: true });
    if (q) fleet.sort((a, b) => b.sc - a.sc);
    if (!shut) for (const { s } of fleet) rows.push({ kind: "session", gid: "fleet", sid: s.id, k: `s:fleet:${s.id}` });
  }
  if (!q) rows.push({ kind: "newgroup", k: "newgroup" });  // always in sight: ⏎ or a click makes a group
  return rows;
}
/* A group's file tabs (not sessions) in its tab row's order. */
function groupFileTabs(gid) {
  const ord = S.tabOrder[gid] || [], pos = p => { const k = ord.indexOf("f:" + p); return k < 0 ? 1e9 : k; };
  return [...(S.ftabs[gid] || [])].sort((a, b) => pos(a.path) - pos(b.path));
}
const ftabTitle = p => isView(p) ? viewInfo(p).title : isNote(p) ? noteTitle(p) : basename(p);
let PROWS = [];
/* A session's title in lists: its name, and · a short key when another open session has the same name. */
function sessionTitle(s) {
  const twin = [...S.sessions.values()].some(x => x.id !== s.id && x.name === s.name);
  return twin ? `${s.name} · ${s.id.slice(0, 4)}` : s.name;
}
/* A press on a row of the Files panel opens it on the click: the rows are not redrawn until the button is up. A
   redraw in between (the panel taking the keyboard, a tree refresh, an agent's step) put new rows under the pointer:
   the click hit none (a folder did not open) and a drag did not start. Drawn once the press ends. */
const PRESS = { on: false, later: false };
function pressEnd() {
  if (!PRESS.on) return;
  PRESS.on = false;
  setTimeout(() => { if (PRESS.later) renderLeft(); }, 0);  // (after the click, which may have drawn them already)
}
for (const ev of ["mouseup", "dragstart"]) addEventListener(ev, pressEnd, true);
addEventListener("blur", pressEnd);
function renderLeft() {
  if (EMBED || $("#left").hidden) return;
  if (DRAG.active) { DRAG.pending.add(renderLeft); return; }  // (a drag is moving its rows: after it)
  if (PRESS.on) { PRESS.later = true; $("#left").classList.toggle("kb", panelFocused()); return; }
  PRESS.later = false;
  $("#left").classList.toggle("kb", panelFocused());
  const tab = S.left.tab;
  $("#ptabs").innerHTML = ptabs().map(([id, label]) => {  // a plugin's tab: its icon when it has one (the title on hover)
    const sb = id.startsWith("p:") && pluginSidebar(id), ic = sb && sb.icon && ICONS[sb.icon];
    return `<button class="ptab${tab === id ? " cur" : ""}${ic ? " picon" : ""}" data-ptab="${esc(id)}" title="${esc(label)}${sb ? " · " + esc(sb.plugin) + (sb.shortcut && PLUGIN_KEYS.has(shortcutCombo(sb.shortcut)) ? " · " + shortcutLabel(sb.shortcut) : "") : ""} (⌘⇧[ ⌘⇧] switch)">${ic ? I(sb.icon, 14) : esc(label)}${sb ? ptabBadge(id) : ""}</button>`;
  }).join("");
  const plt = plistTab(tab), ptab = tab.startsWith("p:") && !plt;  // (a list: the panel's own rows; a doc: #pside)
  $("#left .pfrow").hidden = ptab; plist.hidden = ptab; $("#pside").hidden = !ptab;
  if (ptab) { $("#pmach").hidden = true; $("#ppeek").hidden = true; return showPluginSidebar(tab); }
  $("#followbtn").hidden = $("#paddbtn").hidden = !!plt;
  $("#followbtn").innerHTML = I("crosshair", 14);
  $("#followbtn").classList.toggle("on", S.follow);
  $("#paddbtn").innerHTML = I("plus", 14);
  $("#paddbtn").title = tab === "files" ? "New file" : tab === "notes" ? "New note" : tab === "misc" ? "New schedule" : "New session (⌘N)";
  const ph = { sessions: "Filter groups", files: "Filter files", notes: "Filter notes", misc: "Filter schedules" }[tab] || `Filter ${plt.title}`;
  $("#pfbox").title = { sessions: "Type to filter sessions and tabs (fuzzy: name, folder, group)", files: "Type to filter files (fuzzy path)", notes: "Type to filter notes (fuzzy)", misc: "Type to filter schedules and meetings" }[tab]
    || `Type to filter ${plt.title} (fuzzy: what it says, then its details)`;
  $("#pfbox").classList.toggle("has", !!S.filter);
  const fp = filterPos();
  $("#pfbox").innerHTML = S.filter ? `<span class="fb">${esc(S.filter.slice(0, fp))}</span><span class="caret"></span><span class="fa">${esc(S.filter.slice(fp))}</span>`
    : `<span class="caret"></span><span class="ph">${esc(ph)}</span>`;
  renderMachineFilter();
  if (tab === "files" || tab === "notes") return renderFilesPanel();
  if (tab === "misc") return renderMiscPanel();
  if (plt) return renderPluginList(plt);
  PROWS = sessionRows();
  if (!PROWS.some(r => r.k === S.pcursor)) {
    const here = PROWS.find(r => r.kind === "session" && r.sid === S.current && r.gid === S.group);
    const first = S.filter ? PROWS.find(r => r.kind === "session") : null;
    S.pcursor = (first || here || PROWS[0] || {}).k || null;
  }
  if (!PROWS.length) {
    hideTip();
    plist.innerHTML = S.filter ? `<div class="pempty">Nothing matches “${esc(S.filter)}”.</div>` : `<div class="pempty"><h3>No session yet</h3>⌘N starts one.</div>`;
    return;
  }
  plist.innerHTML = PROWS.map(r => {
    const kc = r.k === S.pcursor ? " kc" : "";
    if (r.kind === "newgroup") return `<div class="prow newgrp${kc}" data-k="newgroup" title="New group (also: right-click a group, or the group chip)"><span class="ix">${I("plus", 11)}</span><span class="nm">New group…</span></div>`;
    const fold = r => r.fold ? `<span class="fold" title="${r.shut ? "Unfold" : "Fold"} (Space, → ←)">${I("chev", 10)}</span>` : `<span class="fold" style="visibility:hidden"></span>`;
    if (r.kind === "fleetgrp") return `<div class="prow grp fleetgrp${kc}${r.shut ? " shut" : ""}" data-k="${esc(r.k)}" title="Fleet helpers: quick asks (⌘K), meeting Prep, --fleet schedules. One opens beside, in the side pane"><span class="ix">${I("zap", 11)}</span><span class="nm">Fleet</span>${fold(r)}<span class="cnt">${esc(r.count)}</span></div>`;
    if (r.kind === "group") {
      const gc = r.gid !== FAV && (groupOf(r.gid) || {}).color;
      return `<div class="prow grp${r.depth ? " sub" : ""}${gc ? " hued" : ""}${kc}${r.gid === S.group ? " curgrp" : ""}${r.shut ? " shut" : ""}" data-k="${esc(r.k)}" style="--d:${r.depth || 0}${gc ? `;--gc:${gc}` : ""}" title="${esc(r.depth ? groupPath(r.gid) : r.key)}"><span class="ix${r.gid === FAV ? " star" : r.ix ? " num" : ""}">${r.gid === FAV ? I("star", 11) : r.depth ? "" : esc(r.ix)}</span>${r.gid === FAV ? "" : groupMark(r.gid, 14)}<span class="nm">${esc(r.name)}</span>${fold(r)}<span class="cnt">${esc(r.count)}</span></div>`;
    }
    if (r.kind === "ftab") {
      const isCur = S.file === r.path && r.gid === S.group, m = /^\/@([\w-]+)(\/.*)$/.exec(r.path);
      const where = isView(r.path) ? "" : m ? `${m[1]}: ${tilde(dirname(m[2]))}` : tilde(dirname(r.path));
      return `<div class="prow ftab${kc}${isCur ? " cur" : ""}" data-k="${esc(r.k)}" style="--d:${r.depth || 0}" data-tip="${esc(isView(r.path) ? ftabTitle(r.path) : tilde(r.path))}">` +
        `<span class="ic">${isView(r.path) ? I(viewInfo(r.path).icon, 12) : fileIcon(r.path)}</span><span class="nm">${esc(ftabTitle(r.path))}</span>` +
        `<span class="wh">${esc(where)}</span></div>`;
    }
    const s = S.sessions.get(r.sid);
    const isCur = r.sid === S.current && r.gid === S.group && !S.file;
    const pv = sessionPreview(s);
    const st = sessionStatus(s), unread = S.unread.has(s.id) && !isCur, model = s.model_seen || s.model;
    const stopped = s.state === "stopped";
    return `<div class="prow ss${kc}${isCur ? " cur" : ""}${stopped ? " stopped" : ""}${unread ? " unread" : ""}" data-k="${esc(r.k)}" style="--d:${r.depth || 0}" data-tip="${esc(s.name)}">` +
      `<span class="si ${st.cls}">${st.icon}</span>${harnessIcon(s.harness)}<span class="nm">${esc(sessionTitle(s))}</span>` +
      `${s.favorite && r.gid !== FAV ? `<span class="star">${I("star", 11)}</span>` : ""}${machineTag(s)}<span class="tm">${esc(lastMoved(s))}</span>${stopped ? `<button class="rsm" data-resume="${esc(s.id)}" title="Start its agent again">Resume</button>` : ""}</div>`;
  }).join("");
  revealCursor();
  renderPeek();
}
function renderPluginList(sb) {
  PROWS = pluginListRows(sb.plugin, sb.source, S.filter);
  if (!PROWS.some(r => r.k === S.pcursor)) S.pcursor = (PROWS.find(r => r.kind === "pitem") || PROWS[0] || {}).k || null;
  if (!PROWS.length) {
    hideTip();
    plist.innerHTML = `<div class="pempty">${S.filter ? `Nothing matches “${esc(S.filter)}”.` : pluginListItems(sb.plugin, sb.source) ? `Nothing from ${esc(sb.plugin)} yet.` : "Loading…"}</div>`;
    renderPeek();
    return;
  }
  plist.innerHTML = PROWS.map(r => pluginRowHtml(r, r.k === S.pcursor ? " kc" : "")).join("");
  revealCursor();
  renderPeek();
}
/* A session row's second line: what its last request ended with (the answer's start, as plain text), what it is
   doing now, or where it is when nothing was asked yet. */
/* A session row's marks (ui.md "Left-panel list"): which agent, how its last request went, when it last moved. */
function sessionStatus(s) {  // {cls, icon, title}: what the session is doing, or how its last request ended
  if (s.attention) return { cls: "attn", icon: "!", title: `Needs you in its terminal: ${s.attention} (⌘T)` };
  if (s.state === "working") return { cls: "run", icon: "", title: "Working" };
  if (s.state === "starting") return { cls: "run", icon: "", title: "Starting" };
  if (s.state === "not ready") return { cls: "err", icon: I("x", 10), title: s.error || "Its agent is not ready" };
  if (s.state === "stopped" || s.closed) return { cls: "stopped", icon: "", title: "Stopped (Resume, or a request, starts it again)" };
  if (s.agents > 0) return { cls: "run", icon: "", title: `${s.agents} subagent${s.agents === 1 ? "" : "s"} still working in the background (a request runs at once)` };
  const l = s.last || {};
  return ({ completed: { cls: "ok", icon: I("check", 12), title: "Its last request is done" },  // drawn marks: the same size in every engine
            error: { cls: "err", icon: I("x", 10), title: "Its last request failed" },
            paused: { cls: "warn", icon: I("pause", 11), title: "Its last request is paused (Continue goes on)" },
            cancelled: { cls: "quiet", icon: I("ban", 11), title: "Its last request was stopped" } })[l.state]
    || { cls: "idle", icon: "", title: "Ready: nothing asked yet" };
}
function lastMoved(s) {  // when it last did something: now while working, else its last request's end
  return s.state === "working" || s.agents > 0 ? "now" : ago((s.last && s.last.time) || s.created);
}
function sessionPreview(s) {
  const plain = t => oneLine(String(t || "").replace(/```[\s\S]*?(```|$)/g, " ").replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[#>*_`|~]+/g, " "));
  if (s.state === "working") { const c = (S.cards.get(s.id) || []).find(c => ACTIVE.has(c.state)); if (c) return { text: "Working: " + plain(c.text), full: oneLine(c.text) }; }
  if (s.last && s.last.text) { const t = plain(s.last.text); return { text: t, full: t }; }
  return { text: tilde(s.cwd), full: tilde(s.cwd), dim: true };
}
/* The machine filter above the sessions: only when there are other machines. */
function renderMachineFilter() {
  const el = $("#pmach");
  const ms = S.machines || [];
  el.hidden = !ms.length || S.left.tab !== "sessions";
  if (el.hidden) return;
  if (S.machine && S.machine !== "local" && !ms.some(m => m.name === S.machine)) { S.machine = ""; LS.set("machine", ""); }
  const chips = [["", "All"], ["local", "This Mac"], ...ms.map(m => [m.name, m.name])];
  el.innerHTML = chips.map(([v, label]) => {
    const m = ms.find(x => x.name === v);
    return `<button class="mchip${S.machine === v ? " on" : ""}${m && !m.ok ? " off" : ""}" data-mach="${esc(v)}" title="${v === "" ? "Sessions on every machine" : v === "local" ? "Sessions on this machine" : m && !m.ok ? `${esc(v)} is unreachable: ${esc(m.error)}` : `Sessions on ${esc(v)}`}">${esc(label)}</button>`;
  }).join("");
}
function cycleMachine(d) {  // All → This Mac → each machine, round
  const opts = ["", "local", ...(S.machines || []).map(m => m.name)];
  const i = Math.max(0, opts.indexOf(S.machine));
  S.machine = opts[(i + d + opts.length) % opts.length]; LS.set("machine", S.machine);
  S.pcursor = null;  // the cursor goes to the first row of what is shown now
  renderLeft();
}
$("#pmach").addEventListener("click", e => {
  const b = e.target.closest("[data-mach]"); if (!b) return;
  S.machine = b.dataset.mach; LS.set("machine", S.machine); renderLeft();
});
/* Where the caret is in the filter (it is drawn, not an <input>); cleared filter → at the start. */
function filterPos() { return S.filter ? Math.min(S.filterPos ?? S.filter.length, S.filter.length) : 0; }
/* ---------- the Files tab: several roots
   The folders of the sessions of the group in front (the current one first, then by their last answer), the other
   groups' folders under a fold, then the places: this Mac's home, each connected machine's, and folders added with
   "Go to a folder…". A session folder lists its whole tree at once (git: tracked + untracked, its status); a place
   lists a folder at a time (a home is too big for one listing). Which roots are open is kept; a root seen for the
   first time is open if it is the current one. Follow (the panel's eye) opens the current root at the open file. */
const FILES = { open: new Set(LS.get("filesOpen", [])), seen: new Set(LS.get("filesSeen", [])), other: LS.get("filesOther", false),
                places: LS.get("filesPlaces", []), dirs: new Map() };
function setRootOpen(root, on) {
  if (on === FILES.open.has(root)) return;
  on ? FILES.open.add(root) : FILES.open.delete(root);
  LS.set("filesOpen", [...FILES.open]);
}
const rootOpen = root => FILES.open.has(root);
function filesRoots() {
  const ctx = focusCtx().root, by = new Map();  // (the current root: where the keyboard is, a side tab's too)
  for (const s of S.sessions.values()) {
    if (s.fleet || !s.cwd || !machineOk(s)) continue;
    const t = (s.last && s.last.time) || s.created || 0, f = by.get(s.cwd) || { path: s.cwd, t: 0, remote: s.remote || "", here: false, n: 0 };
    f.t = Math.max(f.t, t); f.n++; if (S.group === FAV ? s.favorite : inView(s, S.group)) f.here = true; by.set(s.cwd, f);
  }
  // The current root: the session folder that holds the context (a file of another session's folder: that folder),
  // else the context itself (a file outside every session's folder: its repo or folder).
  const holder = ctx && [...by.keys()].filter(p => ctx === p || ctx.startsWith(p + "/")).sort((a, b) => b.length - a.length)[0];  // (the nearest)
  const cur = holder || ctx;
  if (cur && !by.has(cur)) by.set(cur, { path: cur, t: Infinity, remote: (/^\/@([^/]+)/.exec(cur) || [])[1] || "", here: true, n: 0 });
  if (holder) by.get(holder).here = true;
  const order = (a, b) => (b.path === cur) - (a.path === cur) || b.t - a.t;
  const all = [...by.values()];
  const here = all.filter(f => f.here).sort(order), other = all.filter(f => !f.here).sort(order);
  const places = [{ path: "~", name: "Home", sub: "This Mac", place: true },
    ...(S.machines || []).map(m => ({ path: `/@${m.name}/~`, name: "Home", sub: m.name, remote: m.name, off: !m.ok, place: true })),
    ...FILES.places.map(p => ({ path: p, name: basename(p.replace(/^\/@[^/]+/, "")) || p, sub: tilde(p), added: true, place: true }))];
  if (cur && !FILES.seen.has(cur)) { FILES.seen.add(cur); LS.set("filesSeen", [...FILES.seen].slice(-300)); setRootOpen(cur, true); }
  return { cur, here, other, places, all };
}
/* A place's folder, a level at a time (/api/dirlist; another machine's home through its server). */
async function loadDir(path, force = false) {
  const c = FILES.dirs.get(path);
  if (c && (c.loading || (!force && Date.now() - c.at < 5000))) return;
  FILES.dirs.set(path, { ...(c || {}), loading: true, at: Date.now() });
  try {
    const home = /^\/@([a-z0-9][a-z0-9_-]*)\/~$/i.exec(path);
    let d;
    if (home) { d = await api("GET", `/api/remote/${home[1]}/api/dirlist?path=~`); d = { ...d, path: `/@${home[1]}${d.path}` }; }
    else d = await api("GET", "/api/dirlist?path=" + encodeURIComponent(path));
    FILES.dirs.set(path, { at: Date.now(), real: d.path, entries: d.entries || [] });
  } catch (e) { FILES.dirs.set(path, { at: Date.now(), error: e.message, entries: [] }); }
  if (S.left.shown && S.left.tab === "files") renderLeft();
}
function filesTabRows() {
  const R = filesRoots(), rows = [];
  const rootOf = (f, open, depth = 0) => {
    const t = S.trees.get(f.path);
    return { kind: "root", root: f.path, k: "r:" + f.path, open, depth, cur: f.path === R.cur, remote: f.remote, off: f.off, place: f.place, added: f.added,
             name: f.name || basename(f.path.replace(/^\/@[^/]+/, "")) || f.path, sub: f.sub || tilde(dirname(f.path.replace(/^\/@[^/]+/, ""))),
             branch: !f.place && t && t.git ? t.branch || "git" : "" };
  };
  if (S.filter) {  // the open session folders, each a filtered tree (those with a match)
    for (const f of [...R.here, ...R.other]) {
      const t = S.trees.get(f.path);
      if (!rootOpen(f.path) || f.off || !t || t.error) continue;
      const sub = fileRows(f.path, false, true), open = !FILTERED.shut.has("root|" + f.path);
      if (sub.length) rows.push(rootOf(f, open), ...(open ? sub : []));
    }
    return rows;
  }
  const rootRow = (f, depth = 0) => {
    const open = rootOpen(f.path) && !f.off, t = S.trees.get(f.path);
    rows.push(rootOf(f, open, depth));
    if (!open) return;
    if (f.place) return dirRows(f.path, depth + 1);
    if (!t) { refreshTree(f.path); rows.push({ kind: "note", k: "n:" + f.path, depth: depth + 1, text: "Loading…" }); return; }
    if (t.error) { rows.push({ kind: "note", k: "n:" + f.path, depth: depth + 1, text: t.error }); return; }
    for (const r of fileRows(f.path, false, true)) rows.push({ ...r, depth: r.depth + depth });
    if (t.truncated) rows.push({ kind: "note", k: "n:" + f.path, depth: depth + 1, text: "The first 20 000 files" });
  };
  const dirRows = (path, depth) => {
    const c = FILES.dirs.get(path);
    if (!c || !c.entries) { loadDir(path); rows.push({ kind: "note", k: "n:" + path, depth, text: "Loading…" }); return; }  // (the first listing on its way)
    if (Date.now() - c.at > 5000) loadDir(path);
    if (c.error) { rows.push({ kind: "note", k: "n:" + path, depth, text: c.error }); return; }
    const base = c.real || path;
    for (const e of c.entries) {
      if (e.name.startsWith(".")) continue;  // (a home's dotfiles: Go to a folder reaches them)
      const p = base.replace(/\/$/, "") + "/" + e.name;
      if (e.dir) { const open = rootOpen(p); rows.push({ kind: "ldir", path: p, k: "l:" + p, depth, open, name: e.name }); if (open) dirRows(p, depth + 1); }
      else rows.push({ kind: "lfile", path: p, k: "lf:" + p, depth, name: e.name });
    }
    if (!c.entries.length) rows.push({ kind: "note", k: "n:" + path, depth, text: "Empty folder" });
  };
  const gname = S.group === FAV ? "Favorites" : (groupOf(S.group) || {}).name || "This group";
  rows.push({ kind: "fsec", k: "h:here", name: gname, n: R.here.length });
  R.here.forEach(f => rootRow(f));
  if (R.other.length) {
    rows.push({ kind: "fsec", k: "h:other", name: "Other groups", n: R.other.length, fold: true, open: FILES.other });
    if (FILES.other) R.other.forEach(f => rootRow(f));
  }
  rows.push({ kind: "fsec", k: "h:places", name: "Places" });
  R.places.forEach(f => rootRow(f));
  rows.push({ kind: "goto", k: "goto", depth: 0 });
  return rows;
}
/* Whether a session's new record changes what the left panel shows: the Sessions tab, yes; the Files tab, when its
   folders changed (a session's folder, a new one, their order); the other tabs, only its name (a schedule names it). */
function leftShowsSession(old, s) {
  if (S.left.tab === "sessions" || !old) return true;
  if (S.left.tab === "files") return !!S.filter || filesTabRows().map(r => r.k).join("\n") !== FILES.drawn;
  return old.name !== s.name;
}
function renderFilesRoots() {
  PROWS = filesTabRows();
  FILES.drawn = S.filter ? "" : PROWS.map(r => r.k).join("\n");
  cursorToBest();
  const w = FILES.want;
  if (w && Date.now() - w.t < 3000) { if (PROWS.some(r => r.k === w.k)) { S.pcursor = w.k; FILES.want = null; } else if (!PROWS.some(r => r.k === S.pcursor)) S.pcursor = w.k; }
  else FILES.want = null;
  if (!PROWS.some(r => r.k === S.pcursor)) {
    const R = filesRoots(), cur = R.cur && S.file && S.file.startsWith(R.cur + "/") ? "f:" + R.cur + "|" + relTo(R.cur, S.file) : null;
    S.pcursor = (PROWS.find(r => r.k === cur) || PROWS.find(r => r.kind === "root" && r.cur) || PROWS.find(r => r.kind !== "fsec") || {}).k || null;
  }
  if (S.filter && !PROWS.length) { hideTip(); plist.innerHTML = `<div class="pempty">Nothing matches “${esc(S.filter)}” in the open folders.</div>`; return; }
  const curFile = S.file || "";
  plist.innerHTML = PROWS.map(r => {
    const kc = r.k === S.pcursor ? " kc" : "", guides = '<span class="ig"></span>'.repeat(r.depth || 0);
    if (r.kind === "fsec") return `<div class="prow fsec${kc}${r.fold ? " fold" : ""}${r.fold && !r.open ? " shut" : ""}" data-k="${esc(r.k)}"><span class="nm">${esc(r.name)}</span>` +
      `${r.fold ? `<span class="fold">${I("chev", 10)}</span>` : ""}${r.n != null ? `<span class="cnt">${r.n}</span>` : ""}</div>`;
    if (r.kind === "goto") return `<div class="prow ft fgoto${kc}" data-k="goto"><span class="tw"></span><span class="ic">${I("plus", 12)}</span><span class="nm">Go to a folder…</span></div>`;
    if (r.kind === "note") return `<div class="prow ft fnote" data-k="${esc(r.k)}">${guides}<span class="tw"></span><span class="nm">${esc(r.text)}</span></div>`;
    if (r.kind === "root") {
      const t = r.place ? null : S.trees.get(r.root);
      return `<div class="prow ft froot${kc}${r.cur ? " curroot" : ""}${r.off ? " off" : ""}" data-k="${esc(r.k)}" data-tip="${esc(tilde(r.root))}">${guides}` +
        `<span class="tw${r.open ? " open" : ""}">${I("chev", 10)}</span><span class="ic">${r.place && !r.added ? I(r.remote ? "server" : "home", 13) : folderIcon(r.open)}</span>` +
        `<span class="nm">${esc(r.name)}</span><span class="fd">\u200e${esc(r.sub)}\u200e</span>` +  // (left-to-right marks: cut at the start, its order kept)
        (r.remote ? `<span class="mtag${machineUp(r.remote) ? "" : " off"}">${esc(r.remote)}</span>` : "") +
        (r.branch ? `<span class="fd br">${esc(r.branch)}</span>` : "") + (t && Object.keys(t.status || {}).length ? '<span class="dd2"></span>' : "") + `</div>`;
    }
    if (r.kind === "ldir") return `<div class="prow ft dir${kc}" data-k="${esc(r.k)}" data-tip="${esc(tilde(r.path))}">${guides}<span class="tw${r.open ? " open" : ""}">${I("chev", 10)}</span><span class="ic">${folderIcon(r.open)}</span><span class="nm">${esc(r.name)}</span></div>`;
    if (r.kind === "lfile") return `<div class="prow ft${kc}${r.path === curFile ? " cur" : ""}" data-k="${esc(r.k)}" data-tip="${esc(tilde(r.path))}">${guides}<span class="tw"></span><span class="ic">${fileIcon(r.name)}</span><span class="nm">${esc(r.name)}</span></div>`;
    const t = S.trees.get(r.root);
    if (r.kind === "dir") return `<div class="prow ft dir${kc}" data-k="${esc(r.k)}" data-tip="${esc(tilde(r.root + "/" + r.rel))}">${guides}<span class="tw${r.open ? " open" : ""}">${I("chev", 10)}</span><span class="ic">${folderIcon(r.open)}</span><span class="nm">${hitName(r, basename(r.rel))}</span>${t && t.dirty.has(r.rel) ? '<span class="dd2"></span>' : ""}</div>`;
    const st = t && t.status[r.rel], letter = st === "?" ? "U" : st, full = r.root + "/" + r.rel;
    const name = r.flat ? r.rel : basename(r.rel);
    return `<div class="prow ft${kc}${full === curFile ? " cur" : ""}${st ? ` st-${esc(letter)}` : ""}" data-k="${esc(r.k)}" data-tip="${esc(tilde(full))}">${guides}<span class="tw"></span><span class="ic">${fileIcon(r.rel)}</span>` +
      `<span class="nm">${hitName(r, name)}</span>${r.flat ? `<span class="fd">${esc(basename(r.root.replace(/^\/@[^/]+/, "")) || r.root)}</span>` : ""}${st ? `<span class="stl st-${esc(letter)}">${esc(letter)}</span>` : ""}</div>`;
  }).join("");
  if (typeof opsDecorate === "function") opsDecorate();  // (fileops.js: draggable rows, the cut ones dimmed)
  revealCursor();
}
function toggleRoot(r, open) {
  const want = open ?? !r.open;
  if (S.filter && r.kind === "root") { const fk = "root|" + r.root; if (want) FILTERED.shut.delete(fk); else FILTERED.shut.add(fk); renderLeft(); return; }  // (that filtered tree only)
  if (r.kind === "fsec") { if (!r.fold) return; FILES.other = want; LS.set("filesOther", want); renderLeft(); return; }
  const key = r.kind === "root" ? r.root : r.path;
  setRootOpen(key, want);
  if (want && r.kind === "root" && !r.place) refreshTree(r.root);
  renderLeft();
}
async function gotoFolder() {
  const v = await dialog({ title: "Go to a folder", ok: "Add to Places", fields: [{ key: "p", label: "Folder (~/…, /…, or /@machine/…)", value: "~/" }],
    validate: v => v.p.trim() ? null : "Which folder?" });
  if (!v) return;
  const p = v.p.trim().replace(/(.)\/+$/, "$1");
  try { await api("GET", "/api/dirlist?path=" + encodeURIComponent(p)); } catch (e) { toast(e.message); return; }
  if (!FILES.places.includes(p)) { FILES.places.push(p); LS.set("filesPlaces", FILES.places); }
  setRootOpen(p, true); S.pcursor = "r:" + p; renderLeft();
}
function renderFilesPanel() {
  if (S.left.tab === "files") return renderFilesRoots();
  const notesTab = S.left.tab === "notes";
  const root = panelRoot();
  if (!root) { PROWS = []; plist.innerHTML = notesTab ? `<div class="pempty">Loading the vault…</div>` : `<div class="pempty"><h3>No folder</h3>Open a session: its folder shows here.</div>`; return; }
  const t = S.trees.get(root);
  if (!t) { PROWS = []; plist.innerHTML = `<div class="pempty">Loading ${esc(tilde(root))}…</div>`; refreshTree(root); return; }
  if (t.error) { PROWS = []; plist.innerHTML = `<div class="pempty"><h3>Cannot list the folder</h3>${esc(t.error)}</div>`; return; }
  PROWS = fileRows(root, notesTab);
  cursorToBest();
  if (notesTab && !S.filter) PROWS.unshift({ kind: "week", k: "w", depth: 0 });  // ⌘⇧D, one ⏎ away
  if (!PROWS.some(r => r.k === S.pcursor)) S.pcursor = (S.file && PROWS.find(r => r.k === "f:" + relTo(root, S.file)) || PROWS[0] || {}).k || null;
  // The tree head: the folder as ~/… (11px, quiet), the branch or the note count at the right.
  const head = `<div class="thead" title="${esc(root)}"><span class="tp">${notesTab ? "Notes · " : ""}${esc(tilde(root))}</span><span class="tc">${notesTab ? `${(t.notes || []).length} notes`
    : `${t.git ? esc(t.branch || "git") : ""}${t.truncated ? " · first 20000" : ""}`}</span></div>`;
  if (!PROWS.length) { hideTip(); plist.innerHTML = head + `<div class="pempty">${S.filter ? `Nothing matches “${esc(S.filter)}”.` : notesTab ? "No notes yet: + makes one, ⌘⇧D opens this week's." : "Empty folder."}</div>`; return; }
  const curRel = S.file ? relTo(root, S.file) : null;
  plist.innerHTML = head + PROWS.map(r => {
    const kc = r.k === S.pcursor ? " kc" : "";
    // A tree row (ui.md §6): an indent guide per depth, the twisty cell, the icon cell, the name; git status letter
    // at the right, the name tinted the same.
    const guides = '<span class="ig"></span>'.repeat(r.depth);
    if (r.kind === "week") return `<div class="prow ft${kc}" data-k="w" title="${esc(journalName()[0].toUpperCase() + journalName().slice(1))} (⌘⇧D)"><span class="tw"></span><span class="ic">${I("calendar", 13)}</span><span class="nm">${esc(JOURNAL.label)}</span><span class="fd">⌘⇧D</span></div>`;
    if (r.kind === "dir") {
      return `<div class="prow ft dir${kc}" data-k="${esc(r.k)}" data-tip="${esc(tilde(root + "/" + r.rel))}">${guides}<span class="tw${r.open ? " open" : ""}">${I("chev", 10)}</span><span class="ic">${folderIcon(r.open)}</span><span class="nm">${hitName(r, basename(r.rel))}</span>${!notesTab && t.dirty.has(r.rel) ? '<span class="dd2"></span>' : ""}</div>`;
    }
    const st = notesTab ? null : t.status[r.rel];
    const letter = st === "?" ? "U" : st;
    let name = r.flat ? r.rel : basename(r.rel);
    if (notesTab) name = name.replace(/\.md$/i, "");
    return `<div class="prow ft${kc}${r.rel === curRel ? " cur" : ""}${st ? ` st-${esc(letter)}` : ""}" data-k="${esc(r.k)}" data-tip="${esc(tilde(root + "/" + r.rel))}">${guides}<span class="tw"></span><span class="ic">${fileIcon(r.rel)}</span><span class="nm">${hitName(r, name)}</span>${st ? `<span class="stl st-${esc(letter)}">${esc(letter)}</span>` : ""}</div>`;
  }).join("");
  revealCursor();
}
/* The cursor row brought into view (and its cut-off name's tip) when the cursor moved: a key, a click, Follow; not on
   every redraw. The tree redraws every few seconds and on an agent's steps, and the user's own scrolling was pulled
   back to the cursor each time. */
let REVEALED = "";
function revealCursor() {
  const el = plist.querySelector(".prow.kc"), k = el ? S.left.tab + "|" + el.dataset.k : "";
  if (k === REVEALED) return;
  REVEALED = k;
  if (el) el.scrollIntoView({ block: "nearest" });
  tipPanelRow();
}
/* Follow: the left panel shows what the keyboard is on: the main tab, or the side tab when it has the keyboard (its
   file, its session). */
function followPanel() {
  if (EMBED || !S.follow || panelFocused()) return;
  const f = focusCtx();
  if (S.left.tab === "sessions") {
    const sid = f.sid || S.current, s = S.sessions.get(sid);
    const k = f.side && s ? [`s:${S.group}:${sid}`, `s:${s.group}:${sid}`].find(x => PROWS.some(r => r.k === x)) : null;
    S.pcursor = k || `s:${S.group}:${S.current}`;
  }
  else if (treeTab() && f.path && (S.left.tab === "files" || isNote(f.path))) revealFile(f.path);
  else if (S.left.tab === "files" && filesRoots().cur) { const c = filesRoots().cur; setRootOpen(c, true); refreshTree(c); S.pcursor = "r:" + c; }  // (a session: its folder)
  renderLeft();
}
const followSoon = () => { clearTimeout(followSoon.t); followSoon.t = setTimeout(followPanel, 30); };
const prow = () => PROWS.find(r => r.k === S.pcursor);
function movePanel(how) {
  if (!PROWS.length) return;
  let i = PROWS.findIndex(r => r.k === S.pcursor);
  if (how === "home") i = 0; else if (how === "end") i = PROWS.length - 1;
  else i = Math.max(0, Math.min(PROWS.length - 1, i + how));
  S.pcursor = PROWS[i].k;
  renderLeft();
}
let recenterAt = 0, recenterStep = 0;
function recenter(box, el) {  // ⌃L: middle, then top, then bottom (a press after 1.5 s starts again at the middle)
  if (!el) return;
  const now = Date.now();
  recenterStep = now - recenterAt < 1500 ? (recenterStep + 1) % 3 : 0;
  recenterAt = now;
  const top = el.offsetTop - box.offsetTop;
  box.scrollTop = [top - box.clientHeight / 2 + el.offsetHeight / 2, top, top - box.clientHeight + el.offsetHeight][recenterStep];
}
function openRow(r, { focus = true, pinned = false } = {}) {
  if (!r) return;
  if (S.left.tab === "misc") return openMiscRow(r);
  if (plistTab()) {  // a plugin's list: its item opens (usually a native thread tab)
    if (r.kind !== "pitem") return;
    const sb = plistTab();
    fromList = true;
    try { openPluginItem(sb.plugin, sb.source, r.it); } finally { fromList = false; }
    return;
  }
  if (S.left.tab !== "sessions") {
    if (r.kind === "week") { weekNote(); return; }
    if (r.kind === "root" || r.kind === "fsec" || r.kind === "ldir") { toggleRoot(r); return; }
    if (r.kind === "goto") { gotoFolder(); return; }
    if (r.kind === "note") return;
    if (r.kind === "dir") { toggleDir(r.rel, undefined, rowRoot(r)); return; }
    S.filter = "";
    // A click: a look at it beside, the keyboard and the main pane left where they were (interaction.md: the user
    // keeps their focus); the next click takes its place unless it was kept. ⏎ and a double-click: the main pane.
    if (byMouse && !pinned && tabKey() && S.file !== rowPath(r)) { openSide({ kind: "file", path: rowPath(r), preview: true }, { focus: false }); return; }
    fromList = true;
    try { openFile(rowPath(r), { pinned, focus }); } finally { fromList = false; }
    return;
  }
  if (r.kind === "group") { openHome(r.gid, { focus: false }); S.pcursor = r.k; renderLeft(); return; }  // (a group itself: its home)
  if (r.kind === "newgroup") { newGroup(); return; }
  if (r.kind === "fleetgrp") return;  // not a group of tabs
  if (byMouse && S.filter) { FILTER_LATER.pending = true; FILTER_LATER.value = S.filter; }  // a click: the list stays as it is under the pointer (cleared once it leaves)
  else S.filter = "";
  fromList = true;
  try {
    if (r.kind === "ftab") {  // a group's file tab: that group, that tab
      if (r.gid !== S.group) switchGroup(r.gid, { focus: false, quiet: true });
      openFile(r.path, { pinned: true, focus });
    } else switchTo(r.sid, { group: r.gid, focus });
  } finally { fromList = false; }
}
/* A file tab closed from the Groups panel (its group, on every screen): the open one through closeFile (unsaved edits
   asked about); another group's straight from the layout. */
function closeGroupTab(r) {
  if (r.gid === S.group) return closeFile(r.path);
  S.ftabs[r.gid] = (S.ftabs[r.gid] || []).filter(t => t.path !== r.path);
  closeAllSides("f:" + r.path);
  S.closedFiles = [{ path: r.path, t: Date.now() / 1000, gid: r.gid }, ...S.closedFiles.filter(f => f.path !== r.path)].slice(0, 30);  // (⌘⇧T)
  saveUi(); renderTabs(); renderLeft();
}
/* ⌃D on a row of the Groups panel: close or delete what it is, always asked first. A group: delete it (its sessions
   move); a session: close it (its conversation is kept); a file tab: close it (in its group, on every screen). */
async function deleteRow(r) {
  if (!r) return;
  if (r.kind === "group" && r.gid !== FAV) {
    if (S.groups.length < 2) { toast("The last group cannot be deleted"); return; }
    return deleteGroup(r.gid);
  }
  if (r.kind === "session") return closeSession(r.sid);  // (asks; from Favorites too: it closes, ⌘W only unstars)
  if (r.kind === "ftab") {
    const ok = await confirmDialog({ title: `Close “${ftabTitle(r.path)}”?`, ok: "Close",
      body: `The tab closes in ${groupPath(r.gid)}, on every screen. ${/^(orbit|plugin):/.test(r.path) ? "" : "The file stays on disk. "}⌘⇧T reopens it.` });
    if (ok) closeGroupTab(r);
    return;
  }
  toast(r.kind === "group" ? "Favorites is not a group to delete: ⌘⌃P unstars a session" : r.kind === "fleetgrp" ? "The Fleet is Orbit's own: close its helpers one by one" : "Nothing to close here");
}
/* ⌘R on a row: rename it. A group, a session, a file tab's file (a view, the Calendar, a plugin's tab has no name to
   change). */
function renameRow(r) {
  if (!r) return;
  if (r.kind === "group" && r.gid !== FAV) return renameGroup(r.gid);
  if (r.kind === "session") return renameSession(r.sid);
  if (r.kind === "ftab" && !/^(orbit|plugin):/.test(r.path)) return renamePath(r.path);
  toast(r.kind === "ftab" ? "A view has no name to change" : "This one cannot be renamed");
}
function toggleDir(rel, open, root = panelRoot()) {
  if (S.filter) return toggleFilteredDir(rel, open, root);
  const set = expandedSet(root);
  const want = open ?? !set.has(rel);
  if (want) set.add(rel); else set.delete(rel);
  setExpanded(root, set);
  renderLeft();
}
function rowMenu(r, where) {
  if (!r) return;
  if (plistTab()) { if (r.kind === "pitem") pluginItemMenu(plistTab().plugin, plistTab().source, r.it, where); return; }
  if (S.left.tab === "misc") {
    if (r.kind === "sched") return scheduleMenu(r.it, where);
    if (r.kind === "event") return eventMenu(r.ev, where);
    return;
  }
  if (S.left.tab !== "sessions") {
    if (r.kind === "week" || r.kind === "note") return;
    if (r.kind === "goto") return gotoFolder();
    if (r.kind === "fsec") return r.fold ? toggleRoot(r) : undefined;
    if (typeof FSEL !== "undefined" && FSEL.keys.size > 1 && FSEL.keys.has(r.k)) return selectionMenu(where);  // (fileops.js)
    if (r.kind === "root") return folderMenu(r.root, where, r);
    if (r.kind === "ldir") return folderMenu(r.path, where);
    return r.kind === "dir" ? folderMenu(rowPath(r), where, null, rowRoot(r)) : fileMenu(rowPath(r), where);
  }
  if (r.kind === "session") return tabMenu(r.sid, where);
  if (r.kind === "ftab") return menu([
    { label: "Open", run: () => openRow(r) },
    r.gid !== FAV ? { label: "Move to", sub: () => moveTabItems(r.path, r.gid) } : null,
    { label: "Close the tab", key: "⌘W", run: () => closeGroupTab(r) },
  ].filter(Boolean), where);
  if (r.kind === "newgroup") return newGroup();
  if (r.gid === FAV) return menu([{ label: "Show Favorites", key: "⌘1", run: () => switchGroup(FAV) }], where);
  groupMenu(r.gid, where);
}
function folderMenu(dir, where, rootRow = null, treeRoot = null) {
  const isRoot = dir === panelRoot() || !!rootRow;
  const inVault = S.vault && (dir === S.vault || dir.startsWith(S.vault + "/"));
  const lazy = (rootRow && rootRow.place) || (!rootRow && !treeRoot && S.left.tab === "files");  // (a place's folder: listed a level at a time)
  const real = rootRow && rootRow.place ? ((FILES.dirs.get(dir) || {}).real || dir) : dir;
  const top = treeRoot || (rootRow && rootRow.root) || panelRoot(), here = !/^\/@/.test(real);  // (another machine's folder: no Terminal here)
  menu([
    inVault ? { label: "New note…", run: () => newNote(relTo(S.vault, dir) === dir ? "" : relTo(S.vault, dir)) }
            : { label: "New file…", run: () => newFileIn(real) },
    { label: "New folder…", run: () => newFolderIn(real) },
    !isRoot || !(rootRow && rootRow.place) ? { label: "Copy", key: "⌥W", run: () => filesClip(false, rootRow, real) } : null,
    isRoot ? null : { label: "Cut", key: "⌃W", run: () => filesClip(true, null, real) },
    { label: "Paste here", key: "⌃Y", run: () => filesPaste(real) },
    EMBED ? null : { label: "New session here…", run: () => newSessionIn(real) },
    here ? { label: "Open in Terminal", run: () => api("POST", "/api/open-external", { path: real, how: "terminal" }).then(r => toast(`Opened in ${r.app}`)).catch(e => toast(e.message)) } : null,
    { sep: true },
    { label: "Refresh", run: () => lazy ? loadDir(dir, true) : refreshTree(top, true) },
    rootRow && rootRow.added ? { label: "Remove from Places", run: () => { FILES.places = FILES.places.filter(p => p !== dir); LS.set("filesPlaces", FILES.places); renderLeft(); } } : null,
    { sep: true },
    { label: "Copy path", run: () => copyText(real) },
    top && real.startsWith(top + "/") ? { label: "Copy relative path", run: () => copyText(relTo(top, real)) } : null,
    { label: "Open in Finder", run: () => openExternal(real, "finder") },
    isRoot ? null : { label: "Rename…", run: () => renamePath(dir) },
    isRoot ? null : { label: "Delete…", danger: true, run: () => trashPath(dir) },
  ].filter(Boolean), where);
}
function groupMenu(gid, where) {
  const g = groupOf(gid); if (!g) return;
  const sib = groupKids(g.parent || ""), i = sib.indexOf(g), deep = groupDepth(gid), h = groupHeight(gid);
  const bad = new Set([gid, ...groupDescendants(gid)]);
  menu([
    { label: "New session here", key: "⌘N", run: () => newSession(gid) },
    { label: "New subgroup…", disabled: deep >= GROUP_DEPTH, run: () => newGroup(gid) },
    { label: "Rename group", key: "⌘R", run: () => renameGroup(gid) },
    { label: "Its look: icon, colour, cover…", run: () => groupLookDialog(gid) },
    { label: "Open its home", run: () => openHome(gid) },
    { sep: true },
    { label: "Move up", key: "⌘⌃H", disabled: i <= 0, run: () => api("POST", `/api/groups/${gid}/move`, { delta: -1 }).catch(e => toast(e.message)) },
    { label: "Move down", key: "⌘⌃L", disabled: i >= sib.length - 1, run: () => api("POST", `/api/groups/${gid}/move`, { delta: 1 }).catch(e => toast(e.message)) },
    { label: "Into the group above", key: "Tab", disabled: i <= 0 || groupDepth(sib[i - 1].id) + h > GROUP_DEPTH, run: () => indentGroup(gid) },
    { label: "Out a level", key: "⇧Tab", disabled: !g.parent, run: () => outdentGroup(gid) },
    { label: "Move into", sub: () => [
        { label: "The top level", disabled: !g.parent, run: () => nestGroup(gid, "") },
        { sep: true },
        ...groupMenuItems(p => nestGroup(gid, p), { disabled: x => bad.has(x.id) || x.id === g.parent || groupDepth(x.id) + h > GROUP_DEPTH }),
      ] },
    { sep: true },
    { label: "New group…", run: () => newGroup() },
    { label: "Delete group…", danger: true, disabled: S.groups.length < 2, run: () => deleteGroup(gid) },
  ], where);
}
/* Tab: into the sibling above (as its last subgroup); ⇧Tab: out a level, right after the group it was in. */
function indentGroup(gid) {
  const g = groupOf(gid); if (!g) return;
  const sib = groupKids(g.parent || ""), i = sib.indexOf(g);
  if (i <= 0) { toast("No group above it to go into"); return; }
  nestGroup(gid, sib[i - 1].id);
}
function outdentGroup(gid) {
  const g = groupOf(gid); if (!g) return;
  if (!g.parent) { toast("It is at the top already"); return; }
  nestGroup(gid, (groupOf(g.parent) || {}).parent || "", g.parent);
}
async function nestGroup(gid, parent, after = "") {
  try {
    await api("POST", `/api/groups/${gid}/nest`, { parent, after });
    if (parent) { FOLDED.delete(parent); LS.set("folded", [...FOLDED]); }  // (where it went: in sight)
    S.pcursor = "g:" + gid; renderLeft();
  } catch (e) { toast(e.message); }
}
/* A group's name: given, and no other group's (case and every space ignored: "Invest Assistant" is "InvestAssistant";
   Favorites and the Fleet count). The server checks the same. */
const groupKey = n => String(n || "").replace(/\s+/g, "").toLowerCase();
function groupNameError(name, gid = "", parent = "") {
  const k = groupKey(name);
  if (!k) return "Give the group a name.";
  if (/[/›]/.test(name)) return "A group's name has no / or › (they separate a subgroup's path).";
  if (!parent && (k === "favorites" || k === "fleet")) return `“${name.trim()}” is Orbit's own: there is a group named that already.`;
  const same = groupKids(parent).find(g => g.id !== gid && groupKey(g.name) === k);
  if (same) return `There is a group named “${same.name}” there already.`;
  return null;
}
async function newGroup(parent = "") {
  parent = typeof parent === "string" ? parent : "";  // (a click handler's event is not a parent)
  const v = await dialog({ title: parent ? `New group in ${groupPath(parent)}` : "New group", fields: [{ key: "name", label: "Name" }], ok: "Create",
    validate: v => groupNameError(v.name, "", parent) });
  if (!v) return;
  try {
    const g = await api("POST", "/api/groups", { name: v.name, parent });
    if (!S.groups.some(x => x.id === g.id)) S.groups.push(g);
    if (parent) { FOLDED.delete(parent); LS.set("folded", [...FOLDED]); }
    switchGroup(g.id, { quiet: true });
  } catch (e) { toast(e.message); }
}
async function renameGroup(gid) {
  const g = S.groups.find(g => g.id === gid); if (!g) return;
  const v = await dialog({ title: "Rename group", fields: [{ key: "name", label: "Name", value: g.name }], validate: v => groupNameError(v.name, gid, g.parent || "") });
  if (v) try { await api("POST", `/api/groups/${gid}/rename`, v); } catch (e) { toast(e.message); }
}
async function deleteGroup(gid) {
  const g = S.groups.find(g => g.id === gid); if (!g) return;
  const to = g.parent ? groupOf(g.parent) : topGroups().find(x => x.id !== gid) || S.groups.find(x => x.id !== gid);
  const n = [...S.sessions.values()].filter(s => s.group === gid).length, kids = groupKids(gid).length;
  const parts = [n ? `Its ${n} session${n === 1 ? "" : "s"} move to “${groupPath(to.id)}”.` : "",
                 kids ? `Its ${kids} subgroup${kids === 1 ? "" : "s"} move up a level.` : ""].filter(Boolean);
  const ok = await confirmDialog({ title: `Delete group “${groupPath(gid)}”?`, ok: "Delete", danger: true,
    body: parts.length ? parts.join(" ") + " No conversation is lost." : "The group is empty." });
  if (ok) try { await api("POST", `/api/groups/${gid}/delete`); } catch (e) { toast(e.message); }
}
/* Keys while the left panel has the keyboard (interaction.md §6). */
function panelKey(e, combo) {
  if (combo === "MS:BracketLeft" || combo === "MS:BracketRight") { panelTab(combo.endsWith("Left") ? -1 : 1); return true; }
  // ⌥⇧[ ⌥⇧]: the sub-tabs here, the machine filter of the Groups tab (elsewhere they switch side tabs)
  if ((combo === "AS:BracketLeft" || combo === "AS:BracketRight") && S.left.tab === "sessions" && (S.machines || []).length) {
    cycleMachine(combo.endsWith("Left") ? -1 : 1); return true;
  }
  // The Files tab, its filter empty: ⌥W copy · ⌃W cut · ⌃Y paste (⌘C ⌘X ⌘V), ⇧↑ ⇧↓ select, ⌘⌫ to the Trash (fileops.js)
  if (S.left.tab === "files" && !S.filter && typeof filesOpsKey === "function" && filesOpsKey(combo)) return true;
  if (combo === "Escape") { S.filter = ""; renderLeft(); focusMain(); return true; }
  const tab = S.left.tab;
  const r = prow();
  const mv = { ArrowDown: 1, ArrowUp: -1, "C:KeyN": 1, "C:KeyP": -1, PageDown: 2, PageUp: -2, "C:KeyV": 2, "A:KeyV": -2,
               Home: "home", End: "end", "AS:Comma": "home", "AS:Period": "end" }[combo];
  if (mv !== undefined) {
    if (typeof mv === "number" && Math.abs(mv) === 2) {
      const page = Math.max(1, Math.floor(plist.clientHeight / (parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--row")) || 30)) - 1);
      let i = PROWS.findIndex(x => x.k === S.pcursor) + Math.sign(mv) * page;
      i = Math.max(0, Math.min(PROWS.length - 1, i));
      if (PROWS[i]) { S.pcursor = PROWS[i].k; renderLeft(); }
    } else movePanel(mv);
    return true;
  }
  if (combo === "C:KeyL") { recenter(plist, plist.querySelector(".prow.kc")); return true; }
  if (combo === "Enter") { openRow(r); return true; }
  if (combo === "M:Enter") { openRow(r, { pinned: true }); return true; }
  if (combo === "M:KeyE") { rowMenu(r, { anchor: plist.querySelector(".prow.kc") }); return true; }
  if (tab === "files" && r && (r.kind === "root" || r.kind === "ldir" || r.kind === "fsec" || r.kind === "lfile" || r.kind === "note")) {  // (fold, unfold, up a level)
    const upTo = () => {  // the row of the folder it is in: the one above at a smaller depth
      const i = PROWS.indexOf(r); for (let j = i - 1; j >= 0; j--) if ((PROWS[j].depth || 0) < (r.depth || 0) && PROWS[j].kind !== "fsec") return PROWS[j];
    };
    if (combo === "ArrowLeft") {
      if ((r.kind === "root" || r.kind === "ldir" || (r.kind === "fsec" && r.fold)) && r.open) toggleRoot(r, false);
      else { const up = upTo(); if (up) { S.pcursor = up.k; renderLeft(); } }
      return true;
    }
    if (combo === "ArrowRight") { if ((r.kind === "root" || r.kind === "ldir" || (r.kind === "fsec" && r.fold)) && !r.open && !r.off) toggleRoot(r, true); else movePanel(1); return true; }
  }
  if ((tab === "files" || tab === "notes") && r && r.rel) {
    const K = r.root ? "r:" + r.root : null, P = r.root ? r.root + "|" : "";  // (the Files tab: keys say the root)
    if (combo === "ArrowLeft") {
      if (r.kind === "dir" && r.open) toggleDir(r.rel, false, rowRoot(r));
      else if (r.rel.includes("/") && !r.flat) { S.pcursor = "d:" + P + r.rel.replace(/\/[^/]*$/, ""); renderLeft(); }
      else if (K) { S.pcursor = K; renderLeft(); }
      return true;
    }
    if (combo === "ArrowRight") { if (r.kind === "dir" && !r.open) toggleDir(r.rel, true, rowRoot(r)); else movePanel(1); return true; }
  }
  if (tab === "sessions" && r && !S.filter && (combo === "ArrowLeft" || combo === "ArrowRight")) {  // fold, unfold, as in a tree
    const grp = r.kind === "group" || r.kind === "fleetgrp";
    if (combo === "ArrowLeft") {
      const up = grp && (groupOf(r.gid) || {}).parent;  // (a subgroup, folded or empty: up to its parent)
      if (grp && !r.shut && r.fold) setFolded(r.gid, true);
      else if (up) { S.pcursor = "g:" + up; renderLeft(); }
      else if (!grp && r.gid) { S.pcursor = "g:" + r.gid; renderLeft(); }
    }
    else if (grp && r.shut) setFolded(r.gid, false); else movePanel(1);
    return true;
  }
  if (tab === "sessions" && combo === "Space" && r && !S.filter) {  // Space: the group folds / unfolds (on a row in it: its group folds, the cursor on it)
    const grp = r.kind === "group" || r.kind === "fleetgrp";
    if (grp) { if (r.fold) setFolded(r.gid, !r.shut); }
    else if (r.gid && r.kind !== "newgroup") setFolded(r.gid, true);  // (it puts the cursor on the group)
    return true;
  }
  if (tab === "sessions") {
    if (combo === "M:KeyR" && r && r.kind !== "newgroup") { renameRow(r); return true; }
    if ((combo === "Tab" || combo === "S:Tab") && r && r.kind === "group" && r.gid !== FAV) {  // into the group above it / out a level
      if (combo === "Tab") indentGroup(r.gid); else outdentGroup(r.gid);
      return true;
    }
    // ⌃D: close / delete the row (asked first). In the filter it still deletes a character, except at its end (where
    // it has none to delete) or with no filter.
    if (combo === "C:KeyD" && r && r.kind !== "newgroup" && (!S.filter || filterPos() >= S.filter.length)) { deleteRow(r); return true; }
    if ((combo === "MC:KeyH" || combo === "MC:KeyL") && r) {
      const d = combo === "MC:KeyH" ? -1 : 1;
      if (r.kind === "group" && r.gid !== FAV) api("POST", `/api/groups/${r.gid}/move`, { delta: d }).catch(err => toast(err.message));
      else if (r.kind === "session") moveSession(r.sid, d);
      return true;
    }
    if (combo === "M:KeyW" && r && r.kind === "session") { closeOrUnfavorite(r.sid, r.gid); return true; }  // (a Favorites row: off the favorites)
    if (combo === "M:KeyW" && r && r.kind === "ftab") { closeGroupTab(r); return true; }
  }
  if (LINE_KEYS.has(combo) || combo === "Backspace") {  // the filter is a line: the editors' Emacs keys move and edit in it
    const r = lineEdit(S.filter, filterPos(), combo);
    if (r) { const changed = r.value !== S.filter; S.filter = r.value; S.filterPos = r.pos; if (changed) S.pcursor = null; renderLeft(); }
    return true;
  }
  if (!e.metaKey && !e.ctrlKey && !e.altKey && e.key.length === 1 && (e.key !== " " || S.filter)) {
    const p = filterPos();
    S.filter = S.filter.slice(0, p) + e.key + S.filter.slice(p); S.filterPos = p + 1; S.pcursor = null; renderLeft(); return true;
  }
  return false;
}
$("#ptabs").addEventListener("mousedown", e => {
  const b = e.target.closest("[data-ptab]"); if (!b) return;
  e.preventDefault(); setPanelTab(b.dataset.ptab); focusPanel();
});
$("#followbtn").addEventListener("click", () => { S.follow = !S.follow; saveUi(); followPanel(); renderLeft(); });
$("#paddbtn").addEventListener("click", () => S.left.tab === "files" && ctxRoot() ? newFileIn(ctxRoot()) : S.left.tab === "notes" ? newNote() : S.left.tab === "misc" ? scheduleDialog()
  : menu([{ label: "New session", key: "⌘N", run: () => newSession() }, { label: "New group…", run: () => newGroup() }, { label: "Resume another session…", run: attachDialog },
                 { sep: true }, { label: "Other machines…", run: remotesDialog }, { label: "Add a machine…", run: addRemote }], { anchor: $("#paddbtn") }));
$("#pfbox").addEventListener("mousedown", e => { e.preventDefault(); plist.focus(); });
plist.addEventListener("mousedown", e => {
  const el = e.target.closest(".prow[data-k]");
  if (!el || e.button !== 0) return;
  if (e.detail >= 2 && !e.metaKey && !e.shiftKey && rowDoubleClick(PROWS.find(x => x.k === el.dataset.k))) { e.preventDefault(); return; }
  if (S.left.tab === "files" && !S.filter && typeof filesMouseDown === "function" && filesMouseDown(e, el)) return;  // (a row that can be dragged: fileops.js)
  e.preventDefault();
  S.pcursor = el.dataset.k;
  const r = prow();
  if (!r) return;
  if (e.target.closest(".fold") && r.fold && r.gid) { plist.focus(); setFolded(r.gid, !r.shut); return; }
  const rs = e.target.closest("[data-resume]"); if (rs) { sessionAction(rs.dataset.resume, "resume"); return; }  // the chevron folds; the rest of the row opens the group
  if (r.kind === "pgrp") { plist.focus(); renderLeft(); return; }
  // A click opens the row and the keyboard stays in the panel (↑↓, ⏎, ⌥W… go on here); a double-click (or ⏎) goes
  // into what it opened; Esc goes back to the main pane.
  byMouse = true;
  try { plist.focus(); openRow(r, { focus: false }); if (document.activeElement !== plist) plist.focus({ preventScroll: true }); }
  finally { byMouse = false; }
});
/* A double-click: into what the click opened (the keyboard leaves the panel); a file pinned too. Told by the press's
   click count (e.detail), not by dblclick: the first click redraws the rows, and two clicks on two nodes make none. */
function rowDoubleClick(r) {
  if (!r) return false;
  if (S.left.tab === "sessions") { if (r.kind !== "session" && r.kind !== "ftab") return false; openRow(r); focusMain(); return true; }
  if (r.kind !== "file" && r.kind !== "lfile") return false;
  pinFile(rowPath(r)); openRow(r, { pinned: true }); dropSidePreview(rowPath(r)); focusMain();
  return true;
}
plist.addEventListener("contextmenu", e => {
  e.preventDefault();
  const el = e.target.closest(".prow[data-k]");
  const at = { at: { x: e.clientX, y: e.clientY } };
  if (el) { S.pcursor = el.dataset.k; renderLeft(); rowMenu(prow(), at); }
  else if (S.left.tab.startsWith("p:")) return;
  else if (S.left.tab === "misc") menu([{ label: "New schedule…", run: () => scheduleDialog() }, { label: "Settings…", key: "⌘,", run: settingsDialog }], at);
  else if (S.left.tab === "files") menu([{ label: "Go to a folder…", run: gotoFolder }, ctxRoot() ? { label: "Refresh", run: () => refreshTree(ctxRoot(), true) } : null].filter(Boolean), at);
  else if (S.left.tab !== "sessions" && panelRoot()) folderMenu(panelRoot(), at);  // empty space = the folder's menu
  else if (S.groups.length) groupMenu(S.group === FAV ? S.groups[0].id : S.group, at);
});
plist.addEventListener("focus", () => renderLeft());
plist.addEventListener("blur", () => setTimeout(() => { if (!panelFocused()) renderLeft(); }, 0));
setInterval(() => {
  if (document.hidden || !S.left.shown || !treeTab()) return;
  if (S.left.tab !== "files") return refreshTree(panelRoot());
  const R = filesRoots();  // (the open session folders, the current one first: a few at most)
  [R.cur, ...R.all.map(f => f.path).filter(p => p !== R.cur && rootOpen(p))].filter(Boolean).slice(0, 6).forEach(p => refreshTree(p));
}, 5000);

/* Resize: drag a split, or ⌃⌥H / ⌃⌥L. Drag math divides by the page zoom (rectScale). */
$("#lsplit").addEventListener("mousedown", e => {
  e.preventDefault();
  const k = rectScale(), x0 = $("#left").getBoundingClientRect().left;
  const mv = ev => { S.left.w = clampLeft((ev.clientX - x0) / k); document.documentElement.style.setProperty("--leftw", S.left.w + "px"); };
  const up = () => { document.removeEventListener("mousemove", mv); document.removeEventListener("mouseup", up); saveUi(); };
  document.addEventListener("mousemove", mv); document.addEventListener("mouseup", up);
});
$("#ssplit").addEventListener("mousedown", e => {
  e.preventDefault();
  const frame = $("#sideframes"); frame.style.pointerEvents = "none";  // the iframe would swallow the drag
  const r = $("#main").getBoundingClientRect();
  const mv = ev => { S.sideW = Math.max(0.05, Math.min(0.95, (r.right - ev.clientX) / r.width)); applySideWidth(); };
  const up = () => { frame.style.pointerEvents = ""; document.removeEventListener("mousemove", mv); document.removeEventListener("mouseup", up); saveUi(); };
  document.addEventListener("mousemove", mv); document.addEventListener("mouseup", up);
});
function nudgeSplit(d) {  // ⌃⌥H (d < 0) / ⌃⌥L: the focused panel's edge; from the main panel the near edge first
  const moveLeft = dd => {
    if (!S.left.shown) return false;
    const w = clampLeft(S.left.w + dd); if (w === clampLeft(S.left.w)) return false;
    S.left.w = w; document.documentElement.style.setProperty("--leftw", w + "px"); return true;
  };
  const moveSide = dd => {
    if (!S.side) return false;
    const w = Math.max(0.05, Math.min(0.95, S.sideW - dd / Math.max(200, $("#main").clientWidth)));
    if (Math.abs(w - S.sideW) < 1e-4) return false;
    S.sideW = w; applySideWidth(); return true;
  };
  const ok = sideFocused() ? moveSide(d) : panelFocused() ? moveLeft(d)
    : d < 0 ? (moveLeft(d) || moveSide(d)) : (moveSide(d) || moveLeft(d));  // the neighbour gives room, else the other edge
  if (ok) saveUi();
}
function resetPanels() { S.left.w = 240; S.sideW = 0.45; applyLeft(); applySideWidth(); saveUi(); toast("Panel sizes reset"); }
addEventListener("resize", () => { if (S.left.shown) document.documentElement.style.setProperty("--leftw", clampLeft(S.left.w) + "px"); });
plist.addEventListener("blur", () => { if (tipPanel) hideTip(); });

/* The details strip at the panel's foot (U15): for the session the keyboard is on, or the one under the mouse: how it
   stands and what its last answer began with; its folder, agent and model. (Tooltips are only for cut-off names.) */
let PEEK_HOVER = null;
function renderPeek() {
  const el = $("#ppeek"); if (!el) return;
  const k = PEEK_HOVER || ((S.left.tab === "sessions" || plistTab()) && panelFocused() ? S.pcursor : null);
  const pi = plistTab() && k && PROWS.find(x => x.k === k && x.kind === "pitem");
  if (pi) {  // a plugin's item: what it says, its details
    el.hidden = false;
    el.innerHTML = `<div class="p1"><span class="t">${esc(pi.it.label)}</span></div>` + (pi.it.detail || pi.it.tag ? `<div class="p2">${esc([pi.it.detail, pi.it.tag].filter(Boolean).join(" · "))}</div>` : "");
    return;
  }
  const r = k && PROWS.find(x => x.k === k && x.kind === "session");
  const s = r && S.sessions.get(r.sid);
  el.hidden = !s || S.left.tab !== "sessions";
  if (!s || el.hidden) return;
  const st = sessionStatus(s), pv = sessionPreview(s), model = s.model_seen || s.model;
  el.innerHTML = `<div class="p1">${stateMark(s)}<span class="t">${esc(st.title)}${pv.dim ? "" : ": " + esc(pv.full)}</span></div>` +
    `<div class="p2">${esc(tilde(s.cwd))} · ${esc(harnessLabel(s.harness))}${model ? " · " + esc(model) : ""}</div>`;
}
plist.addEventListener("mouseover", e => { const row = e.target.closest(".prow.ss"); const k = row ? row.dataset.k : null; if (k !== PEEK_HOVER) { PEEK_HOVER = k; renderPeek(); } });
plist.addEventListener("mouseleave", () => { PEEK_HOVER = null; renderPeek(); });
/* A session clicked in a filtered list: the rows do not reshuffle under the pointer on its way out. The filter is
   cleared once the pointer has been away a moment, the current row kept where it was on screen. */
let byMouse = false;
const FILTER_LATER = { pending: false, value: "", tm: 0 };
plist.addEventListener("mouseenter", () => clearTimeout(FILTER_LATER.tm));
plist.addEventListener("mouseleave", () => {
  if (!FILTER_LATER.pending) return;
  clearTimeout(FILTER_LATER.tm);
  FILTER_LATER.tm = setTimeout(() => {
    if (!FILTER_LATER.pending || S.filter !== FILTER_LATER.value || panelFocused()) { FILTER_LATER.pending = false; return; }  // (typed again since)
    FILTER_LATER.pending = false;
    const cur = () => plist.querySelector(".prow.cur");
    const a = cur(), top = a ? a.getBoundingClientRect().top : null;
    const seen = new Map([...plist.querySelectorAll(".prow[data-k]")].map(el => [el.dataset.k, el.getBoundingClientRect().top]));
    S.filter = ""; S.pcursor = null; renderLeft();
    const b = cur(), k = rectScale();
    if (b && top !== null) plist.scrollTop += (b.getBoundingClientRect().top - top) / k;
    for (const el of plist.querySelectorAll(".prow[data-k]")) {  // the rows slide to their places (FLIP), new ones fade in
      const was = seen.get(el.dataset.k), now = el.getBoundingClientRect().top;
      el.style.transition = "none";
      if (was === undefined) el.style.opacity = "0"; else if (Math.abs(was - now) > .5) el.style.transform = `translateY(${(was - now) / k}px)`;
      el.offsetWidth;
      el.style.transition = "transform .22s ease, opacity .22s ease"; el.style.transform = ""; el.style.opacity = "";
      setTimeout(() => { el.style.transition = ""; }, 260);
    }
  }, 900);
});

/* The Sessions list by mouse (its neighbours slide aside as it goes): drag a session to reorder it, or among another
   group's rows (or just under its head) to move it there; drag a group's head to move the group with its sessions.
   (The keys do the same: ⌘⌃H ⌘⌃L.) */
const prowOf = el => el && PROWS.find(x => x.k === el.dataset.k);
const rowDepth = el => +(el.style.getPropertyValue("--d") || 0);
/* A group's head with everything under it: its rows and its subgroups', up to the next group at its level or above. */
const groupBlock = head => {
  const d = rowDepth(head), out = [head];
  let n = head.nextElementSibling;
  while (n && !n.matches(".prow.newgrp") && !(n.matches(".prow.grp") && rowDepth(n) <= d)) { out.push(n); n = n.nextElementSibling; }
  return out;
};
const parentOf = gid => (groupOf(gid) || {}).parent || "";
dragSort({
  box: plist, item: ".prow.ss, .prow.ftab, .prow.grp", axis: "y",
  keyOf: el => el.dataset.k, byKey: k => [...plist.querySelectorAll(".prow[data-k]")].find(x => x.dataset.k === k),
  canDrag: el => { const r = prowOf(el); return S.left.tab === "sessions" && !S.filter && !!r && ((r.kind === "session" && r.gid !== "fleet") || (r.kind === "ftab" && r.gid !== FAV) || (r.kind === "group" && r.gid !== FAV)); },
  unit: el => prowOf(el).kind === "group" ? groupBlock(el) : [el],
  peers: el => {
    const src = prowOf(el), rows = [...plist.querySelectorAll(".prow[data-k]")].filter(x => x !== el);
    if (src.kind === "group") return rows.filter(x => { const r = prowOf(x); return r && r.kind === "group" && r.gid !== FAV && parentOf(r.gid) === parentOf(src.gid); })  // (among its siblings: Tab, ⇧Tab and Move into nest)
      .map(groupBlock);
    if (src.kind === "ftab")  // (a file or view tab: among the rows of the groups, it lands in the group it is put under)
      return rows.filter(x => { const r = prowOf(x); return r && r.gid !== FAV && r.gid !== "fleet" && ["session", "ftab", "group"].includes(r.kind); }).map(x => [x]);
    const fav = src.gid === FAV;
    return rows.filter(x => { const r = prowOf(x); return r && ((r.kind === "session" && r.gid !== "fleet" && (r.gid === FAV) === fav) || (!fav && r.kind === "group" && r.gid !== FAV)); }).map(x => [x]);
  },
  redraw: () => renderLeft(),
  drop: (key, prev, next) => {
    const src = PROWS.find(x => x.k === key), pr = prowOf(prev), nr = prowOf(next);
    if (!src) return renderLeft();
    if (src.kind === "group") return nr ? dropOn(key, next, false) : pr ? dropOn(key, prev, true) : renderLeft();
    if (src.kind === "ftab") {  // the group of the row above it (a group's head: that group), else of the row below
      const gid = pr ? pr.gid : nr && nr.gid;
      if (gid && gid !== src.gid) moveFileTab(src.path, src.gid, gid); else renderLeft();
      return;
    }
    if (pr && pr.kind === "session") return dropOn(key, prev, true);
    if (pr && pr.kind === "group") return nr && nr.kind === "session" && nr.gid === pr.gid ? dropOn(key, next, false) : dropOn(key, prev, true);
    if (nr && nr.kind === "session") return dropOn(key, next, false);
    renderLeft();  // (above every group: nowhere to go)
  },
});
async function dropOn(key, el, after) {
  const src = PROWS.find(x => x.k === key), r = prowOf(el); if (!src || !r) return renderLeft();
  if (src.kind === "group") {
    const sib = groupKids(parentOf(src.gid)), i = sib.findIndex(g => g.id === src.gid);
    let j = sib.findIndex(g => g.id === r.gid) + (after ? 1 : 0); if (j > i) j--;
    if (j !== i) api("POST", `/api/groups/${src.gid}/move`, { delta: j - i }).catch(e => toast(e.message)); else renderLeft();
    return;
  }
  const sid = src.sid, gid = r.gid;
  if (gid !== src.gid && gid !== FAV) await moveToGroup(sid, gid);
  if (r.kind === "group") { renderLeft(); return; }  // (under a group's head with nothing after: into it, at its end)
  const list = [...S.sessions.values()].filter(s => s.id !== sid && (s.id === r.sid || inView(s, gid))).map(s => s.id);
  let i = list.indexOf(r.sid); if (after) i++;
  let before = list[i] || null;
  if (!before) { const all = [...S.sessions.keys()].filter(x => x !== sid), last = list[list.length - 1]; before = all[all.indexOf(last) + 1] || null; }
  const ord = S.tabOrder[gid];  // the group's tab row, if arranged, follows
  if (ord) { const k = "s:" + sid, at = ord.indexOf(k); if (at >= 0) ord.splice(at, 1); const b = before ? ord.indexOf("s:" + before) : -1; ord.splice(b >= 0 ? b : ord.length, 0, k); saveUi(); }
  try { await api("POST", `/api/sessions/${sid}/move`, { before }); } catch (e) { toast(e.message); renderLeft(); }
}
