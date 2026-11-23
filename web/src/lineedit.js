"use strict";
/* ===================================================================== Emacs line editing (one-line fields) */
/* The editors' cursor keys, in every one-line field: the left panel's filter (drawn, not an <input>) and every text
   <input> (⌘O, dialogs, Settings, the find bar). What ⌃K / ⌃U / ⌃W / ⌥D / ⌥⌫ kill, ⌃Y yanks back. ↑↓ and ⌃N ⌃P stay
   the lists' (they move in the list under the field). */
const LINE_KILL = { text: "" };
const LINE_KEYS = new Set(["C:KeyA", "C:KeyE", "C:KeyF", "C:KeyB", "A:KeyF", "A:KeyB", "A:ArrowLeft", "A:ArrowRight",
  "C:KeyD", "A:KeyD", "C:KeyH", "A:Backspace", "C:KeyW", "C:KeyK", "C:KeyU", "C:KeyY"]);
const wordRight = (v, p) => { while (p < v.length && !/\w/.test(v[p])) p++; while (p < v.length && /\w/.test(v[p])) p++; return p; };
const wordLeft = (v, p) => { while (p > 0 && !/\w/.test(v[p - 1])) p--; while (p > 0 && /\w/.test(v[p - 1])) p--; return p; };
/* One key on (value, cursor [, selection end]): the new {value, pos}, or null if the key is not a line key. */
function lineEdit(value, pos, combo, selEnd = pos) {
  const v = value, p = Math.max(0, Math.min(pos, v.length));
  const cut = (a, b, kill) => { if (kill) LINE_KILL.text = v.slice(a, b); return { value: v.slice(0, a) + v.slice(b), pos: a }; };
  if (selEnd !== p && ["C:KeyD", "C:KeyH", "A:Backspace", "C:KeyW", "A:KeyD", "Backspace"].includes(combo)) {
    return cut(Math.min(p, selEnd), Math.max(p, selEnd), combo === "C:KeyW");  // a selection: these delete it (⌃W kills it)
  }
  switch (combo) {
    case "C:KeyA": return { value: v, pos: 0 };
    case "C:KeyE": return { value: v, pos: v.length };
    case "C:KeyF": case "ArrowRight": return { value: v, pos: Math.min(v.length, p + 1) };
    case "C:KeyB": case "ArrowLeft": return { value: v, pos: Math.max(0, p - 1) };
    case "A:KeyF": case "A:ArrowRight": return { value: v, pos: wordRight(v, p) };
    case "A:KeyB": case "A:ArrowLeft": return { value: v, pos: wordLeft(v, p) };
    case "C:KeyD": return cut(p, Math.min(v.length, p + 1), false);
    case "Backspace": case "C:KeyH": return cut(Math.max(0, p - 1), p, false);
    case "A:KeyD": return cut(p, wordRight(v, p), true);
    case "A:Backspace": case "C:KeyW": return cut(wordLeft(v, p), p, true);
    case "C:KeyK": return cut(p, v.length, true);
    case "C:KeyU": return cut(0, p, true);
    case "C:KeyY": return { value: v.slice(0, p) + LINE_KILL.text + v.slice(p), pos: p + LINE_KILL.text.length };
  }
  return null;
}
/* A text <input> (not a number, a range, or an editor's own field). */
function isLineInput(el) {
  return !!el && el.tagName === "INPUT" && ["text", "search", "url", "email", ""].includes(el.type || "") && !el.readOnly &&
    !(el.closest && el.closest(".cm-editor"));
}
/* A line key in a text <input>: edit it, tell its listeners (input event); true when the key was one. */
function lineEditInput(el, combo) {
  const r = lineEdit(el.value, el.selectionStart ?? el.value.length, combo, el.selectionEnd ?? el.selectionStart);
  if (!r) return false;
  const changed = r.value !== el.value;
  if (changed) el.value = r.value;
  el.setSelectionRange(r.pos, r.pos);
  if (changed) el.dispatchEvent(new Event("input", { bubbles: true }));
  return true;
}
