# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An export's save browser starts in the document's folder with one clean file name (``export_names``; fails first).

The 2.1.0 exports review (S-S1, R-S1) found the default path built from an object's name: a name the assistant set
to a path steered the folder the person's confirm vouches. These names are unlike each other and unlike any one scene:
relative and absolute paths, Windows drives and streams, separators of both systems, reserved characters, control
characters, leading dots, empty and blank names. Each must become a single file name in the document's folder.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import importlib.util
import ntpath
import os
import posixpath
import unittest
from pathlib import Path
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"

NAMES = ["Scan", "../../Desktop/steer", "/Users/Shared/drop/scan", "C:\\Users\\Public\\scan", "D:scan",
         "scan.ply:stream", "a/b\\c", "..", ".", "...hidden", "con<>:\"|?*", "tab\tnew\nline", "", "   ",
         "trailing. ", "日本語の点群"]


def load():
    spec = importlib.util.spec_from_file_location("_angelo_export_names_under_test", ADDON / "export_names.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Each system's rules with a document of its own: the path the save browser receives must be valid there.
SYSTEMS = (("posix", posixpath, "/work/project/scene.blend", "/work/project"),
           ("windows", ntpath, "C:\\work\\project\\scene.blend", "C:\\work\\project"),
           ("windows, forward slashes", ntpath, "C:/work/project/scene.blend", "C:/work/project"))


class DefaultPaths(unittest.TestCase):
    """Every system's rules on every host (VM CI win-3 on release/v2.1.0 c91073340b5: a POSIX literal failed on
    Windows, where the product rightly joins with Windows' separator)."""

    def setUp(self):
        self.names = load()

    def check(self, rules, document, folder):
        with patch.object(self.names, "PATH", rules):
            for name in NAMES:
                with self.subTest(name=name, document=document):
                    path = self.names.default_export_path(name, ".ply", document)
                    head, tail = rules.split(path) if folder else ("", path)
                    self.assertEqual(head, folder)
                    self.assertTrue(tail.endswith(".ply") and len(tail) > len(".ply"), tail)
                    for sep in ("/", "\\", ":"):
                        self.assertNotIn(sep, tail)
                    self.assertEqual((ntpath.basename(tail), posixpath.basename(tail)), (tail, tail))
                    self.assertFalse(tail.startswith("."), tail)

    def test_every_name_is_one_file_in_the_documents_folder_on_every_system(self):
        for system, rules, document, folder in SYSTEMS:
            with self.subTest(system=system):
                self.check(rules, document, folder)
                self.check(rules, "", "")   # An unsaved document: the name alone.

    def test_this_systems_own_rules_with_a_native_document(self):
        document = os.path.join(os.path.abspath(os.sep), "work", "project", "scene.blend")
        self.check(os.path, document, os.path.dirname(document))

    def test_a_plain_name_is_kept(self):
        for system, rules, document, folder in SYSTEMS:
            with self.subTest(system=system), patch.object(self.names, "PATH", rules):
                self.assertEqual(self.names.default_export_path("Scan", ".ply", document), rules.join(folder, "Scan.ply"))


class ExportersUseIt(unittest.TestCase):
    """The splat export's browser starts there, asks before replacing a file, and keeps its extension."""

    def test_the_splat_export_takes_its_default_from_the_helper_and_checks_existing(self):
        tree = ast.parse((ADDON / "splat_mesh.py").read_text(encoding="utf-8"))
        op = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "ANGELO_OT_export_splat_ply")
        fields = {n.target.id: n for n in op.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
        self.assertIn("check_existing", fields)
        call = fields["check_existing"].annotation
        self.assertTrue(any(isinstance(k.value, ast.Constant) and k.value.value is True
                            for k in call.keywords if k.arg == "default"))
        invoke = next(n for n in op.body if isinstance(n, ast.FunctionDef) and n.name == "invoke")
        calls = {ast.unparse(n.func) for n in ast.walk(invoke) if isinstance(n, ast.Call)}
        self.assertIn("export_names.default_export_path", calls)
        self.assertNotIn("bpy.path.ensure_ext", calls)
        self.assertTrue(any(isinstance(n, ast.FunctionDef) and n.name == "check" for n in op.body))


if __name__ == "__main__":
    unittest.main()
