# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A headless child that never says ready names why, in its own words, without personal paths.

release-105 (2026-10-23): 1.0.5 builds did not ship ``scripts/angelo/angelo_headless.py``; the child printed that it
could not open the file, but its output was discarded, so every app saw only "it did not start". The child's own
output now goes to an owner-only file in its private folder (removed with it), and a start that fails carries its
last lines, with the private folder, the home folder and the user name replaced, in the error and the launcher's
log. Contract: doc/angelo/external_mcp.rst, "Headless". A real process stands in for Angelo here.
"""

import getpass
import io
import os
import shutil
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.headless_pipe import ChildProcess  # noqa: E402
from angelo_agent.headless_session import (CHILD_OUTPUT, PRIVATE_PREFIX, HeadlessSession,  # noqa: E402
                                           HeadlessUnavailable)

HOME = os.path.expanduser("~")


@unittest.skipIf(sys.platform == "win32", "Headless sessions are macOS and Linux for now")
class ChildOutputTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="hco-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.source = self.root / "scene.blend"
        self.source.write_bytes(b"BLENDER-v502 scene")
        self.seen = {}

    def session(self, printed):
        """A session whose child is a real process that prints ``printed`` to its error stream and exits."""
        def child(command, environ, stderr=None):
            self.seen["stderr"] = stderr
            script = "import sys; sys.stderr.write(%r); sys.exit(1)" % printed
            return ChildProcess([sys.executable, "-I", "-c", script, "{in}", "{out}"], environ, stderr=stderr)
        return HeadlessSession("claude-code", "claude_code", str(self.root / "state"), binary=Path(sys.executable),
                               script="/s/x.py", child_type=child, config_directory=self.root / "no-preferences")

    def test_a_child_that_never_says_ready_names_why_without_personal_paths(self):
        printed = ("Blender 5.2.2 LTS\n"
                   'OSError: Python file "%s/Angelo.app/Contents/Resources/5.2/scripts/angelo/angelo_headless.py" '
                   "could not be opened: No such file or directory\n"
                   "Error: script failed, user %s, exiting.\n" % (HOME, getpass.getuser()))
        log = io.StringIO()
        with redirect_stderr(log), self.assertRaises(HeadlessUnavailable) as raised:
            self.session(printed).open(str(self.source), ("read", "edit"))
        message = str(raised.exception)
        self.assertIn("it did not start", message)
        self.assertIn("scripts/angelo/angelo_headless.py\" could not be opened", message)
        self.assertIn("~/Angelo.app", message)
        for personal in (HOME, getpass.getuser()):
            self.assertNotIn(personal, message)
            self.assertNotIn(personal, log.getvalue())
        self.assertIn("the headless child did not start; its last output", log.getvalue())
        self.assertTrue(str(self.seen["stderr"]).endswith(CHILD_OUTPUT))
        self.assertFalse(Path(self.seen["stderr"]).parent.exists(), "removed with the private folder")

    def test_the_output_file_is_owner_only_new_and_never_through_a_link(self):
        folder = Path(tempfile.mkdtemp(prefix=PRIVATE_PREFIX))
        self.addCleanup(shutil.rmtree, folder, True)
        path = folder / CHILD_OUTPUT
        child = ChildProcess([sys.executable, "-I", "-c", "import sys; sys.stderr.write('x' * 9000 + '\\nlast\\n')",
                              "{in}", "{out}"], {}, stderr=path)
        child.process.wait(30)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(child.last_output()[-1], "last")
        self.assertLessEqual(len(child.last_output()[0]), 240)
        child.close()
        target = folder / "elsewhere"
        target.write_text("kept")
        link = folder / "link"
        link.symlink_to(target)
        with self.assertRaises(OSError):
            ChildProcess([sys.executable, "-c", "pass", "{in}", "{out}"], {}, stderr=link)
        self.assertEqual(target.read_text(), "kept")


if __name__ == "__main__":
    unittest.main()
