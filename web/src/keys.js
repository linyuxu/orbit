"use strict";
/* ===================================================================== the key handler (interaction.md §2) */
/* Synthetic or forwarded events may lack e.code: derive it from e.key. */
const PUNCT = { "/": "Slash", "?": "Slash", "[": "BracketLeft", "{": "BracketLeft", "]": "BracketRight", "}": "BracketRight",
  "=": "Equal", "+": "Equal", "-": "Minus", "_": "Minus", ",": "Comma", "<": "Comma", ".": "Period", ">": "Period",
  "`": "Backquote", "~": "Backquote", ";": "Semicolon", "'": "Quote", "\\": "Backslash" };
function codeFromKey(key) {
  if (/^[a-z]$/i.test(key)) return "Key" + key.toUpperCase();
  if (/^[0-9]$/.test(key)) return "Digit" + key;
  return PUNCT[key] || key;
}
function comboOf(e) {
  const m = (e.metaKey ? "M" : "") + (e.ctrlKey ? "C" : "") + (e.altKey ? "A" : "") + (e.shiftKey ? "S" : "");
  // Letters, digits, brackets and punctuation by e.code (WebKit turns ⌥ letters into symbols); named keys by e.key.
  const named = ["Enter", "Escape", "Tab", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "PageUp", "PageDown", "Home", "End", "Backspace", " "];
  const k = named.includes(e.key) ? (e.key === " " ? "Space" : e.key) : (e.code || codeFromKey(e.key));
  return m ? m + ":" + k : k;
}
const MODAL_PASS = new Set(["M:KeyC", "M:KeyV", "M:KeyX", "M:KeyA", "M:KeyZ", "MS:KeyZ"]);
function handleEsc() {
  if ($("#lightbox").classList.contains("show")) { closeLightbox(); return; }       // 1
  if (modal) { modal.close(); return; }                                              // 2
  if (FIND.open) { closeFind(); return; }                                            // 3
  if (panelFocused()) { S.filter = ""; renderLeft(); focusMain(); return; }          // 4 (the panel stays)
  if (pviewEsc()) return;                                                            // 5 (a plugin's thread: its editor → its messages)
  if (document.activeElement === input) { conv.focus({ preventScroll: true }); return; } // 5
  focusMain();                                                                       // 6
}
/* In a side tab, the keys it owns stay; other ⌘ shortcuts go to the main page. */
const EMBED_OWN = new Set(["MS:KeyD", "M:Enter", "MS:Enter", "M:KeyS", "M:KeyL", "M:KeyE", "M:KeyC", "M:KeyV", "M:KeyX", "M:KeyA", "M:KeyZ", "MS:KeyZ", "M:KeyF", "M:KeyG", "MS:KeyG"]);
const isCmd = c => /^M[CAS]*:/.test(c);  // a ⌘ combination ("M:…", "MS:…"), not a key named M… (Minus)
let ctrlX = 0;  // ⌃X prefix (Emacs): the next key within 1.5 s completes it
function onKey(e) {
  if (e.isComposing || e.keyCode === 229) return;  // IME: ⏎ / Esc only confirm a candidate
  if (["Meta", "Control", "Alt", "Shift"].includes(e.key)) return;
  const combo = comboOf(e);
  const consume = () => { e.preventDefault(); e.stopPropagation(); };
  if (document.activeElement === findq && !modal) {
    if (combo === "Enter" || combo === "S:Enter") { consume(); findStep(combo === "Enter" ? 1 : -1); return; }
    // ⌃S / ⌃R (Emacs isearch): next / previous match; on an empty query, the last search again.
    if ((combo === "C:KeyS" || combo === "C:KeyR") && FIND.target && FIND.target.kind === "editor") {
      consume();
      if (!findq.value) { findq.value = LS.get("findq", ""); findStep(0); } else findStep(combo === "C:KeyS" ? 1 : -1);
      return;
    }
    if (combo === "Escape") { consume(); closeFind(); return; }
    if (combo === "M:Enter") { consume(); return; }
  }
  // Emacs line keys in a text field (⌃A ⌃E ⌃F ⌃B ⌥F ⌥B ⌃D ⌥D ⌃H ⌥⌫ ⌃W ⌃K ⌃U ⌃Y), as in the editors.
  if (LINE_KEYS.has(combo) && isLineInput(document.activeElement) && lineEditInput(document.activeElement, combo)) { consume(); return; }
  // A review comment box owns ⌘⏎ (add) and Esc (cancel).
  if (EMBED_REVIEW && !modal && e.target.closest && e.target.closest(".cbox") && (combo === "M:Enter" || combo === "Escape")) return;
  if ($("#lightbox").classList.contains("show")) { consume(); if (combo === "Escape") closeLightbox(); return; }
  if (MENTION.open && document.activeElement === input && !modal && mentionKey(combo)) { consume(); return; }
  if (combo === "ArrowUp" && document.activeElement === input && !input.value && !modal) { consume(); inputHistory(); return; }
  if (combo === "Escape" && document.activeElement === input && S.editing && !input.value && !modal) { consume(); stopEdit(); return; }
  if (modal) {
    // The key that opened it closes it; any other shortcut closes a menu and runs.
    if (modal.opener && combo === modal.opener && modal.kind !== "finder") { consume(); closeModal(); return; }
    if ((modal.kind === "menu" || modal.kind === "bell") && isCmd(combo) && !MODAL_PASS.has(combo) && combo !== "M:Enter") {
      consume(); closeModal(); const fn = BINDINGS.get(combo); if (fn) fn(); return;
    }
    if (modal.onKey(e, combo)) { consume(); return; }
    if (combo === "Escape") { consume(); modal.close(); return; }
    if (isCmd(combo) && !MODAL_PASS.has(combo)) {
      consume();
      // Any shortcut closes an open menu (and runs); a dialog suspends them.
      if (modal.kind === "menu" || modal.kind === "bell") { closeModal(); const fn = BINDINGS.get(combo); if (fn) fn(); }
      else if (modal.kind === "sheet" && combo === "M:Slash") closeModal();
    }
    return;
  }
  if (shellFocused()) {  // the terminal keeps its keys (⌃X, ⌃L, ⌥…); ⌘ shortcuts, ⌃⌥HJKL and the plugins' keys stay the app's
    if (combo === "Escape") { consume(); shellEsc(); return; }
    if (!isCmd(combo) && !/^CA:Key[HJKL]$/.test(combo) && !PLUGIN_KEYS.has(combo)) return;
    if (["M:KeyC", "M:KeyV", "M:KeyA"].includes(combo) && !BINDINGS.get(combo)) return;
  }
  if (ctrlX && Date.now() - ctrlX < 1500) {
    ctrlX = 0;
    if (combo === "Digit1") { consume(); if (EMBED) window.parent.postMessage({ orbit: "key", combo: "CX:1" }, location.origin); else onlyMain(); return; }
    if (combo === "C:KeyS") { consume(); saveCurrent(); return; }
    if (combo === "KeyH" && S.file) { consume(); const ed = EDITORS.get(S.file); if (ed && ed.ed) ed.ed.selectAll(); return; }
  }
  ctrlX = 0;
  if (combo === "C:KeyX") { consume(); ctrlX = Date.now(); return; }
  // ⌃S / ⌃R in a code or Markdown editor open the find bar (then they step through the matches from there).
  if ((combo === "C:KeyS" || combo === "C:KeyR") && S.file && !modal && document.activeElement && document.activeElement.closest
      && document.activeElement.closest("#fileview .cm-editor")) { consume(); openFind(); return; }
  if (combo === "Escape" && xOn() && XN.editing && inCalNote()) { consume(); leaveCalNote(); return; }  // the meeting note
  if (combo === "Escape" && xKind() === "calendar" && inXView() && !XN.editing && !EMBED && S.side) { consume(); focusSide(); return; }  // the list → the session beside
  if (combo === "Escape") { consume(); handleEsc(); return; }
  if (EMBED && ((isCmd(combo) && !EMBED_OWN.has(combo)) || /^CA:Key[HJKL]$/.test(combo) || /^AS:Bracket(Left|Right)$/.test(combo) || PLUGIN_KEYS.has(combo))) {
    consume(); window.parent.postMessage({ orbit: "key", combo }, location.origin); return;
  }
  if (panelFocused() && panelKey(e, combo)) { consume(); return; }
  const fn = BINDINGS.get(combo);
  if (fn) { consume(); const before = modal; fn(); if (modal && modal !== before && !modal.opener) modal.opener = combo; return; }
  if (xOn() && inXView() && !inCalNote() && xKey(e, combo)) { consume(); return; }
  if (EMBED_REVIEW && !(e.target.closest && e.target.closest("textarea"))) {
    const mv = { ArrowUp: "up", ArrowDown: "down", PageUp: "pgup", PageDown: "pgdn", "C:KeyV": "pgdn", "A:KeyV": "pgup", Home: "home", End: "end", "AS:Comma": "home", "AS:Period": "end" }[combo];
    if (mv) { consume(); scrollBox($("#rvbody"), mv); return; }
  }
  if (!modal && !panelFocused()) {  // a plugin's view (pviews.js) whose scroll box has the keyboard
    const mv = { ArrowUp: "up", ArrowDown: "down", PageUp: "pgup", PageDown: "pgdn", "C:KeyV": "pgdn", "A:KeyV": "pgup",
                 Home: "home", End: "end", "AS:Comma": "home", "AS:Period": "end", Space: "pgdn", "S:Space": "pgup" }[combo];
    if (mv && pviewScrollKey(mv)) { consume(); return; }
  }
  if (document.activeElement === conv || (document.activeElement === document.body && !S.file && !EMBED_REVIEW)) {
    const mv = { ArrowUp: "up", ArrowDown: "down", PageUp: "pgup", PageDown: "pgdn", "C:KeyV": "pgdn", "A:KeyV": "pgup",
                 Home: "home", End: "end", "AS:Comma": "home", "AS:Period": "end", Space: "pgdn", "S:Space": "pgup" }[combo];
    if (mv) { consume(); scrollConv(mv); return; }
  }
  if (document.activeElement === input) {
    // ⌥⇧, ⌥⇧. (Emacs M-< M->): the input's own start / end, where the keyboard is. ⌥V still pages the conversation.
    if (combo === "AS:Comma" || combo === "AS:Period") { consume(); const p = combo === "AS:Comma" ? 0 : input.value.length; input.setSelectionRange(p, p); return; }
    if (combo === "A:KeyV") { consume(); scrollConv("pgup"); return; }
  }
}
window.addEventListener("keydown", onKey, true);
window.addEventListener("keydown", e => { if (e.key === "Escape") e.preventDefault(); });  // Esc never leaves macOS full screen
// Keys the app shell owns natively arrive here, routed like typed keys.
window.deskEsc = () => handleEsc();
window.deskKey = combo => {
  const fake = { metaKey: /^[^:]*M/.test(combo), ctrlKey: false, altKey: false, key: "", preventDefault() {}, stopPropagation() {} };
  if (modal) { if (combo === "Escape") modal.close(); return; }
  if (window.deskModal()) return;  // a dialog in the side tab
  if (combo === "CX:1") return onlyMain();
  if (panelFocused() && panelKey(fake, combo)) return;
  const fn = BINDINGS.get(combo); if (fn) fn();
};
