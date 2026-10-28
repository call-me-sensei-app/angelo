# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Break-it r2m4c round 10 on owner files and held handles.

* M-3: the owner-file read never blocks and reads only a regular file: a FIFO, a folder or a link named like it is no
  owner file, and a listing beside one finishes.
* M-1: an identity whose handle could not be taken is never trusted, and a dead identity leaves a workspace's cache
  for good; a platform without handles is a separate case.
* M-2: on Linux a held folder is gone only when its link count is 0 (``fstat``), never by a path's " (deleted)".
Synthetic folders only.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ADDONS = Path(__file__).resolve().parents[3] / "scripts/addons_core"
sys.path.insert(0, str(ADDONS))
from angelo_agent import private_roots  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402

LIST = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from angelo_agent.files import FileWorkspace
files = FileWorkspace(Path(sys.argv[2]), excluded=[])
print(json.dumps([e["name"] for e in files.execute("angelo_list_files", {"path": "."})["entries"]]))
"""


class Fixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="angelo-private-handles-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()


@unittest.skipIf(os.name == "nt", "FIFOs and POSIX handles")
class OwnerFileTests(Fixture):
    def test_a_fifo_folder_or_link_named_like_an_owner_file_is_none_and_never_blocks(self):
        for kind in ("fifo", "folder", "link"):
            with self.subTest(kind=kind):
                project = self.base / kind
                (project / "ordinary").mkdir(parents=True)
                marker = project / "ordinary" / private_roots.OWNER
                if kind == "fifo":
                    os.mkfifo(marker)
                elif kind == "folder":
                    marker.mkdir()
                else:
                    (self.base / ("target-" + kind)).write_text("token")
                    marker.symlink_to(self.base / ("target-" + kind))
                result = subprocess.run([sys.executable, "-I", "-c", LIST, str(ADDONS), str(project)],
                                        capture_output=True, text=True, timeout=30)   # Blocked on a FIFO: times out.
                self.assertEqual(result.returncode, 0, result.stderr[-1500:])
                self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1]), ["ordinary"])


@unittest.skipIf(os.name == "nt", "POSIX handles")
class HandleTests(Fixture):
    def test_an_identity_whose_handle_cannot_be_taken_is_never_trusted(self):
        folder = self.base / "root"
        folder.mkdir()
        real = os.open

        def refusing(path, flags, *arguments, **options):
            if Path(path) == folder:
                raise PermissionError("synthetic")
            return real(path, flags, *arguments, **options)
        with patch.object(private_roots.os, "open", side_effect=refusing):
            self.assertIsNone(private_roots.hold(folder))
        info = os.stat(folder)
        self.assertIs(private_roots.alive((info.st_dev, info.st_ino) + ("never-held",)), False)

    def test_a_dead_identity_leaves_the_workspace_cache_for_good(self):
        root = self.base / "project" / "custom-root"
        root.mkdir(parents=True)
        files = FileWorkspace(self.base / "project", excluded=[root])
        identity = (os.stat(root).st_dev, os.stat(root).st_ino)
        self.assertIn(identity, files.known)
        shutil.rmtree(root)
        files._private()
        self.assertNotIn(identity, files.known)

    def test_on_linux_removal_is_decided_by_the_link_count_alone(self):
        gone = getattr(private_roots, "_handle_gone", None)
        self.assertIsNotNone(gone, "a removal decision from fstat")
        with patch.object(private_roots.sys, "platform", "linux"), \
                patch.object(private_roots.os, "readlink", side_effect=AssertionError("never the path")):
            for links, expected in ((0, True), (2, False)):
                with patch.object(private_roots.os, "fstat", return_value=SimpleNamespace(st_nlink=links)):
                    self.assertIs(gone(3, (1, 2)), expected)
            with patch.object(private_roots.os, "fstat", side_effect=OSError("uncertain")):
                self.assertIs(gone(3, (1, 2)), False, "an uncertain lookup keeps ownership")


if __name__ == "__main__":
    unittest.main()
