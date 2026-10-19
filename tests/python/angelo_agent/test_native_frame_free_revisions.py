# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An object revision carries the frame only when the object can change with time.

Recorded run (GPT-6 Astra, one-shot bridge, trial 1): the model's own script
called ``scene.frame_set(60)``; every object revision hashed the current frame,
and the script's result re-reported only the objects whose signature changed
(the animated car), so the next program's 16 untouched rocks were refused as
STALE_REVISION and the request's recovery barrier then held. Now a revision
leaves the frame out for an object nothing can move in time
(``revision_time.still``), and keeps it, as before, for one that can:
animation, drivers, constraints, physics, a time-dependent modifier, or an
object it follows (parent, Mirror or Array object, focus object) that can.
"""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_native_scene_queries as fixtures
from angelo_agent import revision_time
from angelo_agent.program_frames import frame_independent as rebind_frame_independent
from angelo_agent.revision_time import MAX_FRAME_SCAN_OBJECTS, still as frame_independent

queries = fixtures.queries
fingerprint_globals = queries._fingerprints.capture.__func__.__globals__
metadata_globals = queries._metadata.settings.__func__.__globals__


def modifier(kind, **values):
    return SimpleNamespace(type=kind, name=kind, show_viewport=True, show_render=True,
                           show_in_editmode=False, show_on_cage=False, **values)


def nla_animated():
    """Animation data that makes ``program_frames._animated`` true without an action to read."""
    track = SimpleNamespace(name="Track", mute=False, is_solo=False, lock=False, select=False, strips=[])
    return SimpleNamespace(action=None, nla_tracks=[track], drivers=[], action_slot_handle=0,
                           action_blend_type="REPLACE", action_extrapolation="HOLD", action_influence=1.0,
                           use_nla=True, use_tweak_mode=False)


class Thing(SimpleNamespace):
    """A minimal object for the dependency-free predicate."""

    def __init__(self, kind="MESH", **values):
        defaults = dict(type=kind, parent=None, constraints=[], modifiers=[], data=SimpleNamespace(),
                        animation_data=None, rigid_body=None, particle_systems=[])
        defaults.update(values)
        super().__init__(**defaults)


class FrameIndependentTests(unittest.TestCase):
    """``revision_time.still``, named ``frame_independent`` here for what it decides."""

    def test_the_rebind_rule_stays_narrower(self):
        """native_programs.rebind keeps program_frames.frame_independent, which refuses any parent."""
        child = Thing(parent=Thing("EMPTY", data=None))
        self.assertTrue(frame_independent(child))
        self.assertFalse(rebind_frame_independent(child))

    def test_still_objects_and_still_parents(self):
        parent = Thing("EMPTY", data=None)
        self.assertTrue(frame_independent(Thing()))
        self.assertTrue(frame_independent(Thing(parent=parent)))
        self.assertTrue(frame_independent(Thing(parent=Thing(parent=parent))))

    def test_anything_that_can_move_it_in_time_makes_it_dependent(self):
        animated = Thing(animation_data=nla_animated())
        wave = Thing(modifiers=[modifier("WAVE")])
        cases = {
            "own animation": Thing(animation_data=nla_animated()),
            "a constraint": Thing(constraints=[SimpleNamespace(type="COPY_LOCATION")]),
            "an animated parent": Thing(parent=animated),
            "an animated grandparent": Thing(parent=Thing(parent=animated)),
            "a bone parent": Thing(parent=Thing("ARMATURE"), parent_type="BONE"),
            "a time-dependent modifier": wave,
            "an animated mirror object": Thing(modifiers=[modifier("MIRROR", mirror_object=animated)]),
            "an array cap with a time-dependent modifier": Thing(modifiers=[modifier(
                "ARRAY", offset_object=None, start_cap=wave, end_cap=None, curve=None)]),
            "an animated focus object": Thing("CAMERA", data=SimpleNamespace(
                dof=SimpleNamespace(focus_object=animated))),
            "a keyed light node tree": Thing("LIGHT", data=SimpleNamespace(node_tree=SimpleNamespace(
                animation_data=nla_animated(), nodes=[]))),
            "physics": Thing(rigid_body=object()),
            "an unsupported type": Thing("CURVE"),
        }
        for label, thing in cases.items():
            with self.subTest(label):
                self.assertFalse(frame_independent(thing))

    def test_a_reference_cycle_ends_and_a_long_chain_is_bounded(self):
        first, second = Thing(), Thing()
        first.modifiers = [modifier("MIRROR", mirror_object=second)]
        second.modifiers = [modifier("MIRROR", mirror_object=first)]
        self.assertTrue(frame_independent(first))
        second.animation_data = nla_animated()
        self.assertFalse(frame_independent(first))
        chain = Thing("EMPTY", data=None)
        for _ in range(MAX_FRAME_SCAN_OBJECTS + 1):
            chain = Thing("EMPTY", data=None, parent=chain)
        self.assertFalse(frame_independent(chain))

    def test_one_object_seen_through_fresh_wrappers_is_visited_once(self):
        """Blender may hand out a new Python wrapper per access; visits are keyed by the object's pointer.

        Two still objects mirror each other and every access returns a new
        wrapper: keyed by wrapper, the walk would run to the object bound and
        call them dependent.
        """
        class Fresh(SimpleNamespace):
            def __init__(self, pointer):
                super().__init__(type="MESH", parent=None, constraints=[], data=SimpleNamespace(), pointer=pointer,
                                 animation_data=None, rigid_body=None, particle_systems=[])

            def as_pointer(self):
                return self.pointer

            @property
            def modifiers(self):
                return [modifier("MIRROR", mirror_object=Fresh(pointer=3 - self.pointer))]

        self.assertTrue(frame_independent(Fresh(pointer=1)))


class RevisionContextTests(unittest.TestCase):
    def setUp(self):
        for namespace in (fingerprint_globals, metadata_globals):
            context = patch.dict(namespace, {"bpy": fixtures.fake_bpy})
            context.start()
            self.addCleanup(context.stop)
        queries.invalidate()
        self.rock = fixtures.FakeObject(201, "Rock", mesh=True)
        self.post = fixtures.FakeObject(202, "Post", mesh=True)
        self.lantern = fixtures.FakeObject(203, "Lantern", mesh=True)
        self.lantern.parent = self.post
        self.window = fixtures.window([self.rock, self.post, self.lantern])

    def revision(self, obj):
        return queries.object_revision(self.window, obj)

    def test_a_frame_change_keeps_still_objects_revisions(self):
        before = {obj.name: self.revision(obj) for obj in (self.rock, self.post, self.lantern)}
        self.window.scene.frame_current = 60
        after = {obj.name: self.revision(obj) for obj in (self.rock, self.post, self.lantern)}
        self.assertEqual(before, after)
        self.assertEqual(queries.object_revision_at_frame(self.window, self.rock, 1), before["Rock"])

    def test_an_object_that_can_change_with_time_keeps_the_frame(self):
        self.rock.modifiers = [modifier("BOOLEAN", object=self.post)]  # Not an allowed modifier: time-dependent.
        at_one = self.revision(self.rock)
        self.window.scene.frame_current = 60
        at_sixty = self.revision(self.rock)
        self.assertNotEqual(at_one, at_sixty)
        self.assertEqual(queries.object_revision_at_frame(self.window, self.rock, 1), at_one)

    def test_a_child_follows_its_parents_time_dependence(self):
        still = self.revision(self.lantern)
        self.post.animation_data = nla_animated()
        animated = self.revision(self.lantern)
        self.assertNotEqual(still, animated, "Gaining a moving parent changes the child's revision")
        self.window.scene.frame_current = 60
        self.assertNotEqual(animated, self.revision(self.lantern))

    def test_a_subframe_changes_a_time_dependent_revision_only(self):
        """Regression (F6): the binding carries the subframe; a time-dependent revision now does too."""
        self.rock.modifiers = [modifier("BOOLEAN", object=self.post)]
        self.window.scene.frame_current = 10
        whole_rock, whole_post = self.revision(self.rock), self.revision(self.post)
        self.window.scene.frame_subframe = 0.5
        self.assertNotEqual(self.revision(self.rock), whole_rock)
        self.assertEqual(self.revision(self.post), whole_post)
        self.window.scene.frame_subframe = 0.0
        self.assertEqual(self.revision(self.rock), whole_rock, "a whole frame keeps the value it always had")

    def test_a_pass_judges_each_object_and_each_shared_group_once(self):
        """Regression (F1): stillness ran for every capture, exact or not, and rescanned shared groups."""
        class Group:  # Hashable, as Blender's node groups are.
            animation_data, nodes = None, [SimpleNamespace(bl_idname="GeometryNodeSetPosition")]
        group = Group()
        props = [Thing(modifiers=[modifier("NODES", node_group=group)]) for _ in range(6)]
        scans = []
        original = revision_time._graph_frame_independent

        def counted(tree, seen, budget):
            scans.append(tree)
            return original(tree, seen, budget)
        memo = {}
        with patch.object(revision_time, "_graph_frame_independent", counted):
            self.assertTrue(all(revision_time.still_in_pass(prop, memo) for prop in props + props))
        self.assertEqual(len(scans), 1, "a group six objects share is scanned once a pass")
        with patch.object(revision_time, "still", side_effect=AssertionError("judged a capture with no revision")):
            with patch.object(queries, "MAX_ELEMENTS", 1):
                state = queries._fingerprints.capture(self.window, self.rock, queries._read_budget())
        self.assertFalse(state["exact"])

    def test_becoming_time_dependent_changes_the_revision_at_the_same_frame(self):
        """None versus a frame number is hashed, so a frame-free revision never matches a dependent one."""
        state = queries._fingerprints.capture(self.window, self.rock, queries._read_budget())
        with patch.object(revision_time, "still", return_value=False):
            dependent = queries._fingerprints.capture(self.window, self.rock, queries._read_budget())
        self.assertEqual({k: v for k, v in state["fields"].items() if k != "context"},
                         {k: v for k, v in dependent["fields"].items() if k != "context"})
        self.assertNotEqual(state["summary"]["object_revision"], dependent["summary"]["object_revision"])


if __name__ == "__main__":
    unittest.main()
