# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual application fixtures for the private capture/cold reader join.

Run only on root's compiled development application with factory/isolated state.
No provider, paid service, real user profile or synthetic native companion.
These tests are prepared source; a host import cannot execute or prove them.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
import unittest
import uuid

import bpy
import _bpy

assert "--factory-startup" in sys.argv, "Requires isolated factory startup"
assert not bpy.app.background, "Requires GUI Sculpt/initial Undo lifecycle"
assert "--disable-autoexec" in sys.argv, "Requires disabled file scripts"
OUT = Path(os.environ["ANGELO_QA_DIR"]).resolve(strict=True)
assert OUT.is_dir(), "Requires an existing private QA directory"
RETAINED_CAPSULE_EVENTS = []
RECEIPT = OUT / "native-checkpoint-cold-recovery.json"
assert not RECEIPT.exists(), "Refuses a stale receipt"

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.checkpoint_capture import CaptureOwner, capture_checkpoint
from angelo_agent.checkpoint_recovery import CheckpointRecovery
from angelo_agent.checkpoints import CheckpointStore


class NativeCheckpointColdRecovery(unittest.TestCase):
    def view_context(self):
        # Factory resets replace UI owners; native Restore clears the current context.
        # Resolve the actual surviving window and its viewport for each operation.
        self.assertEqual(len(bpy.data.window_managers), 1)
        manager = bpy.data.window_managers[0]
        self.assertEqual(len(manager.windows), 1)
        window = manager.windows[0]
        area = next(area for area in window.screen.areas if area.type == "VIEW_3D")
        region = next(region for region in area.regions if region.type == "WINDOW")
        return bpy.context.temp_override(window=window, area=area, region=region)

    def fixture_object(self):
        with self.view_context():
            obj = bpy.data.objects["CheckpointFixture"]
            self.assertEqual(bpy.context.scene.objects.get(obj.name), obj)
            self.assertEqual(bpy.context.view_layer.objects.active, obj)
            return obj

    def surface(self):
        with self.view_context():
            return _bpy._angelo_undo._test_sculpt_surface()

    def capture(self, **kwargs):
        with self.view_context():
            return capture_checkpoint(self.store, bpy.data.filepath, **kwargs)

    def restore(self, prepared, safety):
        with self.view_context():
            return _bpy._angelo_checkpoint.restore(prepared, safety)

    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-native-cold-fixture-", dir=OUT)
        self.store = CheckpointStore(Path(self.temp.name).resolve() / "profile", str(uuid.uuid4()))
        self.resources = []
        self.prepared = []
        self.handlers = []
        self.safeties = []
        with self.view_context():
            self.assertEqual(bpy.ops.mesh.primitive_cube_add(), {"FINISHED"})
            obj = bpy.context.view_layer.objects.active
            self.assertIsNotNone(obj)
            self.assertEqual(bpy.context.scene.objects.get(obj.name), obj)
            obj.name = "CheckpointFixture"

    def tearDown(self):
        try:
            for handler in self.handlers:
                if handler in bpy.app.handlers.load_post:
                    bpy.app.handlers.load_post.remove(handler)
            for prepared in reversed(self.prepared):
                _bpy._angelo_checkpoint.close_restore(prepared)
            for safety in reversed(self.safeties):
                _bpy._angelo_checkpoint.capture_release(safety)
            for resource in reversed(self.resources):
                resource.close()
        finally:
            self.temp.cleanup()

    def dyntopo(self):
        with self.view_context():
            self.assertEqual(bpy.ops.object.mode_set(mode="SCULPT"), {"FINISHED"})
            self.assertEqual(bpy.ops.sculpt.dynamic_topology_toggle(), {"FINISHED"})
            self.assertTrue(self.fixture_object().use_dynamic_topology_sculpting)
            return self.surface()

    def prepare(self, record):
        resource = CheckpointRecovery.prepare(self.store, record["id"])
        self.resources.append(resource)
        prepared = _bpy._angelo_checkpoint.prepare_restore(resource)
        self.prepared.append(prepared)
        return resource, prepared

    def safety(self):
        record, capsule = self.capture(purpose="safety", retain_native=True)
        self.assertEqual(record["state"], "READY")
        self.assertTrue(record["protected"])
        self.safeties.append(capsule)
        return capsule

    def test_ordinary_capture_has_no_companion_and_uses_v3(self):
        record = self.capture()
        self.assertEqual(record["version"], 3)
        self.assertEqual(record["state"], "READY")
        self.assertNotIn("capture_outputs", record)
        self.assertFalse((self.store.folder(record["id"]) / "sculpt.runtime").exists())
        # Exact same-capsule cleanup/replay during POST and FAIL must not retire its WM owner.
        for failed, handlers in ((False, bpy.app.handlers.save_post),
                                 (True, bpy.app.handlers.save_post_fail)):
            owner = CaptureOwner.reserve(self.store, bpy.data.filepath)
            capsule = _bpy._angelo_checkpoint.capture(owner)
            refused = []
            def attempt_cleanup(_main):
                actions = (lambda: _bpy._angelo_checkpoint.capture_close(capsule),
                           lambda: _bpy._angelo_checkpoint.capture_release(capsule),
                           lambda: _bpy._angelo_checkpoint.capture_abort(capsule, owner),
                           lambda: _bpy._angelo_checkpoint.capture_post(capsule, owner))
                for action in actions:
                    try:
                        action()
                    except RuntimeError:
                        refused.append(True)
                    else:
                        refused.append(False)
            handlers.append(attempt_cleanup)
            try:
                if failed:
                    _bpy._angelo_checkpoint.capture_abort(capsule, owner)
                else:
                    owner.publish(capsule)
                    _bpy._angelo_checkpoint.capture_post(capsule, owner)
                    owner.finish(capsule)
                self.assertEqual(refused, [True] * 4)
                with self.assertRaises(ValueError):
                    _bpy._angelo_checkpoint.capture_post(capsule, owner)
            finally:
                handlers.remove(attempt_cleanup)
                _bpy._angelo_checkpoint.capture_close(capsule)
                _bpy._angelo_checkpoint.capture_release(capsule)
                _bpy._angelo_checkpoint.capture_release(capsule)
                owner.abandon()

    def test_real_dyntopo_pair_restores_exact_surface_and_rejects_missing_safety(self):
        expected = self.dyntopo()
        record = self.capture()
        self.assertEqual(record["version"], 4)
        self.assertEqual(record["capture_validation"], "VALIDATED")
        self.assertEqual(self.surface(), expected)
        resource, prepared = self.prepare(record)
        with self.assertRaises((TypeError, ValueError)):
            self.restore(prepared, {"protected": True})
        self.assertEqual(self.surface(), expected)
        stale_safety = self.safety()
        old_path = bpy.data.filepath
        # Keep every ID and topology unchanged: membership alone must not certify safety.
        self.fixture_object().data.vertices[0].co.x += 0.25
        with self.assertRaises((ValueError, RuntimeError)):
            self.restore(prepared, stale_safety)
        self.assertEqual(bpy.data.filepath, old_path)
        _bpy._angelo_checkpoint.capture_release(stale_safety)
        @bpy.app.handlers.persistent
        def change_safety_after_write(_main):
            self.fixture_object().data.vertices[0].co.y += 0.25
            bpy.app.handlers.save_post.remove(change_safety_after_write)
        bpy.app.handlers.save_post.append(change_safety_after_write)
        try:
            with self.assertRaises(ValueError):
                self.safety()
            self.assertEqual(bpy.data.filepath, old_path)
        finally:
            if change_safety_after_write in bpy.app.handlers.save_post:
                bpy.app.handlers.save_post.remove(change_safety_after_write)
        safety = self.safety()
        refused = []
        @bpy.app.handlers.persistent
        def attempt_restore_cleanup(_main):
            actions = (lambda: _bpy._angelo_checkpoint.close_restore(prepared),
                       lambda: _bpy._angelo_checkpoint.capture_release(safety),
                       lambda: _bpy._angelo_checkpoint.capture_close(safety),
                       lambda: _bpy._angelo_checkpoint.restore(prepared, safety))
            for action in actions:
                try:
                    action()
                except RuntimeError:
                    refused.append(True)
                else:
                    refused.append(False)
        bpy.app.handlers.load_pre.append(attempt_restore_cleanup)
        bpy.app.handlers.load_post.append(attempt_restore_cleanup)
        try:
            self.assertIs(self.restore(prepared, safety), True)
            self.assertEqual(refused, [True] * 8)
        finally:
            for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.load_post):
                if attempt_restore_cleanup in handlers:
                    handlers.remove(attempt_restore_cleanup)
        self.assertEqual(Path(bpy.data.filepath), resource.path)
        self.assertEqual(self.surface(), expected)
        with self.assertRaises((TypeError, ValueError)):
            self.restore(prepared, safety)

    def retained_consumed_capsule_canary(self, refuse_first):
        native = _bpy._angelo_checkpoint
        case = {"first_refused": refuse_first, "first_replaced_main": False,
                "first_capsule_retained": False, "save_pre_calls": 0,
                "refresh_graph_resolved": False, "refresh_update_posts": [],
                "later_capture_ready": False,
                "second_restore_success": False, "surface_restored": False,
                "old_capsule_double_close_safe": False}
        RETAINED_CAPSULE_EVENTS.append(case)
        expected = self.dyntopo()
        base_x = self.fixture_object().data.vertices[0].co.x
        record = self.capture()
        resource, first = self.prepare(record)
        first_safety = self.safety()
        conflict_calls = []

        @bpy.app.handlers.persistent
        def refuse_selected(_main):
            obj = bpy.data.objects["CheckpointFixture"]
            obj.data.vertices[0].co.x += 0.5
            conflict_calls.append(float(obj.data.vertices[0].co.x))
            bpy.app.handlers.load_post.remove(refuse_selected)

        if refuse_first:
            bpy.app.handlers.load_post.append(refuse_selected)
            self.handlers.append(refuse_selected)
            with self.assertRaises(RuntimeError):
                self.restore(first, first_safety)
            self.assertEqual(conflict_calls, [base_x + 0.5])
            self.assertEqual(bpy.data.objects["CheckpointFixture"].data.vertices[0].co.x,
                             base_x + 0.5, "Refusal must preserve the callback's coarse edit")
        else:
            self.assertIs(self.restore(first, first_safety), True)
            self.assertEqual(self.surface(), expected)
        self.assertEqual(Path(bpy.data.filepath), resource.path)
        case["first_replaced_main"] = True
        self.assertIs(first, self.prepared[0])
        case["first_capsule_retained"] = True
        # An attempted Restore is consumed; keep its capsule and resource alive anyway.
        with self.assertRaises(ValueError):
            self.restore(first, first_safety)

        refresh_active = False
        callback_errors = []

        def graph_post(_scene, graph):
            if refresh_active:
                case["refresh_update_posts"].append([
                    {"id": update.id.name, "geometry": bool(update.is_updated_geometry)}
                    for update in graph.updates])

        def refresh_during_save_pre(_main):
            nonlocal refresh_active
            case["save_pre_calls"] += 1
            try:
                # This is a real tagged node evaluation under a later native capture lease.
                # With the old leak, it can invoke the retired hook's owner/stack reports.
                with self.view_context():
                    # A load_post refusal skips cold graph materialization. Resolve the live
                    # graph before tagging: scene_graph_update_tagged selects its callbacks
                    # before rebuilding relations, so a first lazy update can evaluate without
                    # an update-post. Bootstrap callbacks do not satisfy this tagged canary.
                    graph = bpy.context.evaluated_depsgraph_get()
                    self.assertIsNotNone(graph)
                    case["refresh_graph_resolved"] = True
                    obj = bpy.context.scene.objects["CheckpointFixture"]
                    self.assertEqual(bpy.context.view_layer.objects.active, obj)
                    refresh_active = True
                    obj.data.update_tag()
                    obj.update_tag(refresh={"DATA"})
                    bpy.context.view_layer.update()
            except BaseException:
                callback_errors.append(traceback.format_exc())
                raise
            finally:
                refresh_active = False

        bpy.app.handlers.depsgraph_update_post.append(graph_post)
        bpy.app.handlers.save_pre.append(refresh_during_save_pre)
        try:
            later = self.capture()
            self.assertEqual(later["state"], "READY")
            case["later_capture_ready"] = True
        finally:
            for handlers, callback in ((bpy.app.handlers.save_pre, refresh_during_save_pre),
                                       (bpy.app.handlers.depsgraph_update_post, graph_post)):
                if callback in handlers:
                    handlers.remove(callback)
        self.assertEqual(callback_errors, [], "Handler errors must not become apparent success")
        self.assertEqual(case["save_pre_calls"], 1)
        self.assertTrue(any(update["id"] == "CheckpointFixture" and update["geometry"]
                            for updates in case["refresh_update_posts"] for update in updates),
                        "The retained-capsule canary requires an actual geometry update-post")
        self.assertIs(first, self.prepared[0])

        # Keep the first consumed capsule alive through a second full WM Restore.
        second_resource, second = self.prepare(record)
        second_safety = self.safety()
        self.assertIs(first, self.prepared[0])
        self.assertIs(self.restore(second, second_safety), True)
        case["second_restore_success"] = True
        self.assertEqual(Path(bpy.data.filepath), second_resource.path)
        self.assertEqual(self.surface(), expected)
        case["surface_restored"] = True
        # Closing the older consumed capsule after the second Restore remains idempotent.
        native.close_restore(first)
        native.close_restore(first)
        case["old_capsule_double_close_safe"] = True

    def test_retained_consumed_capsule_after_success_allows_capture_and_second_restore(self):
        self.retained_consumed_capsule_canary(False)

    def test_retained_consumed_capsule_after_refusal_allows_capture_and_second_restore(self):
        self.retained_consumed_capsule_canary(True)

    def test_wrong_native_header_refuses_before_replacement_even_with_matching_declared_hash(self):
        expected = self.dyntopo()
        record = self.capture()
        folder = self.store.folder(record["id"])
        path = folder / "sculpt.runtime"
        with path.open("r+b") as file:
            first = file.read(1)
            file.seek(0)
            file.write(bytes([first[0] ^ 1]))
        metadata = json.loads((folder / "checkpoint.json").read_text())
        metadata["sculpt"]["hash"] = hashlib.blake2b(path.read_bytes(), digest_size=32).hexdigest()
        # Re-seal with the profile key, so only the native header check stands between this
        # companion and Restore (checkpoint_seal refuses an unsealed rewrite first).
        from angelo_agent.checkpoint_seal import seal
        from angelo_agent.checkpoint_transactions import _transaction
        with _transaction(self.store.root):
            metadata["component_seal"] = seal(self.store.root, metadata, metadata["scene_hash"])
        (folder / "checkpoint.json").write_text(json.dumps(metadata))
        resource = CheckpointRecovery.prepare(self.store, record["id"])
        self.resources.append(resource)
        filepath = bpy.data.filepath
        with self.assertRaises((RuntimeError, ValueError)):
            _bpy._angelo_checkpoint.prepare_restore(resource)
        self.assertEqual(bpy.data.filepath, filepath)
        self.assertEqual(self.surface(), expected)

    def test_load_post_same_topology_coordinate_conflict_is_partial_with_complete_safety(self):
        self.dyntopo()
        record = self.capture()
        resource, prepared = self.prepare(record)
        safety = self.safety()
        nested_refused = []
        @bpy.app.handlers.persistent
        def change_selected(_main):
            bpy.data.objects["CheckpointFixture"].data.vertices[0].co.x += 0.5
            # A self-removing handler must not free the live target behind read_post's locals.
            bpy.app.handlers.load_post.remove(change_selected)
            try:
                nested_refused.append(bpy.ops.wm.read_factory_settings(use_empty=True) == {"CANCELLED"})
            except RuntimeError:
                nested_refused.append(True)
        bpy.app.handlers.load_post.append(change_selected)
        self.handlers.append(change_selected)
        with self.assertRaises(RuntimeError):
            self.restore(prepared, safety)
        self.assertEqual(Path(bpy.data.filepath), resource.path)
        self.assertEqual(nested_refused, [True])
        self.assertTrue(any(row.get("protected") for row in self.store.inspect()["checkpoints"]))


def run():
    report = {"success": False, "testsrun": 0, "failures": 0, "errors": 0, "skips": 0}
    try:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(NativeCheckpointColdRecovery)
        assert suite.countTestCases() == 6
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        report.update(testsrun=result.testsRun, failures=len(result.failures),
                      errors=len(result.errors), skips=len(result.skipped))
        report["success"] = (result.testsRun == 6 and result.wasSuccessful() and
                             not result.skipped and len(RETAINED_CAPSULE_EVENTS) == 2 and
                             all(case["surface_restored"] and case["later_capture_ready"] and
                                 case["old_capsule_double_close_safe"]
                                 for case in RETAINED_CAPSULE_EVENTS))
    except BaseException:
        report["errors"] += 1
        report["error"] = traceback.format_exc()
    finally:
        report["retained_capsule_canaries"] = RETAINED_CAPSULE_EVENTS
        # The external suite runner supplies the hard timeout for blocked native calls.
        # Every returned Python path writes one fresh receipt and requests GUI shutdown.
        try:
            with RECEIPT.open("x", encoding="utf-8") as file:
                json.dump(report, file, indent=2)
            print("ANGELO_NATIVE_CHECKPOINT_COLD_RECOVERY", json.dumps(report), flush=True)
        finally:
            bpy.ops.wm.quit_blender()
    return None


if __name__ == "__main__":
    bpy.app.timers.register(run, first_interval=1)
