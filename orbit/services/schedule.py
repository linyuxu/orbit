"""Schedules: agents that wake up. A schedule sends a request to a session at set times.

Stored in <data root>/schedules.json. A schedule targets an existing session (reopened if closed), a folder (each
run starts a new session there), or the Fleet (an idle helper). When a run is missed because Orbit was not running,
it runs once on start if it is less than CATCH_UP late (marked late), else it is skipped.

When (what people type; `parse` normalizes it):
    every 30m · every 2h · every 1d       an interval, from the last run
    daily 09:00 · weekdays 9:00 · weekends 10:30 · mon,wed,fri 18:00 · mon-thu 8am
    once 2026-11-19 09:00 · tomorrow 09:00 · today 17:00
    in 30m · in 2h                        once, that long from now
    cron 0 9 * * 1-5 · 0 9 * * 1-5        a 5-field cron (minute hour day month weekday), for what the rest cannot say

A reminder (`notify`) sends its text to the phone's lock screen at its times (orbit/services/push.py) and wakes no
agent; its session, if it names one, is what a tap opens. Each time it is due is sent once: the key is the schedule
and that time, so a run fired again after a restart (its next time not yet saved) reaches no device twice.

A watch schedule (`check`) runs a shell command on its interval with no agent and no cost; the command prints one
event per line (JSON with an "id", or plain text); only events not seen before wake the agent, with the task and the
new events. The first run only learns what is there (unless include_existing).

A repeating job keeps a journal (<data root>/jobs/<id>.md): every run is told to read it first and append what it did,
so a wake-up knows the last run without the session's context. Guards: at most `max` agent runs a day (default 24),
none after `until` (HH:MM) in a day, and a wake-up every minute at most (a watch may check every 10 s).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from orbit.desk import Desk

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
UNITS = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60, "h": 3600, "hr": 3600, "hrs": 3600,
         "hour": 3600, "hours": 3600, "d": 86400, "day": 86400, "days": 86400}
CATCH_UP = 6 * 3600
TICK = 5
MAX_A_DAY = 24  # agent runs a day per job, unless the job says otherwise
WATCH_MIN = 10  # seconds between a watch's checks, at least
CHECK_TIMEOUT = 120
EVENTS_SENT = 50  # new events a wake-up lists at most


# ------------------------------------------------------------------ when
def _clock(s: str) -> str:
    """'9:00', '09:00', '9am', '6:30pm' → 'HH:MM'."""
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", s.strip().lower())
    if not m or (m.group(2) is None and m.group(3) is None):
        raise ValueError(f"not a time of day: {s!r} (use 09:00 or 9am)")
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3):
        if not 1 <= h <= 12:
            raise ValueError(f"not a time of day: {s!r}")
        h = h % 12 + (12 if m.group(3) == "pm" else 0)
    if h > 23 or mi > 59:
        raise ValueError(f"not a time of day: {s!r}")
    return f"{h:02d}:{mi:02d}"


def _days(s: str) -> list[int]:
    s = s.lower()
    named = {"daily": range(7), "everyday": range(7), "every day": range(7), "weekdays": range(5),
             "weekends": range(5, 7)}
    if s in named:
        return list(named[s])
    full = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

    def day(x: str) -> int:
        x = x.strip()
        for i, name in enumerate(full):
            if len(x) >= 3 and name.startswith(x):
                return i
        raise ValueError(f"not a day: {x!r} (use mon, tue … or weekdays)")

    out: set[int] = set()
    for part in s.split(","):
        if "-" in part:
            i, j = (day(x) for x in part.split("-", 1))
            out.update(range(i, j + 1) if i <= j else [*range(i, 7), *range(0, j + 1)])
        else:
            out.add(day(part))
    return sorted(out)


def parse(when: str, now: float | None = None) -> dict:
    """Normalize what people type into {kind: every|weekly|once, ...}; ValueError says what is wrong."""
    now = time.time() if now is None else now
    w = " ".join(when.strip().lower().split())
    if not w:
        raise ValueError("say when: every 2h, daily 09:00, mon,wed 18:00, in 30m, tomorrow 9am")
    m = re.fullmatch(r"(?:cron\s+)?([\d*/,\-]+(?:\s+[\d*/,\-a-z]+){4})", w)
    if m and (w.startswith("cron") or len(w.split()) == 5 and "*" in w):
        _cron_fields(m.group(1))  # ValueError says what is wrong
        return {"kind": "cron", "expr": m.group(1)}
    m = re.fullmatch(r"(every|in)\s+(\d+)\s*([a-z]+)", w)
    if m:
        n, unit = int(m.group(2)), m.group(3)
        if unit not in UNITS or n <= 0:
            raise ValueError(f"not an interval: {w!r} (use 30m, 2h or 1d)")
        secs = n * UNITS[unit]
        if m.group(1) == "in":
            return {"kind": "once", "at": int(now + secs)}
        return {"kind": "every", "seconds": secs}  # the shortest allowed: Scheduler._check (1 min; a watch 10 s)
    m = re.fullmatch(r"(?:once\s+|at\s+)?(today|tomorrow|\d{4}-\d{2}-\d{2})\s+(.+)", w)
    if m:
        d0 = datetime.fromtimestamp(now).date()
        day = {"today": d0, "tomorrow": d0 + timedelta(days=1)}.get(m.group(1))
        if day is None:
            try:
                day = date.fromisoformat(m.group(1))
            except ValueError:
                raise ValueError(f"not a date: {m.group(1)!r}") from None
        hh, mm = map(int, _clock(m.group(2)).split(":"))
        return {"kind": "once", "at": int(datetime(day.year, day.month, day.day, hh, mm).timestamp())}
    m = re.fullmatch(r"(?:at\s+)?(\d[\d:]*\s*(?:am|pm)?)", w)
    if m:  # a bare time of day: every day
        return {"kind": "weekly", "days": list(range(7)), "time": _clock(m.group(1))}
    m = re.fullmatch(r"(?:every\s+)?(day|daily|everyday|weekdays|weekends|[a-z,\- ]+?)\s+(?:at\s+)?(\d[\d:]*\s*(?:am|pm)?)",
                     w)
    if m:
        days = m.group(1)
        days = "daily" if days == "day" else days.replace(" ", "") if "," in days or "-" in days else days
        return {"kind": "weekly", "days": _days(days), "time": _clock(m.group(2))}
    raise ValueError(f"cannot read {when!r}: try every 2h, daily 09:00, mon,wed 18:00, in 30m, tomorrow 9am")


def _cron_field(f: str, lo: int, hi: int, names: dict[str, int] | None = None) -> set[int]:
    out: set[int] = set()
    for part in f.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
            if step <= 0:
                raise ValueError(f"bad cron step: {f!r}")
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = (names.get(x, None) if names and x in names else int(x) for x in part.split("-", 1))
        else:
            a = names[part] if names and part in names else int(part)
            b = hi if step > 1 else a
        if a < lo or b > hi or a > b:
            raise ValueError(f"cron field out of range: {f!r} ({lo}-{hi})")
        out.update(range(a, b + 1, step))
    return out


def _cron_fields(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int], bool, bool]:
    try:
        mi, h, dom, mon, dow = expr.split()
        dnames = {d: i for i, d in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}
        days = _cron_field(dow, 0, 7, dnames)
        if 7 in days:
            days = (days - {7}) | {0}
        return (_cron_field(mi, 0, 59), _cron_field(h, 0, 23), _cron_field(dom, 1, 31), _cron_field(mon, 1, 12), days,
                dom != "*", dow != "*")
    except ValueError as e:
        raise ValueError(f"cannot read the cron {expr!r}: {e}") from None


def _cron_next(expr: str, after: float) -> float | None:
    mins, hours, doms, mons, dows, dom_set, dow_set = _cron_fields(expr)
    t = datetime.fromtimestamp(after).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 2):
        d = t.date()
        cdow = (d.weekday() + 1) % 7  # cron: 0 = Sunday
        day_ok = (d.day in doms or cdow in dows) if dom_set and dow_set else (d.day in doms and cdow in dows)
        if d.month in mons and day_ok:
            for hh in sorted(hours):
                for mm in sorted(mins):
                    c = datetime(d.year, d.month, d.day, hh, mm)
                    if c >= t:
                        return c.timestamp()
        t = datetime(d.year, d.month, d.day) + timedelta(days=1)
    return None


def describe(spec: dict) -> str:
    k = spec["kind"]
    if k == "cron":
        return f"cron {spec['expr']}"
    if k == "every":
        s = spec["seconds"]
        for unit, n in (("day", 86400), ("hour", 3600), ("minute", 60), ("second", 1)):
            if s % n == 0:
                v = s // n
                return f"every {unit}" if v == 1 else f"every {v} {unit}s"
    if k == "weekly":
        d = spec["days"]
        days = ("every day" if len(d) == 7 else "weekdays" if d == list(range(5)) else
                "weekends" if d == [5, 6] else ", ".join(DAYS[i].capitalize() for i in d))
        return f"{days} at {spec['time']}"
    return "once, " + datetime.fromtimestamp(spec["at"]).strftime("%a %Y-%m-%d %H:%M")


def next_run(spec: dict, after: float, last: float | None = None) -> float | None:
    """The first run time strictly after `after` (for an interval: `last` + interval)."""
    k = spec["kind"]
    if k == "once":
        return spec["at"] if spec["at"] > after else None
    if k == "cron":
        return _cron_next(spec["expr"], after)
    if k == "every":
        base = last if last else after
        t = base + spec["seconds"]
        return t if t > after else after + spec["seconds"]
    hh, mm = map(int, spec["time"].split(":"))
    d = datetime.fromtimestamp(after).date()
    for i in range(8):
        day = d + timedelta(days=i)
        if day.weekday() in spec["days"]:
            t = datetime(day.year, day.month, day.day, hh, mm).timestamp()
            if t > after:
                return t
    return None


# ------------------------------------------------------------------ scheduler
class Scheduler:
    def __init__(self, desk: "Desk", path: Path):
        self.desk = desk
        self.path = path
        self.lock = threading.RLock()
        self.items: list[dict] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._load()

    # storage
    def _load(self) -> None:
        try:
            self.items = json.loads(self.path.read_text())["schedules"]
        except (OSError, ValueError, KeyError):
            self.items = []
        now = time.time()
        for it in self.items:
            it.pop("_running", None)  # a check that was running when Orbit stopped
        for it in self.items:  # a run missed while Orbit was off: once if recent, else skip to the next
            if it.get("enabled") and it.get("next") and it["next"] < now - CATCH_UP:
                it["next"] = next_run(it["spec"], now, now)
                if it["next"] is None:
                    it["enabled"] = False
                it["skipped"] = it.get("skipped", 0) + 1

    def save(self) -> None:
        with self.lock:
            data = json.dumps({"schedules": self.items}, indent=1, ensure_ascii=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(data)
        os.replace(tmp, self.path)

    def _changed(self) -> None:
        self.save()
        self.desk.broadcast({"t": "schedules", "schedules": self.list()})

    # thread
    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="orbit-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(TICK):
            try:
                self.tick()
            except Exception as e:  # never let one bad schedule stop the others
                print(f"orbit: scheduler: {e}", flush=True)

    # API
    def list(self) -> list[dict]:
        with self.lock:
            out = []
            for it in self.items:
                d = json.loads(json.dumps(it))
                d["describe"] = describe(it["spec"])
                d["target_name"] = self._target_name(it)
                j = self.journal(it)
                d["journal"] = str(j) if j.exists() else ""
                d.pop("_running", None)
                out.append(d)
            return out

    def _target_name(self, it: dict) -> str:
        if it.get("notify"):
            s = self.desk.sessions.get(it.get("sid") or "")
            return "phone notification" + (f" → {s.name}" if s else "")
        if it.get("fleet"):
            return "Fleet" + (f" in {os.path.basename(it['cwd'].rstrip('/'))}" if it.get("cwd") else "")
        if it.get("sid"):
            s = self.desk.sessions.get(it["sid"])
            return s.name if s else "(session gone)"
        return "new session in " + os.path.basename(it.get("cwd", "").rstrip("/"))

    def journal(self, it: dict) -> Path:
        return self.path.parent / "jobs" / f"{it['id']}.md"

    def get(self, ref: str) -> dict:
        with self.lock:
            hits = [it for it in self.items if it["id"] == ref or it["id"].startswith(ref) and len(ref) >= 4]
            if not hits:
                hits = [it for it in self.items if it["name"].lower() == ref.lower()]
            if len(hits) != 1:
                raise KeyError(ref)
            return hits[0]

    def _check(self, b: dict, it: dict | None = None) -> dict:
        out: dict[str, Any] = {}
        if "name" in b or it is None:
            out["name"] = str(b.get("name") or "").strip()
        notify = bool(b.get("notify")) if "notify" in b else bool((it or {}).get("notify"))
        if "notify" in b or it is None:
            out["notify"] = notify
        if "text" in b or it is None:
            out["text"] = str(b.get("text") or "").strip()
            if not out["text"]:
                raise ValueError("say what the reminder says" if notify else "say what to ask the agent")
        if "when" in b or it is None:
            out["when"] = str(b.get("when") or "")
            out["spec"] = parse(out["when"])
        if "check" in b:
            out["check"] = str(b.get("check") or "").strip()
            out["include_existing"] = bool(b.get("include_existing"))
        check = out.get("check", (it or {}).get("check", ""))
        if notify and check:
            raise ValueError("a reminder sends a notification at its times: it has no check command")
        if "spec" in out:
            sp = out["spec"]
            if check and sp["kind"] != "every" and sp["kind"] != "cron":
                raise ValueError("a watch checks on an interval: every 30s, every 5m (or a cron)")
            if sp["kind"] == "every" and sp["seconds"] < (WATCH_MIN if check else 60):
                raise ValueError(f"a watch checks every {WATCH_MIN} s at most" if check else
                                 "an agent wakes up every minute at most: for anything faster, watch with a check command")
        if "max" in b:
            n = int(b.get("max") or 0)
            if n < 0:
                raise ValueError("max: runs a day, 1 or more (0: the default)")
            out["max"] = n
        if "until" in b:
            out["until"] = _clock(str(b["until"])) if b.get("until") else ""
        if notify:  # a reminder: no agent; its session (optional) is what a tap on the notification opens
            if "sid" in b or "cwd" in b or "fleet" in b or it is None:
                sid = str(b.get("sid") or "")
                if sid:
                    self.desk.get(sid)  # KeyError: no such session
                out["fleet"], out["sid"], out["cwd"] = False, sid, ""
        elif "fleet" in b and b.get("fleet"):
            cwd = os.path.realpath(os.path.expanduser(str(b.get("cwd") or "~")))
            if not os.path.isdir(cwd):
                raise ValueError(f"no such folder: {cwd}")
            out["fleet"], out["sid"], out["cwd"] = True, "", cwd
        elif "sid" in b or "cwd" in b or it is None:
            out["fleet"] = False
            sid, cwd = str(b.get("sid") or ""), str(b.get("cwd") or "")
            if sid:
                self.desk.get(sid)  # KeyError: no such session
                out["sid"], out["cwd"] = sid, ""
            elif cwd:
                cwd = os.path.realpath(os.path.expanduser(cwd))
                if not os.path.isdir(cwd):
                    raise ValueError(f"no such folder: {cwd}")
                out["sid"], out["cwd"] = "", cwd
            else:
                raise ValueError("pick a session, or a folder for a new session each run")
        if "group" in b:
            out["group"] = str(b.get("group") or "")
        if "enabled" in b:
            out["enabled"] = bool(b["enabled"])
        return out

    def create(self, b: dict) -> dict:
        fields = self._check(b)
        now = time.time()
        it = {"id": uuid.uuid4().hex[:10], "created": now, "enabled": True, "group": "", "by": str(b.get("by", "")),
              "last": {}, "runs": 0, **fields}
        if not it["name"]:
            it["name"] = it["text"][:40]
        it["next"] = next_run(it["spec"], now) if it["enabled"] else None
        if it["enabled"] and it["next"] is None:
            raise ValueError("that time has passed")
        if it["spec"]["kind"] != "once" and not it.get("notify"):  # a repeating job keeps a journal of its runs
            j = self.journal(it)
            j.parent.mkdir(parents=True, exist_ok=True)
            if not j.exists():
                j.write_text(f"# Journal: {it['name']}\n\n{describe(it['spec'])} · {it['text']}\n\n"
                             "Each run appends one entry: when, what it did, what the next run should know.\n")
        with self.lock:
            self.items.append(it)
        self._changed()
        return self._pub(it)

    def update(self, ref: str, b: dict) -> dict:
        with self.lock:
            it = self.get(ref)
            fields = self._check(b, it)
            if fields.get("enabled", it["enabled"]) and next_run(fields.get("spec", it["spec"]), time.time()) is None:
                raise ValueError("that time has passed")
            it.update(fields)
            if not it["name"]:
                it["name"] = it["text"][:40]
            now = time.time()
            if "spec" in fields or "enabled" in fields:
                it["next"] = next_run(it["spec"], now) if it["enabled"] else None
        self._changed()
        return self._pub(it)

    def delete(self, ref: str) -> None:
        with self.lock:
            it = self.get(ref)
            self.items.remove(it)
        self._changed()

    def check(self, ref: str) -> dict:
        """A watch's check command, now: what it prints and which events are new (nothing is sent, nothing kept)."""
        with self.lock:
            it = self.get(ref)
        if not it.get("check"):
            raise ValueError(f"{it['name']} is not a watch (it has no check command)")
        events, err = self._run_check(it)
        seen = set(it.get("seen") or [])
        return {"events": events, "new": [e for e in events if e["id"] not in seen], "error": err,
                "primed": bool(it.get("primed"))}

    def _pub(self, it: dict) -> dict:
        return next(x for x in self.list() if x["id"] == it["id"])

    def run(self, ref: str) -> dict:
        """Run now (does not move the next scheduled run)."""
        with self.lock:
            it = self.get(ref)
        self._fire(it, late=False, manual=True)
        return self._pub(it)

    def tick(self, now: float | None = None) -> list[str]:
        """Fire every due schedule; returns their ids."""
        now = time.time() if now is None else now
        with self.lock:
            due = [it for it in self.items if it.get("enabled") and it.get("next") and it["next"] <= now
                   and not it.get("_running")]
            for it in due:
                if it.get("check"):
                    it["_running"] = True  # a slow check does not pile up
        for it in due:
            late = now - it["next"] > 2 * TICK + 30
            if it.get("check"):
                threading.Thread(target=self._fire, args=(it, late, False, now), daemon=True).start()
            else:
                self._fire(it, late=late, now=now)
        return [it["id"] for it in due]

    # ---------- watch: the check command
    def _run_check(self, it: dict) -> tuple[list[dict], str]:
        import hashlib
        import subprocess
        cwd = it.get("cwd") or (self.desk.sessions[it["sid"]].cwd if it.get("sid") in self.desk.sessions else "")
        try:
            r = subprocess.run(it["check"], shell=True, cwd=cwd or os.path.expanduser("~"), capture_output=True,
                               text=True, timeout=CHECK_TIMEOUT)
        except subprocess.TimeoutExpired:
            return [], f"the check took over {CHECK_TIMEOUT} s"
        except OSError as e:
            return [], str(e)
        if r.returncode != 0:
            return [], f"the check exited {r.returncode}: {(r.stderr or r.stdout).strip()[-300:]}"
        events = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                d = None
            if isinstance(d, dict) and d.get("id") is not None:
                events.append({"id": str(d["id"]), "line": line})
            else:
                events.append({"id": hashlib.sha1(line.encode()).hexdigest()[:16], "line": line})
        return events, ""

    def _notify(self, it: dict, last: dict, manual: bool, now: float) -> None:
        from orbit.services import push
        due = it.get("next") or now
        key = f"sched:{it['id']}:" + (f"manual:{now:.3f}" if manual else str(int(due)))
        r = push.of(self.desk).notify(it["name"], it["text"], it.get("sid") or "", key=key, via="schedule")
        last["push"] = {"sent": r["sent"], "duplicate": r["duplicate"], "devices": r["devices"], "key": key}
        if not r["devices"]:
            raise RuntimeError("no phone has notifications on (Orbit on the phone: Misc → Notifications)")
        if not r["sent"] and not r["duplicate"]:
            raise RuntimeError("the push service took it on no device: " + "; ".join(
                f'{x.get("status") or ""} {x.get("reason") or x["outcome"]}'.strip() for x in r["results"]))

    def _today_runs(self, it: dict, now: float) -> int:
        day = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        if it.get("day") != day:
            it["day"], it["today"] = day, 0
        return it["today"]

    def _fire(self, it: dict, late: bool, manual: bool = False, now: float | None = None) -> None:
        now = time.time() if now is None else now
        last: dict[str, Any] = {"at": now, "late": late, "manual": manual}
        wake, extra, notified = True, "", False
        try:
            if it.get("notify"):  # a reminder: to the phone, no agent
                wake, notified = False, True
                self._notify(it, last, manual, now)
            elif it.get("check"):  # a watch: the command decides whether the agent wakes
                events, err = self._run_check(it)
                if err:
                    raise RuntimeError(err)
                seen = list(it.get("seen") or [])
                new = [e for e in events if e["id"] not in set(seen)]
                first = not it.get("primed")
                last["events"] = len(new)
                wake = bool(new) and (not first or it.get("include_existing") or manual)
                if wake:
                    shown = new[:EVENTS_SENT]
                    extra = ("\n\nNew events (one per line" + (f", the first {EVENTS_SENT} of {len(new)}" if len(new) > EVENTS_SENT else "")
                             + "):\n" + "\n".join(e["line"][:500] for e in shown))
            if it.get("check") and not wake:  # the first look (or nothing new): what is there now counts as seen
                with self.lock:
                    it["seen"], it["primed"] = (seen + [e["id"] for e in new])[-1000:], True
            if wake and not manual:
                cap = it.get("max") or MAX_A_DAY
                until = it.get("until") or ""
                if self._today_runs(it, now) >= cap:
                    wake, last["skipped"] = False, f"{cap} runs today already (max)"
                elif until and datetime.fromtimestamp(now).strftime("%H:%M") >= until:
                    wake, last["skipped"] = False, f"after {until} (until)"
            if wake:
                text = f'(schedule "{it["name"]}") {it["text"]}{extra}'
                if it["spec"]["kind"] != "once" or it.get("check"):
                    text += (f"\n\nThis job's journal is {self.journal(it)}: read it first; before you finish, append one "
                             "entry (the time, what you did, what the next run should know).")
                if it.get("fleet"):
                    r = self.desk.fleet_ask(text, it.get("cwd") or "")
                    sid, card = r["session"]["id"], r["card"]
                elif it.get("sid"):
                    s = self.desk.get(it["sid"])
                    if s.closed:
                        self.desk.reopen(s.id)
                    sid = s.id
                    card = self.desk.send(sid, text)
                else:
                    stamp = datetime.fromtimestamp(now).strftime("%m-%d %H:%M")
                    sid = self.desk.create(cwd=it["cwd"], name=f'{it["name"]} · {stamp}', group=it.get("group", ""))["id"]
                    card = self.desk.send(sid, text)
                last.update(sid=sid, cid=card["id"])
                with self.lock:
                    self._today_runs(it, now)
                    it["today"] = it.get("today", 0) + 1
                    if it.get("check"):  # told: seen (held back by max / until, they wake a later run)
                        it["seen"], it["primed"] = (seen + [e["id"] for e in new])[-1000:], True
        except Exception as e:  # noqa: BLE001 - the schedule shows why it could not run
            last["error"] = str(e) if not isinstance(e, KeyError) else "the session is gone"
        with self.lock:
            it.pop("_running", None)
            if wake or not it.get("check") or last.get("error") or last.get("skipped"):
                it["last"] = last
            else:
                it["checked"] = {"at": now, "events": last.get("events", 0)}  # a quiet check: the last run stays
            if wake or (notified and not last.get("error")):
                it["runs"] = it.get("runs", 0) + 1
            if not manual:
                if it["spec"]["kind"] == "once":
                    it["enabled"], it["next"] = False, None
                else:
                    it["next"] = next_run(it["spec"], now, now)
        self._changed()
