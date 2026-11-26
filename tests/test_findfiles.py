"""⌘O → All Files (orbit/workspace/findfiles.py, GET /api/findfiles): file names in every open session's folder and
the home, every term in the path below its root, names first, then shorter paths. The home here is a temp folder
(ORBIT_FIND_HOME): never the real one, and no Spotlight.

Run: uv run python -m unittest discover -s tests -v
"""

import os
from pathlib import Path

import test_skeleton
from orbit.workspace import findfiles


def touch(p: Path, text: str = "x") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


class FindFilesTest(test_skeleton.SkeletonTest):
    def setUp(self):
        self.home = Path(self.tmp.name) / "home"
        for rel in ("proj/plan.md", "planner.txt", "docs/deep/very/long/way/plan.md", ".hidden/plan.md", "Library/plan.md",
                    "app/node_modules/x/plan.md", "repo/src/plan.py", "notes/other.md"):
            touch(self.home / rel)
        touch(Path(self.work) / "src" / "plan_view.py")
        os.environ[findfiles.HOME_ENV] = str(self.home)
        findfiles._TREES.clear()

    def tearDown(self):
        os.environ.pop(findfiles.HOME_ENV, None)

    def test_names_first_then_short_paths_and_the_skips(self):
        r = findfiles.find("plan", [self.work])
        rels = [(x["where"], x["rel"]) for x in r["files"]]
        self.assertEqual(rels[0], ("home", "planner.txt"))  # (in its name, the shortest path)
        self.assertIn(("session", "src/plan_view.py"), rels)
        self.assertIn(("home", "docs/deep/very/long/way/plan.md"), rels)
        for gone in (".hidden/plan.md", "Library/plan.md", "app/node_modules/x/plan.md"):
            self.assertNotIn(("home", gone), rels)  # (hidden folders, ~/Library, dependencies)
        self.assertNotIn("notes/other.md", [x["rel"] for x in r["files"]])
        names = [sum(t in os.path.basename(x["rel"]).lower() for t in ["plan"]) for x in r["files"]]
        self.assertEqual(names, sorted(names, reverse=True))
        self.assertFalse(r["truncated"])

    def test_every_term_in_the_path(self):
        r = findfiles.find("deep PLAN", [])
        self.assertEqual([x["rel"] for x in r["files"]], ["docs/deep/very/long/way/plan.md"])
        self.assertEqual(findfiles.find("  ", [])["files"], [])

    def test_a_session_folder_in_the_home_is_searched_once(self):
        repo = str(self.home / "repo")
        r = findfiles.find("plan.py", [repo, repo + "/src"])  # (a folder inside another: one listing)
        self.assertEqual([(x["where"], x["path"]) for x in r["files"]], [("session", os.path.realpath(repo) + "/src/plan.py")])
        self.assertEqual(findfiles.outermost([repo + "/src", repo, "/nope/a", "/nope/a/b"]), ["/nope/a", os.path.realpath(repo)])

    def test_the_walk_stops_at_its_budget(self):
        old = findfiles.WALK_FILES
        findfiles.WALK_FILES = 1
        try:
            r = findfiles.find("plan", [])
        finally:
            findfiles.WALK_FILES = old
        self.assertTrue(r["truncated"])
        r = findfiles.find("plan", [], limit=2)
        self.assertEqual((len(r["files"]), r["truncated"]), (2, True))

    def test_the_route(self):
        self.new_session("finder")  # (its folder: self.work)
        code, d = self.req("GET", "/api/findfiles?q=plan_view")
        self.assertEqual(code, 200, d)
        self.assertEqual([(x["where"], x["rel"]) for x in d["files"]], [("session", "src/plan_view.py")])
        self.assertEqual(d["unreachable"], [])
        code, d = self.req("GET", "/api/findfiles?q=planner&local=1")
        self.assertEqual([x["rel"] for x in d["files"]], ["planner.txt"])
        self.assertEqual(self.req("GET", "/api/findfiles?q=")[1]["files"], [])


def load_tests(loader, tests, pattern):  # (its own tests only: not the skeleton's again, which it builds on)
    import unittest
    return unittest.TestSuite(FindFilesTest(n) for n in loader.getTestCaseNames(FindFilesTest) if n in vars(FindFilesTest))


if __name__ == "__main__":
    import unittest
    unittest.main()
