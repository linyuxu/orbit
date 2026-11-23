"use strict";
/* ===================================================================== a card: a request, its steps, its answer
   The desktop's drawing (web/src/cards.js), for a narrow column and fingers: no hover (what the desktop shows on hover
   is either always there or gone), every control a 44px target, the steps folded to one line until tapped. */
const STATE_WORD = { queued: "Queued", thinking: "Thinking", running: "Running", paused: "Paused", completed: "Done", error: "Error", cancelled: "Cancelled" };
const STEP_KINDS = [
  [/web|fetch|browse|url/i, n => `${n} web lookup${n === 1 ? "" : "s"}`],
  [/bash|shell|command|exec|terminal/i, n => `ran ${n} command${n === 1 ? "" : "s"}`],
  [/edit|write|patch|notebook|create_file|replace/i, n => `edited ${n} file${n === 1 ? "" : "s"}`],
  [/read|view|open|cat/i, n => `read ${n} file${n === 1 ? "" : "s"}`],
  [/grep|glob|search|find|list|ls/i, n => `searched ${n} time${n === 1 ? "" : "s"}`],
  [/task|agent/i, n => `${n} agent${n === 1 ? "" : "s"}`],
];
function stepsDid(tools) {  // "ran 10 commands, read 3 files": what the steps did, by kind, top 3
  const counts = new Map();
  for (const t of tools) { const k = STEP_KINDS.findIndex(([re]) => re.test(t.name || "")); counts.set(k, (counts.get(k) || 0) + 1); }
  return [...counts].sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k, n]) => k < 0 ? `${n} other${n === 1 ? "" : "s"}` : STEP_KINDS[k][1](n)).join(", ");
}
function currentTool(c) {
  for (let i = c.steps.length - 1; i >= 0; i--) { const st = c.steps[i]; if (st.kind === "tool" && !st.done) return `${st.name}: ${st.summary || ""}`; }
  return c.activity || "";
}
const stepMark = st => st.error ? `<span class="mk bad">${I("x", 12)}</span>` : st.done ? `<span class="mk done">${I("check", 12)}</span>` : `<span class="mk cur"><span class="cdot"></span></span>`;
/* The turn as it happened: what the agent said between tool calls (Markdown), each run of tool calls as one folded
   line, each subagent its own block. */
function timelineHtml(c) {
  if (!c.steps.length) return "";
  const parts = [];
  for (const st of c.steps) {
    if (st.kind === "note") { parts.push({ note: st }); continue; }
    if (st.agent) { parts.push({ agent: st }); continue; }
    const last = parts[parts.length - 1];
    if (last && last.tools) last.tools.push(st); else parts.push({ tools: [st] });
  }
  return parts.map((p, i) => p.note ? `<div class="md said">${renderMd(p.note.text || p.note.summary)}</div>`
    : p.agent ? agentHtml(c, p.agent) : toolsHtml(c, p.tools, i)).join("");
}
function toolsHtml(c, tools, i) {
  const key = `${c.sid}:${c.id}:${i}`, open = S.openSteps.has(key);
  const did = stepsDid(tools), live = tools.some(t => !t.done);
  const rows = tools.map(st => `<li>${stepMark(st)}<span class="nm">${esc(st.name)}</span><span class="sm">${esc(st.summary)}</span></li>`).join("");
  return `<div class="steps${open ? " open" : ""}${live ? " live" : ""}"><button class="sum" data-steps="${esc(key)}">${I("caretr", 14)}` +
    `<span class="t">${tools.length} step${tools.length === 1 ? "" : "s"}${did ? " · " + esc(did) : ""}</span></button><ol>${rows}</ol></div>`;
}
/* A subagent: its type and task, what it does (open while it runs), then its report (folded once it is done). */
function agentHtml(c, st) {
  const key = `${c.sid}:${c.id}:a:${st.id}`;
  const open = S.openSteps.has(key) || (!st.done && !S.closedSteps.has(key));
  const sub = st.sub || [], tools = sub.filter(x => x.kind === "tool"), did = stepsDid(tools);
  const now = (tools.find(x => !x.done) || {}).name;
  const state = st.lost ? "ended with its agent" : st.error ? "failed" : st.done ? "done" : st.background ? (now ? `in the background · ${now}` : "running in the background") : now || "working";
  const rows = sub.map(x => x.kind === "note" ? `<li class="said">${esc(oneLine(x.text || x.summary).slice(0, 400))}</li>`
    : `<li>${stepMark(x)}<span class="nm">${esc(x.name)}</span><span class="sm">${esc(x.summary)}</span></li>`).join("");
  return `<div class="steps agent${open ? " open" : ""}${st.done ? "" : " live"}" data-agent="${esc(key)}">
    <button class="sum" data-steps="${esc(key)}">${I("caretr", 14)}${stepMark(st)}<span class="agtype">${esc(st.agent)}</span><span class="agtask">${esc(st.summary || "")}</span></button>
    <div class="agmeta">${tools.length ? `${tools.length} step${tools.length === 1 ? "" : "s"}${did ? " · " + esc(did) : ""} · ` : ""}${esc(state)}</div>
    <div class="agbody">${rows ? `<ol>${rows}</ol>` : ""}${st.result ? `<div class="agres"><div class="lbl">Report</div><div class="md">${renderMd(st.result)}</div></div>` : ""}</div>
  </div>`;
}
/* Elapsed · steps · compactions: on the head line (the desktop's footer, which shows on hover). */
function footText(c) {
  if (!c.started) return "";
  const n = c.steps.filter(s => s.kind === "tool").length;
  const parts = [dur((c.finished || Date.now() / 1000) - c.started)];
  if (n && !ACTIVE.has(c.state)) parts.push(`${n} step${n === 1 ? "" : "s"}`);
  if (c.compactions) parts.push(`⟲${c.compactions}`);
  return parts.join(" · ");
}
function originHtml(o) {
  if (!o || !(o.label || o.plugin)) return "";
  const what = esc(o.label || o.plugin), url = o.url && /^(https?|slack):\/\//.test(o.url) ? o.url : "";
  return `<div class="origin">${I("plug", 12)}<span>via ${url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${what}</a>` : what}</span></div>`;
}
/* A request as written: plain text, its code as code (fenced and `inline`), ![](path) as the image. */
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
const rawUrl = p => withToken(`/api/raw?path=${encodeURIComponent(p)}`);
function reqInline(text) {
  return text.split(/(`[^`\n]+`)/).map((part, k) => k % 2 ? `<code class="rc">${esc(part.slice(1, -1))}</code>` : reqLinks(part)).join("");
}
function reqLinks(text) {
  const re = /(!?)\[([^\]\n]*)\]\((<[^>\n]+>|[^)\s]+)\)/g;
  let out = "", last = 0, m;
  while ((m = re.exec(text))) {
    out += esc(text.slice(last, m.index));
    const target = machinePath(m[3].replace(/^<|>$/g, "")), local = target.startsWith("/");
    if (m[1] && (local || /^https?:/i.test(target))) out += `<img class="rimg" src="${esc(local ? rawUrl(target) : target)}" alt="${esc(m[2])}" loading="lazy">`;
    else if (local) out += `<span class="rfile">${esc(m[2] || basename(target))}</span>`;
    else out += esc(m[0]);
    last = m.index + m[0].length;
  }
  return out + esc(text.slice(last));
}
const REQ_FOLD = 12;
function reqBody(c) {
  const n = (c.text || "").split("\n").length, key = c.sid + ":" + c.id, fold = n > REQ_FOLD && !S.openReqs.has(key);
  return `<div class="rtext${fold ? " fold" : ""}">${reqHtml(c.text || "")}</div>` +
    (n > REQ_FOLD ? `<button class="rmore" data-reqmore="${esc(key)}">${fold ? `Show all ${n} lines` : "Show less"}</button>` : "");
}
const canSteer = sid => { const s = S.sessions.get(sid); return !!(s && s.steers && s.state === "working"); };
function cardHtml(c) {
  const sess = S.sessions.get(c.sid) || {};
  MD_MACHINE = sess.remote || ""; MD_CWD = sess.cwd || "";
  try { return cardHtml0(c); } finally { MD_MACHINE = ""; MD_CWD = ""; }
}
function cardHtml0(c) {
  const active = ACTIVE.has(c.state), tool = active ? currentTool(c) : "";
  const body = c.answer || [c.live, c.partial].filter(Boolean).join("\n\n");
  const acts = c.state === "queued"
    ? `${canSteer(c.sid) ? `<button data-steernow="${c.id}">${I("zap", 12)}Steer now</button>` : ""}<button data-withdraw="${c.id}">${I("undo", 12)}Withdraw</button>`
    : active ? "" : `<button data-resend="${c.id}">${I("resend", 12)}Resend</button>`;
  return `
    <div class="req">
      <div class="bubble">${originHtml(c.origin)}${reqBody(c)}
        <div class="meta"><button data-copyreq="${c.id}">${I("copy", 12)}Copy</button>${acts}<span class="when">#${c.id} · ${esc(dayTime(c.time))}</span></div></div>
      ${(c.steers || []).map(x => `<div class="steer"><span class="lbl">steer</span>${reqHtml(x.text)}</div>`).join("")}
    </div>
    <div class="ans s-${c.state}">
      <div class="head"><span class="cdot"></span><span class="st">${STATE_WORD[c.state] || esc(c.state)}</span>${tool ? `<span class="cur">${esc(tool)}</span>` : ""}
        <span class="el"${active && c.started ? ` data-t0="${c.started}"` : ""}>${esc(footText(c))}</span>
        ${c.state === "paused" && !c.continued ? `<button class="pill" data-continue="${c.id}">Continue</button>` : ""}</div>
      ${timelineHtml(c)}
      ${active && c.thinking && !c.partial ? `<div class="thinking">${esc(c.thinking.slice(-300))}</div>` : ""}
      ${body ? `<div class="md">${renderMd(body)}</div>` : ""}
      ${c.error && (c.state === "error" || c.state === "paused") ? `<div class="err-line">${esc(c.error)}</div>` : ""}
      ${(c.followups || []).map(f => `<div class="followup"><div class="lbl">↳ Follow-up</div><div class="md">${renderMd(f)}</div></div>`).join("")}
    </div>`;
}
const cardDiv = c => `<div class="card" id="card-${c.id}">${cardHtml(c)}</div>`;

/* Subagents at work in a session, foreground or background (the strip above the input). */
function runningAgents(sid) {
  const out = [];
  for (const c of S.cards.get(sid) || []) for (const st of c.steps) if (st.agent && !st.done && !st.lost) out.push({ c, st });
  return out;
}
