"use strict";
/* ===================================================================== what plugins add to the page */
/* builddoc/plugins.md §5. Declared in a plugin's plugin.toml; nothing of the plugin runs in this page, and since plugin
   API 2.0 none of its pages either (no iframes): Orbit draws what it gives (pviews.js).
   - status items: set by the plugin (POST /api/plugins/NAME/status), shown at the right of the status bar; a click
     opens its panel or URL;
   - panels: its views (a doc, a list, a thread) in the side pane (More menu, a status item);
   - actions: menu items (More menu) or answer items (right-click an answer) that run the plugin's command with what
     they act on as JSON on stdin; its first line of output is shown. This machine's page only. */
const PUI = { status: [], panels: [], actions: [], sidebars: [], tabs: [], badges: {}, palette: [], messages: [], schemes: {}, local: true };
async function loadPluginUi() {
  try { Object.assign(PUI, await api("GET", "/api/plugin-ui")); } catch {}
  bindPluginKeys();
  renderPluginStatus();
  if (S.left.tab.startsWith("p:") && !pluginSidebar(S.left.tab)) S.left.tab = "sessions";  // its plugin is gone
  pviewsReloaded();  // (views whose plugin or kind changed are drawn again; the lists' counts fetched)
  renderLeft(); renderTabs();
}
function renderPluginStatus() {
  const el = $("#sb-plugins"); if (!el) return;
  el.innerHTML = PUI.status.map((x, i) => `<button type="button" class="plst lv-${esc(x.level)}" data-i="${i}" title="${esc((x.title || x.text) + " · " + x.plugin)}">${esc(x.text)}</button>`).join("");
  el.hidden = !PUI.status.length;
}
/* A panel's view path: a doc, a list or a thread of the plugin (pviews.js). */
function panelPath(p) {
  return p.view === "thread" ? pluginTabPath(p.plugin, p.kind, p.key) : pluginTabPath(p.plugin, p.view === "list" ? "~list" : "~doc", p.view === "list" ? p.source : p.doc);
}
function openPluginPanel(plugin, id) {
  const p = PUI.panels.find(x => x.plugin === plugin && x.id === id);
  if (p) openSide({ kind: "pview", path: panelPath(p), title: p.title });
  else toast(`${plugin} has no panel ${id}`);
}
async function runPluginAction(a, ctx) {
  try {
    const r = await api("POST", `/api/plugins/${a.plugin}/run`, { args: a.args, stdin: JSON.stringify(ctx), session: ctx.session || "" });
    const out = ((r.code === 0 ? r.stdout : (r.stderr || r.stdout)) || "").trim().split("\n");
    toast(r.code === 0 ? `${a.title}: ${out[0] || "done"}`.slice(0, 240) : `${a.title} failed: ${out[out.length - 1] || "exit " + r.code}`.slice(0, 240));
  } catch (e) { toast(e.message); }
}
function pluginMenuItems() {
  const items = PUI.panels.map(p => ({ label: p.title, key: shortcutLabel(p.shortcut), run: () => openPluginPanel(p.plugin, p.id) }));
  if (PUI.local) for (const a of PUI.actions.filter(x => x.where === "menu"))
    items.push({ label: a.title, run: () => runPluginAction(a, { session: S.current || "", file: S.file || "" }) });
  return items;
}
/* Right-click on an answer: the plugins' answer actions (only when there are some: else the browser's own menu). */
function answerMenu(c, at) {
  const acts = PUI.local ? PUI.actions.filter(x => x.where === "answer") : [];
  if (!acts.length || !c) return false;
  const card = { id: c.id, text: c.text, answer: c.answer || "", state: c.state, origin: c.origin || null };
  menu([
    { label: "Copy answer", run: () => copyText(c.answer || "") },
    { sep: true },
    ...acts.map(a => ({ label: a.title, run: () => runPluginAction(a, { session: c.sid, card }) })),
  ], at);
  return true;
}
$("#sb-plugins").addEventListener("click", e => {
  const b = e.target.closest("[data-i]"); if (!b) return;
  const x = PUI.status[+b.dataset.i]; if (!x) return;
  if (x.panel) openPluginPanel(x.plugin, x.panel); else if (x.url) openUrl(x.url);
});


/* ===================================================================== shortcuts (plugin API 1.3) */
/* A left tab's or a panel's `shortcut` ("Mod-Shift-S": ⌘⇧S on macOS, Ctrl+Shift+S elsewhere; "Ctrl-Alt-S": ⌃⌥S), as
   the server let it through (not Orbit's, not another plugin's). It joins BINDINGS, so it works wherever Orbit's own
   keys do: the main page, the input and editors, the shell, and forwarded from side tabs. One of Orbit's own bindings
   is never replaced. */
const IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || "");
function shortcutCombo(sc) {  // Mod-Shift-S → MS:KeyS (Ctrl elsewhere: CS:KeyS); Ctrl-Alt-7 → CA:Digit7 (keys.js comboOf)
  const m = /^(Mod-Shift|Ctrl-Alt)-([A-Z0-9])$/.exec(sc || "");
  if (!m) return "";
  return (m[1] === "Ctrl-Alt" ? "CA" : IS_MAC ? "MS" : "CS") + ":" + (/\d/.test(m[2]) ? "Digit" : "Key") + m[2];
}
function shortcutLabel(sc) {
  const m = /^(Mod-Shift|Ctrl-Alt)-([A-Z0-9])$/.exec(sc || "");
  if (!m) return "";
  return IS_MAC ? (m[1] === "Ctrl-Alt" ? "⌃⌥" : "⌘⇧") + m[2] : (m[1] === "Ctrl-Alt" ? "Ctrl+Alt+" : "Ctrl+Shift+") + m[2];
}
const PLUGIN_KEYS = new Map();  // combo → {plugin, label, what, run}: what the plugins' shortcuts bound
function pluginKeyList() {
  return [...PUI.sidebars.map(x => ({ plugin: x.plugin, sc: x.shortcut, what: `Show its ${x.title} tab in the left panel (again: the tab before)`,
                                     run: () => togglePluginSidebar(`p:${x.plugin}:${x.id}`) })),
          ...PUI.panels.map(x => ({ plugin: x.plugin, sc: x.shortcut, what: `${x.title} in the side pane (toggle)`,
                                   run: () => togglePluginPanel(x.plugin, x.id) }))].filter(x => x.sc);
}
function bindPluginKeys() {
  for (const [combo, k] of PLUGIN_KEYS) if (BINDINGS.get(combo) === k.run) BINDINGS.delete(combo);
  PLUGIN_KEYS.clear();
  for (const k of pluginKeyList()) {
    const combo = shortcutCombo(k.sc);
    if (!combo || BINDINGS.has(combo) || PLUGIN_KEYS.has(combo)) continue;  // (Orbit's own wins; the server said so too)
    PLUGIN_KEYS.set(combo, { ...k, label: shortcutLabel(k.sc) });
    BINDINGS.set(combo, k.run);
  }
}
/* In a side tab (an embedded page) the plugins' keys go to the main page, which runs them; a plugin's view there
   needs the rest too (main.js waits for it). */
const EMBED_PUI = EMBED ? api("GET", "/api/plugin-ui").then(d => {
  Object.assign(PUI, d);
  for (const k of pluginKeyList()) { const c = shortcutCombo(k.sc); if (c) PLUGIN_KEYS.set(c, { ...k, label: shortcutLabel(k.sc) }); }
}, () => {}) : null;
/* A left tab's key: show the tab and give it the keyboard (the panel opens if hidden); pressed while that tab shows,
   back to the tab before. */
function togglePluginSidebar(tab) {
  if (!pluginSidebar(tab)) return;
  if (S.left.shown && S.left.tab === tab) {
    const back = S.left.prev && S.left.prev !== tab && ptabs().some(t => t[0] === S.left.prev) ? S.left.prev : "sessions";
    setPanelTab(back); focusPanel(); return;
  }
  if (!S.left.shown) { S.left.shown = true; applyLeft(); }
  setPanelTab(tab); focusPanel(); saveUi();
}
/* A panel's key: open it in the side pane (with the keyboard), or close it when it is the side tab shown. */
function togglePluginPanel(plugin, id) {
  const p = PUI.panels.find(x => x.plugin === plugin && x.id === id);
  if (!p) return;
  if (S.side && S.side.kind === "pview" && S.side.path === panelPath(p)) { closeSide(); return; }
  openPluginPanel(plugin, id);
}
/* The plugins' shortcuts in the ⌘/ sheet: a section per plugin, after Orbit's. */
function pluginShortcutGroups() {
  const by = new Map();
  for (const [combo, k] of PLUGIN_KEYS) {
    if (!by.has(k.plugin)) by.set(k.plugin, []);
    by.get(k.plugin).push([k.label, k.what, combo, k.run]);
  }
  return [...by].map(([plugin, rows]) => [`${plugin} (plugin)`, rows]);
}

/* ===================================================================== left tabs and main tabs (1.1; views: 2.0) */
/* A left-panel tab ([[ui.sidebar]]: "p:<plugin>:<id>": a list of an item source, drawn like the Sessions list in
   left.js, or a doc) and main tabs of a plugin's kinds ([[ui.tab]]: a view path "plugin:<plugin>/<kind>/<key>": a
   thread or a doc). A panel's doc or list has the path "plugin:<plugin>/~doc/<id>", "…/~list/<source>". All drawn by
   pviews.js. */
const pluginSidebar = tab => PUI.sidebars.find(x => `p:${x.plugin}:${x.id}` === tab);
const pluginKind = (plugin, kind) => PUI.tabs.find(t => t.plugin === plugin && t.kind === kind);
const pluginTabPath = (plugin, kind, key) => `plugin:${plugin}/${kind}/${encodeURIComponent(key)}`;
function parsePluginTab(path) {
  const m = /^plugin:([a-z0-9_-]+)\/(~doc|~list|[a-z0-9_-]+)\/(.*)$/.exec(path || "");
  if (!m) return null;
  try { return { plugin: m[1], kind: m[2], key: decodeURIComponent(m[3]) }; } catch { return null; }
}
/* The tab's title and icon (the title the view or the opener gave, else the kind's, the panel's). */
function pluginTabInfo(path) {
  const p = parsePluginTab(path) || {}, k = pluginKind(p.plugin, p.kind) || {};
  const pn = PUI.panels.find(x => x.plugin === p.plugin && panelPath(x) === path) || PUI.sidebars.find(x => x.plugin === p.plugin && x.view === "doc" && p.kind === "~doc" && x.doc === p.key);
  const t = Object.values(S.ftabs).flat().find(x => x.path === path) || {};
  const icon = k.icon && ICONS[k.icon] ? k.icon : pn && pn.icon && ICONS[pn.icon] ? pn.icon : "plug";
  return { kind: "plugin", title: t.title || (pn && pn.title) || k.title || p.kind || "Plugin", icon };
}
/* ---- the left-panel tab: a list (left.js) or a doc (here) */
function showPluginSidebar(tab) {
  const sb = pluginSidebar(tab), box = $("#pside");
  if (!sb) return;
  const v = mountPView("sb:" + tab, pluginTabPath(sb.plugin, "~doc", sb.doc), box);
  for (const f of box.children) f.hidden = f !== v.el;
  v.show();
}
function focusPluginSidebar() { const v = PVIEWS.get("sb:" + S.left.tab); if (v) v.focus(); }
function focusPanel() {
  const sb = S.left.tab.startsWith("p:") && pluginSidebar(S.left.tab);
  if (sb && sb.view === "doc") focusPluginSidebar(); else plist.focus();
}
function ptabBadge(id) {
  const sb = pluginSidebar(id);
  let b = PUI.badges[id.slice(2)];
  if (!b && sb && sb.view === "list") { const n = pluginListUnread(sb); b = n ? { count: n, level: "info" } : null; }  // its unread items
  return b && b.count ? `<span class="pbadge lv-${esc(b.level || "info")}">${b.count > 99 ? "99+" : b.count}</span>` : "";
}
/* ---- main tabs */
function openPluginTab(plugin, kind, key, { title = "", focus = true } = {}) {
  if (EMBED) { window.parent.postMessage({ orbit: "open-ptab", plugin, kind, key: String(key), title, focus }, location.origin); return; }
  if (!pluginKind(plugin, kind)) { toast(`${plugin} has no tab kind ${kind}`); return; }
  const path = pluginTabPath(plugin, kind, String(key));
  const ft = fileTabs();
  let t = ft.find(x => x.path === path);
  if (!t) { t = { path, pinned: true }; ft.push(t); }
  if (title) t.title = String(title).slice(0, 80);
  if (focus) openFile(path, { pinned: true });
  else { saveUi(); renderTabs(); toast(`${t.title || pluginKind(plugin, kind).title} opened in a tab`, () => openFile(path, { pinned: true })); }
}
function showPluginTab(path) {
  const box = $("#pview");
  for (const f of box.children) f.hidden = true;
  if (!viewSpec(path)) { box.querySelector(".pempty") || box.insertAdjacentHTML("beforeend", `<div class="empty pempty"><h2>Not available</h2>Its plugin is not loaded (Settings → Plugins).</div>`); box.querySelector(".pempty").hidden = false; return; }
  const v = mountPView(path, path, box);
  v.el.hidden = false;
  v.show();
  renderStatus();
  setTimeout(() => { if (S.file === path && mayFocus() && !panelFocused() && !sideFocused()) v.focus(); }, 0);
}
function dropPluginTabFrame(path) { dropPView(path); }
function pluginTabMenu(path, where) {
  const p = parsePluginTab(path), k = p && pluginKind(p.plugin, p.kind), v = PVIEWS.get(path);
  menu([
    ...((k && PUI.local ? k.menu : []) || []).map(it => ({ label: it.title, run: () => runPluginAction({ plugin: p.plugin, title: it.title, args: it.args }, { kind: p.kind, key: p.key }) })),
    k && k.menu.length && PUI.local ? { sep: true } : null,
    v && v.url && v.url() ? { label: "Open at the source", run: () => openUrl(v.url()) } : null,
    { label: "Reload", run: () => { const w = PVIEWS.get(path); if (w) w.reload(); else showPluginTab(path); } },
    { label: "Open in side tab", run: () => openSide({ kind: "pview", path, title: pluginTabInfo(path).title }) },
    { label: "Close", key: "⌘W", run: () => closeFile(path) },
  ].filter(Boolean), where || { anchor: document.querySelector(`.tab[data-file="${CSS.escape(path)}"]`) || $("#tabs") });
}
/* ---- notifications (the bell) */
function pluginNotified(n) {
  S.notes = [{ kind: "plugin", plugin: n.plugin, name: n.title, text: n.body, time: n.time, open: n.open || null }, ...S.notes].slice(0, 50);
  LS.set("notes", S.notes);
  renderStatus();
  toast(`${n.title}${n.body ? ": " + n.body : ""}`.slice(0, 200), n.open ? () => openPluginTab(n.plugin, n.open.kind, n.open.key, { title: n.open.title }) : null);
}
