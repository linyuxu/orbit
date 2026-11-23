"use strict";
/* ===================================================================== attachments: paste, drop, upload */
/* ⌘V / drop / attach: the file is saved by the server and its path goes into the request (the agent reads it). */
function fileB64(f) {
  return new Promise((ok, bad) => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(",")[1] || ""); r.onerror = () => bad(r.error); r.readAsDataURL(f); });
}
/* A file attached to another machine's session is saved on that machine; its path here is /@NAME/… */
const uploadPath = () => (cur() && cur().remote ? `/api/remote/${cur().remote}` : "") + "/api/upload";
const uploaded = r => cur() && cur().remote && r.path.startsWith("/") ? `/@${cur().remote}${r.path}` : r.path;
async function attachFiles(files) {
  files = [...files].filter(f => f && f.size);
  if (!files.length || !S.current) return;
  const paths = [];
  for (const f of files) {
    try { paths.push(uploaded(await api("POST", uploadPath(), { name: f.name || (f.type === "image/png" ? "pasted.png" : "pasted"), data: await fileB64(f) }))); }
    catch (e) { toast(`${f.name || "File"}: ${e.message}`); }
  }
  insertPaths(paths);
}
/* Files into the input at the cursor, as Markdown: an image as ![name](path) (it shows in place), any other file as
   [name](path). The agent reads the files from their paths. */
const IMG_RE = /\.(png|jpe?g|gif|webp|svg|bmp|avif|heic)$/i;
const quotePath = p => /[\s"']/.test(p) ? `"${p.replace(/"/g, '\\"')}"` : p;
const mdPath = p => /[\s()<>]/.test(p) ? `<${p}>` : p;
const mdLink = p => `${IMG_RE.test(p) ? "!" : ""}[${basename(p).replace(/[\[\]]/g, "")}](${mdPath(p)})`;
function insertPaths(paths) {
  if (!paths.length || !S.current) return;
  const v = input.value, at = document.activeElement === input ? input.selectionStart : v.length;
  const before = v.slice(0, at), after = v.slice(at);
  const ins = (before && !/\s$/.test(before) ? " " : "") + paths.map(mdLink).join(" ") + (/^\s/.test(after) ? "" : " ");
  input.focus();
  input.insertAt(ins, at);
  updateSend(); saveDraft();
  toast(paths.length === 1 ? `Attached ${basename(paths[0])}: the agent gets its path` : `${paths.length} files attached`);
}
/* Absolute paths in a text ("quoted" when they hold spaces). */
function pathsIn(text) {
  const out = [], re = /"(\/[^"\n]+)"|(?:^|(?<=\s))(\/[^\s"]+)/g;
  let m;
  while ((m = re.exec(text || ""))) { const p = m[1] || m[2]; if (/\.\w{1,8}$/.test(p) && !out.includes(p)) out.push(p); }
  return out;
}
const rawUrl = p => `/api/raw?path=${encodeURIComponent(p)}`;
/* A request in its bubble: plain text, with ![](path) as the image and [name](path) as a link, in place. */
/* A request as the user wrote it, plain text, with its code shown as code: fenced blocks and `inline` spans (U4). */
function reqHtml(text) {
  const fence = /^```([^\n`]*)\n([\s\S]*?)^```[ \t]*(?:\n|$)/gm;
  let out = "", last = 0, m;
  while ((m = fence.exec(text))) {
    out += reqInline(text.slice(last, m.index));
    out += `<pre class="rcode"><code>${esc(m[2].replace(/\n$/, ""))}</code></pre>`;
    last = m.index + m[0].length;
  }
  return out + reqInline(text.slice(last));
}
function reqInline(text) {
  return text.split(/(`[^`\n]+`)/).map((part, k) => k % 2 ? `<code class="rc">${esc(part.slice(1, -1))}</code>` : reqLinks(part)).join("");
}
function reqLinks(text) {
  const re = /(!?)\[([^\]\n]*)\]\((<[^>\n]+>|[^)\s]+)\)/g;
  let out = "", last = 0, m;
  while ((m = re.exec(text))) {
    out += esc(text.slice(last, m.index));
    const target = machinePath(m[3].replace(/^<|>$/g, ""));
    const local = target.startsWith("/");
    if (m[1] && (local || /^https?:/i.test(target))) out += `<img class="rimg" src="${esc(local ? rawUrl(target) : target)}" alt="${esc(m[2])}" title="${esc(target)}" data-bimg="${esc(target)}" loading="lazy">`;
    else if (local) out += `<a class="rfile" data-rfile="${esc(target)}" title="${esc(target)}">${esc(m[2] || basename(target))}</a>`;
    else out += esc(m[0]);
    last = m.index + m[0].length;
  }
  return out + esc(text.slice(last));
}
document.addEventListener("click", e => {
  const b = e.target.closest && e.target.closest("[data-bimg]"), f = e.target.closest && e.target.closest("[data-rfile]");
  if (b) openLightbox(b.dataset.bimg.startsWith("/") ? rawUrl(b.dataset.bimg) : b.dataset.bimg);
  else if (f) openFile(f.dataset.rfile);
});

/* Files dropped from Finder. In the app the shell passes real paths (WKWebView never gives the page a drop); in a
   browser only the session view and the editors take File objects (uploaded). Where it lands decides:
   a Markdown editor → copied beside it and linked (![[img]] in the vault); a code editor → its paths as text;
   the session view → paths into the input; anywhere else → the files open in tabs. */
const dropAt = (x, y) => { const k = rectScale(); return { cx: x / S.zoom * k, cy: y / S.zoom * k }; };
function dropTarget(el) {
  if (!el) return null;
  if (el.closest("#fbody .cm-editor") && S.file && EDITORS.get(S.file) && EDITORS.get(S.file).ed) return { kind: "editor", path: S.file };
  if (el.closest("#sessview") && S.current && !S.file) return { kind: "input" };
  if (el.closest(".pthread") && pviewShown() && pviewShown().kind === "thread") return { kind: "pthread", view: pviewShown() };
  if (el.closest("#side iframe")) return { kind: "frame", frame: el.closest("iframe") };
  return { kind: "open" };
}
function dropHint(t) {
  $("#composer").classList.toggle("drop", !!t && t.kind === "input");
  $("#fbody").classList.toggle("drop", !!t && t.kind === "editor");
}
async function dropIntoEditor(path, pos, items) {  // items: [{src}] (paths) or [{file}] (File objects)
  const ed = EDITORS.get(path); if (!ed || !ed.ed) return;
  if (!ed.ed.markdown) {  // code: the paths as text (uploaded first when only the bytes are known)
    const ps = [];
    for (const it of items) {
      try { ps.push(it.src || uploaded(await api("POST", uploadPath(), { name: it.file.name || "pasted", data: await fileB64(it.file) }))); }
      catch (e) { toast(e.message); }
    }
    if (ps.length) ed.ed.insert(pos, ps.map(quotePath).join(" "));
    return;
  }
  const links = [];
  for (const it of items) {
    try {
      const body = it.src ? { doc: path, src: it.src, name: "" } : { doc: path, name: it.file.name || "", data: await fileB64(it.file) };
      links.push(await api("POST", "/api/attach", body));
    } catch (e) { toast(`${it.src ? basename(it.src) : it.file.name || "File"}: ${e.message}`); }
  }
  if (!links.length) return;
  // Images (or several files) on lines of their own; one file link goes inline where it was dropped.
  const own = links.length > 1 || links.some(l => l.image);
  let text = links.map(l => l.link).join(own ? "\n" : " ");
  let at = pos;
  if (own) {  // below the line it was dropped on (never splitting a heading or a sentence)
    const docText = ed.ed.getDoc();
    at = pos == null ? docText.length : docText.indexOf("\n", pos);
    if (at < 0) at = docText.length;
    if (at > 0 && docText[at - 1] !== "\n") text = "\n" + text;
  }
  ed.ed.insert(at, text);
  if (isNote(path)) { loadNoteNames(); refreshTree(S.vault, true); }
  toast(links.length === 1 ? `Added ${basename(links[0].path)} beside the note` : `${links.length} files added`);
}
window.orbitDrop = ev => {
  if (modal) { dropHint(null); return; }
  const { cx, cy } = ev.type === "leave" ? { cx: 0, cy: 0 } : dropAt(ev.x, ev.y);
  const t = ev.type === "leave" ? null : dropTarget(document.elementFromPoint(cx, cy));
  if (t && t.kind === "frame") {  // a side tab: its own page decides, in its own coordinates
    const k = rectScale(), r = t.frame.getBoundingClientRect();
    try { t.frame.contentWindow.orbitDrop({ ...ev, x: ev.x - r.left / k * S.zoom, y: ev.y - r.top / k * S.zoom }); } catch {}
    dropHint(null);
    if (ev.type !== "drop") return;
    return;
  }
  dropHint(ev.type === "drop" ? null : t);
  if (ev.type !== "drop" || !t || !ev.paths || !ev.paths.length) return;
  if (t.kind === "editor") { const ed = EDITORS.get(t.path); dropIntoEditor(t.path, ed.ed.posAt(cx, cy), ev.paths.map(src => ({ src }))); }
  else if (t.kind === "input") insertPaths(ev.paths);
  else if (t.kind === "pthread") { t.view.files.push(...ev.paths.map(p => ({ name: basename(p), path: p, image: IMG_RE.test(p) }))); drawChips(t.view); }  // (files of this machine: sent as they are)
  else ev.paths.forEach(p => openFile(p));
};
// A browser drop outside the session view and the editors must not navigate the page away to the file.
document.addEventListener("dragover", e => { if (e.dataTransfer && [...e.dataTransfer.types].includes("Files")) e.preventDefault(); });
document.addEventListener("drop", e => { if (e.dataTransfer && e.dataTransfer.files.length && !e.defaultPrevented) { e.preventDefault(); toast("Drop it on the input or into a file"); } });
$("#sessview").addEventListener("dragover", e => { if (e.dataTransfer && [...e.dataTransfer.types].includes("Files")) { e.preventDefault(); $("#composer").classList.add("drop"); } });
$("#sessview").addEventListener("dragleave", e => { if (!e.relatedTarget || !$("#sessview").contains(e.relatedTarget)) $("#composer").classList.remove("drop"); });
$("#sessview").addEventListener("drop", e => {
  $("#composer").classList.remove("drop");
  if (e.dataTransfer && e.dataTransfer.files.length && !e.defaultPrevented) { e.preventDefault(); attachFiles(e.dataTransfer.files); }
});
$("#attach").innerHTML = I("clip", 14);
$("#modechip").addEventListener("click", () => { if (cur()) modeMenu(cur()); });
$("#runon").addEventListener("click", () => { if (cur()) modelMenu(cur()); });
$("#attach").addEventListener("click", () => $("#attachfile").click());
$("#attachfile").addEventListener("change", () => { attachFiles($("#attachfile").files); $("#attachfile").value = ""; });
input.addEventListener("focus", () => $("#composer").classList.add("focus"));
input.addEventListener("blur", () => $("#composer").classList.remove("focus"));
