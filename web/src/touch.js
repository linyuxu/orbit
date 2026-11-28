"use strict";
/* ===================================================================== touch: the full page on an iPad
   The page is made for a mouse and keys. A finger on iOS sends touch events and, for a tap only, the mouse events and
   a click; nothing for a finger that holds or moves. Here the gestures do what the mouse does (the Mac, with no touch
   events, never runs any of it):
   - a long press is a right-click: the menu where one is (lists, tabs, side tabs, ⌘O rows, a home's widgets);
     held, then moved: a press-and-move, so a tab or a row is dragged to its place as with the mouse;
   - on a handle (the panel splitters, a widget's header and its resize corners) a finger drags at once;
   - a second tap soon after the first, near it, is a double-click: the events a mouse sends (a click with detail 2,
     then a dblclick), so a file opens, a preview is kept, a widget's note is edited;
   - the page follows the visual viewport: the on-screen keyboard never covers the input (as the phone's, m.js);
   - narrower than OVER px (an upright iPad): the left panel floats over the work, with a scrim; a tap on the scrim, or a
     session or file opened from it, puts it away.
   touch.css has the rest: bigger targets, what a hover reveals always shown, 16 px text fields (no zoom on focus). */
const TOUCH = { HOLD_MS: 480, SLOP: 10, DOUBLE_MS: 350, OVER: 1100, st: null, swallow: 0, tapAt: 0, prev: null, menuAt: 0 };
const T_HANDLES = "#lsplit, #ssplit, #hsplit, .hw > header, .hrz, [data-touch-drag]";
const T_HOLD = "#left, #tabs, #sidetabs, .finder .flist, .menu, #xview, [data-touch-hold]";
const T_NATIVE = "input, textarea, select, [contenteditable], .cm-editor";

function tMouse(type, p, target, extra = {}) {  // a mouse event where the finger is (CSS px of the viewport, as a mouse's)
  const el = target || document.elementFromPoint(p.clientX, p.clientY) || document.body;
  const ev = new MouseEvent(type, { bubbles: true, cancelable: true, view: window, clientX: p.clientX, clientY: p.clientY,
    screenX: p.screenX, screenY: p.screenY, button: type === "contextmenu" ? 2 : 0, buttons: type === "mouseup" ? 0 : 1, ...extra });
  el.dispatchEvent(ev);
  return ev;
}
const tFar = (t, s) => Math.hypot(t.clientX - s.x0, t.clientY - s.y0) > TOUCH.SLOP;

if ("ontouchstart" in window && !EMBED) {
  /* A finger's moves and its lift go to the element it touched, even once a redraw took that out of the page (a press
     redraws a list): they are listened to there, not on the window (a detached element's events reach no window). */
  const onMove = e => {
    const st = TOUCH.st; if (!st) return;
    const t = e.touches[0]; if (!t) return;
    if (st.mode === "wait") { if (tFar(t, st)) { clearTimeout(st.timer); end(); } return; }  // (a scroll)
    if (st.mode === "held" && tFar(t, st)) {  // held, then moved: the mouse's press-and-move (a drag to reorder)
      st.mode = "drag";
      tMouse("mousedown", st.first, st.target);
    }
    if (st.mode === "drag") { e.preventDefault(); tMouse("mousemove", t, document.elementFromPoint(t.clientX, t.clientY)); }
  };
  const onEnd = e => {
    const st = TOUCH.st; end();
    if (!st) return;
    clearTimeout(st.timer);
    const t = (e.changedTouches && e.changedTouches[0]) || st.first;
    if (st.mode === "drag") { e.preventDefault(); tMouse("mouseup", t, document.elementFromPoint(t.clientX, t.clientY)); return; }
    if (st.mode === "held") {  // a long press, not moved: the right-click's menu (unless the system sent one itself)
      e.preventDefault();
      if (performance.now() - TOUCH.menuAt > 1000) { TOUCH.menuAt = performance.now(); tMouse("contextmenu", st.first, st.target); }
      TOUCH.swallow = performance.now() + 450;
      return;
    }
    if (e.type === "touchend") TOUCH.tapAt = performance.now();  // a tap: its click comes next (iOS), a double tap is told there
  };
  const end = () => {
    const st = TOUCH.st; TOUCH.st = null;
    if (st) for (const [k, f] of [["touchmove", onMove], ["touchend", onEnd], ["touchcancel", onEnd]]) st.target.removeEventListener(k, f, true);
  };
  addEventListener("touchstart", e => {
    const s0 = TOUCH.st; if (s0) { clearTimeout(s0.timer); end(); }
    if (e.touches.length !== 1) return;
    const t = e.touches[0], target = e.target;
    if (target.closest(T_NATIVE)) return;
    const base = { x0: t.clientX, y0: t.clientY, target, first: t };
    const listen = () => { for (const [k, f] of [["touchmove", onMove], ["touchend", onEnd], ["touchcancel", onEnd]]) target.addEventListener(k, f, { capture: true, passive: false }); };
    if (target.closest(T_HANDLES) && !target.closest("button, a, .ibtn")) {  // a handle: drags at once, no scroll
      e.preventDefault();
      TOUCH.st = { ...base, mode: "drag" }; listen();
      tMouse("mousedown", t, target);
      return;
    }
    if (!target.closest(T_HOLD)) { TOUCH.tapAt = performance.now(); return; }
    const st = TOUCH.st = { ...base, mode: "wait" }; listen();
    st.timer = setTimeout(() => { if (TOUCH.st === st && st.mode === "wait") st.mode = "held"; }, TOUCH.HOLD_MS);
  }, { capture: true, passive: false });
  addEventListener("touchend", () => { if (!TOUCH.st) TOUCH.tapAt = performance.now(); }, true);  // (a tap elsewhere: its click may double)

  addEventListener("contextmenu", e => {  // (one menu per long press: the system's, if it sends one, or ours)
    if (!e.isTrusted) return;
    if (performance.now() - TOUCH.menuAt < 1000) { e.preventDefault(); e.stopImmediatePropagation(); return; }
    if (TOUCH.st) { TOUCH.menuAt = performance.now(); TOUCH.st.mode = "done"; }
  }, true);

  /* A double tap on what acts on the press (the left panel opens a row on mousedown and tells a double-click by its
     count, e.detail): the second tap's mousedown with a count of 2, as a mouse's, when the system sends it with 1. */
  addEventListener("mousedown", e => {
    if (!e.isTrusted || performance.now() - TOUCH.tapAt > 600) return;  // (a trackpad's or a mouse's: as it is)
    const now = performance.now(), p = TOUCH.prevDown;
    TOUCH.prevDown = { t: now, x: e.clientX, y: e.clientY };
    if (!(p && now - p.t < TOUCH.DOUBLE_MS && Math.hypot(e.clientX - p.x, e.clientY - p.y) < 24)) return;
    TOUCH.prevDown = null;
    TOUCH.dblDown = now;  // (its dblclick: after the lift, if no click brought one: a press that redraws loses the clicks)
    if (e.detail < 2) { e.preventDefault(); e.stopImmediatePropagation(); tMouse("mousedown", e, e.target, { detail: 2 }); }
  }, true);
  addEventListener("mouseup", e => {
    if (!e.isTrusted || !TOUCH.dblDown || performance.now() - TOUCH.dblDown > 600) return;
    const at = TOUCH.dblDown; TOUCH.dblDown = 0;
    setTimeout(() => {
      if ((TOUCH.dblAt || 0) >= at) return;  // (the clicks made it: sent there)
      TOUCH.dblAt = performance.now();
      tMouse("dblclick", e, null, { detail: 2 });
    }, 0);
  }, true);
  addEventListener("dblclick", e => {  // (the browser's own, after the one sent above: once)
    if (e.isTrusted && performance.now() - (TOUCH.dblAt || 0) < 600) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);
  addEventListener("click", e => {
    if (!e.isTrusted) return;
    const now = performance.now();
    if (now < TOUCH.swallow) { e.preventDefault(); e.stopImmediatePropagation(); TOUCH.swallow = 0; return; }  // (after a menu)
    if (now - TOUCH.tapAt > 600) return;  // a click from a trackpad or a mouse: as it is
    const p = TOUCH.prev;
    if (p && now - p.t < TOUCH.DOUBLE_MS && Math.hypot(e.clientX - p.x, e.clientY - p.y) < 24) {
      e.preventDefault(); e.stopImmediatePropagation();  // the second tap: the mouse's second click and its dblclick
      TOUCH.prev = null;
      tMouse("click", e, e.target, { detail: 2 });
      tMouse("dblclick", e, e.target, { detail: 2 });
      TOUCH.dblAt = now;
      return;
    }
    TOUCH.prev = { t: now, x: e.clientX, y: e.clientY };
  }, true);

  /* The on-screen keyboard: iOS moves the visual viewport, not the layout; the app follows it (touch.css #app). */
  const fitViewport = () => {
    const vv = window.visualViewport; if (!vv) return;
    const r = document.documentElement.style;
    r.setProperty("--vvh", vv.height + "px");
    r.setProperty("--vvtop", vv.offsetTop + "px");
    document.body.classList.toggle("kb", innerHeight - vv.height > 120);
    if (window.scrollY) window.scrollTo(0, 0);
  };
  if (window.visualViewport) { visualViewport.addEventListener("resize", fitViewport); visualViewport.addEventListener("scroll", fitViewport); }

  /* Upright: the left panel floats over the work (touch.css body.tnarrow); the scrim, or what it opened, puts it away. */
  const narrow = () => matchMedia(`(max-width: ${TOUCH.OVER}px)`).matches;
  const fitWidth = () => { document.body.classList.toggle("tnarrow", narrow()); fitViewport(); };
  addEventListener("resize", fitWidth);
  document.body.classList.add("touch");
  fitWidth();
  const scrim = document.createElement("div");
  scrim.id = "lscrim";
  $("#left").after(scrim);
  const putAway = () => { if (S.left.shown) { S.left.shown = false; applyLeft(); saveUi(); } };
  scrim.addEventListener("click", putAway);
  $("#left").addEventListener("click", e => {
    if (!document.body.classList.contains("tnarrow")) return;
    const r = e.target.closest(".prow");
    if (r && r.matches(".ss, .ftab, .ft:not(.dir):not(.froot):not(.fgoto):not(.fnote)") && !e.target.closest(".fold, .rsm, button"))
      setTimeout(putAway, 0);  // (after the row's own click opened it)
  });
}
