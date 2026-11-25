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

    # ------------------------------------------------------------------ a group's look, its home, its pins
    def test_style_and_home_layout(self):
        work = self.make("Work")
        code, g = self.req("POST", f"/api/groups/{work}/style", {"icon": "🚀", "color": "#2A78D6", "cover": "/tmp/c.png"})
        self.assertEqual((code, g["icon"], g["color"], g["cover"]), (200, "🚀", "#2a78d6", "/tmp/c.png"))
        for bad in ({"color": "blue"}, {"icon": "<script>"}, {"cover": "relative.png"}, {"icon": "img:x.png"}):
            self.assertEqual(self.req("POST", f"/api/groups/{work}/style", bad)[0], 400, bad)
        self.req("POST", f"/api/groups/{work}/style", {"cover": ""})
        self.assertNotIn("cover", next(x for x in self.groups() if x["id"] == work))
        ws = [{"id": "sessions", "kind": "sessions", "span": 6}, {"id": "n1", "kind": "md", "path": "/tmp/a.md", "span": 3, "title": " About  "},
              {"id": "pins", "kind": "pins", "span": 5, "hidden": True}]
        code, g = self.req("POST", f"/api/groups/{work}/home", {"widgets": ws})
        self.assertEqual(code, 200, g)
        self.assertEqual(g["home"]["widgets"][1], {"id": "n1", "kind": "md", "span": 3, "path": "/tmp/a.md", "title": "About"})
        self.assertEqual(g["home"]["widgets"][2], {"id": "pins", "kind": "pins", "span": 6, "hidden": True})  # (an odd width: the row)
        for bad in ([{"id": "x", "kind": "nope"}], [{"id": "a", "kind": "md"}], [{"id": "a", "kind": "tabs"}, {"id": "a", "kind": "pins"}]):
            self.assertEqual(self.req("POST", f"/api/groups/{work}/home", {"widgets": bad})[0], 400, bad)
        looked = [{"id": "sessions", "kind": "sessions", "span": 3, "icon": "🚀", "color": "#2A78D6", "bg": "/tmp/b.png", "title": " Now "},
                  {"id": "pins", "kind": "pins", "icon": "img:/tmp/i.png", "color": "", "bg": ""}]
        code, g = self.req("POST", f"/api/groups/{work}/home", {"widgets": looked})  # (a card's own icon, tint, picture, title)
        self.assertEqual(code, 200, g)
        self.assertEqual(g["home"]["widgets"], [{"id": "sessions", "kind": "sessions", "span": 3, "icon": "🚀", "color": "#2a78d6", "bg": "/tmp/b.png", "title": "Now"},
                                                {"id": "pins", "kind": "pins", "span": 6, "icon": "img:/tmp/i.png"}])
        code, g = self.req("POST", f"/api/groups/{work}/home", {"widgets": [{"id": "a", "kind": "tabs", "h": 240.4}, {"id": "b", "kind": "pins", "h": 0}]})
        self.assertEqual((code, g["home"]["widgets"][0].get("h"), "h" in g["home"]["widgets"][1]), (200, 240, False))  # (resized; 0: natural)
        for bad in ({"icon": "<b>"}, {"color": "red"}, {"bg": "rel.png"}, {"h": 20}, {"h": "big"}, {"h": True}):
            code, e = self.req("POST", f"/api/groups/{work}/home", {"widgets": [{"id": "a", "kind": "tabs", **bad}]})
            self.assertEqual(code, 400, bad)
            self.assertIn("widget a", e["error"])
        self.req("POST", f"/api/groups/{work}/home", {"widgets": None})
        self.assertNotIn("home", next(x for x in self.groups() if x["id"] == work))
        reloaded = tidy(self.groups())  # (kept as it is on load)
        self.assertEqual(next(x for x in reloaded if x["id"] == work)["icon"], "🚀")

    def test_pins_newest_first_and_unpin(self):
        work = self.make("Work")
        orbit = self.make("Orbit", work)
        s = self.new_session("pinner")
        a = self.req("POST", f"/api/groups/{orbit}/pin", {"text": "Ship on **Friday**", "by": "pinner", "sid": s["id"], "cid": "3"})[1]
        b = self.req("POST", f"/api/groups/{orbit}/pin", {"text": "Use the v2 API"})[1]
        self.assertEqual(a["path"], b["path"])
        self.assertTrue(a["path"].endswith("Groups/Work/Orbit/Pins.md"), a["path"])
        text = open(a["path"]).read()
        self.assertTrue(text.startswith("# Pins\n\n<!-- pin:" + b["id"]), text)  # (the newest on top)
        self.assertIn(f"· pinner · [source](orbit://session/{s['id']}#3)\n\nShip on **Friday**", text)
        self.req("POST", f"/api/groups/{work}/rename", {"name": "Job"})  # (renamed: its pins stay where they are)
        c = self.req("POST", f"/api/groups/{orbit}/pin", {"text": "third"})[1]
        self.assertEqual(c["path"], a["path"])
        self.assertEqual(self.req("POST", f"/api/groups/{orbit}/unpin", {"id": a["id"]})[0], 200)
        text = open(a["path"]).read()
        self.assertNotIn("Friday", text)
        self.assertIn("Use the v2 API", text)
        self.assertIn("third", text)
        self.assertEqual(self.req("POST", f"/api/groups/{orbit}/unpin", {"id": a["id"]})[0], 400)
        self.assertEqual(self.req("POST", f"/api/groups/{orbit}/pin", {"text": "  "})[0], 400)

    def test_orbit_agent_pin(self):
        import os, subprocess, sys
        work = self.make("Work")
        s = self.new_session("pinner")
        self.req("POST", f"/api/sessions/{s['id']}/update", {"group": work})
        env = {**os.environ, "ORBIT_PORT": str(self.port), "ORBIT_SESSION": s["id"]}
        run = lambda *a, **k: subprocess.run([sys.executable, "-m", "orbit.cli.agent", *a], env=env, capture_output=True, text=True, timeout=30, **k)
        r = run("pin", "We use **Postgres 16**")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pinned to the home of Work", r.stdout)
        path = next(g for g in self.groups() if g["id"] == work)["pins"]
        self.assertIn(f"pinner · [source](orbit://session/{s['id']})\n\nWe use **Postgres 16**", open(path).read())
        r = run("pin", "-", "--group", "Main", input="from stdin\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("from stdin", open(next(g for g in self.groups() if g["id"] == "main")["pins"]).read())
        self.assertNotEqual(run("pin", "x", "--group", "Nowhere").returncode, 0)


if __name__ == "__main__":
    import unittest
    unittest.main()
