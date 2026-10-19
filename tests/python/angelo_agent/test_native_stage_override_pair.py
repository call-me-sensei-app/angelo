# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A staged program's context keeps the stage's scene and view layer as one pair.

Blender's ``temp_override(window=...)`` calls ``CTX_wm_window_set``, which drops
a Python ``scene`` override given in the same call (and inherited from an outer
one) but keeps ``view_layer``. Stock Blender then evaluated the artist's scene
with the stage's view layer in the view-layer update before every ``bpy.ops``
call (``bpy_op_view_layer_update``) and crashed (SIGSEGV in
``BKE_object_eval_eval_base_flags``). Angelo's context guard
(``test_blender_context_override_pair.py``) makes ``temp_override`` raise
``TypeError`` for a view layer it names that the resulting scene lacks, and
resolves an inherited one from the scene (``CTX_data_view_layer``): no crash,
but an operator a kernel calls without binding the pair again would run on the
artist's scene. ``FakeBlender`` models exactly those rules; the native
reproduction is ``duplicate_then_edit`` in ``test_blender_particles.py``.
"""

import ast
import contextlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core"
sys.path.insert(0, str(ADDON))
from angelo_agent import action_particles  # noqa: E402
from angelo_agent.program_objects import ProgramObjectsMixin  # noqa: E402

# CTX_wm_window_set clears these members of the Python override (PYCTX_WINDOW_MEMBERS); "view_layer" is not one.
WINDOW_MEMBERS = ("window", "scene", "workspace", "screen", "area", "space_data", "region", "region_data")


class FakeContext:
    def __init__(self, window):
        self.window, self._scene, self._members = window, window.scene, {}

    @property
    def scene(self):
        return self._members.get("scene", self._scene)

    @property
    def view_layer(self):
        """What a script reads: an overridden view layer as given."""
        return self._members.get("view_layer", self.scene.view_layers[0])

    def resolved_view_layer(self):
        """What Blender's C code uses (CTX_data_view_layer): an overridden layer only with its own scene."""
        layer = self.view_layer
        return layer if layer in self.scene.view_layers else self.scene.view_layers[0]

    @contextlib.contextmanager
    def temp_override(self, **members):
        saved = (self.window, self._scene, self._members)
        merged = dict(self._members, **members)  # Nested overrides inherit the outer members.
        if "window" in members:  # CTX_wm_window_set: the window's scene, and the Python scene override dropped.
            window = members["window"]
            self.window, self._scene = window, window.scene if window is not None else self._scene
            merged = {key: value for key, value in merged.items() if key not in WINDOW_MEMBERS}
        self._members = merged
        if "view_layer" in members and self.view_layer not in self.scene.view_layers:
            self.window, self._scene, self._members = saved  # Refused on enter, the context left as it was.
            raise TypeError("view_layer %r belongs to another scene, not to the context scene %r"
                            % (self.view_layer.name, self.scene.name))
        try:
            yield
        finally:
            self.window, self._scene, self._members = saved


class FakeBlender:
    def __init__(self, window):
        self.context = FakeContext(window)
        self.ops = NS(particle=NS(new=self._particle_new))
        self.operator_pairs = []

    def _view_layer_update(self):
        """bpy_op_view_layer_update: the context's scene with the view layer Blender resolves for it."""
        self.operator_pairs.append((self.context.scene.name, self.context.resolved_view_layer().name))

    def _particle_new(self):
        self._view_layer_update()
        system = self.context._members["particle_system"]
        system.settings = NS(name=system.settings.name + ".001")
        return {"FINISHED"}


def scene(name):
    layer = NS(name=name + " ViewLayer")
    return NS(name=name, view_layers=[layer])


class StageOverridePair(unittest.TestCase):
    def setUp(self):
        self.artist = scene("Scene")
        self.stage_scene = scene("__angelo_stage")
        self.window = NS(scene=self.artist)
        self.stage = NS(scene=self.stage_scene, view_layer=self.stage_scene.view_layers[0])
        self.backend = NS(window=self.window)
        self.blender = FakeBlender(self.window)

    def test_window_with_scene_and_view_layer_in_one_call_is_refused(self):
        # The fake reproduces Blender's rule; this is what the program executor did before (a crash then).
        context = self.blender.context
        with self.assertRaisesRegex(TypeError, "belongs to another scene"):
            with context.temp_override(window=self.window, scene=self.stage_scene, view_layer=self.stage.view_layer):
                self.fail("the override is refused on enter")
        self.assertIs(context.scene, self.artist)

    def test_an_inherited_view_layer_follows_a_window_override(self):
        # Why kernels enter the stage's scene and view layer again inside any window override of their own.
        with patch.dict(sys.modules, {"bpy": self.blender}):
            with ProgramObjectsMixin._override(self.backend, self.stage):
                with self.blender.context.temp_override(window=None):
                    self.blender._view_layer_update()
        self.assertEqual(self.blender.operator_pairs, [("Scene", "Scene ViewLayer")])

    def test_program_override_binds_the_stage_pair(self):
        with patch.dict(sys.modules, {"bpy": self.blender}):
            with ProgramObjectsMixin._override(self.backend, self.stage):
                context = self.blender.context
                self.assertIs(context.window, self.window)
                self.assertIs(context.scene, self.stage_scene)
                self.assertIs(context.view_layer, self.stage.view_layer)
        self.assertIs(self.blender.context.scene, self.artist)

    def test_operator_without_its_own_scene_inside_a_program_runs_on_the_stage(self):
        # particle_set on a duplicate whose system shares its source's staged settings copies them with
        # particle.new, overriding only the object and system (action_particles.copy_settings).
        system = NS(settings=NS(name="ParticleSettings"))
        with patch.dict(sys.modules, {"bpy": self.blender}):
            with ProgramObjectsMixin._override(self.backend, self.stage):
                copied = action_particles.copy_settings(NS(name="Twin"), system)
        self.assertEqual(copied.name, "ParticleSettings.001")
        self.assertEqual(self.blender.operator_pairs, [("__angelo_stage", "__angelo_stage ViewLayer")])


class NoWindowWithSceneOverride(unittest.TestCase):
    def test_no_add_on_override_gives_window_with_scene_or_view_layer(self):
        """A window override resets the scene; give the scene and view layer in a nested override instead."""
        offenders = []
        for path in sorted((ADDON / "angelo_agent").rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), str(path))):
                if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "temp_override":
                    names = {keyword.arg for keyword in node.keywords}
                    if "window" in names and names & {"scene", "view_layer"}:
                        offenders.append("%s:%d" % (path.relative_to(ADDON), node.lineno))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
