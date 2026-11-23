"use strict";
/* ===================================================================== ± Review (a side tab kind) */
async function pollReview() {
  if (EMBED) return;
  const root = cur() ? cur().cwd : null;
  S.review.root = root;
  if (!root || document.hidden) { renderReviewBadge(); return; }
  try {
    const r = await api("GET", "/api/review?summary=1&root=" + encodeURIComponent(root));
    if (S.review.root === root) { S.review.count = r.git ? r.count : 0; S.review.add = r.add || 0; S.review.del = r.del || 0; S.review.git = r.git; renderReviewBadge(); }
  } catch {}
}
setInterval(pollReview, 5000);
function renderReviewBadge() {
  if (EMBED) return;
  const b = $("#revbadge");
  const n = S.review.count || 0;
  b.hidden = !S.review.git || (!n && !(S.side && S.side.kind === "review"));
  const add = S.review.add || 0, del = S.review.del || 0;
  b.innerHTML = add || del ? `<span>+${add}</span><span>−${del}</span>` : `<span>± ${n}</span>`;
  b.title = `± Review: ${n} uncommitted file${n === 1 ? "" : "s"}, +${add} −${del} lines, in ${tilde(S.review.root || "")} (⌘⇧I)`;
  b.classList.toggle("on", !!S.side && S.side.kind === "review");
}
$("#revbadge").addEventListener("click", toggleReview);
const RV = { data: null, comments: [], open: null, lines: {} };  // open: key of the line whose comment box is open
const cKey = (path, l) => `${encodeURIComponent(path)}:${l.old ?? ""}:${l.new ?? ""}`;  // printable: it goes into attributes
async function loadReview() {
  const { root } = EMBED;
  RV.comments = LS.get("comments:" + root, []);
  try { RV.data = await api("GET", "/api/review?root=" + encodeURIComponent(root)); }
  catch (e) { RV.data = { error: e.message, files: [] }; }
  renderReview();
  try { window.parent.postMessage({ orbit: "review-count", count: RV.data.count || 0 }, location.origin); } catch {}
}
function saveComments() { LS.set("comments:" + EMBED.root, RV.comments); }
function renderReview() {
  const d = RV.data;
  const s = S.sessions.get(EMBED.sid);
  const files = d && d.files || [];
  const add = files.reduce((n, f) => n + f.add, 0), del = files.reduce((n, f) => n + f.del, 0);
  const nc = RV.comments.length;
  $("#rvhead").innerHTML = `<span class="t">± Review</span><span class="s">${esc(tilde(d && d.root || EMBED.root))}${d && d.branch ? " · " + esc(d.branch) : ""}${d && d.git ? ` · ${files.length} file${files.length === 1 ? "" : "s"} · <span style="color:var(--add)">+${add}</span> <span style="color:var(--del)">−${del}</span>` : ""}</span>
    <div class="acts"><button class="qbtn" data-rv="refresh" title="Refresh">${I("refresh", 14)}Refresh</button>
    <button class="btn sm ${nc ? "primary" : ""}" data-rv="send" ${nc && s ? "" : "disabled"} title="${s ? `Send the comments to ${esc(s.name)}` : "The session is closed"}">Send ${nc || ""} comment${nc === 1 ? "" : "s"}</button></div>`;
  const body = $("#rvbody");
  if (!d) { body.innerHTML = `<div class="rvnote">Loading…</div>`; return; }
  if (d.error) { body.innerHTML = `<div class="empty"><h2>Cannot read the changes</h2>${esc(d.error)}</div>`; return; }
  if (!d.git) { body.innerHTML = `<div class="empty"><h2>Not a git repository</h2>± Review shows the uncommitted changes of a git folder.</div>`; return; }
  if (!files.length) { body.innerHTML = `<div class="empty"><h2>No uncommitted changes</h2>The working tree matches ${esc(d.branch || "HEAD")}.</div>`; return; }
  const byLine = new Map(RV.comments.map(c => [c.key, c]));
  RV.lines = {};
  body.innerHTML = files.map((f, fi) => `
    <div class="rvfile">
      <div class="rvfh"><span class="stl st-${f.status === "?" ? "U" : esc(f.status)}">${esc(f.status === "?" ? "U" : f.status)}</span><span class="p" data-rvopen="${fi}" title="Open the file">${esc(f.path)}</span>
        <span class="n"><span class="a">+${f.add}</span> <span class="d">−${f.del}</span></span>
        <span class="acts"><button class="qbtn" data-rvopen="${fi}">Open</button></span></div>
      ${f.binary ? `<div class="rvnote">Binary file${f.status === "?" ? " (untracked)" : ""}</div>` : f.hunks.map((h, hi) => `
        <div class="hunk">
          <div class="hh"><span class="h">${esc(h.header)}</span>${f.status === "?" ? "" : `<button class="btn sm" data-revert="${fi}:${hi}" title="Undo this hunk in the working tree">Revert hunk</button>`}</div>
          <div class="hlines">${h.lines.map((l, li) => {
            if (l.t === "\\") return `<div class="dl"><span class="no"></span><span class="no"></span><span class="sg"></span><span class="tx" style="color:var(--ink-3)">${esc(l.text)}</span></div>`;
            const key = cKey(f.path, l), c = byLine.get(key);
            RV.lines[key] = { path: f.path, l };
            const cls = l.t === "+" ? " add" : l.t === "-" ? " del" : "";
            let html = `<div class="dl${cls}${c ? " cmt" : ""}" data-key="${esc(key)}"><span class="no" title="Comment on this line">${l.old ?? ""}</span><span class="no" title="Comment on this line">${l.new ?? ""}</span><span class="sg">${l.t === " " ? "" : l.t}</span><span class="tx">${esc(l.text)}</span></div>`;
            if (c && RV.open !== key) html += `<div class="cmtv"><span class="tx">${esc(c.text)}</span><button class="qbtn" data-cedit="${esc(key)}">Edit</button><button class="qbtn" data-cdel="${esc(key)}">Delete</button></div>`;
            if (RV.open === key) html += `<div class="cbox" data-cbox="${esc(key)}"><textarea placeholder="Comment for the agent on ${esc(f.path)}:${l.new ?? l.old}">${esc(c ? c.text : "")}</textarea><div class="row"><span class="h">⌘⏎ add · Esc cancel</span><button class="btn sm" data-ccancel>Cancel</button><button class="btn sm primary" data-cadd>${c ? "Update" : "Add comment"}</button></div></div>`;
            return html;
          }).join("")}</div>
        </div>`).join("")}
    </div>`).join("");
  const box = body.querySelector(".cbox textarea");
  if (box) { box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
  refreshFind();
}
function commitComment(key) {
  const ta = [...document.querySelectorAll(".cbox")].find(b => b.dataset.cbox === key)?.querySelector("textarea");
  const text = ta ? ta.value.trim() : "";
  const ln = RV.lines[key];
  RV.comments = RV.comments.filter(c => c.key !== key);
  if (text && ln) RV.comments.push({ key, path: ln.path, text, line: ln.l.new ?? ln.l.old, side: ln.l.t, code: ln.l.text });
  RV.open = null; saveComments(); renderReview();
  $("#rvbody").focus({ preventScroll: true });
}
async function sendComments() {
  const s = S.sessions.get(EMBED.sid);
  if (!s || !RV.comments.length) return;
  const lines = [...RV.comments].sort((a, b) => a.path.localeCompare(b.path) || a.line - b.line).map(c =>
    `- \`${c.path}:${c.line}\` (${c.side === "+" ? "added" : c.side === "-" ? "removed" : "context"} line: \`${c.code.trim().slice(0, 160)}\`)\n  ${c.text.replace(/\n/g, "\n  ")}`);
  const text = `Review comments on the uncommitted changes in ${agentPath(RV.data.root)}:\n\n${lines.join("\n")}\n\nPlease address each comment.`;
  try {
    await sendText(s.id, text);
    toast(`Sent ${RV.comments.length} comment${RV.comments.length === 1 ? "" : "s"} to ${s.name}`);
    RV.comments = []; saveComments(); renderReview();
  } catch (e) { toast("Send failed: " + e.message); }
}
$("#reviewview").addEventListener("click", async e => {
  const t = e.target;
  const act = t.closest("[data-rv]");
  if (act) { if (act.dataset.rv === "refresh") loadReview(); else sendComments(); return; }
  const op = t.closest("[data-rvopen]");
  if (op) { const f = RV.data.files[+op.dataset.rvopen]; window.parent.postMessage({ orbit: "open-file", path: RV.data.root + "/" + f.path, pinned: true }, location.origin); return; }
  const rv = t.closest("[data-revert]");
  if (rv) {
    const [fi, hi] = rv.dataset.revert.split(":").map(Number);
    const f = RV.data.files[fi], h = f.hunks[hi];
    const ok = await confirmDialog({ title: `Revert this hunk of ${f.path}?`, body: `${h.header}\nThe working tree goes back to the committed lines for this hunk.`, ok: "Revert hunk", danger: true });
    if (!ok) return;
    try { await api("POST", "/api/review/revert", { root: RV.data.root, path: f.path, header: h.header }); toast("Hunk reverted"); loadReview(); }
    catch (err) { toast("Revert failed: " + err.message); }
    return;
  }
  if (t.closest("[data-cadd]")) { commitComment(t.closest(".cbox").dataset.cbox); return; }
  if (t.closest("[data-ccancel]")) { RV.open = null; renderReview(); return; }
  const ce = t.closest("[data-cedit]");
  if (ce) { RV.open = ce.dataset.cedit; renderReview(); return; }
  const cd = t.closest("[data-cdel]");
  if (cd) { RV.comments = RV.comments.filter(c => c.key !== cd.dataset.cdel); saveComments(); renderReview(); return; }
  const no = t.closest(".dl .no");
  if (no && no.parentElement.dataset.key) {
    const key = no.parentElement.dataset.key;
    RV.open = RV.open === key ? null : key;
    renderReview();
  }
});
$("#reviewview").addEventListener("keydown", e => {
  const ta = e.target.closest && e.target.closest(".cbox textarea");
  if (!ta || e.isComposing || e.keyCode === 229) return;
  if (e.key === "Enter" && e.metaKey) { e.preventDefault(); e.stopPropagation(); commitComment(ta.closest(".cbox").dataset.cbox); }
  else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); RV.open = null; renderReview(); $("#rvbody").focus(); }
}, true);
