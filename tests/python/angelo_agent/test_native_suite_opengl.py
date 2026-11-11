# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A suite that renders with a GPU-drawn engine is marked ``needs_opengl`` (native_suite_manifest). No Blender.

EEVEE and Workbench render through OpenGL, and so does ``bpy.ops.render.opengl`` whatever the engine. On a machine
without a GPU (the Windows release builder runs the windowless suites with ``ANGELO_QA_NO_OPENGL``), such a render
ends Blender with an access violation after its OpenGL contexts fail. test_blender_render_region_pixels did so on
build4 (1.0.8, a486d96a9e8) because it was not marked, and test_blender_custom_normals_transform's Workbench render
did so on build4 (1.0.11, 116c309e9f6) and build3 (1.1) while it passed on the T4. GPU-less machines are out of scope
for Angelo, so such a suite runs on a GPU machine instead.

The check reads every windowless suite's source, and the local test modules it imports. A suite that names a GPU-drawn
engine in code (docstrings and comments do not count) and refers to a render operator (``bpy.ops.render.render`` or
``bpy.ops.render.opengl``), or that draws on the GPU itself (``bpy.ops.render.opengl``, the ``gpu`` or ``gpu_extras``
modules, ``GPUOffScreen``), must be ``needs_opengl``, unless EXEMPT says why it never renders on the GPU.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import native_suite_manifest as manifest  # noqa: E402

# Every engine Blender draws with the GPU. Cycles and add-on engines (a Python RenderEngine) render without it.
GPU_ENGINES = frozenset({"BLENDER_EEVEE", "BLENDER_EEVEE_NEXT", "BLENDER_WORKBENCH"})
RENDER_OPERATORS = frozenset({"render", "opengl"})
GPU_MODULES = frozenset({"gpu", "gpu_extras"})   # Blender's drawing modules: using one needs an OpenGL context.
EXEMPT = {
    "test_blender_render_extensions": "sets EEVEE and Workbench only to inspect the extension policy; its one render "
                                      "runs on Cycles",
    "test_blender_report_untranslated": "sets Workbench only to leave its probe engine; its renders run on that "
                                        "Python engine",
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


def _render_operator(node):
    """``"render"`` or ``"opengl"`` for ``<...>.ops.render.render`` / ``<...>.ops.render.opengl``, called or passed
    on; None for anything else."""
    if (isinstance(node, ast.Attribute) and node.attr in RENDER_OPERATORS
            and isinstance(node.value, ast.Attribute) and node.value.attr == "render"
            and isinstance(node.value.value, ast.Attribute) and node.value.value.attr == "ops"):
        return node.attr
    return None


def source_facts(source):
    """(names a GPU-drawn engine in code, refers to a render operator, draws on the GPU itself, local modules it
    imports) of one module. Drawing itself is ``render.opengl``, importing a GPU module, or naming ``GPUOffScreen``."""
    tree = ast.parse(source)
    skip = _docstrings(tree)
    gpu_engine = any(isinstance(node, ast.Constant) and node.value in GPU_ENGINES and id(node) not in skip
                     for node in ast.walk(tree))
    operators = {_render_operator(node) for node in ast.walk(tree)} - {None}
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imports.add(node.module.split(".")[0])
    offscreen = any((isinstance(node, ast.Name) and node.id == "GPUOffScreen")
                    or (isinstance(node, ast.Attribute) and node.attr == "GPUOffScreen") for node in ast.walk(tree))
    draws = "opengl" in operators or bool(imports & GPU_MODULES) or offscreen
    return gpu_engine, bool(operators), draws, imports


def renders_on_gpu(script):
    """Whether a suite's script, with the local test modules it imports (transitively), renders on the GPU."""
    gpu_engine = render = draws = False
    pending, seen = [HERE / script], set()
    while pending:
        path = pending.pop()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        names_engine, refers_render, draws_itself, imports = source_facts(path.read_text(encoding="utf-8"))
        gpu_engine, render, draws = gpu_engine or names_engine, render or refers_render, draws or draws_itself
        pending.extend(HERE / (name + ".py") for name in imports)
    return draws or (gpu_engine and render)


def windowless():
    """The suites the runner starts without a window: the ones a machine without OpenGL runs."""
    return [suite for suite in manifest.SUITES if not suite.host_args and not suite.needs_window]


class GpuRenderNeedsOpenGLTests(unittest.TestCase):
    def test_a_windowless_suite_that_renders_on_the_gpu_is_marked_needs_opengl(self):
        unmarked = [suite.name for suite in windowless()
                    if not suite.needs_opengl and suite.name not in EXEMPT and renders_on_gpu(suite.script)]
        self.assertEqual(unmarked, [], "these suites render with EEVEE or Workbench, or draw with render.opengl or "
                                       "the gpu module: mark them needs_opengl in native_suite_manifest.py, or say "
                                       "in EXEMPT why they never do")

    def test_every_exemption_is_still_needed(self):
        suites = {suite.name: suite for suite in windowless()}
        for name, reason in EXEMPT.items():
            self.assertIn(name, suites, name)
            self.assertTrue(reason)
            self.assertFalse(suites[name].needs_opengl, "%s is needs_opengl: drop its exemption" % name)
            self.assertTrue(renders_on_gpu(suites[name].script), "%s no longer looks like it renders on the GPU" % name)

    def test_the_scan_sees_gpu_renders_and_ignores_prose(self):
        self.assertTrue(renders_on_gpu("test_blender_render_region_pixels.py"))
        self.assertTrue(renders_on_gpu("test_blender_custom_normals_transform.py"))   # Workbench (build4, 1.0.11).
        render = "import bpy\nbpy.ops.render.render()\n"
        self.assertEqual(source_facts(render + "scene.render.engine = 'BLENDER_EEVEE'\n")[:3], (True, True, False))
        self.assertEqual(source_facts(render + "scene.render.engine = 'BLENDER_WORKBENCH'\n")[:3], (True, True, False))
        self.assertEqual(source_facts('"""Renders with BLENDER_EEVEE."""\n' + render)[:3], (False, True, False))
        self.assertEqual(source_facts("run(bpy.ops.render.opengl, 'BLENDER_EEVEE_NEXT')\n")[:3], (True, True, True))
        self.assertEqual(source_facts("scene.render.engine = 'CYCLES'\nbpy.ops.render.render()\n")[:3],
                         (False, True, False))
        # The OpenGL operator draws on the GPU whatever the scene's engine.
        self.assertEqual(source_facts("scene.render.engine = 'CYCLES'\nbpy.ops.render.opengl()\n")[:3],
                         (False, True, True))
        # Drawing through Blender's gpu modules needs a context too, with no render at all.
        for source in ("import gpu\n", "from gpu.types import GPUOffScreen\n", "from gpu_extras.batch import x\n",
                       "def f():\n    import gpu\n    gpu.state.blend_set('ALPHA')\n",
                       "offscreen = something.GPUOffScreen(64, 64)\n"):
            self.assertEqual(source_facts(source)[:3], (False, False, True), source)
        self.assertEqual(source_facts('"""Draws with gpu."""\nimport bpy\n')[:3], (False, False, False))


if __name__ == "__main__":
    unittest.main()
