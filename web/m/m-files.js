"use strict";
/* ===================================================================== Files
   The desktop's Files panel for a phone: the session folders (the most recent first) and Home, then a folder a page
   (folders first, then files), a file a page: code highlighted (hljs), Markdown drawn, images shown; Edit opens the
   plain editor (m-tabs.js), which saves as the desktop does. Another machine's paths are /@NAME/… and go through the
   same routes (its server reads them); its Home is asked of that server (/api/remote/NAME/…). */
TAB_ROOT.files = () => pushPage(filesRootPage(), "files");
const isMdPath = p => /\.(md|markdown|mdown|mkd)$/i.test(p);
const joinPath = (dir, name) => dir.replace(/\/+$/, "") + "/" + name;

/* The folders the sessions work in: one row each, the one that moved last first. */
function sessionFolders() {
  const by = new Map();
  for (const s of S.sessions.values()) {
    if (s.fleet || !s.cwd) continue;
    const t = (s.last && s.last.time) || s.created || 0, f = by.get(s.cwd) || { path: s.cwd, t: 0, n: 0, remote: s.remote || "" };
    f.t = Math.max(f.t, t); f.n++; by.set(s.cwd, f);
  }
  return [...by.values()].sort((a, b) => b.t - a.t);
}
function filesRootPage() {
  const page = makePage({ root: true, title: "Files", right: navBtn("go", I("search", 21), "Go to a folder") });
  page.acts.go = () => askText({ title: "Go to a folder", ok: "Open", label: "Folder", placeholder: "~/project or /@machine/path", value: "~/", mono: true,
    onOk: async v => { const p = v.trim().replace(/(.)\/+$/, "$1"); await listDir(p); pushPage(folderPage(p), "files"); } });
  page.render = () => {
    const fs = sessionFolders();
    const tagOf = f => f.remote ? `<span class="mtag${machineUp(f.remote) ? "" : " off"}">${esc(f.remote)}</span>` : "";
    page.body.innerHTML =
      (fs.length ? `<div class="shead">Session folders</div><div class="rows">` + fs.map(f => navRow({ attrs: `data-dir="${esc(f.path)}"`, icon: folderIcon(false),
        name: basename(f.path) || f.path, sub: esc(tilde(f.path.replace(/^\/@[^/]+/, ""))), tag: tagOf(f), hint: esc(ago(f.t)) })).join("") + `</div>` : "") +
      `<div class="shead">Places</div><div class="rows">` +
      navRow({ attrs: `data-dir="~"`, icon: I("home", 18), name: "Home", sub: "This Mac" }) +
      (S.machines || []).map(m => navRow({ attrs: `data-dir="/@${esc(m.name)}/~"${m.ok ? "" : " disabled"}`, icon: I("server", 18), name: "Home",
        sub: esc(m.name) + (m.ok ? "" : " · unreachable"), cls: m.ok ? "" : "off" })).join("") + `</div>`;
  };
  page.onShow = page.render;
  page.body.addEventListener("click", e => { const r = e.target.closest("[data-dir]"); if (r && !r.disabled) pushPage(folderPage(r.dataset.dir), "files"); });
  page.render();
  return page;
}

/* ===================================================================== a folder */
async function listDir(path) {
  const home = /^\/@([a-z0-9][a-z0-9_-]*)\/~$/.exec(path);
  if (home) {  // another machine's home: its server says where (/@NAME/~ is no path there)
    const n = home[1], d = await api("GET", `/api/remote/${n}/api/dirlist?path=~`);
    return { ...d, path: `/@${n}${d.path}`, parent: d.parent ? `/@${n}${d.parent}` : null };
  }
  return api("GET", "/api/dirlist?path=" + encodeURIComponent(path));
}
function folderPage(path) {
  const home = path === "~" || /^\/@[^/]+\/~$/.test(path);  // (named Home, not after the user)
  const page = makePage({ title: home ? "Home" : basename(path) || path, sub: tilde(path), right: navBtn("more", MORE_SVG, "Folder actions") });
  page.path = path;
  page.load = async () => {
    let d;
    try { d = await listDir(page.path); }
    catch (e) { if (!(e instanceof NotPaired)) page.body.innerHTML = emptyBox("Cannot open this folder", esc(e.message)); return; }
    page.path = d.path; page.parent = d.parent;
    page.setTitle(home ? "Home" : /^(\/@[^/]+)?\/?$/.test(d.path) ? d.path || "/" : basename(d.path), tilde(d.path));
    const es = d.entries || [];
    page.body.innerHTML = es.length ? `<div class="rows files">` + es.map(x => navRow({
      attrs: `data-${x.dir ? "dir" : "file"}="${esc(joinPath(d.path, x.name))}"`, icon: x.dir ? folderIcon(false) : fileIcon(x.name),
      name: x.name, chev: x.dir, cls: (x.name.startsWith(".") ? "dot" : "") + (x.dir ? "" : " leaf") })).join("") + `</div>`
      : emptyBox("Empty folder", "Nothing in here yet.");
  };
  page.onShow = page.load;  // (back from a file: what changed meanwhile)
  page.body.innerHTML = loadingHtml();
  page.body.addEventListener("click", e => {
    const d = e.target.closest("[data-dir]"), f = e.target.closest("[data-file]");
    if (d) pushPage(folderPage(d.dataset.dir), page.tab);
    else if (f) pushPage(filePage(f.dataset.file), page.tab);
  });
  page.acts.more = () => actionSheet(tilde(page.path), [
    { label: "New file…", icon: "file", run: () => newPathIn(page, false) },
    { label: "New folder…", icon: "folder", run: () => newPathIn(page, true) },
    { label: "Parent folder", icon: "up", disabled: !page.parent, run: () => pushPage(folderPage(page.parent), page.tab) },
    { label: "Copy path", icon: "copy", run: () => copyText(page.path, "Path copied") },
    { label: "Refresh", icon: "refresh", run: () => page.load() },
  ]);
  page.load();
  return page;
}
function newPathIn(page, folder) {
  askText({ title: folder ? "New folder" : "New file", ok: "Create", label: `In ${tilde(page.path)}`, placeholder: folder ? "name" : "name.txt (a/b.txt makes folders)", mono: true,
    onOk: async v => {
      const path = joinPath(page.path, v.trim().replace(/\/+$/, ""));
      await api("POST", "/api/files/create", { path, folder });
      await page.load();
      if (!folder) pushPage(filePage(path), page.tab);
    } });
}

/* ===================================================================== a file: code, Markdown, an image */
const LANG_OF = { py: "python", pyi: "python", js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript",
  sh: "bash", zsh: "bash", bash: "bash", yml: "yaml", html: "xml", htm: "xml", svg: "xml", rs: "rust", kt: "kotlin", h: "c", hpp: "cpp", cc: "cpp",
  rb: "ruby", toml: "ini", cfg: "ini", conf: "ini", jsonl: "json", md: "markdown", txt: "", log: "" };
function langOf(path) {
  const name = basename(path).toLowerCase();
  if (name === "dockerfile" || name === "makefile") return name;
  const ext = name.includes(".") ? name.slice(name.lastIndexOf(".") + 1) : "";
  const l = ext in LANG_OF ? LANG_OF[ext] : ext;
  return l && window.hljs && hljs.getLanguage(l) ? l : "";
}
/* Highlighted lines, each whole (a span that runs over a line end is closed there and opened again on the next). */
function codeLines(text, lang) {
  let html = esc(text);
  if (lang && text.length < 300000) try { html = hljs.highlight(text, { language: lang, ignoreIllegals: true }).value; } catch {}
  const out = [], open = [];
  let line = "", m;
  const re = /(<span[^>]*>)|(<\/span>)|(\n)|([^<\n]+)/g;
  while ((m = re.exec(html))) {
    if (m[1]) { open.push(m[1]); line += m[1]; }
    else if (m[2]) { open.pop(); line += m[2]; }
    else if (m[3]) { out.push(line + "</span>".repeat(open.length)); line = open.join(""); }
    else line += m[4];
  }
  out.push(line + "</span>".repeat(open.length));
  if (out.length > 1 && text.endsWith("\n")) out.pop();
  return out;
}
function codeHtml(text, path, wrap) {
  const lines = codeLines(text, langOf(path));
  return `<div class="code${wrap ? " wrap" : ""}" style="--nw:${String(lines.length).length}ch"><div class="lines">` +
    lines.map((l, i) => `<div class="cl"><span class="n">${i + 1}</span><span class="t">${l || " "}</span></div>`).join("") + `</div></div>`;
}
/* Markdown as the answers draw it; a note's [[links]] too (m-notes.js). Its images are this file's neighbours. */
function renderDoc(text, path) {
  const was = [MD_MACHINE, MD_CWD, MD_WIKI];
  MD_MACHINE = machineOf(path); MD_CWD = dirname(path); MD_WIKI = isNotePath(path) ? path : "";
  let fm = "";
  const f = /^---\n([\s\S]*?)\n---(?:\n|$)/.exec(text);  // front matter: its properties, quiet, above the text
  if (f) { fm = `<div class="props">${f[1].split("\n").map(l => `<div>${esc(l)}</div>`).join("")}</div>`; text = text.slice(f[0].length); }
  try { return fm + md.render(text); } finally { [MD_MACHINE, MD_CWD, MD_WIKI] = was; }
}
function filePage(path) {
  const page = makePage({ title: basename(path), sub: tilde(dirname(path)), right: navBtn("more", MORE_SVG, "File actions") + navTxt("edit", "Edit") });
  page.path = path;
  const edit = $("[data-nav=edit]", page.el); edit.hidden = true;
  page.show = f => {
    page.f = f;
    edit.hidden = f.kind !== "text";
    page.el.classList.toggle("paperpage", f.kind === "text" && isMdPath(path) && !page.source);
    if (f.kind === "text") {
      page.body.innerHTML = isMdPath(path) && !page.source ? `<div class="doc md">${renderDoc(f.text, path)}</div>${isNotePath(path) ? `<div class="blinks"></div>` : ""}`
        : codeHtml(f.text, path, LS.get("wrap", true));
      if (isNotePath(path) && !page.source) loadBacklinks(page);
    } else if (f.kind === "image") page.body.innerHTML = `<div class="imgview"><img src="${esc(rawUrl(path) + "&v=" + f.mtime)}" alt="${esc(basename(path))}"></div>`;
    else if (f.kind === "pdf") page.body.innerHTML = emptyBox("PDF", `${esc(bytes(f.size))}<div class="eacts"><a class="pill" href="${esc(rawUrl(path))}" target="_blank" rel="noopener">Open the PDF</a></div>`);
    else page.body.innerHTML = emptyBox(f.kind === "too large" ? "Too large to show here" : "Binary file", esc(bytes(f.size)));
  };
  page.load = async () => {
    try { page.show(await api("GET", "/api/file?path=" + encodeURIComponent(path))); }
    catch (e) { if (!(e instanceof NotPaired)) page.body.innerHTML = emptyBox(`Cannot open ${basename(path)}`, esc(e.message)); }
  };
  page.body.innerHTML = loadingHtml();
  page.acts.edit = () => editFile(page);
  page.acts.more = () => {
    const f = page.f || {}, text = f.kind === "text";
    actionSheet(basename(path), [
      text && isMdPath(path) ? { label: page.source ? "Show it drawn" : "Show the source", icon: "file", run: () => { page.source = !page.source; page.show(f); } } : null,
      text && (!isMdPath(path) || page.source) ? { label: LS.get("wrap", true) ? "Don't wrap lines" : "Wrap lines", icon: "undo", run: () => { LS.set("wrap", !LS.get("wrap", true)); page.show(f); } } : null,
      { label: "Copy path", icon: "copy", run: () => copyText(path, "Path copied") },
      text ? { label: "Copy text", icon: "copy", run: () => copyText(f.text) } : null,
      isNotePath(path) ? { label: "Copy [[link]]", icon: "link", run: () => copyText(`[[${basename(path).replace(/\.md$/i, "")}]]`, "Link copied") } : null,
      { label: "Reload", icon: "refresh", run: () => page.load() },
    ].filter(Boolean));
  };
  page.body.addEventListener("click", e => docClick(e, page));
  page.load();
  return page;
}
/* Edit: from what the disk has now (the page may be old), back into the page once saved. */
async function editFile(page, { focusEnd = false } = {}) {
  let f;
  try { f = await api("GET", "/api/file?path=" + encodeURIComponent(page.path)); } catch (e) { if (!(e instanceof NotPaired)) toast(e.message); return; }
  if (f.kind !== "text") { page.show(f); return; }
  page.show(f);
  openEditor({ path: page.path, text: f.text, hash: f.hash, prose: isMdPath(page.path), focusEnd, onSaved: g => page.show({ ...f, ...g }) });
}
/* Taps in a drawn document: [[links]], its own links (another file beside it), `file.py` references, images, Copy. */
function docClick(e, page) {
  const t = e.target;
  const w = t.closest("a.wiki");
  if (w) { e.preventDefault(); openWiki(w.dataset.wiki, page.path, w.dataset.head, page.tab); return; }
  if (t.closest("[data-copy]")) { copyText(t.closest(".pre").querySelector("code").innerText); return; }
  const bl = t.closest("[data-note]");
  if (bl) { pushPage(notePage(bl.dataset.note), page.tab); return; }
  const img = t.closest(".doc img, .imgview img");
  if (img) { openLightbox(img.src); return; }
  const fr = t.closest("code.fref");
  if (fr) {
    api("GET", `/api/resolve?ref=${encodeURIComponent(fileRef(fr.dataset.fref).ref)}&cwd=${encodeURIComponent(dirname(page.path))}`)
      .then(d => d.path ? pushPage(filePage(d.path), page.tab) : toast(`No file ${fr.dataset.fref} here`)).catch(err => toast(err.message));
    return;
  }
  const a = t.closest("a[href]"), href = a && a.getAttribute("href");
  if (a && href && !/^[a-z][\w+.-]*:/i.test(href) && !href.startsWith("#")) {  // a relative link: that file, beside this one
    e.preventDefault();
    let p = href.split("#")[0];
    try { p = decodeURIComponent(p); } catch {}
    const parts = p.startsWith("/") ? [] : dirname(page.path).split("/");
    for (const seg of p.split("/")) { if (seg === "..") parts.pop(); else if (seg && seg !== ".") parts.push(seg); }
    const to = (p.startsWith("/") ? "/" : "") + parts.filter((x, i) => x || i === 0).join("/");
    pushPage(p.endsWith("/") ? folderPage(to) : isNotePath(to) ? notePage(to) : filePage(to), page.tab);
    return;
  }
  if (a && href && href.startsWith("orbit://session/")) {
    e.preventDefault();
    const sid = href.slice(16).split("#")[0];
    if (S.sessions.has(sid)) { switchTab("sessions"); openSession(sid); } else toast("That session is not open");
  }
}
