"use strict";
/* ===================================================================== focus */
/* A side tab never takes the keyboard on its own: it focuses only once the parent has focused its frame. */
function mayFocus() {
  if (!EMBED) return true;
  try { return !window.frameElement || parent.document.activeElement === window.frameElement; } catch { return true; }
}
function focusMain() {
  if (!mayFocus()) return;
  if (xOn()) { $("#xbody").focus({ preventScroll: true }); return; }
  if (EMBED_REVIEW) { $("#rvbody").focus({ preventScroll: true }); return; }
  if (isPluginTab(S.file)) { focusPView(); return; }  // a plugin's view: its reply editor, else its messages
  if (S.file) { const ed = EDITORS.get(S.file); if (ed && ed.ed) ed.ed.focus(); return; }
  if (cur()) input.focus({ preventScroll: true });
  else conv.focus({ preventScroll: true });
}
function focusPlace() {
  const a = document.activeElement;
  return a === input ? "input" : a === conv ? "conv" : panelFocused() ? "left" : sideFocused() ? "side" : S.file ? "editor" : "other";
}
setInterval(() => {
  if (document.hidden || EMBED) return;
  const k = tabKey(), w = sideFocused() ? "side" : mainFocused() ? "main" : null;
  if (k && w) S.tabFocus[k] = w;
  LS.set("focus", focusPlace()); saveUi();
}, 1000);

/* ===================================================================== modal layer: dialogs, menus, dropdowns */
let modal = null;
function openModal(m) {
  if (typeof hideTip === "function") hideTip();  // a tooltip never covers a menu or a dialog
  if (modal) closeModal();
  modal = m;
}
/* The app shell asks before it runs a native shortcut (⌘⇧R, ⌘`, menu items): a dialog here or in the side tab owns
   every key, so native shortcuts wait too. */
/* ⌘Z / ⌘⇧Z from the app shell: an editor (CodeMirror) undoes in its own history; a text field natively; the side tab
   and the shell get theirs. */
window.deskUndo = (redo = false) => {
  if (!EMBED && sideFocused()) { try { const w = sideFrame().contentWindow; if (w.deskUndo) return w.deskUndo(redo); } catch {} }
  const ae = document.activeElement;
  if (!ae || (typeof shellFocused === "function" && shellFocused())) return;
  if (ae.closest && ae.closest(".cm-editor")) {
    ae.dispatchEvent(new KeyboardEvent("keydown", { key: "z", code: "KeyZ", metaKey: true, shiftKey: redo, bubbles: true, cancelable: true }));
    return;
  }
  if (ae.matches && ae.matches("input, textarea")) document.execCommand(redo ? "redo" : "undo");
};
window.deskModal = () => {
  if (modal) return true;
  try { const w = !EMBED && sideFrame() && sideFrame().contentWindow; return !!(w && w.deskModal && w.deskModal()); } catch { return false; }
};
function closeModal() {
  const m = modal; modal = null;
  if (m) { m.teardown(); if (m.restore) m.restore(); }
}
function dialog({ title, body = "", fields = [], ok = "OK", danger = false, wide = false, validate, render, kind = "dialog", cancel = "Cancel", extra = [], hint = "", focus = "" }) {
  return new Promise(resolve => {
    const prev = document.activeElement;
    const back = document.createElement("div"); back.className = "backdrop";
    const box = document.createElement("div"); box.className = "dialog" + (wide ? " " + (wide === true ? "wide" : wide) : "");
    box.setAttribute("role", "dialog");
    box.innerHTML = `<div class="title">${esc(title)}</div>${body ? `<div class="body">${esc(body)}</div>` : ""}
      <div class="fields"></div><div class="error"></div>
      <div class="btns">${hint ? `<span class="hintl">${esc(hint)}</span>` : ""}<button class="btn" data-cancel>${esc(cancel)}</button>${extra.map((x, i) => `<button class="btn" data-extra="${i}">${esc(x.label)}</button>`).join("")}${ok ? `<button class="btn ${danger ? "danger" : "primary"}" data-ok>${esc(ok)}</button>` : ""}</div>`;
    const fbox = box.querySelector(".fields");
    const ctl = {};
    for (const f of fields) {
      const wrap = document.createElement("div"); wrap.className = "field";
      wrap.innerHTML = f.label ? `<label>${esc(f.label)}</label>` : "";
      if (f.type === "dropdown") ctl[f.key] = dropdown(wrap, f);
      else {
        const inp = document.createElement("input");
        inp.value = f.value || ""; inp.placeholder = f.placeholder || ""; inp.spellcheck = false;
        wrap.appendChild(inp);
        ctl[f.key] = { get: () => inp.value, focusEl: inp, isOpen: () => false };
        if (f.complete) ctl[f.key] = completer(wrap, inp, f.complete, f.recent);
      }
      fbox.appendChild(wrap);
    }
    if (render) render(fbox, ctl);
    document.body.append(back, box);
    const errEl = box.querySelector(".error");
    const focusables = () => [...box.querySelectorAll("input, button, [tabindex='0']")].filter(el => el.offsetParent !== null);
    const values = () => Object.fromEntries(Object.entries(ctl).map(([k, c]) => [k, c.get()]));
    let done = false;
    const finish = async okd => {
      if (done) return;
      if (okd && validate) {
        const err = await validate(values());
        if (err) { errEl.textContent = err; return; }
      }
      done = true;
      closeModal();
      resolve(okd ? values() : null);
    };
    box.addEventListener("click", e => {
      const xb = e.target.closest("[data-extra]");
      if (xb) extra[+xb.dataset.extra].run(values());
      else if (e.target.closest("[data-cancel]")) finish(false);
      else if (e.target.closest("[data-ok]")) finish(true);
    });
    back.addEventListener("mousedown", e => { e.preventDefault(); });
    const trap = e => { if (!box.contains(e.target) && !e.target.closest(".menu")) { e.stopPropagation(); (focusables()[0] || box).focus(); } };
    document.addEventListener("focusin", trap, true);
    openModal({
      kind,
      restore: () => { if (prev && prev.focus && document.contains(prev)) prev.focus({ preventScroll: true }); },
      teardown: () => { document.removeEventListener("focusin", trap, true); back.remove(); box.remove(); Object.values(ctl).forEach(c => c.closeList && c.closeList()); },
      close: () => finish(false),
      onKey(e, combo) {
        const open = Object.values(ctl).find(c => c.isOpen());
        if (open && open.onKey(e, combo)) return true;
        if (combo === "Escape") { finish(false); return true; }
        if (combo === "Enter" || combo === "M:Enter") {
          if (document.activeElement && document.activeElement.matches("[data-cancel]")) finish(false);
          else if (document.activeElement && document.activeElement.classList.contains("dd") && combo === "Enter") return false;
          else finish(true);
          return true;
        }
        const mv = { PageDown: "pgdn", "C:KeyV": "pgdn", PageUp: "pgup", "A:KeyV": "pgup", "AS:Comma": "home", "AS:Period": "end" }[combo];
        const ae = document.activeElement;
        if (mv && !(ae && ae.closest && ae.closest(".cm-editor")) && !(ae && ae.tagName === "TEXTAREA")) {
          if (ae && ae.tagName === "INPUT" && (mv === "home" || mv === "end") && ae.type !== "number") {
            const at = mv === "home" ? 0 : ae.value.length; ae.setSelectionRange(at, at); return true;
          }
          const sc = [box, ...box.querySelectorAll("*")].find(el => el.scrollHeight > el.clientHeight + 2 && /auto|scroll/.test(getComputedStyle(el).overflowY));
          if (sc) { scrollBox(sc, mv); return true; }
        }
        if (combo === "Tab" || combo === "S:Tab") {
          const fs = focusables(); if (!fs.length) return true;
          const i = fs.indexOf(document.activeElement);
          fs[(i + (combo === "Tab" ? 1 : -1) + fs.length) % fs.length].focus();
          return true;
        }
        return false;
      },
    });
    const first = ctl[focus] || Object.values(ctl)[0];
    // focus: "cancel" for a confirmation whose OK cannot be undone (Enter then cancels; Tab reaches OK)
    const firstEl = first ? first.focusEl : (focus === "cancel" && box.querySelector("[data-cancel]")) || box.querySelector("[data-ok]") || box.querySelector("[data-cancel]");
    firstEl.focus();
    if (firstEl.select) firstEl.select();
  });
}
const confirmDialog = o => dialog(o).then(v => v !== null);
/* How the last thing was asked for: a menu opened from the keyboard (⌘E, → into a submenu) shows its letter keys
   underlined; one opened with the mouse looks like a Mac's (the letters work in both). */
let LAST_INPUT = "mouse";
addEventListener("keydown", e => { if (!["Meta", "Control", "Alt", "Shift"].includes(e.key)) LAST_INPUT = "key"; }, true);
addEventListener("mousedown", () => { LAST_INPUT = "mouse"; }, true);
function listPopup({ items, anchor, at, side, sel = 0, onPick, onClose, accelerators = false, filterable = false, inside, maxWidth }) {
  if (typeof hideTip === "function") hideTip();
  const el = document.createElement("div"); el.className = "menu" + (LAST_INPUT === "key" ? " keys" : ""); el.setAttribute("role", "menu");
  if (maxWidth) el.style.maxWidth = maxWidth + "px";
  let q = "";
  const filtering = () => filterable && items.filter(it => !it.sep).length > 6;
  let view = items, i = 0;
  const pickable = k => view[k] && !view[k].sep && !view[k].disabled;
  const firstRow = () => view.findIndex((_, k) => pickable(k));
  const acc = () => {
    const used = new Set();
    return view.map(it => {
      if (!accelerators || filtering() || it.sep || it.disabled) return null;
      for (const ch of it.label.toLowerCase()) if (/[a-z]/.test(ch) && !used.has(ch)) { used.add(ch); return ch; }
      return null;
    });
  };
  let A = [];
  const labelHtml = (it, k) => {
    if (!A[k]) return esc(it.label);
    const j = it.label.toLowerCase().indexOf(A[k]);
    return esc(it.label.slice(0, j)) + "<u>" + esc(it.label[j]) + "</u>" + esc(it.label.slice(j + 1));
  };
  const draw = () => {
    view = filtering() && q ? items.filter(it => !it.sep && fuzzy(q, it.label) >= 0) : items;
    if (!pickable(i)) i = firstRow();
    A = acc();
    el.innerHTML = (filtering() ? `<div class="q">${q ? "Filter: " + esc(q) : "Type to filter"}</div>` : "") +
      view.map((it, k) => it.sep ? "<hr>" :
        `<div class="row${k === i ? " sel" : ""}${it.danger ? " danger" : ""}" data-i="${k}"${it.disabled ? ' style="opacity:.45"' : ""}><span class="lbl">${labelHtml(it, k)}</span>${it.key ? `<span class="k">${esc(it.key)}</span>` : ""}${it.sub ? `<span class="sub">${I("chev", 12)}</span>` : ""}</div>`).join("");
    const r = el.querySelector(".row.sel"); if (r) r.scrollIntoView({ block: "nearest" });
  };
  i = Math.max(0, Math.min(sel, items.length - 1));
  draw();
  document.body.appendChild(el);
  // Work in CSS px of the zoomed page: rects (and mouse coordinates) may come back zoomed, depending on the engine.
  const k = rectScale(), vw = innerWidth / S.zoom, vh = innerHeight / S.zoom;
  const r0 = anchor ? anchor.getBoundingClientRect() : { left: at.x, right: at.x, bottom: at.y, top: at.y, width: 0 };
  const r = { left: r0.left / k, right: r0.right / k, top: r0.top / k, bottom: r0.bottom / k, width: r0.width / k };
  el.style.minWidth = Math.max(220, side ? 0 : r.width) + "px";
  const h = el.offsetHeight, w = el.offsetWidth;
  let top, left;
  // a submenu: its first row level with the row it opens from (the menu's 5px padding)
  if (side) { top = Math.max(8, Math.min(r.top - 5, vh - h - 8)); left = r.right + 2; if (left + w > vw - 8) left = Math.max(8, r.left - w - 2); }
  else { top = r.bottom + 4; if (top + h > vh - 8) top = Math.max(8, r.top - h - 4); left = Math.max(8, Math.min(r.left, vw - w - 8)); }
  el.style.top = top + "px"; el.style.left = left + "px";
  el.addEventListener("mousedown", e => e.preventDefault());
  el.addEventListener("click", e => { const row = e.target.closest(".row"); if (row) pick(+row.dataset.i); });
  el.addEventListener("mousemove", e => { const row = e.target.closest(".row"); if (row && +row.dataset.i !== i && pickable(+row.dataset.i)) { i = +row.dataset.i; draw(); } });
  const move = d => {
    const rows = view.map((_, k) => k).filter(pickable);
    if (!rows.length) return;
    const p = rows.indexOf(i);
    if (d === "home") i = rows[0]; else if (d === "end") i = rows[rows.length - 1];
    else i = rows[Math.max(0, Math.min(rows.length - 1, (p < 0 ? 0 : p) + d))];
    draw();
  };
  let closed = false;
  const outside = e => { if (!el.contains(e.target) && !(anchor && anchor.contains(e.target)) && !(inside && inside(e.target))) close(); };
  document.addEventListener("mousedown", outside, true);
  const close = () => { if (closed) return; closed = true; document.removeEventListener("mousedown", outside, true); el.remove(); onClose && onClose(); };
  const rowEl = k => el.querySelector(`.row[data-i="${k}"]`);
  const pick = k => { if (!pickable(k)) return; const it = view[k]; if (onPick(it, k, rowEl(k)) !== true) close(); };
  return {
    el, close,
    onKey(e, combo) {
      const page = 8;
      if (combo === "ArrowDown" || combo === "C:KeyN") move(1);
      else if (combo === "ArrowUp" || combo === "C:KeyP") move(-1);
      else if (combo === "PageDown" || combo === "C:KeyV") move(page);
      else if (combo === "PageUp" || combo === "A:KeyV") move(-page);
      else if (combo === "Home" || combo === "AS:Comma") move("home");
      else if (combo === "End" || combo === "AS:Period") move("end");
      else if (combo === "C:KeyL") recenter(el, el.querySelector(".row.sel"));
      else if (combo === "Enter") pick(i);
      else if (combo === "ArrowRight" && view[i] && view[i].sub) pick(i);
      else if (combo === "Escape") close();
      else if (filtering() && combo === "Backspace") { q = q.slice(0, -1); draw(); }
      else if (filtering() && !e.metaKey && !e.ctrlKey && !e.altKey && e.key.length === 1) { q += e.key; draw(); }
      else if (accelerators && /^Key[A-Z]$/.test(combo)) {
        const j = A.indexOf(combo.slice(3).toLowerCase());
        if (j >= 0) { i = j; pick(j); }
      } else return false;
      return true;
    },
  };
}
function menu(items, where, kind = "menu") {
  const prev = document.activeElement;
  const stack = [];
  const inside = t => stack.some(p => p.el.contains(t));
  const open = (its, wh, level) => {
    const lp = listPopup({ items: its, ...wh, accelerators: true, filterable: level > 0 || kind === "bell", inside,
      onClose: () => { const at = stack.indexOf(lp); if (at >= 0) stack.splice(at); if (!stack.length && modal === m) closeModal(); },
      onPick: (it, k, row) => {
        if (it.sub) {
          while (stack.length > level + 1) stack[stack.length - 1].close();
          open(typeof it.sub === "function" ? it.sub() : it.sub, { anchor: row, side: true }, level + 1);
          return true;
        }
        const run = it.run;
        closeModal();
        if (run) run();  // at once: the key that picked it is consumed, and the next key must see the result
      } });
    stack.push(lp);
  };
  const m = {
    kind,
    restore: () => { if (prev && prev.focus && document.contains(prev)) prev.focus({ preventScroll: true }); },
    teardown: () => { [...stack].reverse().forEach(p => p.close()); stack.length = 0; },
    close: () => closeModal(),
    onKey(e, combo) {
      if ((combo === "ArrowLeft" || combo === "Escape") && stack.length > 1) { stack[stack.length - 1].close(); return true; }
      if (combo === "ArrowLeft") return true;
      stack[stack.length - 1].onKey(e, combo);
      return true;  // a menu owns every key
    },
  };
  openModal(m);
  open(items, where, 0);
}
function dropdown(wrap, f) {
  let value = f.value ?? f.options[0].value;
  const btn = document.createElement("button");
  btn.className = "dd"; btn.type = "button";
  const label = () => (f.options.find(o => o.value === value) || { label: value }).label;
  const draw = () => { btn.innerHTML = `<span>${esc(label())}</span>${I("caret", 12)}`; };
  draw();
  wrap.appendChild(btn);
  let lp = null;
  const open = () => {
    if (lp) return;
    lp = listPopup({ items: f.options.map(o => ({ label: o.label, value: o.value })), anchor: btn,
      sel: f.options.findIndex(o => o.value === value),
      onPick: it => { value = it.value; draw(); btn.focus(); if (f.onChange) f.onChange(value); },
      onClose: () => { lp = null; } });
  };
  btn.addEventListener("click", () => lp ? lp.close() : open());
  btn.addEventListener("keydown", e => {
    if (lp) return;
    if (["ArrowDown", "ArrowUp", " ", "Enter"].includes(e.key) && !e.isComposing) { e.preventDefault(); e.stopPropagation(); open(); }
  });
  const setOptions = (options, v) => { if (lp) lp.close(); f.options = options; value = options.some(o => o.value === v) ? v : options[0].value; draw(); };
  return { get: () => value, setOptions, focusEl: btn, isOpen: () => !!lp, onKey: (e, c) => lp ? lp.onKey(e, c) : false, closeList: () => lp && lp.close() };
}
function completer(wrap, inp, fetcher, recent) {
  let lp = null, seq = 0;
  const show = async all => {
    const my = ++seq;
    const items = all ? recent() : await fetcher(inp.value).catch(() => []);
    if (my !== seq || document.activeElement !== inp) return;
    if (lp) lp.close();
    if (!items.length || (!all && items.length === 1 && items[0] === inp.value)) return;
    lp = listPopup({ items: items.map(v => ({ label: v, value: v })), anchor: inp, sel: -1,
      onPick: it => { inp.value = it.value; inp.focus(); show(); },
      onClose: () => { lp = null; } });
  };
  inp.addEventListener("input", () => show());
  inp.addEventListener("blur", () => setTimeout(() => { if (lp && document.activeElement !== inp) lp.close(); }, 0));
  if (recent) {  // a combo box: ▾ (or ↓ in the field) lists the recent values
    wrap.classList.add("combo");
    const b = document.createElement("button"); b.type = "button"; b.className = "ddbtn"; b.tabIndex = -1; b.title = "Recent folders (↓)";
    b.innerHTML = I("caret", 12);
    b.addEventListener("mousedown", e => { e.preventDefault(); inp.focus(); if (lp) lp.close(); else show(true); });
    wrap.appendChild(b);
    inp.addEventListener("keydown", e => { if (e.key === "ArrowDown" && !lp && !e.isComposing) { e.preventDefault(); show(true); } });
  }
  return {
    get: () => inp.value, focusEl: inp, isOpen: () => !!lp, closeList: () => lp && lp.close(),
    onKey(e, combo) {
      if (combo === "Tab" && lp) { lp.onKey(e, "Enter"); return true; }
      if (["ArrowDown", "ArrowUp", "Enter", "Escape", "PageDown", "PageUp"].includes(combo)) return lp.onKey(e, combo);
      return false;
    },
  };
}
