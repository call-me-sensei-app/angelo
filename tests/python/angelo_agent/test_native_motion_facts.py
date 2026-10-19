# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The motion read-back's host-side rules: which edits it follows, which frames it reads, what it asks for.

Models built an animated object in the wrong frame of reference while its animated origin, the only thing their
checks measured, was right. ``motion_facts`` states the geometry at a few
frames after an edit; ``test_blender_motion_readback`` checks every value against Blender's own evaluation on
held-out rigs. These pin the pure parts on stand-ins. No Blender.
"""

import sys
import unittest
from array import array
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import change_feed, motion_facts, motion_guards, motion_scope  # noqa: E402
from angelo_agent.program_completion import commit_facts  # noqa: E402
from angelo_agent.scene_spec import validate_scene  # noqa: E402

HANDLE = "scn:" + "a" * 32 + ":7"


class Points(list):
    """Key points as ``foreach_get`` reads them."""

    def foreach_get(self, _name, values):
        for index, point in enumerate(self):
            values[2 * index:2 * index + 2] = array("f", point.co)


def keyed(*frames):
    """A stand-in object whose assigned Action slot keys ``frames`` (anim_channels.channelbag's path)."""
    points = Points(SimpleNamespace(co=(float(frame), 0.0)) for frame in frames)
    bag = SimpleNamespace(fcurves=[SimpleNamespace(keyframe_points=points, data_path="location", array_index=0)])
    strip = SimpleNamespace(channelbag=lambda slot: bag)
    action = SimpleNamespace(layers=[SimpleNamespace(strips=[strip])])
    animation = SimpleNamespace(action=action, action_slot=object(), drivers=[], nla_tracks=[])
    return SimpleNamespace(animation_data=animation, parent=None, as_pointer=lambda value=id(points): value)


class ChangedNames(unittest.TestCase):
    def test_added_and_moving_changes_count_and_a_material_alone_does_not(self):
        changes = {"added": [{"name": "Car", "type": "EMPTY"}],
                   "modified": [{"name": "Wheel", "fields": ["matrix"]}, {"name": "Paint", "fields": ["materials"]},
                                {"name": "Keyed", "fields": ["motion"]}, {"name": "Car", "fields": ["geometry"]}]}
        self.assertEqual(motion_facts.changed_names(changes), ["Car", "Wheel", "Keyed"])
        self.assertEqual(motion_facts.changed_names(None), [])


class Frames(unittest.TestCase):
    def scene(self, start=1, end=120):
        return SimpleNamespace(frame_start=start, frame_end=end)

    def test_first_middle_and_last_frame_and_the_keys_between(self):
        self.assertEqual(motion_facts.frames_for(self.scene(), [keyed(1, 120)]), [1, 60, 120])
        self.assertEqual(motion_facts.frames_for(self.scene(), [keyed(1, 30, 120)]), [1, 30, 60, 120])

    def test_at_most_five_frames_with_the_keys_chosen_evenly(self):
        frames = motion_facts.frames_for(self.scene(), [keyed(*range(2, 120, 7))])
        self.assertEqual(len(frames), motion_facts.MAX_FRAMES)
        self.assertEqual((frames[0], frames[-1]), (1, 120))
        self.assertIn(60, frames)

    def test_keys_outside_the_range_and_a_one_frame_scene(self):
        self.assertEqual(motion_facts.frames_for(self.scene(), [keyed(-10, 500)]), [1, 60, 120])
        self.assertEqual(motion_facts.frames_for(self.scene(5, 5), [keyed(1, 9)]), [5])


def thing(name, parent=None, keys=False, constraints=(), modifiers=(), kind="MESH", data=None, parent_type="OBJECT"):
    """A stand-in object for ``motion_scope.Scope`` (no ``bl_rna``: no pointer properties)."""
    animation = SimpleNamespace(action=object(), drivers=[], nla_tracks=[]) if keys else None
    obj = SimpleNamespace(name=name, parent=parent, parent_type=parent_type, animation_data=animation,
                          constraints=list(constraints), modifiers=list(modifiers), type=kind, data=data,
                          instance_type="NONE", rigid_body=None)
    obj.as_pointer = lambda value=id(obj): value
    return obj


class Scope(unittest.TestCase):
    def test_a_keyed_car_on_a_still_road_curve_tops_at_the_car(self):
        road = thing("Road", kind="CURVE", data=SimpleNamespace(use_path=False, animation_data=None))
        car = thing("Car", parent=road, keys=True)
        body = thing("Body", parent=car)
        scope = motion_scope.Scope()
        self.assertTrue(scope.animates(body))
        self.assertIs(scope.top(body), car)
        self.assertFalse(scope.moves(road))

    def test_a_still_object_with_a_boolean_or_a_constraint_on_still_targets_reads_nothing(self):
        cutter = thing("Cutter")
        wall = thing("Wall", modifiers=[SimpleNamespace(type="BOOLEAN", show_viewport=True)])
        held = thing("Held", constraints=[SimpleNamespace(type="COPY_LOCATION", enabled=True, influence=1.0)])
        scope = motion_scope.Scope()
        self.assertFalse(scope.animates(wall) or scope.animates(held) or scope.animates(cutter))

    def test_time_modifiers_constraints_and_vertex_parents_animate(self):
        wave = thing("Wave", modifiers=[SimpleNamespace(type="WAVE", show_viewport=True)])
        tracked = thing("Tracked", constraints=[SimpleNamespace(type="FOLLOW_TRACK", enabled=True, influence=1.0)])
        shaped = thing("Shaped", data=SimpleNamespace(animation_data=None, shape_keys=SimpleNamespace(
            animation_data=SimpleNamespace(action=object(), drivers=[], nla_tracks=[]))))
        rider = thing("Rider", parent=shaped, parent_type="VERTEX")
        muted = thing("Muted", constraints=[SimpleNamespace(type="FOLLOW_TRACK", enabled=False, influence=1.0)])
        scope = motion_scope.Scope()
        self.assertTrue(all(scope.animates(obj) for obj in (wave, tracked, shaped, rider)))
        self.assertFalse(scope.moves(shaped))
        self.assertFalse(scope.animates(muted))


def signed(*frames):
    obj = keyed(*frames)
    obj.constraints, obj.data, obj.parent_type, obj.parent_bone = [], None, "OBJECT", ""
    return obj


class Signature(unittest.TestCase):
    def test_a_long_curve_is_sampled_and_a_digest_never_depends_on_another_object(self):
        long_keys = range(1, motion_facts.MAX_SIGNED_KEYS + 50)
        first = motion_facts.motion_signature(signed(*long_keys))
        self.assertNotIn(first, (0, -1))
        self.assertEqual(first, motion_facts.motion_signature(signed(*long_keys)))
        moved = list(long_keys)
        moved[-1] += 5  # An end key moved: sampled ends are signed.
        self.assertNotEqual(first, motion_facts.motion_signature(signed(*moved)))
        short = signed(1, 2, 3)
        before = motion_facts.motion_signature(short)
        motion_facts.motion_signature(signed(*range(1, 5000)))  # Another object's keys use no shared budget.
        self.assertEqual(before, motion_facts.motion_signature(short))


class Scene:
    session_uid = 7


class StillObject(SimpleNamespace):
    """A stand-in object nothing moves: no keys, parent or constraints; mesh data without shape keys."""

    def __init__(self, **changes):
        data = SimpleNamespace(animation_data=None, shape_keys=None)
        super().__init__(type="MESH", data=data, animation_data=None, parent=None, constraints=[],
                         parent_type="OBJECT", parent_bone="", name="Still")
        for key, value in changes.items():
            setattr(data if key in ("data_animation", "shape_keys") else self,
                    {"data_animation": "animation_data"}.get(key, key), value)


class MotionSignature(unittest.TestCase):
    def test_a_still_object_signs_nothing_and_each_kind_of_motion_edit_signs(self):
        self.assertEqual(motion_facts.motion_signature(StillObject()), 0)
        animation = keyed(1, 96).animation_data
        keyed_shapes = SimpleNamespace(animation_data=animation)
        edits = {"keys": StillObject(animation_data=animation),
                 "constraint": StillObject(constraints=[SimpleNamespace(type="COPY_LOCATION", target=None,
                                                                         influence=1.0)]),
                 "parent": StillObject(parent=SimpleNamespace(name="Rig")),
                 "data animation": StillObject(data_animation=animation),
                 "shape-key animation": StillObject(shape_keys=keyed_shapes)}
        for kind, obj in edits.items():
            with self.subTest(kind):
                self.assertNotIn(motion_facts.motion_signature(obj, obj.data, obj.type), (0, -1))
        unanimated_shapes = StillObject(shape_keys=SimpleNamespace(animation_data=None))
        self.assertEqual(motion_facts.motion_signature(unanimated_shapes, unanimated_shapes.data, "MESH"), 0)


class Estimates(unittest.TestCase):
    def test_a_slow_scene_is_held_longer_each_time_and_a_fast_one_is_forgotten(self):
        now = [0.0]
        estimates = motion_guards.Estimates(clock=lambda: now[0])
        estimates.note(Scene, 0.4, 0.3)
        self.assertEqual(estimates.cost(Scene), 0.4)
        now[0] = motion_guards.HOLD_SECONDS + 1
        self.assertIsNone(estimates.cost(Scene))
        estimates.note(Scene, 0.4, 0.3)  # Slow again: held twice as long.
        now[0] += 2 * motion_guards.HOLD_SECONDS - 1
        self.assertEqual(estimates.cost(Scene), 0.4)
        estimates.note(Scene, 0.05, 0.3)
        self.assertIsNone(estimates.cost(Scene))
        for _ in range(20):
            estimates.note(Scene, 0.4, 0.3)
        self.assertEqual(estimates.found[7][2], motion_guards.MAX_HOLD_SECONDS)


class LargeEdits(unittest.TestCase):
    def test_each_question_has_its_own_budget_and_an_unknown_parent_ends_the_climb(self):
        root = thing("Root")
        planks = [thing("Plank %d" % index, parent=root) for index in range(3 * motion_scope.MAX_SCAN)]
        road = thing("Road")
        car = thing("Car", parent=road, keys=True)
        scope = motion_scope.Scope()
        self.assertFalse(any(scope.animates(plank) for plank in planks))
        self.assertIs(scope.top(thing("Body", parent=car)), car)
        chain = thing("Deep 0")
        for index in range(1, motion_scope.MAX_SCAN + 10):
            chain = thing("Deep %d" % index, parent=chain)
        moving = thing("Moving", parent=chain, keys=True)
        self.assertIs(motion_scope.Scope().top(thing("Child", parent=moving)), moving)


class Tolerances(unittest.TestCase):
    def test_a_millimetre_group_moves_by_a_tenth_of_a_millimetre_and_a_bridge_does_not(self):
        rows = [{"origin": [0.0, 0.0, 0.0], "bounds": None}, {"origin": [0.0001, 0.0, 0.0], "bounds": None}]
        self.assertTrue(motion_facts._moves(rows, 0.004))
        self.assertFalse(motion_facts._moves(rows, 40.0))
        self.assertEqual(motion_facts._digits([[0, 0, 0], [0.004, 0.004, 0.004]]), 5)
        self.assertEqual(motion_facts._digits(None), 3)

    def test_a_budget_reads_the_first_and_last_frames_first(self):
        self.assertEqual(motion_facts._first_ends([60, 1, 120, 30, 90]), [1, 120, 60, 90, 30])
        self.assertEqual(motion_facts._first_ends([5]), [5])


class Requests(unittest.TestCase):
    def test_scene_object_takes_up_to_eight_distinct_frames(self):
        checked = validate_scene("angelo_scene_object", {"object": HANDLE, "frames": [1, 60, 120]})
        self.assertEqual(checked["frames"], [1, 60, 120])
        for frames in ([], list(range(9)), [1, 1], [1.5], ["1"], [10 ** 7]):
            with self.subTest(frames=frames), self.assertRaises(ValueError):
                validate_scene("angelo_scene_object", {"object": HANDLE, "frames": frames})
        self.assertNotIn("frames", validate_scene("angelo_scene_object", {"object": HANDLE}))

    def test_a_compact_entry_keeps_the_motion(self):
        motion = {"frames": [1, 120], "moving": [{"name": "Car", "origin": [[0, 0, 0], [1, 0, 0]]}]}
        self.assertEqual(commit_facts({"motion": motion}), {"motion": motion})


class Feed(unittest.TestCase):
    def test_a_paused_read_records_nothing(self):
        recorded = []
        change_feed._feeds["chat"] = SimpleNamespace(baseline={"scene_uid": 1},
                                                    record=lambda epoch, updates: recorded.append(updates))
        self.addCleanup(change_feed.clear)
        scene = SimpleNamespace(session_uid=1)
        graph = SimpleNamespace(updates=[])
        with change_feed.paused():
            self.assertTrue(change_feed.is_paused())
            change_feed.record_depsgraph(scene, graph, "epoch")
        self.assertEqual(recorded, [])
        self.assertFalse(change_feed.is_paused())
        change_feed.record_depsgraph(scene, graph, "epoch")
        self.assertEqual(len(recorded), 1)

    def test_collected_edits_are_flagged_updates_and_users_of_an_updated_action(self):
        def obj(name, action=None, strips=()):
            tracks = [SimpleNamespace(strips=[SimpleNamespace(action=item) for item in strips])]
            animation = SimpleNamespace(action=action, nla_tracks=tracks) if action or strips else None
            return SimpleNamespace(id_type="OBJECT", name=name, animation_data=animation)

        def update(block, transform=False, geometry=False):
            return SimpleNamespace(id=block, is_updated_transform=transform, is_updated_geometry=geometry)
        keyed_action = SimpleNamespace(id_type="ACTION", as_pointer=lambda: 11)
        other_action = SimpleNamespace(id_type="ACTION", as_pointer=lambda: 12)
        rebuild = [update(obj("Moved"), transform=True), update(obj("Bent"), geometry=True), update(obj("Untouched")),
                   update(obj("Keyed", action=keyed_action)), update(obj("Strip", strips=[keyed_action])),
                   update(obj("Other", action=other_action)), update(keyed_action)]
        found = set()
        change_feed._collecting.append(found)
        self.addCleanup(change_feed._collecting.remove, found)
        change_feed.collect(SimpleNamespace(updates=rebuild))  # A relations rebuild lists every object unflagged.
        self.assertEqual(found, {"Moved", "Bent", "Keyed", "Strip"})
        with change_feed.paused():
            change_feed.collect(SimpleNamespace(updates=[update(obj("During a read"), transform=True)]))
        self.assertNotIn("During a read", found)

    def test_an_update_at_another_frame_is_time_moving_not_an_edit(self):
        found = change_feed.Touched()
        found.frame = (1, 0.0)
        change_feed._collecting.append(found)
        self.addCleanup(change_feed._collecting.remove, found)

        def graph(frame, name):
            block = SimpleNamespace(id_type="OBJECT", name=name, animation_data=None)
            return SimpleNamespace(scene=SimpleNamespace(frame_current=frame, frame_subframe=0.0),
                                   updates=[SimpleNamespace(id=block, is_updated_transform=True,
                                                            is_updated_geometry=False)])
        change_feed.collect(graph(20, "Scrubbed rig"))  # frame_current set by the script: every animated object.
        change_feed.collect(graph(20, "Edited after"))  # Same frame: an edit.
        self.assertEqual(set(found), {"Edited after"})


if __name__ == "__main__":
    unittest.main()
