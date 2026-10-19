# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exact synchronous native rights, cleanup ordering and primitive transfer transport."""

import ast
import contextlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent_runtime_fixtures import AgentRuntimeFixture
import test_native_program_storage as fixtures
from angelo_agent import program_commit_scope as scopes, program_storage as storage, program_transfers as transfers
from angelo_agent import program_identities
from angelo_agent import node_edit_commit
from angelo_agent.program_commit import ProgramCommitMixin
from angelo_agent.program_jobs import ProgramError
from angelo_agent.program_dependencies import SuspendedStage


class Bridge:
    def __init__(self, current=None):
        self.current = current if current is not None else {}
        self.calls = []
        self.partial = False

    def stage_residue(self, scope):
        return {"remaining": [], "unknown": [], "error": ""}

    def stage_owned(self, scope):
        return []

    def seal_approved_objects(self, scope, roots):
        self.calls.append(("seal", scope, roots))

    def delete_stage(self, scope, roots, keep=()):
        self.calls.append(("stage", scope, roots, keep))
        removed = []
        for index, block in enumerate(roots):
            if self.partial and index:
                break
            removed.append(scopes.identity(block))
            self.current.pop(block.as_pointer(), None)
            block.alive = False
        remaining = [scopes.identity(block) for block in roots if block.alive]
        return {"success": not remaining, "kernel_called": True, "removed": removed,
                "remaining": remaining, **({"error": "partial native deletion"} if remaining else {})}

    def delete_approved_objects(self, scope, roots):
        self.calls.append(("approved", scope, roots))
        return {"success": True, "kernel_called": True, "remaining": [], "removed": [scopes.identity(x) for x in roots]}

    def new_collection(self, scope, name):
        self.calls.append(("new_collection", scope, name))
        return fixtures.FakeID(90, "Collection")

    def delete_created_collections(self, scope, roots):
        self.calls.append(("created", scope, roots))
        return {"success": True, "kernel_called": True, "remaining": [], "removed": [scopes.identity(x) for x in roots]}

    def engine_prepare(self, scope, scene, expected, target):
        self.calls.append(("engine_prepare", scope, scene, expected, target))
        return (scene, expected, target)

    def engine_apply(self, scope, right, rollback=False, /):
        self.calls.append(("engine_apply", scope, right, rollback))
        right[0].render.engine = right[1 if rollback else 2]
        return {"success": True, "setter_called": True, "update_called": True}


class ID(fixtures.FakeID):
    def get(self, key, default=None):
        return self.values.get(key, default)

    def __setitem__(self, key, value):
        self.values[key] = value

    def __delitem__(self, key):
        del self.values[key]


def preparation(backend, originals=None, program=None):
    program = program or {"operations": []}
    binding = {"originals": originals or {}}
    job = SimpleNamespace(id="job", backend=backend, binding=binding, program=program)
    owner = SimpleNamespace(job=job, binding=binding, approved_program=program,
                            approved_deletions=scopes.approved_deletions(program, binding["originals"]),
                            invocation_id="call", document=("document", 1), epoch=3, generation=7)
    call = SimpleNamespace(native_preparation=owner)
    controller = SimpleNamespace(prepare_native_finish=Mock(), record_native_residue=Mock())
    return controller, call, owner


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.backend = fixtures.Backend()
        self.bridge, self.native = Bridge(), object()
        self.controller, self.call, self.preparation = preparation(self.backend)

    def bind(self):
        return scopes.bind(self.controller, self.call, self.preparation, self.bridge, self.native)

    def test_exact_owner_binding_and_finally_cleanup_precede_clear(self):
        def finish(call, preparation):
            self.assertIs(scopes.for_backend(self.backend), scopes.active())
            self.assertIs(call, self.call)
            self.assertIs(preparation, self.preparation)
        self.controller.prepare_native_finish.side_effect = finish
        with self.assertRaisesRegex(RuntimeError, "commit failed"):
            with self.bind() as scope:
                self.assertIs(scope.native_scope, self.native)
                with self.assertRaises(ProgramError):
                    with self.bind():
                        self.fail("nested scope entered")
                with self.assertRaises(ProgramError):
                    scopes.for_backend(fixtures.Backend())
                raise RuntimeError("commit failed")
        self.controller.prepare_native_finish.assert_called_once()
        self.assertIsNone(scopes.active())
        self.assertIsNone(self.backend._native_commit_scope)
        with self.assertRaises(ProgramError):
            scope.delete_stage(())

    def test_cleanup_runs_when_seal_fails_and_owner_still_clears(self):
        self.bridge.seal_approved_objects = Mock(side_effect=RuntimeError("seal failed"))
        with self.assertRaisesRegex(RuntimeError, "seal failed"):
            with self.bind():
                self.fail("failed seal yielded")
        self.controller.prepare_native_finish.assert_called_once()
        self.assertIsNone(scopes.active())

    def test_deletion_authority_uses_original_objects_not_mutable_binding(self):
        original, replacement = fixtures.FakeID(1, "Object"), fixtures.FakeID(2, "Object")
        program = {"operations": [{"kind": "object_delete", "target": "handle"}]}
        self.controller, self.call, self.preparation = preparation(self.backend, {"handle": original}, program)
        self.preparation.binding["originals"]["handle"] = replacement
        with self.bind() as scope:
            self.assertEqual(self.bridge.calls[0], ("seal", self.native, (original,)))
            scope.delete_approved_objects((original,))
            made = scope.new_collection("Collection")
            scope.delete_created_collections((made,))
        self.assertEqual([row[0] for row in self.bridge.calls], ["seal", "approved", "new_collection", "created"])

    def test_engine_setter_and_original_custom_engine_rollback_use_same_right(self):
        scene, staged = fixtures.FakeID(1, "Scene"), fixtures.FakeID(2, "Scene")
        property = SimpleNamespace(type="ENUM")
        scene.render = SimpleNamespace(engine="CUSTOM_RENDERER", bl_rna=SimpleNamespace(properties={"engine": property}), id_data=scene)
        staged.render = SimpleNamespace(engine="CYCLES", bl_rna=SimpleNamespace(properties={"engine": property}), id_data=staged)
        self.backend.scene = scene
        self.preparation.binding["scene"] = {"handle": "scene", "write": True}
        self.preparation.approved_program = {"operations": [{"kind": "property_set", "target": "scene",
            "parameters": {"values": {"render.engine": "CYCLES"}}}]}
        journal = []
        with self.bind() as scope:
            with patch.object(transfers, "holder_and_name", side_effect=lambda obj, path: (obj.render, "engine")):
                transfers._transfer_settings(scene, staged, ["render.engine"], {}, journal, commit_scope=scope)
            self.assertEqual(scene.render.engine, "CYCLES")
            for holder, name, value in reversed(journal):
                setattr(holder, name, value)
            self.assertEqual(scene.render.engine, "CUSTOM_RENDERER")
        self.assertEqual([row[-1] for row in self.bridge.calls if row[0] == "engine_apply"], [False, True])
        self.assertEqual(self.bridge.calls[1][3:], ("CUSTOM_RENDERER", "CYCLES"))

    def test_unapproved_deletion_is_refused_before_unlink_or_live_write(self):
        original, extra = ID(1, "Object"), ID(2, "Object")
        program = {"operations": [{"kind": "object_delete", "target": "approved"}]}
        self.controller, self.call, self.preparation = preparation(self.backend, {"approved": original, "extra": extra}, program)
        stage = SimpleNamespace(binding=self.preparation.binding, originals={"approved": original, "extra": extra},
                                extras={"deleted": ["extra"]})
        with self.bind() as scope:
            with self.assertRaisesRegex(ProgramError, "original approved"):
                scope.validate_stage(stage)
        self.assertEqual([row[0] for row in self.bridge.calls], ["seal"])

    def test_sealed_transfer_transport_is_primitive_and_not_a_public_result(self):
        original, staged, made = [fixtures.FakeID(index, "Object") for index in (1, 2, 3)]
        original.data, staged.data = fixtures.FakeID(4), fixtures.FakeID(5)
        made.data = None
        self.controller, self.call, self.preparation = preparation(self.backend, {"handle": original})
        stage = SimpleNamespace(binding=self.preparation.binding, extras={}, originals={"handle": original},
                                objects={"handle": staged}, context=SimpleNamespace(aliases={"made": made}))
        with self.bind() as scope:
            scope.record_transfer(staged.data, original.data, "actual_source_restore", "handle/data")
            scope.seal_transfers(stage, [made], [original, original.data, made])
            sealed = self.preparation.job.committed_transfers
            staged.alive = staged.data.alive = False
            self.assertEqual(sealed.targets, (("handle", (1, 10, "OBJECT")), ("$made", (3, 30, "OBJECT"))))
            self.assertIn(((5, 50, "MESH"), (4, 40, "MESH"), "actual_source_restore", "handle/data"), sealed.resources)
            with self.assertRaises(ProgramError):
                scope.record_transfer(original, made, "late")
        self.assertFalse(hasattr(sealed, "success"))
        self.assertEqual(sealed.invocation_id, "call")

    def test_name_conflict_removes_only_native_created_collection(self):
        self.backend = ProgramCommitMixin()
        self.backend._collections_named = lambda names: []
        self.controller, self.call, self.preparation = preparation(self.backend)
        stage = SimpleNamespace(context=SimpleNamespace(aliases={}), originals={"handle": ID(1, "Object")},
                                extras={"collections": {"handle": [{"collection": "Wanted", "create": True}]}})
        module = SimpleNamespace(MASTER_COLLECTION="Scene Collection")
        with patch.dict(sys.modules, {"angelo_agent.action_object_management": module}), self.bind():
            with self.assertRaisesRegex(ProgramError, "NAME_TAKEN"):
                self.backend._apply_collection_intents(stage, [])
        self.assertEqual([row[0] for row in self.bridge.calls], ["seal", "new_collection", "created"])


class ApprovedDeletionTests(unittest.TestCase):
    """The production scope approves exactly the deletions the approved program's own operations make.

    object_delete's target, object_join's sources and object_convert's source
    without keep_original (action_catalog.deleted_references), each a declared
    object. Before, only object_delete targets were approved, so a join or an
    in-place conversion failed OWNERSHIP through the native Undo bridge
    (agent_workspace_ui -> program_undo -> program_commit_scope), the path the
    QA commits of the native suites do not take.
    """

    def bind(self, program, originals):
        controller, call, owner = preparation(fixtures.Backend(), originals, program)
        bridge = Bridge()
        return scopes.bind(controller, call, owner, bridge, object()), owner, bridge

    def test_convert_and_join_deletions_are_approved_and_sealed(self):
        text, part, other, target = (ID(index, "Object") for index in (1, 2, 3, 4))
        program = {"operations": [
            {"kind": "object_convert", "target": None, "parameters": {"source": "text", "keep_original": False,
                                                                       "name": "Text", "alias": "mesh"}},
            {"kind": "object_join", "target": "target", "parameters": {"sources": ["part", "other"]}},
            {"kind": "object_convert", "target": None, "parameters": {"source": "target", "keep_original": True,
                                                                       "name": None, "alias": "copy"}}]}
        originals = {"text": text, "part": part, "other": other, "target": target}
        scope, owner, bridge = self.bind(program, originals)
        with scope as bound:
            bound.validate_stage(SimpleNamespace(binding=owner.binding, originals=originals,
                                                 extras={"deleted": ["text", "part", "other"]}))
        self.assertEqual(bridge.calls[0][2], (text, part, other))

    def test_a_deletion_the_program_does_not_make_is_still_refused(self):
        text, extra = ID(1, "Object"), ID(2, "Object")
        program = {"operations": [{"kind": "object_convert", "target": None, "parameters": {
            "source": "text", "keep_original": False, "name": None, "alias": "mesh"}}]}
        scope, owner, _ = self.bind(program, {"text": text, "extra": extra})
        with scope as bound:
            stage = SimpleNamespace(binding=owner.binding, originals={"text": text, "extra": extra},
                                    extras={"deleted": ["text", "extra"]})
            with self.assertRaisesRegex(ProgramError, "original approved") as raised:
                bound.validate_stage(stage)
        self.assertEqual(raised.exception.code, "OWNERSHIP")

    def test_an_undeclared_deletion_is_never_sealed(self):
        program = {"operations": [{"kind": "object_join", "target": "target", "parameters": {"sources": ["ghost"]}}]}
        scope, _, bridge = self.bind(program, {"target": ID(1, "Object")})
        with self.assertRaisesRegex(ProgramError, "no exact original"):
            with scope:
                self.fail("an unapproved deletion was sealed")
        self.assertEqual(bridge.calls, [])

    def test_the_controller_and_the_derived_checks_share_one_definition(self):
        from angelo_agent.action_catalog import deleted_references
        from angelo_agent.derived_checks import _removed
        operation = lambda kind, target=None, **parameters: {"kind": kind, "target": target, "parameters": parameters}
        operations = [operation("object_delete", "a"), operation("object_join", "t", sources=["b", "c"]),
                      operation("object_convert", source="d", keep_original=False),
                      operation("object_convert", source="e", keep_original=True), operation("object_rename", "f", name="x")]
        self.assertEqual([list(deleted_references(item)) for item in operations], [_removed(item) for item in operations])
        self.assertEqual([reference for item in operations for reference in deleted_references(item)], ["a", "b", "c", "d"])
        controller = Path(scopes.__file__).with_name("program_commit_controller.py").read_text()
        self.assertIn("from .program_commit_scope import approved_deletions as deletions", controller)
        self.assertIn("approved_deletions = deletions(approved_program, originals)", controller)


class TransferObserverTests(unittest.TestCase):
    def test_material_restore_records_selected_id_and_embedded_tree_per_slot(self):
        from angelo_agent.program_objects import MATERIAL_SOURCE
        clone, live = ID(10, "Material"), ID(20, "Material")
        clone.node_tree, live.node_tree = ID(11, "NodeTree"), ID(21, "NodeTree")
        clone[MATERIAL_SOURCE] = live.session_uid
        obj = ID(30, "Object")
        obj.material_slots = [SimpleNamespace(material=clone), SimpleNamespace(material=clone)]
        obj.modifiers = []
        stage = SimpleNamespace(owned_ids=[clone, clone.node_tree], context=SimpleNamespace(created_ids=[]))
        bpy = SimpleNamespace(data=SimpleNamespace(materials=[live], node_groups=[]))
        rows = []
        with patch.object(program_identities, "_bpy", return_value=bpy):
            program_identities._restore_source_identities([obj], stage,
                record_transfer=lambda before, after, reason, occurrence: rows.append((scopes.identity(before), scopes.identity(after), occurrence)))
        self.assertTrue(all(slot.material is live for slot in obj.material_slots))
        self.assertEqual([row[:2] for row in rows], [((10, 100, "MATERIAL"), (20, 200, "MATERIAL")),
                                                   ((11, 110, "NODETREE"), (21, 210, "NODETREE"))] * 2)
        self.assertNotEqual(rows[0][2], rows[2][2])

    def test_nested_group_restore_records_choice_before_marker_is_lost(self):
        clone, live, tree, node = ID(10, "NodeTree"), ID(20, "NodeTree"), ID(30, "NodeTree"), ID(40, "Node")
        clone[node_edit_commit.SOURCE] = live.session_uid
        node.node_tree, tree.nodes = clone, [node]
        bpy = SimpleNamespace(data=SimpleNamespace(node_groups=[live], images=[]))
        modules = {"bpy": bpy, "angelo_agent.action_node_graphs": SimpleNamespace(IMAGE_MARKER="image_marker"),
                   "angelo_agent.action_world": SimpleNamespace(resolve_image=lambda value: None)}
        rows = []
        with patch.dict(sys.modules, modules):
            node_edit_commit.finish(tree, object(), {10, 30},
                record_transfer=lambda before, after, reason, occurrence: rows.append((scopes.identity(before), scopes.identity(after), reason)))
        self.assertIs(node.node_tree, live)
        self.assertEqual(rows, [((10, 100, "NODETREE"), (20, 200, "NODETREE"), "nested_source_restored")])

    def test_isolated_world_records_actual_assigned_world_and_tree_before_unmark(self):
        world, live, scene = ID(10, "World"), ID(20, "World"), ID(30, "Scene")
        world.node_tree, live.node_tree = ID(11, "NodeTree"), ID(21, "NodeTree")
        world[node_edit_commit.EDITED], world[node_edit_commit.MODE] = live.session_uid, "ASSIGN"
        live.users, scene.world = 2, live
        stage = SimpleNamespace(scene=SimpleNamespace(world=world), owned_ids=[world, world.node_tree],
                                context=SimpleNamespace(created_ids=[]))
        rows = []
        def record(before, after, reason, occurrence):
            self.assertIn(node_edit_commit.EDITED, world.keys())
            rows.append((scopes.identity(before), scopes.identity(after), reason))
        with patch.dict(sys.modules, {"bpy": SimpleNamespace()}), patch.object(node_edit_commit, "finish"):
            result = node_edit_commit.transfer_world(scene, stage, [], [], [], record_transfer=record)
        self.assertIs(result, world)
        self.assertIs(scene.world, world)
        self.assertEqual(rows, [((10, 100, "WORLD"), (10, 100, "WORLD"), "node_tree_assigned"),
                                ((11, 110, "NODETREE"), (11, 110, "NODETREE"), "node_tree_assigned")])
        self.assertNotIn(node_edit_commit.EDITED, world.keys())


class ScopedStorageTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.StorageTests(methodName="test_clean_teardown_maps_main_once_and_verifies_once")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.bpy.data.as_pointer = Mock(return_value=100)
        self.backend = self.fixture.backend
        self.bridge = Bridge(self.fixture.current)
        self.native = object()
        self.controller, self.call, self.preparation = preparation(self.backend)

    def bind(self):
        return scopes.bind(self.controller, self.call, self.preparation, self.bridge, self.native)

    def test_failed_registration_handoff_preserves_known_and_unknown_without_public_retry(self):
        self.bridge.stage_residue = Mock(return_value={"remaining": [(1, 10, "OBJECT")],
            "unknown": [(9, 90, "KEY")], "error": "native registration proof incomplete"})
        with self.bind():
            pass
        self.bridge.stage_residue.assert_called_once_with(self.native)
        cleanup = self.controller.record_native_residue.call_args.args[2]
        self.assertEqual(cleanup["temporary_ids_remaining"], 1)
        self.assertTrue(cleanup["cleanup_pending"])
        self.assertIn("unresolved", cleanup["cleanup_error"])
        self.fixture.batch_failure = False
        self.assertTrue(storage.cleanup_pending())
        self.assertEqual(self.fixture.batch_calls, [])
        self.fixture.current.pop(1)
        self.assertTrue(storage.cleanup_pending())
        records = list(storage._pending_cleanup.values())
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].unknown, ((9, 90, "KEY"),))
        self.assertEqual(records[0].document, self.preparation.document)
        self.assertEqual(records[0].main_pointer, 100)
        with self.assertRaisesRegex(ProgramError, "membership is unresolved") as failure:
            storage.require_cleanup_complete()
        self.assertEqual(failure.exception.temporary_ids_remaining, 1)  # Last-native-observed; the Python scan is incomplete.

    def test_healthy_receipt_does_not_queue_transferred_kept_ids(self):
        with self.bind() as scope:
            scope.record_transfer(self.fixture.first, self.fixture.original, "actual_source_restore")
        self.assertFalse(storage.has_pending_cleanup())
        self.controller.record_native_residue.assert_not_called()
        self.assertTrue(self.fixture.original.alive)

    def test_native_known_key_and_failed_deletion_closure_survive_partial_python_inventory(self):
        key = ID(9, "Key")
        self.bridge.stage_residue = Mock(return_value={"remaining": [(9, 90, "KEY")], "unknown": [],
                                                        "error": "native Key registration failed"})
        with self.bind():
            pass
        storage.retain_cleanup({9: (90, "KEY")})  # Cannot downgrade the complete native observation.
        self.assertTrue(storage.cleanup_pending())
        record = next(iter(storage._pending_cleanup.values()))
        self.assertEqual(record, {9: (90, "KEY")})
        self.assertTrue(record.unresolved)
        self.assertEqual(record.unknown, ())
        self.bridge.stage_residue = Mock(return_value={"remaining": [], "unknown": [], "error": ""})
        self.bridge.delete_stage = Mock(return_value={"success": False, "kernel_called": True,
            "remaining": [(9, 90, "KEY")], "removed": [], "error": "native closure Key survived"})
        with self.bind():
            with self.assertRaises(ProgramError) as failure:
                self.backend._remove_ids([key])
            self.assertEqual(failure.exception.temporary_ids_remaining, 1)
            self.assertTrue(failure.exception.cleanup_pending)
        self.assertTrue(storage.cleanup_pending())
        self.assertEqual(len(storage._pending_cleanup), 1)
        self.assertEqual(self.fixture.batch_calls, [])
        self.assertTrue(key.alive)

    def test_unknown_registration_identity_blocks_later_public_cleanup_and_survives_pruning(self):
        self.bridge.stage_residue = Mock(return_value={"remaining": [], "unknown": [(1, 10, "OBJECT")],
                                                        "error": "registration lookup unresolved"})
        with self.bind():
            pass
        storage.retain_cleanup({1: (10, "OBJECT")})
        self.fixture.batch_failure = False
        self.assertTrue(storage.cleanup_pending())
        with self.assertRaisesRegex(ProgramError, "public retry was not run"):
            self.backend._remove_ids([self.fixture.first])
        self.assertEqual(self.fixture.batch_calls, [])
        self.fixture.current.clear()
        self.assertTrue(storage.cleanup_pending())
        self.assertTrue(any(record.unresolved for record in storage._pending_cleanup.values()))

    def test_later_exact_native_removal_retires_failed_delete_rows_in_same_scope(self):
        key, row = ID(9, "Key"), (9, 90, "KEY")
        self.bridge.delete_stage = Mock(side_effect=[
            {"success": False, "kernel_called": True, "scope_valid_after": True,
             "remaining": [row], "removed": [], "error": "closure Key survived"},
            {"success": True, "kernel_called": True, "scope_valid_after": True,
             "remaining": [], "removed": [row]}])
        with self.bind() as scope:
            with self.assertRaises(ProgramError):
                scope.delete_stage((key,))
            self.assertTrue(storage.has_pending_cleanup())
            scope.delete_stage((key,))
            self.assertFalse(storage.has_pending_cleanup())
        self.assertFalse(storage.cleanup_pending())
        self.assertEqual(self.fixture.batch_calls, [])

    def test_removal_retirement_requires_exact_document_main_identity_and_native_proof(self):
        key, row = ID(9, "Key"), (9, 90, "KEY")
        storage.retain_native_residue([row], (), "failed deletion", document=self.preparation.document,
                                      main_pointer=100)
        for document, main, removed in [(("other", 1), 100, [row]), (self.preparation.document, 200, [row]),
                                        (self.preparation.document, 100, [(9, 99, "KEY")])]:
            storage.retire_native_receipt(document, main, removed)
            self.assertTrue(storage.has_pending_cleanup())
        with self.bind() as scope:
            for kernel, valid in [(False, True), (True, False)]:
                self.bridge.delete_stage = Mock(return_value={"success": True, "kernel_called": kernel,
                    "scope_valid_after": valid, "remaining": [], "removed": [row]})
                scope.delete_stage((key,))
                self.assertTrue(storage.has_pending_cleanup())
        self.assertTrue(storage.has_pending_cleanup())

    def test_exact_removal_proof_retires_unknown_row_but_not_opaque_completeness_marker(self):
        row = (9, 90, "KEY")
        for uncertain in (False, True):
            storage._pending_cleanup.clear()
            storage.retain_native_residue([], [row], "native inventory incomplete", document=self.preparation.document,
                                          main_pointer=100, uncertain=uncertain)
            storage.retire_native_receipt(self.preparation.document, 100, [row])
            self.assertEqual(storage.has_pending_cleanup(), uncertain)
            if uncertain:
                record = next(iter(storage._pending_cleanup.values()))
                self.assertEqual(record, {})
                self.assertEqual(record.unknown, ())
                self.assertTrue(record.completeness_uncertain)

    def test_hostile_cleanup_exception_cannot_skip_stage_or_created_collection_retention(self):
        hooks = []
        class Failure(RuntimeError):
            def __bool__(self):
                hooks.append("bool")
                raise AssertionError("exception truth hook must not run")
            def __str__(self):
                hooks.append("str")
                raise AssertionError("exception string hook must not run")
        with self.bind():
            with self.assertRaisesRegex(ProgramError, "native cleanup failed"):
                storage._remove_owned({1: (10, "OBJECT")}, Mock(side_effect=Failure("native cleanup failed")))
        self.assertTrue(storage.protected_cleanup({1: (10, "OBJECT")}))
        collection = ID(90, "Collection")
        self.fixture.current[90] = collection
        self.bridge.new_collection = Mock(return_value=collection)
        with self.bind() as scope:
            made = scope.new_collection("Retained")
            with patch.object(scope, "_delete", side_effect=Failure("collection cleanup failed")):
                with self.assertRaises(Failure):
                    scope.delete_created_collections((made,))
        self.assertTrue(storage.protected_cleanup({90: (900, "COLLECTION")}))
        self.assertEqual(hooks, [])
        self.assertEqual(self.fixture.batch_calls, [])

    def test_malformed_deletion_receipt_never_runs_hooks_or_grants_artist_cleanup_authority(self):
        hooks = []
        class HostileList(list):
            def __bool__(self):
                hooks.append("bool")
                raise AssertionError("list truth hook must not run")
            def __iter__(self):
                hooks.append("iter")
                raise AssertionError("list iteration hook must not run")
        for receipt in (None,
                        {"success": False, "kernel_called": True, "remaining": HostileList(), "removed": []},
                        {"success": False, "kernel_called": True, "remaining": [(1, True, "OBJECT")], "removed": []},
                        {"success": True, "kernel_called": True, "scope_valid_after": None, "remaining": [], "removed": []}):
            storage._pending_cleanup.clear()
            self.bridge.delete_stage = Mock(return_value=receipt)
            self.bridge.delete_approved_objects = Mock(return_value=receipt)
            with self.bind() as scope:
                with self.assertRaises(ProgramError) as failure:
                    scope.delete_stage((self.fixture.first,))
                self.assertTrue(failure.exception.cleanup_pending)
                self.assertTrue(next(iter(storage._pending_cleanup.values())).completeness_uncertain)
                storage._pending_cleanup.clear()
                with self.assertRaises(ProgramError):
                    scope.delete_approved_objects((self.fixture.original,))
                self.assertFalse(storage.has_pending_cleanup())
        self.assertEqual(hooks, [])

    def test_deletion_receipt_budget_includes_embedded_closure_and_never_truncates(self):
        rows = [(index, index, "NODETREE") for index in range(1, scopes.MAX_DELETE_RECEIPT_IDS + 1)]
        self.assertGreater(len(rows), scopes.MAX_STAGE_IDS)
        self.bridge.delete_stage = Mock(return_value={"success": True, "kernel_called": True,
            "scope_valid_after": True, "remaining": [], "removed": rows})
        with self.bind() as scope:
            receipt = scope.delete_stage(())
            self.assertEqual(len(receipt["removed"]), 3 * scopes.MAX_STAGE_IDS)
            self.assertFalse(storage.has_pending_cleanup())
            rows.append((scopes.MAX_DELETE_RECEIPT_IDS + 1, 1, "NODETREE"))
            with self.assertRaises(ProgramError) as failure:
                scope.delete_stage(())
            self.assertTrue(failure.exception.cleanup_pending)
            self.assertTrue(next(iter(storage._pending_cleanup.values())).completeness_uncertain)
        self.assertEqual(len(rows), scopes.MAX_DELETE_RECEIPT_IDS + 1)

    def test_residue_handoff_runs_after_cleanup_failure_before_owner_clear(self):
        order = []
        def cleanup(*_args):
            order.append("cleanup")
            raise RuntimeError("prefinish failed")
        def residue(_scope):
            order.append("residue")
            self.assertIsNotNone(scopes.for_backend(self.backend))
            return {"remaining": [], "unknown": [], "error": "incomplete native inventory"}
        self.controller.prepare_native_finish.side_effect = cleanup
        self.bridge.stage_residue = residue
        with self.assertRaisesRegex(RuntimeError, "prefinish failed"), self.bind():
            pass
        self.assertEqual(order, ["cleanup", "residue"])
        self.assertIsNone(scopes.active())
        self.assertIsNone(self.backend._native_commit_scope)
        self.assertTrue(storage.has_pending_cleanup())

    def test_hostile_or_oversized_receipts_retain_bounded_transport_uncertainty(self):
        class Hostile:
            def __bool__(self):
                raise AssertionError("truthiness must not run")
            def __str__(self):
                raise AssertionError("string hook must not run")
            def __eq__(self, other):
                raise AssertionError("equality must not run")
        class HostileDict(dict):
            def get(self, *args):
                raise AssertionError("custom dict methods must not run")
        bad = [HostileDict(), Hostile(),
               {"remaining": [(True, 10, "OBJECT")], "unknown": [], "error": ""},
               {"remaining": [(1, 10, Hostile())], "unknown": [], "error": ""},
               {"remaining": [(1, 10, "OBJECT")], "unknown": [], "error": ""},
               {"remaining": [], "unknown": [], "error": Hostile()},
               {"remaining": [(1, 10, "OBJECT")] * (scopes.MAX_STAGE_IDS + 1), "unknown": [], "error": ""}]
        for receipt in bad:
            with self.subTest(receipt_type=type(receipt).__name__):
                storage._pending_cleanup.clear()
                self.bridge.stage_residue = Mock(return_value=receipt)
                with self.bind():
                    pass
                records = list(storage._pending_cleanup.values())
                self.assertEqual(len(records), 1)
                self.assertTrue(records[0].unresolved)
                self.assertEqual(records[0].unknown, ())
                self.assertIn("transport failed", records[0].reason)
                self.assertEqual(records[0].document, self.preparation.document)

    def test_successful_load_requires_distinct_document_and_main_before_retiring_uncertainty(self):
        self.bridge.stage_residue = Mock(return_value={"remaining": [], "unknown": [(9, 90, "KEY")],
                                                        "error": "registration failed"})
        with self.bind():
            pass
        # load_pre may change serial even if the load fails: cleanup does not retire it.
        self.assertTrue(storage.cleanup_pending())
        storage.retire_native_residue_after_load(("new document", 2))
        self.assertTrue(storage.has_pending_cleanup())  # Same native Main.
        self.fixture.bpy.data.as_pointer.return_value = 200
        storage.retire_native_residue_after_load(("document", 2))
        self.assertTrue(storage.has_pending_cleanup())  # Scene change is not document replacement.
        storage.retire_native_residue_after_load(("new document", 2))
        self.assertFalse(storage.has_pending_cleanup())
        self.assertEqual(self.fixture.batch_calls, [])

    def test_missing_main_identity_never_claims_retirement_and_duplicate_receipts_remain_bounded(self):
        self.fixture.bpy.data.as_pointer.side_effect = RuntimeError("no Main pointer")
        self.bridge.stage_residue = Mock(return_value={"remaining": [], "unknown": [], "error": "inventory incomplete"})
        for _ in range(2):
            with self.bind():
                pass
        self.assertEqual(len(storage._pending_cleanup), 1)
        self.fixture.bpy.data.as_pointer.side_effect = None
        self.fixture.bpy.data.as_pointer.return_value = 200
        storage.retire_native_residue_after_load(("replacement", 2))
        self.assertTrue(storage.has_pending_cleanup())

    def test_actual_load_post_retires_replaced_document_before_font_callback_can_fail(self):
        self.bridge.stage_residue = Mock(return_value={"remaining": [], "unknown": [(9, 90, "KEY")],
                                                        "error": "registration failed"})
        with self.bind():
            pass
        source = Path(storage.__file__).with_name("ui.py")
        callback = next(node for node in ast.parse(source.read_text()).body
                        if isinstance(node, ast.FunctionDef) and node.name == "document_load_post")
        namespace = {"__package__": "angelo_agent", "persistent": lambda function: function,
            "document_key": lambda: ("replacement", 2), "_restoring_snapshot": True,
            "chat_view": SimpleNamespace(reload_fonts=Mock(side_effect=RuntimeError("font reload failed")))}
        exec(compile(ast.Module(body=[callback], type_ignores=[]), str(source), "exec"), namespace)
        self.fixture.bpy.data.as_pointer.return_value = 200
        with self.assertRaisesRegex(RuntimeError, "font reload failed"):
            namespace["document_load_post"](None)
        self.assertFalse(storage.has_pending_cleanup())

    def test_throwing_receipt_uses_fixed_diagnostic_and_always_releases_binding(self):
        class Failure(RuntimeError):
            def __str__(self):
                raise AssertionError("exception formatting must not run")
        self.bridge.stage_residue = Mock(side_effect=Failure())
        with self.bind():
            pass
        self.assertTrue(storage.has_pending_cleanup())
        self.assertIsNone(scopes.active())
        self.assertIsNone(self.backend._native_commit_scope)
        self.assertIn("transport failed", self.controller.record_native_residue.call_args.args[2]["cleanup_error"])

    def test_stage_cleanup_uses_native_right_even_with_overridden_remover(self):
        with self.bind(), patch.object(self.backend, "_remove_ids", side_effect=AssertionError("public remover")):
            self.backend._remove_stage_ids(self.fixture.stage, keep=set())
        self.assertEqual(self.fixture.batch_calls, [])
        self.assertEqual(set(self.fixture.current), {3})

    def test_partial_native_deletion_preserves_residue_without_public_fallback(self):
        self.bridge.partial = True
        with self.bind():
            with self.assertRaises(ProgramError) as error:
                self.backend._remove_stage_ids(self.fixture.stage, keep=set())
            self.assertTrue(error.exception.cleanup_pending)
            self.assertTrue(storage.cleanup_pending())
        self.assertEqual(self.fixture.batch_calls, [])
        retained = {pointer: record for values in storage._pending_cleanup.values() for pointer, record in values.items()}
        self.assertEqual(retained, {2: (20, "SCENE")})
        self.assertTrue(self.fixture.original.alive)
        self.fixture.batch_failure = False
        self.assertTrue(storage.cleanup_pending())
        self.assertEqual(self.fixture.batch_calls, [])
        with self.assertRaisesRegex(ProgramError, "fresh native") as failure:
            storage.require_cleanup_complete()
        self.assertEqual(failure.exception.temporary_ids_remaining, 1)
        with self.assertRaisesRegex(ProgramError, "public retry was not run"):
            self.backend._remove_ids([self.fixture.second])
        self.assertTrue(self.fixture.second.alive)

    def test_protected_residue_dedup_upgrades_only_overlap_and_never_downgrades(self):
        storage.retain_cleanup({1: (10, "OBJECT"), 2: (20, "SCENE")})
        with self.bind():
            storage.retain_cleanup({1: (10, "OBJECT")}, reason="artist now references this stage Object")
        storage.retain_cleanup({1: (10, "OBJECT")})
        self.fixture.batch_failure = False
        self.assertTrue(storage.cleanup_pending())
        self.assertEqual(self.fixture.batch_calls, [[2]])
        records = list(storage._pending_cleanup.values())
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0], {1: (10, "OBJECT")})
        self.assertTrue(records[0].native_proof_required)
        self.assertIn("artist now references", records[0].reason)

    def test_reentrant_promotion_is_not_recreated_as_public_cleanup(self):
        f = self.fixture
        storage.retain_cleanup({1: (10, "OBJECT"), 2: (20, "SCENE")})
        def partial_remove(*, ids):
            f.remove(f.first)
            with self.bind():
                storage.retain_cleanup({2: (20, "SCENE")}, reason="native incoming-reference refusal")
            raise RuntimeError("earlier public cleanup partially failed")
        f.bpy.data.batch_remove = Mock(side_effect=partial_remove)
        self.assertTrue(storage.cleanup_pending())
        self.assertTrue(storage.cleanup_pending())
        f.bpy.data.batch_remove.assert_called_once()
        records = list(storage._pending_cleanup.values())
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0], {2: (20, "SCENE")})
        self.assertTrue(records[0].native_proof_required)

    def test_absent_or_replaced_protected_identity_is_pruned_without_removal(self):
        with self.bind():
            storage.retain_cleanup({1: (10, "OBJECT"), 2: (20, "SCENE")})
        self.fixture.current.pop(1)
        self.fixture.second._uid = 99
        self.assertFalse(storage.cleanup_pending())
        self.assertFalse(storage.has_pending_cleanup())
        self.assertTrue(self.fixture.second.alive)
        self.assertEqual(self.fixture.batch_calls, [])

    def test_failed_created_collection_cleanup_is_protected_but_artist_object_is_not(self):
        collection = ID(90, "Collection")
        self.fixture.current[90] = collection
        self.bridge.new_collection = Mock(return_value=collection)
        self.bridge.delete_created_collections = Mock(return_value={"success": False, "kernel_called": False,
            "remaining": [scopes.identity(collection)], "removed": [], "error": "new artist link prevents private deletion"})
        original = self.fixture.original
        self.bridge.delete_approved_objects = Mock(return_value={"success": False, "kernel_called": False,
            "remaining": [scopes.identity(original)], "removed": [], "error": "artist Object is still referenced"})
        with self.bind() as scope:
            made = scope.new_collection("Collision")
            with self.assertRaisesRegex(ProgramError, "new artist link"):
                scope.delete_created_collections((made,))
            with self.assertRaisesRegex(ProgramError, "still referenced"):
                scope.delete_approved_objects((original,))
        self.assertTrue(storage.cleanup_pending())
        records = list(storage._pending_cleanup.values())
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0], {90: (900, "COLLECTION")})
        self.assertTrue(records[0].native_proof_required)
        self.assertTrue(original.alive and collection.alive)
        self.assertEqual(self.fixture.batch_calls, [])

    def test_name_conflict_cleanup_failure_retains_the_native_created_collection(self):
        collection = ID(90, "Collection")
        collection.name = "Wanted.001"
        self.fixture.current[90] = collection
        self.backend = ProgramCommitMixin()
        self.backend._collections_named = lambda names: []
        self.controller, self.call, self.preparation = preparation(self.backend)
        self.bridge.new_collection = Mock(return_value=collection)
        self.bridge.delete_created_collections = Mock(return_value={"success": False, "kernel_called": False,
            "remaining": [scopes.identity(collection)], "removed": [], "error": "native collection cleanup refused"})
        stage = SimpleNamespace(context=SimpleNamespace(aliases={}), originals={"handle": ID(1, "Object")},
                                extras={"collections": {"handle": [{"collection": "Wanted", "create": True}]}})
        module = SimpleNamespace(MASTER_COLLECTION="Scene Collection")
        with patch.dict(sys.modules, {"angelo_agent.action_object_management": module}), self.bind():
            with self.assertRaisesRegex(ProgramError, "native collection cleanup refused"):
                self.backend._apply_collection_intents(stage, [])
        self.assertTrue(storage.protected_cleanup({90: (900, "COLLECTION")}))
        self.assertTrue(storage.cleanup_pending())
        self.assertTrue(collection.alive)
        self.assertEqual(self.fixture.batch_calls, [])

    def test_detached_attach_receives_exact_native_capture(self):
        native_owner = object()
        descriptor = SuspendedStage("", "Stage", {}, {}, {}, {}, {}, native_owner=native_owner, resident=self.fixture.stage)
        attach = Mock()
        self.backend.validate_stage = lambda stage: None
        module = SimpleNamespace(_angelo_stage=SimpleNamespace(attach=attach))
        with patch.dict(sys.modules, {"_bpy": module}), self.bind():
            self.assertIs(self.backend.resume_stage(descriptor), self.fixture.stage)
        attach.assert_called_once_with(native_owner, self.native)

    def test_attached_ids_are_guarded_when_attach_raises_after_move(self):
        descriptor = SuspendedStage("", "Stage", {}, {}, {}, {}, {}, native_owner=object(), resident=self.fixture.stage)
        attach = Mock(side_effect=RuntimeError("attached but registration failed"))
        module = SimpleNamespace(_angelo_stage=SimpleNamespace(attach=attach))
        with patch.dict(sys.modules, {"_bpy": module}), self.bind():
            with self.assertRaisesRegex(RuntimeError, "registration failed"):
                self.backend.resume_stage(descriptor)
        self.assertEqual(set(self.fixture.current), {3})
        self.assertEqual(self.fixture.batch_calls, [])

    def test_file_hydration_receives_capture_and_failure_uses_native_cleanup(self):
        f = self.fixture
        f.current = {3: f.original}
        self.bridge.current = f.current
        artist, key = ID(4, "Object"), ID(9, "Key")
        f.bpy.data.shape_keys = [key]
        self.bridge.stage_owned = Mock(return_value=[(1, 10, "OBJECT"), (2, 20, "SCENE"), (9, 90, "KEY")])
        seen = []
        @contextlib.contextmanager
        def load(path, **kwargs):
            seen.append(kwargs)
            destination = SimpleNamespace(scenes=[])
            yield SimpleNamespace(scenes=["Stage"]), destination
            f.current.update({1: f.first, 2: f.second, 4: artist})  # BLENDIMPORT callback creates a separate artist ID.
            destination.scenes = [f.second]
        f.bpy.data.libraries = SimpleNamespace(load=load)
        with tempfile.TemporaryDirectory(prefix="angelo-native-preview-") as folder:
            bundle = Path(folder) / "fixture.blend"
            bundle.write_text("private test fixture")
            descriptor = SuspendedStage(str(bundle), "Stage", {}, {}, {}, {}, {})
            with self.bind(), self.assertRaisesRegex(ProgramError, "not an admitted"):
                self.backend.resume_stage(descriptor)
        self.assertEqual(seen, [{"link": False, "_angelo_capture": self.native}])
        self.assertEqual(f.batch_calls, [])
        self.assertEqual(set(f.current), {3, 4})
        self.assertTrue(artist.alive)
        self.assertFalse(key.alive)
        self.assertFalse(storage.protected_cleanup({4: (40, "OBJECT")}))

    def test_file_hydration_owned_ids_exclude_callback_artist_ids_and_include_native_keys(self):
        f = self.fixture
        f.current = {3: f.original}
        self.bridge.current = f.current
        artist, collection, key = ID(4, "Object"), ID(5, "Collection"), ID(9, "Key")
        f.bpy.data.shape_keys = [key]
        f.bpy.types.NodeTree = type("FakeNodeTree", (), {})
        f.second.get = lambda *_args: True
        f.second.collection = SimpleNamespace(children=[collection])
        f.second.objects = fixtures.MainCollection([f.first])
        f.second.view_layers = [SimpleNamespace()]
        self.bridge.stage_owned = Mock(return_value=[(1, 10, "OBJECT"), (2, 20, "SCENE"),
                                                     (5, 50, "COLLECTION"), (9, 90, "KEY")])
        @contextlib.contextmanager
        def load(path, **kwargs):
            destination = SimpleNamespace(scenes=[])
            yield SimpleNamespace(scenes=["Stage"]), destination
            f.current.update({1: f.first, 2: f.second, 4: artist, 5: collection})
            destination.scenes = [f.second]
        class Context:
            def __init__(self, *args, **kwargs):
                self.topology_versions, self.node_bounds = {}, {}
        f.bpy.data.libraries = SimpleNamespace(load=load)
        self.backend.validate_stage = lambda stage: None
        module = SimpleNamespace(StagedActionContext=Context, restore_node_metadata=lambda *_args: None)
        with tempfile.TemporaryDirectory(prefix="angelo-native-preview-") as folder:
            bundle = Path(folder) / "fixture.blend"
            bundle.write_text("private test fixture")
            descriptor = SuspendedStage(str(bundle), "Stage", {"originals": {}}, {}, {}, {}, {})
            with patch.dict(sys.modules, {"angelo_agent.action_kernels": module}), self.bind():
                hydrated = self.backend.resume_stage(descriptor)
        self.assertEqual({block.as_pointer() for block in hydrated.owned_ids}, {1, 2, 5, 9})
        self.assertNotIn(artist, hydrated.owned_ids)
        self.assertTrue(artist.alive)
        self.assertFalse(storage.has_pending_cleanup())

    def test_missing_native_stage_identity_retains_uncertainty_without_artist_cleanup(self):
        self.bridge.stage_owned = Mock(return_value=[(9, 90, "KEY")])
        with self.bind() as scope:
            with self.assertRaisesRegex(ProgramError, "could not be resolved"):
                scope.stage_owned_ids()
        record = next(iter(storage._pending_cleanup.values()))
        self.assertEqual(record, {9: (90, "KEY")})
        self.assertTrue(record.completeness_uncertain)
        self.assertEqual(self.fixture.batch_calls, [])


class ControllerCleanupTests(AgentRuntimeFixture, unittest.TestCase):
    def test_private_preparation_error_formatting_never_invokes_exception_string_hook(self):
        string_calls = []
        class CallbackError(Exception):
            def __str__(self):
                string_calls.append(True)
                raise RuntimeError("exception formatting callback must not run")
        for phase in ("enter", "leave", "cleanup"):
            with self.subTest(phase=phase):
                c = self.new_controller()
                answers, call = self.enqueue(c)
                job = self.ready(c)
                failure = CallbackError("native preparation callback failed")
                if phase == "enter":
                    self.executor.on_precondition = Mock(side_effect=failure)
                preparation = c.enter_native_preparation(call, self.executor, c.document)
                self.executor.on_precondition = None
                if phase == "cleanup":
                    cancel = job.cancel
                    def fail_after_cleanup():
                        cancel()
                        raise failure
                    job.cancel = fail_after_cleanup
                    with scopes.bind(c, call, preparation, Bridge(), object()):
                        c.stop()
                    job.cancel = cancel
                c.leave_native_preparation(call, preparation, error=failure if phase == "leave" else None)
                result = c.finish_native_commit(call, {"undo_published": False})
                self.assertFalse(string_calls)
                self.assertIsNone(call.native_preparation)
                self.assertIsNone(self.writer.owner)
                self.assertEqual(len(answers), 1)
                self.assertTrue(result["partial_changes_possible"])
                c._native_cleanup.clear()  # The injected exception happened after its actual cleanup.

    def test_binder_cleans_preapply_stop_before_scope_clear_and_keeps_writer(self):
        answers, call = self.enqueue()
        job = self.ready()
        preparation = self.c.enter_native_preparation(call, self.executor, self.c.document)
        bridge = Bridge()
        seen = []
        discard = job.backend.discard
        def scoped_discard(stage):
            seen.append(scopes.for_backend(job.backend))
            self.c.prepare_native_finish(call, preparation)  # Reentry consumes nothing twice.
            self.assertFalse(answers)
            self.assertIs(self.writer.owner, job)
            discard(stage)
        job.backend.discard = scoped_discard
        with scopes.bind(self.c, call, preparation, bridge, object()):
            self.c.stop()
        self.assertEqual(len(seen), 1)
        self.assertEqual(job.backend.discards, 1)
        self.assertIs(self.writer.owner, job)
        self.assertFalse(answers)
        self.c.leave_native_preparation(call, preparation)
        result = self.c.finish_native_commit(call, {"undo_published": False})
        self.assertEqual(result["error_code"], "CANCELLED")
        self.assertEqual(job.backend.discards, 1)
        self.assertEqual(len(answers), 1)
        self.assertIsNone(self.writer.owner)
        self.assertNotIn(call.id, self.c.native_jobs)

    def test_cleanup_warning_preserves_original_refusal_and_uncertainty(self):
        answers, call = self.enqueue()
        job = self.ready()
        preparation = self.c.enter_native_preparation(call, self.executor, self.c.document)
        job.backend.discard = Mock(side_effect=RuntimeError("native cleanup refused"))
        with scopes.bind(self.c, call, preparation, Bridge(), object()):
            self.c.stop()
        self.c.leave_native_preparation(call, preparation)
        result = self.c.finish_native_commit(call, {"undo_published": False})
        self.assertEqual(result["error_code"], "CANCELLED")
        self.assertIn("native cleanup refused", result["cleanup_error"])
        self.assertTrue(result["partial_changes_possible"])
        self.assertNotIn("scene_changed", result)
        job.backend.discard.assert_called_once()

    def test_registration_uncertainty_preserves_prior_cleanup_count_and_original_refusal(self):
        self.addCleanup(storage._pending_cleanup.clear)
        answers, call = self.enqueue()
        job = self.ready()
        preparation = self.c.enter_native_preparation(call, self.executor, self.c.document)
        bridge = Bridge()
        bridge.stage_residue = Mock(return_value={"remaining": [], "unknown": [], "error": "registration incomplete"})
        with scopes.bind(self.c, call, preparation, bridge, object()):
            self.c.stop()
            self.c.record_native_residue(call, preparation, {"cleanup_error": "two exact known survivors",
                "cleanup_pending": True, "temporary_ids_remaining": 2})
        self.assertIs(self.writer.owner, job)
        self.assertFalse(answers)
        self.c.leave_native_preparation(call, preparation)
        result = self.c.finish_native_commit(call, {"undo_published": False})
        self.assertEqual(result["error_code"], "CANCELLED")
        self.assertEqual(result["temporary_ids_remaining"], 2)
        self.assertIn("two exact known survivors", result["cleanup_error"])
        self.assertIn("registration incomplete", result["cleanup_error"])
        self.assertTrue(result["cleanup_pending"])
        self.assertNotIn("scene_changed", result)
        self.assertEqual(len(answers), 1)
        self.assertIsNone(self.writer.owner)

    def test_prefinish_cleanup_failure_cannot_continue_a_private_restage(self):
        answers, call = self.enqueue()
        job = self.ready()
        preparation = self.c.enter_native_preparation(call, self.executor, self.c.document)
        preparation.restaging = True
        preparation.result = {"restaging": True, "final": False, "partial_changes_possible": True}
        job.backend.discard = Mock(side_effect=RuntimeError("native cleanup refused"))
        with scopes.bind(self.c, call, preparation, Bridge(), object()):
            pass
        self.assertFalse(preparation.restaging)
        self.c.leave_native_preparation(call, preparation)
        result = self.c.finish_native_commit(call, {"undo_published": False})
        self.assertEqual(result["error_code"], "CLEANUP_FAILED")
        self.assertNotIn("restaging", result)
        self.assertEqual(call.staging_restarts, 0)
        self.assertEqual(len(answers), 1)

    def test_stop_after_hydration_keeps_closed_job_cleanup_failure_visible(self):
        answers, call = self.enqueue()
        job = self.ready()
        preparation = self.c.enter_native_preparation(call, self.executor, self.c.document)
        def hydrate(stage):
            self.c.stop()
            return stage
        def discard(stage):
            self.assertIsNotNone(scopes.for_backend(job.backend))
            raise RuntimeError("hydrated stage native cleanup refused")
        job.backend.resume_stage, job.backend.discard = hydrate, Mock(side_effect=discard)
        with scopes.bind(self.c, call, preparation, Bridge(), object()):
            self.c.commit_native_job(call, self.executor, self.c.document, defer_finish=True)
            self.assertFalse(answers)
            self.assertFalse(call.mutation_started)
            self.assertTrue(job.closed)
        self.c.leave_native_preparation(call, preparation)
        result = self.c.finish_native_commit(call, {"undo_published": False})
        self.assertEqual(result["error_code"], "CANCELLED")
        self.assertIn("hydrated stage native cleanup refused", result["cleanup_error"])
        job.backend.discard.assert_called_once()
        self.assertEqual(len(answers), 1)


if __name__ == "__main__":
    unittest.main()
