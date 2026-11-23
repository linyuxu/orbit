"use strict";
/* ===================================================================== ⌘F: find in what has the keyboard */
const FIND = { open: false, target: null, ranges: [], i: -1, lastQ: null, back: null };
const findq = $("#findq");
const hasHighlights = () => !!(window.CSS && CSS.highlights && window.Highlight);
function findTarget() {
  if (isPluginTab(S.file)) { const v = pviewShown(); return v && v.root ? { kind: "dom", root: v.root, box: v.box, list: v.list } : null; }  // a plugin's view
  if (S.file) { const ed = EDITORS.get(S.file); return ed && ed.ed ? { kind: "editor", ed: ed.ed } : null; }
  if (EMBED_REVIEW) return { kind: "dom", root: $("#rvbody"), box: $("#rvbody") };
  if (xOn()) return { kind: "dom", root: $("#xbody"), box: $("#xbody") };
  return { kind: "dom", root: $("#cards"), box: conv, list: CONVL };
}
function openFind(q) {
  if (panelFocused()) { toast("Type to filter this list"); return; }
  const t = findTarget();
  if (!t) return;
  if (!FIND.open) FIND.back = document.activeElement;
  FIND.open = true; FIND.target = t; FIND.lastQ = null;
  if (t.root === $("#cards") && typeof showAllCards === "function") showAllCards();  // ⌘F finds in every card
  const bar = $("#findbar");
  bar.hidden = false;
  bar.classList.toggle("file", !!S.file && !isPluginTab(S.file));
  const sel = window.getSelection && String(window.getSelection() || "").trim();
  if (q != null) findq.value = q;
  else if (sel && sel.length < 80 && !sel.includes("\n")) findq.value = sel;
  else if (!findq.value) findq.value = LS.get("findq", "");
  findq.focus(); findq.select();
  findStep(0);
}
function closeFind(restore = true) {
  if (!FIND.open) return;
  FIND.open = false;
  $("#findbar").hidden = true;
  if (FIND.target && FIND.target.kind === "editor") { FIND.target.ed.clearFind(); if (restore) { FIND.target.ed.focus(); return; } }
  if (hasHighlights()) { CSS.highlights.delete("orbit-find"); CSS.highlights.delete("orbit-find-cur"); }
  FIND.ranges = [];
  if (restore) { const b = FIND.back; if (b && document.contains(b) && b !== findq) b.focus({ preventScroll: true }); else focusMain(); }
}
function domRanges(root, q) {
  const out = [];
  if (!q) return out;
  const lq = q.toLowerCase();
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: n => n.parentElement.closest("button, .meta, textarea") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT });
  for (let n; (n = walker.nextNode());) {
    const t = n.nodeValue.toLowerCase();
    for (let i = t.indexOf(lq); i >= 0 && out.length < 5000; i = t.indexOf(lq, i + lq.length)) {
      const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + lq.length); out.push(r);
    }
  }
  return out;
}
function paintFind(scroll) {
  const t = FIND.target, rs = FIND.ranges, cur = rs[FIND.i];
  if (hasHighlights()) {
    CSS.highlights.set("orbit-find", new Highlight(...rs));
    if (cur) CSS.highlights.set("orbit-find-cur", new Highlight(cur)); else CSS.highlights.delete("orbit-find-cur");
  } else if (cur && scroll) { const sel = getSelection(); sel.removeAllRanges(); sel.addRange(cur); }
  if (cur && scroll) {
    const k = rectScale(), r = cur.getBoundingClientRect(), b = t.box.getBoundingClientRect();
    t.box.scrollTop += ((r.top - b.top) - b.height / 2) / k;
    if (t.list) t.list.setAtEnd(t.list.atBottom());
  }
  $("#findn").textContent = rs.length ? `${FIND.i + 1}/${rs.length}` : findq.value ? "no match" : "";
}
/* dir 0: as you type (the first match from the top of the view); 1 / -1: next / previous, wrapping. */
function findStep(dir) {
  if (!FIND.open) return;
  const q = findq.value;
  if (!EMBED) LS.set("findq", q);
  const t = FIND.target;
  if (t.kind === "editor") {
    const r = t.ed.find(q, dir);
    $("#findn").textContent = r.count ? `${r.index + 1}/${r.count}` : q ? "no match" : "";
    return;
  }
  if (dir === 0 || FIND.lastQ !== q) {
    FIND.ranges = domRanges(t.root, q); FIND.lastQ = q;
    const top = t.box.getBoundingClientRect().top;
    FIND.i = FIND.ranges.length ? Math.max(0, FIND.ranges.findIndex(r => r.getBoundingClientRect().top >= top)) : -1;
    if (dir !== 0 && FIND.ranges.length) FIND.i = (FIND.i + (dir > 0 ? 0 : -1) + FIND.ranges.length) % FIND.ranges.length;
  } else if (FIND.ranges.length) FIND.i = (FIND.i + dir + FIND.ranges.length) % FIND.ranges.length;
  paintFind(true);
}
/* The conversation changed under an open find bar: recount and repaint, without scrolling. */
function refreshFind() {
  if (!FIND.open || !FIND.target || FIND.target.kind !== "dom") return;
  FIND.ranges = domRanges(FIND.target.root, findq.value);
  FIND.i = Math.min(Math.max(FIND.i, 0), FIND.ranges.length - 1);
  paintFind(false);
}
function findNext(dir) { if (FIND.open) findStep(dir); else { openFind(LS.get("findq", "") || null); } }
findq.addEventListener("input", () => findStep(0));
$("#findbar").addEventListener("mousedown", e => { const b = e.target.closest("[data-f]"); if (!b) return; e.preventDefault(); if (b.dataset.f === "close") closeFind(); else findStep(+b.dataset.f); });
