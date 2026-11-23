"use strict";
/* ===================================================================== a plugin's views, drawn by Orbit (plugin API 2.0) */
/* builddoc/plugin-views.md. A plugin gives data; Orbit draws it with the conversation's own code, in the main page:
   - a thread ([[ui.tab]] view = "thread"): its messages as a conversation's (msglist.js: the scroll box, Latest, the
     anchors; renderMd in plugin mode; code blocks, KaTeX, the image viewer, ⌘F, the quote menu), Orbit's own editor
     below (a second CM.createEditor: drafts by the tab's path, pasted images as chips, ⌘⏎), a context menu per message;
   - a doc (view = "doc"): a Markdown document, in the answers' type;
   - a list (a left tab's or a panel's view = "list"): an item source's items, drawn like the Sessions list (left.js).
   A view lives in a main tab (#pview), a side tab (this page embedded: EMBED.kind "pview") or the left panel (a doc).
   Each is named by a path: "plugin:<plugin>/<kind>/<key>" (a tab), "…/~doc/<id>", "…/~list/<source>" (a panel's). */
const PVIEWS = new Map();  // id → view: a main tab's (or side tab's) path, "sb:<left tab>" (a doc in the left panel)
const TSCROLL = LS.get("threadScroll", {});  // path → {card, off}: a thread held where the user left it
const TDRAFTS = "orbit.threadDrafts";  // path → the reply being written (one key for every page, side tabs too)
const tdrafts = () => { try { return JSON.parse(localStorage.getItem(TDRAFTS) || "{}") || {}; } catch { return {}; } };
function saveTDraft(path, text) {
  const d = tdrafts();
  if (text) d[path] = text; else delete d[path];
  try { localStorage.setItem(TDRAFTS, JSON.stringify(d)); } catch {}
}
function viewSpec(path) {
  const p = parsePluginTab(path);
  if (!p) return null;
  if (p.kind === "~doc") return PUI.tabs.concat(PUI.panels, PUI.sidebars).some(x => x.plugin === p.plugin && x.view === "doc") ? { ...p, view: "doc", doc: p.key } : null;
  if (p.kind === "~list") return PUI.palette.some(x => x.plugin === p.plugin && x.id === p.key) ? { ...p, view: "list", source: p.key } : null;
  const k = pluginKind(p.plugin, p.kind);
  return !k ? null : k.view === "doc" ? { ...p, view: "doc", doc: p.key } : { ...p, view: "thread" };
}
const specSig = s => JSON.stringify([s.plugin, s.view, s.kind, s.key]);
function mountPView(id, path, box) {
  let v = PVIEWS.get(id);
  const spec = viewSpec(path);
  if (v && (!spec || v.sig !== specSig(spec))) { dropPView(id); v = null; }
  if (v) { if (v.el.parentNode !== box) box.append(v.el); return v; }
  v = spec.view === "thread" ? threadView(path, spec) : spec.view === "doc" ? docView(path, spec) : listView(path, spec);
  Object.assign(v, { id, sig: specSig(spec) });
  box.append(v.el);
  PVIEWS.set(id, v);
  return v;
}
function dropPView(id) { const v = PVIEWS.get(id); if (v) { v.destroy(); PVIEWS.delete(id); } }
const pviewShown = () => isPluginTab(S.file) ? PVIEWS.get(S.file) || null : null;
const viewTitle = v => (v.data && v.data.title) || pluginTabInfo(v.path).title;
const isShownView = v => v.el.isConnected && !v.el.hidden && !(v.el.closest("[hidden]")) && (EMBED || v.id.startsWith("sb:") || S.file === v.path);
const htmlEl = html => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };
/* Keys and commands that reach the view in front (⌘⏎, ⌘L, Esc): whether it took them. */
function pviewKey(what) {
  const v = pviewShown();
  if (!v) return false;
  if (what === "latest") { if (v.list) v.list.latest(); return true; }
  if (what === "send") { if (v.send) v.send(); return true; }
  return false;
}
function pviewEsc() {  // the reply editor → its thread (as the input → the conversation)
  const v = [...PVIEWS.values()].find(x => x.ed && x.el.contains(document.activeElement));
  if (!v) return false;
  v.box.focus({ preventScroll: true });
  return true;
}
function pviewScrollKey(mv) {  // ↑↓ PgUp PgDn … in the view whose scroll box has the keyboard (or the shown one)
  const a = document.activeElement;
  const v = [...PVIEWS.values()].find(x => x.box === a) || (a === document.body && pviewShown());
  if (!v || !v.box) return false;
  if (v.list) v.list.scroll(mv); else scrollBox(v.box, mv);
  return true;
}
/* The selection quote (notes.js): text selected in a thread's message or a doc, quoted with the view's title.
   undefined: the selection is in no view (and the main tab is none). */
function pviewQuote() {
  const sel = getSelection();
  const node = sel && !sel.isCollapsed && sel.anchorNode && (sel.anchorNode.nodeType === 1 ? sel.anchorNode : sel.anchorNode.parentElement);
  const v = node && [...PVIEWS.values()].find(x => x.root && x.root.contains(node) && isShownView(x));
  if (!v) return isPluginTab(S.file) ? null : undefined;
  if (v.kind === "thread" && !node.closest(".tmsg")) return null;
  const text = sel.toString().trim();
  if (!text) return null;
  const range = sel.getRangeAt(0);
  return { text, ref: viewTitle(v), at: () => { const rs = range.getClientRects(); return rs[rs.length - 1] || range.getBoundingClientRect(); } };
}
function setTabTitle(path, title) {
  if (EMBED || !title) return;
  let ch = false;
  for (const t of Object.values(S.ftabs).flat()) if (t.path === path && t.title !== title.slice(0, 80)) { t.title = title.slice(0, 80); ch = true; }
  if (ch) { saveUi(); renderTabs(); if (S.file === path) renderStatus(); }
}
/* Opened: its unread dot goes, and that plugin's notices for it (the bell). */
function clearViewUnread(path) {
  if (EMBED) return;
  const p = parsePluginTab(path);
  let ch = false;
  for (const t of Object.values(S.ftabs).flat()) if (t.path === path && t.unread) { t.unread = false; ch = true; }
  const n = S.notes.length;
  S.notes = S.notes.filter(x => !(x.kind === "plugin" && p && x.plugin === p.plugin && x.open && x.open.kind === p.kind && String(x.open.key) === p.key));
  if (S.notes.length !== n) { LS.set("notes", S.notes); ch = true; }
  if (ch) { saveUi(); renderTabs(); renderStatus(); }
}
function viewClicks(v, e) {  // what every view takes alike: Copy, a picture, a link (any scheme it may use: the system opens it)
  if (mdClick(e)) return true;
  const a = e.target.closest("a[href]");
  if (a && !/^(https?|mailto|orbit):/i.test(a.href)) { e.preventDefault(); openUrl(a.getAttribute("href")); return true; }
  return false;
}

/* ===================================================================== a thread */
const threadUrl = v => `/api/plugins/${v.spec.plugin}/threads/${v.spec.kind}/${encodeURIComponent(v.spec.key)}`;
function threadView(path, spec) {
  const v = { path, spec, kind: "thread", data: null, rev: 0, older: [], olderMore: null, files: [], sent: {}, ed: null };
  v.el = htmlEl(`<div class="pthread">
    <div class="pconv" tabindex="-1"><div class="col pcol"><div class="ptop"></div><div class="pmsgs"></div></div></div>
    <button class="pjump" title="Scroll to the latest message (⌘L)">${I("down", 14)}Latest</button>
    <div class="pcomposer-wrap"><div class="composer pcomposer">
      <div class="pchips" hidden></div>
      <div class="pinputhost" title="⌘⏎ sends · ⏎ new line · paste or drop a picture to attach it"></div>
      <div class="crow"><button class="ibtn sm pattach" title="Attach a file or image (⌘V or drop works too)">${I("clip", 14)}</button>
        <input type="file" class="pfile" multiple hidden><span class="pnote"></span><button class="psend" title="Send (⌘⏎)" disabled>${I("up", 16)}</button></div>
    </div><div class="pnosend" hidden></div></div></div>`);
  v.box = v.el.querySelector(".pconv"); v.root = v.el.querySelector(".pcol"); v.msgs = v.el.querySelector(".pmsgs"); v.top = v.el.querySelector(".ptop");
  v.list = new MsgList({ box: v.box, items: v.root, jump: v.el.querySelector(".pjump"), itemSel: ".tmsg",
    idOf: e => e.dataset.mid, elOf: id => v.msgs.querySelector(`.tmsg[data-mid="${CSS.escape(id)}"]`),
    keyOf: () => path, anchors: TSCROLL, persist: () => LS.set("threadScroll", TSCROLL),
    active: () => isShownView(v), hasItems: () => !!(v.data && v.data.messages.length) });
  v.list.atEnd = !TSCROLL[path];
  v.show = () => { clearViewUnread(path); if (!v.data && !v.loading) v.load(); else { drawThread(v); v.list.keepPlace(); } };
  v.load = async (fresh = false) => {
    v.loading = true;
    try {
      const d = await api("GET", threadUrl(v) + (fresh ? "?fresh=1" : ""));
      v.data = d; v.rev = d.rev || 0;
      for (const [cid, s] of Object.entries(v.sent)) if (!d.messages.some(m => m.client_id === cid) && s.local) d.messages.push(s.local);  // (never reached the server)
      setTabTitle(path, d.title);
    } catch (e) { v.data = v.data || { messages: [], error: e.message }; }
    v.loading = false;
    drawThread(v);
    if (v.list.atEnd) v.list.stick(); else v.list.restoreAnchor();
    if (v.missed > v.rev) { v.missed = 0; v.load(); }  // (a change came while it loaded)
  };
  v.reload = () => { v.older = []; v.olderMore = null; v.load(true); };
  v.focus = () => { if (v.ed && !v.el.querySelector(".pcomposer").hidden) v.ed.focus(); else v.box.focus({ preventScroll: true }); };
  v.url = () => v.data && v.data.url || "";
  v.send = () => threadSend(v);
  v.destroy = () => { if (v.ed) v.ed.view.destroy(); v.el.remove(); };
  v.el.addEventListener("click", e => threadClick(v, e));
  v.el.addEventListener("contextmenu", e => {
    const el = e.target.closest(".tmsg"), m = el && findMsg(v, el.dataset.mid);
    if (!m || m.pending || String(getSelection() || "").trim()) return;  // (a selection: the browser's menu)
    e.preventDefault();
    messageMenu(v, m, { at: { x: e.clientX, y: e.clientY } });
  });
  v.box.addEventListener("scroll", () => {  // near the top: older messages come in (once per frame)
    if (v.box.scrollTop < 300 && moreOlder(v) && !v.loadingOlder && !v.olderT) {
      v.olderT = requestAnimationFrame(() => { v.olderT = 0; if (v.box.scrollTop < 300) loadOlder(v); });
    }
  }, { passive: true });
  v.el.querySelector(".pfile").addEventListener("change", e => { attachThreadFiles(v, e.target.files); e.target.value = ""; });
  return v;
}
const findMsg = (v, id) => [...v.older, ...(v.data ? v.data.messages : [])].find(m => m.id === id);
const moreOlder = v => v.data && (v.olderMore ?? !!v.data.more);
function threadClick(v, e) {
  if (viewClicks(v, e)) return;
  const t = e.target, el = t.closest(".tmsg"), m = el && findMsg(v, el.dataset.mid);
  if (t.closest("[data-older]")) { loadOlder(v); return; }
  if (t.closest(".pjump")) { v.list.latest(); return; }
  if (t.closest(".psend")) { threadSend(v); return; }
  if (t.closest(".pattach")) { v.el.querySelector(".pfile").click(); return; }
  const x = t.closest("[data-unchip]");
  if (x) { v.files.splice(+x.dataset.unchip, 1); drawChips(v); return; }
  if (!m) return;
  if (t.closest("[data-replies]") && m.replies) { openPluginTab(v.spec.plugin, m.replies.kind, m.replies.key); return; }
  if (t.closest("[data-retry]")) { retrySend(v, m); return; }
  if (t.closest("[data-edit]")) { editPending(v, m); }
}
/* ---- drawing */
const TPEOPLE = new Map();  // a name → its avatar from the plugins' [people] ("": none), asked once
let peopleAsk = new Set();
function avatarHtml(a) {
  const name = a.name || "", src = a.avatar || TPEOPLE.get(name) || "";
  if (src) return `<img class="tav" src="${esc(src)}" alt="">`;
  if (name && !TPEOPLE.has(name) && PUI.local) {
    peopleAsk.add(name);
    clearTimeout(avatarHtml.t);
    avatarHtml.t = setTimeout(async () => {
      const names = [...peopleAsk]; peopleAsk = new Set();
      for (const n of names) TPEOPLE.set(n, "");
      try {
        const d = await api("POST", "/api/people", { names });
        let got = false;
        for (const [n, x] of Object.entries(d.people || {})) if (x.avatar) { TPEOPLE.set(n, x.avatar); got = true; }
        if (got) for (const w of PVIEWS.values()) if (w.kind === "thread" && w.data) drawThread(w);
      } catch {}
    }, 60);
  }
  const ini = name.split(/\s+/).filter(Boolean).slice(0, 2).map(w => [...w][0]).join("").toUpperCase() || "?";
  let h = 0; for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 360;
  return `<span class="tav ini" style="background:hsl(${h},40%,52%)">${esc(ini)}</span>`;
}
function filesHtml(v, m) {
  const fs = m.files || [];
  if (!fs.length) return "";
  const url = f => f.url.startsWith("svc:") ? svcUrl(v.spec.plugin, f.url) : f.url;
  const imgs = fs.filter(f => f.image && f.url), rest = fs.filter(f => !(f.image && f.url));
  return (imgs.length ? `<div class="md tfimgs">${imgs.map(f => `<img src="${esc(url(f))}" alt="${esc(f.name)}" title="${esc(f.name)}" loading="lazy">`).join("")}</div>` : "") +
    (rest.length ? `<div class="tfiles">${rest.map(f => f.url ? `<a class="chip tfile" href="${esc(url(f))}" target="_blank" rel="noopener" title="${esc(f.name)}">${I("file", 12)}<span>${esc(f.name || "file")}</span></a>`
      : `<span class="chip tfile">${I("file", 12)}<span>${esc(f.name || "file")}</span></span>`).join("")}</div>` : "");
}
const reactsHtml = m => (m.reactions || []).length ? `<div class="treacts">${m.reactions.map(r => `<span class="treact${r.me ? " me" : ""}">${esc(r.emoji)} ${r.count}</span>`).join("")}</div>` : "";
/* One message: another's as an answer (an author line over the Markdown; one line for an author's messages within
   5 minutes), the user's own as a request, in a bubble (Markdown inside). */
function tmsgHtml(v, m, prev) {
  const a = m.author || {}, plugin = v.spec.plugin;
  const body = m.markdown ? `<div class="md">${renderPluginMd(m.markdown, plugin, PUI.schemes[plugin] || [])}</div>` : "";
  const more = m.replies && m.replies.count ? `<button class="treplies" data-replies>${I("chev", 11)}${m.replies.count} repl${m.replies.count === 1 ? "y" : "ies"}</button>` : "";
  const when = esc(dayTime(m.time));
  if (a.me) {
    const st = m.pending === "sending" ? `<span class="tpend">Sending…</span>`
      : m.pending === "failed" ? `<span class="tfail" title="${esc(m.error || "")}">Not sent${m.error ? ": " + esc(m.error.slice(0, 80)) : ""}</span><button data-retry title="Send it again">${I("resend", 11)}retry</button><button data-edit title="Back into the input">${I("pencil", 11)}edit</button>` : "";
    return `<div class="tmsg req${m.pending ? " pending" : ""}" data-mid="${esc(m.id)}"><div class="bubble">${body}${filesHtml(v, m)}` +
      `<div class="meta">${st}${m.edited ? "<span>edited</span>" : ""}<span class="when">${when}</span></div></div>${reactsHtml(m)}${more}</div>`;
  }
  const pa = prev && prev.author || {};
  const grouped = !!prev && !pa.me && !prev.pending && pa.name === a.name && m.time && prev.time && m.time >= prev.time && m.time - prev.time < 300;
  const head = grouped ? "" : `<div class="head tauthor">${avatarHtml(a)}<span class="st">${esc(a.name || "Someone")}</span><span class="tm">${when}</span>${m.edited ? `<span class="ted">edited</span>` : ""}</div>`;
  return `<div class="tmsg ans${grouped ? " cont" : ""}" data-mid="${esc(m.id)}">${head}${body}${filesHtml(v, m)}${reactsHtml(m)}${more}</div>`;
}
function firstVisible(v) {
  const top = v.box.getBoundingClientRect().top;
  const el = [...v.msgs.children].find(c => c.getBoundingClientRect().bottom > top + 1);
  return el && { el, off: el.getBoundingClientRect().top - top };
}
function drawThread(v) {
  const d = v.data;
  if (!d) { v.msgs.innerHTML = `<div class="empty">Loading…</div>`; return; }
  const msgs = [...v.older, ...d.messages];
  v.top.innerHTML = (moreOlder(v) ? `<div class="earlier"><button class="btn sm" data-older>${v.loadingOlder ? "Loading…" : "Show earlier messages"}</button></div>` : "") +
    (d.sub || d.url ? `<div class="tsub">${esc(d.sub || "")}${d.url ? `${d.sub ? " · " : ""}<a href="${esc(d.url)}" target="_blank" rel="noopener">Open at the source</a>` : ""}</div>` : "");
  if (!msgs.length) {
    v.msgs.innerHTML = d.loading ? `<div class="empty">Loading…</div>` : d.error ? `<div class="empty"><h2>Not available</h2>${esc(d.error)}</div>` : `<div class="empty">No messages yet.</div>`;
  } else {
    const pin = v.list.atEnd ? null : firstVisible(v);
    for (const e of [...v.msgs.children]) if (!e.dataset.mid) e.remove();
    const have = new Map([...v.msgs.children].map(e => [e.dataset.mid, e]));
    let i = 0;
    for (const [k, m] of msgs.entries()) {
      const html = tmsgHtml(v, m, msgs[k - 1]);
      let e = have.get(m.id);
      if (e && e._html !== html) { const n = htmlEl(html); e.replaceWith(n); e = n; }
      if (!e) e = htmlEl(html);
      e._html = html; have.delete(m.id);
      if (v.msgs.children[i] !== e) v.msgs.insertBefore(e, v.msgs.children[i] || null);
      i++;
    }
    for (const e of have.values()) e.remove();
    if (v.list.atEnd) v.list.stick();
    else if (pin && pin.el.isConnected) {
      v.box.scrollTop += (pin.el.getBoundingClientRect().top - v.box.getBoundingClientRect().top - pin.off) / rectScale();
      v.list.saveAnchor();
    }
  }
  v.list.updateJump();
  drawComposer(v);
  if (FIND.open && FIND.target && FIND.target.root === v.root) refreshFind();
}
/* ---- older messages: scrolled to the top (or the button), through the plugin's service; they stay in this view */
async function loadOlder(v) {
  if (v.loadingOlder || !moreOlder(v)) return;
  const first = [...v.older, ...v.data.messages].find(m => !m.pending);
  if (!first) return;
  v.loadingOlder = true; drawThread(v);
  try {
    const r = await api("GET", `${threadUrl(v)}?before=${encodeURIComponent(first.id)}`);
    const have = new Set([...v.older, ...v.data.messages].map(m => m.id));
    v.older = [...r.messages.filter(m => !have.has(m.id)), ...v.older];
    v.olderMore = !!r.more && r.messages.length > 0;
  } catch (e) { v.olderMore = false; toast(`Older messages: ${e.message}`); }
  v.loadingOlder = false;
  drawThread(v);
}
/* ---- live: the store's changes, in order (a gap: the whole thread again) */
function insertMsg(v, x) {
  const ms = v.data.messages, i = ms.findIndex(y => y.id === x.id);
  if (i >= 0) { ms[i] = x; return; }
  const p = x.pending ? -1 : ms.findIndex(y => y.pending);  // (new ones go before the replies still being sent)
  if (p < 0) ms.push(x); else ms.splice(p, 0, x);
}
function threadChanged(v, ev) {
  if (v.loading) { v.missed = Math.max(v.missed || 0, ev.rev); return; }
  if (!v.data || ev.rev <= v.rev) return;
  const c = ev.change || {};
  if (ev.rev !== v.rev + 1 || c.whole) { v.load(); return; }
  v.rev = ev.rev;
  for (const k of ["title", "sub", "url", "composer", "more", "unread", "error"]) if (k in c) v.data[k] = c[k];
  if (c.remove) { const gone = new Set(c.remove); v.data.messages = v.data.messages.filter(m => !gone.has(m.id)); }
  for (const x of c.update || []) { const i = v.data.messages.findIndex(m => m.id === x.id); if (i >= 0) v.data.messages[i] = x; }
  for (const [old, x] of Object.entries(c.replace || {})) {
    const i = v.data.messages.findIndex(m => m.id === old);
    if (i >= 0) v.data.messages[i] = x; else insertMsg(v, x);
    delete v.sent[x.client_id];
  }
  for (const x of c.append || []) insertMsg(v, x);
  if (c.title) setTabTitle(v.path, c.title);
  drawThread(v);
}
/* ---- the reply editor */
function drawComposer(v) {
  const d = v.data || {}, wrap = v.el.querySelector(".pcomposer"), no = v.el.querySelector(".pnosend");
  const off = d.composer && d.composer.enabled === false;
  wrap.hidden = off || !PUI.local;
  no.hidden = off || PUI.local;
  if (!PUI.local) no.textContent = `Replies are written on ${v.spec.plugin}'s own machine.`;
  if (wrap.hidden) return;
  const ph = (d.composer && d.composer.placeholder) || `Reply in ${viewTitle(v)}`;
  if (!v.ed && window.CM) {
    v.ed = CM.createEditor({
      parent: v.el.querySelector(".pinputhost"), doc: tdrafts()[v.path] || "", path: "reply.md",
      composer: { placeholder: ph },
      preview: { resolve: src => /^https:/i.test(src) ? src : "", inline: s => md.renderInline(s) },  // (its own text: no local files)
      zoom: url => openLightbox(url), onOpenLink: url => openUrl(url),
      onFiles: files => attachThreadFiles(v, files),
      onChange: () => { saveTDraft(v.path, v.ed.getDoc()); updateTSend(v); },
    });
    const c = v.ed.view.contentDOM;
    c.addEventListener("focus", () => wrap.classList.add("focus"));
    c.addEventListener("blur", () => wrap.classList.remove("focus"));
    v.ph = ph;
  } else if (v.ed && v.ph !== ph) { v.ed.setPlaceholder(ph); v.ph = ph; }
  updateTSend(v);
}
const updateTSend = v => { v.el.querySelector(".psend").disabled = !((v.ed && v.ed.getDoc().trim()) || v.files.length); };
function drawChips(v) {
  const box = v.el.querySelector(".pchips");
  box.hidden = !v.files.length;
  box.innerHTML = v.files.map((f, i) => `<span class="chip pchip" title="${esc(f.path)}">${f.image ? `<img src="${esc(rawUrl(f.path))}" alt="">` : I("file", 12)}<span class="nm">${esc(f.name)}</span>` +
    `<button class="x" data-unchip="${i}" title="Remove">${I("x", 10)}</button></span>`).join("");
  updateTSend(v);
}
/* Pasted, dropped or attached files: uploaded to this machine (whatever the plugin), sent as files[] (chips). */
async function attachThreadFiles(v, files) {
  for (const f of [...files].filter(f => f && f.size)) {
    try {
      const name = f.name || (f.type === "image/png" ? "pasted.png" : "pasted");
      const r = await api("POST", "/api/upload", { name, data: await fileB64(f) });
      v.files.push({ name, path: r.path, image: IMG_RE.test(name) || /^image\//.test(f.type || "") });
    } catch (e) { toast(`${f.name || "File"}: ${e.message}`); }
  }
  drawChips(v);
  if (v.ed) v.ed.focus();
}
function threadSend(v) {
  const text = v.ed ? v.ed.getDoc() : "", files = v.files.slice();
  if (!text.trim() && !files.length) return;
  const cid = "c" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  const local = { id: "pending:" + cid, client_id: cid, author: { name: "", me: true }, time: Date.now() / 1000, markdown: text,
                  ...(files.length ? { files: files.map(f => ({ name: f.name })) } : {}), pending: "sending" };
  v.sent[cid] = { markdown: text, files, local };
  insertMsg(v, local);  // at once, "Sending…"
  if (v.ed) v.ed.setDoc("", false);
  v.files = []; drawChips(v); saveTDraft(v.path, "");
  v.list.setAtEnd(true);
  drawThread(v);
  v.list.latest();
  postSend(v, cid);
}
async function postSend(v, cid, body) {
  const s = v.sent[cid];
  try {
    await api("POST", threadUrl(v) + "/send", body || { markdown: s.markdown, files: s.files.map(f => ({ name: f.name, path: f.path })), client_id: cid });
    if (s) delete s.local;  // (the server keeps it from now on)
  } catch (e) {  // never reached the server (or refused): "Not sent" here, with Retry and Edit
    const m = v.data.messages.find(x => x.client_id === cid && x.pending);
    if (m) { m.pending = "failed"; m.error = e.message; drawThread(v); }
  }
}
function retrySend(v, m) {
  m.pending = "sending"; m.error = "";
  drawThread(v);
  const s = v.sent[m.client_id];
  postSend(v, m.client_id, s ? null : { markdown: m.markdown, files: [], client_id: m.client_id });  // (same client_id: the service can tell)
}
async function editPending(v, m) {
  const s = v.sent[m.client_id];
  if (v.ed) { const cur = v.ed.getDoc(); v.ed.setDoc(cur.trim() ? cur.replace(/\s+$/, "") + "\n\n" + m.markdown : m.markdown); v.ed.focus(); }
  if (s) { v.files.push(...s.files); drawChips(v); }
  delete v.sent[m.client_id];
  v.data.messages = v.data.messages.filter(x => x !== m);
  drawThread(v);
  api("POST", threadUrl(v) + "/discard", { client_id: m.client_id }).catch(() => {});
}
/* ---- a message's context menu */
const msgText = (v, m) => { const el = v.msgs.querySelector(`.tmsg[data-mid="${CSS.escape(m.id)}"] .md`); return el ? el.innerText.trim() : m.markdown || ""; };
const publicMsg = m => Object.fromEntries(Object.entries(m).filter(([k]) => !["pending", "error"].includes(k)));
function messageMenu(v, m, where) {
  const title = viewTitle(v), plugin = v.spec.plugin, url = m.url || (v.data && v.data.url) || "";
  const ss = [...S.sessions.values()].filter(s => !s.fleet).sort((a, b) => (b.id === S.current) - (a.id === S.current));
  const acts = PUI.local ? PUI.messages.filter(x => x.plugin === plugin) : [];
  menu([
    { label: "Copy", run: () => copyText(msgText(v, m)) },
    { label: "Copy as Markdown", run: () => copyText(m.markdown || "") },
    url ? { label: "Open at the source", run: () => openUrl(url) } : null,
    ss.length ? { sep: true } : null,
    ss.length ? { label: "Quote in a session…", sub: () => ss.map(s => ({ label: s.name, key: s.id === S.current ? "current" : "", run: () => quoteTo({ text: m.markdown || msgText(v, m), ref: title }, s.id) })) } : null,
    ss.length ? { label: "Send to a session…", sub: () => ss.map(s => ({ label: s.name, key: s.id === S.current ? "current" : "", run: () => sendToSession(v, m, s) })) } : null,
    acts.length ? { sep: true } : null,
    ...acts.map(a => ({ label: a.title, run: () => runPluginAction(a, { kind: v.spec.kind, key: v.spec.key, message: publicMsg(m) }) })),
  ].filter(Boolean), where);
}
/* Send to a session: the message and the thread's last 30 messages (20 KB at most) as a new request, via the thread. */
async function sendToSession(v, m, s) {
  const title = viewTitle(v), who = a => a && a.me ? "me" : (a && a.name) || "someone";
  const quote = md => md.replace(/\s+$/, "").split("\n").map(l => "> " + l).join("\n");
  const head = `From ${title} (${v.spec.plugin}), ${who(m.author)} wrote:\n\n${quote(m.markdown || "")}\n\n`;
  const bytes = t => new TextEncoder().encode(t).length;
  let rest = [...v.older, ...v.data.messages].filter(x => !x.pending).slice(-30).map(x => `**${who(x.author)}** (${dayTime(x.time)}):\n${x.markdown}`);
  const ctx = () => rest.length ? `The thread (its last ${rest.length} message${rest.length === 1 ? "" : "s"}):\n\n${rest.join("\n\n")}` : "";
  while (rest.length && bytes(head + ctx()) > 20000) rest.shift();
  let text = head + ctx();
  while (bytes(text) > 20000) text = text.slice(0, Math.floor(text.length * 0.9));
  const url = v.data && /^https?:\/\//.test(v.data.url || "") ? v.data.url : "";
  try {
    await api("POST", `/api/sessions/${s.id}/send`, { text, origin: { label: title, plugin: v.spec.plugin, ...(url ? { url } : {}) } });
    toast(`Sent to ${s.name}`, EMBED ? null : () => switchTo(s.id));
  } catch (e) { toast(`Send to ${s.name}: ${e.message}`); }
}

/* ===================================================================== a doc */
function docView(path, spec) {
  const v = { path, spec, kind: "doc", data: null, rev: 0, list: null };
  v.el = htmlEl(`<div class="pdocv"><div class="pdocbox" tabindex="-1"><div class="col"><div class="md pdocmd"></div></div></div></div>`);
  v.box = v.el.querySelector(".pdocbox"); v.root = v.el.querySelector(".pdocmd");
  v.show = () => { clearViewUnread(path); if (!v.data && !v.loading) v.load(); };
  v.load = async (fresh = false) => {
    v.loading = true;
    try { v.data = await api("GET", `/api/plugins/${spec.plugin}/docs/${encodeURIComponent(spec.doc)}${fresh ? "?fresh=1" : ""}`); v.rev = v.data.rev || 0; setTabTitle(path, v.data.title); }
    catch (e) { v.data = { error: e.message }; }
    v.loading = false;
    drawDoc(v);
    if (v.missed > v.rev) { v.missed = 0; v.load(); }  // (it changed while it loaded)
  };
  v.reload = () => v.load(true);
  v.focus = () => v.box.focus({ preventScroll: true });
  v.url = () => "";
  v.destroy = () => v.el.remove();
  v.el.addEventListener("click", e => viewClicks(v, e));
  return v;
}
function drawDoc(v) {
  const d = v.data || {}, top = v.box.scrollTop;
  v.root.innerHTML = d.markdown ? renderPluginMd(d.markdown, v.spec.plugin, PUI.schemes[v.spec.plugin] || [])
    : `<div class="empty">${d.loading || v.loading ? "Loading…" : d.error ? `<h2>Not available</h2>${esc(d.error)}` : "Nothing here yet."}</div>`;
  v.box.scrollTop = top;
  if (FIND.open && FIND.target && FIND.target.root === v.root) refreshFind();
}

/* ===================================================================== a list (an item source) */
/* Its items are fetched when it shows (and when the source changes: plugin-items), filtered in the page. The left
   panel draws them like the Sessions list (left.js: rows, the filter box, its keys and menus); a panel's list in the
   side pane is the same rows in a box of its own. */
const PLISTS = new Map();  // "plugin:source" → {items, at}
async function loadPluginList(plugin, source) {
  const k = `${plugin}:${source}`;
  try {
    const x = (await api("GET", `/api/palette?plugin=${encodeURIComponent(plugin)}&source=${encodeURIComponent(source)}`)).sources[0];
    PLISTS.set(k, { items: x ? x.items : [], at: Date.now() });
  } catch { if (!PLISTS.has(k)) PLISTS.set(k, { items: [], at: Date.now(), error: true }); }
  renderLeft();
  for (const v of PVIEWS.values()) if (v.kind === "list" && `${v.spec.plugin}:${v.spec.source}` === k) drawList(v);
}
const pluginListItems = (plugin, source) => (PLISTS.get(`${plugin}:${source}`) || {}).items;
function pluginListUnread(sb) { return (pluginListItems(sb.plugin, sb.source) || []).filter(x => x.unread).length; }
/* Rows: by group (an item's `group`; the ungrouped first), newest first; filtered: the best matches, flat. */
function pluginListRows(plugin, source, q) {
  const items = pluginListItems(plugin, source) || [];
  if (q) {
    return items.map(it => ({ it, sc: Math.max(fuzzy(q, it.label), fuzzy(q, it.match || "") - 1, fuzzy(q, it.detail || "") - 2, fuzzy(q, it.tag || "") - 2) }))
      .filter(x => x.sc >= 0).sort((a, b) => b.sc - a.sc).map(({ it }) => ({ kind: "pitem", k: "i:" + it.key, it }));
  }
  const groups = new Map([["", []]]);
  for (const it of items) { const g = it.group || ""; if (!groups.has(g)) groups.set(g, []); groups.get(g).push(it); }
  const rows = [];
  for (const [g, its] of groups) {
    if (!its.length) continue;
    if (g) rows.push({ kind: "pgrp", k: "g:" + g, name: g, count: String(its.length) });
    for (const it of its) rows.push({ kind: "pitem", k: "i:" + it.key, it });
  }
  return rows;
}
function pluginRowHtml(r, kc) {
  if (r.kind === "pgrp") return `<div class="prow grp${kc}" data-k="${esc(r.k)}"><span class="ix"></span><span class="nm">${esc(r.name)}</span><span class="fold" style="visibility:hidden"></span><span class="cnt">${esc(r.count)}</span></div>`;
  const it = r.it;
  return `<div class="prow ss pitem${kc}${it.unread ? " unread" : ""}" data-k="${esc(r.k)}" data-tip="${esc(it.label)}">` +
    `<span class="si${it.unread ? " pnew" : ""}" title="${it.unread ? "Unread" : ""}"></span><span class="nm">${esc(it.label)}</span>` +
    `${it.tag ? `<span class="mtag">${esc(it.tag)}</span>` : ""}<span class="tm">${esc(ago(it.time))}</span></div>`;
}
/* Opening one: its `open` (a tab of the plugin, or a URL), else what its source does (finder.js). */
function openPluginItem(plugin, source, it, { side = false } = {}) {
  const src = PUI.palette.find(x => x.plugin === plugin && x.id === source) || { plugin, id: source, title: source, open: "tab" };
  const o = it.open;
  if (o && o.url) { openUrl(o.url); return; }
  if (o && o.kind) {
    if (side) openSide({ kind: "pview", path: pluginTabPath(plugin, o.kind, o.key), title: it.label });
    else openPluginTab(plugin, o.kind, o.key, { title: it.label });
    return;
  }
  openPaletteItem(src, it, side);
}
function itemTarget(plugin, source, it) {  // the tab kind and key an item opens, if it opens one
  if (it.open) return it.open.kind ? { kind: it.open.kind, key: it.open.key } : null;
  const src = PUI.palette.find(x => x.plugin === plugin && x.id === source) || {};
  return src.open === "tab" || !src.open ? { kind: it.kind || src.kind || "", key: it.key } : null;
}
function pluginItemMenu(plugin, source, it, where) {
  const tgt = itemTarget(plugin, source, it), k = tgt && pluginKind(plugin, tgt.kind);
  menu([
    { label: "Open", key: "⏎", run: () => openPluginItem(plugin, source, it) },
    k ? { label: "Open in the side tab", run: () => openPluginItem(plugin, source, it, { side: true }) } : null,
    k && PUI.local && k.menu.length ? { sep: true } : null,
    ...(k && PUI.local ? k.menu : []).map(m => ({ label: m.title, run: () => runPluginAction({ plugin, title: m.title, args: m.args }, { kind: tgt.kind, key: tgt.key }) })),
    it.url ? { sep: true } : null,
    it.url ? { label: "Open the link", run: () => openUrl(it.url) } : null,
  ].filter(Boolean), where);
}
/* A panel's list in the side pane: the same rows; ↑↓ ⏎, a click opens, right-click its menu. */
function listView(path, spec) {
  const v = { path, spec, kind: "list", cur: 0, list: null, root: null };
  v.el = htmlEl(`<div class="plistv"><div class="plistbox" tabindex="0"></div></div>`);
  v.box = v.el.querySelector(".plistbox"); v.root = v.box;
  v.rows = () => pluginListRows(spec.plugin, spec.source, "").filter(r => r.kind === "pitem");
  v.show = () => { drawList(v); loadPluginList(spec.plugin, spec.source); };
  v.load = v.reload = () => loadPluginList(spec.plugin, spec.source);
  v.focus = () => v.box.focus({ preventScroll: true });
  v.url = () => "";
  v.destroy = () => v.el.remove();
  v.box.addEventListener("click", e => {
    const el = e.target.closest(".prow[data-k]"), rs = v.rows(), i = el ? rs.findIndex(r => r.k === el.dataset.k) : -1;
    if (i >= 0) { v.cur = i; drawList(v); openPluginItem(spec.plugin, spec.source, rs[i].it); }
  });
  v.box.addEventListener("contextmenu", e => {
    const el = e.target.closest(".prow[data-k]"), r = el && v.rows().find(x => x.k === el.dataset.k);
    if (r) { e.preventDefault(); pluginItemMenu(spec.plugin, spec.source, r.it, { at: { x: e.clientX, y: e.clientY } }); }
  });
  v.box.addEventListener("keydown", e => {
    const rs = v.rows(), mv = { ArrowDown: 1, ArrowUp: -1 }[e.key];
    if (mv) { e.preventDefault(); v.cur = Math.max(0, Math.min(rs.length - 1, v.cur + mv)); drawList(v); }
    else if (e.key === "Enter" && rs[v.cur]) { e.preventDefault(); openPluginItem(spec.plugin, spec.source, rs[v.cur].it); }
  });
  return v;
}
function drawList(v) {
  const rows = pluginListRows(v.spec.plugin, v.spec.source, "");
  const items = rows.filter(r => r.kind === "pitem");
  v.cur = Math.min(v.cur, Math.max(0, items.length - 1));
  const cur = items[v.cur] && items[v.cur].k;
  v.box.innerHTML = rows.length ? rows.map(r => pluginRowHtml(r, r.k === cur ? " kc" : "")).join("")
    : `<div class="pempty">${pluginListItems(v.spec.plugin, v.spec.source) ? "Nothing here yet." : "Loading…"}</div>`;
}

/* ===================================================================== live (live.js) */
function pviewThreadEvent(ev) {
  for (const v of PVIEWS.values()) if (v.kind === "thread" && v.spec.plugin === ev.plugin && v.spec.kind === ev.kind && v.spec.key === ev.key) threadChanged(v, ev);
  if (EMBED) return;
  const path = pluginTabPath(ev.plugin, ev.kind, ev.key), c = ev.change || {};
  const theirs = (c.append || []).concat(Object.values(c.replace || {})).some(x => !(x.author || {}).me) || c.unread === true;
  const shown = S.file === path && !document.hidden;
  let ch = false;
  for (const t of Object.values(S.ftabs).flat()) {
    if (t.path !== path) continue;
    if (theirs && !shown && !t.unread) { t.unread = true; ch = true; }
    if (c.unread === false && t.unread) { t.unread = false; ch = true; }
    if (c.title && t.title !== c.title.slice(0, 80)) { t.title = c.title.slice(0, 80); ch = true; }
  }
  if (ch) { saveUi(); renderTabs(); }
}
function pviewDocEvent(ev) {
  for (const v of PVIEWS.values()) {
    if (v.kind !== "doc" || v.spec.plugin !== ev.plugin || v.spec.doc !== ev.id || ev.rev <= v.rev) continue;
    if (v.loading) v.missed = Math.max(v.missed || 0, ev.rev); else v.load();
  }
}
function pviewItemsEvent(ev) { if (PLISTS.has(`${ev.plugin}:${ev.source}`)) loadPluginList(ev.plugin, ev.source); }
/* The plugins (re)loaded, or the server said hello: views whose plugin or kind changed go (drawn again if shown);
   the rest fetch what they show again (a gap in the events). The left lists' items come for their counts. */
function pviewsReloaded() {
  for (const [id, v] of [...PVIEWS]) {
    const spec = viewSpec(v.path);
    if (!spec || specSig(spec) !== v.sig) { const shown = !v.el.hidden && v.el.isConnected; dropPView(id); if (shown && S.file === v.path && !id.startsWith("sb:")) showPluginTab(v.path); }
    else if (v.data || v.kind === "list") v.load();
  }
  for (const sb of PUI.sidebars) if (sb.view === "list") loadPluginList(sb.plugin, sb.source);
  if (isPluginTab(S.file) && !PVIEWS.has(S.file) && viewSpec(S.file)) showPluginTab(S.file);  // (restored before the plugins were known)
}
function focusPView() { const v = pviewShown(); if (v) v.focus(); }
