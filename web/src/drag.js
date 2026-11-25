"use strict";
/* ===================================================================== drag to reorder (the tab row, the left panel) */
/* As a browser's tab strip does it: the dragged item follows the pointer, and its neighbours slide aside (animated)
   to open the place it would land in; on release it settles there and the new order is saved. Esc puts it back.
   Pointer-based (WKWebView's HTML drag and drop is not reliable).

   A press that moves 5px becomes a drag. The press itself still does what it did (a tab or row is selected on
   mousedown, which may redraw the list), so the dragged item is found again by its key. While dragging, the list is
   not redrawn (DRAG.active: renderTabs / renderLeft wait, and draw once it ends).

   Places are computed in layout coordinates (offsetLeft/Top, which transforms do not move): the slide animations
   never feed back into where the item lands. The box is the items' offsetParent (position: relative).

   o: {
     box, item (selector), axis: "x" (a row that may wrap) | "y",
     keyOf(el), byKey(key),        the dragged item, found again by its key
     canDrag(el),
     unit(el) -> [els],            what moves together (a row; a group's head with its rows), contiguous in the DOM
     peers(el) -> [[els], …],      the units it can be placed among, in DOM order (not itself)
     drop(key, prevEl, nextEl),    where it landed: the first element of the peer unit before / after it (or null)
     redraw()                      draws the list again (after a cancelled drag)
   } */
const DRAG = { active: false, pending: new Set() };
function dragSort(o) {
  o.box.addEventListener("mousedown", e => {
    if (e.button !== 0 || e.target.closest("[data-close], [data-closefile], .fold, [data-resume], button.x, .rsm")) return;
    const first = e.target.closest(o.item);
    if (!first || (o.canDrag && !o.canDrag(first))) return;  // (it may be detached already: the press redrew the list)
    const key = o.keyOf(first), x0 = e.clientX, y0 = e.clientY;
    if (!e.target.closest("input, textarea, [contenteditable]")) e.preventDefault();  // (a press that may become a drag starts no text selection)
    let on = false, unit, peers, grab, k = 1, slot = -1, t = { x: 0, y: 0 };
    const local = ev => {  // the pointer in the box's layout coordinates (CSS px)
      const b = o.box.getBoundingClientRect();
      return { x: (ev.clientX - b.left) / k + o.box.scrollLeft, y: (ev.clientY - b.top) / k + o.box.scrollTop };
    };
    const span = els => { const a = els[0], z = els[els.length - 1]; return { left: a.offsetLeft, top: a.offsetTop, w: a.offsetWidth, h: z.offsetTop + z.offsetHeight - a.offsetTop }; };
    const start = ev => {
      const src = o.byKey(key); if (!src) return false;
      unit = o.unit(src); peers = o.peers(src);
      if (!unit.length || !peers.length) return false;
      on = true; DRAG.active = true; k = rectScale();
      const sel = getSelection(); if (sel && sel.rangeCount) sel.removeAllRanges();  // (nothing stays selected under it)
      document.body.classList.add("dragsort"); if (typeof hideTip === "function") hideTip();
      unit.forEach(el => el.classList.add("dragging"));
      peers.flat().forEach(el => el.classList.add("dragpeer"));
      const L = local({ clientX: x0, clientY: y0 }), u = span(unit);
      grab = { x: L.x - u.left, y: L.y - u.top };  // where in the item the pointer holds it
      return true;
    };
    const follow = ev => {
      const L = local(ev), u = span(unit);
      t = { x: o.axis === "y" ? 0 : L.x - grab.x - u.left, y: L.y - grab.y - u.top };
      if (o.axis === "x" && !o.box.classList.contains("wrapped")) t.y = 0;  // a single row: along it only
      unit.forEach(el => { el.style.transform = `translate(${t.x}px, ${t.y}px)`; });
    };
    // The place asked for: before the first peer the dragged item's centre is ahead of (reading order), else the end.
    const wanted = ev => {
      const L = local(ev), u = span(unit), c = { x: L.x - grab.x + u.w / 2, y: L.y - grab.y + u.h / 2 };
      for (let i = 0; i < peers.length; i++) {
        const p = span(peers[i]);
        if (o.axis === "y") { if (c.y < p.top + p.h / 2) return i; }
        else if (c.y < p.top || (c.y < p.top + p.h && c.x < p.left + p.w / 2)) return i;
      }
      return peers.length;
    };
    // Move the unit to slot i in the DOM; every peer slides from where it was seen to its new place (FLIP).
    const place = i => {
      const els = peers.flat(), seen = new Map(els.map(el => [el, el.getBoundingClientRect()]));
      const parent = unit[0].parentNode, ref = i < peers.length ? peers[i][0] : peers[peers.length - 1][peers[peers.length - 1].length - 1].nextSibling;
      unit.forEach(el => parent.insertBefore(el, ref));
      for (const el of els) {
        el.style.transition = "none"; el.style.transform = "";
        const a = seen.get(el), b = el.getBoundingClientRect(), dx = (a.left - b.left) / k, dy = (a.top - b.top) / k;
        if (Math.abs(dx) < .5 && Math.abs(dy) < .5) { el.style.transition = ""; continue; }
        el.style.transform = `translate(${dx}px, ${dy}px)`;
        el.offsetWidth;  // (from where it was…)
        el.style.transition = ""; el.style.transform = "";  // (…to its place, by the CSS transition)
      }
    };
    const move = ev => {
      if (!on && (Math.hypot(ev.clientX - x0, ev.clientY - y0) < 5 || !start(ev))) return;
      ev.preventDefault();  // (the pointer passing over text selects none of it)
      follow(ev);
      const i = wanted(ev);
      if (i !== slot) { slot = i; place(i); follow(ev); }
    };
    const finish = drop => {
      document.removeEventListener("mousemove", move, true); document.removeEventListener("mouseup", up, true); removeEventListener("keydown", esc, true);
      if (!on) return;
      const swallow = ev => { ev.stopPropagation(); ev.preventDefault(); };  // the click a drag ends with does nothing
      document.addEventListener("click", swallow, true); setTimeout(() => document.removeEventListener("click", swallow, true), 0);
      unit.forEach(el => { el.classList.add("settling"); el.style.transform = ""; });  // it settles into its place
      const owner = el => el && peers.find(p => p.includes(el));
      const pu = owner(unit[0].previousElementSibling), nu = owner(unit[unit.length - 1].nextElementSibling);
      setTimeout(() => {
        unit.forEach(el => el.classList.remove("dragging", "settling"));
        peers.flat().forEach(el => { el.classList.remove("dragpeer"); el.style.transform = ""; el.style.transition = ""; });
        document.body.classList.remove("dragsort");
        DRAG.active = false;
        if (drop) o.drop(key, pu ? pu[0] : null, nu ? nu[0] : null);
        else DRAG.pending.add(o.redraw);
        const redraw = [...DRAG.pending]; DRAG.pending.clear(); redraw.forEach(f => f());
      }, 170);
    };
    const up = () => finish(true);
    const esc = ev => { if (ev.key === "Escape") { ev.stopImmediatePropagation(); ev.preventDefault(); finish(false); } };
    document.addEventListener("mousemove", move, true); document.addEventListener("mouseup", up, true); addEventListener("keydown", esc, true);  // (on the window: before the app's own keys)
  });
}
