"""Build step 3 (Files): tree, read / save with conflicts, create / rename / trash, ± Review and hunk revert.

Run: uv run python -m unittest discover -s tests -v
"""

import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import test_skeleton
from orbit.workspace import files


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


class FilesTest(test_skeleton.SkeletonTest):
    def setUp(self):
        self.repo = Path(self.tmp.name) / f"repo-{self._testMethodName}"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "a.py").write_text("one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\nten\n")
        (self.repo / "sub").mkdir()
        (self.repo / "sub" / "b.md").write_text("# B\n")
        (self.repo / ".gitignore").write_text("ignored.txt\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "init")

    def p(self, rel):
        return str(self.repo / rel)

    # ------------------------------------------------------------------ file names in answers
    def test_resolve(self):
        (self.repo / "sub" / "deep").mkdir()
        (self.repo / "sub" / "deep" / "a.py").write_text("x")
        other = Path(self.tmp.name) / f"other-{self._testMethodName}"
        (other / "pkg").mkdir(parents=True)
        git(other, "init", "-q")
        (other / "pkg" / "only_there.go").write_text("package x")
        sub = str(self.repo / "sub")
        self.assertEqual(files.resolve("a.py", str(self.repo)), os.path.realpath(self.p("a.py")))  # as written
        self.assertEqual(files.resolve("deep/a.py", sub), os.path.realpath(self.p("sub/deep/a.py")))
        self.assertEqual(files.resolve("a.py", sub), str(files.git_root(self.repo) / "a.py"))  # shortest in the repo
        self.assertEqual(files.resolve("b.md", str(self.repo)), str(files.git_root(self.repo) / "sub" / "b.md"))
        self.assertEqual(files.resolve("only_there.go", str(self.repo)),  # a git repo next to the session's
                         str(files.git_root(other) / "pkg" / "only_there.go"))
        self.assertIsNone(files.resolve("missing.py", str(self.repo)))
        self.assertIsNone(files.resolve("", str(self.repo)))

    def test_editor_command(self):
        t = Path("/x/a.py")
        with mock.patch("shutil.which", side_effect=lambda n: f"/bin/{n}"):
            self.assertEqual(files.editor_command(t, 12, "jetbrains"), ["/bin/pycharm", "--line", "12", "/x/a.py"])
            self.assertEqual(files.editor_command(Path("/x/A.kt"), 3, "jetbrains"), ["/bin/idea", "--line", "3", "/x/A.kt"])
            self.assertEqual(files.editor_command(t, 12, "vscode"), ["/bin/code", "-g", "/x/a.py:12"])
            self.assertEqual(files.editor_command(t, None, "vscode"), ["/bin/code", "-g", "/x/a.py"])
        with mock.patch.dict(os.environ, {"ORBIT_EDITOR": "subl -w"}):
            self.assertEqual(files.editor_command(t, 5, "default"), ["subl", "-w", "/x/a.py"])
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ORBIT_EDITOR", None)
            self.assertEqual(files.editor_command(t, 5, "default"), ["open", "/x/a.py"])

    # ------------------------------------------------------------------ tree
    def test_tree_is_git_aware(self):
        (self.repo / "new.txt").write_text("n")
        (self.repo / "ignored.txt").write_text("i")
        (self.repo / "a.py").write_text("changed\n")
        code, t = self.req("GET", f"/api/tree?root={self.repo}")
        self.assertEqual(code, 200)
        self.assertTrue(t["git"])
        self.assertEqual(t["branch"], "main")
        self.assertIn("sub/b.md", t["files"])
        self.assertIn("new.txt", t["files"])
        self.assertNotIn("ignored.txt", t["files"])
        self.assertEqual(t["status"]["a.py"], "M")
        self.assertEqual(t["status"]["new.txt"], "?")

    def test_tree_of_subfolder_and_plain_folder(self):
        (self.repo / "sub" / "b.md").write_text("# B2\n")
        t = files.tree(str(self.repo / "sub"))
        self.assertEqual(t["files"], ["b.md"])
        self.assertEqual(t["status"], {"b.md": "M"})
        plain = Path(self.tmp.name) / f"plain-{self._testMethodName}"
        (plain / "node_modules").mkdir(parents=True)
        (plain / "node_modules" / "x.js").write_text("")
        (plain / "k.txt").write_text("")
        t = files.tree(str(plain))
        self.assertFalse(t["git"])
        self.assertEqual(t["files"], ["k.txt"])

    # ------------------------------------------------------------------ read / save
    def test_read_kinds(self):
        (self.repo / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00")
        (self.repo / "bin.dat").write_bytes(b"\x00\x01\x02")
        _, f = self.req("GET", f"/api/file?path={self.p('a.py')}")
        self.assertEqual(f["kind"], "text")
        self.assertTrue(f["text"].startswith("one\n"))
        self.assertEqual(files.read(self.p("img.png"))["kind"], "image")
        self.assertEqual(files.read(self.p("bin.dat"))["kind"], "binary")
        self.assertEqual(self.req("GET", f"/api/file?path={self.p('nope')}")[0], 404)
        self.assertEqual(self.req("GET", "/api/file?path=relative.txt")[0], 400)

    def test_empty_save_needs_consent(self):
        f = files.read(self.p("a.py"))
        code, r = self.req("POST", "/api/file", {"path": self.p("a.py"), "text": "", "base_hash": f["hash"]})
        self.assertEqual(code, 409)
        self.assertTrue(r["empty"])
        self.assertTrue((self.repo / "a.py").read_text().startswith("one"))  # untouched
        code, _ = self.req("POST", "/api/file", {"path": self.p("a.py"), "text": "", "base_hash": f["hash"], "allow_empty": True})
        self.assertEqual(code, 200)
        self.assertEqual((self.repo / "a.py").read_text(), "")
        code, _ = self.req("POST", "/api/file", {"path": self.p("new-empty.txt"), "text": ""})  # a new empty file is fine
        self.assertEqual(code, 200)

    def test_page_log(self):
        code, _ = self.req("POST", "/api/log", {"kind": "slow", "op": "open file", "ms": 512, "fetchMs": 400})
        self.assertEqual(code, 200)
        log = self.desk.state_dir / "page.log"
        entry = json.loads(log.read_text().splitlines()[-1])
        self.assertEqual((entry["kind"], entry["op"], entry["ms"]), ("slow", "open file", 512))
        self.assertIn("t", entry)

    def test_save_and_conflict(self):
        f = files.read(self.p("a.py"))
        code, r = self.req("POST", "/api/file", {"path": self.p("a.py"), "text": "mine\n", "base_hash": f["hash"]})
        self.assertEqual(code, 200)
        self.assertEqual((self.repo / "a.py").read_text(), "mine\n")
        # Someone else (the agent) writes; saving from the old base must be refused with the current text.
        (self.repo / "a.py").write_text("theirs\n")
        code, r = self.req("POST", "/api/file", {"path": self.p("a.py"), "text": "mine again\n", "base_hash": r["hash"]})
        self.assertEqual(code, 409)
        self.assertEqual(r["current"]["text"], "theirs\n")
        self.assertEqual((self.repo / "a.py").read_text(), "theirs\n")
        code, _ = self.req("POST", "/api/file", {"path": self.p("a.py"), "text": "forced\n", "base_hash": "x", "force": True})
        self.assertEqual(code, 200)
        self.assertEqual((self.repo / "a.py").read_text(), "forced\n")

    def test_save_keeps_mode_and_leaves_no_temp(self):
        script = self.repo / "run.sh"
        script.write_text("#!/bin/sh\n")
        script.chmod(0o755)
        files.save(str(script), "#!/bin/sh\necho hi\n", files.read(str(script))["hash"])
        self.assertEqual(script.stat().st_mode & 0o777, 0o755)
        self.assertFalse([n for n in os.listdir(self.repo) if n.endswith(".orbit-tmp")])

    def test_stats(self):
        code, s = self.req("POST", "/api/stats", {"paths": [self.p("a.py"), self.p("gone")]})
        self.assertEqual(code, 200)
        self.assertIn("mtime", s[self.p("a.py")])
        self.assertIsNone(s[self.p("gone")])

    def test_create_rename_trash(self):
        self.assertEqual(self.req("POST", "/api/files/create", {"path": self.p("n/new.txt")})[0], 200)
        self.assertTrue((self.repo / "n" / "new.txt").exists())
        self.assertEqual(self.req("POST", "/api/files/create", {"path": self.p("n/new.txt")})[0], 400)
        self.req("POST", "/api/files/rename", {"path": self.p("n/new.txt"), "new": self.p("n/renamed.txt")})
        self.assertTrue((self.repo / "n" / "renamed.txt").exists())
        with mock.patch("orbit.workspace.files.shutil.which", return_value=None), \
             mock.patch("orbit.workspace.files.Path.home", return_value=Path(self.tmp.name)):
            (Path(self.tmp.name) / ".Trash").mkdir(exist_ok=True)
            files.trash(self.p("n/renamed.txt"))
        self.assertFalse((self.repo / "n" / "renamed.txt").exists())
        self.assertTrue((Path(self.tmp.name) / ".Trash" / "renamed.txt").exists())  # recoverable, not unlinked

    def test_dirlist(self):
        d = files.dirlist(str(self.repo))
        self.assertEqual(d["entries"][0], {"name": "sub", "dir": True})  # folders first
        self.assertNotIn(".git", [e["name"] for e in d["entries"]])

    # ------------------------------------------------------------------ review
    def test_review_lists_changes_and_untracked(self):
        (self.repo / "a.py").write_text("one\nTWO\nthree\nfour\nfive\nsix\nseven\neight\nnine\nTEN\n")
        (self.repo / "fresh.txt").write_text("x\ny\n")
        code, r = self.req("GET", f"/api/review?root={self.repo}")
        self.assertEqual(code, 200)
        by = {f["path"]: f for f in r["files"]}
        self.assertEqual(set(by), {"a.py", "fresh.txt"})
        self.assertEqual(len(by["a.py"]["hunks"]), 2)
        self.assertEqual((by["a.py"]["add"], by["a.py"]["del"]), (2, 2))
        first = by["a.py"]["hunks"][0]["lines"]
        self.assertIn({"t": "-", "text": "two", "old": 2, "new": None}, first)
        self.assertIn({"t": "+", "text": "TWO", "old": None, "new": 2}, first)
        self.assertEqual(by["fresh.txt"]["status"], "?")
        self.assertEqual(by["fresh.txt"]["add"], 2)
        _, s = self.req("GET", f"/api/review?root={self.repo}&summary=1")
        self.assertEqual(s["count"], 2)
        self.assertEqual((s["add"], s["del"]), (4, 2))  # the ± badge: a.py +2 −2, fresh.txt +2 (untracked, whole)

    def test_revert_one_hunk(self):
        (self.repo / "a.py").write_text("one\nTWO\nthree\nfour\nfive\nsix\nseven\neight\nnine\nTEN\n")
        r = files.review(str(self.repo))
        hunks = next(f for f in r["files"] if f["path"] == "a.py")["hunks"]
        code, _ = self.req("POST", "/api/review/revert", {"root": str(self.repo), "path": "a.py", "header": hunks[1]["header"]})
        self.assertEqual(code, 200)
        self.assertEqual((self.repo / "a.py").read_text(), "one\nTWO\nthree\nfour\nfive\nsix\nseven\neight\nnine\nten\n")
        code, err = self.req("POST", "/api/review/revert", {"root": str(self.repo), "path": "a.py", "header": "@@ -9,9 +9,9 @@"})
        self.assertEqual(code, 400)

    def test_review_without_commits_and_outside_git(self):
        fresh = Path(self.tmp.name) / f"nocommit-{self._testMethodName}"
        fresh.mkdir()
        git(fresh, "init", "-q")
        (fresh / "x.txt").write_text("x\n")
        git(fresh, "add", "x.txt")
        r = files.review(str(fresh))
        self.assertEqual([f["path"] for f in r["files"]], ["x.txt"])
        self.assertEqual(r["files"][0]["status"], "A")
        plain = Path(self.tmp.name) / f"plain2-{self._testMethodName}"
        plain.mkdir()
        self.assertFalse(files.review(str(plain))["git"])


for name in [n for n in dir(test_skeleton.SkeletonTest) if n.startswith("test_")]:
    if name not in FilesTest.__dict__:
        setattr(FilesTest, name, None)

if __name__ == "__main__":
    unittest.main()
