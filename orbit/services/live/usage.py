"""What the voice calls cost (Settings → Voice → Usage, and the phone): from each call's own log.

OpenAI does not let an API key read its account's balance or spending (the billing and organization endpoints answer
403 to a project key), so Orbit counts its own: every call ends with GPT Live's usage (active and standby seconds,
`session.closed`), kept in <state>/live/<call>.jsonl; the price is per second (config.RATES). The user may say what is
left on their account (`live_budget`, as of `live_budget_at`); what calls since then cost is taken off it, and the rest
is said in minutes of talking (active) and of standby.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from orbit.services.live import config


def cost(active_s: float, standby_s: float) -> float:
    return active_s / 60 * config.RATES["active"] + standby_s / 60 * config.RATES["standby"]


def calls(state: Path) -> list[dict]:
    """Each call: {call, t (its start), active_s, standby_s, estimated}. A call that ended without OpenAI's usage
    (no session.closed: a crash, a lost connection) counts its whole length as active: what it may have cost."""
    out = []
    for f in sorted((Path(state) / "live").glob("call-*.jsonl")):
        t0, usage, end_ms, last_ms = None, None, None, 0
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if t0 is None:
                    t0 = float(r.get("t") or 0) - (r.get("ms") or 0) / 1000
                last_ms = max(last_ms, int(r.get("ms") or 0))
                if isinstance(r.get("usage"), dict) and r["usage"]:
                    usage = r["usage"]
                if r.get("stage") == "ended":
                    end_ms = int(r.get("ms") or 0)
        except OSError:
            continue
        if t0 is None or (usage is None and last_ms < 1000):  # (never created: nothing billed)
            continue
        if usage and ("active_seconds" in usage or "paused_seconds" in usage):
            a, p, est = float(usage.get("active_seconds") or 0), float(usage.get("paused_seconds") or 0), False
        elif usage and "seconds" in usage:  # (no split given: all of it at the higher rate)
            a, p, est = float(usage["seconds"] or 0), 0.0, False
        else:
            a, p, est = (end_ms or last_ms) / 1000, 0.0, True
        out.append({"call": f.stem, "t": t0, "active_s": a, "standby_s": p, "estimated": est})
    return out


def summary(state: Path, settings: dict | None, now: float | None = None) -> dict:
    """This month's calls and cost; the balance left (when the user gave one) and the minutes it buys."""
    now = now or time.time()
    cfg = config.current(settings)
    start = datetime.fromtimestamp(now).replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    cs = calls(state)
    month = [c for c in cs if c["t"] >= start]
    a, p = sum(c["active_s"] for c in month), sum(c["standby_s"] for c in month)
    out = {"rates": config.RATES, "month": {"calls": len(month), "active_s": round(a), "standby_s": round(p),
                                            "cost": round(cost(a, p), 4), "estimated": any(c["estimated"] for c in month)}}
    budget, since = float((settings or {}).get("live_budget") or 0), float((settings or {}).get("live_budget_at") or 0)
    if budget > 0:
        spent = sum(cost(c["active_s"], c["standby_s"]) for c in cs if c["t"] >= since)
        left = max(0.0, budget - spent)
        out["budget"] = {"given": budget, "at": since, "spent": round(spent, 4), "left": round(left, 4),
                         "active_min": round(left / config.RATES["active"], 1), "standby_min": round(left / config.RATES["standby"], 1)}
    return out
