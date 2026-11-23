"""Conversations started outside Orbit (a `claude` or `codex` in a terminal, anywhere), to attach to.

Read-only listing of each harness's own store: Claude Code transcripts (~/.claude/projects/*/<id>.jsonl, with the
running ones in ~/.claude/sessions/<pid>.json) and Codex rollouts (~/.codex/sessions/**/rollout-*.jsonl).
Attaching makes an Orbit session that resumes the conversation (the Desk does that).

Names the user gave there come along: Claude Code's /rename is a {"type":"custom-title"} line in the transcript (the
last one counts; its own summary is an "ai-title" line), Codex's thread names are in ~/.codex/session_index.jsonl
(the last line for an id counts). Orbit's own names are Orbit's (sessions.json): it writes neither store.
"""

from __future__ import annotations

import glob
import json
import os
from pathlib import Path
from typing import Any

from orbit.drivers.base import strip_orbit_line
from orbit.drivers.claude import _user_text
from orbit.drivers.codex_store import rollout_files


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def claude_live() -> dict[str, dict]:
    """Claude Code conversations a running process holds: session id → {pid, status, kind}."""
    out: dict[str, dict] = {}
    for f in glob.glob(str(Path.home() / ".claude" / "sessions" / "*.json")):
        try:
            d = json.loads(Path(f).read_text())
        except (OSError, ValueError):
            continue
        if d.get("sessionId") and isinstance(d.get("pid"), int) and _alive(d["pid"]):
            out[d["sessionId"]] = {"pid": d["pid"], "status": d.get("status", ""), "kind": d.get("kind", "")}
    return out


_TITLES: dict[str, tuple[float, int, str, str]] = {}  # transcript -> (mtime, size, its name, Claude's own title)


def _last_of_type(b: bytes, kind: str) -> dict:
    """The last JSON line of b whose "type" is kind (found from the end, without parsing the rest)."""
    key, end = json.dumps(kind).encode(), len(b)
    while (i := b.rfind(key, 0, end)) >= 0:
        start, stop = b.rfind(b"\n", 0, i) + 1, b.find(b"\n", i)
        try:
            d = json.loads(b[start:stop if stop >= 0 else len(b)])
        except ValueError:
            d = None
        if isinstance(d, dict) and d.get("type") == kind:
            return d
        end = start
    return {}


def claude_titles(path: str) -> tuple[str, str]:
    """(the name the user gave it with /rename, Claude Code's own title): read once per change of the file."""
    try:
        st = os.stat(path)
    except OSError:
        return "", ""
    hit = _TITLES.get(path)
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2], hit[3]
    try:
        with open(path, "rb") as f:
            b = f.read()
    except OSError:
        return "", ""
    name = str(_last_of_type(b, "custom-title").get("customTitle") or "").strip()
    title = str(_last_of_type(b, "ai-title").get("aiTitle") or "").strip()
    _TITLES[path] = (st.st_mtime, st.st_size, name, title)
    return name, title


def codex_names(home: Path) -> dict[str, str]:
    """Codex thread id -> the name it has (renamed, or Codex's own)."""
    out: dict[str, str] = {}
    try:
        with open(home / "session_index.jsonl", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict) and d.get("id") and isinstance(d.get("thread_name"), str):
                    out[d["id"]] = d["thread_name"].strip()
    except OSError:
        pass
    return out


def _first_lines(path: str, n: int = 400):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= n:
                    return
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def _stat_newest(paths) -> list[tuple[str, float, int]]:
    """(path, mtime, size) of each file that is still there, newest first."""
    out = []
    for f in paths:
        try:
            st = os.stat(f)
        except OSError:  # moved or compressed meanwhile
            continue
        out.append((f, st.st_mtime, st.st_size))
    return sorted(out, key=lambda x: -x[1])


def claude_files() -> list[tuple[str, float, int]]:
    root = os.environ.get("ORBIT_CLAUDE_PROJECTS", str(Path.home() / ".claude" / "projects"))
    return _stat_newest(glob.glob(os.path.join(glob.escape(root), "*", "*.jsonl")))


def claude_row(f: str) -> dict[str, Any] | None:
    """One transcript as a conversation to resume (its "live" is added when listed), or None: nothing asked yet."""
    sid = Path(f).stem
    cwd, first = "", ""
    for d in _first_lines(f):
        cwd = cwd or d.get("cwd", "")
        if d.get("type") == "user" and not d.get("isMeta") and not d.get("isSidechain"):
            t = _user_text((d.get("message") or {}).get("content"))
            if t and not t.startswith("<"):
                first = strip_orbit_line(t).strip()
        if cwd and first:
            break
    name, title = claude_titles(f)
    if not first and not name:
        return None  # nothing asked yet (or only tool noise)
    try:
        mtime = os.path.getmtime(f)
    except OSError:
        return None
    return {"harness": "claude", "id": sid, "cwd": cwd, "first": first[:300], "name": name[:200],
            "title": title[:200], "time": mtime, "live": None}


def codex_files() -> list[tuple[str, float, int]]:
    return _stat_newest(rollout_files())


def codex_row(f: str) -> dict[str, Any] | None:
    """One rollout as a conversation to resume (its thread name is added when listed), or None."""
    tid, cwd, first = "", "", ""
    for d in _first_lines(f, 200):
        p = d.get("payload") or {}
        if d.get("type") == "session_meta":
            tid, cwd = p.get("id", ""), p.get("cwd", "")
        elif d.get("type") == "event_msg" and p.get("type") == "user_message" and p.get("message"):
            first = strip_orbit_line(str(p["message"])).strip()
            break
        elif d.get("type") == "response_item" and p.get("role") == "user":  # newer Codex: the prompt as an item
            text = "\n".join(c.get("text", "") for c in p.get("content") or [] if c.get("type") == "input_text")
            if text and not text.lstrip().startswith("<"):  # not the environment context
                first = strip_orbit_line(text).strip()
                break
    if not tid or not first:
        return None
    try:
        mtime = os.path.getmtime(f)
    except OSError:
        return None
    return {"harness": "codex", "id": tid, "cwd": cwd, "first": first[:300], "name": "", "title": "", "time": mtime,
            "live": None}


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def claude_sessions(limit: int = 40, skip: set[str] | None = None) -> list[dict[str, Any]]:
    live = claude_live()
    out = []
    for f, _, _ in claude_files():
        x = claude_row(f)
        if x is None or (skip and x["id"] in skip):
            continue
        out.append({**x, "live": live.get(x["id"])})
        if len(out) >= limit:
            break
    return out


def codex_sessions(limit: int = 40, skip: set[str] | None = None) -> list[dict[str, Any]]:
    names = codex_names(codex_home())
    out = []
    for f, _, _ in codex_files():
        x = codex_row(f)
        if x is None or (skip and x["id"] in skip):
            continue
        out.append({**x, "name": names.get(x["id"], "")[:200]})
        if len(out) >= limit:
            break
    return out
