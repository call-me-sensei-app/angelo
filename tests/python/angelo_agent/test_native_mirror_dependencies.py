# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Mirror-origin edge roles in real bounded scene captures; native proof is separate."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_native_scene_queries as fixtures

queries = fixtures.queries
# The shared fake-bpy fixture restores sys.modules after import. Reuse its
# existing owner/globals; a second import would create a different document epoch.
fingerprint_globals = queries._fingerprints.capture.__func__.__globals__
metadata_globals = queries._metadata.settings.__func__.__globals__


def modifier(kind, **values):
    return SimpleNamespace(type=kind, name=kind, show_viewport=True, show_render=True,
                           show_in_editmode=False, show_on_cage=False, **values)


class MirrorDependencyTests(unittest.TestCase):
    def setUp(self):
        for namespace in (fingerprint_globals, metadata_globals):
            context = patch.dict(namespace, {"bpy": fixtures.fake_bpy})
            context.start()
            self.addCleanup(context.stop)
        queries.invalidate()
        self.owner = fixtures.FakeObject(101, "Owner", mesh=True)
        self.origin = fixtures.FakeObject(102, "Origin", mesh=True)
        self.other = fixtures.FakeObject(103, "Other", mesh=True)
        self.window = fixtures.window([self.owner, self.origin, self.other])
        self.origin.modifiers = [modifier("SUBSURF", levels=1, render_levels=1)]
        self.owner.modifiers = [modifier("MIRROR", mirror_object=self.origin)]

    def targets(self):
        unsupported = []
        result = fingerprint_globals["_dependency_targets"](self.owner, unsupported)
        return result, unsupported

    def capture(self, budget=None):
        return queries._fingerprints.capture(self.window, self.owner, budget or queries._read_budget())

    def revision(self):
        return queries.object_revision(self.window, self.owner)

    def test_one_and_repeated_mirror_edges_keep_one_exclusive_target_and_charge(self):
        first_budget = queries._read_budget()
        first = self.capture(first_budget)
        self.assertTrue(first["exact"])
        self.owner.modifiers.append(modifier("MIRROR", mirror_object=self.origin))
        second_budget = queries._read_budget()
        second = self.capture(second_budget)
        self.assertEqual(self.targets(), ({102: (self.origin, True)}, []))
        self.assertTrue(second["exact"])
        self.assertEqual(second["captured"]["dependencies"], first["captured"]["dependencies"])
        # Only the extra owner's modifier is charged; the target is still read once.
        self.assertEqual(first_budget.items - second_budget.items, 1)
        self.assertEqual(first_budget.elements, second_budget.elements)
        self.assertEqual(first_budget.packed, second_budget.packed)

    def test_ordinary_modifier_role_dominates_before_and_after_mirror(self):
        mirror = self.owner.modifiers[0]
        boolean = modifier("BOOLEAN", object=self.origin)
        for stack in ([mirror, boolean], [boolean, mirror], [mirror, boolean, mirror]):
            with self.subTest(types=[m.type for m in stack]):
                self.owner.modifiers = stack
                self.assertEqual(self.targets()[0], {102: (self.origin, False)})
                with self.assertRaisesRegex(ValueError, "nested_dependency_configuration"):
                    self.revision()

    def test_roles_are_per_target_and_do_not_transfer_between_objects(self):
        self.owner.modifiers.append(modifier("BOOLEAN", object=self.other))
        self.assertEqual(self.targets()[0], {102: (self.origin, True), 103: (self.other, False)})
        self.assertTrue(self.capture()["exact"])
        self.other.modifiers = [modifier("SUBSURF")]
        with self.assertRaisesRegex(ValueError, "nested_dependency_configuration"):
            self.revision()

    def test_null_missing_self_and_nonobject_values_do_not_become_targets(self):
        self.owner.modifiers = [modifier("MIRROR", mirror_object=None), modifier("MIRROR"),
                                modifier("MIRROR", mirror_object=self.owner), modifier("BEVEL", width=.1)]
        self.assertEqual(self.targets(), ({}, []))

    def test_exact_modifier_type_and_field_qualify_not_other_object_fields(self):
        self.owner.modifiers = [modifier("MIRROR", use_axis=self.origin)]
        self.assertEqual(self.targets()[0], {102: (self.origin, False)})
        self.owner.modifiers = [modifier("ARRAY", offset_object=self.origin)]
        self.assertEqual(self.targets()[0], {102: (self.origin, False)})

    def test_parent_camera_focus_and_constraint_edges_remain_ordinary(self):
        for edge in ("parent", "focus", "target", "pole_target", "space_object", "targets", "pose"):
            with self.subTest(edge=edge):
                self.owner.parent, self.owner.constraints, self.owner.pose = None, [], None
                self.owner.type, self.owner.data = "MESH", fixtures.FakeMesh(1101)
                if edge == "parent":
                    self.owner.parent = self.origin
                elif edge == "focus":
                    self.owner.type = "CAMERA"
                    self.owner.data = SimpleNamespace(dof=SimpleNamespace(focus_object=self.origin))
                elif edge == "pose":
                    self.owner.pose = SimpleNamespace(bones=[SimpleNamespace(constraints=[
                        SimpleNamespace(type="COPY_LOCATION", target=self.origin)])])
                else:
                    values = {edge: [SimpleNamespace(target=self.origin)] if edge == "targets" else self.origin}
                    self.owner.constraints = [SimpleNamespace(type="COPY_LOCATION", **values)]
                self.assertEqual(self.targets()[0], {102: (self.origin, False)})

    def test_generic_modifier_particle_and_unknown_constraint_edges_retained(self):
        generic = modifier("UNKNOWN")
        settings = SimpleNamespace()
        constraint = SimpleNamespace(type="UNKNOWN")
        for edge in ("modifier", "particle", "constraint"):
            with self.subTest(edge=edge):
                self.owner.modifiers = [modifier("MIRROR", mirror_object=self.origin)]
                self.owner.particle_systems, self.owner.constraints = [], []
                if edge == "modifier":
                    self.owner.modifiers.append(generic)
                    expected = generic
                elif edge == "particle":
                    self.owner.particle_systems = [SimpleNamespace(settings=settings), SimpleNamespace(settings=None)]
                    expected = settings
                else:
                    self.owner.constraints = [constraint]
                    expected = constraint
                pointers = Mock(return_value=[self.origin])
                with patch.dict(fingerprint_globals, {"object_pointers": pointers}):
                    self.assertEqual(self.targets()[0], {102: (self.origin, False)})
                    pointers.assert_called_once_with(expected)

    def test_collections_keep_limitation_and_each_reference_getter_is_read_once(self):
        class CountedMirror:
            type = "MIRROR"
            reads = 0

            @property
            def mirror_object(inner):
                inner.reads += 1
                return self.origin

        counted = CountedMirror()
        collection = fixtures.FakeCollection(201, "Collection")
        self.owner.modifiers = [counted, modifier("BOOLEAN", collection=collection)]
        self.assertEqual(self.targets(), ({102: (self.origin, True)}, ["collection_instance_or_modifier_dependency"]))
        self.assertEqual(counted.reads, 1)

    def test_parent_constraint_and_pose_refusals_are_not_exempted(self):
        for field, value in (("parent", self.other),
                             ("constraints", [SimpleNamespace(name="Copy", type="COPY_LOCATION", target=self.other)]),
                             ("pose", SimpleNamespace(bones=[SimpleNamespace(constraints=[object()])]))):
            with self.subTest(field=field), patch.object(self.origin, field, value):
                with self.assertRaisesRegex(ValueError, "nested_dependency_configuration"):
                    self.revision()

    def test_physics_excludes_only_new_modifier_exemption(self):
        for field in ("rigid_body", "rigid_body_constraint"):
            with self.subTest(field=field), patch.object(self.origin, field, object(), create=True):
                with self.assertRaisesRegex(ValueError, "nested_dependency_configuration"):
                    self.revision()
                # This owner capture's historical plain-origin behavior is unchanged.
                with patch.object(self.origin, "modifiers", []):
                    self.assertTrue(self.capture()["exact"])

    def test_sculpt_and_unknown_data_refusals_remain(self):
        with patch.object(self.origin, "mode", "SCULPT"):
            with self.assertRaisesRegex(ValueError, "live_sculpt_dependency"):
                self.revision()
        with patch.object(self.origin, "type", "VOLUME"):
            with self.assertRaisesRegex(ValueError, "unfingerprinted_dependency_data"):
                self.revision()

    def test_driver_metadata_refuses_without_reading_expression_or_evaluating(self):
        class Driver:
            type = "SCRIPTED"
            variables = []

            @property
            def expression(self):
                raise AssertionError("Must not read or evaluate a driver expression")

        driver = SimpleNamespace(data_path="location", array_index=0, driver=Driver(), mute=False, is_valid=True)
        self.origin.animation_data = SimpleNamespace(action=None, nla_tracks=[], drivers=[driver])
        with self.assertRaisesRegex(ValueError, "animation_driver_expressions"):
            self.revision()

    def test_retained_dependency_capture_and_invalidation_ids(self):
        state = self.capture()
        dependency = state["captured"]["dependencies"][0]
        self.assertEqual(set(dependency), {"object", "matrix_world", "transform", "geometry", "mesh_limitations",
                         "rig", "animation", "data_animation", "shape_key_animation", "materials"})
        self.assertEqual(state["captured"]["dependency_ids"], [102])
        self.assertTrue({1, 101, 102, self.owner.data.session_uid}.issubset(queries.dependency_ids(state)))
        self.assertIsNotNone(dependency["geometry"])

    def test_original_data_transform_and_identity_change_revision_a_still_objects_frame_does_not(self):
        previous = self.revision()
        self.origin.data.vertices[0].co[0] += .25
        changed = self.revision()
        self.assertNotEqual(previous, changed)
        self.origin.location[0] += 2
        self.origin.matrix_world[0][3] += 2
        moved = self.revision()
        self.assertNotEqual(changed, moved)
        # Nothing moves the owner or its mirror origin in time, so the frame is not in its revision
        # (scene_fingerprints.context_field); test_native_frame_free_revisions covers the time-dependent side.
        self.window.scene.frame_current = 2
        self.assertEqual(moved, self.revision())
        self.assertEqual(queries.object_revision_at_frame(self.window, self.owner, 1), moved)
        self.owner.modifiers[0].mirror_object = self.other
        self.assertNotEqual(moved, queries.object_revision_at_frame(self.window, self.owner, 1))

    def test_origin_modifier_configuration_keeps_separate_full_revision(self):
        owner_before, origin_before = self.revision(), queries.object_revision(self.window, self.origin)
        self.origin.modifiers[0].levels = 2
        # The origin's separately observed revision still binds its complete modifier settings.
        self.assertNotEqual(queries.object_revision(self.window, self.origin), origin_before)
        self.assertEqual(self.revision(), owner_before)

    def test_frame_recovery_reads_the_object_once(self):
        at_one = self.revision()
        self.window.scene.frame_current = 2
        capture = queries._fingerprints.capture
        with patch.object(queries._fingerprints, "capture", wraps=capture) as counted:
            self.assertEqual(queries.object_revision_at_frame(self.window, self.owner, 1), at_one)
        self.assertEqual(counted.call_count, 1, "One capture both gates exactness and is hashed")

    def test_an_incomplete_frame_capture_is_never_hashed_as_exact(self):
        at_one = self.revision()
        self.window.scene.frame_current = 2
        with patch.object(queries, "MAX_ELEMENTS", 1):
            exhausted = queries._read_budget()
        with self.assertRaisesRegex(ValueError, "original_state_work_budget"):
            queries._fingerprints.revision_at_frame(self.window, self.owner, exhausted, 1)
        # Through the public read: a pass that ran out of budget refuses, whichever capture it is.
        budgets = iter([queries._read_budget(), exhausted, exhausted])
        with patch.object(queries, "_read_budget", side_effect=lambda deadline=None: next(budgets)):
            try:
                revision = queries.object_revision_at_frame(self.window, self.owner, 1)
            except ValueError as error:
                self.assertIn("original_state_work_budget", str(error))
            else:
                self.assertEqual(revision, at_one, "A returned revision is the exact one, never a partial hash")

    def test_budget_exhaustion_and_epoch_replacement_still_refuse(self):
        with patch.object(queries, "MAX_ELEMENTS", 1):
            with self.assertRaisesRegex(ValueError, "original_state_work_budget"):
                self.revision()
        capture = queries._fingerprints
        with patch.object(capture, "current_epoch", side_effect=[1, 2]):
            with self.assertRaisesRegex(ValueError, "Document IDs were replaced"):
                self.capture()


if __name__ == "__main__":
    unittest.main()
