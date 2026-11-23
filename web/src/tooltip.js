"use strict";
/* ===================================================================== tooltips for cut-off text */
/* A name cut off by its box (a long path in the tree, a tab, the tree head) shows its whole text in a small box: on
   hover (after a short pause) and, in the left panel, on the row the keyboard is on. Only when the text is really cut
   off; the box takes the element's data-tip, else its row's title, else its own text. Finder rows (⌘O …) too: on
   hover, and on the selected row once the keys rest. */
const TIP_SEL = ".prow .nm, .prow .pv, .tab .name, .thead .tp, .stab .nm, .frow .fl, .frow .fd";
let tipEl = null, tipTimer = 0, tipFor = null, tipPanel = false;  // tipPanel: shown for the panel's keyboard row
function tipText(el) {
  const host = el.closest("[data-tip]") || el.closest("[title]");
  return (host && (host.dataset.tip || host.getAttribute("title"))) || el.textContent;
}
const cutOff = el => el.scrollWidth > el.clientWidth + 1;
function showTip(el, beside = null) {
  // a session row's tip is its preview (last answer, model, folder): shown whether or not the name is cut off
  if (!el || !document.contains(el) || !cutOff(el)) return hideTip();
  if (!tipEl) { tipEl = document.createElement("div"); tipEl.id = "tip"; tipEl.setAttribute("role", "tooltip"); document.body.appendChild(tipEl); }
  tipFor = el;
  tipEl.textContent = tipText(el);
  tipEl.style.visibility = "hidden"; tipEl.classList.add("show");
  const r = el.getBoundingClientRect(), k = rectScale(), w = tipEl.offsetWidth, h = tipEl.offsetHeight;
  const vw = innerWidth / k, vh = innerHeight / k;
  let x = r.left / k, y = r.bottom / k + 4;
  if (beside) { x = beside.getBoundingClientRect().right / k + 8; y = r.top / k - 4; if (y + h > vh - 4) y = vh - h - 4; }
  else if (y + h > vh - 4) y = r.top / k - h - 4;  // no room below: above
  x = Math.max(4, Math.min(x, vw - w - 4));
  tipEl.style.left = x + "px"; tipEl.style.top = y + "px"; tipEl.style.visibility = "";
}
function hideTip() { clearTimeout(tipTimer); tipFor = null; tipPanel = false; if (tipEl) tipEl.classList.remove("show"); }
document.addEventListener("mouseover", e => {
  const el = e.target.closest && e.target.closest(TIP_SEL);
  if (el === tipFor) return;
  hideTip();
  const always = false;  // (a session's details: the strip at the panel's foot, U15)
  if (el && cutOff(el)) {
    const host = el.closest("[title]");  // ours replaces the system's (slow, unstyled) tooltip
    if (host && !host.dataset.tip) { host.dataset.tip = host.getAttribute("title"); host.removeAttribute("title"); }
    tipTimer = setTimeout(() => showTip(el), always ? 600 : 350);
  }
});
document.addEventListener("mouseout", e => { if (tipFor && !(e.relatedTarget && tipFor.contains(e.relatedTarget))) hideTip(); });
addEventListener("scroll", hideTip, true);
addEventListener("blur", hideTip);
document.addEventListener("mousedown", hideTip, true);
/* The left panel's keyboard row: its whole name when it is cut off (keys move the row, so no hover). */
let tipPanelTimer = 0;
function tipPanelRow() {
  clearTimeout(tipPanelTimer);
  if (tipPanel) hideTip();  // (moving on: the last row's tip goes at once; the new one waits for the keys to rest)
  tipPanelTimer = setTimeout(() => {
    if (typeof modal !== "undefined" && modal) return;
    const row = panelFocused() && plist.querySelector(".prow.kc");
    const nm = row && row.querySelector(".nm");
    if (nm && cutOff(nm)) { showTip(nm, $("#left")); tipPanel = true; }
  }, 600);
}
/* The finder's selected row: its whole path (or name) when cut off, once the keys rest (not on every keystroke). */
let tipFinderTimer = 0;
function tipFinderRow(list) {
  clearTimeout(tipFinderTimer);
  if (tipFor && tipFor.closest(".frow")) hideTip();
  tipFinderTimer = setTimeout(() => {
    const row = document.contains(list) && list.querySelector(".frow.sel");
    const el = row && [...row.querySelectorAll(".fd, .fl")].find(cutOff);
    if (el) showTip(el);
  }, 500);
}
