# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The bound scene in the turn context: handle and settings revision, bound without a read.

Pure tables and compositions over host fakes. The real revision, staging and
commit are covered by test_blender_world.py and test_blender_turn_context.py.
"""

import json
import sys
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent.program_checks import required_checks
from angelo_agent import context_ledger, scene_binding, scene_settings, turn_context  # noqa: E402
from angelo_agent.identifiers import IdentifierError, IdentifierTable, expand_call  # noqa: E402
from angelo_agent.plan_schema import CLAUDE_CORE_ACTIONS, plan_arguments  # noqa: E402
from angelo_agent.tool_specs import TOOL_MAP, validate_call  # noqa: E402
from test_native_turn_context import Clock, TurnContextFixture  # noqa: E402

EPOCH = "a" * 32
SCENE, CUBE = "scn:%s:34" % EPOCH, "scn:%s:159" % EPOCH
SETTINGS, NEWER, CUBE_REVISION = "5" * 8 + "e" * 56, "6" * 8 + "f" * 56, "1" * 8 + "b" * 56
WORLD = {"color": [.48, .32, .2], "strength": .35}


def observation(revision=SETTINGS, **fields):
    scene = {"handle": SCENE}
    if revision:
        scene["revision"] = revision
    return dict({"scene": "Scene", "scene_settings": scene, "object_count": 1, "objects": [
        {"name": "Cube", "type": "MESH", "handle": CUBE, "location": [0., 0., 0.], "object_revision": CUBE_REVISION}]},
        **fields)


def program(*operations, **fields):
    return dict({"mode": "APPLY", "operations": list(operations)}, **fields)


def world(target, **parameters):
    return {"kind": "world_configure", "target": target, "parameters": dict(WORLD, **parameters)}


class ObservedSceneTests(unittest.TestCase):
    def setUp(self):
        self.table = IdentifierTable()
        self.shown = context_ledger.compact_observation(observation(), self.table)

    def test_the_model_reads_a_short_handle_and_revision(self):
        self.assertEqual(self.shown["scene_settings"], {"handle": "o1", "revision": SETTINGS[:8]})
        self.assertEqual(self.table.observed(SCENE), SETTINGS)
        self.assertLess(len(json.dumps(self.shown["scene_settings"], separators=(",", ":"))), 40)

    def test_a_scene_edit_binds_the_observed_settings_revision(self):
        for target in ("o1", SCENE):
            with self.subTest(target=target):
                resolved = expand_call("angelo_scene_program", program(
                    {"kind": "property_set", "target": target, "parameters": {"values": {"frame_end": 120}}}), self.table)
                self.assertEqual(resolved["write_set"], [{"handle": SCENE, "revision": SETTINGS}])
                canonical = validate_call("angelo_scene_program", resolved)
                self.assertEqual(canonical["operations"][0]["target"], SCENE)

    def test_a_null_world_target_is_the_observed_scene(self):
        resolved = expand_call("angelo_scene_program", program(world(None)), self.table)
        self.assertEqual(resolved["operations"][0]["target"], SCENE)
        self.assertEqual(resolved["write_set"], [{"handle": SCENE, "revision": SETTINGS}])
        canonical = validate_call("angelo_scene_program", resolved)
        self.assertEqual(required_checks(canonical), [{"kind": "world_settings", "target": SCENE, "values": WORLD}])

    def test_an_object_where_the_scene_belongs_is_refused_with_what_to_write(self):
        """Before validation and before any stage: the kernel would refuse it too, one round later."""
        for operation in (world("o2"), world(CUBE),
                          {"kind": "property_set", "target": "o2", "parameters": {"values": {"frame_end": 120}}},
                          {"kind": "marker_edit", "target": "o2", "parameters": {"operation": "ADD", "name": "A", "frame": 1}}):
            with self.subTest(kind=operation["kind"], target=operation["target"]):
                with self.assertRaisesRegex(ValueError, "OWNER_TYPE_MISMATCH: this edits the bound scene, which "
                                                        "needs no target: leave target out"):
                    expand_call("angelo_scene_program", program(operation), self.table)

    def test_a_word_where_the_scene_belongs_gets_the_same_remedy(self):
        """Recorded: "target": "scene" on the property_set that makes a camera the scene's."""
        for target in ("scene", "Scene", "SCENE", "active scene"):
            for table in (self.table, None):
                with self.subTest(target=target, table=table is not None):
                    with self.assertRaisesRegex(ValueError, "property_set OWNER_TYPE_MISMATCH: this edits the bound "
                                                            "scene, which needs no target: leave target out"):
                        expand_call("angelo_scene_program", program(
                            {"kind": "property_set", "target": target, "parameters": {"values": {"camera": "o2"}}}), table)
        # A word on an object's own edit is still the resolver's to answer.
        with self.assertRaisesRegex(ValueError, "not an object handle|handle"):
            expand_call("angelo_scene_program", program(
                {"kind": "transform", "target": "cube", "parameters": {"location": [0, 0, 1]}}), self.table)

    def test_a_node_edit_of_the_world_needs_no_target(self):
        edit = {"kind": "node_edit", "parameters": {"world": True, "edits": [
            {"op": "set", "node": "Background", "inputs": {"Strength": 0.5}}]}}
        self.assertTrue(scene_binding.scene_only(edit))
        self.assertFalse(scene_binding.scene_only({"kind": "node_edit", "parameters": {"material": "Stone"}}))
        resolved = expand_call("angelo_scene_program", program(edit), self.table)
        self.assertEqual(resolved["operations"][0]["target"], SCENE)
        self.assertEqual(resolved["write_set"], [{"handle": SCENE, "revision": SETTINGS}])

    def test_an_object_this_program_made_is_never_the_scene(self):
        """Pure: an alias is an object, with or without an observation (the new camera as target)."""
        made = {"kind": "camera_create", "parameters": {"alias": "cam"}}
        setting = {"kind": "property_set", "target": "$cam", "parameters": {"values": {"camera": "$cam"}}}
        for table in (self.table, IdentifierTable(), None):
            with self.subTest(table=table is not None):
                with self.assertRaisesRegex(ValueError, "property_set OWNER_TYPE_MISMATCH: .* leave target out"):
                    validate_call("angelo_scene_program", expand_call("angelo_scene_program", program(made, setting), table))

    def test_scene_settings_markers_and_a_collections_visibility_need_no_target(self):
        cases = ({"kind": "property_set", "parameters": {"values": {"frame_end": 120, "render.fps": 24}}},
                 {"kind": "property_set", "target": None, "parameters": {"values": {"camera": "$cam"}}},
                 {"kind": "marker_edit", "parameters": {"operation": "ADD", "name": "A", "frame": 1}},
                 {"kind": "visibility_set", "parameters": {"collection": "Props", "hidden": True}})
        made = {"kind": "camera_create", "parameters": {"alias": "cam"}}
        for operation in cases:
            with self.subTest(kind=operation["kind"]):
                resolved = expand_call("angelo_scene_program", program(made, operation), self.table)
                self.assertEqual(resolved["operations"][1]["target"], SCENE)
                self.assertEqual(resolved["write_set"], [{"handle": SCENE, "revision": SETTINGS}])
                self.assertEqual(validate_call("angelo_scene_program", resolved)["operations"][1]["target"], SCENE)

    def test_an_objects_own_edit_still_needs_its_target(self):
        for operation in ({"kind": "property_set", "target": None, "parameters": {"values": {"hide_render": True}}},
                          {"kind": "property_set", "parameters": {"values": {"data.lens": 35}}},
                          {"kind": "visibility_set", "parameters": {"hidden": True}},
                          {"kind": "transform", "target": None, "parameters": {"location": [0, 0, 1]}}):
            with self.subTest(operation=operation):
                with self.assertRaises(ValueError):
                    validate_call("angelo_scene_program", expand_call("angelo_scene_program", program(operation), self.table))

    def test_a_setting_of_both_is_left_to_its_owner(self):
        """Mixed paths are the kernel's MIXED_OWNERS: nothing is filled in for them."""
        mixed = {"kind": "property_set", "parameters": {"values": {"frame_end": 120, "hide_render": True}}}
        self.assertFalse(scene_binding.scene_only(mixed))
        self.assertEqual(scene_binding.implicit_targets(program(mixed), self.table), program(mixed))

    def test_a_scene_without_a_revision_must_be_declared(self):
        table = IdentifierTable()
        context_ledger.compact_observation(observation(revision=None), table)
        with self.assertRaisesRegex(IdentifierError, "no revision in your latest observation"):
            expand_call("angelo_scene_program", program(world(None)), table)
        declared = expand_call("angelo_scene_program", program(
            world(None), write_set=[{"handle": SCENE, "revision": NEWER}]), table)
        self.assertEqual(declared["write_set"], [{"handle": SCENE, "revision": NEWER}])

    def test_a_later_observation_or_commit_moves_the_binding(self):
        context_ledger.compact_observation(observation(revision=NEWER), self.table)
        self.assertEqual(self.table.observed(SCENE), NEWER)
        result = {"live_revisions": {"status": "COMMITTED", "objects": [
            {"handle": SCENE, "name": "Scene", "type": "SCENE", "role": "WRITTEN", "object_revision": SETTINGS}]}}
        self.table.compact(result)
        context_ledger.observe_committed(self.table, result)
        self.assertEqual(self.table.observed(SCENE), SETTINGS, "A committed settings edit binds the next one")

    def test_without_an_observed_scene_the_refusal_says_what_to_pass(self):
        for table in (IdentifierTable(), None):
            for operation in (world(None), {"kind": "world_configure", "parameters": dict(WORLD)}):
                with self.subTest(table=table is not None, written="target" in operation):
                    with self.assertRaisesRegex(ValueError, "pass the scene handle as target .*scene_settings.handle"):
                        validate_call("angelo_scene_program", expand_call("angelo_scene_program", program(operation), table))

    def test_implicit_targets_leaves_everything_else_alone(self):
        arguments = program(world(SCENE), {"kind": "create_primitive", "target": None,
                                           "parameters": {"primitive": "CUBE"}})
        self.assertEqual(scene_binding.implicit_targets(arguments, self.table), arguments)
        self.assertEqual(scene_binding.implicit_targets({"operations": "broken"}, self.table), {"operations": "broken"})
        self.assertEqual(scene_binding.implicit_targets(program(world(None)), IdentifierTable()), program(world(None)))
        created = {"kind": "create_primitive", "parameters": {"primitive": "CUBE"}}
        self.assertEqual(scene_binding.implicit_targets(program(created), IdentifierTable()),
                         program(dict(created, target=None)), "One resolved form, however it was written")


class OmittedTargetTests(unittest.TestCase):
    """A kind whose target can only be null needs none written: 14 bytes of every created object."""

    def test_a_creating_operation_needs_no_target(self):
        from angelo_agent.action_catalog import validate_operation
        from angelo_agent.action_schemas import OPERATION_SCHEMAS
        written = {"kind": "create_primitive", "parameters": {"primitive": "CUBE", "name": "Plank"}}
        self.assertEqual(validate_operation(written), validate_operation(dict(written, target=None)))
        self.assertNotIn("target", written)
        creating = [kind for kind, schema in OPERATION_SCHEMAS.items() if schema["properties"]["target"] == {"type": "null"}]
        self.assertIn("camera_create", creating)
        self.assertIn("object_duplicate", creating)
        self.assertNotIn("transform", creating)

    def test_an_operation_on_an_object_still_needs_its_target(self):
        from angelo_agent.action_catalog import validate_operation
        with self.assertRaisesRegex(ValueError, "missing required fields"):
            validate_operation({"kind": "transform", "parameters": {"location": [0, 0, 1]}})
        with self.assertRaises(ValueError):
            validate_operation({"kind": "create_primitive", "target": "o1", "parameters": {"primitive": "CUBE"}})

    def test_a_program_written_without_targets_is_the_same_program(self):
        full = program({"kind": "create_primitive", "target": None,
                        "parameters": {"primitive": "CUBE", "name": "Plank", "alias": "plank"}},
                       {"kind": "transform", "target": "$plank", "parameters": {"location": [0, 0, 1]}})
        short = json.loads(json.dumps(full))
        del short["operations"][0]["target"]
        self.assertEqual(validate_call("angelo_scene_program", expand_call("angelo_scene_program", short, IdentifierTable())),
                         validate_call("angelo_scene_program", expand_call("angelo_scene_program", full, IdentifierTable())))


class DeltaTests(unittest.TestCase):
    def blocks(self, received, value, table):
        blocks = context_ledger.TurnBlocks(received, identifiers=table)
        return blocks, blocks.observation_value(value)

    def test_the_scene_entry_is_state_sent_again_only_when_it_changes(self):
        table = IdentifierTable()
        first, (kind, _) = self.blocks(None, observation(), table)
        self.assertEqual(kind, "whole")
        ledger = dict(first.after, native_id="native")
        _, (kind, value) = self.blocks(ledger, observation(), table)
        self.assertEqual((kind, value), ("unchanged", None))
        _, (kind, value) = self.blocks(ledger, observation(revision=NEWER), table)
        self.assertEqual(kind, "delta")
        self.assertEqual(value["scene_settings"], {"handle": "o1", "revision": NEWER[:8]})
        self.assertEqual(value["objects"], [], "Nothing else changed")


class ModelFacingTests(unittest.TestCase):
    def test_kinds_that_edit_the_scene_show_an_optional_target(self):
        """Claude's plan schema here; a Codex thread's reference in test_native_codex_launch.py."""
        claude = plan_arguments(TOOL_MAP["angelo_scene_program"], CLAUDE_CORE_ACTIONS)
        variants = {item["properties"]["kind"].get("const"): item
                    for item in claude["properties"]["operations"]["items"]["oneOf"]}
        for kind in ("world_configure", "property_set"):
            self.assertIn({"type": "null"}, variants[kind]["properties"]["target"]["oneOf"], kind)
            self.assertNotIn("target", variants[kind]["required"], kind)
        for kind in ("transform", "object_delete", "light_configure"):
            self.assertNotIn("oneOf", variants[kind]["properties"]["target"], kind)
            self.assertIn("target", variants[kind]["required"], kind)

    def test_a_creating_kind_requires_no_target(self):
        """Nothing to choose, so nothing to write (the host fills null in: action_catalog.validate_operation)."""
        claude = plan_arguments(TOOL_MAP["angelo_scene_program"], CLAUDE_CORE_ACTIONS)
        for kind in ("create_primitive", "camera_create", "light_create"):
            variant = next(item for item in claude["properties"]["operations"]["items"]["oneOf"]
                           if item["properties"]["kind"].get("const") == kind)
            self.assertEqual(variant["properties"]["target"], {"type": "null"}, "Writing null stays valid")
            self.assertEqual(variant["required"], ["kind", "parameters"], kind)


class TurnContextTests(TurnContextFixture):
    def test_the_observation_states_the_scene_handle_and_settings_revision(self):
        with patch.object(scene_settings, "settings_revision", return_value=SETTINGS) as revision:
            entry = self.prepare().observation["scene_settings"]
        self.assertEqual(entry, {"handle": scene_settings.scene_handle(self.window.scene), "revision": SETTINGS})
        revision.assert_called_once_with(self.window.scene)

    def test_the_settings_revision_is_never_left_out_for_the_budget(self):
        """A small revision (revision_budget): when a turn's context left it out, the next program, which set a
        scene setting, was refused."""
        with patch.object(scene_settings, "settings_revision", return_value=SETTINGS) as revision:
            entry = self.prepare(clock=Clock(turn_context.REVISION_BUDGET_SECONDS)).observation["scene_settings"]
        self.assertEqual(entry["revision"], SETTINGS)
        self.assertNotIn("revision_omitted", entry)
        revision.assert_called_once_with(self.window.scene)

    def test_an_unreadable_revision_is_omitted_with_its_reason_and_the_send_proceeds(self):
        with patch.object(scene_settings, "settings_revision", side_effect=AttributeError("no such setting")):
            entry = self.prepare().observation["scene_settings"]
        self.assertEqual(entry["revision_omitted"], {"reason": "UNSUPPORTED", "detail": "revision_error:AttributeError"})

    def test_the_scene_entry_states_the_scene_camera(self):
        """Where a render looks from and along which direction: 'behind it' and 'facing the camera' need both."""
        from test_native_scene_queries import FakeObject, queries

        class Matrix:
            translation = (7.358891, -6.925791, 4.958309)
            rows = ((.6859, -.3240, .6516), (.7277, .3054, -.6142), (0., .8954, .4453))

            def __getitem__(self, row):
                return self.rows[row]

        camera = FakeObject(77, "Camera")
        camera.matrix_world = Matrix()
        self.window.scene.camera = camera
        self.window.scene.render = SimpleNamespace(engine="CYCLES")
        with patch.object(scene_settings, "settings_revision", return_value=SETTINGS):
            entry = self.prepare().observation["scene_settings"]
        self.assertEqual(entry.get("engine"), "CYCLES")
        self.assertEqual(entry["camera"], {"handle": queries.object_handle(camera),
                                           "location": [7.358891, -6.925791, 4.958309],
                                           "direction": [-.652, .614, -.445]})
        table = IdentifierTable()
        shown = context_ledger.compact_observation({"scene": "Scene", "scene_settings": entry, "object_count": 0,
                                                    "objects": []}, table)
        self.assertRegex(shown["scene_settings"]["camera"]["handle"], r"^o\d+$")
        self.assertLess(len(json.dumps(shown["scene_settings"]["camera"], separators=(",", ":"))), 100)
        # A scene without a camera, or a camera that cannot report its matrix, has no entry and the send proceeds.
        for broken in (None, FakeObject(78, "Broken")):
            self.window.scene.camera = broken
            with patch.object(scene_settings, "settings_revision", return_value=SETTINGS):
                self.assertNotIn("camera", self.prepare().observation["scene_settings"])

    def test_every_object_of_a_small_scene_gets_a_revision(self):
        """The default scene's Light and Camera: unnamed, unselected, and the first things a scene request removes."""
        from test_native_turn_context import make_window, object_id
        from test_native_scene_queries import FakeObject, queries

        def revised(count):
            objects = [object_id(FakeObject(900 + index, "Thing %d" % index, mesh=True)) for index in range(count)]
            window = make_window(objects, objects[0])
            entries = turn_context.prepare(window, None, clock=Clock(0.)).observation["objects"]
            return [entry for entry in entries if "object_revision" in entry], entries, window, objects

        small, entries, window, objects = revised(turn_context.SMALL_SCENE_OBJECTS)
        self.assertEqual(len(small), turn_context.SMALL_SCENE_OBJECTS)
        self.assertEqual(small[3]["object_revision"], queries.object_revision(window, objects[3]))
        larger, entries, _, _ = revised(turn_context.SMALL_SCENE_OBJECTS + 1)
        self.assertEqual(len(larger), 1, "Only the active object, as before")
        self.assertTrue(all("revision_omitted" not in entry for entry in entries))

    @patch("angelo_agent.revision_budget.SMALL_REVISION_VERTICES", 0)  # Every fake mesh counts as large.
    def test_small_scene_revisions_share_the_cooperative_budget(self):
        from test_native_turn_context import make_window, object_id
        from test_native_scene_queries import FakeObject
        objects = [object_id(FakeObject(950 + index, "Thing %d" % index, mesh=True)) for index in range(4)]
        entries = turn_context.prepare(make_window(objects, objects[0]), None,
                                       clock=Clock(turn_context.REVISION_BUDGET_SECONDS)).observation["objects"]
        self.assertTrue(all(entry.get("revision_omitted", {}).get("reason") == "BUDGET" for entry in entries))

    def test_the_legacy_observation_is_unchanged(self):
        legacy = {"scene": "Turn scene", "object_count": 40, "objects": [], "truncated": True}
        with patch.object(turn_context, "legacy_observation", return_value=dict(legacy)), \
                patch.object(turn_context.change_feed, "legacy_turn_context", return_value=True):
            self.assertEqual(turn_context.prepare(self.window, "chat-a").observation, legacy)


if __name__ == "__main__":
    unittest.main()
