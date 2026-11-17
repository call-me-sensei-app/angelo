# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every compositor effect a render runs is read, not only the active one (compositor_stack), without Blender.

Blender 5.3 made the compositor a stack of effects and turned ``Scene.compositing_node_group`` into the active
effect's group. Read alone it hid a File Output node, a Render Layers node of another scene or an image-wide node in
any other effect from render admission and from a material swap's safety check. A 5.2 scene keeps its one group.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rendered_view_case import LookCase  # noqa: E402,F401  (puts the add-on on the path)
from angelo_agent import compositor_stack, render_reach, worker_material_reach  # noqa: E402
from angelo_agent.job_spec import JobRequestError  # noqa: E402

FILE_OUTPUT = SimpleNamespace(bl_idname="CompositorNodeOutputFile", mute=False, node_tree=None)
BLUR = SimpleNamespace(bl_idname="CompositorNodeBlur", bl_label="Blur", name="Blur", mute=False, node_tree=None)
PASS = SimpleNamespace(bl_idname="NodeGroupOutput", bl_label="Group Output", name="Group Output", mute=False,
                       node_tree=None)


def group(name, *nodes):
    found = SimpleNamespace(name=name, nodes=list(nodes))
    found.as_pointer = lambda: id(found)   # A data-block's identity, as bpy gives it.
    return found


def effect(tree, render=True):
    return SimpleNamespace(node_group=tree, enable_for_render=render)


def scene(name, uid, effects=None, single=None, compositing=True):
    """A 5.3 scene when ``effects`` is given (its ``compositing_node_group`` is the first, active, effect's group, as
    Blender returns it), else a 5.2 scene with the one group ``single``."""
    found = SimpleNamespace(name=name, session_uid=uid, use_nodes=True,
                            render=SimpleNamespace(use_sequencer=False, use_compositing=compositing,
                                                   engine="CYCLES"),
                            sequence_editor=None, view_layers=[], display=None)
    if effects is not None:
        found.compositor_effects = list(effects)
        found.compositing_node_group = effects[0].node_group if effects else None
    else:
        found.compositing_node_group = single
    return found


class StackTests(unittest.TestCase):
    def test_a_5_2_scene_has_its_one_group(self):
        tree = group("Compositing")
        self.assertEqual(compositor_stack.render_trees(scene("A", 1, single=tree)),
                         [("compositing_node_group", tree)])
        self.assertEqual(compositor_stack.render_trees(scene("A", 1)), [])

    def test_every_render_enabled_effect_in_order_each_group_once(self):
        first, second, off = group("Grade"), group("Outputs"), group("Preview only")
        found = scene("A", 1, effects=[effect(first), effect(off, render=False), effect(second), effect(first),
                                       effect(None)])
        self.assertEqual(compositor_stack.render_trees(found),
                         [("compositor_effects[0].node_group", first), ("compositor_effects[2].node_group", second)])
        self.assertEqual(compositor_stack.groups(found), [first, off, second])

    def test_an_animated_flag_counts_as_on_and_a_constant_one_does_not(self):
        keyed, plain, driven = group("Keyed"), group("Plain"), group("Driven")
        found = scene("A", 1, effects=[effect(keyed, render=False), effect(plain, render=False),
                                       effect(driven, render=False)])
        curve = lambda path: SimpleNamespace(data_path=path)
        found.animation_data = SimpleNamespace(
            action=None, action_slot=None, drivers=[curve("compositor_effects[2].enable_for_render")], nla_tracks=[])
        found.animation_data.drivers.append(curve("compositor_effects[0].enable_for_render"))
        self.assertEqual([tree for _path, tree in compositor_stack.render_trees(found)], [keyed, driven])

    def test_an_effect_path_is_blenders_own_by_name(self):
        tree = group("Outputs")
        named = effect(tree)
        named.path_from_id = lambda: 'compositor_effects["Outputs"]'
        self.assertEqual(compositor_stack.render_trees(scene("A", 1, effects=[named])),
                         [('compositor_effects["Outputs"].node_group', tree)])

    def test_another_id_has_no_compositor(self):
        material = SimpleNamespace(node_tree=group("Shader"))
        self.assertEqual(compositor_stack.groups(material), [])


class RenderReachTests(unittest.TestCase):
    def test_a_file_output_in_an_effect_that_is_not_active_is_seen(self):
        main = scene("Main", 1, effects=[effect(group("Grade", PASS)), effect(group("Outputs", FILE_OUTPUT))])
        self.assertTrue(render_reach.compositor_writes(main))
        with self.assertRaisesRegex(JobRequestError, "File Output"):
            render_reach.admit_look(main)

    def test_an_effect_off_for_render_or_compositing_off_writes_nothing(self):
        off = scene("Main", 1, effects=[effect(group("Grade", PASS)), effect(group("Outputs", FILE_OUTPUT), False)])
        self.assertFalse(render_reach.compositor_writes(off))
        self.assertFalse(render_reach.compositor_writes(
            scene("Main", 1, effects=[effect(group("Outputs", FILE_OUTPUT))], compositing=False)))

    def test_a_render_layers_node_of_another_scene_in_any_effect_renders_it(self):
        shot = scene("Shot", 2, effects=[])
        layers = SimpleNamespace(bl_idname="CompositorNodeRLayers", mute=False, scene=shot)
        main = scene("Main", 1, effects=[effect(group("Grade", PASS)), effect(group("Mix", layers))])
        self.assertEqual([found.name for found in render_reach.rendered_scenes(main)], ["Main", "Shot"])


class MaterialReachTests(unittest.TestCase):
    def test_an_image_wide_node_in_any_render_effect_refuses_a_swap(self):
        main = scene("Main", 1, effects=[effect(group("Grade", PASS)), effect(group("Soften", BLUR))])
        self.assertIn("Blur", worker_material_reach.render_unsafe(main))
        main.compositor_effects[1].enable_for_render = False
        self.assertIsNone(worker_material_reach.render_unsafe(main))


if __name__ == "__main__":
    unittest.main()
