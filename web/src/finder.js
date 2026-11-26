"use strict";
/* ===================================================================== finders: ⌘O, ⌘R, ⌘⇧F, attach, browse */
/* Escape text, then bold each search term in it. */
function boldTerms(text, terms) {
  let h = esc(text);
  for (const t of terms || []) {
    if (!t) continue;
    const e = esc(t).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    h = h.replace(new RegExp(`(${e})(?![^<]*>)`, "gi"), "<b>$1</b>");
  }
  return h;
}
/* A tab: `items()` (fetched once when it opens; each key filters them here) or `search(q)` (the server matches the
   debounced query). `live(q, signal)` (a plugin source's search, 1.3): for 2+ characters, 150 ms after the last key,
   one request in flight (a newer query aborts the older, so a late answer is never shown); its items come below the
   tab's own, under "Search in <name>", without those already shown (by `dedup`). `watch(ev)`: an event that changes
   the tab (live.js), which then redraws with the query and the selection kept. */
function finder({ title, tabs, start = 0, opener, lead }) {  // lead(q): items above every tab's (a web address: open it)
  const prev = document.activeElement;
  const back = document.createElement("div"); back.className = "backdrop";
  const box = document.createElement("div"); box.className = "dialog finder";
  box.innerHTML = `${tabs.length > 1 ? `<div class="ftabs"></div>` : `<div class="title">${esc(title)}</div>`}
    <input class="fq" spellcheck="false" placeholder="Type to filter"><div class="flist"></div>`;
  document.body.append(back, box);
  const q = box.querySelector(".fq"), list = box.querySelector(".flist");
  if (typeof start === "string") start = Math.max(0, tabs.findIndex(x => x.name === start));  // (a tab by its name)
  let t = Math.max(0, Math.min(start, tabs.length - 1)), all = [], view = [], i = 0, seq = 0;
  const live = { q: "", items: [], state: "", err: "", ctl: null, timer: 0, seq: 0 };
  const drawTabs = () => {
    const ft = box.querySelector(".ftabs");
    if (ft) ft.innerHTML = tabs.map((x, k) => `<button class="ptab${k === t ? " cur" : ""}" data-t="${k}">${esc(x.name)}</button>`).join("");
  };
  const row = (it, k) => `<div class="frow${k === i ? " sel" : ""}" data-i="${k}">${it.mark ? stateMark(it.mark) : it.dot ? `<span class="dot ${it.dot}"></span>` : ""}${it.ficon ? `<span class="gi">${fileIcon(it.ficon)}</span>` : it.icon ? `<span style="color:var(--ink-3)">${I(it.icon, 13)}</span>` : it.iconHtml ? `<span class="gi">${it.iconHtml}</span>` : ""}<span class="fl">${esc(it.label)}</span>${it.tag ? `<span class="tag">${esc(it.tag)}</span>` : ""}<span class="fd">${boldTerms(it.detail || "", it.hl)}</span><span class="fk">${esc(it.key || "")}</span></div>`;
  const draw = () => {
    const s = q.value.trim();
    const server = !!tabs[t].search;  // the server already matched these
    const shown = s && !server ? all.map(it => { const sc = Math.max(idScore(s, it.ids), fuzzy(s, it.label), fuzzy(s, it.match || "") - 1); return { it, sc: sc >= 0 ? sc + (it.boost || 0) : sc }; }).filter(x => x.sc >= 0)
      .sort((a, b) => b.sc - a.sc).slice(0, 400).map(x => x.it) : all.slice(0, 400);
    if (lead && s) shown.unshift(...lead(s));
    const withLive = !!tabs[t].live && s.length >= 2;
    let extra = [];
    if (withLive && live.q === s) {
      const seen = new Set(shown.map(it => it.dedup).filter(Boolean));
      extra = live.items.filter(it => !it.dedup || !seen.has(it.dedup));
    }
    view = [...shown, ...extra];
    i = Math.max(0, Math.min(i, view.length - 1));
    const empty = `<div class="fempty">${esc(failed ? `Could not ${server ? "search" : "list them"}: ${failed}` : s ? (server ? (searching ? "Searching…" : "Nothing found.") : "Nothing matches.") : (tabs[t].empty || "Nothing here yet."))}</div>`;
    if (withLive) {
      const st = live.state === "error" ? `not available: ${live.err}` : live.state === "done" && !extra.length ? (live.items.length ? "nothing more" : "nothing found") : live.state === "done" ? "" : "searching…";
      list.innerHTML = shown.map(row).join("") + `<div class="fdiv"><span>Search in ${esc(tabs[t].name)}</span>${st ? `<span class="fds">${esc(st)}</span>` : ""}</div>`
        + extra.map((it, k) => row(it, shown.length + k)).join("");
    } else list.innerHTML = view.length ? view.map(row).join("") : empty;
    const sel = list.querySelector(".frow.sel"); if (sel) sel.scrollIntoView({ block: "nearest" });
    tipFinderRow(list);
  };
  let searching = false, stimer = 0, failed = "";
  const load = async (keep = false) => {  // keep: the same tab again (a new query, a change): no blank list meanwhile
    const my = ++seq;
    if (!keep) { drawTabs(); all = []; i = 0; }
    searching = !!tabs[t].search && !!q.value.trim(); failed = "";
    if (!keep) draw();
    let got;
    let err = "";
    try { got = tabs[t].search ? await tabs[t].search(q.value.trim()) : await tabs[t].items(); } catch (e) { got = []; err = e.message || "failed"; }
    if (my === seq) { all = got; failed = err; searching = false; draw(); }  // (a failure says so in the list: "Nothing found" would be wrong)
  };
  const stopLive = () => { clearTimeout(live.timer); if (live.ctl) live.ctl.abort(); Object.assign(live, { q: "", items: [], state: "", err: "", ctl: null }); live.seq++; };
  const kickLive = () => {
    const tab = tabs[t], s = q.value.trim();
    if (!tab.live || s === live.q) return;
    stopLive();
    if (s.length < 2) return;
    Object.assign(live, { q: s, state: "searching" });
    const my = live.seq;
    live.timer = setTimeout(async () => {
      const ctl = live.ctl = new AbortController();
      try {
        const items = await tab.live(s, ctl.signal);
        if (my !== live.seq) return;  // a newer query (or another tab) since: never shown
        Object.assign(live, { items, state: "done", ctl: null });
      } catch (e) {
        if (my !== live.seq || ctl.signal.aborted) return;
        Object.assign(live, { state: "error", err: e.message, ctl: null });
      }
      draw();
    }, 150);
  };
  const switchTab = k => { t = k; stopLive(); load(); kickLive(); };
  const finish = (it, alt) => { closeModal(); if (it) (alt && it.alt ? it.alt : it.run)(); };
  q.addEventListener("input", () => {
    i = 0;
    if (tabs[t].search) { clearTimeout(stimer); stimer = setTimeout(() => load(true), 160); } else draw();
    kickLive();
  });
  list.addEventListener("mousedown", e => e.preventDefault());
  list.addEventListener("click", e => { const r = e.target.closest(".frow"); if (r) finish(view[+r.dataset.i]); });
  list.addEventListener("contextmenu", e => {
    const r = e.target.closest(".frow"), it = r && view[+r.dataset.i];
    if (!it || !it.menu) return;
    e.preventDefault(); const k = rectScale(); closeModal(); it.menu({ at: { x: e.clientX / k, y: e.clientY / k } });
  });
  box.addEventListener("mousedown", e => { const b = e.target.closest("[data-t]"); if (b) { e.preventDefault(); switchTab(+b.dataset.t); q.focus(); } });
  back.addEventListener("mousedown", e => { e.preventDefault(); closeModal(); });
  const trap = e => { if (!box.contains(e.target)) { e.stopPropagation(); q.focus(); } };
  document.addEventListener("focusin", trap, true);
  openModal({
    kind: "finder", opener,
    changed: ev => { if (tabs[t].watch && tabs[t].watch(ev)) load(true); },  // (live.js: a plugin source, the conversations)
    restore: () => { if (prev && prev.focus && document.contains(prev)) prev.focus({ preventScroll: true }); },
    teardown: () => { stopLive(); document.removeEventListener("focusin", trap, true); back.remove(); box.remove(); clearTimeout(tipFinderTimer); hideTip(); },
    close: () => closeModal(),
    onKey(e, combo) {
      const page = Math.max(1, Math.floor(list.clientHeight / 30) - 1);
      const mv = { ArrowDown: 1, ArrowUp: -1, "C:KeyN": 1, "C:KeyP": -1, PageDown: page, PageUp: -page, "C:KeyV": page, "A:KeyV": -page }[combo];
      if (mv) { i = Math.max(0, Math.min(view.length - 1, i + mv)); draw(); return true; }
      if (combo === "AS:Comma" || combo === "Home") { i = 0; draw(); return true; }
      if (combo === "AS:Period" || combo === "End") { i = view.length - 1; draw(); return true; }
      if (combo === "M:KeyE" && view[i] && view[i].menu) {  // the row's context menu (the finder closes)
        const it = view[i], row = list.querySelector(".frow.sel"), rc = row && row.getBoundingClientRect(), k = rectScale();
        closeModal(); it.menu(rc ? { at: { x: rc.left / k + 24, y: rc.bottom / k } } : undefined); return true;
      }
      if (combo === "C:KeyL") { recenter(list, list.querySelector(".frow.sel")); return true; }
      if (combo === "Enter") { finish(view[i]); return true; }
      if (combo === "M:Enter") { finish(view[i], true); return true; }
      if (combo === "Escape" || combo === opener) { closeModal(); return true; }
      if ((combo === "MS:BracketLeft" || combo === "MS:BracketRight") && tabs.length > 1) {
        switchTab((t + (combo.endsWith("Left") ? -1 : 1) + tabs.length) % tabs.length); return true;
      }
      return false;
    },
  });
  q.focus();
  load();
}
/* A session found by its id (as Copy link, a resume command, `orbit-agent sessions` or a log shows it): 4 or more
   characters of it, anywhere in it (a uuid's start ranks first), ahead of every match by name. */
function idScore(q, ids) {
  q = q.toLowerCase().replace(/#.*$/, "").replace(/^.*[\/~]/, "");  // (orbit://session/<id>#3, NAME~ID: the id part)
  if (!ids || q.length < 4 || !/^[0-9a-z_-]+$/.test(q)) return -1;
  let best = -1;
  for (const id of ids) { const k = String(id || "").toLowerCase().indexOf(q); if (k >= 0) best = Math.max(best, k === 0 ? 100000 : 50000); }
  return best;
}
function sessionItem(s, { detail, side } = {}) {
  return {
    ids: [s.id, s.id.replace(/^.*~/, "")],
    label: s.name, mark: s, iconHtml: harnessIcon(s.harness, 13), tag: s.closed ? "closed" : groupPath(s.group),
    detail: detail ?? tilde(s.cwd), match: `${tilde(s.cwd)} ${groupPath(s.group)}`,
    key: s.closed ? ago(s.closed_at) : "",
    run: side ? async () => { if (s.closed) await api("POST", `/api/sessions/${s.id}/reopen`); openSide({ kind: "session", sid: s.id }); }
              : () => s.closed ? reopen(s.id) : switchTo(s.id),
    menu: s.closed ? null : where => tabMenu(s.id, where),
  };
}
/* A group, as ⌘O's Sessions and ⌘R list it: chosen, the group in front, on the tab it was left on (its last session,
   file or home: switchGroup). Groups are switched often; reached here by name like a session. */
function groupItem(gid, { key = "" } = {}) {
  const n = tabIds(gid).length, last = String(S.lastTab[gid] || "");
  const at = last === "f:" + HOME ? "its home" : last.startsWith("f:") ? basename(last.slice(2)) : (S.sessions.get(last.replace(/^s:/, "")) || {}).name || "";
  return { label: groupPath(gid), iconHtml: groupMark(gid, 13), tag: "group", key, match: `${groupPath(gid)} group`, boost: 1000,  // (matched: above every session and file)
           detail: [at ? "→ " + at : "", `${n} session${n === 1 ? "" : "s"}`].filter(Boolean).join(" · "), run: () => switchGroup(gid) };
}
/* The Files & Folders tab: every folder and file of every open session's folder (this machine's and the connected ones'; a folder inside
   another counted once), the current one's first. Each says its folder ("project/dir"), and the folder's name is
   matched too ("orbit readme"). A folder listed before is used at once (and listed again meanwhile); the others are
   listed together. */
async function fileItems(side) {
  const cur = typeof filesRoots === "function" ? filesRoots().cur : ctxRoot();
  const seen = [...new Set([cur, ...[...S.sessions.values()].filter(s => !s.fleet && s.cwd && machineOk(s)).map(s => s.cwd)].filter(Boolean))];
  const roots = seen.filter(r => !seen.some(o => o !== r && r.startsWith(o + "/")));  // (the outermost of nested ones)
  await Promise.all(roots.map(r => S.trees.has(r) ? (refreshTree(r), null) : refreshTree(r)));
  const out = [];
  for (const root of roots) {
    const t = S.trees.get(root); if (!t || t.error) continue;
    const name = basename(root.replace(/^\/@[^/]+/, "")) || root, machine = (/^\/@([^/]+)/.exec(root) || [])[1] || "";
    const here = root === cur || (cur && cur.startsWith(root + "/"));
    // its folders (the session folder itself first): chosen, the Files panel on it
    out.push({ label: name, detail: tilde(root.replace(/^\/@[^/]+/, "")), match: name, iconHtml: folderIcon(false), tag: machine || "folder",
               boost: here ? 0.5 : 0, run: () => revealFolder(root, "") });
    for (const d of t.index ? t.index.keys() : []) {
      if (!d) continue;
      out.push({ label: basename(d), detail: name + (d.includes("/") ? "/" + dirname(d) : ""), match: name + "/" + d, iconHtml: folderIcon(false),
                 tag: machine || "folder", boost: here ? 0.5 : 0, run: () => revealFolder(root, d) });
    }
    for (const f of t.files || []) {
      const st = here && t.status[f];
      out.push({ label: basename(f), detail: name + (f.includes("/") ? "/" + dirname(f) : ""), match: name + "/" + f, ficon: f,
        tag: st ? (st === "?" ? "U" : st) : machine, boost: here ? 0.5 : 0,
        run: () => openFile(root + "/" + f, { side }), alt: () => openFile(root + "/" + f, { pinned: true, side }),
        menu: where => fileMenu(root + "/" + f, where) });
    }
  }
  return out;
}
/* The All Files tab: file names in every open session's folder and in the home folders (this machine's, and each
   machine this server reaches: their paths /@NAME/…), matched by the server (GET /api/findfiles). */
async function allFileItems(text, side = false) {
  if (!text.trim()) return [];
  const d = await api("GET", "/api/findfiles?q=" + encodeURIComponent(text));
  const terms = text.toLowerCase().split(/\s+/).filter(Boolean);
  const items = d.files.map(f => {
    const local = f.path.replace(/^\/@[^/]+/, "");
    return { label: basename(f.path), detail: tilde(dirname(local)), hl: terms, ficon: f.path, tag: f.machine || (f.where === "home" ? "home" : ""),
             key: f.time ? ago(f.time) : "", run: () => openFile(f.path, { side }), alt: () => openFile(f.path, { pinned: true, side }),
             menu: where => fileMenu(f.path, where) };
  });
  if (d.unreachable && d.unreachable.length) items.push({ label: `Not searched: ${d.unreachable.join(", ")} (unreachable)`, detail: "", run: () => {} });
  return items;
}
/* The Notes tab: every note of the vault by its title (and folder), filtered as you type; for 2+ characters, the
   notes whose text has it too ("Search in Notes": the server's search, with the line it is on). */
async function noteItems(side) {
  if (!S.vault) return [];
  await refreshTree(S.vault);
  const t = S.trees.get(S.vault);
  return (t && t.files || []).filter(f => /\.md$/i.test(f)).map(f => {
    const path = S.vault + "/" + f;
    return { label: noteTitle(path), detail: f.includes("/") ? dirname(f) : "", match: f, ficon: path, dedup: path,
             run: () => openFile(path, { side }), alt: () => openFile(path, { pinned: true, side }), menu: where => fileMenu(path, where) };
  });
}
async function noteSearch(text, signal, side) {
  const r = await fetch("/api/search?q=" + encodeURIComponent(text), { signal });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  const terms = text.toLowerCase().split(/\s+/).filter(Boolean);
  return (d.notes || []).map(n => ({ label: n.title, ficon: n.path, tag: "text", detail: n.snippet, hl: terms, key: ago(n.time), dedup: n.path,
    run: () => side ? openFile(n.path, { side: true }) : openNoteAt(n.path, n.line_text), menu: where => fileMenu(n.path, where) }));
}
function openFinder(start, { side = false, opener } = {}) {
  opener = opener || (side ? "MC:KeyO" : "M:KeyO");
  if (modal && modal.kind === "finder" && modal.opener === opener) { closeModal(); return; }
  if (start == null) start = S.file && isNote(S.file) ? "Notes" : S.file ? "Files & Folders" : "Sessions";  // starts on Files & Folders in a file tab, on Notes in a note
  finder({ opener, start, lead: urlLead, tabs: [
    { name: "Sessions", empty: "No sessions. ⌘N starts one.", items: async () => {
      const all = (await api("GET", "/api/sessions?closed=1")).sessions;
      const open = all.filter(s => !s.closed);
      const closed = all.filter(s => s.closed && s.named).sort((a, b) => (b.closed_at || 0) - (a.closed_at || 0));
      const groups = side ? [] : S.groups.filter(g => g.id !== S.group).map(g => groupItem(g.id));
      return [...groups, ...open.map(s => sessionItem(s, { side })), ...closed.map(s => sessionItem(s, { side }))];  // (groups first: switched often)
    } },
    { name: "Files & Folders", empty: "No session folders: open a session (All Files also looks in the homes).", items: () => fileItems(side) },
    { name: "All Files", empty: "Type to search file names in every session's folder and in the home folder, on this Mac and on the machines it reaches.",
      search: t => allFileItems(t, side) },
    { name: "Notes", empty: S.vault ? "No notes yet: ⌘⇧D opens this week's." : "No vault: Settings → Notes.", items: () => noteItems(side), live: (t, signal) => noteSearch(t, signal, side) },
    { name: "History", empty: "Type to search every session's requests and answers, and the notes.", search: t => historyItems(t, side) },
    OTHER_TAB,
    ...(PUI.palette || []).map(src => paletteTab(src, side)),
  ] });
}
/* A plugin's item source (1.3: [[ui.palette]]), a tab after Orbit's: its items are fetched when the tab opens and
   filtered here (nothing runs per key); with `search`, the plugin's running service is asked too (finder: live). */
function paletteTab(src, side = false) {
  const qs = `plugin=${encodeURIComponent(src.plugin)}&source=${encodeURIComponent(src.id)}`;
  return {
    name: src.title, empty: `Nothing from ${src.plugin} yet.`,
    items: async () => {
      const x = (await api("GET", `/api/palette?${qs}`)).sources[0];
      return x ? x.items.map(it => paletteItem(src, it, side)) : [];
    },
    live: src.search ? async (text, signal) => {
      const r = await fetch(`/api/palette/search?${qs}&q=${encodeURIComponent(text)}`, { signal });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
      return (d.items || []).map(it => paletteItem(src, it, side));
    } : null,
    watch: ev => ev.t === "plugin-items" && ev.plugin === src.plugin && ev.source === src.id,
  };
}
function paletteItem(src, it, side) {
  const icon = [it.icon, src.icon].find(x => x && ICONS[x]) || "";
  return { label: it.label, detail: it.detail || "", tag: it.tag || "", icon, key: it.time ? ago(it.time) : "",
           match: [it.match, it.detail, it.tag].filter(Boolean).join(" "), ids: [it.key], dedup: it.key,
           run: () => openPaletteItem(src, it, side) };
}
/* Choosing one: `tab` opens the plugin's tab of its kind and key, `action` runs its [cli] with {"source", "item"} on
   stdin, `url` opens the item's url. */
function openPaletteItem(src, it, side = false) {
  if (it.open && (it.open.url || it.open.kind) && !openPaletteItem.inner) {  // (2.0) the item's own `open`
    openPaletteItem.inner = true;
    try { openPluginItem(src.plugin, src.id, it, { side }); } finally { openPaletteItem.inner = false; }
    return;
  }
  if (src.open === "url") { if (it.url) openUrl(it.url); else toast(`${it.label}: no url`); return; }
  if (src.open === "action") {
    if (!PUI.local) { toast(`${src.title}: on ${src.plugin}'s own machine only`); return; }
    runPluginAction({ plugin: src.plugin, title: src.title, args: src.args || [] }, { source: src.id, item: it }); return;
  }
  const kind = it.kind || src.kind || "", k = pluginKind(src.plugin, kind);
  if (side && k) { openSide({ kind: "pview", path: pluginTabPath(src.plugin, kind, it.key), title: it.label }); return; }
  openPluginTab(src.plugin, kind, it.key, { title: it.label });
}
/* Other sessions: everything that can be resumed and is not in the Sessions tab, newest first. Orbit's own closed
   unnamed sessions (reopened), and Claude Code, Codex or a plugin agent's conversations that are no Orbit session
   (started in a terminal, an IDE, anywhere; or an Orbit session's once it was deleted) on every machine: one becomes an
   Orbit session there that resumes it, its history as cards. One a process still runs is marked: both would write to
   it. The server keeps an index of them (it never waits for a plugin agent's list): a typed query is matched there,
   against all of them, with the same matcher; `external-sessions` (a list came in) redraws the tab. */
const OTHER_TAB = { name: "Other sessions", empty: "Nothing else to resume: no closed session, no Claude Code or Codex conversation outside Orbit.",
  watch: ev => ev.t === "external-sessions", search: async text => {
  const [all, ext] = await Promise.all([api("GET", "/api/sessions?closed=1"),
                                        api("GET", "/api/external-sessions?limit=200" + (text ? "&q=" + encodeURIComponent(text) : ""))]);
  const score = it => Math.max(idScore(text, it.ids), fuzzy(text, it.label), fuzzy(text, it.match || "") - 1);
  const closed = all.sessions.filter(s => s.closed && !s.named)  // named closed ones are in the Sessions tab
    .map(s => ({ t: s.closed_at || 0, item: { ...sessionItem(s), tag: ["closed", s.remote || ""].filter(Boolean).join(" · ") } }))
    .map(x => ({ ...x, sc: text ? score(x.item) : 0 })).filter(x => x.sc >= 0);
  const other = ext.sessions.map(x => {  // its name there (/rename in Claude Code, a Codex thread name), else its title
    const first = oneLine(x.first || ""), label = x.name || x.title || first;
    return { t: x.time, sc: x.score || 0, item: {
      label: label.slice(0, 100), icon: "terminal",
      detail: [label !== first ? first.slice(0, 80) : "", tilde(x.cwd), harnessLabel(x.harness)].filter(Boolean).join(" · "),
      tag: [x.machine || "", x.live ? "running elsewhere" : ""].filter(Boolean).join(" · "), key: ago(x.time),
      match: `${x.name || ""} ${x.title || ""} ${tilde(x.cwd)} ${x.machine || ""} ${harnessLabel(x.harness)} ${first}`, ids: [x.id], run: () => attachTo(x) } };
  });
  return [...closed, ...other].sort((a, b) => b.sc - a.sc || b.t - a.t).map(x => x.item);
} };
function attachDialog() { finder({ title: "Resume another session", tabs: [OTHER_TAB] }); }
async function attachTo(x) {
  if (x.live && !(await confirmDialog({ title: "Resume a conversation that is still running?",
    body: `A process still runs it (pid ${x.live.pid}${x.live.status ? ", " + x.live.status : ""}). If both go on, both write to it: quit it there first, or resume anyway.`, ok: "Resume anyway" }))) return;
  try {
    const s = await api("POST", "/api/attach-session", { harness: x.harness, id: x.id, cwd: x.cwd, machine: x.machine || "", name: x.name || "",
                                                         group: S.group !== FAV ? S.group : "" });
    S.sessions.set(s.id, s); S.cards.delete(s.id);
    switchTo(s.id);
    toast(`Resumed: ${s.name} (${harnessLabel(s.harness)}${x.machine ? " on " + x.machine : ""}), the next request continues it`);
  } catch (e) { toast(e.message); }
}
function openRecent() {
  if (modal && modal.kind === "finder" && modal.opener === "M:KeyR") { closeModal(); return; }
  finder({ title: "Recent", opener: "M:KeyR", tabs: [{ name: "Recent", empty: "Nothing seen yet.", items: async () => {
    const all = new Map((await api("GET", "/api/sessions?closed=1")).sessions.map(s => [s.id, s]));
    const here = tabKey(), seenG = new Set([S.group, FAV]), groups = [], out = [];
    const gname = g => g && g !== FAV && S.groups.some(x => x.id === g) ? groupPath(g) : "";
    // The groups visited (not the one in front), latest first, above the tabs: back to a group as it was left
    for (const r of S.recent.filter(r => r.id !== here)) {
      const s = r.id.startsWith("s:") && all.get(r.id.slice(2)), it = item(r), g = r.g || (s && s.group) || "";
      if (!it) continue;
      out.push(it);
      if (g && !seenG.has(g) && gname(g)) { seenG.add(g); groups.push(groupItem(g, { key: it.key })); }
    }
    return [...groups, ...out];
    function item(r) {
      if (r.id.startsWith("f:")) {
        const p = r.id.slice(2);
        const open = (S.ftabs[r.g] || []).some(t => t.path === p) || fileTabs().some(t => t.path === p);
        if (isView(p)) return { label: viewInfo(p).title, icon: viewInfo(p).icon, detail: isPluginTab(p) ? parsePluginTab(p).plugin : "Orbit", match: viewInfo(p).title,
                                key: ago(r.t), tag: open ? "" : "closed", run: () => openFile(p, { pinned: true }) };
        const g = gname(r.g);
        return { label: isNote(p) ? noteTitle(p) : basename(p), ficon: p, detail: tilde(dirname(p)), match: `${p} ${g}`, key: ago(r.t),
                 tag: [g, isNote(p) ? "note" : "", open ? "" : "closed"].filter(Boolean).join(" · "),
                 run: () => { if (r.g && r.g !== S.group && gname(r.g) && (S.ftabs[r.g] || []).some(t => t.path === p)) switchGroup(r.g, { quiet: true }); openFile(p, { pinned: open }); },
                 menu: where => fileMenu(p, where) };  // a closed file reopens as a preview
      }
      const s = all.get(r.id.slice(2));
      if (!s) return null;
      return { ...sessionItem(s, { detail: s.last ? oneLine(s.last.text) : tilde(s.cwd) }), key: ago(r.t) };
    }
  } }] });
}
/* ⌘⇧F: every session (open or closed) and the notes; newest first. ⏎ opens the card or the note at the line. */
async function historyItems(text, side = false) {
  if (!text) return [];
  const d = await api("GET", "/api/search?q=" + encodeURIComponent(text));
  const terms = text.toLowerCase().split(/\s+/).filter(Boolean);
  const cards = d.cards.map(r => ({
    time: r.time, label: `${r.name} #${r.cid}`, tag: r.closed ? "closed" : r.where, detail: r.snippet, hl: terms, key: ago(r.time),
    mark: S.sessions.get(r.sid), run: () => openHit(r, text, side),
    menu: S.sessions.has(r.sid) ? where => tabMenu(r.sid, where) : null }));
  const notesHits = d.notes.map(n => ({
    time: n.time, label: n.title, ficon: n.path, tag: "note", detail: n.snippet, hl: terms, key: ago(n.time),
    run: () => side ? openFile(n.path, { side: true }) : openNoteAt(n.path, n.line_text), menu: where => fileMenu(n.path, where) }));
  return [...cards, ...notesHits].sort((a, b) => (b.time || 0) - (a.time || 0));
}
async function openHit(r, text, side = false) {
  if (!S.sessions.has(r.sid)) {
    try { const s = await api("POST", `/api/sessions/${r.sid}/reopen`); S.sessions.set(s.id, s); }
    catch (e) { toast("Reopen failed: " + e.message); return; }
  }
  if (side) { openSide({ kind: "session", sid: r.sid }); return; }
  S.pendingFindQ = text.split(/\s+/)[0];  // highlight it in the conversation
  openCard(r.sid, r.cid);
}

/* ⌘⇧E: browse the open file's folders; `..` goes up. */
function browseFolder(dir) {
  dir = dir || (S.file ? dirname(S.file) : ctxRoot());
  if (!dir) { toast("No folder to browse"); return; }
  if (modal && modal.kind === "finder" && modal.opener === "MS:KeyE") closeModal();
  finder({ title: tilde(dir), opener: "MS:KeyE", tabs: [{ name: "Folder", empty: "Empty folder.", items: async () => {
    const t0 = performance.now();
    const d = await api("GET", "/api/dirlist?path=" + encodeURIComponent(dir));
    const t1 = performance.now();
    setTimeout(() => {  // after the list rendered
      const ms = performance.now() - t0;
      if (ms > SLOW_MS) logPage("slow", { op: "breadcrumb list", path: dir, ms: Math.round(ms), fetchMs: Math.round(t1 - t0),
        fetchShare: Math.round((t1 - t0) / ms * 100) / 100, entries: d.entries.length });
    }, 0);
    const items = [];
    if (d.parent) items.push({ label: "..", icon: "folder", detail: tilde(d.parent), run: () => browseFolder(d.parent) });
    for (const e of d.entries) {
      const p = d.path.replace(/\/$/, "") + "/" + e.name;
      items.push(e.dir ? { label: e.name + "/", icon: "folder", run: () => browseFolder(p) }
                       : { label: e.name, ficon: p, run: () => openFile(p), alt: () => openFile(p, { pinned: true }) });
    }
    return items;
  } }] });
}
