# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Checkpoint content binding in the built app (BACKGROUND, one factory process; no provider or paid call).

Native hashes what its capture writes and compares it, inside native, with the bytes ``publish`` reads back
(``doc/angelo/checkpoint_policy.rst``, "Content binding"). The rewrite proved here is the one stamps cannot see on
APFS: after native's last write and before Python's first witness (the first ``_native_facts`` call) another writer
replaces bytes in place, same size, and puts the modification time back with ``futimens`` (nanoseconds; no fake
clock). Native's ``admit`` ignores ctime, so before content binding this capture was sealed over foreign bytes.

* A safety capture taken in Object mode (no Sculpt owner) and a Sculpt pair (BASE or RUNTIME rewritten) are refused:
  SAVE_POST_FAIL, never SAVE_POST, nothing READY. Through the Restore operator, from Object mode, to a version 3 and
  a version 4 target: the Restore is refused before ``native.restore`` and the document is unchanged; untouched,
  the safety record is content-sealed and Restores.
* Native admission: POST is refused when the bind was skipped (pair and safety); hasher misuse is refused (wrong
  role, another capsule's hasher, a partial read, a spent hasher, a second ``capture_bind``); ``capture_facts`` keys
  and the module's public names are pinned, so no API returns the binding; an ordinary request on this (fine-clock)
  volume binds nothing; a fine prediction contradicted by a coarse stamp is refused; in a WITH_GTESTS build, a
  capture whose Sculpt owners appear without a BASE binding is refused, and a Restore that fails after replacing the
  document reloads the content-sealed safety, or refuses it when its bytes were rewritten keeping the stamp
  (``gtest_seams`` in the receipt says whether those cases ran).

Writes ``checkpoint-content-binding.json`` with every check; exits non-zero on any failure.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
import unittest
from unittest.mock import patch
import uuid

import addon_utils
import bpy
import _bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Requires an isolated background factory process"
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
OUT = Path(os.environ["ANGELO_QA_DIR"]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_DISABLE_KEYCHAIN="1")
RECEIPT = OUT / "checkpoint-content-binding.json"
from angelo_agent import checkpoint_capture as capture_api  # noqa: E402
from angelo_agent import checkpoint_seal  # noqa: E402
from angelo_agent.checkpoint_capture import CaptureOwner, capture_checkpoint  # noqa: E402
from angelo_agent.checkpoint_publication import CheckpointPublicationMixin  # noqa: E402
from angelo_agent.checkpoints import CheckpointStore  # noqa: E402
from content_binding_fixtures import Rewrite, SaveEvents, read_all, rewrite_keeping_stamp, sculpt_target  # noqa: E402

NATIVE = _bpy._angelo_checkpoint
CHANGED = "native checkpoint output changed"
REPORT = {"success": False, "binary": bpy.app.binary_path, "gtest_seams": hasattr(NATIVE, "_test_skip_next_sculpt_gate")
          and hasattr(_bpy._angelo_undo, "_test_fail_next_checkpoint_install"),
          "results": {}}


class Fixture(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.temp = tempfile.TemporaryDirectory(prefix="binding-", dir=OUT)
        self.store = CheckpointStore(Path(self.temp.name).resolve() / "profile", str(uuid.uuid4()))
        self.project = Path(self.temp.name) / "project.blend"
        bpy.ops.mesh.primitive_cube_add()
        bpy.ops.wm.save_as_mainfile(filepath=str(self.project))
        self.open = []

    def tearDown(self):
        for capsule, owner in reversed(self.open):
            for action in (lambda: NATIVE.capture_abort(capsule, owner), lambda: NATIVE.capture_close(capsule),
                           lambda: NATIVE.capture_release(capsule), owner.abandon):
                try:
                    action()
                except Exception:
                    pass
        self.temp.cleanup()

    def begin(self, purpose="request"):
        owner = CaptureOwner.reserve(self.store, bpy.data.filepath, purpose=purpose)
        capsule = NATIVE.capture(owner)
        self.open.append((capsule, owner))
        return capsule, owner

    def descriptor(self, owner, role):
        return owner._resource.outputs[role]._resource.descriptor

    def identities(self):
        folder = Path(self.store.directory)
        return {path.name for path in folder.iterdir() if path.is_dir()} if folder.is_dir() else set()

    def refused_capture(self, rewrite, **kwargs):
        before = self.identities()
        with SaveEvents() as saves, rewrite.patch(), self.assertRaisesRegex(ValueError, CHANGED):
            capture_checkpoint(self.store, bpy.data.filepath, **kwargs)
        self.assertTrue(rewrite.done, "the rewrite ran")
        self.assertEqual(saves.events, ["fail"], "SAVE_POST_FAIL, never SAVE_POST")
        new = self.identities() - before
        self.assertEqual(len(new), 1)
        self.assertNotEqual(self.store.load(new.pop()).get("state"), "READY", "nothing READY")


class RewriteKeepingTheStamp(Fixture):
    def test_object_mode_safety_capture_is_refused(self):
        self.refused_capture(Rewrite("BASE"), purpose="safety", retain_native=True)

    def test_sculpt_pair_base_is_refused(self):
        sculpt_target()
        self.refused_capture(Rewrite("BASE"))

    def test_sculpt_pair_runtime_is_refused(self):
        sculpt_target()
        self.refused_capture(Rewrite("RUNTIME"))

    def test_sculpt_safety_pair_is_refused(self):
        sculpt_target()
        self.refused_capture(Rewrite("RUNTIME"), purpose="safety", retain_native=True)

    def test_untouched_object_mode_safety_is_content_sealed(self):
        record, capsule = capture_checkpoint(self.store, bpy.data.filepath, purpose="safety", retain_native=True)
        try:
            self.assertEqual((record["version"], checkpoint_seal.binding(record)), (3, "content"))
        finally:
            NATIVE.capture_release(capsule)


class RestoreFixture(Fixture):
    """The Restore operator's own safety capture, taken from Object mode (owner_count 0)."""

    def setUp(self):
        super().setUp()
        from angelo_agent import ui
        self.ui = ui
        ui._authentication.start = lambda *args, **kwargs: None
        addon_utils.enable("angelo_agent", default_set=False, persistent=False)
        self.controller = ui.get_controller()

    def target(self, version):
        if version == 4:
            sculpt_target()
        record = self.ui.capture_checkpoint(self.controller)
        self.assertEqual(record["version"], version)
        self.controller.state["messages"].append({"role": "user", "text": "target", "checkpoint": record["id"],
                                                  "checkpoint_scope": "before_request"})
        if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.mesh.primitive_cube_add()
        bpy.context.object.name = "AfterCheckpoint"
        return record

    def restore(self, identity):
        try:
            return sorted(bpy.ops.angelo.restore_checkpoint(checkpoint_id=identity))
        except RuntimeError as error:   # A background operator's ERROR report raises.
            return ["RAISED", str(error)]


class RestoreFromObjectMode(RestoreFixture):
    def refused_restore(self, version):
        record = self.target(version)
        path, native_restores = bpy.data.filepath, []
        original_restore = NATIVE.restore

        def spy(*args):
            native_restores.append(1)
            return original_restore(*args)
        rewrite = Rewrite("BASE", purpose="safety")
        with rewrite.patch(), patch.object(NATIVE, "restore", spy):
            outcome = self.restore(record["id"])
        self.assertTrue(rewrite.done, "the safety capture's BASE was rewritten")
        self.assertNotEqual(outcome, ["FINISHED"])
        self.assertEqual(native_restores, [], "Restore never ran")
        self.assertIn("AfterCheckpoint", bpy.data.objects, "the document is unchanged")
        self.assertEqual(bpy.data.filepath, path)

    def test_a_rewritten_safety_refuses_restore_to_a_version_3_target(self):
        self.refused_restore(3)

    def test_a_rewritten_safety_refuses_restore_to_a_version_4_target(self):
        self.refused_restore(4)

    def test_an_untouched_safety_is_content_sealed_and_restores(self):
        for version in (3, 4):
            with self.subTest(version=version):
                record = self.target(version)
                self.assertEqual(self.restore(record["id"]), ["FINISHED"])
                notice = next(m for m in reversed(self.controller.state["messages"])
                              if m.get("restore_source_checkpoint") == record["id"])
                # FINISHED also covers a refused Restore whose safety reloaded: report its reason.
                self.assertEqual(notice.get("restore_state"), "complete", notice.get("restore_reason"))
                self.assertNotIn("AfterCheckpoint", bpy.data.objects)
                safety = self.controller.checkpoints.load(notice["restore_checkpoint"])
                self.assertEqual((safety["version"], checkpoint_seal.binding(safety)), (3, "content"))
                self.controller = self.ui.get_controller()
                self.controller.state["messages"].append({"role": "user", "text": "safety", "checkpoint": safety["id"],
                                                          "checkpoint_scope": "before_request"})
                self.assertEqual(self.restore(safety["id"]), ["FINISHED"], "the content-sealed safety reloads")
                self.assertIn("AfterCheckpoint", bpy.data.objects)
                bpy.data.objects.remove(bpy.data.objects["AfterCheckpoint"])
                self.controller = self.ui.get_controller()


class FailedRestoreReload(RestoreFixture):
    """A Restore that fails after replacing the document reloads its Object-mode safety capture (version 3, content-
    sealed): ``recovery_copy`` hashes its bytes against the record (``copy_sealed``), so one rewritten after the
    bind, its stamp kept, is refused there and never loaded. Needs the WITH_GTESTS install-failure seam."""

    def setUp(self):
        if not hasattr(_bpy._angelo_undo, "_test_fail_next_checkpoint_install"):
            self.skipTest("needs a WITH_GTESTS build (_angelo_undo._test_fail_next_checkpoint_install)")
        super().setUp()

    def failed_restore(self, rewrite_safety):
        record = self.target(4)
        original_restore = NATIVE.restore

        def failing(*args):
            notice = next(m for m in reversed(self.controller.state["messages"]) if m.get("restore_checkpoint"))
            safety = self.controller.checkpoints.folder(notice["restore_checkpoint"]) / "scene.blend"
            self.assertEqual(checkpoint_seal.binding(self.controller.checkpoints.load(notice["restore_checkpoint"])),
                             "content")
            if rewrite_safety:
                fd = os.open(safety, os.O_RDWR | getattr(os, "O_BINARY", 0))
                try:
                    rewrite_keeping_stamp(fd)
                finally:
                    os.close(fd)
            _bpy._angelo_undo._test_fail_next_checkpoint_install()
            return original_restore(*args)
        with patch.object(NATIVE, "restore", failing):
            self.restore(record["id"])
        return next(m for m in reversed(self.controller.state["messages"])
                    if m.get("restore_source_checkpoint") == record["id"])

    def test_the_content_sealed_safety_reloads_after_a_failed_restore(self):
        notice = self.failed_restore(False)
        self.assertEqual((notice.get("restore_state"), notice.get("scene_reloaded")), ("not_applied", True))
        self.assertIn("AfterCheckpoint", bpy.data.objects, "the pre-Restore document is back")

    def test_a_safety_rewritten_keeping_its_stamp_is_refused_by_the_reload(self):
        notice = self.failed_restore(True)
        self.assertNotEqual(notice.get("scene_reloaded"), True, "the rewritten safety bytes were never loaded")
        self.assertNotEqual(notice.get("restore_state"), "complete")


class NativeAdmission(Fixture):
    @staticmethod
    def unbound(owner, capture, resource, roles, facts):
        """``_bind_content`` with the native comparison skipped (a regression): hashes, never binds."""
        return {role: capture_api._hash_descriptor(resource.outputs[role]._resource.descriptor,
                                                   facts["scene_bytes" if role == "BASE" else "runtime_bytes"])
                for role in roles}

    def post_refused_without_bind(self, purpose):
        capsule, owner = self.begin(purpose)   # Never published, never bound: native's gate comes first.
        with SaveEvents() as saves, self.assertRaisesRegex(ValueError, "not bound to its published content"):
            NATIVE.capture_post(capsule, owner)
        self.assertEqual(saves.events, ["fail"])
        self.assertEqual(NATIVE.capture_facts(capsule, owner)["post_state"], "failed")

    def test_post_is_refused_for_a_pair_whose_bind_was_skipped(self):
        sculpt_target()
        self.post_refused_without_bind("request")

    def test_post_is_refused_for_a_safety_capture_whose_bind_was_skipped(self):
        self.post_refused_without_bind("safety")

    def skipped_bind_never_valid(self, purpose):
        """A Python regression that skips capture_bind: nothing becomes READY and valid, nothing is restorable."""
        before = self.identities()
        # Refused at publish, or (before the owner's flag) by native's POST and then finish: one error or a group.
        with patch.object(CheckpointPublicationMixin, "_bind_content", NativeAdmission.unbound), \
                self.assertRaises((ValueError, ExceptionGroup)):
            capture_checkpoint(self.store, bpy.data.filepath, purpose=purpose,
                               retain_native=purpose == "safety")
        (identity,) = self.identities() - before
        record = self.store.load(identity)
        self.assertFalse(record.get("state") == "READY" and record.get("capture_validation", "VALIDATED")
                         in {"VALIDATED", "INTERRUPTED_VALID"}, "never a valid READY record: %r" % (
                             {key: record.get(key) for key in ("state", "capture_validation", "version")},))
        self.assertFalse(self.store.availability(identity)["available"], "never listed as restorable")

    def test_a_pair_whose_bind_was_skipped_is_never_valid_or_restorable(self):
        sculpt_target()
        self.skipped_bind_never_valid("request")

    def test_a_safety_capture_whose_bind_was_skipped_is_never_valid_or_restorable(self):
        self.skipped_bind_never_valid("safety")

    def test_hasher_is_native_owned_and_exposes_no_digest(self):
        capsule, owner = self.begin("safety")
        hasher = NATIVE.capture_hasher(capsule, owner, "BASE")
        with self.assertRaises(TypeError):
            type(hasher)()
        self.assertEqual([name for name in dir(hasher) if not name.startswith("__")], ["update"])
        self.assertIsNone(hasher.update(b""))
        with self.assertRaises(ValueError):
            NATIVE.capture_hasher(capsule, owner, "RUNTIME")   # No RUNTIME: nothing bound there.
        with self.assertRaises(ValueError):
            NATIVE.capture_hasher(capsule, owner, "OTHER")

    def test_a_partial_read_is_refused_and_bind_is_attempted_once(self):
        capsule, owner = self.begin("safety")
        data = read_all(self.descriptor(owner, "BASE"))
        partial = NATIVE.capture_hasher(capsule, owner, "BASE")
        partial.update(data[:-1])
        with self.assertRaisesRegex(ValueError, CHANGED):
            NATIVE.capture_bind(capsule, owner, partial)
        with self.assertRaisesRegex(ValueError, "already compared"):
            partial.update(data[-1:])
        with self.assertRaises(ValueError):
            NATIVE.capture_hasher(capsule, owner, "BASE")   # The binding was attempted: nothing pending.

    def test_a_correct_bind_is_spent_and_cannot_repeat(self):
        capsule, owner = self.begin("safety")
        hasher = NATIVE.capture_hasher(capsule, owner, "BASE")
        hasher.update(read_all(self.descriptor(owner, "BASE")))
        self.assertIsNone(NATIVE.capture_bind(capsule, owner, hasher))
        with self.assertRaisesRegex(ValueError, "already compared"):
            hasher.update(b"x")
        with self.assertRaisesRegex(ValueError, "already attempted"):
            NATIVE.capture_bind(capsule, owner, hasher)

    def test_a_hasher_of_the_wrong_role_is_refused(self):
        sculpt_target()
        capsule, owner = self.begin()
        hashers = {}
        for role in ("BASE", "RUNTIME"):
            hashers[role] = NATIVE.capture_hasher(capsule, owner, role)
            hashers[role].update(read_all(self.descriptor(owner, role)))
        with self.assertRaisesRegex(ValueError, CHANGED):
            NATIVE.capture_bind(capsule, owner, hashers["RUNTIME"], hashers["BASE"])

    def test_a_hasher_of_another_capture_is_refused(self):
        first, first_owner = self.begin("safety")
        foreign = NATIVE.capture_hasher(first, first_owner, "BASE")
        foreign.update(read_all(self.descriptor(first_owner, "BASE")))
        NATIVE.capture_abort(first, first_owner)
        NATIVE.capture_close(first)
        NATIVE.capture_release(first)
        capsule, owner = self.begin("safety")
        with self.assertRaisesRegex(ValueError, CHANGED):
            NATIVE.capture_bind(capsule, owner, foreign)

    def test_capture_facts_and_the_module_expose_no_binding(self):
        capsule, owner = self.begin("safety")
        facts = NATIVE.capture_facts(capsule, owner)
        self.assertEqual(set(facts), {"capture_id", "scene_bytes", "runtime_bytes", "owner_count", "format_version",
                                      "post_state"})
        public = sorted(name for name in dir(NATIVE) if not name.startswith("_"))
        self.assertEqual(public, sorted([
            "adopt_restored_document", "begin_restore", "capture", "capture_abort", "capture_bind", "capture_close",
            "capture_facts", "capture_hasher", "capture_never_packs", "capture_post", "capture_release",
            "clear_failed_restore", "close_restore", "failed_restore_safety", "is_recovery_copy", "prepare_restore",
            "quiesce_jobs", "reload_failed_restore", "restore", "restore_handler_errors", "restored_save_pending",
            "set_state_root",
            # 7db16e3e860's Restore memory admission: a capsule, byte counts and outcome names, or None; no binding.
            "restore_admission", "restore_admission_state", "restore_admission_begin_target",
            "restore_admission_end_target"]), "a new public entry must not return the binding: pin it here")

    def test_an_ordinary_request_on_this_volume_binds_nothing(self):
        capsule, owner = self.begin("request")
        self.assertFalse(owner._resource.bind_required)
        with self.assertRaisesRegex(ValueError, "no pending content binding"):
            NATIVE.capture_hasher(capsule, owner, "BASE")

    def test_a_fine_prediction_contradicted_by_a_coarse_stamp_is_refused(self):
        answers = iter([False])   # The prediction, from the empty BASE; every later stamp reads as coarse.
        with patch.object(checkpoint_seal, "coarse_timestamps", lambda *_: next(answers, True)), \
                SaveEvents() as saves, self.assertRaisesRegex(ValueError, "coarse clock but not content-bound"):
            capture_checkpoint(self.store, bpy.data.filepath)
        self.assertEqual(saves.events, ["fail"])

    def test_sculpt_owners_without_a_base_binding_are_refused(self):
        if not REPORT["gtest_seams"]:
            self.skipTest("needs a WITH_GTESTS build (_test_skip_next_sculpt_gate)")
        sculpt_target()
        NATIVE._test_skip_next_sculpt_gate()
        with self.assertRaisesRegex(RuntimeError, "Sculpt checkpoint owner has no BASE content binding"):
            capture_checkpoint(self.store, bpy.data.filepath)


def main():
    ui = None
    try:
        from angelo_agent import ui
        ui._authentication.start = lambda *args, **kwargs: None
    except Exception:
        traceback.print_exc()
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    for case, error in result.failures + result.errors:
        REPORT["results"][case.id()] = "FAILED: " + error.splitlines()[-1]
    for case, reason in result.skipped:
        REPORT["results"][case.id()] = "SKIPPED: " + reason
    REPORT["ran"], REPORT["skipped"] = result.testsRun, len(result.skipped)
    REPORT["success"] = result.wasSuccessful()
    return result.wasSuccessful()


try:
    passed = main()
finally:
    RECEIPT.write_text(json.dumps(REPORT, indent=1, default=str))
    print("ANGELO_CHECKPOINT_CONTENT_BINDING", json.dumps({"success": REPORT["success"], "ran": REPORT.get("ran"),
          "skipped": REPORT.get("skipped"), "gtest_seams": REPORT["gtest_seams"]}), flush=True)
if not passed:
    sys.exit(1)
