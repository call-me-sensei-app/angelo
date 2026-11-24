# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The one reader of a conversation's picture (``reference_images``): every check made again on the descriptor that
is read, so a file changed after ``attached_image`` looked (a FIFO or a link swapped in, a file grown past the limit)
is refused, never waited on or read past its limit; and the app's pictures and views read through it too (break-it
review of e0fc461e471, NIT-2 and NIT-3). No Blender here."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import io
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

import host_waits
from host_test_policy import environment_skip

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import reference_images  # noqa: E402

WHOLE = b"\x89PNG\r\n\x1a\n" + b"pixels" + b"\x00\x00\x00\x00IEND\xaeB`\x82"
POSIX = os.name == "posix"


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.folder, True)

    def store(self, path, limit=1024):
        return SimpleNamespace(attached_image=lambda identity: path, image_limit=limit)

    def test_a_picture_reads_whole_with_its_descriptors_stat(self):
        path = self.folder / "a.png"
        path.write_bytes(WHOLE)
        with reference_images.opened(self.store(path), "a") as (found, status, handle):
            self.assertEqual((found, status.st_size), (path, len(WHOLE)))
            self.assertEqual(reference_images.read(self.store(path), handle), WHOLE)
        self.assertEqual(reference_images.read_image(self.store(path), "a"), (WHOLE, path))

    def test_a_file_over_the_limit_is_refused_before_a_read(self):
        path = self.folder / "big.png"
        path.write_bytes(WHOLE * 10)
        with self.assertRaises(ValueError):
            reference_images.read_image(self.store(path, limit=len(WHOLE)), "big")

    def test_a_file_grown_past_the_limit_during_the_read_is_refused(self):
        store = SimpleNamespace(image_limit=10)
        self.assertEqual(reference_images.read(store, io.BytesIO(b"x" * 10)), b"x" * 10)
        with self.assertRaises(ValueError):
            reference_images.read(store, io.BytesIO(b"x" * 11))

    @unittest.skipUnless(POSIX and hasattr(os, "mkfifo"), environment_skip("FIFOs and O_NONBLOCK are POSIX"))
    def test_a_fifo_swapped_in_is_refused_without_waiting_on_it(self):
        path = self.folder / "pipe.png"
        os.mkfifo(path)
        outcome = []

        def read():
            try:
                reference_images.read_image(self.store(path), "pipe")
            except (ValueError, OSError) as error:
                outcome.append(type(error))
        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        thread.join(host_waits.CAP_S)
        self.assertFalse(thread.is_alive(), "never waits for a writer on a FIFO")
        self.assertEqual(outcome, [reference_images.Refused], "no regular file: refused (a ValueError)")

    @unittest.skipUnless(POSIX, environment_skip("O_NOFOLLOW is POSIX"))
    def test_a_link_swapped_in_is_never_followed(self):
        target = self.folder / "elsewhere.png"
        target.write_bytes(WHOLE)
        link = self.folder / "link.png"
        link.symlink_to(target)
        with self.assertRaises(OSError):
            reference_images.read_image(self.store(link), "link")

    def test_a_missing_file_is_an_os_error(self):
        with self.assertRaises(OSError):
            reference_images.read_image(self.store(self.folder / "gone.png"), "gone")


class ReadersTests(unittest.TestCase):
    """Every reader of a conversation's picture by its id goes through ``reference_images``."""

    def function(self, module, name):
        tree = ast.parse((ADDON / module).read_text(encoding="utf-8"))
        return ast.unparse(next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                                and node.name == name))

    def test_the_app_pictures_and_views_and_the_live_page_read_through_it(self):
        for module, name, call in (("external_look.py", "_image", "reference_images.read_image("),
                                   ("external_visibility.py", "_pictures", "reference_images.read_image("),
                                   ("session_page_pending.py", "_try", "reference_images.opened(")):
            with self.subTest(reader="%s:%s" % (module, name)):
                code = self.function(module, name)
                self.assertIn(call, code)
                self.assertNotIn("read_bytes(", code)
                self.assertNotIn("attached_image(", code)


if __name__ == "__main__":
    unittest.main()
