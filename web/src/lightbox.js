"use strict";
/* ===================================================================== the image viewer (lightbox) */
/* A picture over the page, fitted to it first. Then:
   - a trackpad pinch zooms around the pointer (ctrl + wheel in browsers; WebKit's gesture events: while a gesture
     runs, its ctrl + wheel twins are ignored, so a pinch counts once); + − 0 (keys.js) zoom in, out, back to fit;
   - two fingers (the wheel) or a drag pan while zoomed; a double-click toggles fit / 2.5×;
   - a click on the dark area closes, zoomed or not; a click on the zoomed image does not (it pans); Esc closes.
   The image is placed by one transform (translate, scale; origin top left), painted at most once an animation frame
   and never by a CSS transition (one made it shake under a pinch). */
const LB = { el: $("#lightbox"), img: $("#lightbox img"), w: 0, h: 0, fit: 1, s: 1, x: 0, y: 0, raf: 0, gesture: false, g0: 1, drag: null };
const lbOn = () => LB.el.classList.contains("show");
const lbZoomed = () => LB.s > LB.fit * 1.01;
function openLightbox(src) {
  // The keyboard comes here (it may be in a side tab's frame, whose keys this page never sees); it goes back on close.
  if (!lbOn()) LB.prev = document.activeElement;
  LB.el.tabIndex = -1;
  LB.img.style.visibility = "hidden";
  LB.img.onload = () => { LB.w = LB.img.naturalWidth || 1; LB.h = LB.img.naturalHeight || 1; lbFit(); LB.img.style.visibility = ""; };
  LB.img.src = src;
  LB.el.classList.add("show");
  LB.el.focus({ preventScroll: true });
  if (LB.img.complete && LB.img.naturalWidth) LB.img.onload();
}
function closeLightbox() {
  LB.el.classList.remove("show", "zoomed"); LB.drag = null; LB.gesture = false;
  const p = LB.prev; LB.prev = null;
  if (p && p.isConnected && p !== document.body) { try { p.focus({ preventScroll: true }); } catch {} }
}
function lbView() { const k = rectScale(); return { vw: innerWidth / k, vh: innerHeight / k, k }; }
function lbFit() {
  const { vw, vh } = lbView();
  LB.fit = Math.min(0.94 * vw / LB.w, 0.94 * vh / LB.h, 1);
  LB.s = LB.fit; LB.x = (vw - LB.w * LB.s) / 2; LB.y = (vh - LB.h * LB.s) / 2;
  lbPaint();
}
/* Keep it in sight: narrower than the window, centred; wider, no gap at either side. */
function lbClamp() {
  const { vw, vh } = lbView(), w = LB.w * LB.s, h = LB.h * LB.s;
  LB.x = w <= vw ? (vw - w) / 2 : Math.min(0, Math.max(vw - w, LB.x));
  LB.y = h <= vh ? (vh - h) / 2 : Math.min(0, Math.max(vh - h, LB.y));
}
function lbPaint() {
  if (LB.raf) return;
  LB.raf = requestAnimationFrame(() => {
    LB.raf = 0;
    LB.img.style.width = LB.w + "px"; LB.img.style.height = LB.h + "px";
    LB.img.style.transform = `translate(${LB.x}px, ${LB.y}px) scale(${LB.s})`;
    LB.el.classList.toggle("zoomed", lbZoomed());
  });
}
/* Zoom to scale s around a point of the window (CSS px): that point of the picture stays under it. */
function lbZoomTo(s, px, py) {
  const { vw, vh } = lbView();
  if (px == null) { px = vw / 2; py = vh / 2; }
  s = Math.max(LB.fit, Math.min(Math.max(LB.fit, 1) * 8, s));
  LB.x = px - (px - LB.x) * s / LB.s; LB.y = py - (py - LB.y) * s / LB.s; LB.s = s;
  lbClamp(); lbPaint();
}
const lbPoint = e => { const k = rectScale(); return [e.clientX / k, e.clientY / k]; };
LB.el.addEventListener("wheel", e => {
  if (!lbOn()) return;
  e.preventDefault();
  if (e.ctrlKey) {  // a pinch (or ctrl + a wheel)
    if (LB.gesture) return;  // (WebKit: its gesture events have it)
    lbZoomTo(LB.s * Math.exp(-e.deltaY * 0.01), ...lbPoint(e));
  } else if (lbZoomed()) {
    const k = rectScale(), line = e.deltaMode === 1 ? 16 : 1;
    LB.x -= e.deltaX * line / k; LB.y -= e.deltaY * line / k; lbClamp(); lbPaint();
  }
}, { passive: false });
LB.el.addEventListener("gesturestart", e => { e.preventDefault(); LB.gesture = true; LB.g0 = LB.s; });
LB.el.addEventListener("gesturechange", e => { e.preventDefault(); lbZoomTo(LB.g0 * e.scale, ...lbPoint(e)); });
LB.el.addEventListener("gestureend", e => { e.preventDefault(); setTimeout(() => { LB.gesture = false; }, 50); });
LB.el.addEventListener("mousedown", e => {
  if (e.button !== 0) return;
  e.preventDefault();
  LB.drag = { x0: e.clientX, y0: e.clientY, x: LB.x, y: LB.y, moved: false, onImg: e.target === LB.img };
});
addEventListener("mousemove", e => {
  const d = LB.drag; if (!d || !lbOn()) return;
  if (Math.hypot(e.clientX - d.x0, e.clientY - d.y0) > 4) d.moved = true;
  if (d.moved && lbZoomed()) { const k = rectScale(); LB.x = d.x + (e.clientX - d.x0) / k; LB.y = d.y + (e.clientY - d.y0) / k; lbClamp(); lbPaint(); }
}, true);
addEventListener("mouseup", e => {
  const d = LB.drag; LB.drag = null; if (!d || !lbOn() || e.button !== 0) return;
  if (d.moved) return;  // (a pan, not a click)
  if (!d.onImg || !lbZoomed()) { clearTimeout(LB.closing); LB.closing = setTimeout(closeLightbox, 220); }  // (a double-click may follow)
}, true);
LB.el.addEventListener("dblclick", e => {
  e.preventDefault(); clearTimeout(LB.closing);
  if (lbZoomed()) lbFit(); else lbZoomTo(LB.fit * 2.5, ...lbPoint(e));
});
addEventListener("resize", () => { if (!lbOn() || !LB.w) return; if (lbZoomed()) { lbClamp(); lbPaint(); } else lbFit(); });
/* Its keys (keys.js, before anything else): Esc closes; + − 0 zoom. Every key stops here while it shows. */
function lightboxKey(combo) {
  if (combo === "Escape") closeLightbox();
  else if (combo === "Equal" || combo === "S:Equal" || combo === "NumpadAdd" || combo === "M:Equal") lbZoomTo(LB.s * 1.25);
  else if (combo === "Minus" || combo === "NumpadSubtract" || combo === "M:Minus") lbZoomTo(LB.s / 1.25);
  else if (combo === "Digit0" || combo === "Numpad0" || combo === "M:Digit0") lbFit();
}
/* In the app (a WebView), window.open does nothing: a picture opened so (a view's own script) shows here, anything
   else goes to the system. */
if (window.__ORBIT_APP__ || (() => { try { return window.parent !== window && window.parent.__ORBIT_APP__; } catch { return false; } })()) {
  const IMG_URL = /^data:image\/|\.(png|jpe?g|gif|webp|svg|bmp|heic|avif)(?:[?#]|$)|^\/api\/raw\?path=[^&]*\.(png|jpe?g|gif|webp|svg|bmp|heic|avif)/i;
  window.open = (url, ...rest) => {
    const u = String(url || "");
    if (IMG_URL.test(u) || IMG_URL.test(decodeURIComponent(u))) { openLightbox(u); return null; }
    if (u) openUrl(new URL(u, location.href).href);
    return null;
  };
}
/* A picture in a view (a plugin's thread or doc, a group's home, the calendar): a click shows it here, unless it is a
   link's or a button's (a message list's pictures: msglist.js mdClick, the same). */
document.addEventListener("click", e => {
  const t = e.target;
  if (t.tagName !== "IMG" || lbOn() || t.closest("a, button, .ej, #lightbox, .tav, .gicon, .lk-prev")) return;
  if (!t.closest("#xview, .pview, .pthread, .pdoc, .hw .wb, .md")) return;
  if (t.closest(".md") && t.closest("#cards, .msgs")) return;  // (taken there)
  e.preventDefault();
  openLightbox(t.currentSrc || t.src);
});
