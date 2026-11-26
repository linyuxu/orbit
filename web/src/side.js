"use strict";
/* ===================================================================== side tabs (embedded pages, per main tab) */
/* Each main tab has its own list of side tabs (S.sides[tab key] = {list, i, hidden}); the pane shows the current tab's
   chosen one. A session's side tabs are the server's, like its conversation (orbit/desk/sides.py): every screen shows
   the same; a change here goes there first-hand (shown at once, then as the server has it), and each change anywhere
   comes back as a side event. Only `hidden` (the panel put away on this screen) and the width are this screen's. A
   file tab's side tabs are this page's own. Frames stay alive while recently shown (a small pool), so switching back
   keeps an editor's undo and scroll. */
const FRAMES = new Map();  // side tab id → iframe
const FRAME_POOL = 10;
const sideKey = () => tabKey() || "_";
const sideId = () => Math.random().toString(36).slice(2, 10);
function sideEntry(key = sideKey()) {
  if (EMBED) return null;
  if (S.legacySide && tabKey()) {  // one side tab saved before they were per main tab: it joins the tab shown at load
    S.sides[tabKey()] = { list: [S.legacySide], i: 0 }; S.legacySide = null;
    try { localStorage.removeItem("orbit.side"); } catch {}
  }
  const e = S.sides[key];
  if (e && e.list) e.list = e.list.filter(sp => sp.kind !== "plugin");  // (a plugin's iframe page: gone in plugin API 2.0)
  if (!e || !e.list || !e.list.length) return null;
  e.i = Math.max(0, Math.min(e.i | 0, e.list.length - 1));
  for (const sp of e.list) if (!sp.id) sp.id = sideId();
  return e;
}
const sideFrame = () => { const sp = S.side; return sp && FRAMES.get(sp.id) || null; };
const sideFocused = () => !EMBED && !!S.side && !!document.activeElement && document.activeElement === sideFrame();
const sameSide = (a, b) => a.kind === b.kind && (a.kind === "web" ? a.url === b.url
  : (a.sid || "") === (b.sid || "") && (a.path || "") === (b.path || "") && (a.root || "") === (b.root || ""));
function sideSrc(spec) {
  const { id, t, ...rest } = spec;  // the id names the frame; the embedded page does not need it (nor when it came)
  return spec.kind === "web" ? spec.url : "/?embedtab=" + encodeURIComponent(JSON.stringify(rest));
}
/* ---------- a session's side tabs: the server's (orbit/desk/sides.py) */
const sideSid = key => !EMBED && key && key.startsWith("s:") ? key.slice(2) : "";
const SIDE_SRV = { loaded: new Set(), loading: new Set(), rev: {} };
/* The server's state of a session's side tabs, shown: frames of tabs that went are dropped; a tab that came shows the
   panel again (an agent put it there: it should be seen); this screen's `hidden` stays otherwise. */
function applySideState(sid, st) {
  if (!st || !Array.isArray(st.items)) return;
  if (SIDE_SRV.rev[sid] != null && (st.rev || 0) < SIDE_SRV.rev[sid]) return;
  SIDE_SRV.rev[sid] = st.rev || 0; SIDE_SRV.loaded.add(sid);
  const key = "s:" + sid, old = S.sides[key], had = new Map(((old && old.list) || []).map(x => [x.id, x]));
  const ids = new Set(st.items.map(x => x.id));
  for (const id of had.keys()) if (!ids.has(id)) dropFrame(id);
  if (!st.items.length) delete S.sides[key];
  else {
    const came = st.items.some(x => !had.has(x.id));
    S.sides[key] = { list: st.items.map(x => ({ ...x })), i: Math.max(0, Math.min(st.i | 0, st.items.length - 1)),
                     hidden: old && !came ? !!old.hidden : false };
  }
  if (key === sideKey()) { renderSide(); renderReviewBadge(); }
  saveUi();
}
async function loadSideState(sid) {
  if (SIDE_SRV.loading.has(sid)) return;
  SIDE_SRV.loading.add(sid);
  try {
    const st = await api("GET", `/api/sessions/${encodeURIComponent(sid)}/side`);
    // Side tabs this page had for it before the server kept them: they go there once
    const mine = S.sides["s:" + sid], moved = LS.get("sidesMoved", []);
    if (!st.items.length && mine && mine.list && mine.list.length && !moved.includes(sid)) {
      LS.set("sidesMoved", [...moved, sid].slice(-500));
      let last = st;
      for (const sp of mine.list) last = await api("POST", `/api/sessions/${encodeURIComponent(sid)}/side`, { op: "open", spec: sp });
      const keep = mine.list[mine.i] && last.items.find(x => x.id === mine.list[mine.i].id);
      if (keep) last = await api("POST", `/api/sessions/${encodeURIComponent(sid)}/side`, { op: "select", id: keep.id });
      applySideState(sid, last);
    } else applySideState(sid, st);
  } catch {}
  finally { SIDE_SRV.loading.delete(sid); }
}
/* A change to a session's side tabs: to the server (shown here already); what it answers is what is shown. */
function sideOp(key, body) {
  const sid = sideSid(key); if (!sid) return;
  api("POST", `/api/sessions/${encodeURIComponent(sid)}/side`, body).then(st => applySideState(sid, st))
    .catch(e => { toast("Side tab: " + e.message); SIDE_SRV.loaded.delete(sid); loadSideState(sid); });
}
function sideTitle(spec) {
  if (!spec) return "";
  if (spec.kind === "session") { const s = S.sessions.get(spec.sid); return s ? s.name : "Session"; }
  if (spec.kind === "file") return basename(spec.path);
  if (spec.kind === "review") return "± Review · " + basename(spec.root);
  if (spec.kind === "web") return spec.title || "Page";
  if (spec.kind === "pview") return spec.title || pluginTabInfo(spec.path).title;  // a plugin's view (pviews.js)
  if (spec.kind === "schedules") return "Schedules";
  if (spec.kind === "calendar") return "Calendar";
  return "Side tab";
}
/* A side tab's mark, as its main tab wears it: a session's status and agent, a file's type, a view's icon. */
function sideIcon(spec) {
  if (spec.kind === "session") { const s = S.sessions.get(spec.sid); return s ? stateMark(s) + harnessIcon(s.harness, 13) : `<span class="fi">${I("terminal", 13)}</span>`; }
  if (spec.kind === "file") return `<span class="fi">${isView(spec.path) ? I(viewInfo(spec.path).icon, 13) : fileIcon(spec.path)}</span>`;
  if (spec.kind === "pview") return `<span class="fi">${I(pluginTabInfo(spec.path).icon, 13)}</span>`;
  const ic = { review: "swap", web: "external", schedules: "clock", calendar: "calendar" }[spec.kind] || "file";
  return `<span class="fi">${I(ic, 13)}</span>`;
}
function ensureFrame(spec) {
  let fr = FRAMES.get(spec.id);
  const src = sideSrc(spec);
  if (!fr) {
    fr = document.createElement("iframe");
    fr.title = "Side tab";
    // An agent's page (orbit-agent show FILE.html: served here) runs sandboxed: scripts yes, Orbit's origin no. A site
    // (orbit-agent show URL, ⌘O a URL) is another origin anyway: it keeps its own (its cookies, its sign-in), its
    // links open new windows outside the sandbox, and it is told nothing of where Orbit is.
    if (spec.kind === "web" && webExternal(spec)) {
      fr.setAttribute("sandbox", "allow-scripts allow-popups allow-forms allow-same-origin allow-popups-to-escape-sandbox allow-downloads allow-modals");
      fr.setAttribute("referrerpolicy", "no-referrer");
      fr.setAttribute("allow", "clipboard-write; fullscreen");
    } else if (spec.kind === "web") fr.setAttribute("sandbox", "allow-scripts allow-popups allow-forms");
    fr.addEventListener("focus", () => { LAST_PANE.side = true; renderSide(); followSoon(); });  // (the keyboard went beside: follow follows it)
    fr.addEventListener("load", () => { fr.dataset.loaded = "1"; sideLoaded(spec, fr); });
    fr.setAttribute("src", src); fr.dataset.src = src;
    $("#sideframes").appendChild(fr);
    FRAMES.set(spec.id, fr);
  } else if ((fr.dataset.src || fr.getAttribute("src")) !== src) {
    const base = s => s.replace(/%2C%22line%22%3A\d+|%22line%22%3A\d+%2C?/g, "");  // (the same file at another line)
    if (spec.kind === "file" && base(fr.getAttribute("src")) === base(src)) {
      try { fr.contentWindow.postMessage({ orbit: "reveal", path: spec.path, line: spec.line }, location.origin); } catch {}
      fr.setAttribute("data-line", String(spec.line || ""));
    } else { fr.setAttribute("src", src); fr.dataset.src = src; }
  }
  fr.dataset.seen = Date.now();
  if (FRAMES.size > FRAME_POOL) {  // the least recently shown frames go; they load again when shown
    const old = [...FRAMES.entries()].filter(([id]) => id !== spec.id).sort((a, b) => a[1].dataset.seen - b[1].dataset.seen);
    for (const [id] of old.slice(0, FRAMES.size - FRAME_POOL)) dropFrame(id);
  }
  return fr;
}
/* A site in a side tab: its bar (where it is, Reload, Copy link, Open in the browser). A site that refuses to be framed
   (most sign-in pages, GitHub, Google) shows blank, and a page cannot tell: the bar is always there for it. */
const webExternal = sp => !!sp && sp.kind === "web" && /^https?:\/\//i.test(sp.url || "");
function renderSideWeb(sp) {
  const bar = $("#sideweb"), on = webExternal(sp);
  bar.hidden = !on;
  if (!on) return;
  let host = sp.url; try { host = new URL(sp.url).host; } catch {}
  const mixed = location.protocol === "https:" && /^http:/i.test(sp.url);  // (an https page frames no http one)
  const html = `<span class="sw-host" title="${esc(sp.url)}">${esc(host)}</span>` +
    (mixed ? `<span class="sw-note">http: shown only in the browser</span>` : `<span class="sw-note" title="Some sites refuse to be shown inside another page: open those in the browser">blank? open it in the browser</span>`) +
    `<button class="ibtn sm" data-sw="reload" title="Reload">${I("refresh", 12)}</button>` +
    `<button class="ibtn sm" data-sw="copy" title="Copy link">${I("copy", 12)}</button>` +
    `<button class="btn sm quiet" data-sw="open" title="Open in the browser">Open in browser</button>`;
  if (bar.innerHTML !== html) bar.innerHTML = html;
}
$("#sideweb").addEventListener("click", e => {
  const b = e.target.closest("[data-sw]"), sp = S.side; if (!b || !webExternal(sp)) return;
  if (b.dataset.sw === "open") openUrl(sp.url);
  else if (b.dataset.sw === "copy") copyText(sp.url);
  else if (b.dataset.sw === "reload") { const fr = FRAMES.get(sp.id); if (fr) { fr.dataset.loaded = ""; fr.setAttribute("src", sp.url); } }
});
/* A web address typed in a finder (⌘O, ⌘⌃O): "Open beside" first. */
function urlLead(q) {
  q = q.trim();
  if (EMBED || !q || /\s/.test(q)) return [];
  const url = /^https?:\/\//i.test(q) ? q : /^(localhost|[\w-]+(\.[\w-]+)+)(:\d+)?([/?#]\S*)?$/i.test(q) && !/^[\w.-]+\.(md|txt|js|py|json|html?|css|ts|rs|go|sh|ya?ml|toml)$/i.test(q) ? "https://" + q : "";
  if (!url) return [];
  let host = url; try { host = new URL(url).host; } catch { return []; }
  return [{ label: "Open beside", detail: url, icon: "panel", tag: "web", run: () => openSide({ kind: "web", url, title: host }) },
          { label: "Open in the browser", detail: url, icon: "external", run: () => openUrl(url) }];
}
function dropFrame(id) { const fr = FRAMES.get(id); if (fr) { fr.remove(); FRAMES.delete(id); } }
const eachFrame = fn => { for (const fr of FRAMES.values()) { try { fn(fr.contentWindow); } catch {} } };
function applySideWidth() { document.documentElement.style.setProperty("--sidew", Math.round(S.sideW * 100) + "%"); }
function renderSide() {
  if (EMBED) return;
  const ssid = sideSid(sideKey());
  if (ssid && !SIDE_SRV.loaded.has(ssid)) loadSideState(ssid);  // (a session's: from the server, once, then by events)
  const e = sideEntry(), sp = S.side;
  $("#side").hidden = !sp; $("#ssplit").hidden = !sp;
  if (sp) ensureFrame(sp);
  for (const [id, fr] of FRAMES) fr.hidden = !sp || id !== sp.id;
  $("#side").classList.toggle("focused", sideFocused());
  const html = e ? e.list.map((x, k) => `<div class="stab${k === e.i ? " active" : ""}${x.preview ? " preview" : ""}" role="tab" data-si="${k}" title="${esc(sideTitle(x))}${x.path ? " · " + esc(tilde(x.path)) : ""}${x.preview ? " · preview: the next file clicked takes its place (double-click or editing keeps it)" : ""} (⌥⇧[ ⌥⇧])">${sideIcon(x)}<span class="nm">${esc(sideTitle(x))}</span><span class="x" data-sclose="${k}" title="Close (⌘W there)">${I("x", 11)}</span></div>`).join("") : "";
  const tabs = $("#sidetabs");
  if (tabs.innerHTML !== html) {
    tabs.innerHTML = html;
    const a = tabs.querySelector(".stab.active"); if (a) a.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
  $("#revbadge").classList.toggle("on", !!sp && sp.kind === "review");
  renderSideWeb(sp);
  const peek = $("#sidepeek"), off = !!e && !!e.hidden;  // hidden side tabs: a handle at the right edge brings them back
  peek.hidden = !off;
  if (off) { peek.innerHTML = `${I("panel", 13)}<span>${e.list.length}</span>`; peek.title = `Show the side panel: ${e.list.map(sideTitle).join(", ")} (⌘\\)`; }
  applySideWidth();
}
/* Hide the side panel (its tabs are kept) or show it again (⌘\, the panel's –, the handle at the right edge). */
function toggleSidePanel() {
  if (EMBED) return;
  const e = sideEntry();
  if (!e) { toast("No side tabs here: ⌘⌃O opens one beside"); return; }
  const wasFocused = sideFocused();
  e.hidden = !e.hidden;
  renderSide(); renderReviewBadge(); saveUi();
  if (wasFocused) focusMain();
}
/* Open a side tab in the current main tab, or in the main tab `key` (an agent's page goes with its session's tab);
   or show it again if that tab has it already. */
function openSide(spec, { focus = true, key = sideKey() } = {}) {
  if (EMBED) { window.parent.postMessage({ orbit: "open-side", spec }, location.origin); return; }
  if (key !== sideKey()) focus = false;
  const e = S.sides[key] = sideEntry(key) || { list: [], i: 0 };
  e.hidden = false;  // (a new or shown again side tab: the panel shows)
  const k = e.list.findIndex(x => sameSide(x, spec));
  const p = spec.preview && k < 0 ? e.list.findIndex(x => x.preview) : -1;
  if (p >= 0) {  // a preview (a file clicked in the Files panel): in the last one's place
    const old = e.list[p];
    dropFrame(old.id);
    e.list[p] = { ...spec, id: sideId() }; e.i = p;
    renderSide(); renderReviewBadge(); saveUi();
    sideOp(key, { op: "replace", id: old.id, spec: e.list[p] });
    if (focus) focusSide();
    return;
  }
  if (k >= 0 && !spec.preview && e.list[k].preview) spec = { ...spec, preview: false };  // (opened on purpose: kept)
  if (k >= 0) {
    const old = e.list[k], fr = FRAMES.get(old.id);
    if (spec.kind === "file" && spec.line && fr && fr.contentWindow) {
      // a file already open beside: move its cursor (a new src would load it again, and drop unsaved edits)
      try { fr.contentWindow.postMessage({ orbit: "reveal", path: spec.path, line: spec.line }, location.origin); } catch {}
    } else if (spec.line || (spec.kind === "web" && spec.title !== old.title) || spec.preview === false) e.list[k] = { ...old, ...spec, id: old.id };
    e.i = k;
  } else {
    e.i = e.list.length ? e.i + 1 : 0;
    e.list.splice(e.i, 0, { ...spec, id: spec.id || sideId() });
  }
  renderSide(); renderReviewBadge(); saveUi();
  sideOp(key, { op: "open", spec: { ...e.list[e.i], ...(spec.line ? { line: spec.line } : {}) } });
  if (focus) focusSide();
}
function showSideAt(k, { focus } = {}) {
  const e = sideEntry(); if (!e) return;
  const wasFocused = sideFocused();
  e.i = k; e.hidden = false;
  renderSide(); renderReviewBadge(); saveUi();
  if (e.list[k]) sideOp(sideKey(), { op: "select", id: e.list[k].id });
  if (focus ?? wasFocused) focusSide();
}
/* A preview side tab kept (double-click on it, editing in it, Keep open): the next file clicked opens beside it. */
function pinSide(k, key = sideKey()) {
  const e = sideEntry(key); if (!e || !e.list[k] || !e.list[k].preview) return;
  e.list[k] = { ...e.list[k], preview: false };
  renderSide(); saveUi();
  sideOp(key, { op: "open", spec: e.list[k] });
}
/* A file's preview beside goes once it is opened in the main pane (a double-click in the Files panel). */
function dropSidePreview(path) {
  const e = sideEntry(), k = e ? e.list.findIndex(x => x.preview && x.kind === "file" && x.path === path) : -1;
  if (k >= 0) closeSide(k);
}
/* What a side tab's menu offers: into the main pane, kept, copied, shown elsewhere; closed (it, the others, all). */
function sideTabMenu(k, where) {
  const e = sideEntry(); if (!e || !e.list[k]) return;
  const x = e.list[k], s = x.kind === "session" && S.sessions.get(x.sid);
  const toMain = x.kind === "session" ? () => switchTo(x.sid) : x.kind === "file" ? () => { openFile(x.path, { pinned: true }); dropSidePreview(x.path); }
    : x.kind === "review" ? () => { closeSide(k); toggleReview(); } : x.kind === "web" ? () => openUrl(x.url) : null;
  const fr = FRAMES.get(x.id);
  menu([
    toMain ? { label: x.kind === "web" ? "Open in the browser" : "Open as a main tab", run: toMain } : null,
    x.preview ? { label: "Keep open", run: () => pinSide(k) } : null,
    k !== e.i ? { label: "Show", run: () => showSideAt(k, { focus: true }) } : null,
    { sep: true },
    x.kind === "file" ? { label: "Copy path", run: () => copyText(x.path) } : null,
    x.kind === "file" && ctxRoot() && x.path.startsWith(ctxRoot() + "/") ? { label: "Copy relative path", run: () => copyText(relTo(ctxRoot(), x.path)) } : null,
    x.kind === "file" ? { label: "Open in Finder", run: () => openExternal(x.path, "finder") } : null,
    s ? { label: "Copy link", run: () => copyText(`orbit://session/${x.sid}`) } : null,
    x.kind === "web" ? { label: "Copy link", run: () => copyText(x.url) } : null,
    fr ? { label: "Reload", run: () => { fr.dataset.loaded = ""; fr.setAttribute("src", sideSrc(x)); } } : null,
    { sep: true },
    { label: "Close", key: "⌘W there", run: () => closeSide(k) },
    e.list.length > 1 ? { label: "Close the others", run: () => { const keep = e.list[k].id; for (let n = e.list.length - 1; n >= 0; n--) if (e.list[n] && e.list[n].id !== keep) closeSide(n); } } : null,
    { label: "Close all side tabs", run: () => { for (let n = e.list.length - 1; n >= 0; n--) closeSide(n); } },
  ].filter(Boolean), where);
}
/* Close one side tab of the current main tab (default: the shown one); its neighbour shows. */
function closeSide(k) {
  const e = sideEntry(); if (!e) return;
  if (typeof k !== "number") k = e.i;
  const wasFocused = sideFocused();
  const key = sideKey(), [gone] = e.list.splice(k, 1);
  if (gone) { dropFrame(gone.id); sideOp(key, { op: "close", id: gone.id }); }
  if (!e.list.length) delete S.sides[key];
  else if (k < e.i) e.i--;
  else e.i = Math.min(e.i, e.list.length - 1);
  renderSide(); renderReviewBadge(); saveUi();
  if (wasFocused) { if (S.side) focusSide(); else focusMain(); }
  else if (document.activeElement === document.body) focusMain();
}
/* Close every side tab of a main tab (⌃X 1 for the current one; closing a main tab). A session's are the server's (on
   every screen): here its panel is only put away. */
function closeAllSides(key = sideKey()) {
  const e = S.sides[key];
  delete S.tabFocus[key];
  if (!e) return;
  if (sideSid(key)) {
    const here = key === sideKey(), wasFocused = here && sideFocused();
    e.hidden = true;
    if (here) { renderSide(); renderReviewBadge(); }
    saveUi();
    if (wasFocused) focusMain();
    return;
  }
  const here = key === sideKey(), wasFocused = here && sideFocused();
  for (const sp of e.list || []) dropFrame(sp.id);
  delete S.sides[key];
  if (here) { renderSide(); renderReviewBadge(); }
  saveUi();
  if (wasFocused) focusMain();
}
function cycleSide(d) {  // ⌥⇧[ ⌥⇧]: from the main page or inside the pane (it forwards the key)
  const e = sideEntry();
  if (!e) { toast("No side tab: ⌘⌃O opens anything there"); return; }
  if (e.list.length > 1) showSideAt((e.i + d + e.list.length) % e.list.length);
}
function focusSide() {
  const fr = sideFrame();
  if (!fr) return;
  fr.focus();
  const tell = () => { try { fr.contentWindow.postMessage({ orbit: "focus" }, location.origin); } catch {} };
  tell(); setTimeout(tell, 300);
  renderSide();
}
function toggleSideFocus() {  // ⌘⇧O
  if (!S.side) { toast("No side tab: ⌘⇧I opens ± Review, ⌘⌃O opens anything there"); return; }
  if (sideFocused()) focusMain(); else focusSide();
  renderSide();
}
/* Three-state toggle for one kind of side tab (± Review, Schedules, Calendar): hidden → show + focus; shown, not
   focused → focus; focused → close it. Another side tab of the same kind in this main tab is shown, not duplicated. */
function toggleSideOf(kind, spec, { focus = true } = {}) {
  if (S.side && S.side.kind === kind) {
    if (!focus) return;
    if (!sideFocused()) { focusSide(); return; }
    closeSide(); return;
  }
  const e = sideEntry(), k = e ? e.list.findIndex(x => x.kind === kind) : -1;
  if (k >= 0) { showSideAt(k, { focus }); return; }
  openSide(spec, { focus });
}
function toggleReview() {  // ⌘⇧I
  if (S.side && S.side.kind === "review") return toggleSideOf("review");
  const root = cur() ? cur().cwd : ctxRoot();
  if (!root && !(sideEntry() || { list: [] }).list.some(x => x.kind === "review")) { toast("Open a session: ± Review shows its folder's uncommitted changes"); return; }
  toggleSideOf("review", { kind: "review", root, sid: S.current });
}
$("#sidehide").innerHTML = I("panel", 12);
$("#sidehide").addEventListener("click", toggleSidePanel);
$("#sidepeek").addEventListener("click", toggleSidePanel);
$("#sideclose").innerHTML = I("x", 12);
$("#sideclose").addEventListener("click", () => closeSide());
$("#sidefocus").innerHTML = I("swap", 12);
$("#sidefocus").addEventListener("click", toggleSideFocus);
$("#sidetabs").addEventListener("mousedown", e => {
  if (e.button !== 0) return;
  const x = e.target.closest("[data-sclose]"), t = e.target.closest(".stab");
  if (!t) return;
  e.preventDefault();
  if (x) closeSide(+x.dataset.sclose);
  else if (e.detail >= 2) pinSide(+t.dataset.si);  // a double-click keeps a preview (told by the click count: the first click redraws)
  else showSideAt(+t.dataset.si, { focus: true });  // a click on a side tab takes the keyboard there
});
$("#sidetabs").addEventListener("contextmenu", e => {
  const t = e.target.closest(".stab"); if (!t) return;
  e.preventDefault();
  sideTabMenu(+t.dataset.si, { at: { x: e.clientX, y: e.clientY } });
});
addEventListener("blur", () => setTimeout(() => { renderSide(); if (sideFocused()) { LAST_PANE.side = true; followSoon(); } }, 0));  // (into a side frame)
document.addEventListener("focusin", e => {  // (the keyboard in the main pane: the main tab is followed again)
  const p = $("#pane"); if (!EMBED && p && p.contains(e.target) && LAST_PANE.side) { LAST_PANE.side = false; followSoon(); }
});
addEventListener("focus", () => setTimeout(() => { renderSide(); followSoon(); }, 0));  // (back to the main page)
/* Messages between the page and its side tab (same origin only). */
/* Orbit's keys from an agent's page beside (its own origin: the server put a small script in it, handler.py
   SHOW_KEYS): only from a web side tab's frame, only these keys. */
const WEB_KEYS = new Set(["MS:KeyO", "M:KeyW", "AS:BracketLeft", "AS:BracketRight", "M:Backslash", "MS:BracketLeft", "MS:BracketRight",
  "M:KeyO", "MC:KeyO", "M:KeyK", "M:KeyJ", "M:KeyN", "M:KeyB", ...[1, 2, 3, 4, 5, 6, 7, 8, 9].map(i => "M:Digit" + i)]);
addEventListener("message", e => {
  if (!EMBED && e.data && e.data.orbit === "web-key") {
    const fr = [...FRAMES.values()].find(f => f.contentWindow === e.source && f.hasAttribute("sandbox"));
    if (fr && WEB_KEYS.has(String(e.data.combo))) runForwarded(String(e.data.combo));
    return;
  }
  if (e.origin !== location.origin || !e.data || !e.data.orbit) return;
  const m = e.data;
  if (EMBED) {
    if (m.orbit === "focus") focusMain();
    else if (m.orbit === "zoom") setZoom(m.z, false);
    else if (m.orbit === "prefs") applyPrefs(m.p, false);
    else if (m.orbit === "reveal" && EMBED.kind === "file" && m.path === EMBED.path) { S.reveal = { path: m.path, line: m.line }; applyReveal(m.path); }
    else if (m.orbit === "quote-in" && EMBED.kind === "session") quoteHere({ text: String(m.text || ""), ref: String(m.ref || "") });
    return;
  }
  const fromView = [...EDITORS.values()].some(ed => ed.frame && ed.frame.contentWindow === e.source);  // a main view tab
  if (m.orbit === "key" && fromView) { if (m.combo === "CX:1") onlyMain(); else { const fn = BINDINGS.get(m.combo); if (fn) fn(); } }
  else if (m.orbit === "key") runForwarded(m.combo);
  else if (m.orbit === "open-file") { if (m.line) S.reveal = { path: m.path, line: m.line }; openFile(m.path, { pinned: !!m.pinned }); if (m.line && S.file === m.path) applyReveal(m.path); }
  else if (m.orbit === "open-side") openSide(m.spec);
  else if (m.orbit === "renamed" && m.from && m.to) pathRenamed(String(m.from), String(m.to));  // (a note's title edited beside)
  else if (m.orbit === "side-pin") {  // edited beside: a preview kept
    const ent = sideEntry(), k = ent ? ent.list.findIndex(x => FRAMES.get(x.id) && FRAMES.get(x.id).contentWindow === e.source) : -1;
    if (k >= 0) pinSide(k);
  }
  else if (m.orbit === "ask") askAboutFile(m.path);
  else if (m.orbit === "open-note-at") openNoteAt(m.path, m.line);
  else if (m.orbit === "open-card") openUrl(`orbit://session/${m.sid}${m.cid ? "#" + m.cid : ""}`);
  else if (m.orbit === "review-count") { S.review.count = m.count; renderReviewBadge(); }
  else if (m.orbit === "quote") quoteTo({ text: String(m.text || ""), ref: String(m.ref || "") }, S.current);  // (from beside: the main session)
  else if (m.orbit === "open-ptab") openPluginTab(String(m.plugin || ""), String(m.kind || ""), String(m.key ?? ""), { title: m.title || "", focus: m.focus !== false });
});
/* A quote for a session beside (notes.js quoteBeside): sent once its frame has loaded. Kept by session, not by side
   tab id: the server's state of a new side tab may come back with its own id, and a new frame. */
const SIDE_QUOTES = new Map();  // session id → [quotes]
function sideLoaded(spec, fr) {
  const qs = spec.kind === "session" && SIDE_QUOTES.get(spec.sid); if (!qs) return;
  SIDE_QUOTES.delete(spec.sid);
  for (const q of qs) try { fr.contentWindow.postMessage({ orbit: "quote-in", text: q.text, ref: q.ref }, location.origin); } catch {}
}
function quoteToSide(q, spec) {
  const fr = FRAMES.get(spec.id);
  if (fr && fr.dataset.loaded) { try { fr.contentWindow.postMessage({ orbit: "quote-in", text: q.text, ref: q.ref }, location.origin); } catch {} }
  else SIDE_QUOTES.set(spec.sid, [...(SIDE_QUOTES.get(spec.sid) || []), q]);
}
function runForwarded(combo) {
  if (combo === "M:KeyW") return closeSide();
  if (combo === "MS:KeyO") { focusMain(); renderSide(); return; }
  if (combo === "CX:1") return onlyMain();
  const fn = BINDINGS.get(combo);
  if (fn) fn();
}
