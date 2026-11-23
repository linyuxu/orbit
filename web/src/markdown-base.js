"use strict";
/* ===================================================================== markdown */
/* One instance per mode with the same rules: `md` for answers and notes (+ markdown.js: mentions, file links, local
   images), `pmd` for what a plugin sends (plugin mode, below: no local paths, only safe links). */
function makeMd() {
const md = window.markdownit({ html: false, linkify: true, breaks: false, typographer: false,
  highlight(code, lang) {
    if (lang && window.hljs && hljs.getLanguage(lang)) {
      try { return hljs.highlight(code, { language: lang, ignoreIllegals: true }).value; } catch {}
    }
    return "";
  } });
md.renderer.rules.fence = (tokens, idx, opts) => {
  const t = tokens[idx], lang = (t.info || "").trim().split(/\s+/)[0];
  const body = opts.highlight(t.content, lang) || esc(t.content);
  return `<div class="pre"><button class="copy" data-copy>Copy</button><pre><code class="hljs${lang ? " language-" + esc(lang) : ""}">${body}</code></pre></div>`;
};
/* Math (KaTeX): $inline$, $$display$$ (also \( \) and \[ \]). A $ next to a digit or space is money, not math. */
function katexHtml(tex, display) {
  if (!window.katex) return esc(tex);
  try { return katex.renderToString(tex, { displayMode: display, throwOnError: false, output: "html", strict: "ignore" }); }
  catch { return esc(tex); }
}
md.inline.ruler.after("escape", "math_inline", (state, silent) => {
  const src = state.src, pos = state.pos;
  let open, close;
  if (src.startsWith("\\(", pos)) { open = "\\("; close = "\\)"; }
  else if (src[pos] === "$" && src[pos + 1] !== "$") { open = close = "$"; }
  else return false;
  const start = pos + open.length;
  if (open === "$" && (/\s/.test(src[start] || " ") || /\d/.test(src[pos - 1] || ""))) return false;
  let end = start;
  while ((end = src.indexOf(close, end)) !== -1) {
    if (src[end - 1] === "\\") { end++; continue; }
    if (open === "$" && (/\s/.test(src[end - 1]) || /\d/.test(src[end + 1] || ""))) { end++; continue; }
    break;
  }
  if (end === -1 || end === start) return false;
  if (!silent) { const t = state.push("math_inline", "math", 0); t.content = src.slice(start, end); }
  state.pos = end + close.length;
  return true;
});
md.block.ruler.after("blockquote", "math_block", (state, startLine, endLine, silent) => {
  const first = state.src.slice(state.bMarks[startLine] + state.tShift[startLine], state.eMarks[startLine]).trim();
  const open = first.startsWith("$$") ? "$$" : first.startsWith("\\[") ? "\\[" : null;
  if (!open) return false;
  const close = open === "$$" ? "$$" : "\\]";
  let body = first.slice(2), line = startLine, found = false;
  if (body.trim().endsWith(close) && body.trim().length >= 2) { body = body.trim().slice(0, -2); found = true; }
  while (!found && ++line < endLine) {
    const l = state.src.slice(state.bMarks[line] + state.tShift[line], state.eMarks[line]);
    if (l.trim().endsWith(close)) { body += "\n" + l.trim().slice(0, -2); found = true; break; }
    body += "\n" + l;
  }
  if (!found) return false;
  if (silent) return true;
  const t = state.push("math_block", "math", 0); t.block = true; t.content = body.trim(); t.map = [startLine, line + 1];
  state.line = line + 1;
  return true;
}, { alt: ["paragraph", "reference", "blockquote", "list"] });
md.renderer.rules.math_inline = (tokens, idx) => katexHtml(tokens[idx].content, false);
md.renderer.rules.math_block = (tokens, idx) => `<div class="math">${katexHtml(tokens[idx].content, true)}</div>`;
/* A table's short columns keep their text on one line: Chinese breaks between any two characters, so the browser
   squeezed a column of short labels to one character beside a column of long text. A column whose longest cell is
   at most 16em wide (estimated: CJK 1em, other .55em) gets that as its min-width; longer ones wrap as before. */
const CJK = /[\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef\u3000-\u303f]/;
const textEm = s => [...s.replace(/[`*_~]|\[([^\]]*)\]\([^)]*\)/g, "$1")].reduce((w, c) => w + (CJK.test(c) ? 1 : .55), 0);
md.renderer.rules.table_open = (tokens, idx) => {
  const cols = [], cells = [];
  let col = 0;
  for (let i = idx + 1; i < tokens.length && tokens[i].type !== "table_close"; i++) {
    const t = tokens[i];
    if (t.type === "tr_open") col = 0;
    else if (t.type === "th_open" || t.type === "td_open") {
      const inl = tokens[i + 1], w = inl && inl.type === "inline" ? textEm(inl.content) : 0;
      cols[col] = Math.max(cols[col] || 0, w); cells.push([t, col++]);
    }
  }
  for (const [t, c] of cells) if (cols[c] > 0 && cols[c] <= 16) t.attrJoin("style", `min-width:${Math.ceil(cols[c] * 10) / 10}em`);
  return '<div class="tbl"><table>';
};
md.renderer.rules.table_close = () => "</table></div>";
const defaultLinkOpen = md.renderer.rules.link_open || ((t, i, o, e, s) => s.renderToken(t, i, o));
md.renderer.rules.link_open = (tokens, idx, opts, env, self) => {
  tokens[idx].attrSet("target", "_blank");
  tokens[idx].attrSet("rel", "noopener");
  return defaultLinkOpen(tokens, idx, opts, env, self);
};
return md;
}
const md = makeMd();
