"""The notes vault: an Obsidian-compatible folder of Markdown files.

The vault is <data root>/notes (`--data-root` / $ORBIT_DATA_ROOT, default ~/.orbit/data), or $ORBIT_VAULT directly.
Links are Obsidian wiki links: [[Name]], [[Name|alias]], [[Name#Heading]], [[folder/Name]]. A bare name resolves like
Obsidian: the note in the linking note's folder first, else the shortest path with that file name.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from orbit.workspace.files import save as save_file


SKIP = {".obsidian", ".trash", ".git", "node_modules"}
WIKI = re.compile(r"\[\[([^\]\n|#]+)(?:#[^\]\n|]*)?(?:\|[^\]\n]*)?\]\]")
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

_root: Path | None = None
_data: Path | None = None


def configure(data_root: str | None) -> Path:
    """Set the vault folder (created if missing) and return it."""
    global _root, _data
    from orbit.home import home
    base = data_root or os.environ.get("ORBIT_DATA_ROOT") or str(home() / "data")
    _data = Path(base).expanduser()
    vault = os.environ.get("ORBIT_VAULT")
    if vault:
        _root = Path(vault).expanduser()
    else:
        _root = _data / "notes"
    _root.mkdir(parents=True, exist_ok=True)
    _root = Path(os.path.realpath(_root))
    return _root


def root() -> Path:
    return _root or configure(None)


def data_root() -> Path:
    """The personal data folder (notes, schedules)."""
    if _data is None:
        configure(None)
    return _data


def _inside(p: Path) -> Path:
    p = Path(os.path.realpath(p))
    r = root()
    if p != r and r not in p.parents:
        raise ValueError(f"{p} is outside the vault")
    return p


def all_notes() -> list[str]:
    """Vault-relative paths of every Markdown note, sorted."""
    r = root()
    out = []
    for dirpath, dirnames, filenames in os.walk(r):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP and not d.startswith("."))
        for f in filenames:
            if f.lower().endswith(".md"):
                out.append(os.path.relpath(os.path.join(dirpath, f), r))
    return sorted(out, key=str.lower)


def stem(rel: str) -> str:
    return os.path.splitext(os.path.basename(rel))[0]


def resolve(name: str, from_path: str | None = None, notes: list[str] | None = None) -> str | None:
    """Absolute path of the note a wiki link names, or None."""
    name = name.strip()
    if not name:
        return None
    notes = notes if notes is not None else all_notes()
    target = name[:-3] if name.lower().endswith(".md") else name
    if "/" in target:
        want = (target + ".md").lower()
        hit = next((n for n in notes if n.lower() == want), None)
        return str(root() / hit) if hit else None
    matches = [n for n in notes if stem(n).lower() == target.lower()]
    if not matches:
        return None
    if from_path:
        here = os.path.relpath(os.path.dirname(os.path.realpath(from_path)), root())
        same = [n for n in matches if os.path.dirname(n) == ("" if here == "." else here)]
        if same:
            return str(root() / same[0])
    return str(root() / min(matches, key=lambda n: (n.count("/"), len(n), n.lower())))


def create(name: str, text: str | None = None) -> dict[str, Any]:
    """Create a note (name may hold folders); returns its path. An existing note is returned as is."""
    name = name.strip().strip("/")
    if not name or name.startswith("."):
        raise ValueError("a note needs a name")
    rel = name if name.lower().endswith(".md") else name + ".md"
    p = _inside(root() / rel)
    if p.exists():
        return {"path": str(p), "created": False}
    p.parent.mkdir(parents=True, exist_ok=True)
    save_file(str(p), text if text is not None else f"# {stem(rel)}\n\n", None)
    return {"path": str(p), "created": True}


def backlinks(path: str) -> list[dict[str, Any]]:
    """Notes that link to this one: [{path, line, text}], one entry per linking line."""
    target = os.path.realpath(path)
    notes = all_notes()
    out = []
    for rel in notes:
        p = root() / rel
        if os.path.realpath(p) == target:
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError:
            continue
        for i, line in enumerate(lines):
            for m in WIKI.finditer(line):
                hit = resolve(m.group(1), str(p), notes)
                if hit and os.path.realpath(hit) == target:
                    out.append({"path": str(p), "line": i + 1, "text": line.strip()[:300]})
                    break
    return out


# ------------------------------------------------------------------ search
def snippet(text: str, term: str, before: int = 50, after: int = 110) -> str:
    """A one-line excerpt of text around the first occurrence of term."""
    flat = " ".join((text or "").split())
    i = flat.lower().find(term.lower())
    if i < 0:
        return flat[:before + after]
    start, end = max(0, i - before), min(len(flat), i + len(term) + after)
    return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")


def search(terms: list[str], limit: int = 50) -> list[dict[str, Any]]:
    """Notes holding every term (in the title or the text), newest first, with the first matching line."""
    out = []
    for rel in all_notes():
        p = root() / rel
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            mtime = p.stat().st_mtime
        except OSError:
            continue
        low = (stem(rel) + "\n" + text).lower()
        if not all(t in low for t in terms):
            continue
        lines = text.split("\n")
        # the first line holding every term, else the first holding the first term
        hit = next((i for i, l in enumerate(lines) if all(t in l.lower() for t in terms)), None)
        if hit is None:
            hit = next((i for i, l in enumerate(lines) if terms[0] in l.lower()), None)
        line = lines[hit] if hit is not None else ""
        out.append({"kind": "note", "path": str(p), "title": stem(rel), "line": (hit or 0) + 1, "line_text": line,
                    "snippet": snippet(line or text, terms[0]), "time": mtime})
    out.sort(key=lambda r: -r["time"])
    return out[:limit]


# ------------------------------------------------------------------ the week note
def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


GUIDE = "Organization Guide.md"
GUIDE_SECTIONS = ["Focus", "Daily Work", "Projects in Flight", "Ideas", "Carry-overs", "Reflections"]


def _guide() -> str | None:
    """The vault's Organization Guide (builddoc/features/files.md §0): when it exists, week notes follow its layout."""
    p = root() / GUIDE
    try:
        return p.read_text(encoding="utf-8") if p.is_file() else None
    except OSError:
        return None


def day_heading(d: date, guided: bool | None = None) -> str:
    guided = _guide() is not None if guided is None else guided
    return f"### {DAYS[d.weekday()]} {d:%m/%d}" if guided else f"## {DAYS[d.weekday()]} {d:%m-%d}"


def week_path(d: date) -> Path:
    if _guide() is not None:
        m = monday(d)
        return root() / "Journal" / f"{m:%Y}" / f"Week of {m:%Y%m%d}.md"
    return root() / "Weekly" / f"Week of {monday(d):%Y-%m-%d}.md"


def _recent_week(d: date) -> Path | None:
    """A week note dated within the last 6 days (Journal/<YYYY>/Week of <YYYYMMDD>.md), reused even when it was not
    started on a Monday."""
    best = None
    for y in {d.year, d.year - 1}:
        folder = root() / "Journal" / str(y)
        if not folder.is_dir():
            continue
        for f in folder.glob("Week of *.md"):
            m = re.fullmatch(r"Week of (\d{8})\.md", f.name)
            if not m:
                continue
            try:
                when = datetime.strptime(m.group(1), "%Y%m%d").date()
            except ValueError:
                continue
            if 0 <= (d - when).days <= 6 and (best is None or when > best[0]):
                best = (when, f)
    return best[1] if best else None


def _guide_template(guide: str, m: date) -> list[str]:
    """The weekly template from the guide: the first fenced block under a heading that names a week template; else
    the standard sections. The day headings (### Mon MM/DD …) go under Daily Work."""
    body: list[str] | None = None
    blocks = re.finditer(r"^#{1,6}[^\n]*week[^\n]*\n(?:(?!^#{1,6} ).*\n)*?```[^\n]*\n(.*?)\n```", guide, re.M | re.I | re.S)
    for mt in blocks:
        body = mt.group(1).split("\n")
        break
    if body is None:
        body = [f"# Week of {m:%Y%m%d}", ""]
        for sec in GUIDE_SECTIONS:
            body += [f"## {sec}", ""]
    subs = {"{{date}}": f"{m:%Y-%m-%d}", "{{monday}}": f"{m:%Y-%m-%d}", "YYYYMMDD": f"{m:%Y%m%d}", "YYYY-MM-DD": f"{m:%Y-%m-%d}"}
    body = [_sub(line, subs) for line in body]
    days = [day_heading(m + timedelta(days=i), True) for i in range(5)]  # Mon–Fri
    if not any(line.startswith("### ") for line in body):
        at = next((i for i, line in enumerate(body) if re.match(r"^##\s+Daily Work", line, re.I)), None)
        block = sum(([h, ""] for h in days), [])
        if at is None:
            body += ["## Daily Work", ""] + block
        else:
            j = at + 1
            while j < len(body) and body[j].strip() == "":
                j += 1
            body[at + 1:j] = [""] + block
    return body


def _sub(line: str, subs: dict[str, str]) -> str:
    for k, v in subs.items():
        line = line.replace(k, v)
    return line


# ------------------------------------------------------------------ the journal: Obsidian's daily note, else a week note
MOMENT = re.compile(r"\[([^\]]*)\]|YYYY|YY|MMMM|MMM|MM|M|DDDD|DDD|DD|Do|D|dddd|ddd|dd|d|GGGG|gggg|WW|W|ww|w|HH|H|mm|ss")


def moment(fmt: str, d: date | datetime) -> str:
    """A date in a moment.js format, as Obsidian writes them (YYYY-MM-DD, YYYYMM/YYYY-MM-DD, [Week] ww, …)."""
    dt = d if isinstance(d, datetime) else datetime(d.year, d.month, d.day)
    iso = dt.isocalendar()
    def one(m: re.Match) -> str:
        t = m.group(0)
        if m.group(1) is not None:
            return m.group(1)
        return {"YYYY": f"{dt:%Y}", "YY": f"{dt:%y}", "MMMM": f"{dt:%B}", "MMM": f"{dt:%b}", "MM": f"{dt:%m}",
                "M": str(dt.month), "DDDD": f"{dt:%j}", "DDD": str(int(f"{dt:%j}")), "DD": f"{dt:%d}", "D": str(dt.day),
                "Do": f"{dt.day}{'th' if 10 <= dt.day % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(dt.day % 10, 'th')}",
                "dddd": f"{dt:%A}", "ddd": f"{dt:%a}", "dd": f"{dt:%a}"[:2], "d": str(dt.isoweekday() % 7),
                "GGGG": str(iso[0]), "gggg": str(iso[0]), "WW": f"{iso[1]:02d}", "W": str(iso[1]), "ww": f"{iso[1]:02d}",
                "w": str(iso[1]), "HH": f"{dt:%H}", "H": str(dt.hour), "mm": f"{dt:%M}", "ss": f"{dt:%S}"}[t]
    return MOMENT.sub(one, fmt)


def daily_config() -> dict[str, str] | None:
    """Obsidian's Daily notes settings (<vault>/.obsidian/daily-notes.json: folder, format, template), when the vault
    has them: the journal is then Obsidian's own daily note, the same file Obsidian opens."""
    try:
        d = json.loads((root() / ".obsidian" / "daily-notes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    return {"folder": str(d.get("folder") or "").strip().strip("/"), "format": str(d.get("format") or "YYYY-MM-DD").strip(),
            "template": str(d.get("template") or "").strip()}


def _template_text(rel: str, title: str, now: datetime) -> str:
    """An Obsidian template ({{title}}, {{date}}, {{time}}, {{date:FORMAT}}, {{time:FORMAT}}), "" when missing."""
    if not rel:
        return ""
    p = root() / (rel if rel.lower().endswith(".md") else rel + ".md")
    try:
        text = _inside(p).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""
    try:
        tcfg = json.loads((root() / ".obsidian" / "templates.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        tcfg = {}
    dfmt, tfmt = tcfg.get("dateFormat") or "YYYY-MM-DD", tcfg.get("timeFormat") or "HH:mm"
    text = re.sub(r"\{\{\s*(date|time)\s*:\s*([^}]+?)\s*\}\}", lambda m: moment(m.group(2), now), text)
    return text.replace("{{title}}", title).replace("{{date}}", moment(dfmt, now)).replace("{{time}}", moment(tfmt, now))


def daily_note(d: date | None = None) -> dict[str, Any] | None:
    """This week's note where Obsidian keeps its daily notes (its folder and date format, e.g. Journal/YYYYMM/
    YYYY-MM-DD.md), dated by the week's Monday (weeks start on Monday: a Sunday belongs to the week before): one note
    a week, a heading per day (## Mon 11-16 … ## Sun 11-22), from Obsidian's template when it has one. None without
    Obsidian's settings."""
    cfg = daily_config()
    if cfg is None:
        return None
    d = d or date.today()
    m = monday(d)
    rel = moment(cfg["format"], m).strip("/")
    p = _inside(root() / cfg["folder"] / (rel + ".md"))
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        head = _template_text(cfg["template"], stem(rel + ".md"), datetime.combine(m, datetime.now().time())).rstrip("\n")
        body = (head.split("\n") if head else [f"# Week of {m:%Y-%m-%d}"]) + [""]
        for k in range(7):
            body += [day_heading(m + timedelta(days=k), False), ""]
        save_file(str(p), "\n".join(body).rstrip("\n") + "\n", None)
    return {"path": str(p), "heading": day_heading(d, False), "kind": "week", "label": "This week"}


def journal_kind() -> str:
    """What ⌘⇧D opens and `orbit-agent note` writes to: always this week's note (Monday to Sunday)."""
    return "week"


def week_note(d: date | None = None) -> dict[str, Any]:
    """The journal: this week's note (Monday to Sunday; created from the template if missing) and today's heading.
    Where Obsidian keeps its daily notes when the vault sets that up (daily_note), else per the guide or Weekly/."""
    day = daily_note(d)
    if day is not None:
        return day
    d = d or date.today()
    guide = _guide()
    p = (_recent_week(d) if guide is not None else None) or week_path(d)
    if not p.exists():
        m = monday(d)
        if guide is not None:
            body = _guide_template(guide, m)
        else:
            body = [f"# Week of {m:%Y-%m-%d}", ""]
            for i in range(7):
                body += [day_heading(m + timedelta(days=i), False), ""]
        p.parent.mkdir(parents=True, exist_ok=True)
        save_file(str(p), "\n".join(body).rstrip("\n") + "\n", None)
    return {"path": str(p), "heading": day_heading(d, guide is not None), "kind": "week", "label": "This week"}


def _level(line: str) -> int:
    m = re.match(r"^(#{1,6})\s", line)
    return len(m.group(1)) if m else 0


def append_today(text: str, source: str = "", d: date | None = None) -> dict[str, Any]:
    """Add text under today's heading of the week note (a missing heading is added: under Daily Work in a guided
    note, at the end otherwise). Never a rewrite: what is there stays, the entry goes after it."""
    text = text.strip("\n")
    if not text.strip():
        raise ValueError("nothing to add")
    d = d or date.today()
    info = week_note(d)
    p = Path(info["path"])
    entry = text.split("\n") + ([f"— {source} · {datetime.now():%H:%M}"] if source else [])
    lines = p.read_text(encoding="utf-8").split("\n")
    head = info["heading"]
    lv = _level(head)
    if head not in lines:
        daily = next((i for i, line in enumerate(lines) if lv == 3 and re.match(r"^##\s+Daily Work", line, re.I)), None)
        if daily is not None:  # at the end of Daily Work, before the next ## section
            j = daily + 1
            while j < len(lines) and not (0 < _level(lines[j]) <= 2):
                j += 1
            while j > daily + 1 and lines[j - 1] == "":
                j -= 1
            lines[j:j] = ["", head, ""]
        else:
            while lines and lines[-1] == "":
                lines.pop()
            lines += ["", head, ""]
    i = lines.index(head) + 1
    j = i
    while j < len(lines) and not (0 < _level(lines[j]) <= lv):
        j += 1
    section = lines[i:j]
    while section and section[0] == "":
        section.pop(0)
    while section and section[-1] == "":
        section.pop()
    # heading, blank, what was there, blank, the new entry, blank, next heading
    lines[i:j] = [""] + (section + [""] if section else []) + entry + [""]
    save_file(str(p), "\n".join(lines).rstrip("\n") + "\n", None)
    return info


# ------------------------------------------------------------------ attachments, embeds, link updates
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".avif", ".heic"}
ANY_WIKI = re.compile(r"(!?)\[\[([^\]\n]+?)\]\]")


def in_vault(path: str | Path) -> bool:
    p = Path(os.path.realpath(path))
    return p == root() or root() in p.parents


def all_files() -> list[str]:
    """Vault-relative paths of every file (notes and attachments), sorted."""
    r = root()
    out = []
    for dirpath, dirnames, filenames in os.walk(r):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP and not d.startswith("."))
        out += [os.path.relpath(os.path.join(dirpath, f), r) for f in filenames if not f.startswith(".")]
    return sorted(out, key=str.lower)


def resolve_file(name: str, from_path: str | None = None, files: list[str] | None = None) -> str | None:
    """Absolute path of the file an embed or link names ("img.png", "folder/img.png", "Note"), Obsidian's way: the
    linking note's folder first, else the shortest path; a name without an extension is a note. Outside the vault,
    a name is relative to the linking file's folder."""
    name = name.strip().replace("\\|", "|").split("|")[0].split("#")[0].strip()
    if not name:
        return None
    if from_path and not in_vault(from_path):
        p = Path(os.path.dirname(os.path.realpath(from_path))) / name
        return str(p) if p.is_file() else None
    if not os.path.splitext(name)[1]:
        return resolve(name, from_path)
    files = files if files is not None else all_files()
    if "/" in name:
        hit = next((f for f in files if f.lower() == name.lower()), None)
        if hit:
            return str(root() / hit)
        name = name.rsplit("/", 1)[1]  # a moved target: found by its file name
    matches = [f for f in files if os.path.basename(f).lower() == name.lower()]
    if not matches:
        return None
    if from_path:
        here = os.path.relpath(os.path.dirname(os.path.realpath(from_path)), root())
        same = [f for f in matches if os.path.dirname(f) == ("" if here == "." else here)]
        if same:
            return str(root() / same[0])
    return str(root() / min(matches, key=lambda f: (f.count("/"), len(f), f.lower())))


def _unique(folder: Path, name: str) -> Path:
    stem, ext = os.path.splitext(name)
    p, n = folder / name, 1
    while p.exists():
        p = folder / f"{stem} {n}{ext}"
        n += 1
    return p


def attach(doc: str, name: str, data: bytes | None = None, src: str | None = None) -> dict[str, Any]:
    """Copy a dropped or pasted file next to a Markdown document and return the text that links it: in the vault
    into attachments/ as ![[name]] (an image) or [[name]]; elsewhere into assets/ beside the document as ![](…) / […](…)."""
    docp = Path(os.path.realpath(doc))
    if src:
        s = Path(os.path.realpath(os.path.expanduser(src)))
        if not s.is_file():
            raise ValueError(f"{src} is not a file")
        data = s.read_bytes()
        name = name or s.name
    if not data:
        raise ValueError("empty file")
    name = re.sub(r'[\\/:*?"<>|#^\[\]]+', " ", os.path.basename(name or "")).strip() or "file"
    if name.lower() in ("image.png", "pasted.png", "pasted"):  # a clipboard image has no name of its own
        name = f"Pasted image {datetime.now():%Y%m%d%H%M%S}.png"
    vault = in_vault(docp)
    folder = root() / "attachments" if vault else docp.parent / "assets"
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    if not (p.exists() and p.read_bytes() == data):  # the same file dropped again is reused
        p = _unique(folder, name)
        p.write_bytes(data)
    image = p.suffix.lower() in IMAGE_EXT
    if vault:
        link = f"![[{p.name}]]" if image else f"[[{p.name}]]"
    else:
        rel = os.path.relpath(p, docp.parent)
        target = f"<{rel}>" if re.search(r"[\s()]", rel) else rel
        link = f"![{p.stem}]({target})" if image else f"[{p.name}]({target})"
    return {"path": str(p), "link": link, "image": image}


def update_links(old: str, new: str) -> int:
    """After a vault file was renamed or moved (old → new, absolute), rewrite every wiki link that pointed at it.
    Returns how many notes changed. A link keeps its shape: a bare name stays bare, a path stays a path."""
    oldp, newp = Path(os.path.realpath(old)), Path(os.path.realpath(new))
    if not (in_vault(oldp) and in_vault(newp)):
        return 0
    old_rel, new_rel = os.path.relpath(oldp, root()), os.path.relpath(newp, root())
    is_note = oldp.suffix.lower() == ".md"
    before = [f for f in all_files() if f != new_rel] + [old_rel]  # the vault as the links saw it
    notes_before = [f for f in before if f.lower().endswith(".md")]
    changed = 0
    for rel in all_notes():
        path = root() / rel
        linking = str(root() / (old_rel if rel == new_rel else rel))  # the moved note resolves from where it was
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        def fix(m: re.Match) -> str:
            inner = m.group(2)
            esc = "\\|" in inner
            body = inner.replace("\\|", "|")
            target, sep, rest = body, "", ""
            for i, ch in enumerate(body):
                if ch in "|#":
                    target, sep, rest = body[:i], ch, body[i + 1:]
                    break
            t = target.strip()
            if not t:
                return m.group(0)
            if is_note and not os.path.splitext(t)[1] or t.lower().endswith(".md"):
                hit = resolve(t, linking, notes_before)
            else:
                hit = resolve_file(t, linking, before)
            if not hit or os.path.realpath(hit) != str(oldp):
                return m.group(0)
            if "/" in t:
                nt = new_rel[:-3] if is_note and not t.lower().endswith(".md") else new_rel
            else:
                nt = newp.stem if is_note and not t.lower().endswith(".md") else newp.name
            out = nt + (sep + rest if sep else "")
            return f"{m.group(1)}[[{out.replace('|', chr(92) + '|') if esc else out}]]"

        new_text = ANY_WIKI.sub(fix, text)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            changed += 1
    return changed


def trash(path: str) -> str:
    """Delete in the vault = move to <vault>/.trash (recoverable, Obsidian's place), never unlink."""
    p = _inside(Path(path))
    if p == root():
        raise ValueError("the vault itself cannot be deleted")
    if not p.exists():
        raise FileNotFoundError(str(p))
    folder = root() / ".trash"
    folder.mkdir(exist_ok=True)
    dest = _unique(folder, p.name)
    os.rename(p, dest)
    return str(dest)
