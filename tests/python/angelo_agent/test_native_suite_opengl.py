# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A suite that renders with EEVEE is marked ``needs_opengl`` (native_suite_manifest). No Blender.

EEVEE renders through OpenGL. On a machine without a GPU (the Windows release builder runs the windowless suites
with ``ANGELO_QA_NO_OPENGL``), EEVEE's first render ends Blender with an access violation after its OpenGL contexts
fail. test_blender_render_region_pixels did so on build4 (1.0.8, a486d96a9e8) because it was not marked. GPU-less
machines are out of scope for Angelo, so such a suite runs on a GPU machine instead.

The check reads every windowless suite's source, and the local test modules it imports. A suite that names an EEVEE
engine in code (docstrings and comments do not count) and refers to a render operator (``bpy.ops.render.render`` or
``bpy.ops.render.opengl``) must be ``needs_opengl``, unless EXEMPT says why it never renders with EEVEE.
"""

import ast
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import native_suite_manifest as manifest  # noqa: E402

EEVEE = frozenset({"BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"})
RENDER_OPERATORS = frozenset({"render", "opengl"})
EXEMPT = {
    "test_blender_exr_codecs": "EEVEE only when the build has no Cycles; every build renders its passes with Cycles",
    "test_blender_render_extensions": "sets EEVEE only to inspect the extension policy; its one render runs on Cycles",
}


def _docstrings(tree):
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(
                    first.value.value, str):
                found.add(id(first.value))
    return found


def _is_render_operator(node):
    """``<...>.ops.render.render`` or ``<...>.ops.render.opengl``, called or passed on."""
    return (isinstance(node, ast.Attribute) and node.attr in RENDER_OPERATORS
            and isinstance(node.value, ast.Attribute) and node.value.attr == "render"
            and isinstance(node.value.value, ast.Attribute) and node.value.value.attr == "ops")


def source_facts(source):
    """(names an EEVEE engine in code, refers to a render operator, local modules it imports) of one module."""
    tree = ast.parse(source)
    skip = _docstrings(tree)
    eevee = any(isinstance(node, ast.Constant) and node.value in EEVEE and id(node) not in skip
                for node in ast.walk(tree))
    render = any(_is_render_operator(node) for node in ast.walk(tree))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imports.add(node.module.split(".")[0])
    return eevee, render, imports


def renders_eevee(script):
    """Whether a suite's script, with the local test modules it imports (transitively), renders with EEVEE."""
    eevee = render = False
    pending, seen = [HERE / script], set()
    while pending:
        path = pending.pop()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        names_eevee, refers_render, imports = source_facts(path.read_text(encoding="utf-8"))
        eevee, render = eevee or names_eevee, render or refers_render
        pending.extend(HERE / (name + ".py") for name in imports)
    return eevee and render


def windowless():
    """The suites the runner starts without a window: the ones a machine without OpenGL runs."""
    return [suite for suite in manifest.SUITES if not suite.host_args and not suite.needs_window]


class EeveeNeedsOpenGLTests(unittest.TestCase):
    def test_a_windowless_suite_that_renders_eevee_is_marked_needs_opengl(self):
        unmarked = [suite.name for suite in windowless()
                    if not suite.needs_opengl and suite.name not in EXEMPT and renders_eevee(suite.script)]
        self.assertEqual(unmarked, [], "these suites render with EEVEE: mark them needs_opengl in "
                                       "native_suite_manifest.py, or say in EXEMPT why they never do")

    def test_every_exemption_is_still_needed(self):
        suites = {suite.name: suite for suite in windowless()}
        for name, reason in EXEMPT.items():
            self.assertIn(name, suites, name)
            self.assertTrue(reason)
            self.assertFalse(suites[name].needs_opengl, "%s is needs_opengl: drop its exemption" % name)
            self.assertTrue(renders_eevee(suites[name].script), "%s no longer looks like it renders EEVEE" % name)

    def test_the_scan_sees_eevee_renders_and_ignores_prose(self):
        self.assertTrue(renders_eevee("test_blender_render_region_pixels.py"))
        render = "import bpy\nbpy.ops.render.render()\n"
        self.assertEqual(source_facts(render + "scene.render.engine = 'BLENDER_EEVEE'\n")[:2], (True, True))
        self.assertEqual(source_facts('"""Renders with BLENDER_EEVEE."""\n' + render)[:2], (False, True))
        self.assertEqual(source_facts("run(bpy.ops.render.opengl, 'BLENDER_EEVEE_NEXT')\n")[:2], (True, True))
        self.assertEqual(source_facts("scene.render.engine = 'CYCLES'\nbpy.ops.render.render()\n")[:2],
                         (False, True))


if __name__ == "__main__":
    unittest.main()
