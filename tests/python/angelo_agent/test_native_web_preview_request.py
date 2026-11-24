# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The live preview's export settings (doc/angelo/web_export.rst, "The live preview"): one request every entry point
uses, and it never re-encodes the artist's textures.

Real use (2026-11-15): the preview's ``textures: AUTO`` wrote lossy WebP, so a painted hair atlas looked soft in the
browser and the preview could not be trusted for texture work. The class is "a preview that changes what it previews
by default"; the preview is a page on this computer, so it has no download to save. The menu operator and the agent's
host route each wrote their own literal, so this pins both to one constant, for any later entry point too."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import web_export_spec  # noqa: E402


def _start_calls():
    """(file name, call) for every ``web_preview.start(...)`` call in the add-on."""
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "start"
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "web_preview"):
                yield path.name, node


class PreviewRequestTests(unittest.TestCase):
    def test_the_preview_keeps_textures_as_authored(self):
        request = web_export_spec.PREVIEW_REQUEST
        self.assertEqual(request["textures"], "KEEP")
        self.assertEqual(set(request), {"meshes", "textures", "merge", "selection_only"})
        self.assertIn(request["meshes"], web_export_spec.MESHES)

    def test_every_entry_point_starts_with_the_one_request(self):
        calls = list(_start_calls())
        self.assertGreaterEqual(len(calls), 2, calls)   # The menu operator and the agent's route at least.
        for name, call in calls:
            request = call.args[1] if len(call.args) > 1 else None
            self.assertFalse(isinstance(request, ast.Dict), f"{name}:{call.lineno} builds its own preview request")
            self.assertEqual(ast.unparse(request).rsplit(".", 1)[-1], "PREVIEW_REQUEST", f"{name}:{call.lineno}")

    def test_the_export_keeps_its_own_default(self):
        # A file made to ship still picks its smallest textures unless asked; only the preview changes.
        self.assertEqual(web_export_spec.validate_web(web_export_spec.CHECK, {})["textures"], "AUTO")


if __name__ == "__main__":
    unittest.main()
