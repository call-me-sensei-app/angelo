# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Adversarial tests of the sealed bundle's bytecode cache retention.

Pins of verified behaviour, and a regression test (``test_a_linked_cache_root_never_deletes_outside_it``) for a
retention that followed a symbolic link at the cache root and removed old folders in the link's target. Cases that
need a child interpreter of a built app are not covered here.
"""

import io
import os
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import pycache_retention  # noqa: E402

DAY = 86400


def age(path, days):
    then = time.time() - days * DAY
    os.utime(path, (then, then))


class RetentionReviewTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="angelo-retention-review-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.cachedir = self.base / "Caches" / "Angelo"
        self.root = self.cachedir / "pycache"
        self.documents = self.base / "Documents"
        (self.documents / "Old Project").mkdir(parents=True)
        (self.documents / "Old Project" / "scene.blend").write_bytes(b"BLENDER")
        age(self.documents / "Old Project", 30)

    def build(self, name, used_days_ago):
        folder = self.root / name
        (folder / "Applications").mkdir(parents=True)
        mark = folder / pycache_retention.LAST_USED
        mark.touch()
        age(mark, used_days_ago)
        return folder

    # -- Regression ----------------------------------------------------------------------------------------------

    def test_a_linked_cache_root_never_deletes_outside_it(self):
        """``<cachedir>/pycache`` is a symbolic link (a person moved the cache, or anything else put one there):
        retention must not remove anything in the link's target. Before the fix ``root.iterdir()`` followed the
        link and every folder in the target untouched for a week (unmarked: its own mtime) was removed."""
        self.cachedir.mkdir(parents=True)
        os.symlink(self.documents, self.root)
        current = self.root / "62eb10e43a0"
        own = pycache_retention.own_cache(str(current), str(self.cachedir) + os.sep)
        self.assertIsNotNone(own)  # The prefix is Angelo's default one: retention runs.
        with redirect_stdout(io.StringIO()):
            pycache_retention._run(*own)
        self.assertTrue((self.documents / "Old Project" / "scene.blend").exists(),
                        "retention removed %s through the linked cache root" % (self.documents / "Old Project"))

    # -- Pins of verified behaviour ------------------------------------------------------------------------------

    def test_a_linked_build_folder_and_links_inside_one_are_not_followed(self):
        """Changed with the fix (was: the old folder removed, the link inside it not followed): a build folder
        holding a link is not bytecode alone, so it is left as it is."""
        current = self.build("62eb10e43a0", 0)
        os.symlink(self.documents, self.root / "0123456789a")      # A link in place of a build folder.
        old = self.build("afaca1a036c", 30)
        os.symlink(self.documents, old / "Applications" / "into-documents")  # A link inside an old build folder.
        removed = pycache_retention.prune(self.root, current)
        self.assertEqual(removed, [])
        self.assertTrue(old.is_dir())
        self.assertTrue((self.root / "0123456789a").is_symlink())
        self.assertTrue((self.documents / "Old Project" / "scene.blend").exists())
        self.assertTrue(current.is_dir())

    def test_a_build_folder_that_is_a_file_stops_quietly(self):
        self.root.mkdir(parents=True)
        (self.root / "62eb10e43a0").write_text("not a folder")
        old = self.build("afaca1a036c", 30)
        output = io.StringIO()
        with redirect_stdout(output):
            pycache_retention._run(self.root, self.root / "62eb10e43a0")
        self.assertIn("retention skipped", output.getvalue())
        self.assertTrue(old.is_dir())  # Nothing removed when the current folder cannot be marked.

    def test_prefixes_outside_the_own_root_are_never_managed(self):
        cachedir = str(self.cachedir) + os.sep
        root = str(self.root)
        for prefix in (str(self.documents), root, root + os.sep, root + "/x/..", root + "/x/../..",
                       root + "/a/b", "pycache/abc", "relative", str(self.cachedir / "pycache2" / "abc")):
            self.assertIsNone(pycache_retention.own_cache(prefix, cachedir), prefix)
        # A path that leaves and re-enters the root still names a folder inside it, which is safe to manage.
        self.assertEqual(pycache_retention.own_cache(root + "/x/../abc", cachedir)[1].name, "abc")

    def test_user_data_in_the_root_is_never_removed(self):
        """Changed with the fix (was: any folder in the root went once a week old): only a folder named like a build
        key and holding bytecode alone is removed, so a person's folder put there stays."""
        current = self.build("62eb10e43a0", 0)
        mine = self.root / "My renders"
        mine.mkdir()
        (mine / "final.png").write_bytes(b"PNG")
        age(mine, 30)
        self.assertEqual(pycache_retention.prune(self.root, current), [])
        self.assertTrue((mine / "final.png").exists())

    def test_a_running_build_older_than_a_week_loses_its_cache(self):
        """The only protection of another build's folder is its mark, written once a minute after a window session
        starts: a session of an older build left open for 8 days has its folder removed. Python then compiles and
        writes again on its next imports, so the cost is time, not correctness."""
        current = self.build("62eb10e43a0", 0)
        running = self.build("afaca1a036c", 8)
        (running / "Applications" / "fresh.cpython-313.pyc").write_bytes(b"written a second ago")
        self.assertEqual(pycache_retention.prune(self.root, current), ["afaca1a036c"])

    def test_an_unreadable_folder_is_kept_and_not_reported(self):
        if os.name == "nt" or os.geteuid() == 0:
            self.skipTest("POSIX permissions as a normal user")
        current = self.build("62eb10e43a0", 0)
        locked = self.build("afaca1a036c", 30)
        (locked / "Applications" / "module.cpython-313.pyc").write_bytes(b"pyc")
        (locked / "Applications").chmod(0o500)
        self.addCleanup(lambda: (locked / "Applications").exists() and (locked / "Applications").chmod(0o700))
        removed = pycache_retention.prune(self.root, current)
        self.assertEqual(removed, [])       # Changed with the fix: only a folder that is gone is reported ...
        self.assertTrue(locked.exists())    # ... this one is still there; retried next session.


if __name__ == "__main__":
    unittest.main()
