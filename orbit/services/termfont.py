"""The terminal font of iTerm's default profile, so the ⌘J terminal can look like the terminal you use.

GET /api/term-font answers {"source": "iTerm", "profile", "font" (its PostScript name, e.g. NotoSansMono-Regular),
"size" (points: CSS px), "line" (vertical spacing, 1 = none), "letter" (horizontal spacing), "thin" (iTerm's thin
strokes: 0 never · 1 Retina, dark backgrounds · 2 dark backgrounds · 3 always · 4 Retina)}, or {} without iTerm. The
page loads that font from GET /api/term-font/file/{regular,bold}: the file found in the font folders by its PostScript
name (WebKit, the app's engine, keeps fonts the user installed from pages, so CSS local() cannot reach them). "weight"
is the instance's weight (iTerm names a variable font's instances NAME_Medium …), "variable" whether one file has every
weight. Read again when iTerm's preferences change.
"""

from __future__ import annotations

import os
import plistlib
import re
import struct
import subprocess
import sys
from pathlib import Path

PREFS = Path.home() / "Library" / "Preferences" / "com.googlecode.iterm2.plist"
NAME = re.compile(r"^[\w .-]{1,100}$")
_cache: dict = {"mtime": None, "font": {}}
FONT_DIRS = [Path.home() / "Library" / "Fonts", Path("/Library/Fonts"), Path("/System/Library/Fonts"),
             Path("/System/Library/Fonts/Supplemental")]
FONT_EXT = (".ttf", ".otf", ".ttc")
WEIGHTS = {"thin": 100, "extralight": 200, "ultralight": 200, "light": 300, "regular": 400, "book": 400, "normal": 400,
           "medium": 500, "semibold": 600, "demibold": 600, "bold": 700, "extrabold": 800, "ultrabold": 800,
           "black": 900, "heavy": 900}


def _names(data: bytes, offset: int = 0) -> tuple[set[str], bool]:
    """The PostScript names (name ID 6) of the font at offset in an sfnt file, and whether it is variable (fvar)."""
    out: set[str] = set()
    _, ntables = struct.unpack_from(">IH", data, offset)
    tables = {}
    for i in range(ntables):
        tag, _, off, length = struct.unpack_from(">4sIII", data, offset + 12 + 16 * i)
        tables[tag] = (off, length)
    if b"name" in tables:
        off, _ = tables[b"name"]
        _, count, strings = struct.unpack_from(">HHH", data, off)
        for i in range(count):
            plat, enc, lang, nid, length, soff = struct.unpack_from(">HHHHHH", data, off + 6 + 12 * i)
            if nid != 6:
                continue
            raw = data[off + strings + soff: off + strings + soff + length]
            out.add(raw.decode("utf-16-be", "ignore") if plat in (0, 3) else raw.decode("latin-1", "ignore"))
    return out, b"fvar" in tables


def font_names(path: Path) -> tuple[set[str], bool]:
    """Every PostScript name in a font file (each font of a .ttc), and whether it is variable; (set(), False) if not
    one."""
    try:
        with open(path, "rb") as f:
            data = f.read(4 * 1024 * 1024)  # (the tables we read are near the start)
        if data[:4] == b"ttcf":
            n = struct.unpack_from(">I", data, 8)[0]
            names, var = set(), False
            for i in range(min(n, 64)):
                ns, v = _names(data, struct.unpack_from(">I", data, 12 + 4 * i)[0])
                names |= ns
                var = var or v
            return names, var
        return _names(data)
    except (OSError, struct.error, IndexError):
        return set(), False


def find_font(ps: str) -> tuple[Path | None, bool]:
    """The file holding PostScript name ps (a likely file name first, then every font file), and whether it is
    variable."""
    stem = re.sub(r"[^a-z0-9]", "", ps.split("-")[0].lower())
    files = [f for d in FONT_DIRS if d.is_dir() for f in sorted(d.iterdir()) if f.suffix.lower() in FONT_EXT]
    likely = [f for f in files if re.sub(r"[^a-z0-9]", "", f.name.lower()).startswith(stem)]
    for f in likely + [f for f in files if f not in likely and f.parent == FONT_DIRS[0]]:
        names, var = font_names(f)
        if ps in names:
            return f, var
    return None, False


def font_files(ps: str) -> dict:
    """{"regular": Path, "bold"?: Path, "variable": bool, "weight": int} for iTerm's font name (NAME or a variable
    font's NAME_Weight instance), or {} if no file has it."""
    base, _, inst = ps.partition("_")
    regular, var = find_font(ps)
    if regular is None and inst:
        regular, var = find_font(base)
    if regular is None:
        return {}
    out = {"regular": regular, "variable": var,
           "weight": WEIGHTS.get(inst.lower(), WEIGHTS.get(base.rsplit("-", 1)[-1].lower(), 400))}
    if not var:
        bold, _ = find_font(base.rsplit("-", 1)[0] + "-Bold")
        if bold:
            out["bold"] = bold
    return out


def font_file(kind: str) -> Path | None:
    """The served file of the current iTerm font: kind "regular" or "bold"."""
    f = iterm_font()
    return f.get("_files", {}).get(kind) if f else None


def public(f: dict) -> dict:
    return {k: v for k, v in f.items() if not k.startswith("_")}


def parse(d: dict) -> dict:
    """The default profile's font from iTerm's preferences (as `defaults export` gives them)."""
    profiles = [b for b in d.get("New Bookmarks") or [] if isinstance(b, dict)]
    if not profiles:
        return {}
    guid = d.get("Default Bookmark Guid")
    prof = next((b for b in profiles if b.get("Guid") == guid), profiles[0])
    m = re.fullmatch(r"\s*(\S.*?)\s+(\d+(?:\.\d+)?)\s*", str(prof.get("Normal Font") or ""))
    if not m or not NAME.match(m.group(1)):
        return {}
    num = lambda k, lo, hi: min(hi, max(lo, float(prof.get(k) or 1)))
    thin = prof.get("Thin Strokes", d.get("ThinStrokes", 1))
    return {"source": "iTerm", "profile": str(prof.get("Name") or "")[:80], "font": m.group(1),
            "size": min(40.0, max(6.0, float(m.group(2)))), "line": num("Vertical Spacing", 0.8, 2.0),
            "letter": num("Horizontal Spacing", 0.8, 2.0), "thin": int(thin) if str(thin).isdigit() else 1}


def iterm_font() -> dict:
    if sys.platform != "darwin":
        return {}
    try:
        mtime = PREFS.stat().st_mtime
    except OSError:
        return {}
    if _cache["mtime"] == mtime:
        return _cache["font"]
    try:
        out = subprocess.run(["defaults", "export", "com.googlecode.iterm2", "-"], capture_output=True, timeout=5).stdout
        font = parse(plistlib.loads(out)) if out else {}
    except (OSError, subprocess.TimeoutExpired, plistlib.InvalidFileException, ValueError, TypeError):
        font = {}
    if font:
        files = font_files(font["font"])
        if files.get("regular") and files["regular"].suffix.lower() == ".ttc":
            files = {}  # (a collection: pages cannot load one; a system font, which CSS local() reaches)
        if files.get("bold") and files["bold"].suffix.lower() == ".ttc":
            files.pop("bold")
        if files:
            font.update(variable=files["variable"], weight=files["weight"], bold=bool(files.get("bold")),
                        file=int(os.path.getmtime(files["regular"])),
                        _files={k: files[k] for k in ("regular", "bold") if k in files})
    _cache.update(mtime=mtime, font=font)
    return font
