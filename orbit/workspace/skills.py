"""Skills and commands an agent in a folder can run, for `/` in the input (editor.md §5).

Claude Code: <folder>/.claude/skills/*/SKILL.md and ~/.claude/skills/*/SKILL.md (name and description from the
front matter), <folder>/.claude/commands/*.md and ~/.claude/commands/*.md. Codex: ~/.codex/prompts/*.md.
Orbit's own skills ship in <repo>/plugin/skills (a Claude Code plugin, `--plugin-dir`) and are offered to every harness.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from orbit import ROOT


ORBIT_SKILLS = ROOT / "plugin" / "skills"


def _front(text: str) -> dict[str, str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*(\n|$)", text, re.S)
    out: dict[str, str] = {}
    if not m:
        return out
    key = None
    for line in m.group(1).splitlines():
        kv = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if kv:
            key = kv.group(1).lower()
            v = kv.group(2).strip()
            out[key] = "" if v in (">", ">-", ">+", "|", "|-", "|+") else v.strip("\"'")  # a folded / literal block
        elif key and line.startswith((" ", "\t")):  # a folded value continues
            out[key] = (out[key] + " " + line.strip()).strip()
    return out


def _first_line(text: str) -> str:
    body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, flags=re.S)
    for line in body.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line
    return ""


def _skill_dirs(base: Path, source: str) -> list[dict[str, Any]]:
    out = []
    if not base.is_dir():
        return out
    for d in sorted(base.iterdir()):
        f = d / "SKILL.md"
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")[:8000]
        except OSError:
            continue
        fm = _front(text)
        out.append({"name": fm.get("name") or d.name, "description": fm.get("description") or _first_line(text),
                    "kind": "skill", "source": source, "path": str(f)})
    return out


def _commands(base: Path, source: str) -> list[dict[str, Any]]:
    out = []
    if not base.is_dir():
        return out
    for f in sorted(base.rglob("*.md")):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            continue
        name = ":".join(f.relative_to(base).with_suffix("").parts)
        fm = _front(text)
        out.append({"name": name, "description": fm.get("description") or _first_line(text), "kind": "command",
                    "source": source, "path": str(f)})
    return out


def list_skills(cwd: str, harness: str = "claude") -> list[dict[str, Any]]:
    folder = Path(os.path.expanduser(cwd or "~"))
    found: list[dict[str, Any]] = []
    from orbit.desk.session import DRIVERS
    cls = DRIVERS.get(harness)
    for d, kind, source in (cls.skill_dirs(folder) if cls else []):  # the harness's own (in the order it reads them)
        found += _skill_dirs(d, source) if kind == "skills" else _commands(d, source)
    found += _skill_dirs(ORBIT_SKILLS, "orbit")
    seen, out = set(), []
    for s in found:  # the folder's own wins over the user's of the same name
        if s["name"] not in seen:
            seen.add(s["name"])
            out.append(s)
    return out
