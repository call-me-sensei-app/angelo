# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A checkpoint's capture-time scene map (break-it R5M1 round 2, R2-M-4): where it is kept and how it reads.

Restore rebinds each chat holding work to the restored scene that had its scene's name when the checkpoint was
taken (``checkpoint_scene_map``); the Restore flow itself is ``test_native_checkpoint_restore_scenes``. Here: the map
is kept beside the ``checkpoints`` folder, never in a record or a checkpoint folder (an earlier build refuses either),
written atomically after its capture; a missing or malformed one reads as no map; it is removed with its checkpoint
and, once orphaned, by the snapshot worker's maintenance; the folder is private application state; and the lookup and
the links a Restore uses.
"""

from contextlib import nullcontext
import copy
import json
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import checkpoint_capture, checkpoint_scene_map as scene_map, checkpoints as checkpoint_module  # noqa: E402
from angelo_agent import document_checkpoint, snapshot_worker  # noqa: E402
from angelo_agent.checkpoint_records import _record  # noqa: E402
from angelo_agent.checkpoints import CheckpointStore  # noqa: E402

MAP = {"load": "load-a", "names": [[11, "Atelier"], [12, "Garden"], [13, "Harbor"]]}
MALFORMED = ("load-a", [], 7, {"load": "load-a"}, {"names": [[11, "Atelier"]]},
             {"load": "load-a", "names": [[11, "Atelier"]], "more": 1}, {"load": "", "names": [[11, "Atelier"]]},
             {"load": 7, "names": [[11, "Atelier"]]}, {"load": "load-a", "names": []},
             {"load": "load-a", "names": {"11": "Atelier"}}, {"load": "load-a", "names": [[11]]},
             {"load": "load-a", "names": [[11, "Atelier", 3]]}, {"load": "load-a", "names": [["11", "Atelier"]]},
             {"load": "load-a", "names": [[True, "Atelier"]]}, {"load": "load-a", "names": [[0, "Atelier"]]},
             {"load": "load-a", "names": [[-4, "Atelier"]]}, {"load": "load-a", "names": [[11.0, "Atelier"]]},
             {"load": "load-a", "names": [[11, None]]},
             {"load": "load-a", "names": [[11, "Atelier"], [11, "Garden"]]},     # One uid twice.
             {"load": "load-a", "names": [[11, "Atelier"], [12, "Atelier"]]})    # One name twice.


def scenes(**uids):
    return [SimpleNamespace(name_full=name, session_uid=uid) for name, uid in uids.items()]


def reset_links():
    for state in (scene_map._parents, scene_map._absent, scene_map._loads, scene_map._written):
        state.clear()


def written(*loads):
    """As if this process had kept a map of a checkpoint of each load (``checkpoint_scene_map.save``): the loads
    whose links a Restore keeps."""
    for load in loads:
        scene_map._written[("profile", load, str(uuid.uuid4()))] = load


class SceneMapTests(unittest.TestCase):
    def setUp(self):
        reset_links()
        self.addCleanup(reset_links)

    def test_a_capture_records_each_scenes_name_by_its_session_uid(self):
        self.assertEqual(scene_map.capture("load-a", scenes(Atelier=11, Garden=12, Harbor=13)), MAP)
        for load in (None, "", 7):
            with self.subTest(load=load):
                self.assertIsNone(scene_map.capture(load, scenes(Atelier=11)), "no load serial: no map")
        self.assertIsNone(scene_map.capture("load-a", []))

    def test_a_map_past_its_budget_is_left_out(self):
        many = [SimpleNamespace(name_full="Scene %06d " % uid + "x" * 40, session_uid=uid)
                for uid in range(1, scene_map.BUDGET // 40)]
        self.assertIsNone(scene_map.capture("load-a", many), "Restore then rebinds by name")
        self.assertEqual(len(scene_map.capture("load-a", many[:100])["names"]), 100)

    def test_a_malformed_map_reads_as_absent(self):
        for value in MALFORMED + (None, {"load": "load-a", "names": [(11, "Atelier")]}):
            with self.subTest(value=value):
                self.assertIsNone(scene_map.clean(value))
                self.assertEqual(scene_map.captured_name(value, ("load-a", 11)), (False, None))
                scene_map.remember_restored(value, "load-b", scenes(Atelier=21), [])
                self.assertEqual(scene_map._parents, {})

    def test_a_clean_map_is_a_copy(self):
        value = copy.deepcopy(MAP)
        cleaned = scene_map.clean(value)
        value["names"][0][1] = "Changed"
        self.assertEqual(cleaned, MAP)

    def test_the_name_a_scene_had_at_capture_by_its_uid_in_the_captures_load(self):
        self.assertEqual(scene_map.captured_name(MAP, ("load-a", 13)), (True, "Harbor"))
        self.assertEqual(scene_map.captured_name(MAP, ("load-a", 99)), (True, None), "made after the capture")
        self.assertEqual(scene_map.captured_name(MAP, ("load-b", 13)), (False, None), "another load: no answer")
        self.assertEqual(scene_map.captured_name(None, ("load-a", 13)), (False, None))

    def test_a_restored_scene_continues_the_captured_scene_of_its_name(self):
        restored = scenes(Harbor=31, Garden=32, Atelier=33)
        scene_map.remember_restored(MAP, "load-b", restored, [("load-b", 33)])
        self.assertEqual(scene_map._parents, {("load-b", 31): {("load-a", 13)}, ("load-b", 32): {("load-a", 12)},
                                              ("load-b", 33): {("load-a", 11)}})
        # A later capture of load-a (Undo restore's safety) named them otherwise: each restored scene still answers.
        later = {"load": "load-a", "names": [[11, "Loft"], [12, "Garden"], [13, "Atelier"]]}
        self.assertEqual(scene_map.captured_name(later, ("load-b", 33)), (True, "Loft"))
        self.assertEqual(scene_map.captured_name(later, ("load-b", 31)), (True, "Atelier"))
        # A chat's key no scene has (its scene left out) stands for its scene before the load.
        scene_map.continues(("load-c", 31), ("load-b", 31))
        self.assertEqual(scene_map.captured_name(later, ("load-c", 31)), (True, "Atelier"))

    def test_only_links_a_scene_or_a_chat_still_reaches_are_kept(self):
        written("load-a")
        scene_map.remember_restored(MAP, "load-b", scenes(Harbor=31, Atelier=33), [])
        scene_map.continues(("load-b", 12), ("load-a", 12))   # A chat whose scene (Garden) was left out.
        second = {"load": "load-b", "names": [[31, "Harbor"], [33, "Atelier"]]}
        scene_map.remember_restored(second, "load-c", scenes(Harbor=41), [("load-b", 12)])
        # Compacted: each kept key lists its ancestors in the loads a map names (load-b: just read, load-a: written).
        self.assertEqual(scene_map._parents, {("load-c", 41): {("load-b", 31), ("load-a", 13)},
                                              ("load-b", 12): {("load-a", 12)}})
        self.assertEqual(scene_map.captured_name(MAP, ("load-c", 41)), (True, "Harbor"))
        scene_map.remember_restored(None, "load-d", scenes(Harbor=51), [])
        self.assertEqual(scene_map._parents, {}, "no map, nothing held: nothing kept")

    def test_a_key_continues_both_its_captured_scene_and_the_chats_key_before_the_load(self):
        """break-it R4-M-2: C0 (load-a) restored into load-b, names swapped there, C0 again into load-c; the safety (or
        a request checkpoint) of load-b then still finds the chat's scene through its load-b key."""
        written("load-b")   # That checkpoint's map.
        scene_map.remember_restored(MAP, "load-b", scenes(Atelier=21, Garden=22, Harbor=23), [])
        scene_map.continues(("load-c", 31), ("load-b", 21))   # The chat, rebound from its load-b key...
        scene_map.remember_restored(MAP, "load-c", scenes(Atelier=31, Garden=32, Harbor=33), [("load-c", 31)])
        self.assertEqual(scene_map._parents[("load-c", 31)], {("load-a", 11), ("load-b", 21)})   # ...and its scene.
        swapped = {"load": "load-b", "names": [[21, "Harbor"], [22, "Garden"], [23, "Atelier"]]}
        self.assertEqual(scene_map.captured_name(swapped, ("load-c", 31)), (True, "Harbor"))
        self.assertEqual(scene_map.captured_name(MAP, ("load-c", 31)), (True, "Atelier"))

    def test_keys_that_name_two_scenes_of_one_load_give_no_answer(self):
        scene_map.continues(("load-b", 31), ("load-a", 11))
        scene_map.continues(("load-b", 31), ("load-a", 13))
        self.assertEqual(scene_map.captured_name(MAP, ("load-b", 31)), (False, None), "ambiguous: by name")


class StorageTests(unittest.TestCase):
    """A real temporary profile: the store's own anchored files and transaction."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-scene-map-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "state"
        self.project = Path(self.temp.name).resolve() / "project"
        self.project.mkdir()
        self.store = CheckpointStore(self.root, str(uuid.uuid4()))

    def snapshot(self):
        record, path = self.store.reserve(str(self.project / "original.blend"))
        path.write_bytes(b"synthetic scene document")
        return self.store.complete(record["id"])

    def path(self, record, store=None):
        store = store or self.store
        return self.root / scene_map.FOLDER / store.session_id / (record["id"] + ".json")

    def test_kept_beside_the_checkpoints_never_in_a_record_or_a_checkpoint_folder(self):
        record = self.snapshot()
        self.assertTrue(scene_map.save(self.store, record["id"], copy.deepcopy(MAP)))
        self.assertEqual(json.loads(self.path(record).read_text()), MAP)
        self.assertEqual(stat.S_IMODE(self.path(record).stat().st_mode), 0o600)
        self.assertEqual(list(self.path(record).parent.iterdir()), [self.path(record)], "no temporary file left")
        self.assertEqual(scene_map.read(self.store, record["id"]), MAP)
        self.assertEqual({path.name for path in self.store.folder(record["id"]).iterdir()},
                         {"checkpoint.json", "scene.blend"}, "the checkpoint folder an earlier build reads is unchanged")
        self.assertNotIn("scenes", self.store.load(record["id"]))
        with self.assertRaisesRegex(ValueError, "Invalid snapshot metadata"):   # The record format stays 1.0.5's.
            _record(dict(self.store.load(record["id"]), scenes=MAP), record["id"])
        self.assertEqual(self.store.inspect()["issues"], [])

    def test_no_map_or_a_malformed_one_reads_as_none_never_raises(self):
        record = self.snapshot()
        self.assertIsNone(scene_map.read(self.store, record["id"]), "none written (an earlier build's checkpoint)")
        self.assertIsNone(scene_map.read(SimpleNamespace(), record["id"]), "no profile store")
        self.assertIsNone(scene_map.read(self.store, "not-an-identity"))
        self.assertFalse(scene_map.save(self.store, record["id"], None))
        for value in MALFORMED:
            with self.subTest(value=value):
                self.assertFalse(scene_map.save(self.store, record["id"], value), "never written")
        scene_map.save(self.store, record["id"], MAP)
        for payload in (b"", b"{", b"\xff\xfe", json.dumps(MALFORMED[-1]).encode(), b"[1, 2]"):
            with self.subTest(payload=payload):
                self.path(record).write_bytes(payload)
                self.assertIsNone(scene_map.read(self.store, record["id"]))
        self.path(record).unlink()
        self.path(record).symlink_to(self.root / "elsewhere.json")
        (self.root / "elsewhere.json").write_text(json.dumps(MAP))
        self.assertIsNone(scene_map.read(self.store, record["id"]), "a link is never followed")

    def test_a_map_that_cannot_be_written_never_fails_its_caller(self):
        record = self.snapshot()
        (self.root / scene_map.FOLDER).write_text("not a folder")
        self.assertFalse(scene_map.save(self.store, record["id"], MAP))
        with patch.object(scene_map, "_transaction", side_effect=RuntimeError("Another app is updating snapshots")):
            self.assertFalse(scene_map.save(self.store, record["id"], MAP))

    def test_removing_a_checkpoint_removes_its_map(self):
        first, second, third = [self.snapshot() for _ in range(3)]
        for record in (first, second, third):
            scene_map.save(self.store, record["id"], MAP)
        plan = self.store.prune_plan({"max_count": 1})
        self.assertEqual([item["id"] for item in plan["candidates"]], [first["id"], second["id"]])
        self.store.prune(plan, user_confirmed=True)
        self.assertFalse(self.path(first).exists() or self.path(second).exists())
        self.assertEqual(scene_map.read(self.store, third["id"]), MAP)

    def test_a_map_whose_removal_failed_is_left_for_the_orphan_sweep(self):
        first, second = self.snapshot(), self.snapshot()
        scene_map.save(self.store, first["id"], MAP)
        with patch.object(scene_map, "remove", side_effect=OSError("held open")):
            self.store.prune(self.store.prune_plan({"max_count": 1}), user_confirmed=True)
        self.assertEqual(self.store.availability(first["id"])["state"], "PRUNED", "the removal itself succeeded")
        self.assertTrue(self.path(first).exists())
        self.assertEqual(scene_map.drop_orphans(self.store), 1)
        self.assertFalse(self.path(first).exists())

    def test_orphan_maps_are_dropped_and_live_or_unreadable_ones_kept(self):
        live, unreadable = self.snapshot(), self.snapshot()
        other = CheckpointStore(self.root, str(uuid.uuid4()))
        gone = str(uuid.uuid4())
        for store, identity in ((self.store, live["id"]), (self.store, unreadable["id"]), (self.store, gone),
                                (other, str(uuid.uuid4()))):
            self.assertTrue(scene_map.save(store, identity, MAP))
        (self.store.folder(unreadable["id"]) / "checkpoint.json").write_text("{")   # Fail closed: kept.
        leftover = self.root / scene_map.FOLDER / self.store.session_id / ".crashed.tmp"
        leftover.write_text("partial")
        stranger = self.root / scene_map.FOLDER / self.store.session_id / "notes.txt"
        stranger.write_text("not a map")
        self.assertEqual(scene_map.drop_orphans(self.store), 2)
        self.assertEqual(sorted(path.name for path in self.path(live).parent.iterdir()),
                         sorted([live["id"] + ".json", unreadable["id"] + ".json", "notes.txt"]))
        self.assertEqual(list(self.path(live, other).parent.iterdir()), [])
        self.assertEqual(scene_map.drop_orphans(self.store), 0, "nothing left to drop")

    def test_the_snapshot_worker_drops_orphan_maps_in_its_maintenance(self):
        record = self.snapshot()
        scene_map.save(self.store, str(uuid.uuid4()), MAP)
        scene_map.save(self.store, record["id"], MAP)
        events = []
        worker = snapshot_worker.SnapshotWorker(self.root)
        with patch.object(worker, "_emit", side_effect=lambda kind, **values: events.append((kind, values))):
            worker._maintain({}, (), 10 ** 6, None, False)
        self.assertEqual(events[-1][1]["summary"]["scene_maps"], 1)
        self.assertEqual([path.name for path in self.path(record).parent.iterdir()], [record["id"] + ".json"])

    def test_the_folder_is_private_application_state(self):
        self.assertIn(scene_map.FOLDER, checkpoint_module._PRIVATE_NAMES)
        record = self.snapshot()
        scene_map.save(self.store, record["id"], MAP)
        relative = str(Path(scene_map.FOLDER) / self.store.session_id / (record["id"] + ".json"))
        with self.assertRaisesRegex(ValueError, "Private application state"):   # No file tool can journal or write it.
            self.store.journal(record["id"], self.root, relative)
        self.assertEqual(scene_map.read(self.store, record["id"]), MAP)


class CaptureTests(unittest.TestCase):
    """The document capture keeps the map after its capture succeeds; a failed capture or a worker's keeps none."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-scene-map-capture-")
        self.addCleanup(self.temp.cleanup)
        self.store = CheckpointStore(Path(self.temp.name).resolve() / "state", str(uuid.uuid4()))

    def capture(self, load=None, fail=False, retain=False):
        prefs = SimpleNamespace(filepaths=SimpleNamespace(file_preview_type="AUTO", save_modified_images="ASK"),
                                is_dirty=False, bl_rna=SimpleNamespace(properties={"is_dirty": SimpleNamespace(is_readonly=True)}))
        bpy = SimpleNamespace(context=SimpleNamespace(preferences=prefs, temp_override=lambda **_: nullcontext()),
                              data=SimpleNamespace(use_autopack=False, scenes=scenes(Atelier=11, Garden=12, Harbor=13)),
                              app=SimpleNamespace(handlers=SimpleNamespace(save_pre=[], save_post=[])))

        def native(store, original, **arguments):
            if fail:
                raise RuntimeError("Injected capture failure")
            record, path = store.reserve(original, capture_mode=arguments["capture_mode"], purpose=arguments["purpose"])
            path.write_bytes(b"synthetic scene document")
            record = store.complete(record["id"])
            return (record, object()) if arguments["retain_native"] else record
        with patch.object(checkpoint_capture, "capture_checkpoint", side_effect=native):
            return document_checkpoint.capture(bpy, self.store, "window", "", capture_mode="EVERY_REQUEST",
                                               purpose="safety" if retain else "request", retain_native=retain,
                                               load=load)

    def test_the_document_capture_keeps_what_each_scene_is_called_now(self):
        record = self.capture(load="load-a")
        self.assertEqual(scene_map.read(self.store, record["id"]), MAP)
        record, _native = self.capture(load="load-a", retain=True)   # Restore's safety capture.
        self.assertEqual(scene_map.read(self.store, record["id"]), MAP)

    def test_a_workers_capture_and_a_failed_one_keep_no_map(self):
        record = self.capture()
        self.assertIsNone(scene_map.read(self.store, record["id"]), "a worker's capture passes no load")
        with self.assertRaisesRegex(RuntimeError, "Injected capture failure"):
            self.capture(load="load-a", fail=True)
        self.assertFalse((self.store.root / scene_map.FOLDER).exists() and any(
            (self.store.root / scene_map.FOLDER / self.store.session_id).iterdir()))

    def test_a_map_that_cannot_be_kept_never_fails_the_capture(self):
        with patch.object(scene_map, "_write_json", side_effect=OSError("disk full")):
            record = self.capture(load="load-a")
        self.assertEqual(record["state"], "READY")
        self.assertIsNone(scene_map.read(self.store, record["id"]))


if __name__ == "__main__":
    unittest.main()
