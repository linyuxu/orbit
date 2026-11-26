"use strict";
/* ===================================================================== files: tabs, editors, save, conflicts */
const EDITORS = new Map();  // path → {kind, ed, host, hash, mtime, size, dirty, saving, conflict, timer, version}
const isMdPath = p => /\.(md|markdown|mdown|mkd)$/i.test(p);
function openFile(path, { pinned = false, focus = true, rec = true, keepPreview = false, side = false } = {}) {
  if (side) return openSide({ kind: "file", path });
  if (EMBED && !(EMBED.kind === "file" && EMBED.path === path)) { window.parent.postMessage({ orbit: "open-file", path, pinned }, location.origin); return; }
  saveDraft();
  const ft = fileTabs();
  let t = ft.find(x => x.path === path);
  if (!t) {
    // A preview tab is replaced by the next preview; a pinned one stays.
    const prev = ft.findIndex(x => !x.pinned);
    t = { path, pinned };
    if (!pinned && prev >= 0) {
      const old = ft[prev];
      const ed = EDITORS.get(old.path);
      if (ed && ed.dirty) { old.pinned = true; ft.push(t); }  // never drop unsaved work
      else { ft[prev] = t; disposeEditor(old.path); }
    } else ft.push(t);
  } else if (pinned && !t.pinned && !keepPreview) t.pinned = true;
  S.file = path;
  S.lastTab[S.group] = "f:" + path;
  if (rec) record("f:" + path);
  showCurrent(focus);
}
/* ⌘⌃C: Orbit's own view (the Calendar) as a main tab: opens it, goes to it, or (when it is in front) closes it. */
function toggleView(path) {
  if (S.file === path && !EMBED) { closeFile(path); return; }
  openFile(path, { pinned: true });
}
function pinFile(path) {
  const t = fileTabs().find(x => x.path === path);
  if (t && !t.pinned) { t.pinned = true; saveUi(); renderTabs(); renderFileHead(); }
}
async function closeFile(path) {
  if (path === HOME) { toast("The home page is the group's own tab: it stays"); return; }
  const ed = EDITORS.get(path);
  if (ed && ed.dirty) {
    const ok = await saveFile(path);
    if (!ok) {
      const go = await confirmDialog({ title: `Close “${basename(path)}” without saving?`, body: "Saving failed or the file changed on disk. Your edits would be lost.", ok: "Close without saving", danger: true });
      if (!go) return;
    }
  }
  const ft = fileTabs(), i = ft.findIndex(x => x.path === path);
  if (i >= 0) ft.splice(i, 1);
  disposeEditor(path);
  if (isPluginTab(path)) dropPluginTabFrame(path);
  closeAllSides("f:" + path);
  S.closedFiles = [{ path, t: Date.now() / 1000, gid: S.group }, ...S.closedFiles.filter(f => f.path !== path)].slice(0, 30);
  if (S.file === path) {
    const next = ft[Math.min(Math.max(i, 0), ft.length - 1)];
    S.file = next ? next.path : null;
    S.lastTab[S.group] = S.file ? "f:" + S.file : S.current ? "s:" + S.current : null;
    showCurrent(true);
  } else { saveUi(); renderTabs(); }
}
function disposeEditor(path) {
  const ed = EDITORS.get(path);
  if (!ed) return;
  clearTimeout(ed.timer);
  if (ed.ed) ed.ed.view.destroy();
  if (ed.host) ed.host.remove();
  EDITORS.delete(path);
}
/* Load (once) and show a file in the file view. Editors stay alive while their tab is open (undo survives). */
async function showFile(path) {
  for (const [p, e] of EDITORS) if (e.host) e.host.hidden = p !== path;
  let ed = EDITORS.get(path);
  if (!ed) {
    ed = { kind: "loading", host: document.createElement("div"), dirty: false };
    ed.host.className = "host";
    $("#fbody").appendChild(ed.host);
    EDITORS.set(path, ed);
    renderFileHead();
    try {
      const t0 = performance.now();
      const f = await api("GET", "/api/file?path=" + encodeURIComponent(path));
      const t1 = performance.now();
      mountFile(path, ed, f);
      const ms = performance.now() - t0;
      if (ms > SLOW_MS) logPage("slow", { op: "open file", path, ms: Math.round(ms), fetchMs: Math.round(t1 - t0),
        fetchShare: Math.round((t1 - t0) / ms * 100) / 100, size: f.size });
    } catch (e) {
      ed.kind = "error";
      ed.host.innerHTML = `<div class="empty"><h2>Cannot open ${esc(basename(path))}</h2>${esc(e.message)}</div>`;
    }
  }
  ed.host.hidden = false;
  renderFileHead();
  renderStatus();
  applyReveal(path);
  loadBacklinks(path);
  setTimeout(() => {
    if (S.file === path && ed.ed && !modal && mayFocus() && !panelFocused() && !sideFocused() && document.activeElement !== input) ed.ed.focus();
  }, 0);
}
function mountFile(path, ed, f) {
  ed.kind = f.kind; ed.hash = f.hash; ed.mtime = f.mtime; ed.size = f.size;
  if (f.kind === "text") {
    ed.host.classList.toggle("md", isMdPath(path));
    ed.ed = CM.createEditor({
      parent: ed.host, doc: f.text, path,
      onChange: () => fileEdited(path),
      onOpenLink: url => openDocLink(url, path),
      extraKeys: [{ key: "Mod-s", run: () => { saveCurrent(); return true; } }],
      wiki: isNote(path) ? { open: name => openWiki(name, path), names: () => S.noteNames } : null,
      preview: { resolve: (src, embed) => previewSrc(src, embed, path), inline: previewInline },
      onFiles: (files, pos) => dropIntoEditor(path, pos, files.map(file => ({ file }))),
      zoom: url => openLightbox(url),
    });
    applyReveal(path);
    if (!ed.host.dataset.linkmenu) {  // a link's own menu (right-click on it): how to open it
      ed.host.dataset.linkmenu = "1";
      ed.host.addEventListener("contextmenu", e => {
        const l = ed.ed && ed.ed.linkAt && ed.ed.linkAt(e.clientX, e.clientY);
        if (!l || l.wiki) return;
        e.preventDefault();
        linkMenu(l.url, path, { at: { x: e.clientX, y: e.clientY } });
      });
    }
  } else if (f.kind === "image") {
    ed.host.innerHTML = `<div class="imgview"><img src="/api/raw?path=${encodeURIComponent(path)}&v=${f.mtime}" alt=""></div>`;
    ed.host.querySelector("img").addEventListener("click", e => openLightbox(e.target.src));
  } else if (f.kind === "pdf") {
    ed.host.innerHTML = `<iframe class="pdf" src="/api/raw?path=${encodeURIComponent(path)}"></iframe>`;
  } else {
    ed.host.innerHTML = `<div class="empty"><h2>${f.kind === "too large" ? "Too large to edit here" : "Binary file"}</h2>${bytes(f.size)} · <a href="#" data-ext>Open in the default app</a></div>`;
    ed.host.querySelector("[data-ext]").addEventListener("click", e => { e.preventDefault(); e.stopPropagation(); openExternal(path, "default"); });
  }
}
/* Live preview: where an image source points (a URL, a path beside the document, or a vault embed). */
function previewSrc(src, embed, docPath) {
  if (/^(https?:|data:)/i.test(src)) return src;
  if (embed) return `/api/embed?name=${encodeURIComponent(src)}&from=${encodeURIComponent(docPath)}`;
  let p = src;
  try { p = decodeURIComponent(src); } catch {}
  if (p.startsWith("file://")) p = p.slice(7);
  if (!p.startsWith("/")) {
    const parts = dirname(docPath).split("/");
    for (const seg of p.split("/")) { if (seg === "..") parts.pop(); else if (seg && seg !== ".") parts.push(seg); }
    p = parts.join("/");
  }
  return rawUrl(p);
}
/* A link in a document. A web address (or orbit://) opens as before; a file's (beside the document: relative to its
   folder; ~/…, absolute, file://) opens in Orbit when it is text, else in its default app (a PDF in Preview); a
   folder in the Finder. how: "auto", "orbit" (in Orbit whatever it is: an image, a PDF show here too), "finder". */
const isWebLink = url => /^[a-z][a-z0-9+.-]*:/i.test(url) && !/^file:/i.test(url);
function linkPath(url, docPath) {
  let p = String(url).trim().replace(/^<|>$/g, "");
  if (/^file:/i.test(p)) p = p.replace(/^file:(\/\/(localhost)?)?/i, "");
  p = p.replace(/[?#].*$/, "");  // (a heading or a page in it: the file)
  try { p = decodeURIComponent(p); } catch {}
  if (p === "~" || p.startsWith("~/") || p.startsWith("/")) return p;
  const parts = dirname(docPath).split("/");
  for (const seg of p.split("/")) { if (seg === "..") { if (parts.length > 1) parts.pop(); } else if (seg && seg !== ".") parts.push(seg); }
  return parts.join("/") || "/";
}
async function openDocLink(url, docPath, how = "auto") {
  if (isWebLink(url)) return openUrl(url);
  const p = linkPath(url, docPath);
  if (how === "finder") return openExternal(p, "finder");
  let info;
  try { info = await api("GET", `/api/file?path=${encodeURIComponent(p)}`); }
  catch (e) {
    if (/is a folder/.test(e.message)) return openExternal(p, "default");  // (a folder: the Finder)
    toast(`Cannot open ${tilde(p)}: ${/No such file|not found|no such/i.test(e.message) ? "no such file" : e.message}`);
    return;
  }
  if (how === "orbit" || info.kind === "text") openFile(info.path, { pinned: true });
  else openExternal(info.path, "default");
}
/* A link's menu: a file's (Open · Open in Orbit · Reveal in Finder · Copy path), a web address's (Open · Copy). */
function linkMenu(url, docPath, where) {
  if (isWebLink(url)) {
    menu([{ label: "Open link", run: () => openUrl(url) }, { label: "Copy link", run: () => copyText(url) }], where);
    return;
  }
  const p = linkPath(url, docPath);
  menu([
    { label: "Open", run: () => openDocLink(url, docPath) },
    { label: "Open in Orbit", run: () => openDocLink(url, docPath, "orbit") },
    { label: "Reveal in Finder", run: () => openDocLink(url, docPath, "finder") },
    { sep: true },
    { label: "Copy path", run: () => copyText(p) },
  ], where);
}
/* A table cell: inline Markdown, with [[links]] (`\|` already unescaped) as clickable names. */
function previewInline(text) {
  return md.renderInline(text).replace(/\[\[([^\]]+?)\]\]/g, (_, x) => {
    const [target, alias] = x.split("|");
    return `<span data-wiki="${target.split("#")[0].trim()}">${alias || target}</span>`;
  });
}
function fileEdited(path) {
  const ed = EDITORS.get(path);
  if (!ed || ed.applying) return;  // text the disk already has (an agent's append): not an edit
  const was = ed.dirty;
  ed.dirty = true;
  ed.version = (ed.version || 0) + 1;
  pinFile(path);  // editing pins a preview tab
  clearTimeout(ed.timer);
  ed.timer = setTimeout(() => saveFile(path), 1500);  // auto-save 1.5 s after the last edit
  if (!was) { renderTabs(); renderFileHead(); renderStatus(); }
}
/* Save with the hash the editor started from; a 409 means someone else (often the agent) wrote the file. */
async function saveFile(path, { force = false, explicit = false } = {}) {
  const ed = EDITORS.get(path);
  if (!ed || !ed.ed) return true;
  if (!ed.dirty && !force) return true;
  if (ed.conflict && !force) return false;
  clearTimeout(ed.timer);
  const text = ed.ed.getDoc(), version = ed.version;
  // A blank editor must never wipe a file: auto-save skips it, ⌘S asks.
  let allowEmpty = false;
  if (!text && ed.size > 0) {
    logPage("empty save", { path, size: ed.size, explicit });
    if (!explicit) { toast(`Not saved: ${basename(path)} would become empty (⌘S asks)`); return false; }
    if (!(await confirmDialog({ title: `Save “${basename(path)}” empty?`, body: `The editor is empty; the file on disk has ${bytes(ed.size)}. Saving replaces it with nothing.`, ok: "Save empty", danger: true }))) return false;
    allowEmpty = true;
  }
  ed.saving = true; renderStatus();
  try {
    const r = await api("POST", "/api/file", { path, text, base_hash: ed.hash, force, allow_empty: allowEmpty }, { keepalive: true });
    ed.hash = r.hash; ed.mtime = r.mtime; ed.size = r.size; ed.conflict = null;
    if (ed.version === version) ed.dirty = false;  // typed since? stay dirty, the timer saves again
    if (isNote(path)) loadNoteNames();
    return true;
  } catch (e) {
    if (e.status === 409 && e.data && e.data.empty) { toast(`Not saved: ${basename(path)} would become empty`); return false; }
    if (e.status === 409) { ed.conflict = e.data.current || { deleted: true }; return false; }
    toast("Save failed: " + e.message);
    return false;
  } finally {
    ed.saving = false;
    if (EDITORS.get(path) === ed) { renderTabs(); if (S.file === path) renderFileHead(); renderStatus(); }
  }
}
async function saveAll() { await Promise.all([...EDITORS.keys()].map(p => saveFile(p))); }
addEventListener("blur", () => saveAll());  // and on window blur
function saveCurrent() { if (S.file) saveFile(S.file, { force: false, explicit: true }).then(ok => { if (ok) toast("Saved"); else if (EDITORS.get(S.file)?.conflict) toast("Not saved: the file changed on disk"); }); }
/* The third way out of a conflict (builddoc/features/files.md §0): my text goes to "<name> (mine).<ext>" beside the file (a free
   name), the file follows the disk, and the copy opens next to it, so neither version is lost. */
async function saveMineAsCopy(path) {
  const ed = EDITORS.get(path); if (!ed || !ed.ed) return;
  const text = ed.ed.getDoc(), dot = basename(path).lastIndexOf(".");
  const stem = dot > 0 ? basename(path).slice(0, dot) : basename(path), ext = dot > 0 ? basename(path).slice(dot) : "";
  for (let n = 1; n < 100; n++) {
    const copy = `${dirname(path)}/${stem} (mine${n > 1 ? " " + n : ""})${ext}`;
    try {
      await api("POST", "/api/file", { path: copy, text, base_hash: null, create_only: true, allow_empty: true });
      reloadFromDisk(path);
      openFile(copy);
      toast(`Your version is in ${basename(copy)}`);
      return;
    } catch (e) { if (e.status !== 409) { toast(e.message); return; } }
  }
}
function reloadFromDisk(path) {
  const ed = EDITORS.get(path);
  if (!ed) return;
  api("GET", "/api/file?path=" + encodeURIComponent(path)).then(f => {
    if (f.kind === "text" && ed.ed) { ed.ed.setDoc(f.text); ed.hash = f.hash; ed.mtime = f.mtime; ed.size = f.size; }
    ed.dirty = false; ed.conflict = null; clearTimeout(ed.timer);
    applyReveal(path);
    if (S.file === path) loadBacklinks(path);
    renderTabs(); if (S.file === path) renderFileHead(); renderStatus();
  }).catch(e => toast(e.message));
}
/* Watch open files: unchanged buffers follow the disk silently; edited ones get the conflict banner. */
async function pollFiles() {
  const paths = [...EDITORS.entries()].filter(([, e]) => e.kind === "text" || e.kind === "image").map(([p]) => p);
  if (!paths.length || document.hidden) return;
  let st;
  try { st = await api("POST", "/api/stats", { paths }); } catch { return; }
  for (const p of paths) {
    const ed = EDITORS.get(p), s = st[p];
    if (!ed || ed.saving) continue;
    if (s === null) {
      if (!ed.conflict && ed.kind === "text") { ed.conflict = { deleted: true }; if (p === S.file) { renderFileHead(); renderStatus(); } }
      continue;
    }
    if (!s || (s.mtime === ed.mtime && s.size === ed.size)) continue;
    if (ed.kind === "image") { const img = ed.host.querySelector("img"); if (img) img.src = img.src.replace(/&v=\d+/, "") + "&v=" + s.mtime; ed.mtime = s.mtime; ed.size = s.size; continue; }
    if (!ed.dirty) { reloadFromDisk(p); continue; }
    try {
      const f = await api("GET", "/api/file?path=" + encodeURIComponent(p));
      if (f.hash === ed.hash) { ed.mtime = f.mtime; ed.size = f.size; continue; }
      ed.conflict = { text: f.text, hash: f.hash, mtime: f.mtime };
      if (p === S.file) { renderFileHead(); renderStatus(); }
    } catch {}
  }
}
setInterval(pollFiles, 2000);
function renderFileHead() {
  const path = S.file;
  if (!path) return;
  $("#fhead").hidden = isView(path);  // a view (the Calendar) has its own head; the tab already names it
  if (isView(path)) return;
  const ed = EDITORS.get(path) || {};
  const root = isNote(path) ? S.vault : ctxRoot();
  const rel = relTo(root, path);
  const base = rel === path ? "" : root;
  const segs = rel.replace(/^\//, "").split("/");
  const crumbs = segs.map((s, i) => {
    const full = (base ? base : "") + "/" + segs.slice(0, i + 1).join("/");
    return i === segs.length - 1 ? `<span class="leaf">${esc(s)}</span>` : `<span class="seg" data-dir="${esc(full)}">${esc(s)}</span><span>/</span>`;
  }).join("");
  const t = fileTabs().find(x => x.path === path);
  $("#fhead").innerHTML = `<span style="color:var(--ink-3)">${I("file", 15)}</span><div class="crumbs" title="${esc(tilde(path))}">${crumbs}</div>
    <span class="meta2">${ed.size != null ? "· " + bytes(ed.size) : ""}${ed.dirty ? " · modified" : ""}</span>
    <div class="acts">
      ${t && !t.pinned ? `<button class="qbtn" data-act="pin" title="Keep this tab: editing pins it too (⌘E → Pin)">${I("pin", 14)}Pin</button>` : ""}
      ${ed.ed ? `<button class="qbtn" data-act="save" title="Save (⌘S · ⌃X ⌃S); auto-saves 1.5 s after the last edit"${ed.dirty ? "" : " disabled"}>Save</button>` : ""}
      ${EMBED ? `<button class="ibtn sm" data-act="more" title="Ask about this file · ${esc(openInLabel())} · Reload (⌘E)">${I("more", 14)}</button>` :  // a side tab: one slim row; the rest in ⋯ (U5)
        `${cur() ? `<button class="qbtn" data-act="ask" title="Ask the session about this file (⌘E)">Ask about this file</button>` : ""}
      <button class="qbtn" data-act="side" title="Open in the side tab (⌘E → Open in side tab)">Side tab</button>
      <button class="ibtn sm" data-act="ext" title="${esc(openInLabel())} (⌘E; ⌘, → Open files in)">${I("external", 14)}</button>
      <button class="ibtn sm" data-act="reload" title="Reload from disk (⌘E)">${I("refresh", 14)}</button>`}
    </div>`;
  const b = $("#fbanner");
  if (ed.conflict) {
    b.hidden = false;
    b.innerHTML = ed.conflict.deleted
      ? `<span>This file was deleted on disk.</span><span class="sp"></span><button class="btn sm" data-act="overwrite">Save to recreate it</button><button class="btn sm" data-act="closefile">Close</button>`
      : `<span>This file changed on disk while you were editing it.</span><span class="sp"></span><button class="btn sm" data-act="reloaddisk">Reload from disk (discard mine)</button><button class="btn sm" data-act="overwrite">Overwrite with mine</button><button class="btn sm" data-act="savecopy">Save mine as a copy</button>`;
  } else b.hidden = true;
}
$("#fileview").addEventListener("click", async e => {
  const a = e.target.closest("[data-act]"), seg = e.target.closest("[data-dir]");
  if (seg) { browseFolder(seg.dataset.dir); return; }
  if (!a || !S.file) return;
  const path = S.file;
  const act = a.dataset.act;
  if (act === "pin") pinFile(path);
  else if (act === "save") saveCurrent();
  else if (act === "ask") askAboutFile(path);
  else if (act === "side") openSide({ kind: "file", path });
  else if (act === "ext") openExternal(path, "editor");
  else if (act === "more") menu([{ label: "Ask about this file", run: () => askAboutFile(path) }, { label: openInLabel(), run: () => openExternal(path, "editor") },
    { sep: true }, { label: "Reload from disk", run: () => reloadAsk(path) }], { anchor: a });
  else if (act === "reload") reloadAsk(path);
  else if (act === "reloaddisk") reloadFromDisk(path);
  else if (act === "overwrite") { const ok = await saveFile(path, { force: true }); if (ok) toast("Saved over the disk version"); }
  else if (act === "savecopy") saveMineAsCopy(path);
  else if (act === "closefile") { const ed = EDITORS.get(path); if (ed) ed.dirty = false; closeFile(path); }
});
async function reloadAsk(path) {
  const ed = EDITORS.get(path);
  if (ed && ed.dirty && !(await confirmDialog({ title: "Reload from disk?", body: "Your unsaved edits in this file would be lost.", ok: "Reload", danger: true }))) return;
  reloadFromDisk(path);
}
function askAboutFile(path) {
  if (EMBED) { window.parent.postMessage({ orbit: "ask", path }, location.origin); return; }
  const s = cur();
  if (!s) { toast("Open a session to ask about this file"); return; }
  const rel = relTo(s.cwd, path);
  const d = (S.drafts[s.id] || "").trimEnd();
  S.drafts[s.id] = (d ? d + "\n" : "") + `About \`${rel}\`: `;
  LS.set("drafts", S.drafts);
  switchTo(s.id);
  input.setSelectionRange(input.value.length, input.value.length);
}
/* ↗ and "Open in …" follow the "Open files in" setting (⌘,): JetBrains, VS Code, the side tab, or the default app. */
const OPEN_IN = { default: "the default app", jetbrains: "JetBrains", vscode: "VS Code", side: "the side tab" };
const openInLabel = () => "Open in " + OPEN_IN[(S.settings && S.settings.open_in) || "default"];
function cursorLine(path) {
  const ed = EDITORS.get(path), v = ed && ed.ed && ed.ed.view;
  return v ? v.state.doc.lineAt(v.state.selection.main.head).number : null;
}
async function openExternal(path, how, line = how === "editor" ? cursorLine(path) : null) {
  if (how === "editor" && S.settings && S.settings.open_in === "side") {
    // A file of another repo next to the session opens with that repo as its folder.
    let root = null;
    try { root = (await api("GET", `/api/resolve?ref=${encodeURIComponent(path)}&cwd=`)).root; } catch {}
    openSide({ kind: "file", path, ...(root ? { root } : {}), ...(line ? { line } : {}) });
    return;
  }
  api("POST", "/api/open-external", { path, how, line }).then(r => {
    if (r && r.orbit) { openFile(path); toast(r.why); }  // another machine's file: Orbit's editor
  }).catch(e => toast(e.message));
}
/* A file or view tab to another group (its menu's Move to, a drag in the Groups panel): out of this group's tab row,
   at the end of that one's; the tabs' layout is the server's, so every screen follows. Its side tabs go with it (they
   are the file's). Moved from the group in front while shown: the group shows its session. */
function moveFileTab(path, from, to) {
  if (!to || to === from || to === FAV) return;
  const src = S.ftabs[from] || [], t = src.find(x => x.path === path); if (!t) return;
  S.ftabs[from] = src.filter(x => x !== t);
  const dst = fileTabs(to); if (!dst.some(x => x.path === path)) dst.push({ ...t, pinned: true });
  for (const [g, add] of [[from, false], [to, true]]) {
    const ord = S.tabOrder[g]; if (!ord) continue;
    const k = "f:" + path, i = ord.indexOf(k); if (i >= 0) ord.splice(i, 1); if (add) ord.push(k);
  }
  if (S.lastTab[from] === "f:" + path) S.lastTab[from] = null;
  if (from === S.group && S.file === path) { S.file = null; showCurrent(false); }
  saveUi(); renderTabs(); renderLeft();
  toast(`Moved to ${groupPath(to)}`);
}
const moveTabItems = (path, from) => groupMenuItems(gid => moveFileTab(path, from, gid), { current: from });
function fileMenu(path, where) {
  if (isPluginTab(path)) return pluginTabMenu(path, where);
  const t = fileTabs().find(x => x.path === path);
  const root = ctxRoot();
  menu([
    { label: "Open", run: () => openFile(path, { pinned: true }) },
    t ? { label: t.pinned ? "Pinned" : "Pin", disabled: t.pinned, run: () => pinFile(path) } : null,
    EMBED ? null : { label: "Open in side tab", run: () => openSide({ kind: "file", path }) },
    cur() || EMBED ? { label: "Ask about this file", run: () => askAboutFile(path) } : null,
    { label: "Reload from disk", run: async () => {
      const ed = EDITORS.get(path);
      if (ed && ed.dirty && !(await confirmDialog({ title: "Reload from disk?", body: "Your unsaved edits in this file would be lost.", ok: "Reload", danger: true }))) return;
      reloadFromDisk(path);
    } },
    { sep: true },
    isNote(path) ? { label: "Copy [[link]]", run: () => copyText(`[[${noteTitle(path)}]]`) } : null,
    { label: "Copy path", run: () => copyText(path) },
    root && path.startsWith(root + "/") ? { label: "Copy relative path", run: () => copyText(relTo(root, path)) } : null,
    { label: openInLabel(), run: () => openExternal(path, "editor") },
    { label: "Open in Finder", run: () => openExternal(path, "finder") },
    { sep: true },
    { label: "Rename…", run: () => renamePath(path) },
    { label: "Delete…", danger: true, run: () => trashPath(path) },
    t && !EMBED && S.group !== FAV ? { label: "Move to", sub: () => moveTabItems(path, S.group) } : null,
    t && !EMBED ? { label: "Close", key: "⌘W", run: () => closeFile(path) } : null,
  ].filter(Boolean), where || { anchor: document.querySelector(`.tab[data-file="${CSS.escape(path)}"]`) || $("#fhead") });
}
async function newFileIn(dir) {
  const v = await dialog({ title: "New file", fields: [{ key: "name", label: `In ${tilde(dir)} (a/b.txt makes folders; end with / for a folder)` }], ok: "Create",
    validate: v => v.name.trim() ? null : "Give it a name." });
  if (!v) return;
  const name = v.name.trim(), folder = name.endsWith("/");
  const path = dir.replace(/\/$/, "") + "/" + name.replace(/\/$/, "");
  try {
    await api("POST", "/api/files/create", { path, folder });
    await refreshTree(ctxRoot(), true);
    if (!folder) openFile(path, { pinned: true });
  } catch (e) { toast(e.message); }
}
async function newFolderIn(dir) {
  const v = await dialog({ title: "New folder", fields: [{ key: "name", label: `In ${tilde(dir)} (a/b makes both)` }], ok: "Create",
    validate: v => v.name.trim().replace(/\/+$/, "") ? null : "Give it a name." });
  if (!v) return;
  const path = dir.replace(/\/$/, "") + "/" + v.name.trim().replace(/\/+$/, "");
  try {
    await api("POST", "/api/files/create", { path, folder: true });
    if (typeof loadDir === "function" && FILES.dirs && FILES.dirs.has(dir)) loadDir(dir, true);
    await refreshTree(ctxRoot(), true);
    toast(`Made ${tilde(path)}/`);
  } catch (e) { toast(e.message); }
}
/* A new session in this folder (the Files tree's folder menu): the New session dialog with it filled in; another
   machine's folder (/@NAME/…) picks that machine too. */
function newSessionIn(dir) {
  const m = /^\/@([^/]+)(\/.*)?$/.exec(dir);
  newSession(undefined, m ? { machine: m[1], cwd: (m[2] || "/").replace(/^\/(?:home\/[^/]+|Users\/[^/]+|root)(?=\/|$)/, "~") } : { cwd: tilde(dir) });
}
async function renamePath(path) {
  const v = await dialog({ title: "Rename", fields: [{ key: "name", label: "New name (or a/relative/path)", value: basename(path) }],
    validate: v => v.name.trim() ? null : "Give it a name." });
  if (!v) return;
  const to = dirname(path) + "/" + v.name.trim();
  try {
    await saveFile(path);
    await api("POST", "/api/files/rename", { path, new: to });
    for (const gid of Object.keys(S.ftabs)) for (const t of S.ftabs[gid]) if (t.path === path) t.path = to;
    const ed = EDITORS.get(path);
    if (ed) { EDITORS.delete(path); EDITORS.set(to, ed); }
    if (S.file === path) S.file = to;
    saveUi(); renderAll(); if (S.file === to) renderFileHead();
    refreshTree(ctxRoot(), true);
  } catch (e) { toast(e.message); }
}
async function trashPath(path) {
  const ok = await confirmDialog({ title: `Delete “${basename(path)}”?`, body: "It moves to the Trash, so you can put it back from there.", ok: "Move to Trash", danger: true });
  if (!ok) return;
  try {
    await api("POST", "/api/files/trash", { path });
    for (const gid of Object.keys(S.ftabs)) S.ftabs[gid] = S.ftabs[gid].filter(t => t.path !== path && !t.path.startsWith(path + "/"));
    for (const p of [...EDITORS.keys()]) if (p === path || p.startsWith(path + "/")) disposeEditor(p);
    if (S.file && (S.file === path || S.file.startsWith(path + "/"))) { S.file = null; showCurrent(true); }
    else { saveUi(); renderTabs(); }
    refreshTree(ctxRoot(), true);
    toast("Moved to the Trash");
  } catch (e) { toast(e.message); }
}
