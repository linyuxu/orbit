"use strict";
/* ===================================================================== the tab bar: four areas, each its own stack
   Sessions, Files, Notes, Misc (iOS's tab bar): one tap switches; each area keeps its pages (a drill-down stack) and
   their scroll, also while another area is on screen (hidden, not removed). A page pushed slides in from the right
   over the one under it; back (its chevron, a swipe from the left edge) slides it off. Pages sit above the tab bar;
   the conversation and the editors ("cover" pages) slide over it. A tap on the area already shown goes to its root
   (at its root: to the top), as in iOS. The bar hides while the keyboard is up. */
const TABS = [
  { id: "sessions", label: "Sessions", icon: "chat" },
  { id: "files", label: "Files", icon: "folder" },
  { id: "notes", label: "Notes", icon: "note" },
  { id: "misc", label: "Misc", icon: "grid" },
];
const TAB_ROOT = {};  // tab → a function that pushes its root page (m-files.js, m-notes.js, m-misc.js)
const AREA = { cur: "sessions", stacks: { sessions: [], files: [], notes: [], misc: [] } };
const areaEl = tab => $("#a-" + tab);
const topPage = (tab = AREA.cur) => { const st = AREA.stacks[tab]; return st[st.length - 1]; };
/* The Sessions list is the first area's root (index.html); its pages are the conversation (#vconv, m-conv.js). */
AREA.stacks.sessions.push({ el: $("#vlist"), scroll: $("#lscroll"), root: true, onShow: () => { if (!S.current) renderList(); } });

/* ===================================================================== a page: nav bar (back · title · buttons) and a scroller
   A root page (or a "large" one: a meeting) has the big title in its scroller and the small one in the bar once it
   scrolls under (the Sessions list's). */
const BACK_SVG = '<svg class="i" width="22" height="22" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M10 2.5L4.5 8l5.5 5.5"/></svg>';
const MORE_SVG = '<svg class="i" width="22" height="22" viewBox="0 0 16 16" fill="currentColor"><circle cx="3.2" cy="8" r="1.15"/><circle cx="8" cy="8" r="1.15"/><circle cx="12.8" cy="8" r="1.15"/></svg>';
const PLUS_SVG = '<svg class="i" width="22" height="22" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M8 3v10M3 8h10"/></svg>';
const navBtn = (act, svg, label) => `<button class="nbtn" data-nav="${act}" aria-label="${esc(label)}">${svg}</button>`;
const navTxt = (act, word, strong) => `<button class="nbtn txt${strong ? " strong" : ""}" data-nav="${act}">${esc(word)}</button>`;
function makePage({ title = "", sub = "", root = false, large = root, cover = false, left = null, right = "", cls = "" } = {}) {
  const el = document.createElement("section");
  el.className = "view page" + (root ? " root" : "") + (large ? " large" : "") + (cover ? " cover" : "") + (cls ? " " + cls : "");
  el.innerHTML = `<header class="nav pnav"><div class="nside l">${left != null ? left : root ? "" : `<button class="nbtn back" data-nav="back" aria-label="Back">${BACK_SVG}</button>`}</div>` +
    `<div class="ntitle"><span class="pt"></span><span class="ps"></span></div><div class="nside r">${right}</div></header>` +
    `<div class="offline-bar">Reconnecting to Orbit…</div>` +
    `<div class="scroll"><div class="inner">${large ? `<h1 class="bigtitle"></h1>` : ""}<div class="pbody2"></div></div></div>`;
  const page = { el, root, cover, nav: $(".nav", el), scroll: $(".scroll", el), body: $(".pbody2", el), acts: {} };
  page.setTitle = (t, s = "") => {
    $(".pt", el).textContent = t; $(".ps", el).textContent = s; $(".ps", el).hidden = !s;
    const big = $(".bigtitle", el); if (big) big.textContent = t;
  };
  page.setTitle(title, sub);
  page.scroll.addEventListener("scroll", () => {
    const big = $(".bigtitle", el);
    page.nav.classList.toggle("scrolled", big ? page.scroll.scrollTop > big.offsetTop + big.offsetHeight - 8 : page.scroll.scrollTop > 2);
  }, { passive: true });
  page.nav.addEventListener("click", e => {
    const b = e.target.closest("[data-nav]"); if (!b || b.disabled) return;
    if (b.dataset.nav === "back") goBackPage(page); else if (page.acts[b.dataset.nav]) page.acts[b.dataset.nav](b);
  });
  return page;
}
const loadingHtml = () => `<div class="loading">${markSvg("run", 18)}</div>`;
const emptyBox = (h, text) => `<div class="empty"><h2>${esc(h)}</h2>${text}</div>`;

/* ===================================================================== the stacks */
function pushPage(page, tab = AREA.cur) {
  const st = AREA.stacks[tab], under = st[st.length - 1];
  page.tab = tab;
  areaEl(tab).appendChild(page.el);
  st.push(page);
  if (under) {
    page.el.classList.add("pushed");
    void page.el.offsetWidth;  // (its start, off to the right, drawn: the slide runs)
    page.el.classList.add("in");
    under.el.classList.add("under");
    if (document.activeElement && under.el.contains(document.activeElement)) document.activeElement.blur();
  }
  return page;
}
function popPage(tab = AREA.cur) {
  const st = AREA.stacks[tab]; if (st.length < 2) return;
  const page = st.pop(), under = st[st.length - 1];
  if (document.activeElement && page.el.contains(document.activeElement)) document.activeElement.blur();
  page.el.classList.remove("in"); under.el.classList.remove("under");
  setTimeout(() => page.el.remove(), 380);
  if (page.onClose) page.onClose();
  if (under.onShow) under.onShow();
}
/* Back from a page: its own say first (an editor with changes asks). */
function goBackPage(page = topPage()) {
  if (page !== topPage(page.tab)) return;
  if (page.back) page.back(); else popPage(page.tab);
}
function toRoot(tab) {
  const st = AREA.stacks[tab];
  if (tab === "sessions" && S.current) { goBack(); return; }
  if (st.length > 1) {
    if (st.some(p => p.dirty && p.dirty())) { toast("Save or cancel the edit first"); return; }
    while (st.length > 2) { const p = st.splice(1, 1)[0]; p.el.remove(); if (p.onClose) p.onClose(); }
    st[0].el.classList.remove("under");
    popPage(tab);
    return;
  }
  if (st[0]) st[0].scroll.scrollTop = 0;
}
function switchTab(id, { tap = false } = {}) {
  if (id === AREA.cur) { if (tap) toRoot(id); return; }
  const was = AREA.cur;
  if (document.activeElement && areaEl(was).contains(document.activeElement)) document.activeElement.blur();
  AREA.cur = id; LS.set("tab", id);
  for (const t of TABS) {
    const a = areaEl(t.id), on = t.id === id;
    a.classList.toggle("on", on);
    if (on) a.removeAttribute("inert"); else a.setAttribute("inert", "");
  }
  if (!AREA.stacks[id].length) TAB_ROOT[id]();
  else { const p = topPage(id); if (p.onShow) p.onShow(); }
  renderTabbar();
}
/* The bar: an icon over its label; the current area tinted; Sessions counts the answers not seen yet. */
function renderTabbar() {
  const unread = S.loaded && S.seen ? [...S.sessions.values()].filter(s => !s.fleet && isUnread(s)).length : 0;
  $("#tabbar").innerHTML = TABS.map(t => `<button class="tab${t.id === AREA.cur ? " on" : ""}" role="tab" data-tab="${t.id}" aria-selected="${t.id === AREA.cur}">` +
    `<span class="ti">${I(t.icon, 25)}${t.id === "sessions" && unread ? `<span class="tbadge" aria-label="${unread} new">${unread > 99 ? "99+" : unread}</span>` : ""}</span>` +
    `<span class="tl">${esc(t.label)}</span></button>`).join("");
}
$("#tabbar").addEventListener("click", e => { const b = e.target.closest("[data-tab]"); if (b) switchTab(b.dataset.tab, { tap: true }); });

/* ===================================================================== swipe from the left edge: back (the page follows the finger) */
const PSWIPE = { x0: null, y0: 0, dx: 0, on: false, page: null, under: null };
for (const t of TABS.slice(1)) {
  const a = areaEl(t.id);
  a.addEventListener("touchstart", e => {
    const st = AREA.stacks[t.id], tch = e.touches[0];
    PSWIPE.x0 = st.length > 1 && e.touches.length === 1 && tch.clientX < 24 ? tch.clientX : null;
    PSWIPE.y0 = tch.clientY; PSWIPE.dx = 0; PSWIPE.on = false;
    PSWIPE.page = st[st.length - 1]; PSWIPE.under = st[st.length - 2];
  }, { passive: true });
  a.addEventListener("touchmove", e => {
    if (PSWIPE.x0 == null) return;
    const tch = e.touches[0], dx = tch.clientX - PSWIPE.x0, dy = tch.clientY - PSWIPE.y0;
    if (!PSWIPE.on) { if (Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx)) { PSWIPE.x0 = null; return; } if (dx > 10) PSWIPE.on = true; else return; }
    e.preventDefault();
    PSWIPE.dx = Math.max(0, dx);
    document.body.classList.add("swiping");
    PSWIPE.page.el.style.transform = `translateX(${PSWIPE.dx}px)`;
    PSWIPE.under.el.style.transform = `translateX(${-30 + PSWIPE.dx / innerWidth * 30}%)`;
  }, { passive: false });
  a.addEventListener("touchend", () => {
    if (PSWIPE.x0 == null) return;
    PSWIPE.x0 = null;
    if (!PSWIPE.on) return;
    document.body.classList.remove("swiping");
    PSWIPE.page.el.style.transform = PSWIPE.under.el.style.transform = "";
    if (PSWIPE.dx > innerWidth * .33) goBackPage(PSWIPE.page);  // (this frame: it slides on from where the finger left it)
  });
}

/* ===================================================================== shared bits of the areas' pages */
/* A plain-text editor over the tab bar (Cancel · name · Save), for a file or a note: it saves as the desktop does,
   with the hash the text came from; a 409 means the file changed on disk meanwhile (often an agent): overwrite it,
   keep mine as a copy beside it, or take the disk's. A blank editor never wipes a file without asking. */
function openEditor({ path, text, hash, title, onSaved, focusEnd = false, prose = false }) {
  const page = makePage({ cover: true, cls: "editor", title: title || basename(path), sub: tilde(dirname(path)),
    left: navTxt("cancel", "Cancel"), right: navTxt("save", "Save", true) });
  const st = { hash, saved: text };
  const ta = document.createElement("textarea");  // (in the scroller's place: it scrolls itself, the page fits the keyboard)
  ta.className = "edta" + (prose ? " prose" : "");
  // code: no autocorrect, no capitals; a note is prose (the keyboard's own help on)
  const attrs = prose ? { spellcheck: "true", autocapitalize: "sentences", autocorrect: "on" } : { spellcheck: "false", autocapitalize: "off", autocorrect: "off", autocomplete: "off" };
  for (const [k, v] of Object.entries(attrs)) ta.setAttribute(k, v);
  page.scroll.replaceWith(ta); page.scroll = ta;
  ta.value = text;
  page.dirty = () => ta.value !== st.saved;
  const close = () => { page.back = null; popPage(page.tab); };
  page.back = () => {
    if (!page.dirty()) { close(); return; }
    actionSheet("Discard your changes?", [{ label: "Discard changes", icon: "x", run: close }]);
  };
  page.acts.cancel = () => page.back();
  const save = async (opts = {}) => {
    const b = $("[data-nav=save]", page.el); b.disabled = true;
    const value = ta.value;
    try {
      const r = await api("POST", "/api/file", { path, text: value, base_hash: st.hash, force: !!opts.force, allow_empty: !!opts.allowEmpty });
      st.hash = r.hash; st.saved = value;
      if (onSaved) onSaved({ kind: "text", text: value, ...r });
      ta.blur(); close();
      toast("Saved");
    } catch (e) {
      if (e instanceof NotPaired) return;
      const d = e.data || {};
      if (e.status === 409 && d.empty) actionSheet(`${basename(path)} would become empty`, [{ label: "Save it empty", icon: "check", run: () => save({ ...opts, allowEmpty: true }) }]);
      else if (e.status === 409 && d.exists) toast("A file of that name is there already");
      else if (e.status === 409) conflictSheet(d.current || { deleted: true });
      else toast("Save failed: " + e.message);
    } finally { b.disabled = false; }
  };
  const conflictSheet = cur => {
    if (cur.deleted) { actionSheet("This file was deleted on disk", [{ label: "Save to recreate it", icon: "check", run: () => save({ force: true }) }]); return; }
    actionSheet("This file changed on disk while you were editing it", [
      { label: "Overwrite with mine", icon: "check", run: () => save({ force: true }) },
      { label: "Save mine as a copy", icon: "copy", run: () => saveCopy(cur) },
      { label: "Take the disk's (discard mine)", icon: "undo", run: () => { ta.value = st.saved = cur.text || ""; st.hash = cur.hash; if (onSaved) onSaved({ kind: "text", ...cur }); toast("Now the disk's version"); } },
    ]);
  };
  // "<name> (mine).<ext>" beside it (a free name); the file follows the disk, so neither version is lost.
  const saveCopy = async cur => {
    const name = basename(path), dot = name.lastIndexOf(".");
    const stem = dot > 0 ? name.slice(0, dot) : name, ext = dot > 0 ? name.slice(dot) : "";
    for (let n = 1; n < 100; n++) {
      const copy = `${dirname(path)}/${stem} (mine${n > 1 ? " " + n : ""})${ext}`;
      try {
        await api("POST", "/api/file", { path: copy, text: ta.value, base_hash: null, create_only: true, allow_empty: true });
        st.saved = ta.value = cur.text || ""; st.hash = cur.hash;
        if (onSaved) onSaved({ kind: "text", ...cur, copy });
        close();
        toast(`Your version is in ${basename(copy)}`);
        return;
      } catch (e) { if (e.status !== 409) { toast(e.message); return; } }
    }
  };
  page.acts.save = () => { if (!page.dirty()) { close(); return; } save(); };
  pushPage(page);
  setTimeout(() => { ta.focus({ preventScroll: true }); if (focusEnd) ta.setSelectionRange(ta.value.length, ta.value.length); else ta.setSelectionRange(0, 0); }, 360);
  return page;
}
/* One field in a page sheet (New note, New file, Add to today): Cancel · title · the action. */
function askText({ title, ok, label = "", value = "", placeholder = "", area = false, mono = false, onOk }) {
  const w = pageSheet({
    title, ok,
    body: `<div class="field">${label ? `<div class="flabel">${esc(label)}</div>` : ""}` +
      (area ? `<textarea id="ask-v" class="tinput area" rows="5" placeholder="${esc(placeholder)}"></textarea>`
        : `<input id="ask-v" class="tinput${mono ? " mono" : ""}" autocapitalize="off" autocorrect="off" spellcheck="false" enterkeyhint="done" placeholder="${esc(placeholder)}">`) +
      `</div><div class="ferr" id="ask-err"></div>`,
    onOk: async w => {
      const v = $("#ask-v", w).value, b = w.querySelector("[data-ok]");
      if (!v.trim()) { $("#ask-err", w).textContent = "Write something first."; return; }
      b.disabled = true;
      try { await onOk(v); closeSheets(); }
      catch (e) { b.disabled = false; if (!(e instanceof NotPaired)) $("#ask-err", w).textContent = e.message; }
    },
  });
  const f = $("#ask-v", w);
  f.value = value;
  if (!area) f.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); w.querySelector("[data-ok]").click(); } });
  setTimeout(() => f.focus(), 320);
  return w;
}
/* Rows of the areas' lists: icon · name (and a second line) · a hint · › (the Sessions rows' measures). */
function navRow({ attrs = "", icon = "", name, sub = "", hint = "", tag = "", chev = true, cls = "" }) {
  sub = String(sub).trim();
  return `<button class="nrow${sub ? " two" : ""}${cls ? " " + cls : ""}" ${attrs}><span class="ic">${icon}</span>` +
    `<span class="tx"><span class="t1"><span class="nm">${esc(name)}</span>${tag}</span>${sub ? `<span class="t2">${sub}</span>` : ""}</span>` +
    `${hint ? `<span class="hint">${hint}</span>` : ""}${chev ? `<span class="go">${I("chev", 14)}</span>` : ""}</button>`;
}
