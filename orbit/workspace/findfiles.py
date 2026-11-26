"""Find files by name everywhere this machine has them in view (⌘O → All Files): every open session's folder and the
home folder. Every term of the query must be in the file's path below its root (case-insensitive); the ones with
more terms in their own name come first, then shorter paths, then the newest.

- A session's folder: its listing as the Files panel has it (files.tree: git's files, or a walk), kept a few seconds.
- The home: on macOS Spotlight (`mdfind -onlyin HOME -name TERM`, the longest term, then every term checked here),
  else (and when Spotlight fails) a walk with a time budget that skips hidden folders, ~/Library, the build and
  dependency folders and the session folders already searched; `truncated` when the budget or the cap cut it.

The home is ORBIT_FIND_HOME when set (tests: never the real home; Spotlight is not used then), else ~.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from typing import Any

from orbit.workspace import files

HOME_ENV = "ORBIT_FIND_HOME"
LIMIT = 200
WALK_BUDGET_S = 1.5
WALK_FILES = 400_000  # files looked at in the home, at most
MATCH_CAP = 5000  # matches kept before ranking (more: truncated)
TREE_TTL_S = 8.0
HOME_SKIP = {"Library", "Applications", "Pictures", "Music", "Movies"}  # the home's own (top level only)
_TREES: dict[str, tuple[float, dict]] = {}
_LOCK = threading.Lock()


def home_root() -> str:
    return os.environ.get(HOME_ENV) or os.path.expanduser("~")


def _terms(q: str) -> list[str]:
    return [t for t in q.lower().split() if t]


def _hit(rel: str, terms: list[str]) -> bool:
    low = rel.lower()
    return all(t in low for t in terms)


def _tree(root: str) -> dict | None:
    now = time.time()
    with _LOCK:
        c = _TREES.get(root)
        if c and now - c[0] < TREE_TTL_S:
            return c[1]
    try:
        t = files.tree(root)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError):
        return None
    with _LOCK:
        _TREES[root] = (now, t)
    return t


def outermost(roots: list[str]) -> list[str]:
    """The folders without those inside another of them (one listing covers both)."""
    out: list[str] = []
    for r in sorted({os.path.realpath(x) for x in roots if x}, key=len):
        if not any(r == o or r.startswith(o.rstrip("/") + "/") for o in out):
            out.append(r)
    return out


def _skip_part(name: str) -> bool:
    return name.startswith(".") or name in files.SKIP_DIRS


def _spotlight(home: str, terms: list[str]) -> list[str] | None:
    """Spotlight's files named with the longest term (None: it failed, walk instead)."""
    try:
        r = subprocess.run(["mdfind", "-onlyin", home, "-name", max(terms, key=len)], capture_output=True, text=True,
                           timeout=2.5)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return [p for p in r.stdout.split("\n") if p]


def _home_hits(home: str, terms: list[str], skip_roots: list[str]) -> tuple[list[str], bool]:
    """Files under home whose path below it has every term; (paths, truncated)."""
    skip = {os.path.realpath(r) for r in skip_roots}
    found: list[str] = []
    if sys.platform == "darwin" and not os.environ.get(HOME_ENV):
        got = _spotlight(home, terms)
        if got is not None:
            for p in got:
                rel = os.path.relpath(p, home)
                parts = rel.split(os.sep)
                if rel.startswith("..") or parts[0] in HOME_SKIP or any(_skip_part(x) for x in parts[:-1]):
                    continue
                if any(p == r or p.startswith(r + "/") for r in skip) or not _hit(rel, terms) or not os.path.isfile(p):
                    continue
                found.append(p)
                if len(found) >= MATCH_CAP:
                    return found, True
            return found, False
    t0, seen, truncated = time.time(), 0, False
    for dirpath, dirnames, filenames in os.walk(home):
        top = dirpath == home
        dirnames[:] = [d for d in dirnames if not _skip_part(d) and not (top and d in HOME_SKIP)
                       and os.path.realpath(os.path.join(dirpath, d)) not in skip]
        rel_dir = os.path.relpath(dirpath, home)
        for f in filenames:
            seen += 1
            if f == ".DS_Store":
                continue
            rel = f if rel_dir == "." else os.path.join(rel_dir, f)
            if _hit(rel, terms):
                found.append(os.path.join(dirpath, f))
                if len(found) >= MATCH_CAP:
                    return found, True
        if seen >= WALK_FILES or time.time() - t0 > WALK_BUDGET_S:
            truncated = True
            break
    return found, truncated


def find(q: str, roots: list[str], limit: int = LIMIT, home: bool = True) -> dict[str, Any]:
    """{files: [{path, root, rel, where: "session" | "home", time}], truncated}: best first, at most `limit`."""
    terms = _terms(q)
    if not terms:
        return {"files": [], "truncated": False}
    hits: dict[str, dict] = {}
    truncated = False
    roots = outermost(roots)
    for root in roots:
        t = _tree(root)
        if not t:
            continue
        truncated = truncated or bool(t.get("truncated"))
        base = t.get("root") or root
        named = os.path.basename(base.rstrip("/")) + "/"  # (the folder's own name counts: "orbit readme" finds orbit/README.md)
        for rel in t.get("files") or []:
            if _hit(named + rel, terms):
                p = os.path.join(base, rel)
                hits.setdefault(p, {"path": p, "root": base, "rel": rel, "where": "session"})
                if len(hits) >= MATCH_CAP:
                    truncated = True
                    break
    if home:
        h = home_root()
        if os.path.isdir(h):
            paths, cut = _home_hits(h, terms, roots)
            truncated = truncated or cut
            for p in paths:
                hits.setdefault(p, {"path": p, "root": h, "rel": os.path.relpath(p, h), "where": "home"})
    out = sorted(hits.values(), key=lambda x: _key(x, terms))[:limit * 3]  # (times only for the best few)
    for x in out:
        try:
            x["time"] = os.stat(x["path"]).st_mtime
        except OSError:
            x["time"] = 0
    out = rank(out, q)
    return {"files": out[:limit], "truncated": truncated or len(out) > limit}


def _key(x: dict, terms: list[str]) -> tuple:
    name = os.path.basename(x.get("rel") or x.get("path") or "").lower()
    return (-sum(1 for t in terms if t in name), len(x.get("rel") or ""), -(x.get("time") or 0))


def rank(xs: list[dict], q: str) -> list[dict]:
    """More terms in the file's own name first, then the shorter path below its root, then the newest."""
    terms = _terms(q)
    return sorted(xs, key=lambda x: _key(x, terms))
