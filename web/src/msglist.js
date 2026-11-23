"use strict";
/* ===================================================================== a message list view */
/* What a conversation and a plugin's native thread share (builddoc/plugin-views.md §8.3): a scroll box of messages
   that is either stuck to its end (it follows every new line) or held where the user scrolled to (the message at the
   top of the view and its offset, kept per view: anchors keyed by the view and the message id, as strings). Only the
   user changes which: their own scrolling (wheel, trackpad, the scroll bar, page keys), Latest or ⌘L (the end again),
   sending (the end). A resize, a new message or a picture loading never does.
   new MsgList({ box, items, jump, itemSel, idOf(el), elOf(id), keyOf(), anchors, persist(), active(), hasItems() }) */
class MsgList {
  constructor(o) {
    Object.assign(this, { atEnd: true, until: 0, jump: null, active: () => true, hasItems: () => true }, o);
    const box = this.box;
    for (const ev of ["wheel", "touchmove"]) box.addEventListener(ev, () => { this.until = Date.now() + 500; }, { passive: true });
    box.addEventListener("mousedown", e => { if (e.target === box) this.until = Date.now() + 3600e3; });  // its scroll bar, while held
    document.addEventListener("mouseup", () => { if (this.until > Date.now() + 2000) this.until = Date.now() + 300; });
    box.addEventListener("keydown", e => { if (/^(Arrow(Up|Down)|Page(Up|Down)|Home|End| )$/.test(e.key)) this.until = Date.now() + 500; });
    box.addEventListener("scroll", () => this.onScroll());
    const keep = () => this.keepPlace();  // a resize: the end, or the same message in the same place
    new ResizeObserver(keep).observe(this.items);
    new ResizeObserver(keep).observe(box);
  }
  userScrolling() { return Date.now() < this.until; }
  atBottom() { const b = this.box; return b.scrollHeight - b.scrollTop - b.clientHeight < 4; }
  setAtEnd(v) {
    this.atEnd = v;
    const k = this.keyOf();
    if (v) { if (k != null && k in this.anchors) { delete this.anchors[k]; this.persist(); } }
    else this.saveAnchor();
    this.updateJump();
  }
  saveAnchor() {  // the first message whose bottom is in view, and how far its top is from the view's top
    const k = this.keyOf();
    if (k == null || !this.active()) return;
    const sc = rectScale(), top = this.box.getBoundingClientRect().top;
    const el = [...this.items.querySelectorAll(this.itemSel)].find(c => c.getBoundingClientRect().bottom > top + 1);
    if (!el) return;
    this.anchors[k] = { card: String(this.idOf(el)), off: Math.round((el.getBoundingClientRect().top - top) / sc) };
    const ks = Object.keys(this.anchors); if (ks.length > 300) delete this.anchors[ks[0]];
    this.persist();
  }
  anchor() { const k = this.keyOf(); return k != null && this.anchors[k] || null; }
  restoreAnchor() {
    const a = this.anchor(), el = a && this.elOf(String(a.card));
    if (!el) return false;
    this.box.scrollTop += (el.getBoundingClientRect().top - this.box.getBoundingClientRect().top) / rectScale() - a.off;
    return true;
  }
  stick() { if (this.atEnd) this.box.scrollTop = this.box.scrollHeight; this.updateJump(); }
  updateJump() { if (this.jump) this.jump.classList.toggle("show", !this.atEnd && this.hasItems() && this.active()); }
  onScroll() {
    if (!this.active()) return;
    if (this.userScrolling()) {
      const end = this.atBottom();
      if (end !== this.atEnd || !end) { this.atEnd = end; clearTimeout(this.t); this.t = setTimeout(() => this.setAtEnd(this.atEnd), 150); }
      this.updateJump();
    } else if (this.atEnd && !this.atBottom()) this.box.scrollTop = this.box.scrollHeight;  // a layout change: still at the end
  }
  keepPlace() { if (this.atEnd) this.stick(); else if (!this.userScrolling()) this.restoreAnchor(); }
  latest() { if (!this.active()) return; this.setAtEnd(true); this.box.scrollTop = this.box.scrollHeight; }
  scroll(how) {
    if (how === "end") { this.latest(); return; }
    scrollBox(this.box, how);
    this.setAtEnd(this.atBottom());
  }
}
function scrollBox(box, how) {
  const page = Math.max(40, box.clientHeight - 60);
  if (how === "home") box.scrollTop = 0;
  else if (how === "end") box.scrollTop = box.scrollHeight;
  else box.scrollTop += { up: -40, down: 40, pgup: -page, pgdn: page }[how];
}
/* Clicks every message list takes the same way: a code block's Copy, a picture → the image viewer. */
function mdClick(e) {
  const t = e.target;
  if (t.closest("[data-copy]")) { copyText(t.closest(".pre").querySelector("code").innerText); return true; }
  if (t.tagName === "IMG" && t.closest(".md")) { openLightbox(t.src); return true; }
  return false;
}
