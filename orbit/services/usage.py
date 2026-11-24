"""How much of each harness's plan is used (the status bar's "Used", like the harness CLIs report it).

Claude Code: `claude -p /usage` prints the plan's windows ("Current week (all models): 94% used · resets Nov 22 at
2am (…)"); it is a local command (no model call, no cost). Codex: every turn's token_count event carries
`rate_limits` (primary / secondary windows: used_percent, window_minutes, resets_at, plan_type) in its rollout files;
the newest one is read. agy (Antigravity): `agy -p /usage` prints its quota windows, one per line, tab-separated
("Gemini Models · Weekly Limit Remaining · 99% · 2026-12-01T04:52:00Z"); a local command too (no model call, no
conversation made). OpenCode reports nothing (no plan of its own: each provider bills). A plugin's
agent answers `--usage` (Harness Protocol §3): its limits, and (plugin API 1.3) maybe a `quota` line ("8 USD left")
the status bar shows after its meter.

Results are cached; a background thread refreshes them every REFRESH seconds once the page has asked.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


REFRESH = 300
_LINE = re.compile(r"^\s*Current ([^:]+?):\s*(\d+(?:\.\d+)?)% used(?:\s*·\s*resets\s*(.+?))?\s*$", re.I)


def _label(window: str) -> str:
    """'week (all models)' → 'wk'; 'week (Fable)' → 'Fable'; 'session' → '5h'."""
    w = window.strip().lower()
    m = re.match(r"(\w+)\s*\((.+)\)", w)
    if m and m.group(2) not in ("all models", "all"):
        return window[window.index("(") + 1:window.rindex(")")].strip()
    base = m.group(1) if m else w
    return {"week": "wk", "session": "5h", "day": "day", "month": "mo"}.get(base, base)


def _resets_at(text: str, now: datetime | None = None) -> float | None:
    """'Nov 22 at 2am (America/Los_Angeles)' / '4:30pm' → an epoch (local time; the next such moment)."""
    now = now or datetime.now()
    t = re.sub(r"\s*\(.*\)\s*$", "", text or "").strip().replace(" at ", " ")
    for fmt in ("%b %d %I%p", "%b %d %I:%M%p", "%B %d %I%p", "%B %d %I:%M%p", "%I%p", "%I:%M%p", "%b %d"):
        try:
            d = datetime.strptime(t.upper() if "%p" in fmt else t, fmt)
        except ValueError:
            continue
        if "%b" in fmt or "%B" in fmt:
            d = d.replace(year=now.year)
            if d < now - timedelta(days=1):
                d = d.replace(year=now.year + 1)
        else:
            d = d.replace(year=now.year, month=now.month, day=now.day)
            if d < now:
                d += timedelta(days=1)
        return d.timestamp()
    return None


def parse_claude(text: str) -> dict[str, Any]:
    limits = []
    for line in (text or "").splitlines():
        m = _LINE.match(line)
        if m:
            limits.append({"label": _label(m.group(1)), "window": m.group(1).strip(), "used": float(m.group(2)),
                           "resets_text": (m.group(3) or "").strip(), "resets_at": _resets_at(m.group(3) or "")})
    first = next((l.strip() for l in (text or "").splitlines() if l.strip()), "")
    plan = "subscription" if "subscription" in first.lower() else ("API" if "api" in first.lower() else "")
    return {"plan": plan, "limits": limits}


def claude_usage() -> dict[str, Any]:
    exe = shlex.split(os.environ.get("ORBIT_CLAUDE_USAGE_BIN") or os.environ.get("ORBIT_CLAUDE_BIN") or "claude")
    if not shutil.which(exe[0]) and not os.path.exists(exe[0]):
        return {"id": "claude", "label": "Claude", "missing": True}
    try:
        r = subprocess.run(exe + ["-p", "/usage", "--output-format", "json", "--no-session-persistence"],
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL, cwd=str(Path.home()))
        d = json.loads(r.stdout or "{}")
        out = parse_claude(str(d.get("result") or ""))
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        return {"id": "claude", "label": "Claude", "error": str(e)[:200], "asof": time.time(), "limits": []}
    return {"id": "claude", "label": "Claude", "asof": time.time(), **out}


def _codex_window(w: dict | None) -> dict | None:
    if not isinstance(w, dict) or w.get("used_percent") is None:
        return None
    mins = int(w.get("window_minutes") or 0)
    label = "wk" if mins == 10080 else "5h" if mins == 300 else (f"{mins // 60}h" if mins and mins % 60 == 0 else f"{mins}m")
    at = w.get("resets_at")
    if at is None and w.get("resets_in_seconds") is not None:
        at = time.time() + float(w["resets_in_seconds"])
    return {"label": label, "window": f"{mins} min", "used": float(w["used_percent"]), "resets_at": at, "resets_text": ""}


def codex_usage() -> dict[str, Any]:
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    files = sorted(glob.glob(str(home / "sessions" / "**" / "rollout-*.jsonl"), recursive=True), key=os.path.getmtime, reverse=True)
    if not files and not shutil.which("codex"):
        return {"id": "codex", "label": "Codex", "missing": True}
    for f in files[:8]:  # the newest turn that reported its limits
        last = None
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"rate_limits"' in line:
                        last = line
        except OSError:
            continue
        if not last:
            continue
        try:
            d = json.loads(last)
        except ValueError:
            continue
        rl = ((d.get("payload") or {}).get("rate_limits")) or {}
        limits = [x for x in (_codex_window(rl.get("primary")), _codex_window(rl.get("secondary"))) if x]
        now = time.time()
        for x in limits:  # a window that has reset since that turn starts again from zero
            if x["resets_at"] and x["resets_at"] < now:
                x["used"], x["resets_at"] = 0.0, None
        plan = str(rl.get("plan_type") or "")
        return {"id": "codex", "label": "Codex", "plan": plan[:1].upper() + plan[1:], "limits": limits,
                "asof": os.path.getmtime(f)}
    return {"id": "codex", "label": "Codex", "limits": [], "asof": time.time()}


def parse_agy(text: str) -> list[dict[str, Any]]:
    """agy's /usage lines → limits (used = 100 − remaining), the tightest first (the status bar shows the first)."""
    limits = []
    for line in (text or "").splitlines():
        f = [x.strip() for x in line.split("\t")]
        if len(f) < 3 or not f[2].endswith("%"):
            continue
        try:
            left = float(f[2].rstrip("%"))
        except ValueError:
            continue
        group = re.sub(r"\s*models?$", "", f[0], flags=re.I).replace(" and ", "/")
        win = f[1].lower()
        span = "wk" if "week" in win else "5h" if "five hour" in win or "5 hour" in win else re.sub(r"\s*limit.*$", "", f[1], flags=re.I)
        at = None
        if len(f) > 3 and f[3]:
            try:
                at = datetime.fromisoformat(f[3].replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
        used = 100.0 - left if "remaining" in win else left
        limits.append({"label": f"{group} {span}", "window": f"{f[0]}: {f[1]}", "used": round(used, 1), "resets_at": at,
                       "resets_text": ""})
    limits.sort(key=lambda x: -x["used"])
    return limits


def agy_usage() -> dict[str, Any]:
    exe = shlex.split(os.environ.get("ORBIT_AGY_USAGE_BIN") or os.environ.get("ORBIT_AGY_BIN") or "agy")
    if not shutil.which(exe[0]) and not os.path.exists(exe[0]):
        return {"id": "agy", "label": "Antigravity", "missing": True}
    try:
        r = subprocess.run(exe + ["-p", "/usage"], capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL, cwd=str(Path.home()))
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"id": "agy", "label": "Antigravity", "error": str(e)[:200], "asof": time.time(), "limits": []}
    limits = parse_agy(r.stdout)
    if r.returncode != 0 and not limits:  # (2: an API key, which has no account quota)
        return {"id": "agy", "label": "Antigravity", "missing": True} if r.returncode == 2 else \
            {"id": "agy", "label": "Antigravity", "error": (r.stderr or r.stdout).strip()[:200], "asof": time.time(), "limits": []}
    return {"id": "agy", "label": "Antigravity", "asof": time.time(), "limits": limits}


class Usage:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {"harnesses": [], "at": 0}
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.wake = threading.Event()

    def refresh(self) -> dict[str, Any]:
        from orbit.desk.session import DRIVERS
        found = []
        for name, cls in list(DRIVERS.items()):
            try:
                x = cls.usage()
            except Exception:  # noqa: BLE001 - one harness's figures must not hide the others
                x = None
            if x and not x.get("missing"):
                if getattr(cls, "usage_fraction", False):  # a protocol agent says 0–1; the page (like Claude and Codex) shows percent
                    x = {**x, "limits": [{**l, "used": round(float(l.get("used") or 0) * 100, 1)} for l in x.get("limits") or [] if isinstance(l, dict)]}
                found.append({"id": name, "label": cls.label, **x})
        with self.lock:
            self.data = {"harnesses": found, "at": time.time()}
            return self.data

    def get(self) -> dict[str, Any]:
        """The cached figures; the first call starts the refresher (so tests and idle backends spawn nothing)."""
        if self.thread is None:
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()
        with self.lock:
            return self.data

    def kick(self) -> None:
        self.get()
        self.wake.set()

    def _loop(self) -> None:
        while True:
            try:
                self.refresh()
            except Exception:  # noqa: BLE001 — a broken CLI must not end the loop
                pass
            self.wake.wait(REFRESH)
            self.wake.clear()
