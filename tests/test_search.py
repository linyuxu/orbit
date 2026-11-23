"""Build step 5 (Search): ⌘⇧F history across every session (open and closed) and the notes.

Run: uv run python -m unittest discover -s tests -v
"""

import os
import time
import unittest
from pathlib import Path

import test_skeleton
from orbit.workspace import notes


class SearchTest(test_skeleton.SkeletonTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        os.environ.pop("ORBIT_VAULT", None)
        cls.vault = notes.configure(str(Path(cls.tmp.name) / "search-data"))

    def search(self, q):
        code, d = self.req("GET", "/api/search?q=" + q.replace(" ", "%20"))
        self.assertEqual(code, 200)
        return d

    def test_finds_requests_and_answers_in_open_and_closed_sessions(self):
        a = self.new_session("alpha")
        self.req("POST", f"/api/sessions/{a['id']}/send", {"text": "zebra migration plan"})
        self.wait_card(a["id"], 1, ("completed",))
        b = self.new_session("beta")
        self.req("POST", f"/api/sessions/{b['id']}/send", {"text": "other zebra topic"})
        self.wait_card(b["id"], 1, ("completed",))
        self.req("POST", f"/api/sessions/{b['id']}/close")
        d = self.search("zebra")
        hits = {(r["name"], r["cid"]) for r in d["cards"]}
        self.assertIn(("alpha", 1), hits)
        self.assertIn(("beta", 1), hits)  # closed sessions are searched too
        self.assertTrue(next(r for r in d["cards"] if r["name"] == "beta")["closed"])
        # every term must match; the answer ("answer: ...") counts
        d = self.search("ZEBRA migration")
        self.assertEqual([(r["name"], r["where"]) for r in d["cards"]], [("alpha", "request")])

    def test_history_survives_a_restart(self):
        s = self.new_session("gamma")
        self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "quokka facts"})
        self.wait_card(s["id"], 1, ("completed",))
        type(self)._unserve()
        type(self)._serve()
        d = self.search("quokka")
        self.assertEqual([r["name"] for r in d["cards"]], ["gamma"])  # rebuilt from the harness transcript

    def test_notes(self):
        (self.vault / "Pandas.md").write_text("# Pandas\n\nline one\nThe red panda eats bamboo.\n")
        time.sleep(0.01)
        (self.vault / "Other.md").write_text("# Other\n\nbamboo only\n")
        d = self.search("panda bamboo")
        self.assertEqual([n["title"] for n in d["notes"]], ["Pandas"])
        n = d["notes"][0]
        self.assertEqual((n["line"], n["line_text"]), (4, "The red panda eats bamboo."))
        self.assertEqual([n["title"] for n in self.search("bamboo")["notes"]], ["Other", "Pandas"])  # newest first

    def test_empty_query_and_snippets(self):
        self.assertEqual(self.search(""), {"q": "", "cards": [], "notes": []})
        long = "word " * 40 + "needle " + "word " * 40
        snip = notes.snippet(long, "needle")
        self.assertTrue(snip.startswith("…") and snip.endswith("…"))
        self.assertIn("needle", snip)
        self.assertLess(len(snip), 200)


for name in [n for n in dir(test_skeleton.SkeletonTest) if n.startswith("test_")]:
    if name not in SearchTest.__dict__:
        setattr(SearchTest, name, None)

if __name__ == "__main__":
    unittest.main()
