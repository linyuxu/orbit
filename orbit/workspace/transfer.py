"""Files moved and copied anywhere: in a folder of this machine, of another machine, from one to another; and the Mac's
clipboard (Finder's copy and paste) both ways.

transfer(src, dir, move) puts src (a file or a folder) into folder dir. Paths are this server's: /@NAME/… is NAME's.
  one machine (this or another)  that machine moves or copies it (another: its own /api/files/transfer)
  two machines                   src packed (tar: /api/files/pack there, or here), unpacked in dir (/api/files/unpack
                                 there, or here); a move then sends the original to its machine's Trash (never gone
                                 before the copy is in place)
A copy whose name is taken gets " copy" (" copy 2"…), as Finder does; a move onto a taken name, or a folder into
itself, is refused. A move within its own folder does nothing.

The clipboard (this Mac's NSPasteboard, through osascript): read_clipboard() gives the file paths Finder copied (and
the pasteboard's change count); write_clipboard(paths) puts files there for Finder's paste (another machine's are
fetched into a cache folder first). Only for this machine's own page: never a request from another machine.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import IO

from orbit.machines import federation, remote
from orbit.workspace import files

SPOOL = 64 * 1024 * 1024  # a pack kept in memory up to this, then on disk
MAX_PACK = 20 * 1024 ** 3  # 20 GB across machines at most
CACHE = Path.home() / "Library" / "Caches" / "Orbit" / "clipboard"  # another machine's files put on the clipboard


# ------------------------------------------------------------------ this machine
def free_name(folder: Path, name: str, copy: bool) -> str:
    """name in folder, or (a copy) "name copy", "name copy 2"…; a move onto a taken name: ValueError."""
    if not (folder / name).exists() and not (folder / name).is_symlink():
        return name
    if not copy:
        raise ValueError(f"{name} already exists in {folder}")
    stem, ext = (name, "") if (folder / name).is_dir() or "." not in name[1:] else (name.rsplit(".", 1)[0], "." + name.rsplit(".", 1)[1])
    for n in range(1, 1000):
        cand = f"{stem} copy{'' if n == 1 else ' ' + str(n)}{ext}"
        if not (folder / cand).exists():
            return cand
    raise ValueError(f"too many copies of {name} in {folder}")


def local_transfer(src: str, dir: str, move: bool) -> dict:
    s, d = files._abs(src), files._abs(dir)
    if not s.exists() and not s.is_symlink():
        raise FileNotFoundError(str(s))
    if not d.is_dir():
        raise ValueError(f"not a folder: {d}")
    if move and s.parent == d:
        return {"path": str(s), "moved": False, "same": True}
    if s.is_dir() and (d == s or s in d.parents):
        raise ValueError(f"{s.name} cannot go into itself")
    name = free_name(d, s.name, copy=not move)
    dst = d / name
    if move:
        shutil.move(str(s), dst)
    elif s.is_dir() and not s.is_symlink():
        shutil.copytree(s, dst, symlinks=True)
    else:
        shutil.copy2(s, dst, follow_symlinks=False)
    return {"path": str(dst), "moved": move}


def pack(src: str) -> IO[bytes]:
    """src (a file or a folder) as a tar, in a temporary file (rewound)."""
    s = files._abs(src)
    if not s.exists() and not s.is_symlink():
        raise FileNotFoundError(str(s))
    out = tempfile.SpooledTemporaryFile(max_size=SPOOL)
    with tarfile.open(fileobj=out, mode="w") as tar:
        tar.add(str(s), arcname=s.name)
    if out.tell() > MAX_PACK:
        raise ValueError(f"{s.name} is over {MAX_PACK // 1024 ** 3} GB")
    out.seek(0)
    return out


def unpack(data: IO[bytes], dir: str, name: str = "", copy: bool = True) -> dict:
    """A tar of one file or folder into dir (its name kept, or " copy" when taken and copy). Safe paths only."""
    d = files._abs(dir)
    if not d.is_dir():
        raise ValueError(f"not a folder: {d}")
    tmp = Path(tempfile.mkdtemp(prefix=".orbit-unpack-", dir=d))
    try:
        with tarfile.open(fileobj=data, mode="r:*") as tar:
            tar.extractall(tmp, filter="data")
        top = [p for p in tmp.iterdir()]
        if len(top) != 1:
            raise ValueError("the pack holds no single file or folder")
        final = free_name(d, name or top[0].name, copy)
        os.rename(top[0], d / final)
        return {"path": str(d / final)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ another machine
def _info(state: Path, name: str) -> dict:
    info = remote.load(state).get(name)
    if not info:
        raise ValueError(f"no remote called {name}")
    return info


def _call(state: Path, name: str, method: str, path: str, query: dict | None = None, body: bytes | IO[bytes] | None = None,
          json_body: dict | None = None, out: IO[bytes] | None = None, timeout: float = 900) -> dict:
    info = _info(state, name)
    url = info["url"] + path + ("?" + urllib.parse.urlencode(query) if query else "")
    headers = {"Authorization": f"Bearer {info['token']}"}
    data = None
    if json_body is not None:
        data, headers["Content-Type"] = json.dumps(json_body).encode(), "application/json"
    elif body is not None:
        if hasattr(body, "read"):
            body.seek(0, 2)
            n = body.tell()
            body.seek(0)
            data, headers["Content-Length"] = body, str(n)
        else:
            data = body
        headers["Content-Type"] = "application/x-tar"
    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if out is not None:
                shutil.copyfileobj(r, out, 1024 * 1024)
                return {}
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            why = json.loads(e.read()).get("error") or e.reason
        except ValueError:
            why = e.reason
        raise ValueError(f"{name}: {why}") from None
    except (urllib.error.URLError, OSError) as e:
        raise ValueError(f"{name} is unreachable: {getattr(e, 'reason', e)}") from None


def _fetch(state: Path, src: str) -> IO[bytes]:
    """src (this machine's, or /@NAME/… another's) as a tar."""
    m = federation.machine_of(src)
    if not m:
        return pack(src)
    out = tempfile.SpooledTemporaryFile(max_size=SPOOL)
    _call(state, m[0], "GET", "/api/files/pack", {"path": m[1]}, out=out)
    out.seek(0)
    return out


def transfer(state: Path, src: str, dir: str, move: bool = False) -> dict:
    """Put src into folder dir (a move, or a copy), on one machine or across two. Paths as this server writes them."""
    ms, md = federation.machine_of(src), federation.machine_of(dir)
    if (ms and ms[0]) == (md and md[0]):  # one machine
        if not ms:
            return local_transfer(src, dir, move)
        r = _call(state, ms[0], "POST", "/api/files/transfer", json_body={"src": ms[1], "dir": md[1], "move": move})
        return {**r, "path": federation.ours(ms[0], r.get("path", ""))}
    name = os.path.basename(src.rstrip("/"))
    data = _fetch(state, src)
    try:
        if md:
            r = _call(state, md[0], "POST", "/api/files/unpack", {"dir": md[1], "name": name, "copy": "0" if move else "1"}, body=data)
            dst = federation.ours(md[0], r.get("path", ""))
        else:
            dst = unpack(data, dir, name, copy=not move)["path"]
    finally:
        data.close()
    if move:  # the copy is in place: the original to its Trash
        if ms:
            _call(state, ms[0], "POST", "/api/files/trash", json_body={"path": ms[1]})
        else:
            files.trash(src)
    return {"path": dst, "moved": move, "across": True}


def put(state: Path, dir: str, name: str, data: IO[bytes]) -> dict:
    """One file's bytes (dropped from Finder on the page) into folder dir, here or on another machine (as a tar)."""
    name = os.path.basename(str(name or "").replace("\\", "/")).strip()
    if not name or name in (".", ".."):
        raise ValueError("a file name")
    m = federation.machine_of(dir)
    if not m:
        d = files._abs(dir)
        if not d.is_dir():
            raise ValueError(f"not a folder: {d}")
        dst = d / free_name(d, name, copy=True)
        with open(dst, "wb") as f:
            shutil.copyfileobj(data, f, 1024 * 1024)
        return {"path": str(dst)}
    out = tempfile.SpooledTemporaryFile(max_size=SPOOL)
    data.seek(0, 2)
    n = data.tell()
    data.seek(0)
    with tarfile.open(fileobj=out, mode="w") as tar:
        info = tarfile.TarInfo(name)
        info.size, info.mtime, info.mode = n, int(time.time()), 0o644
        tar.addfile(info, data)
    out.seek(0)
    try:
        r = _call(state, m[0], "POST", "/api/files/unpack", {"dir": m[1], "name": name, "copy": "1"}, body=out)
    finally:
        out.close()
    return {"path": federation.ours(m[0], r.get("path", ""))}


# ------------------------------------------------------------------ the Mac's clipboard (Finder)
_READ = """ObjC.import('AppKit');
var pb = $.NSPasteboard.generalPasteboard;
var urls = pb.readObjectsForClassesOptions($([$.NSURL]), $({}));
var out = [];
if (urls) for (var i = 0; i < urls.count; i++) { var u = urls.objectAtIndex(i); if (u.isFileURL) out.push(ObjC.unwrap(u.path)); }
JSON.stringify({count: pb.changeCount, paths: out});"""
_WRITE = """function run(argv) {
  ObjC.import('AppKit');
  var pb = $.NSPasteboard.generalPasteboard;
  pb.clearContents;
  var arr = $.NSMutableArray.alloc.init;
  for (var i = 0; i < argv.length; i++) arr.addObject($.NSURL.fileURLWithPath(argv[i]));
  pb.writeObjects(arr);
  return JSON.stringify({count: pb.changeCount});
}"""


def _jxa(script: str, args: list[str] | None = None) -> dict:
    r = subprocess.run(["osascript", "-l", "JavaScript", "-e", script, *(args or [])], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise ValueError(f"the clipboard: {(r.stderr or r.stdout).strip()[:200]}")
    return json.loads(r.stdout.strip() or "{}")


def read_clipboard() -> dict:
    """{count, paths}: the files Finder (or anything) put on the clipboard, and the clipboard's change count."""
    return _jxa(_READ)


def write_clipboard(state: Path, paths: list[str]) -> dict:
    """Put these files on the clipboard for Finder's paste; another machine's are fetched into a cache folder first."""
    if not paths:
        raise ValueError("no files")
    CACHE.mkdir(parents=True, exist_ok=True)
    for old in CACHE.iterdir():  # (yesterday's fetched files: gone)
        try:
            if time.time() - old.stat().st_mtime > 86400:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            pass
    local, fetched = [], 0
    for p in paths:
        if federation.machine_of(p):
            box = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%d-%H%M%S-"), dir=CACHE))
            data = _fetch(state, p)
            try:
                local.append(unpack(data, str(box))["path"])
            finally:
                data.close()
            fetched += 1
        else:
            q = files._abs(p)
            if not q.exists():
                raise FileNotFoundError(str(q))
            local.append(str(q))
    r = _jxa(_WRITE, local)
    return {"count": r.get("count"), "paths": local, "fetched": fetched}
