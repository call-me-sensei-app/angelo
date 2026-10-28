# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A render never writes or renders past what its admission saw: Sequencer scene strips and their File Output nodes.

Review blocker (PR #64): a production render whose Sequencer renders another
scene ran that scene's compositor File Output node and wrote a file its
approval did not list; a look of such a scene rendered the other scene with
its own samples behind a 32-sample receipt and wrote that node's file too.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rendered_view_case import LookCase  # noqa: E402  (puts the add-on on the path)
from angelo_agent import render_reach  # noqa: E402
from angelo_agent.job_spec import JobRequestError  # noqa: E402

FILE_OUTPUT = SimpleNamespace(bl_idname="CompositorNodeOutputFile", mute=False, node_tree=None)


def scene(name, uid, *, strips=(), sequencer=True, compositing=False, writes=False):
    tree = SimpleNamespace(nodes=[FILE_OUTPUT] if writes else [])
    return SimpleNamespace(name=name, session_uid=uid, compositing_node_group=tree,
                           render=SimpleNamespace(use_sequencer=sequencer, use_compositing=compositing),
                           sequence_editor=SimpleNamespace(strips_all=list(strips)))


def strip(other, kind="SCENE", source="CAMERA", mute=False):
    return SimpleNamespace(type=kind, scene=other, scene_input=source, mute=mute)


class ReachTests(unittest.TestCase):
    def test_camera_strips_are_rendered_scenes_and_sequencer_strips_are_followed(self):
        shot = scene("Shot", 2)
        nested = scene("Nested", 4)
        edit = scene("Edit", 3, strips=[strip(nested)])
        bound = scene("Main", 1, strips=[strip(shot), strip(edit, source="SEQUENCER"), strip(None, kind="SOUND")])
        self.assertEqual([(other.name, camera) for other, camera in render_reach.strip_scenes(bound)],
                         [("Shot", True), ("Edit", False), ("Nested", True)])
        self.assertEqual(render_reach.admit_job(bound), ["Shot", "Edit", "Nested"])

    def test_muted_strips_an_off_sequencer_and_the_scene_itself_reach_nothing(self):
        shot = scene("Shot", 2, writes=True, compositing=True)
        self.assertEqual(render_reach.strip_scenes(scene("Main", 1, strips=[strip(shot, mute=True)])), [])
        self.assertEqual(render_reach.strip_scenes(scene("Main", 1, strips=[strip(shot)], sequencer=False)), [])
        main = scene("Main", 1)
        main.sequence_editor.strips_all.append(strip(SimpleNamespace(**vars(main))))  # Another wrapper, same uid.
        self.assertEqual(render_reach.strip_scenes(main), [])

    def test_cycles_of_sequencer_strips_end(self):
        a, b = scene("A", 1), scene("B", 2)
        a.sequence_editor.strips_all.append(strip(b, source="SEQUENCER"))
        b.sequence_editor.strips_all.append(strip(a, source="SEQUENCER"))
        self.assertEqual([other.name for other, _camera in render_reach.strip_scenes(a)], ["B"])

    def test_a_strip_scenes_file_output_refuses_the_job_naming_it(self):
        shot = scene("Shot", 2, compositing=True, writes=True)
        with self.assertRaisesRegex(JobRequestError, "Shot, whose compositor has a File Output node") as caught:
            render_reach.admit_job(scene("Main", 1, strips=[strip(shot)]))
        self.assertEqual(caught.exception.code, "UNSUPPORTED")
        off = scene("Shot", 2, compositing=False, writes=True)  # Its compositor does not run.
        self.assertEqual(render_reach.admit_job(scene("Main", 1, strips=[strip(off)])), ["Shot"])
        through = scene("Edit", 3, compositing=True, writes=True)  # Sequencer input renders its strips, not its compositor.
        self.assertEqual(render_reach.admit_job(scene("Main", 1, strips=[strip(through, source="SEQUENCER")])), ["Edit"])

    def test_a_look_refuses_any_strip_scene_and_its_own_file_output(self):
        with self.assertRaisesRegex(JobRequestError, r"renders other scenes \(Shot\)"):
            render_reach.admit_look(scene("Main", 1, strips=[strip(scene("Shot", 2))]))
        with self.assertRaisesRegex(JobRequestError, "File Output"):
            render_reach.admit_look(scene("Main", 1, compositing=True, writes=True))
        render_reach.admit_look(scene("Main", 1, strips=[strip(scene("Shot", 2), mute=True)]))

    def test_strip_bound_refuses_instead_of_an_unchecked_tail(self):
        bound = scene("Main", 1, strips=[strip(scene("S%d" % n, 10 + n)) for n in range(3)])
        with patch.object(render_reach, "MAX_STRIPS", 2), self.assertRaisesRegex(JobRequestError, "too many strips"):
            render_reach.admit_job(bound)


class LookAdmissionTests(LookCase):
    def test_a_look_of_a_sequencer_scene_with_scene_strips_starts_nothing(self):
        main = self.blender.scene
        main.render.values["use_sequencer"] = True
        main.sequence_editor = SimpleNamespace(strips_all=[strip(scene("Shot", 2))])
        before = main.snapshot()
        with self.assertRaisesRegex(RuntimeError, "Sequencer renders other scenes"):
            self.begin()
        self.assertEqual(self.blender.invoked, [])
        self.assertEqual(main.snapshot(), before)
        self.assertEqual(self.reference_files(), [])


class Plan:
    """A production plan with the scene's own image only (``job_render_scene.Plan`` is tested elsewhere)."""

    def __init__(self, scene, file_format, name=None):
        self.views, self.nodes, self.single, self.name, self.extension = [], [], True, None, ".png"
        self.outputs = [{"main": True, "labels": {}, "prefix": "frame_", "suffix": "", "extension": ".png"}]


class ProductionAdmissionTests(LookCase):
    def runner(self):
        from angelo_agent.job_runners import RenderRunner
        runner = RenderRunner.__new__(RenderRunner)
        runner.window, runner.scene = self.blender.window, self.blender.scene
        runner.request = {"kind": "RENDER_STILL", "format": "PNG", "output": "a.png", "resolution": [64, 64]}
        runner.output, runner.approved, runner.plan = object(), None, None
        return runner

    def test_the_approval_names_the_strip_scenes_and_a_queued_change_refuses(self):
        from angelo_agent import job_approvals, job_runners
        main = self.blender.scene
        main.render.values["use_sequencer"] = True
        main.sequence_editor = SimpleNamespace(strips_all=[strip(scene("Shot", 2))])
        with patch("angelo_agent.job_render_scene.Plan", Plan):
            runner = self.runner()
            runner.admit()
            self.assertEqual(runner.facts["strip_scenes"], ["Shot"])
            body = job_approvals.message("angelo_job_start", {"kind": "RENDER_STILL", "output": "a.png",
                                                              "_facts": runner.facts})["body"]
            self.assertIn("The Sequencer also renders Shot with their own render settings", body)
            runner.approved = job_runners.render_outputs.signature(runner.plan.outputs)
            runner.approved_strips = list(runner.strip_scenes)
            runner.admit()  # Unchanged while queued.
            main.sequence_editor.strips_all.append(strip(scene("Insert", 5)))
            with self.assertRaises(JobRequestError) as caught:
                runner.admit()
        self.assertEqual(caught.exception.code, "STALE_SETTING")
        self.assertEqual(str(caught.exception), job_runners.STRIPS_CHANGED)


if __name__ == "__main__":
    unittest.main()
