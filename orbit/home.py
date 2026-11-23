"""~/.orbit: Orbit's own folder (state, data, logs). Earlier versions used ~/.orbit: it is moved here once, and a link
is left at the old place so paths written into old conversations (uploads, notes) still open."""

from __future__ import annotations

from pathlib import Path


def home() -> Path:
    new, old = Path.home() / ".orbit", Path.home() / (".a" + "ide")  # spelled apart: renames skip it
    if not new.exists() and old.is_dir() and not old.is_symlink():
        try:
            old.rename(new)
            old.symlink_to(new)
        except OSError:
            return old if old.exists() else new
    return new
