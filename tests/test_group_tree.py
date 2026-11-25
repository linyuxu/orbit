"""Groups as a tree (orbit/desk/groups.py): subgroups up to three levels, names unique among siblings, moves among
siblings, nesting in and out (never into itself, never too deep), deleting a parent moves what it held up a level.

Run: uv run python -m unittest discover -s tests -v
"""

import json

import test_skeleton
from orbit.desk.groups import tidy


class GroupTreeTest(test_skeleton.SkeletonTest):
    def setUp(self):
        for g in [g for g in self.groups() if g["id"] not in ("main", "projects", "personal")]:
            self.req("POST", f"/api/groups/{g['id']}/delete")

    def groups(self):
        return self.req("GET", "/api/sessions")[1]["groups"]

    def tree(self):
        """Each group as 'A/B/C', in the tree's order (a group, then its subgroups; siblings in list order)."""
        gs = self.groups()
        out = []
        def walk(parent, prefix):
            for g in gs:
                if g.get("parent") == parent:
                    out.append(prefix + g["name"])
                    walk(g["id"], prefix + g["name"] + "/")
        walk(None, "")
        return out

    def make(self, name, parent=""):
        code, g = self.req("POST", "/api/groups", {"name": name, **({"parent": parent} if parent else {})})
        self.assertEqual(code, 200, g)
        return g["id"]

    def test_subgroups_three_levels_and_sibling_names(self):
        work = self.make("Work")
        orbit = self.make("Orbit", work)
        rel = self.make("Release", orbit)
        code, e = self.req("POST", "/api/groups", {"name": "Deeper", "parent": rel})
        self.assertEqual(code, 400)
        self.assertIn("3 levels", e["error"])
        self.make("Release", work)  # (another parent: the same name is fine)
        code, e = self.req("POST", "/api/groups", {"name": "re lease", "parent": orbit})
        self.assertEqual((code, "already" in e["error"]), (400, True))  # (a sibling has it, spaces and case ignored)
        self.assertEqual(self.req("POST", "/api/groups", {"name": "Favorites", "parent": work})[0], 200)  # (only the top's)
        self.assertEqual(self.req("POST", "/api/groups", {"name": "a/b"})[0], 400)
        self.assertEqual(self.tree(), ["Main", "Projects", "Personal", "Work", "Work/Orbit", "Work/Orbit/Release",
                                       "Work/Release", "Work/Favorites"])
        self.assertEqual(self.req("POST", "/api/groups", {"name": "X", "parent": "nope"})[0], 404)

    def test_move_among_siblings_and_nest(self):
        work = self.make("Work")
        a, b = self.make("A", work), self.make("B", work)
        self.req("POST", f"/api/groups/{b}/move", {"delta": -1})
        self.assertEqual([p for p in self.tree() if p.startswith("Work/")], ["Work/B", "Work/A"])
        c = self.make("C", work)
        self.req("POST", f"/api/groups/{c}/move", {"delta": -2})  # (two places at once: a move, not a swap)
        self.assertEqual([p for p in self.tree() if p.startswith("Work/")], ["Work/C", "Work/B", "Work/A"])
        self.req("POST", f"/api/groups/{c}/delete")
        self.req("POST", f"/api/groups/{work}/move", {"delta": -1})  # (a top group passes a top group, not a child)
        self.assertEqual(self.tree()[:3], ["Main", "Projects", "Work"])
        code, g = self.req("POST", f"/api/groups/{a}/nest", {"parent": b})  # (Tab: into the sibling before it)
        self.assertEqual((code, g["parent"]), (200, b))
        self.assertIn("Work/B/A", self.tree())
        self.assertEqual(self.req("POST", f"/api/groups/{b}/nest", {"parent": a})[0], 400)  # (into its own subgroup)
        self.assertEqual(self.req("POST", f"/api/groups/{work}/nest", {"parent": "personal"})[0], 400)  # (4 levels)
        code, g = self.req("POST", f"/api/groups/{a}/nest", {"parent": work, "after": b})  # (⇧Tab: out, after its parent)
        self.assertEqual([p for p in self.tree() if p.startswith("Work/")], ["Work/B", "Work/A"])
        self.req("POST", f"/api/groups/{a}/nest", {"parent": ""})
        self.assertEqual(self.tree()[-1], "A")
        self.assertNotIn("parent", next(g for g in self.groups() if g["id"] == a))

    def test_delete_moves_what_it_held_up(self):
        work = self.make("Work")
        orbit = self.make("Orbit", work)
        rel = self.make("Release", orbit)
        self.make("Release", work)
        s = self.new_session("in orbit")
        self.req("POST", f"/api/sessions/{s['id']}/update", {"group": orbit})
        self.req("POST", f"/api/groups/{orbit}/delete")
        self.assertEqual(self.desk.get(s["id"]).group, work)  # (its session: up to Work)
        tree = self.tree()
        self.assertIn("Work/Release (Orbit)", tree)  # (its subgroup up a level; a sibling had the name)
        self.assertEqual(next(g for g in self.groups() if g["id"] == rel)["parent"], work)
        self.req("POST", f"/api/groups/{work}/delete")
        self.assertIn("Release (Orbit)", self.tree())  # (a top group's subgroups go to the top)
        self.assertEqual(self.desk.get(s["id"]).group, "main")  # (a top group's sessions: the first group)

    def test_a_broken_tree_is_mended_on_load(self):
        gs = tidy([{"id": "a", "name": "A", "parent": "b"}, {"id": "b", "name": "B", "parent": "a"},
                   {"id": "c", "name": "C", "parent": "gone"}, {"id": "d", "name": "D", "parent": "d"},
                   {"id": "e", "name": "E"}, {"id": "f", "name": "F", "parent": "e"}, {"id": "g", "name": "G", "parent": "f"},
                   {"id": "h", "name": "H", "parent": "g"}, {"id": "x", "name": "X", "extra": 1}])
        by = {g["id"]: g.get("parent") for g in gs}
        self.assertTrue(by["a"] is None or by["b"] is None)  # (the cycle is cut)
        self.assertEqual((by["c"], by["d"], by["f"], by["g"], by["h"]), (None, None, "e", "f", None))  # (4 deep: cut)
        self.assertNotIn("extra", json.dumps(gs))


if __name__ == "__main__":
    import unittest
    unittest.main()
