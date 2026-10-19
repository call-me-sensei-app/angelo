# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Automatic reads never reach Python-level overrides of document classes.

An add-on can replace ``__getattribute__``, ``__getattr__`` or ``__init__`` on a
``bpy.types`` class (read-only break #6: a ``Curve.__getattribute__`` override
ran during an automatic read). The reader's Python side only ever holds copied
primitives from the native address ABI, so such an override has nothing to run
on. These host tests prove the Python side's half of that contract with the
contract backend: overrides installed on the stand-in classes are counted only
while the reader, not the backend standing in for native code, is running.
The compiled half is test_blender_read_access.py (real ``Curve`` and
``EnumProperty`` overrides, zero callbacks).
"""

import ast
from pathlib import Path
import unittest

from test_native_read_tools import ReaderFixture
from read_tools_fakes import Collection, ReadAPI, Struct

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
READER_MODULES = ("data_read.py", "data_read_access.py", "data_read_records.py", "data_read_rows.py",
                  "data_read_values.py", "data_read_policy.py")


class _CountingBackend(ReadAPI):
    """The contract backend, with a flag raised while a backend operation runs."""

    inside = 0

    def __getattribute__(self, name):
        attribute = object.__getattribute__(self, name)
        if name.startswith("_") or not callable(attribute):
            return attribute

        def call(*arguments, **keywords):
            _CountingBackend.inside += 1
            try:
                return attribute(*arguments, **keywords)
            finally:
                _CountingBackend.inside -= 1
        return call


class OverrideTests(ReaderFixture):
    PATHS = ("scene", "scene.frame_current", "scene.cycles", "scene.cycles.samples", "data.objects",
             'data.objects["Cube"]', 'data.objects["Cube"].name', "data.objects[0].modifiers",
             'data.node_groups["Rock"].nodes', "data.meshes[0].vertices[1].groups")

    def setUp(self):
        super().setUp()
        backend = _CountingBackend(self.bpy)
        self.native_read = backend
        from unittest.mock import patch
        helper = patch("angelo_agent.data_read_access.native_api", lambda: backend)
        helper.start()
        self.addCleanup(helper.stop)
        self.hits = []
        hits = self.hits

        def install(cls, name, wrapper):
            original = cls.__dict__.get(name)
            setattr(cls, name, wrapper)
            self.addCleanup(lambda: setattr(cls, name, original) if original is not None else delattr(cls, name))

        def counted(cls, name):
            inherited = getattr(cls, name)

            def override(instance, *arguments, **keywords):
                if not _CountingBackend.inside:
                    hits.append((cls.__name__, name, arguments[:1]))
                return inherited(instance, *arguments, **keywords)
            return override

        for cls in (Struct, Collection):
            for name in ("__getattribute__", "__getattr__", "__len__", "__getitem__", "__iter__"):
                if hasattr(cls, name):
                    install(cls, name, counted(cls, name))
        original_init = Struct.__init__

        def init(instance, *arguments, **keywords):
            if not _CountingBackend.inside:
                hits.append(("Struct", "__init__", ()))
            original_init(instance, *arguments, **keywords)
        install(Struct, "__init__", init)

    def test_overrides_installed_on_document_classes_never_run_during_reads(self):
        scene = self.window.scene
        _ = scene.frame_current  # Positive control: an ordinary Python read reaches the override.
        self.assertTrue(self.hits, "the counting override is not installed")
        self.hits.clear()
        answers = [self.read(path, depth=2) for path in self.PATHS]
        self.assertTrue(any(answer.get("found") for answer in answers), answers)
        self.assertEqual(self.hits, [])

    def test_a_live_object_from_the_backend_is_refused_not_used(self):
        from angelo_agent.data_read_records import _copy
        with self.assertRaisesRegex(ValueError, "live or unsupported"):
            _copy({"value": self.window.scene})
        self.assertEqual(self.hits, [])


class SourceShapeTests(unittest.TestCase):
    def test_reader_modules_never_touch_bpy_or_live_window_attributes(self):
        for name in READER_MODULES:
            tree = ast.parse((ADDON / name).read_text(encoding="utf-8"), name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                    # The window is handed to the native ABI only; no attribute of it is read.
                    self.assertNotEqual(node.value.id, "window", "%s reads window.%s" % (name, node.attr))
                    self.assertNotEqual(node.value.id, "bpy", "%s uses bpy.%s" % (name, node.attr))
                if isinstance(node, ast.Name):
                    self.assertNotIn(node.id, {"bl_rna", "__getattribute__"}, name)


if __name__ == "__main__":
    unittest.main()
