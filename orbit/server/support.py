"""What the routes lean on: uploads, the page log, installed fonts, folder completion, the HTTP server class."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

from orbit.services import usage


UPLOAD_MAX = 100 * 1024 * 1024
USAGE = usage.Usage()


LOG_LOCK = threading.Lock()
LOG_MAX = 1 << 20  # page.log rolls over to page.log.1 at 1 MB


def append_log(path: Path, entry: dict) -> None:
    line = json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), **entry}, ensure_ascii=False)[:20000]
    with LOG_LOCK:
        try:
            if path.exists() and path.stat().st_size > LOG_MAX:
                os.replace(path, path.with_name(path.name + ".1"))
        except OSError:
            pass
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def save_upload(state: Path, name: str, data_b64: str) -> dict:
    """Write an uploaded file under <state>/uploads/<date>/ and return its path (what the request text carries)."""
    import base64
    import uuid
    try:
        data = base64.b64decode(data_b64, validate=True)
    except ValueError:
        raise ValueError("the file is not base64") from None
    if not data:
        raise ValueError("empty file")
    if len(data) > UPLOAD_MAX:
        raise ValueError(f"too large (more than {UPLOAD_MAX // 1024 // 1024} MB)")
    safe = re.sub(r"[^\w.\-]+", "-", os.path.basename(name)).strip("-.") or "pasted"
    if "." not in safe:
        safe += ".png"
    folder = state / "uploads" / time.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:6]}-{safe}"
    target.write_bytes(data)
    return {"path": str(target), "size": len(data)}


_FONTS: list[str] | None = None


def font_families() -> list[str]:
    """Installed font families (macOS NSFontManager through JXA), for Settings → Note font. Cached."""
    global _FONTS
    if _FONTS is None:
        js = ("ObjC.import('AppKit'); var a = $.NSFontManager.sharedFontManager.availableFontFamilies; var out = [];"
              "for (var i = 0; i < a.count; i++) out.push(ObjC.unwrap(a.objectAtIndex(i))); out.join('\\n')")
        try:
            r = subprocess.run(["osascript", "-l", "JavaScript", "-e", js], capture_output=True, text=True, timeout=20)
            names = [n.strip() for n in r.stdout.splitlines() if n.strip() and not n.startswith(".")]
        except (OSError, subprocess.TimeoutExpired):
            names = []
        _FONTS = sorted(set(names), key=str.lower)
    return _FONTS


def complete_dir(q: str) -> list[str]:
    """Folder completions for the New session dialog."""
    q = q or "~/"
    exp = os.path.expanduser(q)
    base, prefix = (exp, "") if exp.endswith("/") else os.path.split(exp)
    try:
        names = sorted(n for n in os.listdir(base or "/") if not n.startswith(".") or prefix.startswith("."))
    except OSError:
        return []
    out = []
    shown_base = q[: len(q) - len(prefix)] if prefix else q
    for n in names:
        if n.lower().startswith(prefix.lower()) and os.path.isdir(os.path.join(base or "/", n)):
            out.append(shown_base + n + "/")
        if len(out) >= 50:
            break
    return out



class Server(ThreadingHTTPServer):
    request_queue_size = 128  # the listen backlog: 5 by default, and a browser opening a page connects for more at once (resets)

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError)):
            return  # a closed tab or SSE stream, not an error
        super().handle_error(request, client_address)
