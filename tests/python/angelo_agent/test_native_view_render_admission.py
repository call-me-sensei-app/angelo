# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only render admission must include deep groups and effective Cycles layer settings."""

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rendered_view_case import LookCase
from rendered_view_fakes import Struct


def node(child=None, output=False, muted=False):
    return SimpleNamespace(bl_idname="CompositorNodeOutputFile" if output else "CompositorNodeGroup",
                           mute=muted, node_tree=child)


class CompositorAdmissionTests(LookCase):
    def test_deep_file_output_refuses_before_start_or_settings_change(self):
        tree = SimpleNamespace(nodes=[node(output=True)])
        for _ in range(64):
            tree = SimpleNamespace(nodes=[node(tree)])
        self.blender.scene.compositing_node_group = tree
        before = self.blender.scene.snapshot()
        with self.assertRaisesRegex(RuntimeError, "File Output"):
            self.begin()
        self.assertEqual(self.blender.invoked, [])
        self.assertEqual(self.blender.scene.snapshot(), before)
        self.assertEqual(self.reference_files(), [])

    def test_cycles_and_shared_groups_are_visited_once_without_depth_limit(self):
        from angelo_agent.render_reach import file_outputs
        a, b = SimpleNamespace(nodes=[]), SimpleNamespace(nodes=[])
        a.nodes = [node(b)] * 50
        b.nodes = [node(a), node(output=True, muted=True)]
        self.assertFalse(file_outputs(a))
        b.nodes.append(node(output=True))
        self.assertTrue(file_outputs(a))

    def test_work_bounds_refuse_instead_of_approving_an_unchecked_tail(self):
        from angelo_agent import render_reach
        scene = self.blender.scene
        for limit in ("MAX_NODES", "MAX_TREES"):
            with self.subTest(limit=limit), patch.object(render_reach, limit, 2):
                scene.compositing_node_group = SimpleNamespace(nodes=[node(SimpleNamespace(nodes=[
                    node(SimpleNamespace(nodes=[node(output=True)]))]))])
                with self.assertRaisesRegex(RuntimeError, "too many"):
                    self.begin()
        self.assertEqual(self.blender.invoked, [])

    def test_unreadable_tree_refuses_instead_of_approving(self):
        class BrokenTree:
            @property
            def nodes(self):
                raise ReferenceError("the group was removed")
        self.blender.scene.compositing_node_group = BrokenTree()
        with self.assertRaisesRegex(RuntimeError, "group was removed"):
            self.begin()
        self.assertEqual(self.blender.invoked, [])


class LayerAdmissionTests(LookCase):
    def configure(self, mode, scene_count, counts, *, enabled=None, single=False):
        scene = self.blender.scene
        scene.cycles.values.update(use_layer_samples=mode, samples=scene_count)
        scene.render.values["use_single_layer"] = single
        scene.view_layers = [Struct(name='Layer "\\%d' % index, samples=count,
                                   use=enabled[index] if enabled else True,
                                   cycles=Struct(use_denoising=False)) for index, count in enumerate(counts)]
        self.blender.window.view_layer = scene.view_layers[0]
        scene.view_layers[0].values["update"] = lambda: None
        for layer in scene.view_layers:
            layer.values["path_from_id"] = lambda item=layer: "view_layers[%s]" % json.dumps(item.name)

        def resolve(path):
            self.assertTrue(path.startswith("view_layers["))
            name, end = json.JSONDecoder().raw_decode(path[len("view_layers["):])
            tail = path[len("view_layers[") + end:]
            owner = next(layer for layer in scene.view_layers if layer.name == name)
            self.assertTrue(tail.startswith("]"))
            for part in tail[2:].split(".") if tail[1:] else ():
                owner = getattr(owner, part)
            return owner
        scene.path_resolve = resolve
        return scene

    def layer_state(self):
        scene = self.blender.scene
        return (scene.cycles.samples, scene.cycles.use_layer_samples, scene.cycles.use_denoising,
                [(layer.samples, layer.cycles.use_denoising) for layer in scene.view_layers])

    def test_sample_modes_denoise_and_exact_restore(self):
        cases = (("USE", 128, [128, 8, 0], [32, 8, 32], 128),
                 ("USE", 8, [128, 0, 4], [32, 8, 4], 128),
                 ("BOUNDED", 16, [128, 8, 0], [16, 8, 16], 16),
                 ("IGNORE", 64, [128, 8, 0], [32, 32, 32], 64))
        for mode, scene_count, counts, expected, original_max in cases:
            with self.subTest(mode=mode, scene=scene_count):
                scene = self.configure(mode, scene_count, counts)
                before = self.layer_state()
                self.controller._request_generation += 1
                pending = self.begin()
                effective = [scene.cycles.samples if scene.cycles.use_layer_samples == "IGNORE" else
                             min(layer.samples or scene.cycles.samples, scene.cycles.samples)
                             for layer in scene.view_layers]
                self.assertEqual(effective, expected)
                self.assertTrue(all(layer.cycles.use_denoising for layer in scene.view_layers))
                self.blender.end()
                self.manager.poll()
                result = pending.poll()
                self.assertEqual(result["rendered"]["samples"], max(expected))
                self.assertEqual(result["rendered"]["scene_samples"], original_max)
                self.assertTrue(result["rendered"]["denoised"])
                self.assertEqual(self.layer_state(), before)

    def test_disabled_and_single_layer_settings_do_not_change(self):
        for single, enabled in ((False, [True, False]), (True, [False, True])):
            with self.subTest(single=single):
                self.configure("USE", 64, [8, 128], enabled=enabled, single=single)
                before = self.layer_state()
                pending = self.begin()
                self.assertEqual(self.blender.scene.cycles.samples, 8)
                self.assertFalse(self.blender.scene.view_layers[1].cycles.use_denoising)
                pending.cancel()
                self.blender.end()
                self.manager.poll()
                self.assertEqual(self.layer_state(), before)

    def test_restore_tag_recovers_layer_settings_after_file_load(self):
        from angelo_agent import job_settings
        self.configure("USE", 8, [128, 0])
        before = self.layer_state()
        pending = self.begin()
        self.blender.bpy.data.scenes = [self.blender.scene]
        job_settings.restore_tags()
        self.assertEqual(self.layer_state(), before)
        pending.cancel()
        self.blender.end(written=False)
        self.manager.poll()


if __name__ == "__main__":
    unittest.main()
