"""Files moved and copied anywhere (orbit/workspace/transfer.py, POST /api/files/transfer, /api/files/pack, /unpack,
/put) and the Mac's clipboard both ways (/api/clipboard/files, osascript replaced here). The Trash is a folder of the
test's own: nothing reaches the user's.

Run: uv run python -m unittest discover -s tests -p test_transfer.py -v
"""

import io
import json
import os
import tarfile
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

import test_remote
from orbit.workspace import files, transfer


class TrashHere:
    """files.trash into a folder of the test's own."""

    def setUp(self):
        super().setUp()
        self._trash_dir = tempfile.mkdtemp(prefix="orbit-test-trash-")
        self._trash = files.trash
        files.trash = lambda p: os.rename(files._abs(p), Path(self._trash_dir) / f"{time.time_ns()}-{os.path.basename(p)}")

    def tearDown(self):
        files.trash = self._trash
        super().tearDown()

    def trashed(self):
        return sorted(n.split("-", 1)[1] for n in os.listdir(self._trash_dir))


def tree(root: Path, spec: dict):
    for k, v in spec.items():
        p = root / k
        if isinstance(v, dict):
            p.mkdir(parents=True, exist_ok=True)
            tree(p, v)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(v)


class LocalTest(TrashHere, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        tree(self.d, {"a": {"x.txt": "x", "sub": {"y.txt": "y"}}, "b": {}, "notes.md": "n"})

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def test_copy_move_names_and_refusals(self):
        r = transfer.local_transfer(str(self.d / "a"), str(self.d / "b"), move=False)
        self.assertEqual((self.d / "b" / "a" / "sub" / "y.txt").read_text(), "y")
        self.assertTrue((self.d / "a").is_dir())  # (a copy: the original stays)
        r2 = transfer.local_transfer(str(self.d / "a"), str(self.d / "b"), move=False)  # (again: Finder's " copy")
        self.assertEqual(Path(r2["path"]).name, "a copy")
        r3 = transfer.local_transfer(str(self.d / "notes.md"), str(self.d), move=False)  # (in its own folder: a duplicate)
        self.assertEqual(Path(r3["path"]).name, "notes copy.md")
        self.assertEqual(Path(transfer.local_transfer(str(self.d / "notes.md"), str(self.d), move=False)["path"]).name, "notes copy 2.md")
        with self.assertRaisesRegex(ValueError, "already exists"):  # (a move onto a taken name)
            transfer.local_transfer(str(self.d / "a"), str(self.d / "b"), move=True)
        with self.assertRaisesRegex(ValueError, "into itself"):
            transfer.local_transfer(str(self.d / "a"), str(self.d / "a" / "sub"), move=True)
        self.assertTrue(transfer.local_transfer(str(self.d / "notes.md"), str(self.d), move=True).get("same"))  # (nothing to do)
        m = transfer.local_transfer(str(self.d / "a" / "x.txt"), str(self.d / "b"), move=True)
        self.assertEqual(Path(m["path"]), self.d / "b" / "x.txt")
        self.assertFalse((self.d / "a" / "x.txt").exists())
        with self.assertRaises(FileNotFoundError):
            transfer.local_transfer(str(self.d / "nope"), str(self.d / "b"), move=False)

    def test_pack_unpack_and_unsafe_tars(self):
        data = transfer.pack(str(self.d / "a"))
        out = transfer.unpack(data, str(self.d / "b"))
        self.assertEqual((Path(out["path"]) / "sub" / "y.txt").read_text(), "y")
        bad = io.BytesIO()
        with tarfile.open(fileobj=bad, mode="w") as t:  # a path out of the folder: refused, nothing written outside
            info = tarfile.TarInfo("../escaped.txt")
            info.size = 1
            t.addfile(info, io.BytesIO(b"x"))
        bad.seek(0)
        with self.assertRaises(Exception):
            transfer.unpack(bad, str(self.d / "b"))
        self.assertFalse((self.d / "escaped.txt").exists())
        self.assertEqual([n for n in os.listdir(self.d / "b") if n.startswith(".orbit-unpack")], [])  # (its scratch folder gone)


class AcrossMachinesTest(TrashHere, test_remote.RemoteTest):
    """This server (the page's) and another (rbox): moved and copied each way, and within that other machine."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        url = f"http://127.0.0.1:{cls.rport}/?token={cls.token}"
        code, _ = cls._req(cls, "POST", "/api/remotes", {"name": "rbox", "url": url})
        assert code == 200

    def _req(self, method, path, body=None):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method, headers={"Content-Type": "application/json"},
                                   data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def setUp(self):
        super().setUp()
        self.here = Path(tempfile.mkdtemp(dir=self.work))
        self.there = Path(tempfile.mkdtemp(dir=self.rwork))
        tree(self.here, {"proj": {"main.py": "print(1)", "pkg": {"m.py": "m"}}, "dst": {}})
        tree(self.there, {"data": {"rows.csv": "1,2", "deep": {"z.txt": "z"}}, "inbox": {}, "other": {}})

    def far(self, p: Path) -> str:
        return "/@rbox" + str(p)

    def go(self, src, dir, move):
        code, r = self._req("POST", "/api/files/transfer", {"src": src, "dir": dir, "move": move})
        self.assertEqual(code, 200, r)
        return r

    def test_here_to_there_and_back(self):
        r = self.go(str(self.here / "proj"), self.far(self.there / "inbox"), move=False)  # a copy across
        self.assertEqual(r["path"], self.far(self.there / "inbox" / "proj"))
        self.assertEqual((self.there / "inbox" / "proj" / "pkg" / "m.py").read_text(), "m")
        self.assertTrue((self.here / "proj").is_dir())
        r = self.go(self.far(self.there / "data"), str(self.here / "dst"), move=True)  # a move across: the original to its Trash
        self.assertEqual(r["path"], str(self.here / "dst" / "data"))
        self.assertEqual((self.here / "dst" / "data" / "deep" / "z.txt").read_text(), "z")
        self.assertFalse((self.there / "data").exists())
        self.assertIn("data", self.trashed())
        # a move onto a taken name across: refused, the original kept
        code, e = self._req("POST", "/api/files/transfer", {"src": str(self.here / "proj"), "dir": self.far(self.there / "inbox"), "move": True})
        self.assertEqual(code, 400, e)
        self.assertTrue((self.here / "proj").is_dir())

    def test_within_the_other_machine(self):
        r = self.go(self.far(self.there / "data" / "rows.csv"), self.far(self.there / "other"), move=True)
        self.assertEqual(r["path"], self.far(self.there / "other" / "rows.csv"))
        self.assertTrue((self.there / "other" / "rows.csv").exists())
        self.assertFalse((self.there / "data" / "rows.csv").exists())
        self.assertEqual(self.trashed(), [])  # (one machine: a real move, nothing to the Trash)

    def test_put_a_dropped_file_here_and_there(self):
        for dir, folder in ((str(self.here / "dst"), self.here / "dst"), (self.far(self.there / "inbox"), self.there / "inbox")):
            for _ in range(2):
                req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/files/put?dir={urllib.parse.quote(dir)}&name=photo.png",
                                             method="POST", data=b"\x89PNG data")
                with urllib.request.urlopen(req, timeout=30) as resp:
                    self.assertEqual(resp.status, 200)
            self.assertEqual(sorted(os.listdir(folder)), ["photo copy.png", "photo.png"])
            self.assertEqual((folder / "photo.png").read_bytes(), b"\x89PNG data")

    def test_the_macs_clipboard_both_ways(self):
        calls = []
        old_jxa, old_cache = transfer._jxa, transfer.CACHE
        transfer.CACHE = Path(tempfile.mkdtemp(prefix="orbit-test-clip-"))

        def fake(script, args=None):
            calls.append(args or [])
            return {"count": 7, "paths": [str(self.here / "proj" / "main.py")]} if args is None else {"count": 8}
        transfer._jxa = fake
        try:
            code, r = self._req("GET", "/api/clipboard/files")  # Finder's copy, read
            self.assertEqual((code, r["count"], r["paths"]), (200, 7, [str(self.here / "proj" / "main.py")]))
            code, r = self._req("POST", "/api/clipboard/files", {"paths": [str(self.here / "proj"), self.far(self.there / "data")]})
            self.assertEqual((code, r["count"], r["fetched"]), (200, 8, 1), r)
            local, fetched = calls[-1]
            self.assertEqual(local, str(self.here / "proj"))  # (this machine's: as it is)
            self.assertTrue(fetched.startswith(str(transfer.CACHE)) and fetched.endswith("/data"))  # (another's: fetched first)
            self.assertEqual((Path(fetched) / "deep" / "z.txt").read_text(), "z")
            # Finder's file pasted into the other machine's folder: an ordinary transfer
            self.go(str(self.here / "proj" / "main.py"), self.far(self.there / "inbox"), move=False)
            self.assertEqual((self.there / "inbox" / "main.py").read_text(), "print(1)")
            # from another machine (its token): no Mac clipboard
            h = {"Authorization": f"Bearer {self.token}", "Host": "orbit.example.ts.net"}
            req = urllib.request.Request(f"http://127.0.0.1:{self.rport}/api/clipboard/files", headers=h)
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=10)
            self.assertEqual(cm.exception.code, 403)
        finally:
            transfer._jxa, transfer.CACHE = old_jxa, old_cache


def load_tests(loader, tests, pattern):  # (their own tests only: not the remote test's again)
    suite = unittest.TestSuite()
    for cls in (LocalTest, AcrossMachinesTest):
        suite.addTests(cls(n) for n in loader.getTestCaseNames(cls) if n in vars(cls))
    return suite


if __name__ == "__main__":
    unittest.main()
