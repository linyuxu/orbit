"use strict";
/* ===================================================================== a group's statistics on its home page */
/* Three widgets (orbit/desk/stats.py has the data): Stats, this week in numbers (requests, agent time, done, the
   hsMedian request, working now) and where the last 30 days' requests came from; Activity, the last 12 weeks' requests
   by day; Agent time, the last 14 days' agent time by session. All from the requests of the group's sessions
   (subgroups', closed ones and other machines' too), bucketed by this page's local day. Colours: the dataviz
   reference palette, validated for colour-vision deficiency, light and dark (home.css --cat-*, --seq-*). */
const HS = { data: new Map(), tip: null };  // gid → {at, d, err, loading}
const HS_SRC = { you: "You", voice: "Voice", agents: "Other agents", schedule: "Schedules", plugin: "Plugins", auto: "Agent's own" };
const HS_DAYS = 84, HS_FRESH_MS = 60000, HS_BARS = 14, HS_TOP = 5;
function statsOf(gid) {
  const c = HS.data.get(gid);
  if ((!c || Date.now() - c.at > HS_FRESH_MS) && !(c && c.loading)) {
    HS.data.set(gid, { ...(c || {}), loading: true, at: (c && c.at) || 0 });
    api("GET", `/api/groups/${encodeURIComponent(gid)}/stats?days=${HS_DAYS}`)
      .then(d => HS.data.set(gid, { at: Date.now(), d }), e => HS.data.set(gid, { at: Date.now(), err: e.message }))
      .then(() => { if (homeOn() && S.group === gid) renderHome(); });
  }
  return HS.data.get(gid);
}
const hsDay = t => { const d = new Date(t * 1000); return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`; };
const hsDayStart = (t, back = 0) => { const d = new Date(t * 1000); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() - back); return d.getTime() / 1000; };
const hsHrs = s => s >= 36000 ? `${Math.round(s / 3600)} h` : s >= 3600 ? `${(s / 3600).toFixed(1)} h` : s >= 60 ? `${Math.round(s / 60)} min` : `${Math.round(s)} s`;
const hsDur = (r, now) => Math.max(0, (r[3] || (["thinking", "running"].includes(r[4]) ? now : r[2])) - r[2]);
const hsAsk = r => r[5] !== "auto";  // (requests: the agent's own turns count as its time, not as requests)
function hsMedian(xs) { if (!xs.length) return 0; const a = [...xs].sort((x, y) => x - y), m = a.length >> 1; return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2; }

function statsBody(gid, w) {
  const c = statsOf(gid);
  if (!c.d) return c.err ? `<div class="hempty">${esc(c.err)}</div>` : `<div class="hempty">Counting…</div>`;
  const d = c.d, now = d.now;
  const part = d.partial && d.partial.length ? `<div class="hsnote">Without ${esc(d.partial.join(", "))} (not reached).</div>` : "";
  if (w.kind === "stats") return hsKpis(gid, d, now) + part;
  if (w.kind === "heatmap") return hsHeatmap(d, now) + part;
  if (w.kind === "agenttime") return hsAgentTime(d, now) + part;
  return "";
}

/* ---------- Stats: this week against the one before; where the requests came from */
function hsKpis(gid, d, now) {
  const wk = now - 7 * 86400, prev = now - 14 * 86400;
  const cur = d.records.filter(r => r[1] >= wk), was = d.records.filter(r => r[1] >= prev && r[1] < wk);
  const n = cur.filter(hsAsk).length, n0 = was.filter(hsAsk).length;
  const t = cur.reduce((a, r) => a + hsDur(r, now), 0), t0 = was.reduce((a, r) => a + hsDur(r, now), 0);
  const ended = cur.filter(r => ["completed", "error", "cancelled"].includes(r[4])), ok = ended.filter(r => r[4] === "completed");
  const med = hsMedian(ok.filter(hsAsk).map(r => hsDur(r, now)));
  const working = subtreeSessions(gid).filter(s => s.state === "working" || s.agents > 0).length;
  // against the week before: a percentage once there is a base to compare with (else 1025 vs 2 reads ↑ 51150%)
  const delta = (a, b, small, fmt) => b >= small ? `${a >= b ? "↑" : "↓"} ${Math.abs(Math.round((a - b) / b * 100))}% vs the week before`
    : `vs ${b ? fmt(b) : "none"} the week before`;
  const tile = (v, label, sub, tip) => `<div class="kpi" data-ct="${esc(tip)}"><div class="kv">${v}</div><div class="kl">${esc(label)}</div><div class="ks">${esc(sub)}</div></div>`;
  const tiles = tile(n, "Requests, 7 days", delta(n, n0, 10, x => x), `${n} requests in the last 7 days, ${n0} the 7 before`) +
    tile(hsHrs(t), "Agent time, 7 days", delta(t, t0, 3600, hsHrs), `Agents worked ${hsHrs(t)} in the last 7 days (${hsHrs(t0)} the 7 before)`) +
    tile(ended.length ? `${Math.round(ok.length / ended.length * 100)}%` : "–", "Done", ended.length ? `${ok.length} of ${ended.length} finished` : "nothing finished",
         `${ok.length} completed, ${ended.length - ok.length} failed or stopped (last 7 days)`) +
    tile(med ? hsHrs(med) : "–", "Median request", ok.length ? `of ${ok.filter(hsAsk).length} completed` : "", "How long a completed request took, the middle one (last 7 days)") +
    tile(working, "Working now", working === 1 ? "session" : "sessions", "Sessions of this group at work now");
  // where the last 30 days' requests came from: one bar, its parts in the sources' fixed order (the colour is the source's)
  const m30 = d.records.filter(r => r[1] >= now - 30 * 86400), by = {};
  for (const r of m30) by[r[5]] = (by[r[5]] || 0) + 1;
  const total = m30.length, srcs = d.sources.filter(s => by[s]);
  let bar = "";
  if (total) {
    let x = 0;
    const segs = srcs.map(s => { const wpc = by[s] / total * 100, seg = `<span class="sseg" style="left:${x}%;width:calc(${wpc}% - 2px);background:var(--cat-${d.sources.indexOf(s) + 1})" data-ct="${esc(`${HS_SRC[s]}: ${by[s]} of ${total} (${Math.round(wpc)}%), last 30 days`)}"></span>`; x += wpc; return seg; }).join("");
    bar = `<div class="srcs"><div class="srch">Who asked, last 30 days · ${total} requests</div><div class="sbar">${segs}</div>` +
      `<div class="slegend">${srcs.map(s => `<span><i style="background:var(--cat-${d.sources.indexOf(s) + 1})"></i>${esc(HS_SRC[s])} <b>${Math.round(by[s] / total * 100)}%</b></span>`).join("")}</div></div>`;
  }
  return `<div class="kpis">${tiles}</div>${bar}${hsCollab(d)}`;
}
/* Agents working together: the most frequent sender → receiver pairs of the last 30 days (either end in this group);
   a session known here opens on a click. */
function hsCollab(d) {
  const es = (d.edges || []).slice(0, 4);
  if (!es.length) return "";
  const end = x => S.sessions.has(x.sid) ? `<button class="clink" data-hopen="${esc(x.sid)}">${esc(sessionTitle(S.sessions.get(x.sid)) || x.name)}</button>`
    : `<span class="cname">${esc(x.name || "?")}</span>`;
  return `<div class="collab"><div class="srch">Agents working together, last 30 days</div>` +
    es.map(e => `<div class="crow" data-ct="${esc(`${e.from.name || "?"} sent ${e.to.name || "?"} ${e.n} request${e.n === 1 ? "" : "s"} (last 30 days)`)}">` +
      `${end(e.from)}<span class="carrow">→</span>${end(e.to)}<span class="cn">×${e.n}</span></div>`).join("") + `</div>`;
}

/* ---------- Activity: 12 weeks of days, Monday on top; the colour is the day's requests */
function hsHeatmap(d, now) {
  const per = new Map(), time = new Map();
  for (const r of d.records) { const k = hsDay(r[1]); if (hsAsk(r)) per.set(k, (per.get(k) || 0) + 1); time.set(k, (time.get(k) || 0) + hsDur(r, now)); }
  const today = new Date(now * 1000), dow = (today.getDay() + 6) % 7;  // Monday 0
  const weeks = 12, start = hsDayStart(now, dow + 7 * (weeks - 1));
  const vals = [...per.values()].sort((a, b) => a - b);
  const q = p => vals.length ? vals[Math.min(vals.length - 1, Math.floor(p * vals.length))] : 0;
  const cuts = [q(0.2), q(0.4), q(0.6), q(0.8)];  // five steps over the days that had requests
  const step = v => !v ? 0 : 1 + cuts.filter(c => v > c).length;
  const C = 12, G = 3, L = 26, T = 16;  // cell, gap, left labels, top labels
  let cells = "", months = "", lastM = -1, active = 0, best = null;
  for (let wk = 0; wk < weeks; wk++) {
    for (let dd = 0; dd < 7; dd++) {
      const t = start + (wk * 7 + dd) * 86400 + 3600;  // (an hour in: a DST day stays itself)
      if (t > now) continue;
      const day = new Date(t * 1000), k = hsDay(t), v = per.get(k) || 0, s = step(v);
      if (v) { active++; if (!best || v > best.v) best = { v, day }; }
      const label = day.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
      cells += `<rect x="${L + wk * (C + G)}" y="${T + dd * (C + G)}" width="${C}" height="${C}" rx="2.5" class="hc s${s}" data-ct="${esc(`${label}: ${v} request${v === 1 ? "" : "s"}` + (time.get(k) ? ` · ${hsHrs(time.get(k))} agent time` : ""))}"/>`;
      if (dd === 0 && day.getMonth() !== lastM) { lastM = day.getMonth(); months += `<text x="${L + wk * (C + G)}" y="10" class="ax">${esc(day.toLocaleDateString(undefined, { month: "short" }))}</text>`; }
    }
  }
  const wd = ["Mon", "", "Wed", "", "Fri", "", ""].map((x, i) => x ? `<text x="0" y="${T + i * (C + G) + C - 2}" class="ax">${x}</text>` : "").join("");
  const W = L + weeks * (C + G), H = T + 7 * (C + G);
  const legend = `<div class="hlegend">Fewer ${[0, 1, 2, 3, 4, 5].map(s => `<svg width="11" height="11"><rect width="11" height="11" rx="2.5" class="hc s${s}"/></svg>`).join("")} more</div>`;
  const head = active ? `${active} active day${active === 1 ? "" : "s"} of the last ${weeks} weeks · busiest ${best.day.toLocaleDateString(undefined, { month: "short", day: "numeric" })} (${best.v})` : `No request in the last ${weeks} weeks`;
  return `<div class="chh">${esc(head)}</div><svg class="heat" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${esc(head)}">${months}${wd}${cells}</svg>${legend}`;
}

/* ---------- Agent time: the last 14 days, a bar a day, a part per session (the top five, then the rest) */
function hsAgentTime(d, now) {
  const first = hsDayStart(now, HS_BARS - 1), days = [...Array(HS_BARS)].map((_, i) => first + i * 86400 + 3600);
  const idx = new Map(days.map((t, i) => [hsDay(t), i]));
  const tot = new Map(), cell = new Map();  // session → seconds; "i:s" → [seconds, requests]
  for (const r of d.records) {
    const i = idx.get(hsDay(r[1])); if (i === undefined) continue;
    const s = hsDur(r, now); if (!s) continue;
    tot.set(r[0], (tot.get(r[0]) || 0) + s);
    const k = `${i}:${r[0]}`, x = cell.get(k) || [0, 0]; x[0] += s; x[1] += hsAsk(r) ? 1 : 0; cell.set(k, x);
  }
  const ranked = [...tot.entries()].sort((a, b) => b[1] - a[1]), top = ranked.slice(0, HS_TOP).map(e => e[0]);
  const all = ranked.reduce((a, e) => a + e[1], 0);
  if (!all) return `<div class="hempty">No agent time in the last ${HS_BARS} days.</div>`;
  const series = [...top.map((sid, k) => ({ keys: [sid], name: d.sessions[sid].name || "?", color: `var(--cat-${k + 1})` })),
    ...(ranked.length > HS_TOP ? [{ keys: ranked.slice(HS_TOP).map(e => e[0]), name: `${ranked.length - HS_TOP} other${ranked.length - HS_TOP === 1 ? "" : "s"}`, color: "var(--cat-rest)" }] : [])];
  const dayTot = days.map((_, i) => series.reduce((a, se) => a + se.keys.reduce((b, sid) => b + ((cell.get(`${i}:${sid}`) || [0])[0]), 0), 0));
  const maxH = Math.max(...dayTot) / 3600, unit = maxH > 6 ? 2 : maxH > 3 ? 1 : 0.5, ymax = Math.max(unit, Math.ceil(maxH / unit) * unit);
  const Wd = 420, Ht = 150, L = 30, B = 18, T = 6, bw = (Wd - L) / HS_BARS, y = h => T + (Ht - T - B) * (1 - h / ymax);
  let grid = "";
  for (let h = 0; h <= ymax + 1e-9; h += unit) grid += `<line x1="${L}" x2="${Wd}" y1="${y(h)}" y2="${y(h)}" class="${h ? "gl" : "bl"}"/><text x="${L - 5}" y="${y(h) + 3}" text-anchor="end" class="ax">${h} h</text>`;
  let bars = "";
  days.forEach((t, i) => {
    let base = 0;
    const parts = series.map(se => { const v = se.keys.reduce((a, sid) => { const c = cell.get(`${i}:${sid}`); return [a[0] + (c ? c[0] : 0), a[1] + (c ? c[1] : 0)]; }, [0, 0]); return { se, v }; }).filter(p => p.v[0] > 0);
    const label = new Date(t * 1000).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
    parts.forEach((p, k) => {
      const h0 = base / 3600, h1 = (base + p.v[0]) / 3600; base += p.v[0];
      const top_ = k === parts.length - 1, x = L + i * bw + bw * 0.18, w = bw * 0.64, y1 = y(h1), y0 = y(h0) - (k ? 1 : 0);  // (a 2px surface gap between parts)
      const hgt = Math.max(0.5, y0 - y1 - (top_ ? 0 : 1));
      bars += `<path d="${top_ ? hsRoundTop(x, y1, w, hgt, Math.min(4, hgt)) : `M${x},${y1 + 1}h${w}v${hgt}h${-w}z`}" fill="${p.se.color}" class="bar" ` +
        `data-ct="${esc(`${p.se.name} · ${label}: ${hsHrs(p.v[0])}${p.v[1] ? ` (${p.v[1]} request${p.v[1] === 1 ? "" : "s"})` : ""}`)}"/>`;
    });
    if (i % 2 === (HS_BARS - 1) % 2) bars += `<text x="${L + i * bw + bw / 2}" y="${Ht - 4}" text-anchor="middle" class="ax">${new Date(t * 1000).getDate()}</text>`;
  });
  const lead = series[0], leadT = lead.keys.reduce((a, sid) => a + (tot.get(sid) || 0), 0);
  const head = `${hsHrs(all)} in ${HS_BARS} days · most: ${lead.name} (${hsHrs(leadT)})`;
  const legend = series.length > 1 ? `<div class="slegend">${series.map(se => `<span><i style="background:${se.color}"></i>${esc(se.name)}</span>`).join("")}</div>` : "";
  return `<div class="chh">${esc(head)}</div><svg class="atime" viewBox="0 0 ${Wd} ${Ht}" role="img" aria-label="${esc(head)}">${grid}${bars}</svg>${legend}`;
}
const hsRoundTop = (x, y, w, h, r) => `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}z`;

/* ---------- hover: the value under the pointer (bars, cells, tiles, source parts) */
function hsChartTip(e) {
  const el = e.target.closest && e.target.closest("[data-ct]");
  if (!el || !homeOn()) { if (HS.tip) HS.tip.classList.remove("show"); return; }
  if (!HS.tip) { HS.tip = document.createElement("div"); HS.tip.id = "ctip"; HS.tip.setAttribute("role", "tooltip"); document.body.appendChild(HS.tip); }
  HS.tip.textContent = el.dataset.ct;
  const k = rectScale(), w = HS.tip.offsetWidth, h = HS.tip.offsetHeight;
  let x = e.clientX / k + 12, y = e.clientY / k - h - 10;
  if (x + w > innerWidth / k - 6) x = e.clientX / k - w - 12;
  if (y < 6) y = e.clientY / k + 16;
  HS.tip.style.left = x + "px"; HS.tip.style.top = y + "px"; HS.tip.classList.add("show");
}
$("#xview").addEventListener("mousemove", hsChartTip);
$("#xview").addEventListener("mouseleave", () => { if (HS.tip) HS.tip.classList.remove("show"); });
