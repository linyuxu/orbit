"use strict";
md.inline.ruler.before("emphasis", "mention", (state, silent) => {
  const src = state.src, pos = state.pos;
  if (src[pos] !== "@" || src[pos + 1] !== "@" || (pos > 0 && /[\w@]/.test(src[pos - 1]))) return false;
  const m = /^@@([\w.-]*\w)/.exec(src.slice(pos));
  if (!m) return false;
  const want = m[1].toLowerCase();
  const s = [...S.sessions.values()].find(x => mentionTag(x.name).slice(2).toLowerCase() === want);
  if (!s) return false;
  if (!silent) {
    const o = state.push("link_open", "a", 1); o.attrSet("href", `orbit://session/${s.id}`); o.attrSet("class", "mention"); o.attrSet("title", `${s.name} (click to open)`);
    const t = state.push("text", "", 0); t.content = m[0];
    state.push("link_close", "a", -1);
  }
  state.pos += m[0].length;
  return true;
});
/* Inline code that names a file (`src/app.py`, `app.py:42`, `push_to_s3.py:104-105, 125`) is a quiet link that opens
   it at the first range. Code blocks never link. */
const CODE_EXT = "py|pyi|ipynb|js|mjs|cjs|jsx|ts|tsx|vue|svelte|java|kt|kts|scala|groovy|gradle|go|rs|c|cc|cpp|cxx|h|hh|hpp|"
  + "m|mm|swift|rb|php|cs|fs|sh|bash|zsh|fish|ps1|sql|lua|pl|ex|exs|erl|hs|ml|dart|jl|css|scss|less|html|htm|xml|json|"
  + "jsonl|yaml|yml|toml|ini|cfg|conf|proto|tf|md|markdown|txt|rst|csv|lock|mk|cmake";
const FREF = new RegExp("^((?:~|\\.{1,2})?/?(?:[\\w@+.-]+/)+[\\w@+-][\\w@+.-]*\\.[A-Za-z0-9]{1,10}|[\\w@+-][\\w@+.-]*\\.(?:" + CODE_EXT
  + ")|(?:[\\w@+.-]+/)*(?:Makefile|Dockerfile|Justfile|BUILD|WORKSPACE))(?::(\\d+)(?:-(\\d+))?(?:,\\s*\\d+(?:-\\d+)?)*)?$");
function fileRef(text) {
  const m = FREF.exec((text || "").trim());
  return m && !/:\/\//.test(text) ? { ref: m[1], line: m[2] ? +m[2] : null } : null;
}
md.renderer.rules.code_inline = (tokens, idx) => {
  const c = tokens[idx].content;
  return fileRef(c) ? `<code class="fref" data-fref="${esc(c)}" title="Open beside the conversation (click) · more (right-click)">${esc(c)}</code>` : `<code>${esc(c)}</code>`;
};
/* A file link in an answer opens beside the conversation (the side pane) by default: the answer stays in view;
   right-click offers a tab of its own or the editor of ⌘, (builddoc/ui.md). `how`: "side" · "tab" · "editor". */
async function resolveFileRef(text, cwd) {
  const r = fileRef(text); if (!r) return null;
  let d = null;
  try { d = await api("GET", `/api/resolve?ref=${encodeURIComponent(r.ref)}&cwd=${encodeURIComponent(cwd || "")}`); } catch {}
  if (!d || !d.path) { toast(`No file ${r.ref} in this session's repo or the repos next to it`); return null; }
  return { path: d.path, root: d.root || null, line: r.line };
}
async function openFileRef(text, cwd, how = "side") {
  const r = await resolveFileRef(text, cwd); if (!r) return;
  const { path, root, line } = r;
  if (how === "side") { openSide({ kind: "file", path, ...(root ? { root } : {}), ...(line ? { line } : {}) }); return; }
  if (how === "editor") { openExternal(path, "editor", line); return; }
  if (EMBED) { window.parent.postMessage({ orbit: "open-file", path, pinned: true, line }, location.origin); return; }
  if (line) S.reveal = { path, line };
  openFile(path, { pinned: true });
  if (line && S.file === path) applyReveal(path);
}
function fileRefMenu(text, cwd, at) {
  menu([
    { label: "Open in side pane", run: () => openFileRef(text, cwd, "side") },
    { label: "Open in new tab", run: () => openFileRef(text, cwd, "tab") },
    (S.settings && S.settings.open_in || "default") === "side" ? null
      : { label: openInLabel(), run: () => openFileRef(text, cwd, "editor") },
    { sep: true },
    { label: "Copy path", run: async () => { const r = await resolveFileRef(text, cwd); if (r) copyText(r.path); } },
    { label: "Copy text", run: () => copyText(text) },
  ], at);
}
const mdCache = new Map();
/* An image an answer names by a local path is served by the server: /abs, file:///abs, ~/… (that machine's home), or
   relative to the session's folder (plot.png, ./out/fig.svg, ../x.png). URLs (https:, data:) are left alone. */
function imagePath(p) {
  if (p.startsWith("file://")) p = p.slice(7);
  if (/^[a-z][\w+.-]*:/i.test(p) || p.startsWith("#") || p.startsWith("/api/")) return null;
  if (p.startsWith("~/")) {
    if (!MD_MACHINE) return p;  // this machine's server expands it
    const home = /^\/@[^/]+(?:\/home\/[^/]+|\/Users\/[^/]+|\/root)(?=\/|$)/.exec(MD_CWD || "");
    return home ? home[0] + p.slice(1) : null;
  }
  if (!p.startsWith("/")) {
    if (!MD_CWD) return null;
    const out = [];
    for (const seg of (MD_CWD.replace(/\/+$/, "") + "/" + p).split("/")) {
      if (seg === "..") { if (out.length > 1) out.pop(); } else if (seg !== "." && seg !== "") out.push(seg); else if (!out.length) out.push("");
    }
    p = out.join("/");
  }
  return machinePath(p);
}
{
  const dflt = md.renderer.rules.image;
  md.renderer.rules.image = (tokens, idx, opts, env, self) => {
    const t = tokens[idx], src = t.attrGet("src") || "";
    let p = src;
    try { p = decodeURIComponent(src); } catch {}
    const local = imagePath(p);
    if (local) t.attrSet("src", (typeof withToken === "function" ? withToken : x => x)(`/api/raw?path=${encodeURIComponent(local)}`));  // (the phone's app: its token)
    return dflt ? dflt(tokens, idx, opts, env, self) : self.renderToken(tokens, idx, opts);
  };
}
function renderMd(src) {
  const key = MD_MACHINE + "\0" + MD_CWD + "\0" + src;
  if (mdCache.has(key)) return mdCache.get(key);
  const html = md.render(src || "");
  if (mdCache.size > 400) mdCache.clear();
  mdCache.set(key, html);
  return html;
}
/* Plugin mode (plugin API 2.0, builddoc/plugin-views.md §8.6): what a plugin sends (a thread's messages, a doc) is
   drawn with the answers' rules but never reaches this machine's files: no /api/raw, no file links, no ~/; images
   only https: and svc: (its service's files, through Orbit: /api/plugins/NAME/svc/…); links only https:, http:,
   mailto: and the plugin's declared schemes. Anything else is left as its text. */
const pmd = makeMd();
const svcUrl = (plugin, u) => (typeof withToken === "function" ? withToken : x => x)(`/api/plugins/${encodeURIComponent(plugin)}/svc${u.slice(4)}`);
const pluginLinkOk = (href, schemes) => { const m = /^([a-z][a-z0-9+.-]*):/i.exec(href || ""); return !!m && (/^(https?|mailto)$/i.test(m[1]) && !/^https?:(?!\/\/)/i.test(href) || (schemes || []).includes(m[1].toLowerCase())); };
const pluginImgSrc = (src, plugin) => /^https:\/\//i.test(src) ? src : /^svc:\/[^/]/i.test(src) ? svcUrl(plugin, src) : null;
{
  const linkOpen = pmd.renderer.rules.link_open, img = pmd.renderer.rules.image;
  pmd.renderer.rules.link_open = (tokens, idx, opts, env, self) => {
    env.dropLink = !pluginLinkOk(tokens[idx].attrGet("href"), env.schemes);
    return env.dropLink ? "" : linkOpen(tokens, idx, opts, env, self);
  };
  pmd.renderer.rules.link_close = (tokens, idx, opts, env, self) => {
    if (env.dropLink) { env.dropLink = false; return ""; }
    return self.renderToken(tokens, idx, opts);
  };
  pmd.renderer.rules.image = (tokens, idx, opts, env, self) => {
    const t = tokens[idx], src = pluginImgSrc(t.attrGet("src") || "", env.plugin);
    if (!src) return esc(t.content || "");
    t.attrSet("src", src);
    return img ? img(tokens, idx, opts, env, self) : self.renderToken(tokens, idx, opts);
  };
}
function renderPluginMd(src, plugin, schemes = []) {
  const key = "\u0001" + plugin + "\0" + schemes.join(",") + "\0" + src;
  if (mdCache.has(key)) return mdCache.get(key);
  const html = pmd.render(src || "", { plugin, schemes });
  if (mdCache.size > 400) mdCache.clear();
  mdCache.set(key, html);
  return html;
}
