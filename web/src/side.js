"use strict";
/* ===================================================================== side tabs (embedded pages, per main tab) */
/* Each main tab keeps its own list of side tabs (S.sides[tab key] = {list, i}); the pane shows the current tab's chosen
   one. Frames stay alive while recently shown (a small pool), so switching back keeps an editor's undo and scroll. */
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
  const { id, ...rest } = spec;  // the id names the frame; the embedded page does not need it
  return spec.kind === "web" ? spec.url : "/?embedtab=" + encodeURIComponent(JSON.stringify(rest));
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
function ensureFrame(spec) {
  let fr = FRAMES.get(spec.id);
  const src = sideSrc(spec);
  if (!fr) {
    fr = document.createElement("iframe");
    fr.title = "Side tab";
    // A web page (orbit-agent show) runs sandboxed: scripts yes, but no access to Orbit's origin.
    if (spec.kind === "web") fr.setAttribute("sandbox", "allow-scripts allow-popups allow-forms");
    fr.addEventListener("focus", () => renderSide());
    fr.setAttribute("src", src);
    $("#sideframes").appendChild(fr);
    FRAMES.set(spec.id, fr);
  } else if (fr.getAttribute("src") !== src) fr.setAttribute("src", src);
  fr.dataset.seen = Date.now();
  if (FRAMES.size > FRAME_POOL) {  // the least recently shown frames go; they load again when shown
    const old = [...FRAMES.entries()].filter(([id]) => id !== spec.id).sort((a, b) => a[1].dataset.seen - b[1].dataset.seen);
    for (const [id] of old.slice(0, FRAMES.size - FRAME_POOL)) dropFrame(id);
  }
  return fr;
}
function dropFrame(id) { const fr = FRAMES.get(id); if (fr) { fr.remove(); FRAMES.delete(id); } }
const eachFrame = fn => { for (const fr of FRAMES.values()) { try { fn(fr.contentWindow); } catch {} } };
function applySideWidth() { document.documentElement.style.setProperty("--sidew", Math.round(S.sideW * 100) + "%"); }
function renderSide() {
  if (EMBED) return;
  const e = sideEntry(), sp = S.side;
  $("#side").hidden = !sp; $("#ssplit").hidden = !sp;
  if (sp) ensureFrame(sp);
  for (const [id, fr] of FRAMES) fr.hidden = !sp || id !== sp.id;
  $("#side").classList.toggle("focused", sideFocused());
  const html = e ? e.list.map((x, k) => `<div class="stab${k === e.i ? " active" : ""}" role="tab" data-si="${k}" title="${esc(sideTitle(x))}${x.path ? " · " + esc(tilde(x.path)) : ""} (⌥⇧[ ⌥⇧])"><span class="nm">${esc(sideTitle(x))}</span><span class="x" data-sclose="${k}" title="Close (⌘W there)">${I("x", 11)}</span></div>`).join("") : "";
  const tabs = $("#sidetabs");
  if (tabs.innerHTML !== html) {
    tabs.innerHTML = html;
    const a = tabs.querySelector(".stab.active"); if (a) a.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
  $("#revbadge").classList.toggle("on", !!sp && sp.kind === "review");
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
  if (k >= 0) {
    const old = e.list[k], fr = FRAMES.get(old.id);
    if (spec.kind === "file" && spec.line && fr && fr.contentWindow) {
      // a file already open beside: move its cursor (a new src would load it again, and drop unsaved edits)
      try { fr.contentWindow.postMessage({ orbit: "reveal", path: spec.path, line: spec.line }, location.origin); } catch {}
    } else if (spec.line || (spec.kind === "web" && spec.title !== old.title)) e.list[k] = { ...old, ...spec, id: old.id };
    e.i = k;
  } else {
    e.i = e.list.length ? e.i + 1 : 0;
    e.list.splice(e.i, 0, { ...spec, id: spec.id || sideId() });
  }
  renderSide(); renderReviewBadge(); saveUi();
  if (focus) focusSide();
}
function showSideAt(k, { focus } = {}) {
  const e = sideEntry(); if (!e) return;
  const wasFocused = sideFocused();
  e.i = k; e.hidden = false;
  renderSide(); renderReviewBadge(); saveUi();
  if (focus ?? wasFocused) focusSide();
}
/* Close one side tab of the current main tab (default: the shown one); its neighbour shows. */
function closeSide(k) {
  const e = sideEntry(); if (!e) return;
  if (typeof k !== "number") k = e.i;
  const wasFocused = sideFocused();
  const [gone] = e.list.splice(k, 1);
  if (gone) dropFrame(gone.id);
  if (!e.list.length) delete S.sides[sideKey()];
  else if (k < e.i) e.i--;
  else e.i = Math.min(e.i, e.list.length - 1);
  renderSide(); renderReviewBadge(); saveUi();
  if (wasFocused) { if (S.side) focusSide(); else focusMain(); }
  else if (document.activeElement === document.body) focusMain();
}
/* Close every side tab of a main tab (⌃X 1 for the current one; closing a main tab). */
function closeAllSides(key = sideKey()) {
  const e = S.sides[key];
  delete S.tabFocus[key];
  if (!e) return;
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
  else showSideAt(+t.dataset.si, { focus: true });  // a click on a side tab takes the keyboard there
});
addEventListener("blur", () => setTimeout(renderSide, 0));
addEventListener("focus", () => setTimeout(renderSide, 0));
/* Messages between the page and its side tab (same origin only). */
addEventListener("message", e => {
  if (e.origin !== location.origin || !e.data || !e.data.orbit) return;
  const m = e.data;
  if (EMBED) {
    if (m.orbit === "focus") focusMain();
    else if (m.orbit === "zoom") setZoom(m.z, false);
    else if (m.orbit === "prefs") applyPrefs(m.p, false);
    else if (m.orbit === "reveal" && EMBED.kind === "file" && m.path === EMBED.path) { S.reveal = { path: m.path, line: m.line }; applyReveal(m.path); }
    return;
  }
  const fromView = [...EDITORS.values()].some(ed => ed.frame && ed.frame.contentWindow === e.source);  // a main view tab
  if (m.orbit === "key" && fromView) { if (m.combo === "CX:1") onlyMain(); else { const fn = BINDINGS.get(m.combo); if (fn) fn(); } }
  else if (m.orbit === "key") runForwarded(m.combo);
  else if (m.orbit === "open-file") { if (m.line) S.reveal = { path: m.path, line: m.line }; openFile(m.path, { pinned: !!m.pinned }); if (m.line && S.file === m.path) applyReveal(m.path); }
  else if (m.orbit === "open-side") openSide(m.spec);
  else if (m.orbit === "ask") askAboutFile(m.path);
  else if (m.orbit === "open-note-at") openNoteAt(m.path, m.line);
  else if (m.orbit === "open-card") openUrl(`orbit://session/${m.sid}${m.cid ? "#" + m.cid : ""}`);
  else if (m.orbit === "review-count") { S.review.count = m.count; renderReviewBadge(); }
  else if (m.orbit === "quote") quoteTo({ text: String(m.text || ""), ref: String(m.ref || "") });
  else if (m.orbit === "open-ptab") openPluginTab(String(m.plugin || ""), String(m.kind || ""), String(m.key ?? ""), { title: m.title || "", focus: m.focus !== false });
});
function runForwarded(combo) {
  if (combo === "M:KeyW") return closeSide();
  if (combo === "MS:KeyO") { focusMain(); renderSide(); return; }
  if (combo === "CX:1") return onlyMain();
  const fn = BINDINGS.get(combo);
  if (fn) fn();
}
