"use strict";
/* ===================================================================== card patches (shared: the page and the phone)
   The live stream (/api/events?delta=1) tells a streaming card's change as {"t":"cardp", sid, id, rev, base, ops}:
   what changed since revision `base` (orbit/desk/cardpatch.py has the ops). Applied to the copy this client holds
   when it is that revision; else (an event missed, the card loaded later) the card is fetched whole, once at a time.
   A card's state change always comes whole ({"t":"card"}). */
function applyCardPatch(c, ops) {
  const n = { ...c };
  if (Array.isArray(n.steps)) n.steps = n.steps.slice();
  for (const op of ops || []) {
    const k = op[0];
    if (k === "s") n[op[1]] = op[2];
    else if (k === "d") delete n[op[1]];
    else if (k === "a") n[op[1]] = (n[op[1]] || "") + op[2];
    else if (k === "L") n.steps.length = op[1];
    else if (k === "S") { if (!n.steps) n.steps = []; n.steps[op[1]] = op[2]; }
    else if (k === "ss" || k === "sd" || k === "sa") {
      const st = n.steps[op[1]] = { ...n.steps[op[1]] };
      if (k === "ss") st[op[2]] = op[3];
      else if (k === "sd") delete st[op[2]];
      else st[op[2]] = (st[op[2]] || "") + op[3];
    }
  }
  return n;
}
const CARDP_FETCHING = new Set();
/* m: the patch; have: this client's copy of that card (or null when it holds none it cares about: then nothing is
   fetched); done(card): what the client does with a card that came whole. */
function onCardPatch(m, have, done) {
  if (!have || (have.rev || 0) >= m.rev) return;
  if ((have.rev || 0) === m.base) { done({ ...applyCardPatch(have, m.ops), rev: m.rev }); return; }
  const key = m.sid + ":" + m.id;
  if (CARDP_FETCHING.has(key)) return;
  CARDP_FETCHING.add(key);
  api("GET", `/api/sessions/${encodeURIComponent(m.sid)}/cards/${m.id}?exact=1`)
    .then(r => { if (r && r.card) done(r.card); }).catch(() => {}).finally(() => CARDP_FETCHING.delete(key));
}
