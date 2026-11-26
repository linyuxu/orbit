"""Files and ± Review for the server: git-aware tree, read / save with conflict detection, diffs, hunk revert.

Paths are absolute. The server serves 127.0.0.1 only, so the user's own files are in scope; nothing here runs a
shell (every git call is an argument list).
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


MAX_TEXT = 5 * 1024 * 1024  # larger files open as "too large", not in the editor
MAX_TREE = 20000
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
             "target", "dist", "build", ".next", ".idea", ".DS_Store"}
IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico", "tiff", "heic"}
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # git's empty tree: the base when there is no commit yet


class Conflict(Exception):
    """The file changed on disk since the editor loaded it."""

    def __init__(self, current: dict):
        super().__init__("changed on disk")
        self.current = current


def _abs(path: str) -> Path:
    if not path or not os.path.isabs(os.path.expanduser(path)):
        raise ValueError("an absolute path is required")
    return Path(os.path.expanduser(path))


def _git(root: Path, *args: str, check: bool = True, input: str | None = None) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, input=input, timeout=20)
    if check and r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"git {args[0]} failed")
    return r.stdout


def git_root(path: Path) -> Path | None:
    try:
        out = _git(path if path.is_dir() else path.parent, "rev-parse", "--show-toplevel")
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return None
    return Path(os.path.realpath(out.strip())) if out.strip() else None


_LS_CACHE: dict[str, tuple[float, list[str]]] = {}


def _repo_files(root: Path) -> list[str]:
    """Tracked and untracked (not ignored) files of a git repo, relative; cached for 30 s."""
    key = str(root)
    hit = _LS_CACHE.get(key)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    try:
        files = _git(root, "ls-files", "-co", "--exclude-standard", "-z").split("\0")
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        files = []
    files = [f for f in files if f]
    _LS_CACHE[key] = (time.time(), files)
    return files


def _ending_with(root: Path, ref: str) -> str | None:
    """The shortest path in the repo that is `ref` or ends with `/ref`."""
    while ref.startswith("./"):
        ref = ref[2:]
    hits = [f for f in _repo_files(root) if f == ref or f.endswith("/" + ref)]
    return str(root / min(hits, key=lambda f: (f.count("/"), len(f)))) if hits else None


def resolve(ref: str, cwd: str) -> str | None:
    """Find the file an answer names (`src/app.py`, `app.py`, `~/x/y.go`): as written relative to the session folder;
    else the shortest path ending with it in the session's repo; else in the git repos next to that repo."""
    ref = ref.strip()
    if not ref or "\0" in ref:
        return None
    p = Path(os.path.expanduser(ref))
    base = Path(os.path.expanduser(cwd)) if cwd else None
    if p.is_absolute():
        return str(p) if p.is_file() else None
    if base and (base / p).is_file():
        return os.path.realpath(base / p)
    if not base:
        return None
    root = git_root(base) or base
    hit = _ending_with(root, ref) if (root / ".git").exists() else None
    if hit:
        return hit
    try:
        siblings = sorted(d for d in root.parent.iterdir() if d != root and (d / ".git").exists())[:60]
    except OSError:
        siblings = []
    found = [h for h in (_ending_with(d, ref) for d in siblings) if h]
    return min(found, key=lambda h: (h.count("/"), len(h))) if found else None


def digest(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def kind_of(path: Path, head: bytes) -> str:
    ext = path.suffix.lower().lstrip(".")
    if ext in IMAGE_EXT:
        return "image"
    if ext == "pdf":
        return "pdf"
    if b"\x00" in head[:8000]:
        return "binary"
    try:
        head[:8000].decode("utf-8")
    except UnicodeDecodeError as e:
        if e.start < len(head[:8000]) - 4:  # a cut multibyte char at the end is fine
            return "binary"
    return "text"


# ------------------------------------------------------------------ tree
def _status(root: Path) -> dict[str, str]:
    """{relative path: one letter} from `git status`: M modified, A added, D deleted, R renamed, ? untracked."""
    out = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", check=False)
    st: dict[str, str] = {}
    parts = out.split("\0")
    i = 0
    while i < len(parts):
        e = parts[i]
        i += 1
        if len(e) < 4:
            continue
        x, y, p = e[0], e[1], e[3:]
        if x == "R" or y == "R":
            i += 1  # the old name follows
            st[p] = "R"
        elif x == "?":
            st[p] = "?"
        elif "D" in (x, y):
            st[p] = "D"
        elif x == "A":
            st[p] = "A"
        else:
            st[p] = "M"
    return st


def tree(root: str) -> dict[str, Any]:
    """Every file under root (git: tracked + untracked, minus ignored), with git status letters; and the empty
    folders (dirs: no file lists them, and a file manager shows them)."""
    base = Path(os.path.realpath(_abs(root)))  # git reports resolved paths (/var → /private/var on macOS)
    if not base.is_dir():
        raise ValueError(f"no such folder: {base}")
    groot = git_root(base)
    files: list[str] = []
    empty: list[str] = []
    status: dict[str, str] = {}
    truncated = False
    if groot:
        out = _git(base, "ls-files", "-z", "--cached", "--others", "--exclude-standard", check=False)
        files = [f for f in out.split("\0") if f]
        rel = os.path.relpath(base, groot)
        for p, s in _status(groot).items():
            if rel == ".":
                status[p] = s
            elif p.startswith(rel + "/"):
                status[p[len(rel) + 1:]] = s
        # Deleted files are not on disk but belong in the tree (marked D).
        files = sorted(set(f for f in files if (base / f).exists() or status.get(f) == "D"))
        # Empty folders (git lists no folder): untracked ones, not ignored, that hold nothing
        out = _git(base, "ls-files", "-z", "--others", "--directory", "--exclude-standard", check=False)
        have = {f.rsplit("/", 1)[0] for f in files if "/" in f}
        for d in (x.rstrip("/") for x in out.split("\0") if x.endswith("/")):  # (an untracked folder, collapsed)
            if not (base / d).is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(base / d):
                dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS]
                rel = os.path.relpath(dirpath, base)
                if not dirnames and not [f for f in filenames if f != ".DS_Store"] and rel not in have:
                    empty.append(rel)
                if len(empty) >= 2000:
                    break
    else:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
            if not dirnames and not [f for f in filenames if f != ".DS_Store"] and dirpath != str(base):
                empty.append(os.path.relpath(dirpath, base))
            for f in sorted(filenames):
                if f == ".DS_Store":
                    continue
                files.append(os.path.relpath(os.path.join(dirpath, f), base))
                if len(files) >= MAX_TREE:
                    truncated = True
                    break
            if truncated:
                break
    if len(files) > MAX_TREE:
        files, truncated = files[:MAX_TREE], True
    branch = ""
    if groot:
        branch = _git(groot, "branch", "--show-current", check=False).strip()
    return {"root": str(base), "git": bool(groot), "branch": branch, "files": files, "status": status,
            "truncated": truncated, "dirs": sorted(empty)[:MAX_TREE]}


def dirlist(path: str) -> dict[str, Any]:
    """One folder's entries (⌘⇧E breadcrumb browsing)."""
    p = _abs(path)
    if not p.is_dir():
        raise ValueError(f"no such folder: {p}")
    entries = []
    for e in sorted(os.scandir(p), key=lambda e: (not e.is_dir(), e.name.lower())):
        if e.name in (".DS_Store", ".git"):
            continue
        entries.append({"name": e.name, "dir": e.is_dir()})
    return {"path": str(p), "parent": str(p.parent) if p.parent != p else None, "entries": entries}


# ------------------------------------------------------------------ read / save
def stat(path: Path) -> dict[str, Any]:
    st = path.stat()
    return {"mtime": st.st_mtime_ns, "size": st.st_size}


def read(path: str) -> dict[str, Any]:
    p = _abs(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    if p.is_dir():
        raise ValueError(f"{p} is a folder")
    info = {"path": str(p), **stat(p)}
    with open(p, "rb") as f:
        head = f.read(8192)
    info["kind"] = kind_of(p, head)
    if info["kind"] != "text":
        return info
    if info["size"] > MAX_TEXT:
        info["kind"] = "too large"
        return info
    data = p.read_bytes()
    info["hash"] = digest(data)
    info["text"] = data.decode("utf-8", errors="replace")
    return info


class EmptyOverwrite(ValueError):
    """An empty buffer would replace a file that has content (a blank editor must never wipe a file)."""


def save(path: str, text: str, base_hash: str | None, force: bool = False, allow_empty: bool = False) -> dict[str, Any]:
    """Atomic write. Refuses (Conflict) when the file on disk is not the version the editor started from, and
    (EmptyOverwrite) to write nothing over a non-empty file unless the user said so."""
    p = _abs(path)
    if p.exists():
        if p.is_dir():
            raise ValueError(f"{p} is a folder")
        current = p.read_bytes()
        if not text and current and not allow_empty:
            raise EmptyOverwrite(f"refusing to save an empty buffer over {p.name} ({len(current)} bytes)")
        if not force and base_hash is not None and digest(current) != base_hash:
            raise Conflict({"path": str(p), "text": current.decode("utf-8", errors="replace"),
                            "hash": digest(current), **stat(p)})
        mode = p.stat().st_mode & 0o7777
    elif base_hash and not force:
        raise Conflict({"path": str(p), "deleted": True})
    else:
        mode = 0o644
        p.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".orbit-tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return {"path": str(p), "hash": digest(data), **stat(p)}


def stats(paths: list[str]) -> dict[str, Any]:
    """Cheap change detection for open tabs: mtime + size per path (None if gone)."""
    out: dict[str, Any] = {}
    for path in paths[:200]:
        try:
            out[path] = stat(_abs(path))
        except (OSError, ValueError):
            out[path] = None
    return out


def create(path: str, folder: bool = False) -> dict[str, Any]:
    p = _abs(path)
    if p.exists():
        raise ValueError(f"{p.name} already exists")
    if folder:
        p.mkdir(parents=True)
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
    return {"path": str(p)}


def rename(path: str, new: str) -> dict[str, Any]:
    p, q = _abs(path), _abs(new)
    if not p.exists():
        raise FileNotFoundError(str(p))
    if q.exists():
        raise ValueError(f"{q.name} already exists")
    q.parent.mkdir(parents=True, exist_ok=True)
    os.rename(p, q)
    return {"path": str(q)}


def trash(path: str) -> None:
    """Delete = move to the Trash (recoverable), never unlink."""
    p = _abs(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    exe = shutil.which("trash")
    if exe:
        r = subprocess.run([exe, str(p)], capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return
    dest = Path.home() / ".Trash" / p.name
    n = 1
    while dest.exists():
        dest = Path.home() / ".Trash" / f"{p.stem} {n}{p.suffix}"
        n += 1
    shutil.move(str(p), dest)


JVM_EXT = {".java", ".kt", ".kts", ".scala", ".groovy", ".gradle"}


def _find_app(names: list[str]) -> Path | None:
    """The first app bundle whose name starts with one of `names`, in /Applications or ~/Applications."""
    for d in (Path("/Applications"), Path.home() / "Applications"):
        try:
            apps = sorted(d.iterdir())
        except OSError:
            continue
        for n in names:
            for a in apps:
                if a.suffix == ".app" and a.name.startswith(n):
                    return a
    return None


def editor_command(target: Path, line: int | None, how: str) -> list[str]:
    """The command that opens `target` (at `line`) in the chosen editor: "jetbrains" (IntelliJ IDEA for JVM files,
    PyCharm otherwise), "vscode", or "default" ($ORBIT_EDITOR, else the file's default app)."""
    if how == "jetbrains":
        jvm = target.suffix.lower() in JVM_EXT
        cli, names = ("idea", ["IntelliJ IDEA"]) if jvm else ("pycharm", ["PyCharm"])
        exe = shutil.which(cli)
        if not exe:
            app = _find_app(names) or _find_app(["PyCharm", "IntelliJ IDEA"])
            if not app:
                raise ValueError(f"no {names[0]} found in /Applications or ~/Applications")
            bins = sorted((app / "Contents" / "MacOS").iterdir())
            exe = str(bins[0]) if bins else None
            if not exe:
                raise ValueError(f"{app.name} has no launcher")
        return [exe, *(["--line", str(line)] if line else []), str(target)]
    if how == "vscode":
        exe = shutil.which("code")
        if not exe:
            app = _find_app(["Visual Studio Code"])
            exe = str(app / "Contents/Resources/app/bin/code") if app else None
        if not exe:
            raise ValueError("no VS Code found (its `code` command or Visual Studio Code.app)")
        return [exe, "-g", f"{target}:{line}" if line else str(target)]
    editor = os.environ.get("ORBIT_EDITOR")
    return [*editor.split(), str(target)] if editor else ["open", str(target)]


def mime(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


# ------------------------------------------------------------------ ± Review
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


def _base(groot: Path) -> str:
    r = subprocess.run(["git", "-C", str(groot), "rev-parse", "--verify", "-q", "HEAD"], capture_output=True, text=True)
    return "HEAD" if r.returncode == 0 else EMPTY_TREE


def parse_diff(text: str) -> list[dict[str, Any]]:
    """Unified diff → [{path, old_path, status, binary, hunks: [{header, old_start, old_lines, new_start, new_lines,
    lines: [{t, text, old, new}]}], add, del}]."""
    files: list[dict[str, Any]] = []
    f: dict[str, Any] | None = None
    h: dict[str, Any] | None = None
    o = n = 0
    for line in text.split("\n"):
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.*) b/(.*)$", line)
            f = {"path": m.group(2) if m else line[11:], "old_path": m.group(1) if m else "", "status": "M",
                 "binary": False, "hunks": [], "add": 0, "del": 0}
            files.append(f)
            h = None
            continue
        if f is None:
            continue
        if h is None:
            if line.startswith("new file mode"):
                f["status"] = "A"
            elif line.startswith("deleted file mode"):
                f["status"] = "D"
            elif line.startswith("rename to "):
                f["status"] = "R"
                f["path"] = line[len("rename to "):]
            elif line.startswith("Binary files"):
                f["binary"] = True
        m = _HUNK.match(line)
        if m:
            o, n = int(m.group(1)), int(m.group(3))
            h = {"header": line, "old_start": o, "old_lines": int(m.group(2) or 1), "new_start": n,
                 "new_lines": int(m.group(4) or 1), "lines": []}
            f["hunks"].append(h)
            continue
        if h is None or not line or line[0] not in " +-\\":
            continue
        tch = line[0]
        if tch == "\\":
            h["lines"].append({"t": "\\", "text": line[2:], "old": None, "new": None})
            continue
        entry = {"t": tch, "text": line[1:], "old": None, "new": None}
        if tch in " -":
            entry["old"] = o
            o += 1
        if tch in " +":
            entry["new"] = n
            n += 1
        if tch == "+":
            f["add"] += 1
        elif tch == "-":
            f["del"] += 1
        h["lines"].append(entry)
    return files


def _line_counts(groot: Path, untracked: list[str]) -> tuple[int, int]:
    """Lines added and deleted by the uncommitted changes (the ± badge): tracked from `git diff --numstat`, untracked
    text files counted whole, the same files the review lists, within a read budget so a 5 s poll stays cheap."""
    add = dele = 0
    for line in _git(groot, "diff", _base(groot), "--numstat", "--no-ext-diff", "--no-renames", check=False).splitlines():
        a, _, rest = line.partition("\t")
        d, _, _ = rest.partition("\t")
        if a.isdigit() and d.isdigit():  # binary files show "-"
            add += int(a)
            dele += int(d)
    budget = 4 * 1024 * 1024
    for rel in untracked[:400]:
        p = groot / rel
        try:
            if not p.is_file() or p.stat().st_size > min(budget, 512 * 1024):
                continue
            data = p.read_bytes()
        except OSError:
            continue
        budget -= len(data)
        if kind_of(p, data[:8192]) == "text":
            add += data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
        if budget <= 0:
            break
    return add, dele


def review(root: str, summary: bool = False) -> dict[str, Any]:
    """Uncommitted changes of the repository holding root: tracked changes vs HEAD, plus untracked files."""
    base_dir = _abs(root)
    groot = git_root(base_dir)
    if not groot:
        return {"git": False, "root": str(base_dir), "files": [], "count": 0}
    if summary:
        st = _status(groot)
        add, dele = _line_counts(groot, [p for p, c in st.items() if c == "?"])
        return {"git": True, "root": str(groot), "count": len(st), "add": add, "del": dele}
    diff = _git(groot, "diff", _base(groot), "--no-color", "--no-ext-diff", "-U3", "--find-renames", check=False)
    files = parse_diff(diff)
    untracked = _git(groot, "ls-files", "-z", "--others", "--exclude-standard", check=False)
    for rel in [u for u in untracked.split("\0") if u]:
        p = groot / rel
        entry: dict[str, Any] = {"path": rel, "old_path": "", "status": "?", "binary": False, "hunks": [],
                                 "add": 0, "del": 0}
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if kind_of(p, data[:8192]) != "text" or len(data) > 512 * 1024:
            entry["binary"] = True
        else:
            lines = data.decode("utf-8", errors="replace").split("\n")
            if lines and lines[-1] == "":
                lines.pop()
            entry["hunks"] = [{"header": f"@@ -0,0 +1,{len(lines)} @@", "old_start": 0, "old_lines": 0,
                               "new_start": 1, "new_lines": len(lines),
                               "lines": [{"t": "+", "text": l, "old": None, "new": i + 1} for i, l in enumerate(lines)]}]
            entry["add"] = len(lines)
        files.append(entry)
    files.sort(key=lambda f: f["path"])
    return {"git": True, "root": str(groot), "branch": _git(groot, "branch", "--show-current", check=False).strip(),
            "files": files, "count": len(files)}


def revert_hunk(root: str, path: str, header: str) -> None:
    """Undo one hunk of a tracked file in the working tree (git apply -R of just that hunk)."""
    groot = git_root(_abs(root))
    if not groot:
        raise ValueError("not a git repository")
    diff = _git(groot, "diff", _base(groot), "--no-color", "--no-ext-diff", "-U3", "--", path, check=False)
    if not diff.strip():
        raise ValueError(f"{path} has no tracked changes to revert")
    head, _, rest = diff.partition("\n@@")
    hunks = ("@@" + rest).split("\n@@")
    hunks = [hunks[0]] + ["@@" + x for x in hunks[1:]]
    pick = next((x for x in hunks if x.split("\n", 1)[0] == header), None)
    if pick is None:
        raise ValueError("that hunk changed; refresh the review")
    patch = head + "\n" + pick.rstrip("\n") + "\n"
    r = subprocess.run(["git", "-C", str(groot), "apply", "-R", "--whitespace=nowarn", "-"], input=patch,
                       capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        raise ValueError(r.stderr.strip() or "git apply failed")
