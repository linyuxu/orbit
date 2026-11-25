// CodeMirror 6 for Orbit. Built into ../vendor/cm.js (window.CM) by `npm run build`; the page never imports modules.
// One factory, CM.createEditor(), makes the code editor and the Markdown editor (warm paper, note font).

import { EditorState, StateField, StateEffect, EditorSelection, Compartment, Prec } from "@codemirror/state";
import {
  EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter, drawSelection,
  highlightSpecialChars, rectangularSelection, dropCursor, ViewPlugin, Decoration, MatchDecorator, placeholder as cmPlaceholder,
} from "@codemirror/view";
import { autocompletion, completionKeymap } from "@codemirror/autocomplete";
import {
  defaultKeymap, history, historyKeymap, indentWithTab, undo, redo, selectAll,
  cursorPageDown, cursorPageUp, cursorDocStart, cursorDocEnd, selectPageDown, selectPageUp, selectDocStart, selectDocEnd,
  cursorCharForward, cursorCharBackward, selectCharForward, selectCharBackward, cursorLineDown, cursorLineUp,
  selectLineDown, selectLineUp, cursorLineStart, cursorLineEnd, selectLineStart, selectLineEnd,
  cursorGroupForward, cursorGroupBackward, selectGroupForward, selectGroupBackward, cursorCharLeft, cursorCharRight,
  selectCharLeft, selectCharRight, cursorGroupLeft, cursorGroupRight, selectGroupLeft, selectGroupRight,
  cursorLineBoundaryLeft, cursorLineBoundaryRight, selectLineBoundaryLeft, selectLineBoundaryRight,
} from "@codemirror/commands";
import {
  syntaxHighlighting, HighlightStyle, indentOnInput, bracketMatching, foldGutter, foldKeymap,
  StreamLanguage, syntaxTree, indentUnit,
} from "@codemirror/language";
import { tags as t } from "@lezer/highlight";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import { javascript } from "@codemirror/lang-javascript";
import { python } from "@codemirror/lang-python";
import { json } from "@codemirror/lang-json";
import { css } from "@codemirror/lang-css";
import { html } from "@codemirror/lang-html";
import { rust } from "@codemirror/lang-rust";
import { yaml } from "@codemirror/lang-yaml";
import { sql } from "@codemirror/lang-sql";
import { cpp } from "@codemirror/lang-cpp";
import { java } from "@codemirror/lang-java";
import { go } from "@codemirror/lang-go";
import { shell } from "@codemirror/legacy-modes/mode/shell";
import { toml } from "@codemirror/legacy-modes/mode/toml";
import { livePreview } from "./livepreview.js";

// ------------------------------------------------------------------ languages by file name
const EXT = {
  js: () => javascript(), mjs: () => javascript(), cjs: () => javascript(), jsx: () => javascript({ jsx: true }),
  ts: () => javascript({ typescript: true }), tsx: () => javascript({ typescript: true, jsx: true }),
  py: () => python(), pyi: () => python(), json: () => json(), jsonl: () => json(), css: () => css(),
  html: () => html(), htm: () => html(), rs: () => rust(), yml: () => yaml(), yaml: () => yaml(), sql: () => sql(),
  c: () => cpp(), h: () => cpp(), cc: () => cpp(), cpp: () => cpp(), hpp: () => cpp(), java: () => java(), go: () => go(),
  sh: () => StreamLanguage.define(shell), bash: () => StreamLanguage.define(shell), zsh: () => StreamLanguage.define(shell),
  fish: () => StreamLanguage.define(shell), toml: () => StreamLanguage.define(toml),
};
const NAMES = { "makefile": null, "dockerfile": () => StreamLanguage.define(shell), ".bashrc": () => StreamLanguage.define(shell),
  ".zshrc": () => StreamLanguage.define(shell) };
export const MARKDOWN_EXT = new Set(["md", "markdown", "mdown", "mkd"]);
export function languageName(path) {
  const base = path.split("/").pop().toLowerCase();
  const ext = base.includes(".") ? base.split(".").pop() : "";
  if (MARKDOWN_EXT.has(ext)) return "Markdown";
  return { js: "JavaScript", mjs: "JavaScript", jsx: "JavaScript", ts: "TypeScript", tsx: "TypeScript", py: "Python",
    json: "JSON", jsonl: "JSON", css: "CSS", html: "HTML", rs: "Rust", yml: "YAML", yaml: "YAML", sql: "SQL",
    c: "C", h: "C", cc: "C++", cpp: "C++", java: "Java", go: "Go", sh: "Shell", bash: "Shell", zsh: "Shell",
    fish: "Shell", toml: "TOML" }[ext] || (base === "dockerfile" ? "Dockerfile" : "Plain text");
}
function languageFor(path) {
  const base = path.split("/").pop().toLowerCase();
  const ext = base.includes(".") ? base.split(".").pop() : "";
  if (MARKDOWN_EXT.has(ext)) return markdown({ base: markdownLanguage });
  const f = EXT[ext] || NAMES[base];
  return f ? f() : [];
}

// ------------------------------------------------------------------ highlighting from Orbit's tokens (no new colors)
const codeStyle = HighlightStyle.define([
  { tag: [t.keyword, t.modifier, t.controlKeyword, t.operatorKeyword], color: "var(--hl-kw)" },
  { tag: [t.string, t.special(t.string), t.regexp], color: "var(--hl-str)" },
  { tag: [t.comment, t.lineComment, t.blockComment], color: "var(--ink-3)", fontStyle: "italic" },
  { tag: [t.number, t.bool, t.null, t.atom], color: "var(--hl-num)" },
  { tag: [t.function(t.variableName), t.function(t.propertyName), t.definition(t.function(t.variableName))], color: "var(--hl-fn)" },
  { tag: [t.typeName, t.className, t.namespace], color: "var(--hl-type)" },
  { tag: [t.definition(t.variableName), t.definition(t.propertyName)], color: "var(--ink)" },
  { tag: [t.propertyName, t.attributeName], color: "var(--ink-2)" },
  { tag: [t.meta, t.processingInstruction, t.annotation], color: "var(--ink-3)" },
  { tag: t.invalid, color: "var(--err)" },
]);
const noteStyle = HighlightStyle.define([
  { tag: t.heading1, fontWeight: "700", fontSize: "1.3em" },
  { tag: t.heading2, fontWeight: "700", fontSize: "1.15em" },
  { tag: [t.heading3, t.heading4, t.heading5, t.heading6], fontWeight: "700" },
  { tag: t.strong, fontWeight: "700" },
  { tag: t.emphasis, fontStyle: "italic" },
  { tag: t.strikethrough, textDecoration: "line-through" },
  { tag: [t.link, t.url], color: "var(--link)" },
  { tag: t.monospace, fontFamily: "var(--mono)", fontSize: ".88em", color: "var(--chip-fg)", backgroundColor: "var(--chip-bg)", borderRadius: "4px" },
  { tag: [t.processingInstruction, t.meta, t.contentSeparator], color: "var(--ink-3)" },
  { tag: t.quote, color: "var(--ink-2)" },
  // fenced code inside notes
  { tag: [t.keyword, t.modifier], color: "var(--hl-kw)" },
  { tag: [t.string], color: "var(--hl-str)" },
  { tag: [t.comment], color: "var(--ink-3)", fontStyle: "italic" },
]);

// ------------------------------------------------------------------ Emacs region: ⌃Space mark, ⌃G cancel, ⌃W ⌥W ⌃Y, ⌃L
const setMark = StateEffect.define();
const markField = StateField.define({
  create: () => null,
  update(v, tr) {
    for (const e of tr.effects) if (e.is(setMark)) return e.value;
    if (v == null) return v;
    if (tr.docChanged) return null;  // an edit deactivates the mark
    if (tr.isUserEvent("select.pointer")) return null;
    return v;
  },
});
// While the mark is set, every cursor motion extends the region from the mark.
const extendFromMark = EditorState.transactionFilter.of(tr => {
  const mark = tr.startState.field(markField, false);
  if (mark == null || tr.docChanged || !tr.selection || tr.effects.some(e => e.is(setMark))) return tr;
  if (tr.isUserEvent("select.pointer")) return tr;
  const sel = tr.selection.main;
  if (!sel.empty) return tr;
  return [tr, { selection: EditorSelection.single(mark, sel.head), sequential: true }];
});
let killRing = [];
function kill(view, text, append = false) {
  if (!text) return;
  if (append && killRing.length) killRing[0] += text; else killRing.unshift(text);
  killRing = killRing.slice(0, 30);
  navigator.clipboard?.writeText(killRing[0])?.catch(() => {});
}
// ⌥D / ⌥⌫: kill the word after / before the cursor (into the kill ring: ⌃Y yanks it back); presses in a row, the
// cursor where the last left it, make one kill (⌥D appends, ⌥⌫ prepends), as in Emacs.
let lastWordKill = null;  // {view, pos, at}
function killWord(view, forward) {
  const r = view.state.selection.main, from = r.head;
  const to = view.moveByGroup(EditorSelection.cursor(from), forward).head;
  if (to === from) return true;
  const [a, b] = forward ? [from, to] : [to, from], text = view.state.sliceDoc(a, b);
  const l = lastWordKill, more = l && l.view === view && l.pos === from && Date.now() - l.at < 4000 && killRing.length;
  if (more && forward) killRing[0] += text;
  else if (more) killRing[0] = text + killRing[0];
  else kill(view, text);
  if (more) navigator.clipboard?.writeText(killRing[0])?.catch(() => {});
  view.dispatch({ changes: { from: a, to: b }, selection: { anchor: a }, effects: setMark.of(null), scrollIntoView: true,
                  userEvent: forward ? "delete.forward" : "delete.backward" });
  lastWordKill = { view, pos: a, at: Date.now() };
  return true;
}
function regionText(view) {
  const r = view.state.selection.main;
  return view.state.sliceDoc(r.from, r.to);
}
// A motion moves the cursor, or, while the mark is set, extends the region. (CodeMirror's cursor commands collapse a
// non-empty selection instead of moving, so after the first ⌃F the region would never grow again.)
// Without the mark a key CodeMirror already binds (⌃F, arrows…) is left to it (so ↑↓ still move in a completion
// list); the Emacs-only ones (⌥F ⌥B ⌃V ⌥V ⌥< ⌥>) move here.
const markActive = v => v.state.field(markField, false) != null;
const OWN = new Set(["Alt-f", "Alt-b", "Ctrl-v", "Alt-v", "Alt-<", "Alt->", "Alt-Shift-,", "Alt-Shift-.", "Alt-Shift-<", "Alt-Shift->"]);
const motion = (key, move, extend) => v => (markActive(v) ? extend(v) : OWN.has(key) ? move(v) : false);
const motionKeys = [
  ["Ctrl-f", cursorCharForward, selectCharForward], ["Ctrl-b", cursorCharBackward, selectCharBackward],
  ["Ctrl-n", cursorLineDown, selectLineDown], ["Ctrl-p", cursorLineUp, selectLineUp],
  ["Ctrl-a", cursorLineStart, selectLineStart], ["Ctrl-e", cursorLineEnd, selectLineEnd],
  ["Alt-f", cursorGroupForward, selectGroupForward], ["Alt-b", cursorGroupBackward, selectGroupBackward],
  ["Ctrl-v", cursorPageDown, selectPageDown], ["Alt-v", cursorPageUp, selectPageUp],
  ["PageDown", cursorPageDown, selectPageDown], ["PageUp", cursorPageUp, selectPageUp],
  ["Alt-<", cursorDocStart, selectDocStart], ["Alt->", cursorDocEnd, selectDocEnd],
  ["Alt-Shift-,", cursorDocStart, selectDocStart], ["Alt-Shift-.", cursorDocEnd, selectDocEnd],
  ["Alt-Shift-<", cursorDocStart, selectDocStart], ["Alt-Shift->", cursorDocEnd, selectDocEnd],
  ["ArrowLeft", cursorCharLeft, selectCharLeft], ["ArrowRight", cursorCharRight, selectCharRight],
  ["ArrowUp", cursorLineUp, selectLineUp], ["ArrowDown", cursorLineDown, selectLineDown],
  ["Alt-ArrowLeft", cursorGroupLeft, selectGroupLeft], ["Alt-ArrowRight", cursorGroupRight, selectGroupRight],
  ["Mod-ArrowLeft", cursorLineBoundaryLeft, selectLineBoundaryLeft], ["Mod-ArrowRight", cursorLineBoundaryRight, selectLineBoundaryRight],
  ["Mod-ArrowUp", cursorDocStart, selectDocStart], ["Mod-ArrowDown", cursorDocEnd, selectDocEnd],
  ["Home", cursorLineBoundaryLeft, selectLineBoundaryLeft], ["End", cursorLineBoundaryRight, selectLineBoundaryRight],
].map(([key, move, extend]) => ({ key, run: motion(key, move, extend) }));
const emacsKeys = [
  ...motionKeys,
  { key: "Ctrl-Space", run: v => { const h = v.state.selection.main.head; v.dispatch({ effects: setMark.of(h), selection: { anchor: h } }); return true; } },
  { key: "Ctrl-g", run: v => { const h = v.state.selection.main.head; v.dispatch({ effects: setMark.of(null), selection: { anchor: h } }); return true; } },
  { key: "Ctrl-w", run: v => {
      const r = v.state.selection.main;
      if (r.empty) return false;
      kill(v, regionText(v));
      v.dispatch({ changes: { from: r.from, to: r.to }, effects: setMark.of(null), userEvent: "delete.cut" });
      return true; } },
  { key: "Alt-w", run: v => {
      const r = v.state.selection.main;
      if (r.empty) return false;
      kill(v, regionText(v));
      v.dispatch({ effects: setMark.of(null), selection: { anchor: r.head } });
      return true; } },
  { key: "Ctrl-k", run: v => {
      const r = v.state.selection.main, line = v.state.doc.lineAt(r.head);
      const to = r.head === line.to ? Math.min(v.state.doc.length, line.to + 1) : line.to;
      if (to === r.head) return true;
      kill(v, v.state.sliceDoc(r.head, to));
      v.dispatch({ changes: { from: r.head, to }, userEvent: "delete.cut" });
      return true; } },
  { key: "Alt-d", run: v => killWord(v, true) },
  { key: "Alt-Backspace", run: v => killWord(v, false) },
  { key: "Ctrl-y", run: v => {
      const ins = text => { const r = v.state.selection.main; v.dispatch({ changes: { from: r.from, to: r.to, insert: text }, selection: { anchor: r.from + text.length }, userEvent: "input.paste" }); };
      if (killRing.length) ins(killRing[0]);
      else navigator.clipboard?.readText()?.then(ins, () => {});
      return true; } },
  { key: "Ctrl-/", run: undo },
  { key: "Mod-z", run: undo }, { key: "Mod-Shift-z", run: redo },
  { key: "Ctrl-l", run: v => { recenter(v); return true; } },
];
let recenterAt = 0, recenterStep = 0;
function recenter(view) {  // middle, then top, then bottom; a press after 1.5 s starts again at the middle
  const now = Date.now();
  recenterStep = now - recenterAt < 1500 ? (recenterStep + 1) % 3 : 0;
  recenterAt = now;
  const y = ["center", "start", "end"][recenterStep];
  view.dispatch({ effects: EditorView.scrollIntoView(view.state.selection.main.head, { y }) });
}

// ------------------------------------------------------------------ links: click opens, ⌥-click edits
function linkAt(view, pos) {
  const tree = syntaxTree(view.state);
  let node = tree.resolveInner(pos, 1);
  for (let n = node; n; n = n.parent) {
    if (n.name === "URL") return view.state.sliceDoc(n.from, n.to).replace(/^<|>$/g, "");
    if (n.name === "Link" || n.name === "Image") {
      const url = n.getChild("URL");
      if (url) return view.state.sliceDoc(url.from, url.to).replace(/^<|>$/g, "");
    }
  }
  // bare URLs in any file
  const line = view.state.doc.lineAt(pos);
  const re = /https?:\/\/[^\s<>()"'`]+/g;
  let m;
  while ((m = re.exec(line.text))) {
    const from = line.from + m.index, to = from + m[0].length;
    if (pos >= from && pos <= to) return m[0].replace(/[.,;:]+$/, "");
  }
  return null;
}

// ------------------------------------------------------------------ [[wiki links]] (notes)
const WIKI_RE = /\[\[([^\]\n]+)\]\]/g;
const wikiMarks = new MatchDecorator({
  regexp: WIKI_RE,
  decoration: () => Decoration.mark({ class: "cm-wikilink" }),
});
// A decoration builder that throws must not take the editor down: the text stays, that update gets no decorations,
// and the page logs the state (CM.onError, set by the page).
export const hooks = { onError: null };
function guarded(where, view, build, fallback) {
  try { return build(); } catch (err) {
    try {
      const sel = view.state.selection.main;
      if (hooks.onError) hooks.onError({ where, docLength: view.state.doc.length, selection: [sel.from, sel.to],
        message: String(err && err.message || err), stack: String(err && err.stack || "") });
    } catch {}
    return fallback;
  }
}
const wikiPlugin = ViewPlugin.fromClass(class {
  constructor(view) { this.decorations = guarded("wiki links", view, () => wikiMarks.createDeco(view), Decoration.none); }
  update(u) {
    this.decorations = guarded("wiki links", u.view, () => wikiMarks.updateDeco(u, this.decorations), Decoration.none);
  }
}, { decorations: v => v.decorations });
/** The note a [[link]] at pos names ("Name" of [[Name#Heading|alias]]), or null. */
function wikiAt(view, pos) {
  const line = view.state.doc.lineAt(pos);
  WIKI_RE.lastIndex = 0;
  let m;
  while ((m = WIKI_RE.exec(line.text))) {
    const from = line.from + m.index, to = from + m[0].length;
    if (pos >= from && pos <= to) return m[1].split("|")[0].split("#")[0].trim();
  }
  return null;
}
function wikiCompletion(names) {
  return ctx => {
    const m = ctx.matchBefore(/\[\[[^\]\n|#]*/);
    if (!m) return null;
    const closed = ctx.state.sliceDoc(ctx.pos, ctx.pos + 2) === "]]";
    return {
      from: m.from + 2,
      options: names().map(n => ({ label: n, apply: closed ? n : n + "]]", type: "text" })),
      validFor: /^[^\]\n|#]*$/,
    };
  };
}

// ------------------------------------------------------------------ find (⌘F): every match marked, the current one selected
const setFind = StateEffect.define();
const findMark = Decoration.mark({ class: "cm-find" }), findCur = Decoration.mark({ class: "cm-find-cur" });
const findField = StateField.define({
  create: () => ({ q: "", deco: Decoration.none }),
  update(v, tr) {
    for (const e of tr.effects) if (e.is(setFind)) return e.value;
    return tr.docChanged ? { q: v.q, deco: v.deco.map(tr.changes) } : v;
  },
  provide: f => EditorView.decorations.from(f, v => v.deco),
});
function findAll(state, q) {
  const text = state.doc.toString().toLowerCase(), lq = q.toLowerCase(), out = [];
  if (!lq) return out;
  for (let i = text.indexOf(lq); i >= 0 && out.length < 20000; i = text.indexOf(lq, i + lq.length)) out.push([i, i + lq.length]);
  return out;
}
/** dir 0: the first match at or after the selection (typing); 1 / -1: next / previous, wrapping. */
function runFind(view, q, dir) {
  const ms = findAll(view.state, q);
  if (!ms.length) { view.dispatch({ effects: setFind.of({ q, deco: Decoration.none }) }); return { count: 0, index: -1 }; }
  const sel = view.state.selection.main;
  let i;
  if (dir === 0) i = ms.findIndex(m => m[0] >= sel.from);
  else if (dir > 0) i = ms.findIndex(m => m[0] >= sel.to && !(m[0] === sel.from && m[1] === sel.to));
  else { i = -1; for (let k = ms.length - 1; k >= 0; k--) if (ms[k][1] <= sel.from && !(ms[k][0] === sel.from && ms[k][1] === sel.to)) { i = k; break; } }
  if (i < 0) i = dir < 0 ? ms.length - 1 : 0;
  const deco = Decoration.set(ms.map(([a, b], k) => (k === i ? findCur : findMark).range(a, b)));
  view.dispatch({ selection: { anchor: ms[i][0], head: ms[i][1] }, effects: [setFind.of({ q, deco }), EditorView.scrollIntoView(ms[i][0], { y: "center" })] });
  return { count: ms.length, index: i };
}

// ------------------------------------------------------------------ factory
const theme = EditorView.theme({
  "&": { height: "100%", backgroundColor: "transparent", color: "var(--ink)" },
  ".cm-scroller": { fontFamily: "var(--mono)", fontSize: "var(--code-size, 13px)", lineHeight: "1.6" },
  ".cm-content": { caretColor: "var(--ink)", padding: "10px 0 40vh" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--ink)", borderLeftWidth: "1.5px" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": { backgroundColor: "var(--sel-focus) !important" },
  ".cm-gutters": { backgroundColor: "transparent", color: "var(--ink-3)", border: "none", paddingLeft: "8px" },
  ".cm-lineNumbers .cm-gutterElement": { padding: "0 12px 0 4px", minWidth: "32px" },
  ".cm-activeLine": { backgroundColor: "var(--hover)" },
  ".cm-activeLineGutter": { backgroundColor: "transparent", color: "var(--ink)" },
  ".cm-line": { padding: "0 16px 0 6px" },
  ".cm-matchingBracket": { backgroundColor: "var(--sel)", outline: "none" },
  ".cm-foldGutter .cm-gutterElement": { color: "var(--ink-3)", padding: "0 4px" },
  ".cm-wikilink": { color: "var(--link)", cursor: "pointer" },
  ".cm-find": { backgroundColor: "var(--sel)", borderRadius: "2px" },
  ".cm-find-cur": { backgroundColor: "var(--run-soft)", boxShadow: "0 0 0 1px var(--run)", borderRadius: "2px" },
  ".cm-tooltip": { border: "none", backgroundColor: "var(--surface)", borderRadius: "var(--r-m)", boxShadow: "var(--menu-shadow)" },
  ".cm-tooltip.cm-tooltip-autocomplete > ul": { fontFamily: "var(--font)", fontSize: "12px", padding: "4px", maxHeight: "16em" },
  ".cm-tooltip.cm-tooltip-autocomplete > ul > li": { padding: "4px 8px", borderRadius: "var(--r-s)", color: "var(--ink)" },
  ".cm-tooltip-autocomplete ul li[aria-selected]": { backgroundColor: "var(--sel)", color: "var(--ink)" },
  ".cm-completionMatchedText": { textDecoration: "none", fontWeight: "600" },
  ".cm-completionIcon": { display: "none" },
  "&.cm-focused": { outline: "none" },
});
const composerTheme = Prec.highest(EditorView.theme({
  "&": { height: "auto", maxHeight: "260px", color: "var(--ink)" },
  ".cm-scroller": { fontFamily: "var(--read-font, var(--font))", fontSize: "calc(var(--read-size) - .5px)", lineHeight: "1.55", overflow: "auto" },
  ".cm-content": { padding: "2px 0 6px", minHeight: "24px" },
  ".cm-line": { padding: "0 2px" },
  ".cm-activeLine": { backgroundColor: "transparent" },
  ".cm-placeholder": { color: "var(--ink-3)" },
  ".cm-lp-codeline": { fontSize: ".9em" },
}));
const noteTheme = EditorView.theme({
  "&": { color: "var(--note-ink)" },
  ".cm-scroller": { fontFamily: "var(--note-font)", fontSize: "var(--note-size, 15.5px)", lineHeight: "1.7" },
  // minWidth 0: the column never grows past the pane (a wide table scrolls in its own box instead); blocks (a table,
  // a formula) get the lines' 32px sides
  ".cm-content": { maxWidth: "820px", minWidth: "0", margin: "0 auto", padding: "28px 0 40vh" },
  ".cm-line": { padding: "0 32px" },
  ".cm-lp-table": { marginLeft: "32px", marginRight: "32px" },
  ".cm-lp-mathblock": { paddingLeft: "32px", paddingRight: "32px" },
  ".cm-activeLine": { backgroundColor: "transparent" },
});

/**
 * createEditor({ parent, doc, path, onChange(doc), onOpenLink(url), extraKeys: [{key, run}],
 *               wiki: { open(name), names() },          — [[links]] for notes (Markdown only)
 *               preview: { resolve(src, embed), inline(md) },  — live preview (Markdown only)
 *               onFiles(files, pos),                     — files dropped or pasted in (File objects)
 *               zoom(url),                               — a click on a rendered image
 *               composer: { placeholder, attrs } })      — the input: UI font, conversation size, grows to 260px
 *                                                          (attrs: the content's, e.g. the phone keyboard's autocorrect)
 * Returns { view, getDoc(), setDoc(text, keepCursor), focus(), revealLine(text), posAt(x, y), linkAt(x, y), insert(pos, text),
 *           markdown, language }   (linkAt: the link at a point, {url} or {wiki}, or null: a link's context menu)
 */
export function createEditor({ parent, doc, path, onChange, onOpenLink, extraKeys = [], wiki = null, preview = null, onFiles = null,
                              zoom = null, composer = null }) {
  const ext = (path.split(".").pop() || "").toLowerCase();
  const isMd = MARKDOWN_EXT.has(ext);
  const lang = new Compartment(), ph = new Compartment();
  const view = new EditorView({
    parent,
    state: EditorState.create({
      doc,
      extensions: [
        markField, extendFromMark, findField,
        keymap.of([...extraKeys, ...emacsKeys]),
        isMd ? [] : [lineNumbers(), highlightActiveLineGutter(), foldGutter()],
        highlightSpecialChars(), history(), drawSelection(), dropCursor(), rectangularSelection(),
        indentOnInput(), bracketMatching(), highlightActiveLine(), indentUnit.of("  "),
        EditorState.allowMultipleSelections.of(true),
        isMd ? EditorView.lineWrapping : [],
        lang.of(languageFor(path)),
        isMd && wiki ? [wikiPlugin, autocompletion({ override: [wikiCompletion(wiki.names)], icons: false })] : [],
        composer ? [composerTheme, ph.of(cmPlaceholder(composer.placeholder || "")),
                    composer.attrs ? EditorView.contentAttributes.of(composer.attrs) : []] : [],
        isMd && preview ? livePreview({
          resolve: preview.resolve, inline: preview.inline, zoom, notes: !composer,
          open: t => (t.wiki ? wiki && wiki.open(t.wiki) : onOpenLink && onOpenLink(t.url)),
          onError: (where, err, state) => {
            if (hooks.onError) hooks.onError({ where, docLength: state.doc.length, selection: [state.selection.main.from, state.selection.main.to],
              message: String(err && err.message || err), stack: String(err && err.stack || "") });
          },
        }) : [],
        syntaxHighlighting(isMd ? noteStyle : codeStyle),
        theme, isMd && !composer ? Prec.highest(noteTheme) : [],
        keymap.of([...(isMd && wiki ? completionKeymap : []), indentWithTab, ...defaultKeymap, ...historyKeymap, ...foldKeymap]),
        EditorView.updateListener.of(u => { if (u.docChanged && onChange) onChange(u); }),
        EditorView.domEventHandlers({
          drop(e, v) {  // files from Finder / the desktop (a browser drop: File objects; the app sends paths itself)
            const files = e.dataTransfer && [...e.dataTransfer.files];
            if (!files || !files.length || !onFiles) return false;
            e.preventDefault();
            const pos = v.posAtCoords({ x: e.clientX, y: e.clientY }) ?? v.state.selection.main.head;
            onFiles(files, pos);
            return true;
          },
          paste(e, v) {  // a pasted image / file goes the same way
            const files = e.clipboardData && [...e.clipboardData.files];
            if (!files || !files.length || !onFiles) return false;
            e.preventDefault();
            onFiles(files, v.state.selection.main.head);
            return true;
          },
          mousedown(e, v) {
            if (e.button !== 0 || e.altKey || e.shiftKey) return false;  // ⌥-click edits the link text
            const pos = v.posAtCoords({ x: e.clientX, y: e.clientY });
            if (pos == null) return false;
            if (isMd && wiki) {
              const el = e.target.closest && e.target.closest(".cm-wikilink");
              const name = el ? (el.dataset.wiki || wikiAt(v, pos)) : null;
              if (name) { e.preventDefault(); wiki.open(name); return true; }
            }
            const url = linkAt(v, pos);
            if (!url || !onOpenLink) return false;
            // A plain click opens it in Markdown; in code only ⌘-click does (a plain click places the cursor).
            if (!isMd && !e.metaKey) return false;
            e.preventDefault();
            onOpenLink(url);
            return true;
          },
        }),
      ],
    }),
  });
  return {
    view, markdown: isMd, language: languageName(path),
    getDoc: () => view.state.doc.toString(),
    setDoc(text, keepCursor = true) {
      const head = Math.min(view.state.selection.main.head, text.length);
      const top = view.scrollDOM.scrollTop;
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text },
                      selection: keepCursor ? { anchor: head } : { anchor: 0 }, annotations: [] });
      if (keepCursor) view.scrollDOM.scrollTop = top;
    },
    focus: () => view.focus(),
    linkAt(x, y) {
      const pos = view.posAtCoords({ x, y });
      if (pos == null) return null;
      if (isMd && wiki) { const name = wikiAt(view, pos); if (name) return { wiki: name }; }
      const url = linkAt(view, pos);
      return url ? { url } : null;
    },
    setPlaceholder: text => view.dispatch({ effects: ph.reconfigure(cmPlaceholder(text || "")) }),
    find: (q, dir = 0) => runFind(view, q, dir),
    clearFind: () => view.dispatch({ effects: setFind.of({ q: "", deco: Decoration.none }) }),
    revealLine(text) {  // put the cursor at the end of the first line equal to text, in the middle of the view
      for (let i = 1; i <= view.state.doc.lines; i++) {
        const line = view.state.doc.line(i);
        if (line.text === text) {
          view.dispatch({ selection: { anchor: line.to }, effects: EditorView.scrollIntoView(line.to, { y: "center" }) });
          return true;
        }
      }
      return false;
    },
    selectAll: () => selectAll(view),
    posAt: (x, y) => view.posAtCoords({ x, y }),
    insert(pos, text) {  // insert at pos (null: the cursor), cursor after it
      const at = pos ?? view.state.selection.main.head;
      view.dispatch({ changes: { from: at, insert: text }, selection: { anchor: at + text.length }, userEvent: "input.drop", scrollIntoView: true });
      view.focus();
    },
  };
}
export { EditorView, EditorSelection };
