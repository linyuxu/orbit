"use strict";
/* ===================================================================== state */
const FAV = "fav";  // the ★ Favorites view (⌘1): every favorite session, whatever its group
const S = {
  sessions: new Map(),            // open sessions, in tab order (the server's order)
  groups: [],                     // [{id, name}] in left-panel order: ⌘2…⌘9
  cards: new Map(),               // sid → [card], loaded on first view
  cardState: new Map(),           // "sid:cid" → last seen state (notifications)
  current: LS.get("current", null),   // the session tab of this group (the folder files belong to)
  file: LS.get("file", null),         // the main tab is this file (null: the session)
  group: LS.get("group", null),   // the group whose tabs the title bar shows (or FAV)
  lastTab: LS.get("lastTab", {}), // group → "s:<sid>" | "f:<path>"
  ftabs: LS.get("ftabs", {}),     // group → [{path, pinned}]
  tabOrder: LS.get("tabOrder", {}), // group → ["s:<sid>" | "f:<path>"]: sessions and files mixed, as the user arranged them
  closedFiles: LS.get("closedFiles", []),  // [{path, t, gid}] for ⌘⇧T
  openSteps: new Set(),
  machines: [],                  // other machines: [{name, ok, error}] (Settings → Machines)
  machine: LS.get("machine", ""), // the Groups panel shows every machine ("") or one ("local", NAME)
  closedSteps: new Set(),         // running cards whose steps the user folded
  atEnd: true,
  pendingScroll: null,
  drafts: LS.get("drafts", {}),
  zoom: LS.get("zoom", ZOOM_DEFAULT),
  hist: [], histPos: -1,          // visited tabs (⌘- ⌘=): {k: "s:<sid>" | "f:<path>", g: its group} (tabs.js)
  recent: LS.get("recent", []),   // [{id: "s:…"|"f:…", t}] (⌘R)
  notes: LS.get("notes", []),
  unread: new Set(LS.get("unread", [])),
  left: LS.get("left", { shown: false, tab: "sessions", w: 240 }),
  follow: LS.get("follow", true),
  filter: "",
  pcursor: null,
  expanded: LS.get("expanded", {}),  // root → [dir] unfolded in the Files tree
  trees: new Map(),               // root → {files, status, git, branch, at, index, dirty}
  sides: LS.get("sides", {}),     // main tab key → {list: [spec], i}: that tab's side tabs and the shown one
  legacySide: LS.get("side", null), // a single side tab saved before side tabs were per main tab
  tabFocus: LS.get("tabFocus", {}), // main tab key → "main" | "side": where the keyboard was in it
  shownKey: null,                 // the main tab key the page shows now
  sideW: LS.get("sideW", 0.45),
  review: { count: 0, root: null, git: false },
  vault: null,                    // the notes vault folder (absolute)
  noteNames: [],                  // note names for [[ completion
  reveal: null,                   // {path, line}: put the cursor on this line once the file shows
  harnesses: [{ id: "claude", label: "Claude Code", models: [] }],  // what /api/harnesses says a session can run
  editing: null,                  // {sid, cid}: the request being edited (✎); sending replaces it
};
const cur = () => S.sessions.get(S.current);
/* The side tab the pane shows: the current main tab's chosen one ({kind, sid?, path?, root?, url?, id}) or null. */
Object.defineProperty(S, "side", { get() { const e = sideEntry(); return e && !e.hidden && e.list[e.i] || null; } });  // (hidden: kept, not shown)
const harnessLabel = id => (S.harnesses.find(h => h.id === id) || { label: id }).label;
const ACTIVE = new Set(["thinking", "running"]);
const groupName = gid => gid === FAV ? "Favorites" : (S.groups.find(g => g.id === gid) || { name: "?" }).name;
/* Groups are a tree (orbit/desk/groups.py): {id, name, parent?}; a group's children are those whose parent it is, in
   list order. GROUP_DEPTH levels at most. ⌘2…⌘9 are the top groups'. */
const GROUP_DEPTH = 3;
const groupOf = gid => S.groups.find(g => g.id === gid);
const groupKids = parent => S.groups.filter(g => (g.parent || "") === (parent || ""));
const topGroups = () => groupKids("");
function groupDepth(gid) { let n = 0, g = groupOf(gid); while (g) { n++; g = g.parent ? groupOf(g.parent) : null; } return n; }
function groupHeight(gid) { return 1 + Math.max(0, ...groupKids(gid).map(c => groupHeight(c.id))); }
function groupDescendants(gid) { return groupKids(gid).flatMap(c => [c.id, ...groupDescendants(c.id)]); }
/* "Work › Orbit › Release" (Favorites as is). */
function groupPath(gid, sep = " › ") {
  if (gid === FAV) return "Favorites";
  const names = []; let g = groupOf(gid);
  while (g && names.length < 8) { names.unshift(g.name); g = g.parent ? groupOf(g.parent) : null; }
  return names.length ? names.join(sep) : "?";
}
/* Every group, a parent before its children: [{g, depth (0 = top)}]. */
function groupTree(parent = "", depth = 0) { return groupKids(parent).flatMap(g => [{ g, depth }, ...groupTree(g.id, depth + 1)]); }
/* Menu items for every group, indented under its parent (⌘2… on the top ones). */
function groupMenuItems(run, { current = "", disabled = () => false } = {}) {
  const tops = topGroups();
  return groupTree().map(({ g, depth }) => {
    const i = depth ? -1 : tops.indexOf(g);
    return { label: g.name, iconHtml: (depth ? `<span style="display:inline-block;width:${depth * 16}px"></span>` : "") + groupMark(g.id, 14), key: g.id === current ? "current" : i >= 0 && i < 8 ? `⌘${i + 2}` : "",
             disabled: g.id === current || disabled(g), run: () => run(g.id) };
  });
}
/* Other machines (from the server): their sessions carry "remote": NAME and sit in this server's groups. The
   Groups panel can show one machine only (S.machine: "" every machine, "local" this one, or NAME). */
const machineOk = s => !S.machine || (S.machine === "local" ? !s.remote : s.remote === S.machine);
const machineUp = name => !(S.machines || []).some(m => m.name === name && !m.ok);
const machineTag = s => s && s.remote ? `<span class="mtag${machineUp(s.remote) ? "" : " off"}" title="${machineUp(s.remote) ? `On ${esc(s.remote)}` : `${esc(s.remote)} is unreachable`}">${esc(s.remote)}</span>` : "";
const inView = (s, gid = S.group) => gid === FAV ? s.favorite : s.group === gid;
function tabIds(gid = S.group) { return [...S.sessions.values()].filter(s => inView(s, gid)).map(s => s.id); }
const fileTabs = (gid = S.group) => (S.ftabs[gid] = S.ftabs[gid] || []);
const tabKey = () => S.file ? "f:" + S.file : S.current ? "s:" + S.current : null;
/* The folder the Files tree, ⌘O Files and ± Review work in: the session tab's folder. */
/* The tree follows what is in front: the session's folder, or, for a file outside it (a note, another repo), that
   file's own root: the vault for a note, its git repo, else its folder. */
const FILE_ROOTS = new Map();  // folder → root, learned from the server
function fileRoot(path) {
  if (S.vault && path.startsWith(S.vault + "/")) return S.vault;
  const dir = dirname(path);
  if (FILE_ROOTS.has(dir)) return FILE_ROOTS.get(dir);
  FILE_ROOTS.set(dir, dir);
  api("GET", "/api/file-root?path=" + encodeURIComponent(path)).then(r => {
    if (!r.root || r.root === dir) return;
    FILE_ROOTS.set(dir, r.root);
    if ((S.file === path || (S.side && S.side.path === path)) && S.left.shown && S.left.tab === "files") { refreshTree(panelRoot()); if (S.follow) followPanel(); renderLeft(); }
  }).catch(() => {});
  return dir;
}
const inFolder = (p, root) => !!root && (p === root || p.startsWith(root.replace(/\/$/, "") + "/"));
/* A view tab: a main tab like a file's, showing one of Orbit's own views (orbit:calendar) instead of a file. */
const VIEWS = { "orbit:calendar": { kind: "calendar", title: "Calendar", icon: "calendar" },
                "orbit:schedules": { kind: "schedules", title: "Schedules", icon: "clock" } };
const isPluginTab = p => typeof p === "string" && p.startsWith("plugin:");  // a plugin's main tab (plugins.js)
const isView = p => typeof p === "string" && (Object.prototype.hasOwnProperty.call(VIEWS, p) || isPluginTab(p));
const viewInfo = p => isPluginTab(p) ? pluginTabInfo(p) : VIEWS[p];
const ctxRoot = () => EMBED && EMBED.root && S.file && S.file.startsWith(EMBED.root + "/") ? EMBED.root  // a side tab's own repo
  : isView(S.file) ? (cur() ? cur().cwd : null)
  : S.file && !(cur() && (inFolder(S.file, cur().cwd) || inFolder(S.file.replace(/^\/private/, ""), cur().cwd.replace(/^\/private/, "")))) ? fileRoot(S.file)
  : cur() ? cur().cwd : S.file ? dirname(S.file) : null;
/* What the keyboard is on (follow follows it): a side tab that has it (its file, its session's folder, its review's
   folder), else the main tab (its file, its session's folder). {path?, root, sid?, side?} */
/* Which pane had the keyboard last, the main tab or a side tab: going from a side tab to the left panel passes the
   main page for a moment (a click on the panel does not focus it at once); only the keyboard landing in the main pane
   (its input, its editor, its conversation) makes it the main tab again. */
const LAST_PANE = { side: false };
function focusCtx() {
  const inMain = () => { const a = document.activeElement, p = document.getElementById("pane"); return !!(a && p && p.contains(a)); };
  const sideOn = !EMBED && typeof sideFocused === "function" && (sideFocused() || (LAST_PANE.side && !inMain()));
  const sp = sideOn ? S.side : null;
  if (sp && sp.kind === "file" && sp.path) return { path: sp.path, root: fileRoot(sp.path), side: true };
  if (sp && sp.kind === "session" && S.sessions.has(sp.sid)) return { root: S.sessions.get(sp.sid).cwd, sid: sp.sid, side: true };
  if (sp && sp.kind === "review" && sp.root) return { root: sp.root, side: true };
  return { path: S.file && !isView(S.file) ? S.file : null, root: ctxRoot(), sid: S.file ? null : S.current };
}
const isNote = p => !!(S.vault && p && p.startsWith(S.vault + "/") && /\.md$/i.test(p));
const noteTitle = p => basename(p).replace(/\.md$/i, "");
/* The folder the left panel's tree shows: the vault on the Notes tab, else the session's folder. */
const panelRoot = () => S.left.tab === "notes" ? S.vault : ctxRoot();

function setSessions(list, groups) {
  S.sessions = new Map(list.filter(s => !s.closed).map(s => [s.id, s]));
  if (groups) S.groups = groups;
  if (EMBED) { if (EMBED.sid) S.current = EMBED.sid; return; }
  const asked = PARAMS.get("session");  // ?session=ID: opened on that session (a remote's page opened from another machine)
  if (asked && !setSessions.done && S.sessions.has(asked)) {
    setSessions.done = true;
    S.group = S.sessions.get(asked).group || S.group; S.current = asked;
  }
  if (S.group !== FAV && !S.groups.some(g => g.id === S.group)) S.group = S.groups[0] ? S.groups[0].id : null;
  if (!S.sessions.has(S.current) || !inView(S.sessions.get(S.current))) {
    const want = String(S.lastTab[S.group] || "");
    const sid = want.startsWith("s:") ? want.slice(2) : want.startsWith("f:") ? null : want;
    S.current = sid && S.sessions.has(sid) && inView(S.sessions.get(sid)) ? sid : (tabIds()[0] || null);
  }
  if (S.file && S.file !== HOME && !fileTabs().some(t => t.path === S.file)) S.file = null;  // (a group's home is no file tab: kept)
}
function saveUi() {
  if (EMBED) return;
  LS.set("current", S.current); LS.set("file", S.file); LS.set("group", S.group); LS.set("lastTab", S.lastTab);
  LS.set("left", S.left); LS.set("follow", S.follow); LS.set("ftabs", S.ftabs); LS.set("tabOrder", S.tabOrder); LS.set("sides", S.sides); LS.set("tabFocus", S.tabFocus); LS.set("sideW", S.sideW);
  LS.set("expanded", S.expanded); LS.set("closedFiles", S.closedFiles);
  if (typeof layoutChanged === "function") layoutChanged();  // (the tabs: the server's, layout.js)
  reportCtx();
}
