"""`/` in the input: skills and commands an agent in a folder can run; Orbit's own skills reach any harness."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orbit.desk import session as desk
from orbit.workspace import skills


class SkillsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.folder = Path(self.tmp.name) / "repo"
        (self.folder / ".claude" / "skills" / "review").mkdir(parents=True)
        (self.folder / ".claude" / "skills" / "review" / "SKILL.md").write_text(
            "---\nname: review\ndescription: >-\n  Review the diff\n  for bugs\n---\n# Review\n")
        (self.folder / ".claude" / "commands" / "git").mkdir(parents=True)
        (self.folder / ".claude" / "commands" / "git" / "ship.md").write_text("Ship the branch: test, push, open a PR\n")
        (self.home / ".claude" / "skills" / "review").mkdir(parents=True)
        (self.home / ".claude" / "skills" / "review" / "SKILL.md").write_text("---\nname: review\ndescription: user one\n---\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_folder_user_and_orbit_skills(self):
        with mock.patch.object(Path, "home", return_value=self.home):
            got = {s["name"]: s for s in skills.list_skills(str(self.folder), "claude")}
        self.assertEqual(got["review"]["source"], "folder")  # the folder's own wins over the user's
        self.assertEqual(got["review"]["description"], "Review the diff for bugs")  # a folded value
        self.assertEqual(got["git:ship"]["kind"], "command")
        self.assertIn("orbit-agents", got)  # Orbit's own, for every harness
        with mock.patch.object(Path, "home", return_value=self.home):
            self.assertEqual([s["name"] for s in skills.list_skills(str(self.folder), "agy")], ["orbit-agents"])

    def test_orbit_skill_hint(self):
        hint = desk.orbit_skill_hint("/orbit-agents ask @@Backend for the schema")
        self.assertIn(str(desk.ORBIT_SKILLS / "orbit-agents" / "SKILL.md"), hint)
        self.assertEqual(desk.orbit_skill_hint("/review this"), "")  # the harness's own skill: left to it
        self.assertEqual(desk.orbit_skill_hint("no slash"), "")


if __name__ == "__main__":
    unittest.main()
