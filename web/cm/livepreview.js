// Obsidian-style live preview for the Markdown editor (builddoc/features/files.md §0): marks hidden off the cursor line, images and
// ![[embeds]], KaTeX math, tables as a grid, clickable [ ] / [x] checkboxes, ==highlight==, #tags as chips.
// The source stays the document: every decoration is a view of it, and the line(s) holding a selection show the source.

import { StateField, Prec } from "@codemirror/state";
import { EditorView, Decoration, WidgetType } from "@codemirror/view";
import { syntaxTree, ensureSyntaxTree } from "@codemirror/language";

const MAX_DOC = 400_000;  // bigger documents keep the plain styled source
const IMAGE_EXT = /\.(png|jpe?g|gif|webp|svg|bmp|avif|heic)$/i;

// ------------------------------------------------------------------ widgets
class ImageWidget extends WidgetType {
  constructor(url, alt, width, block) { super(); this.url = url; this.alt = alt; this.width = width; this.block = block; }
  eq(o) { return o.url === this.url && o.width === this.width && o.alt === this.alt; }
  toDOM() {
    const wrap = document.createElement("span");
    wrap.className = "cm-lp-img";
    if (!this.url) { wrap.classList.add("bad"); wrap.textContent = this.alt || "image not found"; return wrap; }
    const img = document.createElement("img");
    img.src = this.url; img.alt = this.alt || ""; img.loading = "lazy"; img.draggable = false;
    if (this.width) img.style.width = this.width + "px";
    img.onerror = () => { wrap.classList.add("bad"); wrap.textContent = `${this.alt || "image"} (not found)`; };
    wrap.appendChild(img);
    return wrap;
  }
  ignoreEvent() { return false; }
}
class EmbedWidget extends WidgetType {  // ![[note]] or ![[file.pdf]]: a link chip (a note embed shows its name)
  constructor(name) { super(); this.name = name; }
  eq(o) { return o.name === this.name; }
  toDOM() {
    const s = document.createElement("span");
    s.className = "cm-lp-embed cm-wikilink";
    s.dataset.wiki = this.name;
    s.textContent = "↪ " + this.name;
    return s;
  }
  ignoreEvent() { return false; }
}
class MathWidget extends WidgetType {
  constructor(tex, display) { super(); this.tex = tex; this.display = display; }
  eq(o) { return o.tex === this.tex && o.display === this.display; }
  toDOM() {
    const el = document.createElement(this.display ? "div" : "span");
    el.className = this.display ? "cm-lp-mathblock" : "cm-lp-math";
    const k = typeof window !== "undefined" && window.katex;
    if (k) {
      try { el.innerHTML = k.renderToString(this.tex, { displayMode: this.display, throwOnError: false }); return el; } catch {}
    }
    el.textContent = this.display ? `$$${this.tex}$$` : `$${this.tex}$`;
    return el;
  }
  ignoreEvent() { return false; }
}
class CheckboxWidget extends WidgetType {
  constructor(checked) { super(); this.checked = checked; }
  eq(o) { return o.checked === this.checked; }
  toDOM(view) {
    const box = document.createElement("input");
    box.type = "checkbox"; box.checked = this.checked; box.className = "cm-lp-check";
    box.addEventListener("mousedown", e => {
      e.preventDefault();
      const pos = view.posAtDOM(box);  // the "[" of "[ ]"
      const cur = view.state.sliceDoc(pos, pos + 3);
      if (!/^\[[ xX]\]$/.test(cur)) return;
      view.dispatch({ changes: { from: pos + 1, to: pos + 2, insert: cur[1] === " " ? "x" : " " }, userEvent: "input.toggle" });
    });
    return box;
  }
  ignoreEvent() { return true; }
}
class HrWidget extends WidgetType {
  eq() { return true; }
  toDOM() { const s = document.createElement("span"); s.className = "cm-lp-hr"; return s; }
}

/** Split a table row into cells: outer pipes dropped, `\|` stays inside a cell (and becomes `|`). */
function cells(line) {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  const out = [];
  let cur = "";
  for (let i = 0; i < s.length; i++) {
    if (s[i] === "\\" && s[i + 1] === "|") { cur += "|"; i++; continue; }
    if (s[i] === "|") { out.push(cur.trim()); cur = ""; continue; }
    cur += s[i];
  }
  out.push(cur.trim());
  return out;
}
class TableWidget extends WidgetType {
  constructor(text, opts) { super(); this.text = text; this.opts = opts; }
  eq(o) { return o.text === this.text; }
  toDOM(view) {
    const lines = this.text.split("\n").filter(l => l.trim());
    const head = cells(lines[0] || "");
    const align = cells(lines[1] || "").map(a => /^:-+:$/.test(a) ? "center" : /^-+:$/.test(a) ? "right" : "");
    const rows = lines.slice(2).map(cells);
    const cell = (tag, txt, i) => `<${tag}${align[i] ? ` style="text-align:${align[i]}"` : ""}>${this.opts.inline(txt)}</${tag}>`;
    const box = document.createElement("div");
    box.className = "cm-lp-table";
    box.innerHTML = `<table><thead><tr>${head.map((c, i) => cell("th", c, i)).join("")}</tr></thead><tbody>${
      rows.map(r => `<tr>${head.map((_, i) => cell("td", r[i] || "", i)).join("")}</tr>`).join("")}</tbody></table>`;
    box.addEventListener("mousedown", e => {
      const a = e.target.closest("a[href], [data-wiki]");
      if (a) { e.preventDefault(); this.opts.open(a.dataset.wiki ? { wiki: a.dataset.wiki } : { url: a.getAttribute("href") }); return; }
      e.preventDefault();  // anywhere else: the source comes back, the cursor at the table's start
      const pos = view.posAtDOM(box);
      view.dispatch({ selection: { anchor: pos } });
      view.focus();
    });
    return box;
  }
  ignoreEvent() { return true; }
}

// ------------------------------------------------------------------ building the decorations
const hide = Decoration.replace({});
const mark = cls => Decoration.mark({ class: cls });
const line = cls => Decoration.line({ class: cls });
class TextWidget extends WidgetType {  // • for a bullet, an emoji for :shortcode:
  constructor(text, cls) { super(); this.text = text; this.cls = cls; }
  eq(o) { return o.text === this.text && o.cls === this.cls; }
  toDOM() { const s = document.createElement("span"); s.className = this.cls; s.textContent = this.text; return s; }
}
const EMOJI = { smile: "😄", smiley: "😃", grin: "😁", joy: "😂", laughing: "😆", wink: "😉", blush: "😊", heart_eyes: "😍",
  thinking: "🤔", thinking_face: "🤔", neutral_face: "😐", expressionless: "😑", unamused: "😒", sweat_smile: "😅", sob: "😭",
  cry: "😢", scream: "😱", rage: "😡", angry: "😠", sunglasses: "😎", upside_down_face: "🙃", slightly_smiling_face: "🙂",
  innocent: "😇", sleeping: "😴", mask: "😷", nerd_face: "🤓", partying_face: "🥳", exploding_head: "🤯", shushing_face: "🤫",
  facepalm: "🤦", shrug: "🤷", eyes: "👀", wave: "👋", clap: "👏", pray: "🙏", muscle: "💪", ok_hand: "👌", raised_hands: "🙌",
  "+1": "👍", thumbsup: "👍", "-1": "👎", thumbsdown: "👎", point_right: "👉", point_left: "👈", point_up: "☝️", point_down: "👇",
  heart: "❤️", broken_heart: "💔", sparkles: "✨", star: "⭐", star2: "🌟", fire: "🔥", boom: "💥", zap: "⚡", tada: "🎉",
  confetti_ball: "🎊", rocket: "🚀", bulb: "💡", memo: "📝", pencil2: "✏️", book: "📖", books: "📚", bookmark: "🔖",
  calendar: "📅", date: "📅", clock: "🕐", hourglass: "⌛", alarm_clock: "⏰", bell: "🔔", lock: "🔒", unlock: "🔓", key: "🔑",
  mag: "🔍", link: "🔗", paperclip: "📎", pushpin: "📌", round_pushpin: "📍", chart_with_upwards_trend: "📈",
  chart_with_downwards_trend: "📉", bar_chart: "📊", clipboard: "📋", file_folder: "📁", open_file_folder: "📂", package: "📦",
  email: "📧", inbox_tray: "📥", outbox_tray: "📤", phone: "📞", computer: "💻", keyboard: "⌨️", gear: "⚙️", wrench: "🔧",
  hammer: "🔨", hammer_and_wrench: "🛠️", nut_and_bolt: "🔩", bug: "🐛", test_tube: "🧪", microscope: "🔬", robot: "🤖",
  white_check_mark: "✅", heavy_check_mark: "✔️", check: "✔️", x: "❌", negative_squared_cross_mark: "❎", warning: "⚠️",
  no_entry: "⛔", no_entry_sign: "🚫", question: "❓", exclamation: "❗", bangbang: "‼️", interrobang: "⁉️", "100": "💯",
  red_circle: "🔴", large_blue_circle: "🔵", green_circle: "🟢", yellow_circle: "🟡", white_circle: "⚪", black_circle: "⚫",
  arrow_right: "➡️", arrow_left: "⬅️", arrow_up: "⬆️", arrow_down: "⬇️", leftwards_arrow_with_hook: "↩️", repeat: "🔁",
  recycle: "♻️", new: "🆕", free: "🆓", up: "🆙", cool: "🆒", sos: "🆘", info: "ℹ️", moneybag: "💰", dollar: "💵",
  money_with_wings: "💸", gift: "🎁", trophy: "🏆", medal: "🏅", dart: "🎯", art: "🎨", musical_note: "🎵", coffee: "☕",
  tea: "🍵", beer: "🍺", pizza: "🍕", cake: "🍰", apple: "🍎", sunny: "☀️", cloud: "☁️", umbrella: "☔", snowflake: "❄️",
  rainbow: "🌈", earth_asia: "🌏", globe_with_meridians: "🌐", house: "🏠", office: "🏢", car: "🚗", airplane: "✈️",
  construction: "🚧", rotating_light: "🚨", checkered_flag: "🏁", triangular_flag_on_post: "🚩", seedling: "🌱", herb: "🌿",
  four_leaf_clover: "🍀", cat: "🐱", dog: "🐶", snail: "🐌", turtle: "🐢", see_no_evil: "🙈", speech_balloon: "💬",
  thought_balloon: "💭", zzz: "💤", hourglass_flowing_sand: "⏳", stopwatch: "⏱️", lightning: "⚡", gem: "💎", crown: "👑",
  brain: "🧠", handshake: "🤝", writing_hand: "✍️", man_technologist: "👨‍💻", woman_technologist: "👩‍💻", ghost: "👻",
  skull: "💀", poop: "💩", hankey: "💩", alien: "👽", sweat: "😓", relieved: "😌", yum: "😋", stuck_out_tongue: "😛",
  zany_face: "🤪", hugs: "🤗", hugging_face: "🤗", face_with_monocle: "🧐", star_struck: "🤩", money_mouth_face: "🤑" };

function build(state, opts) {
  const doc = state.doc;
  if (doc.length > MAX_DOC) return Decoration.none;
  const tree = ensureSyntaxTree(state, doc.length, 200) || syntaxTree(state);
  const notes = !!opts.notes;  // tasks, ==highlight==, #tags, [[wiki]] (Markdown files and notes, not the input)
  // REBUILD editor.md §1: the raw form shows only where the cursor is: on a block mark itself, inside (or touching)
  // an inline construct, on the line of a quote or a rule, in the lines of a table or a formula.
  const ranges = state.selection.ranges;
  const near = (a, b) => ranges.some(r => r.from <= b && r.to >= a);
  // Math renders as soon as it is closed: its source shows only while the cursor is strictly inside it (typing the
  // closing $ leaves the cursor just after it, so the formula appears at once, on the same line).
  const within = (a, b) => ranges.some(r => r.from > a && r.to < b);
  const lineOf = pos => doc.lineAt(pos);
  const nearLines = (a, b) => near(lineOf(a).from, lineOf(b).to);
  const decos = [];
  const code = [];     // ranges no inline rule may touch (code, links, formulas)
  const covered = [];  // ranges replaced by a block widget: nothing else inside
  const inside = (list, from, to) => list.some(([x, y]) => from < y && to > x);
  const add = (from, to, d) => { if (from < to) decos.push(d.range(from, to)); };
  const block = (from, to, widget) => { decos.push(Decoration.replace({ widget, block: true }).range(from, to)); covered.push([from, to]); };

  // Display math first ($$ … $$ or \[ … \], on one line or over several), so the tree walk can skip inside it.
  for (let n = 1; n <= doc.lines; n++) {
    const l = doc.line(n);
    const one = /^\s*(?:\$\$(.+)\$\$|\\\[(.+)\\\])\s*$/.exec(l.text);
    if (one) {
      if (!within(l.from, l.to)) block(l.from, l.to, new MathWidget((one[1] || one[2]).trim(), true));
      else code.push([l.from, l.to]);
      continue;
    }
    const open = /^\s*(\$\$|\\\[)\s*$/.exec(l.text);
    if (open) {
      const close = open[1] === "$$" ? /^\s*\$\$\s*$/ : /^\s*\\\]\s*$/;
      let m = n + 1;
      while (m <= doc.lines && !close.test(doc.line(m).text)) m++;
      if (m > doc.lines) break;
      const end = doc.line(m);
      const tex = doc.sliceString(l.to + 1, end.from).replace(/\n$/, "");
      if (!within(l.from, end.to)) block(l.from, end.to, new MathWidget(tex, true));
      else code.push([l.from, end.to]);
      n = m;
    }
  }

  tree.iterate({
    enter: node => {
      const { name, from, to } = node;
      if (name !== "Document" && inside(covered, from, to)) return false;
      switch (name) {
        case "FencedCode": case "CodeBlock": case "HTMLBlock": case "CommentBlock": {
          code.push([from, to]);
          for (let p = from; p <= to;) { const l = lineOf(p); decos.push(line("cm-lp-codeline").range(l.from)); p = l.to + 1; }
          return false;
        }
        case "InlineCode": case "Emphasis": case "StrongEmphasis": case "Strikethrough": {
          if (name === "InlineCode") code.push([from, to]);
          if (!near(from, to)) {
            for (let ch = node.node.firstChild; ch; ch = ch.nextSibling)
              if (ch.name === "CodeMark" || ch.name === "EmphasisMark" || ch.name === "StrikethroughMark") add(ch.from, ch.to, hide);
          }
          return name === "InlineCode" ? false : undefined;
        }
        case "Table": {
          code.push([from, to]);
          const a = lineOf(from), b = lineOf(to);
          if (!near(a.from, b.to)) block(a.from, b.to, new TableWidget(doc.sliceString(a.from, b.to), opts));
          return false;
        }
        case "HeaderMark": {
          const parent = node.node.parent;
          if (parent && parent.name.startsWith("ATXHeading")) {
            const sp = doc.sliceString(to, to + 1) === " " ? 1 : 0;
            if (!near(from, to + sp)) add(from, to + sp, hide);
          }
          return;
        }
        case "ListMark": {
          const parent = node.node.parent, list = parent && parent.parent;
          if (!list || list.name !== "BulletList" || near(from, to + 1)) return;
          const task = notes && /^\s\[[ xX]\](\s|$)/.test(doc.sliceString(to, to + 5));
          if (task) add(from, to + 1, hide);  // a task shows its checkbox, not a bullet as well
          else decos.push(Decoration.replace({ widget: new TextWidget("•", "cm-lp-bullet") }).range(from, to));
          return;
        }
        case "QuoteMark": {
          const l = lineOf(from);
          decos.push(line("cm-lp-quote").range(l.from));
          if (!near(l.from, l.to)) add(from, to + (doc.sliceString(to, to + 1) === " " ? 1 : 0), hide);
          return;
        }
        case "HorizontalRule": {
          const l = lineOf(from);
          if (!near(l.from, l.to)) decos.push(Decoration.replace({ widget: new HrWidget() }).range(from, to));
          return false;
        }
        case "Image": {
          if (doc.sliceString(from, from + 3) === "![[") return false;  // ![[embed]]: the wiki rule below
          const c = node.node, url = c.getChild("URL");
          const alt0 = doc.sliceString(from + 2, (c.getChildren("LinkMark")[1] || { from: from + 2 }).from);
          const [alt, w] = alt0.split("|");
          const src = url ? doc.sliceString(url.from, url.to).replace(/^<|>$/g, "") : "";
          const widget = new ImageWidget(src ? opts.resolve(src, false) : "", alt, /^\d+$/.test(w || "") ? +w : 0);
          if (!near(from, to)) decos.push(Decoration.replace({ widget }).range(from, to));
          else decos.push(Decoration.widget({ widget, side: 1 }).range(to));
          code.push([from, to]);
          return false;
        }
        case "Link": {
          // [[wiki]] parses as a link inside brackets: leave it to the wiki rule below
          if (doc.sliceString(from - 1, from) === "[" || doc.sliceString(from, from + 2) === "[[") return false;
          const c = node.node, marks = c.getChildren("LinkMark");
          code.push([from, to]);
          if (marks.length >= 2 && doc.sliceString(from, from + 1) === "[") {
            add(marks[0].to, marks[1].from, mark("cm-lp-link"));
            if (!near(from, to)) { add(marks[0].from, marks[0].to, hide); add(marks[1].from, to, hide); }
          }
          return false;
        }
        case "URL":
          code.push([from, to]);
          return;
      }
    },
  });

  // Line-based inline rules, outside code and block widgets.
  const headings = [];
  tree.iterate({ enter: n => { if (n.name.startsWith("ATXHeading") || n.name.startsWith("SetextHeading")) { headings.push([n.from, n.to]); return false; } } });
  for (let n = 1; n <= doc.lines; n++) {
    const l = doc.line(n);
    if (!l.text || inside(covered, l.from, l.to + 1)) continue;
    const free = (a, b) => !inside(code, l.from + a, l.from + b);
    let m;
    if (notes) {
      // [ ] / [x] checkboxes, with or without a list marker
      const cb = /^(\s*(?:[-*+]\s+|\d+[.)]\s+)?)\[([ xX])\](?=\s|$)/.exec(l.text);
      if (cb) {
        const a = l.from + cb[1].length, b = a + 3;
        if (!near(a, b)) decos.push(Decoration.replace({ widget: new CheckboxWidget(cb[2] !== " ") }).range(a, b));
        if (cb[2] !== " ") decos.push(mark("cm-lp-done").range(b, l.to));
      }
      // ![[embed]] and [[wiki|alias]]
      const wre = /(!?)\[\[([^\]\n]+?)\]\]/g;
      while ((m = wre.exec(l.text))) {
        const a = l.from + m.index, b = a + m[0].length;
        if (!free(m.index, m.index + m[0].length)) continue;
        const target = m[2].replace(/\\\|/g, "|").split("|")[0].split("#")[0].trim();
        if (m[1]) {
          const parts = m[2].split("|"), w = parts[1] && /^\d+$/.test(parts[1].trim()) ? +parts[1].trim() : 0;
          const widget = IMAGE_EXT.test(target) ? new ImageWidget(opts.resolve(target, true), target, w) : new EmbedWidget(target);
          if (!near(a, b)) decos.push(Decoration.replace({ widget }).range(a, b));
          else decos.push(Decoration.widget({ widget, side: 1 }).range(b));
        } else {
          decos.push(mark("cm-wikilink").range(a, b));
          if (!near(a, b)) {
            const bar = m[2].search(/(?<!\\)\|/);
            decos.push(hide.range(a, bar >= 0 ? a + 2 + bar + 1 : a + 2));
            decos.push(hide.range(b - 2, b));
          }
        }
        code.push([a, b]);
      }
    }
    // inline math: $…$ and \(…\)
    const mre = /(^|[^\\$])\$(?=\S)([^$\n]*?\S)\$(?!\$|\d)|\\\((.+?)\\\)/g;
    while ((m = mre.exec(l.text))) {
      const s = m[3] !== undefined ? m.index : m.index + m[1].length, e = m.index + m[0].length;
      if (!free(s, e)) continue;
      const a = l.from + s, b = l.from + e;
      if (!within(a, b)) decos.push(Decoration.replace({ widget: new MathWidget(m[3] !== undefined ? m[3] : m[2], false) }).range(a, b));
      code.push([a, b]);
    }
    // :shortcode: emoji
    const ere = /:([a-z0-9_+-]{1,40}):/g;
    while ((m = ere.exec(l.text))) {
      const e = EMOJI[m[1]];
      if (!e || !free(m.index, m.index + m[0].length)) continue;
      const a = l.from + m.index, b = a + m[0].length;
      if (!near(a, b)) decos.push(Decoration.replace({ widget: new TextWidget(e, "cm-lp-emoji") }).range(a, b));
    }
    if (!notes) continue;
    // ==highlight==
    const hre = /==(?=\S)([^=\n]*?\S)==/g;
    while ((m = hre.exec(l.text))) {
      const a = l.from + m.index, b = a + m[0].length;
      if (!free(m.index, m.index + m[0].length)) continue;
      decos.push(mark("cm-lp-hl").range(a + 2, b - 2));
      if (!near(a, b)) { decos.push(hide.range(a, a + 2)); decos.push(hide.range(b - 2, b)); }
    }
    // #tags (not all digits, not in headings, code or links)
    if (inside(headings, l.from, l.to)) continue;
    const tre = /(^|[\s(])#([\p{L}\p{N}_][\p{L}\p{N}_\/-]*)/gu;
    while ((m = tre.exec(l.text))) {
      if (/^\d+$/.test(m[2])) continue;
      const s = m.index + m[1].length, e = s + 1 + m[2].length;
      if (!free(s, e)) continue;
      decos.push(mark("cm-lp-tag").range(l.from + s, l.from + e));
    }
  }
  return Decoration.set(decos, true);
}

/**
 * livePreview({ resolve(src, embed) → url|"", inline(markdown) → html, open({url}|{wiki}), zoom(url), notes: bool,
 *               onError(where, err) })   notes: tasks, ==highlight==, #tags and [[wiki]] (files and notes, not the input)
 */
export function livePreview(opts) {
  const safe = state => {
    try { return build(state, opts); } catch (err) { if (opts.onError) opts.onError("live preview", err, state); return Decoration.none; }
  };
  const activeKey = state => state.selection.ranges.map(r => r.from + "." + r.to).join(",");
  const field = StateField.define({
    create: state => ({ deco: safe(state), key: activeKey(state) }),
    update(v, tr) {
      const key = activeKey(tr.state);
      // A new parse (the tree grew) also rebuilds: the first build may have seen half a tree.
      if (!tr.docChanged && key === v.key && syntaxTree(tr.state) === syntaxTree(tr.startState)) return v;
      return { deco: safe(tr.state), key };
    },
    provide: f => EditorView.decorations.from(f, v => v.deco),
  });
  return [field, lpTheme, Prec.low(EditorView.domEventHandlers({
    mousedown(e, view) {  // a click on an image zooms it (lightbox); on a formula, the cursor goes in: its source shows
      const img = e.target.closest && e.target.closest(".cm-lp-img img");
      if (img && opts.zoom) { e.preventDefault(); opts.zoom(img.src); return true; }
      const w = e.target.closest && e.target.closest(".cm-lp-img, .cm-lp-math, .cm-lp-mathblock");
      if (!w) return false;
      e.preventDefault();
      const off = w.classList.contains("cm-lp-mathblock") ? 2 : w.classList.contains("cm-lp-math") ? 1 : 0;  // inside the $ / $$
      view.dispatch({ selection: { anchor: Math.min(view.posAtDOM(w) + off, view.state.doc.length) } });
      view.focus();
      return true;
    },
  }))];
}

const lpTheme = EditorView.theme({
  ".cm-lp-link": { color: "var(--link)", cursor: "pointer" },
  ".cm-lp-img": { display: "inline-block", maxWidth: "100%", verticalAlign: "bottom", margin: "4px 0" },
  ".cm-lp-img img": { display: "block", maxWidth: "260px", maxHeight: "180px", width: "auto", height: "auto",
    borderRadius: "var(--r-s)", border: "1px solid var(--line)", cursor: "zoom-in" },
  ".cm-lp-img img[style*=width]": { maxWidth: "100%", maxHeight: "none" },
  ".cm-lp-bullet": { color: "var(--ink-3)", display: "inline-block", minWidth: ".6em" },
  ".cm-lp-emoji": { fontFamily: "'Apple Color Emoji', 'Segoe UI Emoji', sans-serif" },
  ".cm-wikilink": { color: "var(--link)", cursor: "pointer", textDecoration: "underline dotted 1px", textUnderlineOffset: "3px" },
  ".cm-lp-img.bad": { color: "var(--ink-3)", fontSize: ".85em", border: "1px dashed var(--line)", borderRadius: "var(--r-s)", padding: "2px 8px" },
  ".cm-lp-embed": { fontSize: ".92em" },
  ".cm-lp-math": { cursor: "text" },
  // contain: a wide block scrolls inside itself; its width never widens the editor's column
  ".cm-lp-mathblock": { textAlign: "center", padding: "6px 0", overflowX: "auto", cursor: "text", contain: "inline-size" },
  ".cm-lp-check": { margin: "0 .35em 0 0", verticalAlign: "-0.1em", cursor: "pointer", accentColor: "var(--ink-2)" },
  ".cm-lp-done": { color: "var(--ink-3)", textDecoration: "line-through", textDecorationColor: "var(--ink-3)" },
  ".cm-lp-hl": { backgroundColor: "#fff3a3", color: "inherit", borderRadius: "2px", padding: "0 1px" },
  ".cm-lp-tag": { backgroundColor: "var(--chip-bg)", color: "var(--chip-fg)", borderRadius: "999px", padding: "0 .45em", fontSize: ".9em" },
  ".cm-lp-quote": { backgroundImage: "linear-gradient(var(--line), var(--line))", backgroundSize: "3px 100%", backgroundRepeat: "no-repeat",
    backgroundPosition: "32px 0", paddingLeft: "46px !important", color: "var(--ink-2)" },
  ".cm-widgetBuffer": { border: "0 !important" },
  ".cm-lp-codeline": { backgroundColor: "var(--code-bg)", fontFamily: "var(--mono)", fontSize: ".86em" },
  ".cm-lp-hr": { display: "inline-block", width: "100%", borderTop: "1px solid var(--line)", verticalAlign: "middle" },
  ".cm-lp-table": { overflow: "auto", maxHeight: "70vh", margin: "6px 0", contain: "inline-size", border: "1px solid var(--line)", borderRadius: "var(--r-s)",
    fontFamily: "var(--font)", fontSize: ".86em", lineHeight: "1.5", cursor: "text" },
  ".cm-lp-table table": { borderCollapse: "separate", borderSpacing: "0", minWidth: "100%" },
  // cells wrap (a narrow pane): headers stay on one line; a long word still scrolls the box rather than breaking
  ".cm-lp-table th, .cm-lp-table td": { padding: "6px 12px", borderBottom: "1px solid var(--line)", textAlign: "left", verticalAlign: "top" },
  ".cm-lp-table th": { whiteSpace: "nowrap" },
  ".cm-lp-table th": { position: "sticky", top: "0", background: "var(--note-bg)", fontWeight: "600", color: "var(--ink-2)",
    borderBottom: "2px solid var(--line)", zIndex: "1" },
  ".cm-lp-table tr:last-child td": { borderBottom: "0" },
  ".cm-lp-table a, .cm-lp-table [data-wiki]": { color: "var(--link)", textDecoration: "none", cursor: "pointer" },
  ".cm-lp-table code": { fontFamily: "var(--mono)", fontSize: ".9em", background: "var(--chip-bg)", color: "var(--chip-fg)", borderRadius: "4px", padding: "0 4px" },
});
