# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The headless child vouches exactly the files its own saves write, as the real paths ``headless_files`` checked
(the cut's GUI round on 3fc1f94df29: the first save of an approved file was refused; its break-it review, SHOULD-1):
the source and a resumed output before the copy beside the source is claimed, the working copy once opened, all
before the host starts (so before any script); ``main`` vouches nothing from its raw arguments. What a script's
writers can still do with these files in 2.0.0 is in doc/angelo/script_confinement.rst, "Known limits in 2.0.0".
Runs outside Blender.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))


def _function(tree, name):
    return next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)


def _calls(node, name):
    return [call for call in ast.walk(node) if isinstance(call, ast.Call)
            and getattr(call.func, "attr", getattr(call.func, "id", None)) == name]


class HeadlessVouchesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse((ADDON / "headless_main.py").read_text(encoding="utf-8"))

    def test_main_vouches_nothing_from_its_raw_arguments(self):
        main = _function(self.tree, "main")
        self.assertEqual(_calls(main, "vouch") + _calls(main, "_vouch_saves"), [])

    def test_serve_vouches_the_checked_files_in_order_before_the_host_starts(self):
        serve = _function(self.tree, "_serve")
        lines = {name: [call.lineno for call in _calls(serve, name)]
                 for name in ("check_source", "check_resume", "open_copy", "open_read", "start")}
        vouches = _calls(serve, "_vouch_saves")
        self.assertEqual([[ast.unparse(arg) for arg in call.args] for call in vouches],
                         [["source", "resume"], ["working"]])
        first, second = (call.lineno for call in vouches)
        self.assertLess(max(lines["check_source"] + lines["check_resume"]), first)
        self.assertLess(first, min(lines["open_copy"] + lines["open_read"]), "before the copy beside it is claimed")
        self.assertLess(max(lines["open_copy"] + lines["open_read"]), second)
        self.assertLess(second, min(lines["start"]), "before the host starts, so before any script")

    def test_vouch_saves_vouches_each_given_file(self):
        from angelo_agent import headless_main, owned_paths
        with mock.patch.object(owned_paths, "vouch") as vouch:
            headless_main._vouch_saves(Path("/a/scene.blend"), None, Path("/a/scene-working.blend"))
        self.assertEqual(vouch.call_args_list, [mock.call(Path("/a/scene.blend")),
                                                mock.call(Path("/a/scene-working.blend"))])



class FakeGuard:
    """C's two facts a save asks about, outside the app: files Angelo made (by identity: a new file renamed onto the
    name is another file) and names vouched for (by name: whatever file is there)."""

    def __init__(self):
        self.made, self.named = set(), set()

    @staticmethod
    def _identity(path):
        info = os.stat(path, follow_symlinks=False)
        return info.st_dev, info.st_ino

    def may_write(self, path):
        path = os.path.realpath(path)
        if path in self.named:
            return True
        return os.path.lexists(path) and self._identity(path) in self.made

    def vouch(self, path, folder=False, persist=True):
        self.named.add(os.path.realpath(path))
        return True

    def create_beside(self, named, name):
        if not self.may_write(named):
            return None
        made = os.path.join(os.path.dirname(os.fspath(named)), name)
        os.close(os.open(made, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        self.made.add(self._identity(made))
        return made


class ClaimedOutputSavesTest(unittest.TestCase):
    """The review of the Python-only fallback, MUST-1: a file the session claims after it starts (a reload's fresh
    copy, a new output in ``save``) stays its saves' target after a plain save of the open copy renames a new file
    onto it (an approved script ending in ``save_mainfile``): the next save of a typed edit still lands there."""

    def test_a_plain_save_of_the_open_copy_never_strands_the_next_save(self):
        from angelo_agent import headless_files, owned_paths
        guard = FakeGuard()
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(owned_paths, "_MAY_WRITE", guard.may_write), \
                mock.patch.object(owned_paths, "_VOUCH", guard.vouch), \
                mock.patch.object(owned_paths, "_CREATE_BESIDE", guard.create_beside):
            source = os.path.join(folder, "scene.blend")
            staged = os.path.join(folder, "staged.blend")
            for path, data in ((source, b"BLENDER the person's"), (staged, b"BLENDER Angelo's save")):
                with open(path, "wb") as handle:
                    handle.write(data)
            owned_paths.vouch(source)   # As the child does at start (_vouch_saves).
            target = headless_files.claim_output(Path(source))
            headless_files.place(staged, target)
            script_save = os.path.join(folder, "scene.blend@")   # Blender's own save: a new file renamed onto it.
            with open(script_save, "wb") as handle:
                handle.write(b"BLENDER the script's save")
            os.replace(script_save, target)
            headless_files.place(staged, target)   # The next typed edit's save.
            with open(target, "rb") as handle:
                self.assertEqual(handle.read(), b"BLENDER Angelo's save")

    def test_claim_output_vouches_what_it_claims(self):
        from angelo_agent import headless_files
        source = (ADDON / "headless_files.py").read_text(encoding="utf-8")
        body = source[source.index("def claim_output("):source.index("def path_digest(")]
        self.assertIn("owned_paths.vouch(candidate)", body)


if __name__ == "__main__":
    unittest.main()
