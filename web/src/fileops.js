"use strict";
/* ===================================================================== the Files tree as a file manager */
/* Copy, cut and paste, and drag and drop, in the Files tab, on this Mac and across machines (POST /api/files/transfer:
   workspace/transfer.py), and with Finder through this Mac's clipboard.
   Keys (the cursor's row; with the filter empty, else they edit it): ⌥W copy · ⌃W cut · ⌃Y paste (Emacs), and ⌘C ⌘X ⌘V.
   Paste goes into the cursor's folder (on a file: its folder). Copy and cut also put the files on the Mac's clipboard
   (another machine's are fetched to a cache first), so Finder's ⌘V pastes them (a copy: Finder takes no cut from an
   app). ⌃Y pastes what Finder copied when the clipboard changed since (its change count), else what was copied here.
   Drag a file or folder onto a folder (or a file: its folder; a root): this machine → a move, another machine → a copy
   (as Finder), ⌥ held → a copy. Hovering a closed folder opens it; near the panel's top or bottom it scrolls. Files
   dragged in from Finder are put in that folder (here or another machine's). */
const FCLIP = { paths: [], cut: false, count: null };
const FDRAG = { path: "", paths: [], over: "", at: 0, timer: 0 };
/* Multi-select: ⌘-click toggles a row, ⇧-click selects a range, ⇧↑ ⇧↓ (⌃⇧P ⌃⇧N) grow it from the cursor, Esc clears.
   Copy, cut, drag and Move to Trash take the selection (a folder with something of its own selected: the folder). */
const FSEL = { keys: new Map(), anchor: "", pressed: "" };  // row key → its path (kept while folded away)
function selectedPaths(r = prow()) {
  const ps = FSEL.keys.size ? [...FSEL.keys.values()] : [opsPath(r)].filter(Boolean);
  return ps.filter(p => !ps.some(q => q !== p && p.startsWith(q + "/")));  // (inside a selected folder: the folder takes it)
}
function selClear() { if (FSEL.keys.size) { FSEL.keys.clear(); FSEL.anchor = ""; return true; } return false; }
function selAdd(r) { const p = opsPath(r); if (p) FSEL.keys.set(r.k, p); }
function selRange(fromK, toK) {
  const i = PROWS.findIndex(x => x.k === fromK), j = PROWS.findIndex(x => x.k === toK);
  if (i < 0 || j < 0) return;
  for (const r of PROWS.slice(Math.min(i, j), Math.max(i, j) + 1)) selAdd(r);
}
const opsRow = r => !!r && ["file", "dir", "lfile", "ldir"].includes(r.kind);
const opsPath = r => opsRow(r) ? rowPath(r) : r && r.kind === "root" && !r.place ? r.root : "";
/* The folder a paste or drop goes into: a folder itself, a file's folder, a root (a place: its real path). */
function opsFolder(r) {
  if (!r) return "";
  if (r.kind === "root") return r.place ? ((FILES.dirs.get(r.root) || {}).real || "") : r.root;
  if (r.kind === "dir" || r.kind === "ldir") return rowPath(r);
  if (r.kind === "file" || r.kind === "lfile") return dirname(rowPath(r));
  return "";
}
const machineOfPath = p => (/^\/@([^/]+)/.exec(p || "") || [])[1] || "";
/* Copy (⌥W, ⌘C) or cut (⌃W, ⌘X) the selection, else the cursor's file or folder (or a path: a menu's). */
async function filesClip(cut, r = prow(), path = "") {
  const ps = path ? [path] : selectedPaths(r);
  if (!ps.length) { toast("A file or a folder to copy"); return; }
  if (cut && ps.some(p => S.trees.has(p))) { toast("A session's folder is not cut: copy it (⌥W)"); return; }
  FCLIP.paths = ps; FCLIP.cut = cut; FCLIP.count = null;
  renderLeft();
  const what = ps.length === 1 ? basename(ps[0]) : `${ps.length} items`;
  toast(`${cut ? "Cut" : "Copied"} ${what}: ⌃Y pastes ${ps.length === 1 ? "it" : "them"} into a folder${ps.some(machineOfPath) ? "" : " · Finder: ⌘V"}`);
  try {
    const d = await api("POST", "/api/clipboard/files", { paths: ps });
    if (FCLIP.paths === ps) FCLIP.count = d.count;
    if (d.fetched) toast(`${what} on the clipboard: Finder's ⌘V pastes ${ps.length === 1 ? "it" : "them"}`);
  } catch (e) { if (e.status !== 403) console.warn("clipboard:", e.message); }  // (another machine's page: no Mac clipboard)
}
/* Move to Trash (⌘⌫, the menu): the selection, else the cursor's row; asked once. */
async function filesTrash(r = prow()) {
  const ps = selectedPaths(r).filter(p => !S.trees.has(p));
  if (!ps.length) return;
  if (ps.length === 1) { selClear(); return trashPath(ps[0]); }
  const ok = await confirmDialog({ title: `Move ${ps.length} items to the Trash?`, body: ps.slice(0, 8).map(p => basename(p)).join(", ") + (ps.length > 8 ? "…" : "") + ". You can put them back from there.", ok: "Move to Trash", danger: true });
  if (!ok) return;
  let n = 0;
  for (const p of ps) {
    try {
      await api("POST", "/api/files/trash", { path: p }); n++;
      for (const gid of Object.keys(S.ftabs)) S.ftabs[gid] = S.ftabs[gid].filter(t => t.path !== p && !t.path.startsWith(p + "/"));
      for (const q of [...EDITORS.keys()]) if (q === p || q.startsWith(p + "/")) disposeEditor(q);
      if (S.file && (S.file === p || S.file.startsWith(p + "/"))) { S.file = null; showCurrent(true); }
    } catch (e) { toast(`${basename(p)}: ${e.message}`); }
  }
  selClear(); saveUi(); renderTabs();
  refreshAround(ps);
  toast(`Moved ${n} item${n === 1 ? "" : "s"} to the Trash`);
}
/* Paste (⌃Y, ⌘V) into a folder: what Finder copied (the clipboard changed since), else what was copied here. */
async function filesPaste(dir = opsFolder(prow())) {
  if (!dir) { toast("A folder (or a file in it) to paste into"); return; }
  let srcs = null, move = false;
  try {
    const c = await api("GET", "/api/clipboard/files");
    const finder = (c.paths || []).length && (FCLIP.count == null ? !FCLIP.paths.length : c.count !== FCLIP.count);
    if (finder) srcs = c.paths;
  } catch {}
  if (!srcs) {
    if (!FCLIP.paths.length) { toast("Nothing to paste: copy (⌥W) or cut (⌃W) a file, or copy one in Finder"); return; }
    srcs = [...FCLIP.paths]; move = FCLIP.cut;
  }
  const done = await runTransfers(srcs, dir, move);
  if (move && done.length) { FCLIP.paths = []; FCLIP.cut = false; FCLIP.count = null; renderLeft(); }
}
/* Move or copy each into dir; the open tabs follow a move; the trees involved redraw. */
async function runTransfers(srcs, dir, move) {
  const done = [];
  const slow = setTimeout(() => toast(`${move ? "Moving" : "Copying"} ${srcs.length === 1 ? basename(srcs[0]) : srcs.length + " items"}…`), 600);
  for (const src of srcs) {
    try {
      if (move && EDITORS.get(src) && EDITORS.get(src).dirty) await saveFile(src);
      const r = await api("POST", "/api/files/transfer", { src, dir, move });
      if (r.same) continue;
      done.push({ src, to: r.path, moved: !!r.moved });
      if (r.moved) retargetTabs(src, r.path);
    } catch (e) { toast(`${basename(src)}: ${e.message}`); }
  }
  clearTimeout(slow);
  refreshAround([...srcs, dir]);
  if (done.length) {
    const d = done[0], where = tilde(dir);
    toast(done.length === 1 ? `${d.moved ? "Moved" : "Copied"} ${basename(d.src)} to ${where}${basename(d.to) !== basename(d.src) ? ` as ${basename(d.to)}` : ""}`
                            : `${done[0].moved ? "Moved" : "Copied"} ${done.length} items to ${where}`);
  }
  return done;
}
/* A moved file's (or folder's files') tabs and editors take the new path. */
function retargetTabs(from, to) {
  const re = p => p === from ? to : p.startsWith(from + "/") ? to + p.slice(from.length) : p;
  let changed = false;
  for (const gid of Object.keys(S.ftabs)) for (const t of S.ftabs[gid]) { const n = re(t.path); if (n !== t.path) { t.path = n; changed = true; } }
  for (const [p, ed] of [...EDITORS]) { const n = re(p); if (n !== p) { EDITORS.delete(p); EDITORS.set(n, ed); } }
  if (S.file && re(S.file) !== S.file) { S.file = re(S.file); changed = true; }
  if (changed) { saveUi(); renderAll(); if (S.file) renderFileHead(); }
}
/* The listings that hold these paths, fetched again. */
function refreshAround(paths) {
  const dirs = new Set(paths.flatMap(p => [p, dirname(p)]));
  for (const root of S.trees.keys()) if (paths.some(p => p === root || p.startsWith(root + "/") || root.startsWith(p + "/"))) refreshTree(root, true);
  for (const [k, c] of FILES.dirs) if (dirs.has(k) || dirs.has(c.real)) loadDir(k, true);
}
/* The tree's rows, after each draw: which can be dragged, which are cut. */
function opsDecorate() {
  if (S.left.tab !== "files" || S.filter) return;
  const cut = FCLIP.cut ? new Set(FCLIP.paths) : null;
  for (const el of plist.querySelectorAll(".prow.ft[data-k]")) {
    const r = PROWS.find(x => x.k === el.dataset.k); if (!r) continue;
    if (opsPath(r)) el.draggable = true;
    if (cut && cut.has(opsPath(r))) el.classList.add("cutrow");
    if (FSEL.keys.has(r.k)) el.classList.add("msel");
  }
}
/* ---------- drag and drop */
const opsRowAt = el => { const row = el && el.closest && el.closest(".prow[data-k]"); return row ? PROWS.find(x => x.k === row.dataset.k) : null; };
function opsClearOver() { clearTimeout(FDRAG.timer); FDRAG.over = ""; for (const el of plist.querySelectorAll(".droptgt")) el.classList.remove("droptgt"); }
const opsCopying = (e, dir) => e.altKey || !FDRAG.path || machineOfPath(FDRAG.path) !== machineOfPath(dir);
plist.addEventListener("dragstart", e => {
  if (S.left.tab !== "files") return;
  const r = opsRowAt(e.target), p = opsPath(r);
  if (!p) { e.preventDefault(); return; }
  FDRAG.paths = FSEL.keys.has(r.k) ? selectedPaths(r) : [p];  // (a selected row: the whole selection; another: itself)
  FDRAG.path = FDRAG.paths[0];
  e.dataTransfer.effectAllowed = "copyMove";
  e.dataTransfer.setData("application/x-orbit-path", FDRAG.paths.join("\n"));
  e.dataTransfer.setData("text/plain", FDRAG.paths.join("\n"));
  if (FDRAG.paths.length > 1) {  // the drag image: "N items"
    const ghost = document.createElement("div");
    ghost.className = "dragghost"; ghost.textContent = `${FDRAG.paths.length} items`;
    document.body.appendChild(ghost);
    e.dataTransfer.setDragImage(ghost, 10, 10);
    setTimeout(() => ghost.remove(), 0);
  }
});
plist.addEventListener("dragover", e => {
  if (S.left.tab !== "files") return;
  const types = [...(e.dataTransfer.types || [])];
  if (!types.includes("application/x-orbit-path")) FDRAG.path = "";  // (not a row of this tree: Finder's files, or nothing)
  const files = !FDRAG.path && types.includes("Files");
  if (!FDRAG.path && !files) return;
  // near the top or bottom: scroll
  const box = plist.getBoundingClientRect(), edge = 28;
  if (e.clientY < box.top + edge) plist.scrollTop -= 14; else if (e.clientY > box.bottom - edge) plist.scrollTop += 14;
  const r = opsRowAt(e.target), dir = opsFolder(r);
  if (!dir || (FDRAG.path && FDRAG.paths.some(p => dir === p || dir.startsWith(p + "/")))) { opsClearOver(); e.dataTransfer.dropEffect = "none"; return; }
  e.preventDefault();
  e.dataTransfer.dropEffect = opsCopying(e, dir) ? "copy" : "move";
  if (FDRAG.over !== r.k) {
    opsClearOver();
    FDRAG.over = r.k;
    const el = [...plist.querySelectorAll(".prow[data-k]")].find(x => x.dataset.k === r.k); if (el) el.classList.add("droptgt");
    const closed = (r.kind === "dir" || r.kind === "ldir" || r.kind === "root") && !r.open && !r.off;
    if (closed) FDRAG.timer = setTimeout(() => {  // a closed folder held over: it opens
      if (FDRAG.over !== r.k) return;
      if (r.kind === "dir") toggleDir(r.rel, true, rowRoot(r)); else toggleRoot(r, true);
      FDRAG.over = "";
    }, 600);
  }
});
plist.addEventListener("dragleave", e => { if (!plist.contains(e.relatedTarget)) opsClearOver(); });
plist.addEventListener("drop", async e => {
  if (S.left.tab !== "files") return;
  const r = opsRowAt(e.target), dir = opsFolder(r);
  const src = FDRAG.path, srcs = [...FDRAG.paths], copying = opsCopying(e, dir);
  opsClearOver(); FDRAG.path = ""; FDRAG.paths = [];
  if (!dir) return;
  e.preventDefault();
  if (src) {
    const go = copying ? srcs : srcs.filter(p => dirname(p) !== dir);  // (a move into its own folder: nothing to do)
    if (go.length) { selClear(); runTransfers(go, dir, !copying); }
    return;
  }
  const list = [...(e.dataTransfer.files || [])];
  if (list.length) uploadInto(dir, list);
});
// (a folder opened under the drag redraws the rows, the dragged one with them: its dragend may never come)
for (const t of ["dragend", "drop"]) document.addEventListener(t, () => setTimeout(() => { opsClearOver(); FDRAG.path = ""; FDRAG.paths = []; FDRAG.dragged = Date.now(); }, 0), true);
/* The mouse in the Files tab (from the panel's mousedown): a row that can be dragged is not redrawn on the press (its
   drag could not start); it opens on the click (a drag ends with none). ⌘-click and ⇧-click select. True: handled. */
function filesMouseDown(e, el) {
  const r = PROWS.find(x => x.k === el.dataset.k);
  if (!r || !opsPath(r)) { selClear(); return false; }
  if (e.metaKey || e.shiftKey) {
    e.preventDefault();
    if (e.shiftKey) { const from = FSEL.anchor || S.pcursor; if (!e.metaKey) FSEL.keys.clear(); selRange(from, r.k); FSEL.anchor = from; }
    else {
      if (FSEL.keys.has(r.k)) FSEL.keys.delete(r.k); else selAdd(r);
      FSEL.anchor = r.k;
    }
    S.pcursor = r.k; plist.focus({ preventScroll: true }); renderLeft();
    return true;
  }
  if (!FSEL.keys.has(r.k)) selClear();  // (a press on the selection keeps it: it may be dragged)
  S.pcursor = r.k; FSEL.pressed = r.k;
  for (const x of plist.querySelectorAll(".prow.kc")) x.classList.remove("kc");
  el.classList.add("kc");
  if (document.activeElement !== plist) plist.focus({ preventScroll: true });
  return true;
}
plist.addEventListener("click", e => {
  if (S.left.tab !== "files" || e.metaKey || e.shiftKey || e.button !== 0) return;
  const el = e.target.closest(".prow[data-k]"); if (!el || FSEL.pressed !== el.dataset.k) return;
  FSEL.pressed = "";
  if (Date.now() - (FDRAG.dragged || 0) < 300) return;  // (the end of a drag)
  const r = PROWS.find(x => x.k === el.dataset.k); if (!r) return;
  if (FSEL.keys.size > 1) selClear();
  byMouse = true;
  try { if (r.kind === "dir" || r.kind === "ldir" || r.kind === "root") { plist.focus(); openRow(r, { focus: false }); } else { openRow(r); renderLeft(); } }
  finally { byMouse = false; }
});
/* Files dragged in from Finder: each put in the folder (here or another machine's). A folder from Finder: copy it
   there (⌘C) and paste here (⌃Y). */
async function uploadInto(dir, list) {
  let n = 0;
  for (const f of list) {
    if (!f.size && !f.type) { toast(`${f.name}: a folder from Finder: copy it there (⌘C) and paste here (⌃Y)`); continue; }
    try {
      const r = await fetch(`/api/files/put?dir=${encodeURIComponent(dir)}&name=${encodeURIComponent(f.name)}`, { method: "POST", body: f });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
      n++;
    } catch (e) { toast(`${f.name}: ${e.message}`); }
  }
  refreshAround([dir]);
  if (n) toast(`Put ${n === 1 ? list[0].name : n + " files"} in ${tilde(dir)}`);
}
/* The menu of a multi-selection (a right-click on one of its rows). */
function selectionMenu(where) {
  const ps = selectedPaths(), n = ps.length;
  menu([
    { label: `Copy ${n} items`, key: "⌥W", run: () => filesClip(false) },
    { label: `Cut ${n} items`, key: "⌃W", run: () => filesClip(true) },
    { label: "Copy their paths", run: () => copyText(ps.join("\n")) },
    { sep: true },
    { label: `Move ${n} items to the Trash…`, key: "⌘⌫", danger: true, run: () => filesTrash() },
    { label: "Clear the selection", key: "Esc", run: () => { selClear(); renderLeft(); } },
  ], where);
}
/* Keys in the Files tab (from panelKey, the filter empty). */
function filesOpsKey(combo) {
  if (combo === "S:ArrowDown" || combo === "S:ArrowUp") {  // the selection grows from the cursor
    if (!FSEL.keys.size) { selAdd(prow()); FSEL.anchor = S.pcursor; }
    movePanel(combo === "S:ArrowDown" ? 1 : -1);
    FSEL.keys.clear(); selRange(FSEL.anchor, S.pcursor); renderLeft();
    return true;
  }
  if (combo === "Escape" && selClear()) { renderLeft(); return true; }
  if (combo === "M:Backspace" || combo === "M:Delete") { filesTrash(); return true; }
  if (["ArrowDown", "ArrowUp", "Home", "End", "PageDown", "PageUp"].includes(combo) && FSEL.keys.size) selClear();  // (a plain move: one row again)
  if (combo === "A:KeyW" || combo === "M:KeyC") { filesClip(false); return true; }
  if (combo === "C:KeyW" || combo === "M:KeyX") { filesClip(true); return true; }
  if (combo === "C:KeyY" || combo === "M:KeyV") { filesPaste(); return true; }
  return false;
}
