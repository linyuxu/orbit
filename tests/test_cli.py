"""cli/: the `orbit` and `orbit-agent` launchers, and `orbit tools install` (Orbit's tools for every harness, any terminal)."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import test_skeleton

ROOT = Path(__file__).resolve().parent.parent


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / ".claude").mkdir()
        (self.home / ".codex").mkdir()  # no ~/.gemini: agy is not set up here

    def tearDown(self):
        self.tmp.cleanup()

    def orbit(self, *args):
        env = {**os.environ, "HOME": str(self.home), "PATH": "/usr/bin:/bin"}
        return subprocess.run([str(ROOT / "cli" / "orbit"), *args], env=env, capture_output=True, text=True, timeout=60)

    def test_install_status_uninstall(self):
        r = self.orbit("tools", "install")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        agent = self.home / ".local" / "bin" / "orbit-agent"
        self.assertEqual(Path(os.path.realpath(agent)), ROOT / "cli" / "orbit-agent")
        self.assertTrue((self.home / ".claude" / "skills" / "orbit-agents" / "SKILL.md").is_file())
        self.assertTrue((self.home / ".codex" / "skills" / "orbit-agents" / "SKILL.md").is_file())
        self.assertFalse((self.home / ".gemini").exists())  # a harness that is not set up is skipped
        self.assertIn("not on PATH", r.stdout)
        # the linked launcher finds the repository from anywhere
        out = subprocess.run([str(agent), "--help"], capture_output=True, text=True, cwd="/", timeout=60,
                             env={**os.environ, "HOME": str(self.home)})
        self.assertIn("sessions", out.stdout)
        self.assertEqual(self.orbit("tools", "install").stdout.count("ok "), 4)  # idempotent
        self.assertIn("installed", self.orbit("tools", "status").stdout)
        self.orbit("tools", "uninstall")
        self.assertFalse(agent.exists() or agent.is_symlink())
        self.assertFalse((self.home / ".claude" / "skills" / "orbit-agents").exists())

    def test_never_replaces_something_else(self):
        (self.home / ".local" / "bin").mkdir(parents=True)
        mine = self.home / ".local" / "bin" / "orbit-agent"
        mine.write_text("#!/bin/sh\necho mine\n")
        r = self.orbit("tools", "install")
        self.assertEqual(r.returncode, 1)
        self.assertIn("KEEP", r.stdout)
        self.assertEqual(mine.read_text(), "#!/bin/sh\necho mine\n")
        self.orbit("tools", "uninstall")
        self.assertTrue(mine.exists())  # not ours: left alone


class TerminalAgentTest(test_skeleton.SkeletonTest):
    def test_a_terminal_agent_signs_with_its_folder(self):
        s = self.new_session("target")
        env = {**os.environ, "ORBIT_PORT": str(self.port), "ORBIT_AGENT_NAME": "~/proj (claude in iTerm)"}
        env.pop("ORBIT_SESSION", None)
        r = subprocess.run([str(ROOT / "cli" / "orbit-agent"), "send", "target", "hello", "--wait"], env=env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("(from a terminal session in ~/proj (claude in iTerm); it cannot be mentioned back) hello", r.stdout)


for name in [n for n in dir(test_skeleton.SkeletonTest) if n.startswith("test_")]:
    if name not in TerminalAgentTest.__dict__:
        setattr(TerminalAgentTest, name, None)
