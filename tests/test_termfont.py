"""The ⌘J terminal's font, like iTerm's (orbit/services/termfont.py): the default profile's font read from iTerm's
preferences, its file found by PostScript name in the font folders, served only that file.

Run: uv run python -m unittest discover -s tests -v
"""

import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_skeleton
from orbit.services import termfont


def sfnt(ps: str, variable: bool = False) -> bytes:
    """A minimal sfnt: a name table with PostScript name ps (Windows, UTF-16BE), and an fvar table when variable."""
    s = ps.encode("utf-16-be")
    name = struct.pack(">HHH", 0, 1, 6 + 12) + struct.pack(">HHHHHH", 3, 1, 0x409, 6, len(s), 0) + s
    tables = [(b"name", name)] + ([(b"fvar", b"\0" * 16)] if variable else [])
    head = struct.pack(">IHHHH", 0x00010000, len(tables), 0, 0, 0)
    off, dirs, body = 12 + 16 * len(tables), b"", b""
    for tag, data in tables:
        dirs += struct.pack(">4sIII", tag, 0, off + len(body), len(data))
        body += data
    return head + dirs + body


class TermFontTest(unittest.TestCase):
    def test_the_default_profiles_font(self):
        d = {"Default Bookmark Guid": "B", "New Bookmarks": [
            {"Guid": "A", "Name": "Other", "Normal Font": "Menlo-Regular 11"},
            {"Guid": "B", "Name": "Mine", "Normal Font": "NotoSansMono-Regular_Medium 12.5", "Vertical Spacing": 1.1,
             "Horizontal Spacing": 1, "Thin Strokes": 4}]}
        self.assertEqual(termfont.parse(d), {"source": "iTerm", "profile": "Mine", "font": "NotoSansMono-Regular_Medium",
                                             "size": 12.5, "line": 1.1, "letter": 1.0, "thin": 4})
        self.assertEqual(termfont.parse({"New Bookmarks": [{"Normal Font": "Evil\"');} 12"}]}), {})  # (goes into CSS)
        self.assertEqual(termfont.parse({}), {})

    def test_files_by_postscript_name(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            (t / "NotoSansMono-VariableFont_wdth,wght.ttf").write_bytes(sfnt("NotoSansMono-Regular", variable=True))
            (t / "Acme-Regular.otf").write_bytes(sfnt("AcmeMono-Regular"))
            (t / "Acme-Bold.otf").write_bytes(sfnt("AcmeMono-Bold"))
            (t / "junk.ttf").write_bytes(b"not a font")
            with mock.patch.object(termfont, "FONT_DIRS", [t]):
                self.assertEqual(termfont.font_names(t / "Acme-Bold.otf"), ({"AcmeMono-Bold"}, False))
                f = termfont.font_files("NotoSansMono-Regular_Medium")  # a variable font's instance: its file, weight 500
                self.assertEqual((f["regular"].name, f["variable"], f["weight"], "bold" in f),
                                 ("NotoSansMono-VariableFont_wdth,wght.ttf", True, 500, False))
                f = termfont.font_files("AcmeMono-Regular")  # (found though the file name differs; its bold beside)
                self.assertEqual((f["regular"].name, f["bold"].name, f["weight"]), ("Acme-Regular.otf", "Acme-Bold.otf", 400))
                self.assertEqual(termfont.font_files("Nowhere-Regular"), {})


@unittest.skipUnless(sys.platform == "darwin", "iTerm is macOS's")
class RouteTest(test_skeleton.SkeletonTest):
    def test_only_the_found_file_is_served(self):
        with tempfile.TemporaryDirectory() as t:
            font = Path(t) / "Mono.ttf"
            font.write_bytes(sfnt("Mono-Regular"))
            fake = {"source": "iTerm", "font": "Mono-Regular", "size": 12.0, "_files": {"regular": font}}
            with mock.patch.object(termfont, "iterm_font", return_value=fake):
                code, d = self.req("GET", "/api/term-font")
                self.assertEqual((code, d), (200, {"source": "iTerm", "font": "Mono-Regular", "size": 12.0}))  # (no paths)
                import urllib.request
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/term-font/file/regular") as r:
                    self.assertEqual((r.headers["Content-Type"], r.read()), ("font/ttf", font.read_bytes()))
                self.assertEqual(self.req("GET", "/api/term-font/file/bold")[0], 404)
                self.assertEqual(self.req("GET", "/api/term-font/file/..%2f..%2fetc%2fpasswd")[0], 404)


if __name__ == "__main__":
    unittest.main()
