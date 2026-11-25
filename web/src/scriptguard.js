"use strict";
/* A page script that fails to load says so on the page (the Mac's and the phone's), not only in the console and the
   server's log (core.js logPage). It fails when it cannot be fetched, or at its top level: the page's scripts share
   one global scope, so one duplicate top-level const in another script drops a whole file, silently, and whatever
   it defines (a key, a panel) with it. One notice per script; loaded first, before every other script.
   Counted: a script element's load error; a SyntaxError from a page script at any time (one loaded later too);
   another error from a page script while the page loads (its top level). Not counted: errors once the page runs. */
(() => {
  const told = new Set();
  let started = false;  // (the page loaded, plus a moment: its scripts' top levels have run)
  addEventListener("load", () => setTimeout(() => { started = true; }, 1500));
  const pageScript = src => {
    try {
      const u = new URL(src, location.href);
      return u.origin === location.origin && /\.js$/.test(u.pathname) ? u.pathname : "";
    } catch { return ""; }
  };
  const say = (src, why) => {
    if (told.has(src)) return;
    told.add(src);
    const show = () => {
      let box = document.getElementById("scripterr");
      if (!box) {
        box = document.createElement("div");
        box.id = "scripterr";
        box.setAttribute("role", "alert");
        box.style.cssText = "position:fixed;left:12px;bottom:12px;z-index:2147483000;max-width:min(560px,calc(100vw - 24px));" +
          "display:flex;flex-direction:column;gap:6px;font:13px/1.4 -apple-system,system-ui,sans-serif";
        document.body.appendChild(box);
      }
      const line = document.createElement("div");
      line.className = "scripterr-line";
      line.style.cssText = "display:flex;gap:10px;align-items:flex-start;padding:9px 12px;border-radius:8px;background:#b42318;" +
        "color:#fff;box-shadow:0 6px 20px rgba(0,0,0,.25)";
      const text = document.createElement("span");
      text.style.flex = "1";
      text.textContent = `${src.split("/").pop()} did not load (${why}): part of Orbit may not work. Reload; the console has more.`;
      const x = document.createElement("button");
      x.textContent = "×";
      x.title = "Dismiss";
      x.style.cssText = "all:unset;cursor:pointer;font-size:16px;line-height:1;opacity:.8";
      x.onclick = () => line.remove();
      line.append(text, x);
      box.appendChild(line);
    };
    document.body ? show() : addEventListener("DOMContentLoaded", show);
  };
  // WebKit names no file for some top-level errors (a duplicate variable): the script's load event comes right after
  // the error and names it; failing that, the latest page script that never loaded. (An element's load event never
  // reaches the window: listened for on the document.)
  const loaded = new Set();
  let orphan = null;
  document.addEventListener("load", e => {
    const t = e.target;
    if (!t || t.tagName !== "SCRIPT" || !t.src) return;
    loaded.add(t.src);
    if (orphan && pageScript(t.src)) { clearTimeout(orphan.tm); say(pageScript(t.src), orphan.msg); orphan = null; }
  }, true);
  const unloaded = () => {
    const s = [...document.scripts].reverse().find(x => x.src && pageScript(x.src) && !loaded.has(x.src) && !told.has(pageScript(x.src)));
    return s ? pageScript(s.src) : "a page script";
  };
  addEventListener("error", e => {
    const t = e.target;
    if (t && t.tagName === "SCRIPT") {  // (fetching it failed: the capture phase sees an element's error)
      const src = pageScript(t.src);
      if (src) say(src, "it could not be fetched");
      return;
    }
    const msg = String(e.message || (e.error && e.error.message) || "an error").replace(/^Uncaught\s+/, "");
    const counts = e.error instanceof SyntaxError || /^SyntaxError/.test(msg) || !started;
    if (!e.filename) {  // (see unloaded above: named a moment later, when the scripts fetched meanwhile have loaded)
      if (counts && !orphan) orphan = { msg, tm: setTimeout(() => { orphan = null; say(unloaded(), msg); }, 100) };
      return;
    }
    const src = pageScript(e.filename);
    if (!src || src.endsWith("/scriptguard.js")) return;
    if (counts) say(src, msg);
  }, true);
})();
