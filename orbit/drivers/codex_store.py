"""Locate Codex rollouts by their session metadata; filenames are only a search hint."""

from __future__ import annotations

import glob
import json
import os
import time
from pathlib import Path


def sessions_root() -> str:
    if os.environ.get("ORBIT_CODEX_SESSIONS"):
        return os.environ["ORBIT_CODEX_SESSIONS"]
    return str(Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "sessions")


def rollout_files(root: str | None = None, thread: str = "") -> list[str]:
    pattern = f"rollout-*{glob.escape(thread)}*.jsonl" if thread else "rollout-*.jsonl"
    paths = glob.glob(os.path.join(glob.escape(root or sessions_root()), "**", pattern), recursive=True)
    dated = []
    for path in paths:
        try:
            dated.append((os.path.getmtime(path), path))
        except OSError:  # a rollout may be moved or compressed during discovery
            continue
    return [path for _, path in sorted(dated, reverse=True)]


def session_meta(path: str) -> dict:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for _, line in zip(range(10), f):
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if isinstance(data, dict) and data.get("type") == "session_meta":
                    meta = data.get("payload")
                    return meta if isinstance(meta, dict) else {}
    except OSError:
        pass
    return {}


_MISSED: dict[tuple[str, str], float] = {}  # (root, thread) → when a full scan found nothing
MISS_TTL = 30.0  # a full scan reads every rollout's head (~1 s for 2000): not again for a thread that has none yet


def find_rollout(thread: str, root: str | None = None) -> str | None:
    if not thread:
        return None
    candidates = rollout_files(root, thread)
    for path in candidates:
        if session_meta(path).get("id") == thread:
            return path
    key = (root or sessions_root(), thread)
    if time.time() - _MISSED.get(key, 0.0) < MISS_TTL:
        return None
    # Discovery can find renamed files too. Use the same metadata when the filename gives no useful hint.
    checked = set(candidates)
    for path in rollout_files(root):
        if path not in checked and session_meta(path).get("id") == thread:
            _MISSED.pop(key, None)
            return path
    _MISSED[key] = time.time()
    return None
