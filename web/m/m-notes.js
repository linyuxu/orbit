"use strict";
/* ===================================================================== Notes
   The vault (an Obsidian vault): Today (the journal note, POST /api/notes/week: the server says which file) and
   Add to today on top, a search, then the vault by folder. A note is drawn as Markdown on paper, its [[links]] open
   the note they name (Obsidian's rules, resolved by the server; a missing one is offered to create), its backlinks
   under it; Edit opens the plain editor. */
TAB_ROOT.notes = () => pushPage(notesRootPage(), "notes");
const NOTES = { root: "", list: null, at: 0 };
let MD_WIKI = "";  // the note being drawn (its [[links]] are links); "" elsewhere: an answer's [[x]] stays text
const isNotePath = p => !!NOTES.root && String(p || "").startsWith(NOTES.root + "/") && /\.md$/i.test(p);
const noteStem = p => basename(p).replace(/\.md$/i, "");
async function loadNotes(force = false) {
  if (!force && NOTES.list && Date.now() - NOTES.at < 4000) return;
  const d = await api("GET", "/api/notes/list");
  NOTES.root = d.root; NOTES.list = d.notes || []; NOTES.at = Date.now();
}
/* Where a [[link]] goes, as the server resolves it (notes.resolve): a path a/b names that note; a bare name, the
   note of that name in the same folder, else the shallowest. Here only to draw a missing link faded. */
function resolveLocal(name, from) {
  const list = NOTES.list || [], target = name.replace(/\.md$/i, "").toLowerCase();
  if (target.includes("/")) return list.find(n => n.toLowerCase() === target + ".md") || null;
  const hits = list.filter(n => noteStem(n).toLowerCase() === target);
  if (!hits.length) return null;
  const here = from ? relTo(NOTES.root, dirname(from)) : "";
  return hits.find(n => (n.includes("/") ? dirname(n) : "") === (here === dirname(from) ? "" : here)) ||
    hits.sort((a, b) => a.split("/").length - b.split("/").length || a.length - b.length)[0];
}
/* [[name]], [[name#heading]], [[name|shown]]; ![[image.png]] shows the image (the vault's attachment rules). */
md.inline.ruler.before("link", "wikilink", (state, silent) => {
  if (!MD_WIKI) return false;
  const src = state.src, embed = src[state.pos] === "!", at = state.pos + (embed ? 1 : 0);
  if (src[at] !== "[" || src[at + 1] !== "[") return false;
  const end = src.indexOf("]]", at + 2), inner = end > 0 ? src.slice(at + 2, end) : "";
  if (!inner.trim() || /[\n[]/.test(inner)) return false;
  if (!silent) {
    const bar = inner.indexOf("|"), target = (bar >= 0 ? inner.slice(0, bar) : inner).trim(), alias = bar >= 0 ? inner.slice(bar + 1).trim() : "";
    const hash = target.indexOf("#"), name = (hash >= 0 ? target.slice(0, hash) : target).trim(), head = hash >= 0 ? target.slice(hash + 1).trim() : "";
    const t = state.push("html_inline", "", 0);
    if (embed && /\.(png|jpe?g|gif|webp|svg|bmp|avif|heic)$/i.test(name))
      t.content = `<img src="${esc(withToken(`/api/embed?name=${encodeURIComponent(name)}&from=${encodeURIComponent(MD_WIKI)}`))}" alt="${esc(alias || name)}">`;
    else t.content = `<a class="wiki${!name || resolveLocal(name, MD_WIKI) ? "" : " missing"}" href="#" data-wiki="${esc(name)}" data-head="${esc(head)}">${esc(alias || target)}</a>`;
  }
  state.pos = end + 2;
  return true;
});
/* Follow a [[link]]: the note it names; a missing one, created when the user says so. */
async function openWiki(name, from, head, tab = AREA.cur) {
  if (!name) { const p = topPage(tab); if (p && head) scrollToHeading(p, head); return; }
  try {
    const r = await api("GET", `/api/notes/resolve?name=${encodeURIComponent(name)}&from=${encodeURIComponent(from || "")}`);
    if (r.path) { pushPage(notePage(r.path, { heading: head }), tab); return; }
    actionSheet(`No note “${name}” yet`, [{ label: `Create “${name}”`, icon: "plus", run: async () => {
      try { const c = await api("POST", "/api/notes/create", { name }); await loadNotes(true).catch(() => {}); pushPage(notePage(c.path), tab); toast(`Created the note “${name}”`); }
      catch (e) { toast(e.message); }
    } }]);
  } catch (e) { if (!(e instanceof NotPaired)) toast(e.message); }
}
function scrollToHeading(page, heading) {
  const want = String(heading || "").replace(/^#+\s*/, "").trim().toLowerCase();
  const h = want && $$(".doc :is(h1,h2,h3,h4,h5,h6)", page.body).find(x => x.textContent.trim().toLowerCase() === want);
  if (h) page.scroll.scrollTop += h.getBoundingClientRect().top - page.scroll.getBoundingClientRect().top - 12;
}

/* ===================================================================== the root: Today, search, the vault */
function notesRootPage() {
  const page = makePage({ root: true, title: "Notes", right: navBtn("new", PLUS_SVG, "New note") });
  page.folder = "";
  const d = new Date();
  page.body.innerHTML = `<div class="today"><button class="tbig" data-today>${I("calendar", 20)}<span class="tx"><span class="t1">Today</span>` +
    `<span class="t2">${esc(d.toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric" }))}</span></span></button>` +
    `<button class="tadd" data-add>${I("plus", 18)}<span>Add to today</span></button></div>` +
    `<label class="search">${I("search", 16)}<input type="search" placeholder="Search notes" enterkeyhint="search" autocapitalize="off" autocorrect="off" spellcheck="false"></label>` +
    `<div class="nlist">${loadingHtml()}</div>`;
  const box = $(".nlist", page.body), q = $(".search input", page.body);
  page.render = () => { if (!q.value.trim()) box.innerHTML = notesFolderHtml(""); };
  page.onShow = () => loadNotes().then(page.render).catch(() => {});
  page.acts.new = () => newNote(page);
  let seq = 0;
  const search = async () => {
    const text = q.value.trim(), my = ++seq;
    if (!text) { page.render(); return; }
    const terms = text.toLowerCase().split(/\s+/);
    const byName = (NOTES.list || []).filter(n => terms.every(t => n.toLowerCase().includes(t))).slice(0, 20);
    const draw = hits => {
      if (my !== seq) return;
      const seen = new Set(byName.map(n => NOTES.root + "/" + n));
      const more = (hits || []).filter(h => !seen.has(h.path));
      box.innerHTML = byName.length || more.length ? `<div class="rows">` +
        byName.map(n => noteRow(n)).join("") +
        more.map(h => navRow({ attrs: `data-note="${esc(h.path)}"`, icon: I("note", 18), name: h.title, sub: esc(plain(wikiPlain(h.snippet || ""))), chev: false, cls: "hit" })).join("") + `</div>`
        : hits ? emptyBox("No notes match", `Nothing holds “${esc(text)}”.`) : loadingHtml();
    };
    draw(null);
    try { const r = await api("GET", `/api/search?q=${encodeURIComponent(text)}&limit=30`); draw(r.notes || []); } catch { draw([]); }
  };
  q.addEventListener("input", () => { clearTimeout(search.t); search.t = setTimeout(search, 220); });
  q.addEventListener("keydown", e => { if (e.key === "Enter") q.blur(); });
  page.body.addEventListener("click", e => {
    if (e.target.closest("[data-today]")) openToday(page.tab);
    else if (e.target.closest("[data-add]")) addToToday();
    else notesListClick(e, page);
  });
  loadNotes(true).then(page.render).catch(e => { if (!(e instanceof NotPaired)) box.innerHTML = emptyBox("Cannot read the notes", esc(e.message)); });
  return page;
}
function noteRow(rel, sub = "") {
  return navRow({ attrs: `data-note="${esc(NOTES.root + "/" + rel)}"`, icon: I("note", 18), name: noteStem(rel), sub: sub || (rel.includes("/") ? esc(dirname(rel)) : ""), chev: false, cls: "leaf" });
}
/* A folder of the vault: its folders (how many notes in each), then its notes. */
function notesFolderHtml(folder) {
  const pre = folder ? folder + "/" : "", dirs = new Map(), notes = [];
  for (const n of NOTES.list || []) {
    if (!n.startsWith(pre)) continue;
    const rest = n.slice(pre.length), i = rest.indexOf("/");
    if (i < 0) notes.push(n); else { const d = pre + rest.slice(0, i); dirs.set(d, (dirs.get(d) || 0) + 1); }
  }
  if (!dirs.size && !notes.length) return emptyBox(folder ? "No notes here" : "No notes yet", "<b>+</b> above makes one.");
  const az = (a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
  return `<div class="rows">` + [...dirs.keys()].sort(az).map(d => navRow({ attrs: `data-folder="${esc(d)}"`, icon: folderIcon(false), name: basename(d), hint: String(dirs.get(d)) })).join("") +
    notes.sort((a, b) => az(noteStem(a), noteStem(b))).map(n => noteRow(n, " ")).join("") + `</div>`;
}
function notesListClick(e, page) {
  const f = e.target.closest("[data-folder]"), n = e.target.closest("[data-note]");
  if (f) pushPage(notesFolderPage(f.dataset.folder), page.tab);
  else if (n) pushPage(notePage(n.dataset.note), page.tab);
}
function notesFolderPage(folder) {
  const page = makePage({ title: basename(folder), sub: dirname(folder) === folder ? "" : dirname(folder), right: navBtn("new", PLUS_SVG, "New note here") });
  page.folder = folder;
  page.render = () => { page.body.innerHTML = notesFolderHtml(folder); };
  page.onShow = () => loadNotes().then(page.render).catch(() => {});
  page.acts.new = () => newNote(page);
  page.body.addEventListener("click", e => notesListClick(e, page));
  page.render();
  return page;
}
function newNote(page) {
  askText({ title: "New note", ok: "Create", label: page.folder ? `In ${page.folder}` : "", placeholder: "Name (a/b makes folders)",
    onOk: async v => {
      const r = await api("POST", "/api/notes/create", { name: (page.folder ? page.folder + "/" : "") + v.trim() });
      await loadNotes(true).catch(() => {});
      const np = pushPage(notePage(r.path), page.tab);
      if (r.created) np.loaded.then(() => editFile(np, { focusEnd: true }));
    } });
}

/* ===================================================================== a note */
function notePage(path, { heading = "" } = {}) {
  const rel = relTo(NOTES.root, path);
  const page = makePage({ title: noteStem(path), sub: rel.includes("/") ? dirname(rel) : "", cls: "paperpage", right: navBtn("more", MORE_SVG, "Note actions") + navTxt("edit", "Edit") });
  page.path = path;
  page.show = f => {
    page.f = f;
    page.body.innerHTML = `<div class="doc md">${renderDoc(f.text || "", path)}</div><div class="blinks"></div>`;
    loadBacklinks(page);
  };
  page.load = async (head = "") => {
    try {
      if (!NOTES.list) await loadNotes().catch(() => {});
      page.show(await api("GET", "/api/file?path=" + encodeURIComponent(path)));
      if (head) requestAnimationFrame(() => scrollToHeading(page, head));
    } catch (e) { if (!(e instanceof NotPaired)) page.body.innerHTML = emptyBox(`Cannot open ${noteStem(path)}`, esc(e.message)); }
  };
  page.body.innerHTML = loadingHtml();
  page.onShow = () => { if (page.f) page.load(); };  // (back on it: what changed, links made meanwhile)
  page.acts.edit = () => editFile(page);
  page.acts.more = () => actionSheet(noteStem(path), [
    { label: "Copy [[link]]", icon: "link", run: () => copyText(`[[${noteStem(path)}]]`, "Link copied") },
    { label: "Copy text", icon: "copy", run: () => copyText((page.f || {}).text || "") },
    { label: "Copy path", icon: "copy", run: () => copyText(path, "Path copied") },
    { label: "Show the folder", icon: "folder", run: () => pushPage(folderPage(dirname(path)), page.tab) },
    { label: "Reload", icon: "refresh", run: () => page.load() },
  ]);
  page.body.addEventListener("click", e => docClick(e, page));
  page.loaded = page.load(heading);
  return page;
}
const wikiPlain = t => String(t || "").replace(/!?\[\[([^\]|#]*)(?:#[^\]|]*)?(?:\|([^\]]*))?\]\]/g, (_, a, b) => b || a);  // (a line as read)
async function loadBacklinks(page) {
  const box = $(".blinks", page.body); if (!box) return;
  let links = [];
  try { links = (await api("GET", "/api/notes/backlinks?path=" + encodeURIComponent(page.path))).links || []; } catch { return; }
  if (!page.el.isConnected && !AREA.stacks[page.tab].includes(page)) return;
  box.innerHTML = `<div class="shead">${links.length ? `${links.length} backlink${links.length === 1 ? "" : "s"}` : "No backlinks"}</div>` +
    (links.length ? `<div class="rows">` + links.map(l => navRow({ attrs: `data-note="${esc(l.path)}"`, icon: I("link", 16), name: noteStem(l.path), sub: esc(wikiPlain(l.text)), chev: false })).join("") + `</div>` : "");
}

/* ===================================================================== Today */
async function openToday(tab = "notes") {
  let r;
  try { r = await api("POST", "/api/notes/week"); } catch (e) { if (!(e instanceof NotPaired)) toast(e.message); return; }
  if (AREA.cur !== tab) switchTab(tab);
  const top = topPage(tab);
  if (top.path === r.path && top.load) { top.load(r.heading); return; }  // (on screen already: what is there now)
  pushPage(notePage(r.path, { heading: r.heading }), tab);
}
function addToToday() {
  askText({ title: "Add to today", ok: "Add", area: true, placeholder: "A thought, a link, a to-do…",
    onOk: async v => {
      const r = await api("POST", "/api/notes/today", { text: v, source: "iPhone" });
      toast("Added to today");
      for (const tab of ["notes", "files"]) for (const p of AREA.stacks[tab]) if (p.path === r.path && p.load && !p.dirty) p.load();
    } });
}
