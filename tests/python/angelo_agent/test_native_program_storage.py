# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Immutable teardown ownership after partial deletion, without native RNA."""

import contextlib
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import program_storage as storage, native_programs as native, program_admission as admission
from angelo_agent.program_jobs import ProgramError, ProgramJob, WriterLease
from angelo_agent.program_dependencies import SuspendedStage


class FakeID:
    def __init__(self, pointer, kind="Mesh"):
        self.pointer, self.alive, self.name = pointer, True, "Stage"
        self._uid, self._rna = pointer * 10, SimpleNamespace(identifier=kind, properties=[])
        self.id_type = kind.upper()
        self.values = {}

    def as_pointer(self):
        if not self.alive:
            raise ReferenceError("freed RNA must never be followed")
        return self.pointer

    @property
    def session_uid(self):
        self.as_pointer()
        return self._uid

    @property
    def bl_rna(self):
        self.as_pointer()
        return self._rna

    def get(self, _key):
        return False

    def keys(self):
        return self.values.keys()

    def __getitem__(self, name):
        return self.values[name]


class FakeObject(FakeID):
    pass


class FakeScene(FakeID):
    pass


class MainCollection(list):
    def get(self, name):
        return next((block for block in self if block is not None and block.name == name), None)


class Backend(storage.ProgramStorageMixin):
    mutation_started = False
    memory_staging = False  # Host doubles exercise the serialized ownership guards.

    def ensure_main_thread(self):
        pass

    def check_context(self, _binding):
        pass

    def verify(self, _binding):
        pass

    @property
    def cleanup_pending(self):
        return storage.has_pending_cleanup()


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.first, self.second, self.original = FakeObject(1, "Object"), FakeScene(2, "Scene"), FakeID(3)
        self.current = {block.pointer: block for block in (self.first, self.second, self.original)}
        self.backend = Backend()
        self.batch_failure = True
        self.fail_after_first = False
        self.removals, self.batch_calls = [], []
        self.stage = SimpleNamespace(owned_ids=[self.first, self.second], context=None, bundle="", scene=self.second,
                                     binding={}, objects={}, changes={}, logical_names={})
        owner = self
        class Collection:
            def __iter__(self):
                return iter(list(owner.current.values()))
            def remove(self, block, **kwargs):
                owner.remove(block, **kwargs)
        collection = Collection()
        self.bpy = SimpleNamespace(types=SimpleNamespace(Object=FakeObject, Scene=FakeScene),
                                   data=SimpleNamespace(batch_remove=self.batch_remove, scenes=MainCollection()))
        self.patches = contextlib.ExitStack()
        self.patches.enter_context(patch.object(storage, "_bpy", return_value=self.bpy))
        self.patches.enter_context(patch.object(storage, "_main_ids", side_effect=lambda: dict(self.current)))
        self.patches.enter_context(patch.object(storage, "_data_collections", return_value=[collection]))
        self.addCleanup(self.patches.close)
        self.addCleanup(storage._pending_cleanup.clear)
        storage._pending_cleanup.clear()

    def remove(self, block, **_kwargs):
        pointer = block.as_pointer()
        self.current.pop(pointer)
        block.alive = False
        self.removals.append(pointer)
        if self.fail_after_first:
            raise RuntimeError("injected removal failure AFTER first ID was freed")

    def batch_remove(self, *, ids):
        self.batch_calls.append([block.as_pointer() for block in ids])
        if self.batch_failure:
            raise RuntimeError("injected batch cleanup unavailable")
        for block in ids:
            self.current.pop(block.as_pointer())
            block.alive = False

    def partial_override(self, ids):
        first = next(block for block in ids if isinstance(block, FakeObject))
        self.remove(first)
        raise RuntimeError("injected remover failed AFTER first ID was freed")

    def assert_retained(self):
        self.assertFalse(self.first.alive)
        self.assertTrue(self.original.alive)
        retained = {pointer: identity for record in storage._pending_cleanup.values() for pointer, identity in record.items()}
        self.assertEqual(retained, {2: (20, "SCENE")})
        with self.assertRaisesRegex(ProgramError, "blocked"):
            storage.require_cleanup_complete()
        with patch.object(native, "_bpy", return_value=self.bpy):
            self.assertTrue(native.runtime_is_staging())  # No scene tag is needed to retain the gate.
        self.batch_failure = False
        self.assertFalse(storage.cleanup_pending())
        self.assertEqual(set(self.current), {3})

    def test_remove_ids_captures_before_native_removal_partial_failure(self):
        self.fail_after_first = True
        with self.assertRaises(ProgramError) as failure:
            self.backend._remove_ids([self.first, self.second])
        self.assertTrue(failure.exception.cleanup_pending)
        self.assertEqual(failure.exception.temporary_ids_remaining, 1)
        self.assert_retained()

    def test_clean_teardown_maps_main_once_and_verifies_once(self):
        storage._main_ids.reset_mock()
        result = self.backend._remove_stage_ids(self.stage, keep={3})
        self.assertIsNone(result)
        self.assertEqual(storage._main_ids.call_count, 2)  # Was six with two nested post-checks each.
        self.assertEqual(set(self.current), {3})
        self.assertEqual(self.batch_calls, [])
        self.assertFalse(storage._pending_cleanup)

    def test_outer_guard_still_checks_a_remover_that_returns_without_removing(self):
        self.batch_failure = False
        with patch.object(self.backend, "_remove_ids", return_value=None):
            self.backend._remove_stage_ids(self.stage, keep={3})
        self.assertEqual(self.batch_calls, [[1, 2]])
        self.assertEqual(set(self.current), {3})
        self.assertTrue(self.original.alive)

    def test_ownership_check_reads_identity_only_for_captured_pointers(self):
        reads = []
        uid = FakeID.session_uid
        with patch.object(FakeID, "session_uid", property(lambda block: reads.append(block.pointer) or uid.fget(block))):
            owned = storage._owned_cleanup_ids({2: (20, "SCENE")})
        self.assertEqual(list(owned), [2])
        self.assertEqual(set(reads), {2})  # IDs 1 and 3 were never captured, so never read.

    def test_removal_uses_the_typed_main_collection_and_skips_embedded_ids(self):
        typed = []

        class Typed:
            def __init__(self, blocks):
                self.blocks = {block.name: block for block in blocks}

            def __len__(self):
                return len(self.blocks)

            def get(self, name):
                return self.blocks.get(name)

            def remove(self, block, **kwargs):
                typed.append(block.as_pointer())
                self.blocks.pop(block.name)

        self.first.id_type = "OBJECT"
        embedded = FakeID(7)
        embedded.id_type = "NODETREE"
        self.bpy.data.objects = Typed([self.first])
        self.bpy.data.node_groups = Typed([])
        self.backend._remove_ids_unchecked([self.first, embedded])
        self.assertEqual(typed, [1])
        self.assertTrue(embedded.alive)
        self.assertNotIn(7, self.removals)

    def test_large_scene_batches_only_owned_main_ids(self):
        self.bpy.data.objects = MainCollection([None] * 1024)
        self.batch_failure = False
        embedded = FakeID(7, "NodeTree")
        self.backend._remove_ids([self.first, self.second, self.first, embedded])
        self.assertEqual(self.batch_calls, [[1, 2]])
        self.assertEqual(set(self.current), {3})
        self.assertTrue(self.original.alive and embedded.alive)
        self.assertFalse(storage.has_pending_cleanup())

    def test_large_scene_partial_batch_failure_keeps_exact_survivors(self):
        self.bpy.data.collections = MainCollection([None] * 256)
        attempts = []

        def partial_batch(*, ids):
            attempts.append([block.as_pointer() for block in ids])
            if len(attempts) == 1:
                self.remove(self.first)
            raise RuntimeError("injected partial batch failure")

        self.bpy.data.batch_remove = partial_batch
        with self.assertRaisesRegex(ProgramError, "partial batch"):
            self.backend._remove_ids([self.first, self.second])
        self.assertEqual(attempts, [[1, 2], [2]])
        self.bpy.data.batch_remove = self.batch_remove
        self.assert_retained()

    def test_stage_outer_guard_survives_overridden_partial_remover(self):
        with patch.object(self.backend, "_remove_ids", side_effect=self.partial_override):
            with self.assertRaisesRegex(ProgramError, "AFTER first"):
                self.backend._remove_stage_ids(self.stage, keep={3})
        self.assert_retained()

    def test_cancel_can_release_job_but_not_teardown_ownership(self):
        writer = WriterLease()
        job = ProgramJob(self.backend, {"mode": "APPLY", "operations": []}, writer=writer)
        job.stage = self.stage
        with patch.object(self.backend, "_remove_ids", side_effect=self.partial_override):
            result = job.cancel()
        self.assertIsNone(job.stage)
        self.assertIsNone(writer.owner)
        self.assertTrue(result["cleanup_warning"] and result["cleanup_pending"])
        self.assertTrue(result["partial_changes_possible"])
        self.assertFalse(result["scene_changed"])
        self.assert_retained()

    def test_failed_suspension_removes_bundle_but_retains_main_survivors(self):
        bundles = []
        def write(path, *_args, **_kwargs):
            Path(path).write_text("private fixture")
            Path(path + "@").write_text("partial")   # Blender's temporary name beside it.
            bundles.append(Path(path))
        self.bpy.data.libraries = SimpleNamespace(write=write)
        with patch.object(self.backend, "_remove_ids", side_effect=self.partial_override):
            with self.assertRaisesRegex(ProgramError, "AFTER first"):
                self.backend.suspend_stage(self.stage)
        # The bundle and its partial write go; the process's shared private folder stays (stage_bundle_folder).
        self.assertTrue(bundles and not bundles[0].exists() and not Path(str(bundles[0]) + "@").exists())
        self.assert_retained()

    def test_preview_context_does_not_lose_failed_inspection_cleanup(self):
        record = {"backend": self.backend, "binding": {}, "stage": "retained private bundle"}
        self.backend.resume_stage = lambda _: self.stage
        window = SimpleNamespace(as_pointer=lambda: 123)
        with patch.object(native.PREVIEWS, "get", return_value=record), patch.object(self.backend, "_remove_ids", side_effect=self.partial_override):
            with self.assertRaisesRegex(ProgramError, "AFTER first"):
                with native.preview_context(str(uuid.uuid4()), window=window) as stage:
                    self.assertIs(stage, self.stage)
        self.assertEqual(record["stage"], "retained private bundle")
        self.assert_retained()

    def test_hydration_keeps_original_failure_and_exact_cleanup_ownership(self):
        self.current = {3: self.original}
        @contextlib.contextmanager
        def load(_path, **_kwargs):
            destination = SimpleNamespace(scenes=[])
            yield SimpleNamespace(scenes=["Stage"]), destination
            self.current.update({1: self.first, 2: self.second})
            destination.scenes = [self.second]  # Invalid tag forces post-load admission failure.
        self.bpy.data.libraries = SimpleNamespace(load=load)
        with tempfile.TemporaryDirectory(prefix="angelo-native-preview-") as folder:
            bundle = Path(folder) / "fixture.blend"
            bundle.write_text("private fixture")
            descriptor = SuspendedStage(str(bundle), "Stage", {}, {}, {}, {}, {})
            with patch.object(self.backend, "_remove_ids", side_effect=self.partial_override):
                with self.assertRaises(ProgramError) as failure:
                    self.backend.resume_stage(descriptor)
            self.assertIn("not an admitted", str(failure.exception))
            self.assertIn("AFTER first", failure.exception.cleanup_error)
            self.assertTrue(bundle.exists())
        self.assert_retained()

    def test_pointer_reuse_and_type_reuse_never_delete_unowned_main_ids(self):
        storage.retain_cleanup({2: (20, "SCENE")})
        self.second._uid = 21
        self.assertFalse(storage.cleanup_pending())
        self.assertTrue(self.second.alive)
        storage.retain_cleanup({2: (21, "MESH")})
        self.assertFalse(storage.cleanup_pending())
        self.assertTrue(self.second.alive)
        self.assertFalse(self.batch_calls)

    def test_rna_subtype_refinement_preserves_exact_cleanup_identity(self):
        old = FakeID(4, 'PointLight')
        current = FakeID(4, 'SpotLight')
        old.id_type = current.id_type = 'LIGHT'
        self.current[4] = current
        identities = storage.cleanup_identities([old])
        self.assertEqual(identities, {4: (40, 'LIGHT')})
        self.assertIs(storage._owned_cleanup_ids(identities)[4], current)
        storage.retain_cleanup(identities)
        self.batch_failure = False
        self.assertFalse(storage.cleanup_pending())
        self.assertFalse(current.alive)
        self.assertTrue(self.original.alive)

    def test_id_family_change_cannot_reuse_an_owned_pointer_and_uid(self):
        old = FakeID(4, 'PointLight')
        old.id_type = 'LIGHT'
        current = FakeID(4, 'Mesh')
        self.current[4] = current
        storage.retain_cleanup(storage.cleanup_identities([old]))
        self.assertFalse(storage.cleanup_pending())
        self.assertTrue(current.alive)
        self.assertFalse(self.batch_calls)

    def test_successful_fallback_warns_without_retaining_stale_registry(self):
        self.batch_failure = False
        with patch.object(self.backend, "_remove_ids", side_effect=self.partial_override):
            with self.assertRaises(ProgramError) as failure:
                self.backend._remove_stage_ids(self.stage, keep=set())
        self.assertFalse(failure.exception.cleanup_pending)
        self.assertFalse(storage.has_pending_cleanup())
        self.assertEqual(set(self.current), {3})

    def test_admission_rejects_nested_and_embedded_custom_id_links(self):
        class Holder:
            bl_rna = SimpleNamespace(properties=[])
            def as_pointer(self):
                return id(self)
            def keys(self):
                return self.values.keys()
            def __getitem__(self, name):
                return self.values[name]
        root, embedded = Holder(), Holder()
        root.values = {"number": 1., "text": "artist label", "nested": {"vector": [1, 2, 3]}}
        root.bl_rna = SimpleNamespace(properties=[SimpleNamespace(identifier="settings", type="POINTER")])
        root.settings = embedded
        embedded.values = {}
        fake = SimpleNamespace(types=SimpleNamespace(ID=FakeID, Mesh=type("Mesh", (FakeID,), {}), Object=FakeObject,
                                                    Scene=FakeScene, Collection=type("Collection", (FakeID,), {})))
        with patch.object(admission, "_bpy", return_value=fake):
            admission.assert_supported_custom_links(root)
            embedded.values = {"nested": {"unsupported": self.original}}
            with self.assertRaisesRegex(ProgramError, "ID-valued custom"):
                admission.assert_supported_custom_links(root)
            embedded.values = {}
            root.image = FakeID(4, 'Image')
            root.image.values = {"unsupported": self.original}
            root.bl_rna.properties.append(SimpleNamespace(identifier="image", type="POINTER"))
            with self.assertRaisesRegex(ProgramError, "ID-valued custom"):
                admission.assert_supported_custom_links(root)  # Read-only serialized resources also need admission.


if __name__ == "__main__":
    unittest.main()
