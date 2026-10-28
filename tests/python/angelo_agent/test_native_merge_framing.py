# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""After a merge the main 3D view is framed once on what it brought, by the approved rules (``merge_framing``): only
with the lead's scene shown, only a view the person left alone since the lead's turn began, never during their
navigation, with the selection kept and no undo step. A stand-in ``bpy`` records the view operator and the timers."""

import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))


class Obj:
    def __init__(self, name, selected=False):
        self.name, self.selected = name, selected

    def select_get(self, view_layer=None):
        return self.selected

    def select_set(self, value, view_layer=None):
        self.selected = value

    def visible_get(self, view_layer=None):
        return True


class Names(list):
    def __contains__(self, name):
        return any(obj.name == name for obj in self)


def fake_bpy(collections, framed, timers):
    bpy = types.ModuleType("bpy")
    bpy.data = SimpleNamespace(collections=SimpleNamespace(get=lambda name: collections.get(name)))
    bpy.app = SimpleNamespace(timers=SimpleNamespace(register=lambda function, first_interval=0.0: timers.append(function)))

    @contextmanager
    def temp_override(**kwargs):
        yield
    bpy.context = SimpleNamespace(temp_override=temp_override)

    def view_selected(*args, **kwargs):
        framed.append([obj.name for obj in LAYER.objects if obj.selected])
        return {'FINISHED'}
    bpy.ops = SimpleNamespace(view3d=SimpleNamespace(view_selected=view_selected))
    return bpy


LAYER = SimpleNamespace(objects=Names())


class MergeFramingTests(unittest.TestCase):
    def setUp(self):
        self.framed, self.timers = [], []
        desk, chair, cube = Obj("Desk"), Obj("Chair"), Obj("Cube", selected=True)
        LAYER.objects = Names([desk, chair, cube])
        LAYER.objects.active = cube
        self.collections = {"Part 1": SimpleNamespace(all_objects=[desk]), "Part 2": SimpleNamespace(all_objects=[chair])}
        patcher = mock.patch.dict(sys.modules, {"bpy": fake_bpy(self.collections, self.framed, self.timers)})
        patcher.start()
        self.addCleanup(patcher.stop)
        sys.modules.pop("angelo_agent.merge_framing", None)
        self.addCleanup(sys.modules.pop, "angelo_agent.merge_framing", None)
        from angelo_agent import input_policy, merge_framing, parallel_sessions
        self.module = merge_framing
        self.matrix = [[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, -10.0], [0, 0, 0, 1.0]]
        self.data = SimpleNamespace(view_matrix=self.matrix)
        self.scene = object()
        self.window = SimpleNamespace(scene=self.scene, view_layer=LAYER)
        self.lead = SimpleNamespace(state={"id": "lead"})
        self.busy = [False]
        for target, name, value in ((merge_framing, "_view", lambda window: ("area", "region", self.data)),
                                    (parallel_sessions, "_document_scene", lambda lead: self.scene),
                                    (input_policy, "interaction_active", lambda window: self.busy[0])):
            patch = mock.patch.object(target, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        ui = types.ModuleType("angelo_agent.ui")
        ui.main_window = lambda: self.window
        patch = mock.patch.dict(sys.modules, {"angelo_agent.ui": ui})
        patch.start()
        self.addCleanup(patch.stop)

    def run_timers(self, rounds=3):
        for _ in range(rounds):
            pending, self.timers[:] = list(self.timers), []
            for function in pending:
                if function() is not None:
                    self.timers.append(function)

    def test_a_view_left_alone_is_framed_once_on_what_the_merge_brought_and_the_selection_is_kept(self):
        self.module.note_turn(self.lead)
        self.module.after_merge(self.lead, self.window, ["Part 1", "Part 2", None])
        self.run_timers()
        self.assertEqual(self.framed, [["Desk", "Chair"]])
        self.assertEqual([obj.name for obj in LAYER.objects if obj.selected], ["Cube"])   # As it was.
        self.assertEqual(LAYER.objects.active.name, "Cube")

    def test_a_view_the_person_moved_since_the_turn_began_is_theirs(self):
        self.module.note_turn(self.lead)
        self.matrix[2][3] = -7.0   # The person dollied in.
        self.module.after_merge(self.lead, self.window, ["Part 1"])
        self.run_timers()
        self.assertEqual(self.framed, [])

    def test_never_when_another_scene_is_shown(self):
        self.module.note_turn(self.lead)
        self.window.scene = object()
        self.module.after_merge(self.lead, self.window, ["Part 1"])
        self.run_timers()
        self.assertEqual(self.framed, [])

    def test_never_during_their_navigation_it_waits_for_it_to_end(self):
        self.module.note_turn(self.lead)
        self.busy[0] = True
        self.module.after_merge(self.lead, self.window, ["Part 1"])
        self.run_timers(2)
        self.assertEqual(self.framed, [])
        self.busy[0] = False
        self.run_timers(1)
        self.assertEqual(self.framed, [["Desk"]])

    def test_a_merge_with_no_turn_view_read_never_frames(self):
        self.module.after_merge(SimpleNamespace(state={"id": "other"}), self.window, ["Part 1"])
        self.run_timers()
        self.assertEqual(self.framed, [])

    def test_the_hooks_are_in_place(self):
        root = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
        self.assertIn("merge_framing.note_turn(self)", (root / "controller.py").read_text(encoding="utf-8"))
        self.assertIn("merge_framing.after_merge(lead, window, collections.values())",
                      (root / "worker_sessions.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
