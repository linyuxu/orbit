"use strict";
/* ===================================================================== a plugin's views on the phone (plugin API 2.0)
   The desktop's native views (web/src/pviews.js, builddoc/plugin-views.md) from the same API, as pages of the Misc
   area: Misc lists each view the plugins declare (their left tabs and panels: lists, docs, threads); a list is drawn
   like the Sessions list (label, detail, time, an unread dot, groups; pull to refresh when its source has a command);
   an item opens its thread or doc (or its URL). A thread is drawn like the conversation (the same type, the user's own
   messages in a bubble, author lines grouped within 5 minutes, the image viewer), live (the event stream), older
   messages paged in at the top; a reply has its own live-preview editor (a draft per thread), photos as files. The
   Markdown is the desktop's plugin mode (markdown.js renderPluginMd: no local paths, its schemes). Replies go to the
   server (it allows the token's holder); the plugin's own actions ([[ui.message.menu]]) stay on its machine. */
const PV = { ui: null, lists: new Map(), threads: new Set(), docs: new Set() };  // lists: "plugin:source" → {items, refresh, error, seen}
const pviewPath = (plugin, kind, key) => `plugin:${plugin}/${kind}/${key}`;
const pkind = (plugin, kind) => PV.ui && PV.ui.tabs.find(t => t.plugin === plugin && t.kind === kind) || null;
const pschemes = plugin => (PV.ui && PV.ui.schemes[plugin]) || [];
async function loadPluginUi() {
  try { PV.ui = await api("GET", "/api/plugin-ui"); } catch (e) { if (e instanceof NotPaired) return; }
  for (const v of pluginViews()) if (v.view === "list") loadPList(v.plugin, v.source);
  miscChanged();
}
/* What Misc lists: each plugin's left tabs and panels it can draw here (a list, a doc, a thread). */
function pluginViews() {
  const u = PV.ui; if (!u) return [];
  const out = [];
  for (const x of [...u.sidebars, ...u.panels]) {
    if (x.view === "list" && x.source) out.push({ plugin: x.plugin, id: x.id, title: x.title, view: "list", source: x.source });
    else if (x.view === "doc") out.push({ plugin: x.plugin, id: x.id, title: x.title, view: "doc", doc: x.doc || x.id });
    else if (x.view === "thread" && x.kind && x.key) out.push({ plugin: x.plugin, id: x.id, title: x.title, view: "thread", kind: x.kind, key: x.key });
  }
  return out;
}
const VIEW_ICON = { list: "list", doc: "file", thread: "chat" };
function pluginRowsHtml() {
  const vs = pluginViews(), by = new Map();
  for (const v of vs) { if (!by.has(v.plugin)) by.set(v.plugin, []); by.get(v.plugin).push(v); }
  return [...by].map(([plugin, xs]) => `<div class="shead"><span class="gn">${esc(plugin)}</span></div><div class="rows">` + xs.map(v => {
    const unread = v.view === "list" ? (plistItems(v.plugin, v.source) || []).filter(x => x.unread).length : 0;
    return navRow({ attrs: `data-pview="${esc(vs.indexOf(v))}"`, icon: I(VIEW_ICON[v.view], 18), name: v.title, hint: unread ? `${unread} new` : "" });
  }).join("") + `</div>`).join("");
}
function openPluginView(i, tab = "misc") {
  const v = pluginViews()[i]; if (!v) return;
  if (v.view === "list") pushPage(plistPage(v), tab);
  else if (v.view === "doc") pushPage(pdocPage(v.plugin, v.doc, v.title), tab);
  else pushPage(pthreadPage(v.plugin, v.kind, v.key, v.title), tab);
}

/* ===================================================================== a list (an item source) */
const plistItems = (plugin, source) => (PV.lists.get(`${plugin}:${source}`) || {}).items;
async function loadPList(plugin, source, fresh = false) {
  const k = `${plugin}:${source}`;
  try {
    const x = (await api("GET", `/api/palette?plugin=${encodeURIComponent(plugin)}&source=${encodeURIComponent(source)}${fresh ? "&fresh=1" : ""}`)).sources[0];
    const was = PV.lists.get(k), seen = was ? was.seen : new Set();
    for (const it of x ? x.items : []) if (it.unread && seen.has(it.key + "\0" + it.time)) it.unread = false;  // (opened here meanwhile)
    PV.lists.set(k, { items: x ? x.items : [], refresh: !!(x && x.refresh), error: x && x.error, seen });
  } catch (e) { if (e instanceof NotPaired) return; if (!PV.lists.has(k)) PV.lists.set(k, { items: [], seen: new Set(), error: e.message }); }
  miscChanged();
}
/* Read here: an item opened, or every item that opens a thread while it was on screen (its answers came in live). */
function markPSeen(plugin, test) {
  let ch = false;
  for (const [k, d] of PV.lists) {
    if (!k.startsWith(plugin + ":")) continue;
    for (const it of d.items) if (it.unread && test(it)) { it.unread = false; d.seen.add(it.key + "\0" + it.time); ch = true; }
  }
  if (ch) miscChanged();
}
const opensThread = (kind, key) => it => !!(it.open && it.open.kind === kind && it.open.key === key);
/* Rows by group (an item's `group`; the ungrouped first), as the desktop's left tab: label · tag · unread · time, the
   detail under it. */
function plistPage(v) {
  const k = `${v.plugin}:${v.source}`;
  const page = makePage({ title: v.title, sub: v.plugin });
  page.plist = k;
  page.refresh = () => {
    const d = PV.lists.get(k);
    if (!d) { page.body.innerHTML = loadingHtml(); return; }
    if (d.refresh && !page.pull) { addPull(page.scroll, () => loadPList(v.plugin, v.source, true)); page.pull = true; }
    if (!d.items.length) { page.body.innerHTML = d.error ? emptyBox("Not available", esc(d.error)) : emptyBox("Nothing here yet", ""); return; }
    const groups = new Map([["", []]]);
    for (const it of d.items) { const g = it.group || ""; if (!groups.has(g)) groups.set(g, []); groups.get(g).push(it); }
    page.items = d.items;
    page.body.innerHTML = [...groups].filter(([, its]) => its.length).map(([g, its]) =>
      (g ? `<div class="shead"><span class="gn">${esc(g)}</span><span class="cnt">${its.length}</span></div>` : "") +
      `<div class="rows">${its.map(it => `<button class="srow irow${it.unread ? " unread" : ""}" data-item="${esc(d.items.indexOf(it))}">` +
        `<span class="l1"><span class="nm">${esc(it.label)}</span>${it.tag ? `<span class="mtag">${esc(it.tag)}</span>` : ""}` +
        `${it.unread ? '<span class="udot" aria-label="Unread"></span>' : ""}<span class="tm">${esc(ago(it.time))}</span></span>` +
        (it.detail ? `<span class="l2">${esc(it.detail)}</span>` : "") + `</button>`).join("")}</div>`).join("");
  };
  page.onShow = () => loadPList(v.plugin, v.source);
  page.body.addEventListener("click", e => {
    const r = e.target.closest("[data-item]"), it = r && page.items && page.items[+r.dataset.item];
    if (!it) return;
    markPSeen(v.plugin, x => x === it);
    openPItem(v.plugin, v.source, it, page.tab);
  });
  page.refresh();
  loadPList(v.plugin, v.source);
  return page;
}
/* An item opens its `open` (a native thread or doc of the plugin; a URL), else what its source opens (a tab of its
   kind by the item's key), else its link. */
function openPItem(plugin, source, it, tab) {
  const o = it.open, src = (PV.ui && PV.ui.palette.find(x => x.plugin === plugin && x.id === source)) || {};
  if (o && o.url) { openLink(o.url); return; }
  const kind = o && o.kind ? o.kind : (src.open === "tab" || !src.open) ? it.kind || src.kind || "" : "";
  const k = kind && pkind(plugin, kind), key = o && o.kind ? o.key : it.key;
  if (k && k.view === "thread") { pushPage(pthreadPage(plugin, kind, key, it.label), tab); return; }
  if (k && k.view === "doc") { pushPage(pdocPage(plugin, key, it.label), tab); return; }
  if (it.url) { openLink(it.url); return; }
  toast("This opens on the Mac");
}

/* ===================================================================== a doc */
function pdocPage(plugin, id, title) {
  const page = makePage({ title: title || id, sub: plugin, right: navBtn("refresh", I("refresh", 20), "Reload") });
  Object.assign(page, { plugin, docId: id, rev: 0 });
  page.body.innerHTML = loadingHtml();
  page.load = async (fresh = false) => {
    let d;
    try { d = await api("GET", `/api/plugins/${plugin}/docs/${encodeURIComponent(id)}${fresh ? "?fresh=1" : ""}`); }
    catch (e) { if (e instanceof NotPaired) return; d = { error: e.message }; }
    page.rev = d.rev || 0;
    if (d.title) page.setTitle(d.title, plugin);
    page.body.innerHTML = d.markdown ? `<div class="doc"><div class="md">${renderPluginMd(d.markdown, plugin, pschemes(plugin))}</div></div>`
      : d.error ? emptyBox("Not available", esc(d.error)) : d.loading ? loadingHtml() : emptyBox("Nothing here yet", "");
  };
  page.acts.refresh = () => page.load(true);
  page.onShow = () => page.load();
  page.onClose = () => PV.docs.delete(page);
  page.body.addEventListener("click", e => pmdClick(e));
  PV.docs.add(page);
  page.load();
  return page;
}
/* A link: the web in Safari; another scheme (one of the plugin's: slack:…) to the system, which opens its app. */
const openLink = url => { if (/^https?:/i.test(url)) window.open(url, "_blank", "noopener"); else location.href = url; };
/* Taps in a plugin's Markdown: Copy, a picture → the viewer, a link of the plugin's own schemes → the system. */
function pmdClick(e) {
  const t = e.target;
  if (t.closest("[data-copy]")) { copyText(t.closest(".pre").querySelector("code").innerText); return true; }
  const img = t.closest(".md img");
  if (img) { openLightbox(img.src); return true; }
  const a = t.closest("a[href]"), href = a && a.getAttribute("href");
  if (a && href && !/^(https?|mailto):/i.test(href)) { e.preventDefault(); openLink(href); return true; }
  return false;
}

/* ===================================================================== a thread */
const TDRAFTS = LS.get("threadDrafts", {});  // a thread's path → the reply being written
const saveTDraft = (path, text) => { if (text) TDRAFTS[path] = text; else delete TDRAFTS[path]; LS.set("threadDrafts", TDRAFTS); };
function pthreadPage(plugin, kind, key, title) {
  const path = pviewPath(plugin, kind, key);
  const page = makePage({ cover: true, cls: "pthread", title: title || key, sub: plugin, right: navBtn("more", MORE_SVG, "Thread actions") });
  const T = page.t = { plugin, kind, key, path, url: `/api/plugins/${plugin}/threads/${kind}/${encodeURIComponent(key)}`,
                       data: null, rev: 0, older: [], olderMore: null, files: [], sent: {}, atEnd: true, prog: null, anchor: null };
  page.body.classList.add("tcards");
  page.el.insertAdjacentHTML("beforeend", `<footer class="tfoot">
      <button class="tjump">${I("down", 14)}<span>Latest</span></button>
      <div class="tchips" hidden></div>
      <div class="crow">
        <button class="cbtn tattach" aria-label="Attach a photo">${I("image", 22)}</button>
        <input type="file" class="tphoto" accept="image/*" multiple hidden>
        <div class="cfield"><div class="treply"></div><button class="cbtn round send tsend" disabled aria-label="Send"><span class="disc">${SEND_SVG}</span></button></div>
      </div></footer>`);
  const sc = page.scroll, foot = $(".tfoot", page.el), jump = $(".tjump", page.el);
  /* ---- the scroll rule (the conversation's, m-conv.js): stuck to the end it follows; scrolled up it stays where it
     is (the message at the top, its offset); a scroll the page made is told apart by where it lands */
  const atBottom = () => sc.scrollHeight - sc.scrollTop - sc.clientHeight < 4;
  const prog = top => { sc.scrollTop = top; T.prog = sc.scrollTop; };
  const updJump = () => jump.classList.toggle("show", !T.atEnd && !!(T.data && T.data.messages.length));
  const firstVisible = () => {
    const top = sc.getBoundingClientRect().top;
    const el = $$(".tmsg", page.body).find(c => c.getBoundingClientRect().bottom > top + 1);
    return el && { id: el.dataset.mid, off: el.getBoundingClientRect().top - top };
  };
  T.firstVisible = firstVisible;
  T.restore = a => {  // the same message in the same place
    const el = a && $$(".tmsg", page.body).find(x => x.dataset.mid === a.id);
    if (!el) return false;
    const d = el.getBoundingClientRect().top - sc.getBoundingClientRect().top - a.off;
    if (Math.abs(d) >= 1) prog(sc.scrollTop + d);
    return true;
  };
  T.stick = () => { if (T.atEnd) prog(sc.scrollHeight); updJump(); };
  T.keep = () => { if (T.atEnd) T.stick(); else T.restore(T.anchor); };
  sc.addEventListener("scroll", () => {
    if (T.prog != null && Math.abs(sc.scrollTop - T.prog) < 2) return;  // where the page put it (null: none since)
    T.prog = null;
    T.atEnd = atBottom();
    T.anchor = T.atEnd ? null : firstVisible();
    updJump();
    if (sc.scrollTop < 300 && moreOlder(T) && !T.loadingOlder && !T.olderT) {
      T.olderT = requestAnimationFrame(() => { T.olderT = 0; if (sc.scrollTop < 300) loadOlder(page); });
    }
  }, { passive: true });
  new ResizeObserver(T.keep).observe(page.body);
  new ResizeObserver(T.keep).observe(sc);
  jump.addEventListener("click", () => { T.atEnd = true; T.stick(); });
  /* ---- loading, live */
  page.load = async (fresh = false) => {
    T.loading = true;
    try {
      const d = await api("GET", T.url + (fresh ? "?fresh=1" : ""));
      for (const [cid, s] of Object.entries(T.sent)) if (!d.messages.some(m => m.client_id === cid) && s.local) d.messages.push(s.local);  // (never reached the server)
      T.data = d; T.rev = d.rev || 0;
      page.setTitle(d.title || title || key, d.sub || plugin);
      markPSeen(plugin, opensThread(kind, key));
    } catch (e) { if (e instanceof NotPaired) { T.loading = false; return; } T.data = T.data || { messages: [], error: e.message }; }
    T.loading = false;
    drawThread(page);
    if (T.missed > T.rev) { T.missed = 0; page.load(); }  // (a change came while it loaded)
  };
  page.onShow = () => page.load();
  page.onClose = () => { PV.threads.delete(page); if (T.ed) T.ed.view.destroy(); markPSeen(plugin, opensThread(kind, key)); };
  page.acts.more = () => actionSheet(page.el.querySelector(".pt").textContent, [
    T.data && T.data.url ? { label: "Open at the source", icon: "external", run: () => openLink(T.data.url) } : null,
    { label: "Reload", icon: "refresh", run: () => { T.older = []; T.olderMore = null; page.load(true); } },
  ].filter(Boolean));
  /* ---- taps */
  page.body.addEventListener("click", e => {
    if (pmdClick(e)) return;
    const t = e.target, el = t.closest(".tmsg"), m = el && findTMsg(T, el.dataset.mid);
    if (t.closest("[data-older]")) { loadOlder(page); return; }
    if (t.closest(".tfimgs img")) { openLightbox(t.src); return; }
    if (!m) return;
    if (t.closest("[data-replies]") && m.replies) { pushPage(pthreadPage(plugin, m.replies.kind, m.replies.key, `${m.replies.count} repl${m.replies.count === 1 ? "y" : "ies"}`), page.tab); return; }
    if (t.closest("[data-retry]")) { retryTSend(page, m); return; }
    if (t.closest("[data-edit]")) editTPending(page, m);
  });
  foot.addEventListener("click", e => {
    const x = e.target.closest("[data-unchip]");
    if (x) { T.files.splice(+x.dataset.unchip, 1); drawTChips(page); }
  });
  $(".tattach", page.el).addEventListener("click", () => $(".tphoto", page.el).click());
  $(".tphoto", page.el).addEventListener("change", e => { const fs = [...e.target.files]; e.target.value = ""; attachTFiles(page, fs); });
  const sendBtn = $(".tsend", page.el);
  sendBtn.addEventListener("pointerdown", e => { if (T.ed && T.ed.view.hasFocus) e.preventDefault(); });  // (the keyboard stays up)
  sendBtn.addEventListener("click", () => tsend(page));
  PV.threads.add(page);
  page.body.innerHTML = loadingHtml();
  page.load();
  return page;
}
const findTMsg = (T, id) => [...T.older, ...(T.data ? T.data.messages : [])].find(m => m.id === id);
const moreOlder = T => T.data && (T.olderMore ?? !!T.data.more);
/* ---- drawing: another's message as an answer (an author line over the Markdown; one line for an author's messages
   within 5 minutes), the user's own as a request, in a bubble (Markdown inside) */
function tavHtml(a) {
  const name = a.name || "";
  if (a.avatar) return `<img class="tav" src="${esc(a.avatar)}" alt="">`;
  const ini = name.split(/\s+/).filter(Boolean).slice(0, 2).map(w => [...w][0]).join("").toUpperCase() || "?";
  let h = 0; for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 360;
  return `<span class="tav ini" style="background:hsl(${h},40%,52%)">${esc(ini)}</span>`;
}
function tfilesHtml(plugin, m) {
  const fs = m.files || [];
  if (!fs.length) return "";
  const url = f => f.url.startsWith("svc:") ? svcUrl(plugin, f.url) : f.url;
  const imgs = fs.filter(f => f.image && f.url), rest = fs.filter(f => !(f.image && f.url));
  return (imgs.length ? `<div class="tfimgs">${imgs.map(f => `<img src="${esc(url(f))}" alt="${esc(f.name)}" loading="lazy">`).join("")}</div>` : "") +
    (rest.length ? `<div class="tfiles">${rest.map(f => f.url ? `<a class="tfile" href="${esc(url(f))}" target="_blank" rel="noopener">${I("file", 14)}<span>${esc(f.name || "file")}</span></a>`
      : `<span class="tfile">${I("file", 14)}<span>${esc(f.name || "file")}</span></span>`).join("")}</div>` : "");
}
function tmsgHtml(T, m, prev) {
  const a = m.author || {};
  const body = m.markdown ? `<div class="md">${renderPluginMd(m.markdown, T.plugin, pschemes(T.plugin))}</div>` : "";
  const reacts = (m.reactions || []).length ? `<div class="treacts">${m.reactions.map(r => `<span class="treact${r.me ? " me" : ""}">${esc(r.emoji)} ${r.count}</span>`).join("")}</div>` : "";
  const more = m.replies && m.replies.count ? `<button class="treplies" data-replies>${I("chev", 12)}${m.replies.count} repl${m.replies.count === 1 ? "y" : "ies"}</button>` : "";
  const when = esc(dayTime(m.time));
  if (a.me) {
    const st = m.pending === "sending" ? `<span class="tpend">Sending…</span>`
      : m.pending === "failed" ? `<span class="tfail">Not sent${m.error ? ": " + esc(m.error.slice(0, 80)) : ""}</span><button data-retry>${I("resend", 12)}Retry</button><button data-edit>${I("pencil", 12)}Edit</button>` : "";
    return `<div class="tmsg req${m.pending ? " pending" : ""}" data-mid="${esc(m.id)}"><div class="bubble">${body}${tfilesHtml(T.plugin, m)}` +
      `<div class="meta">${st}${m.edited ? "<span>edited</span>" : ""}<span class="when">${when}</span></div></div>${reacts}${more}</div>`;
  }
  const pa = prev && prev.author || {};
  const grouped = !!prev && !pa.me && !prev.pending && pa.name === a.name && m.time && prev.time && m.time >= prev.time && m.time - prev.time < 300;
  const head = grouped ? "" : `<div class="head tauthor">${tavHtml(a)}<span class="st">${esc(a.name || "Someone")}</span><span class="tm">${when}</span>${m.edited ? `<span class="ted">edited</span>` : ""}</div>`;
  return `<div class="tmsg ans${grouped ? " cont" : ""}" data-mid="${esc(m.id)}">${head}${body}${tfilesHtml(T.plugin, m)}${reacts}${more}</div>`;
}
const htmlEl = html => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };
function drawThread(page) {
  const T = page.t, d = T.data, box = page.body;
  if (!d) { box.innerHTML = loadingHtml(); return; }
  const msgs = [...T.older, ...d.messages];
  if (!msgs.length) {
    box.innerHTML = d.loading ? loadingHtml() : d.error ? emptyBox("Not available", esc(d.error)) : emptyBox("No messages yet", "");
  } else {
    const pin = T.atEnd ? null : T.anchor || null;
    for (const e of [...box.children]) if (!e.dataset.mid && !e.classList.contains("earlier")) e.remove();
    let top = $(".earlier", box);
    if (moreOlder(T)) {
      if (!top) { top = htmlEl(`<div class="earlier"><button class="pill quiet" data-older></button></div>`); box.prepend(top); }
      top.firstChild.textContent = T.loadingOlder ? "Loading…" : "Show earlier messages";
    } else if (top) top.remove();
    const have = new Map([...box.children].filter(e => e.dataset.mid).map(e => [e.dataset.mid, e]));
    let i = top && top.isConnected ? 1 : 0;
    for (const [k, m] of msgs.entries()) {
      const html = tmsgHtml(T, m, msgs[k - 1]);
      let e = have.get(m.id);
      if (e && e._html !== html) { const n = htmlEl(html); e.replaceWith(n); e = n; }
      if (!e) e = htmlEl(html);
      e._html = html; have.delete(m.id);
      if (box.children[i] !== e) box.insertBefore(e, box.children[i] || null);
      i++;
    }
    for (const e of have.values()) e.remove();
    if (T.atEnd) T.stick(); else if (pin) T.restore(pin);
  }
  drawTComposer(page);
  page.el.querySelector(".tjump").classList.toggle("show", !T.atEnd && msgs.length > 0);
}
/* ---- older messages: scrolled to the top (or the button), through the plugin's service; they stay in this page */
async function loadOlder(page) {
  const T = page.t;
  if (T.loadingOlder || !moreOlder(T)) return;
  const first = [...T.older, ...T.data.messages].find(m => !m.pending);
  if (!first) return;
  T.loadingOlder = true;
  drawThread(page);
  try {
    const r = await api("GET", `${T.url}?before=${encodeURIComponent(first.id)}`);
    const have = new Set([...T.older, ...T.data.messages].map(m => m.id));
    T.older = [...r.messages.filter(m => !have.has(m.id)), ...T.older];
    T.olderMore = !!r.more && r.messages.length > 0;
  } catch (e) { T.olderMore = false; if (!(e instanceof NotPaired)) toast(`Older messages: ${e.message}`); }
  T.loadingOlder = false;
  if (!T.atEnd) T.anchor = T.firstVisible() || T.anchor;  // (the one in view stays where it is)
  drawThread(page);
}
/* ---- live: the store's changes, in order (a gap: the whole thread again) */
function insertTMsg(T, x) {
  const ms = T.data.messages, i = ms.findIndex(y => y.id === x.id);
  if (i >= 0) { ms[i] = x; return; }
  const p = x.pending ? -1 : ms.findIndex(y => y.pending);  // (new ones go before the replies still being sent)
  if (p < 0) ms.push(x); else ms.splice(p, 0, x);
}
function threadEvent(page, ev) {
  const T = page.t;
  if (T.loading) { T.missed = Math.max(T.missed || 0, ev.rev); return; }
  if (!T.data || ev.rev <= T.rev) return;
  const c = ev.change || {};
  if (ev.rev !== T.rev + 1 || c.whole) { page.load(); return; }
  T.rev = ev.rev;
  for (const k of ["title", "sub", "url", "composer", "more", "unread", "error"]) if (k in c) T.data[k] = c[k];
  if (c.remove) { const gone = new Set(c.remove); T.data.messages = T.data.messages.filter(m => !gone.has(m.id)); }
  for (const x of c.update || []) { const i = T.data.messages.findIndex(m => m.id === x.id); if (i >= 0) T.data.messages[i] = x; }
  for (const [old, x] of Object.entries(c.replace || {})) {
    const i = T.data.messages.findIndex(m => m.id === old);
    if (i >= 0) T.data.messages[i] = x; else insertTMsg(T, x);
    delete T.sent[x.client_id];
  }
  for (const x of c.append || []) insertTMsg(T, x);
  if (c.title || c.sub) page.setTitle(T.data.title || page.el.querySelector(".pt").textContent, T.data.sub || T.plugin);
  drawThread(page);
}
/* The event stream (m.js onEvent): threads, docs and lists on screen follow it; hello (a reconnect, a restart: maybe
   other plugins) fetches all again. */
function pviewEvent(m) {
  if (m.t === "hello") {
    if (PV.ui) loadPluginUi();
    for (const p of PV.threads) if (p.t.data) p.load();
    for (const p of PV.docs) p.load();
    return;
  }
  if (m.t === "plugin-thread") { for (const p of PV.threads) if (p.t.plugin === m.plugin && p.t.kind === m.kind && p.t.key === m.key) threadEvent(p, m); }
  else if (m.t === "plugin-doc") { for (const p of PV.docs) if (p.plugin === m.plugin && p.docId === m.id && m.rev > p.rev) p.load(); }
  else if (m.t === "plugin-items") { if (PV.lists.has(`${m.plugin}:${m.source}`)) loadPList(m.plugin, m.source); }
}
/* ---- the reply: its own live-preview editor (a draft per thread), photos as files (chips), no session controls */
function drawTComposer(page) {
  const T = page.t, d = T.data || {}, foot = $(".tfoot", page.el);
  const off = !!(d.composer && d.composer.enabled === false);
  foot.classList.toggle("ro", off);
  if (off) return;
  const ph = (d.composer && d.composer.placeholder) || `Reply in ${d.title || page.el.querySelector(".pt").textContent}`;
  if (!T.ed) {
    T.ed = CM.createEditor({
      parent: $(".treply", page.el), doc: TDRAFTS[T.path] || "", path: "reply.md",
      composer: { placeholder: ph, attrs: { autocorrect: "on", autocapitalize: "sentences", spellcheck: "true", enterkeyhint: "enter" } },
      preview: { resolve: src => /^https:/i.test(src) ? src : "", inline: s => md.renderInline(s) },  // (its own text: no local files)
      zoom: url => openLightbox(url),
      onOpenLink: url => window.open(url, "_blank", "noopener"),
      onFiles: files => attachTFiles(page, files),
      onChange: () => { saveTDraft(T.path, T.ed.getDoc()); updTSend(page); },
    });
    T.ph = ph;
  } else if (T.ph !== ph) { T.ed.setPlaceholder(ph); T.ph = ph; }
  updTSend(page);
}
const updTSend = page => { const T = page.t; $(".tsend", page.el).disabled = !((T.ed && T.ed.getDoc().trim()) || T.files.length); };
function drawTChips(page) {
  const T = page.t, box = $(".tchips", page.el);
  box.hidden = !T.files.length;
  box.innerHTML = T.files.map((f, i) => `<span class="tchip">${f.image ? `<img src="${esc(rawUrl(f.path))}" alt="">` : I("file", 14)}<span class="nm">${esc(f.name)}</span>` +
    `<button class="x" data-unchip="${i}" aria-label="Remove ${esc(f.name)}">${I("x", 12)}</button></span>`).join("");
  updTSend(page);
}
/* A photo (the camera, the library) or a pasted image: a smaller JPEG first, uploaded to the server's machine (the
   plugin's), sent as a file of the reply (a chip), not a link in the text. */
async function attachTFiles(page, files) {
  const T = page.t, btn = $(".tattach", page.el);
  files = [...files].filter(f => f && f.size);
  for (const f of files) {
    btn.classList.add("busy");
    try {
      const img = /^image\//.test(f.type || "");
      const { name, data } = img ? await shrinkPhoto(f) : { name: f.name || "file", data: await fileData(f) };
      const r = await api("POST", "/api/upload", { name, data });
      T.files.push({ name, path: r.path, image: img });
    } catch (e) { if (!(e instanceof NotPaired)) toast(`${f.name || "Photo"}: ${e.message}`); }
    finally { btn.classList.remove("busy"); }
  }
  drawTChips(page);
}
function tsend(page) {
  const T = page.t, text = T.ed ? T.ed.getDoc() : "", files = T.files.slice();
  if (!text.trim() && !files.length) return;
  const cid = "c" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  const local = { id: "pending:" + cid, client_id: cid, author: { name: "", me: true }, time: Date.now() / 1000, markdown: text,
                  ...(files.length ? { files: files.map(f => ({ name: f.name })) } : {}), pending: "sending" };
  T.sent[cid] = { markdown: text, files, local };
  insertTMsg(T, local);  // at once, "Sending…"
  if (T.ed) T.ed.setDoc("", false);
  T.files = []; drawTChips(page); saveTDraft(T.path, "");
  T.atEnd = true;
  drawThread(page);
  postTSend(page, cid);
}
async function postTSend(page, cid, body) {
  const T = page.t, s = T.sent[cid];
  try {
    await api("POST", T.url + "/send", body || { markdown: s.markdown, files: s.files.map(f => ({ name: f.name, path: f.path })), client_id: cid });
    if (s) delete s.local;  // (the server keeps it from now on)
  } catch (e) {  // never reached the server (or refused): "Not sent", with Retry and Edit
    const m = T.data.messages.find(x => x.client_id === cid && x.pending);
    if (m) { m.pending = "failed"; m.error = e.message; drawThread(page); }
  }
}
function retryTSend(page, m) {
  const T = page.t;
  m.pending = "sending"; m.error = "";
  drawThread(page);
  postTSend(page, m.client_id, T.sent[m.client_id] ? null : { markdown: m.markdown, files: [], client_id: m.client_id });  // (same client_id: the service can tell)
}
function editTPending(page, m) {
  const T = page.t, s = T.sent[m.client_id];
  if (T.ed) { const cur = T.ed.getDoc(); T.ed.setDoc(cur.trim() ? cur.replace(/\s+$/, "") + "\n\n" + m.markdown : m.markdown); T.ed.focus(); }
  if (s) { T.files.push(...s.files); drawTChips(page); }
  delete T.sent[m.client_id];
  T.data.messages = T.data.messages.filter(x => x !== m);
  drawThread(page);
  api("POST", T.url + "/discard", { client_id: m.client_id }).catch(() => {});
}
