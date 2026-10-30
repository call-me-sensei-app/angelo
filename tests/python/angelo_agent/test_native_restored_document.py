# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""After Restore and Undo restore, the document open at the Restore is the document a person sees and saves.

Evidence (1.0.6 GUI dogfood, finding C7): after a restore the window showed ``recovered-<uuid>.blend``, Save wrote
into Angelo's hidden state folder and the quit prompt offered to save that copy, while the person's ``stool.blend``
was left behind. The maintainer's decision: the window keeps the person's file, the first Save over it asks once,
the pre-restore safety snapshot stays, the hidden copy is never a save target. A break-it review then found the
document taken from the snapshot's record (wrong after a Save As), a refused adoption left as a saved-looking private
copy and a safety reload's refusal only printed. Here: the add-on's half (what it reads before the load, what it asks
the native step, how a refusal is reported, the update card's Save) and the native contract's source.
``test_blender_restore_keeps_file`` proves the native behaviour in the compiled app.
"""

from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_native_checkpoint_restore_ui as restore_case  # noqa: E402
from angelo_agent import checkpoint_restore_ui as flow, restored_document  # noqa: E402
from angelo_agent.checkpoint_review_ui import _SCOPES  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
WM_FILES = ROOT / "source/blender/windowmanager/intern/wm_files.cc"


class NativeStepTests(unittest.TestCase):
    def test_begin_and_adopt_take_no_path_from_anywhere(self):
        """The native step remembers the open document itself; adopt takes no argument a record could fill."""
        native = SimpleNamespace(begin_restore=Mock(), adopt_restored_document=Mock(return_value=True),
                                 is_recovery_copy=Mock(return_value=True))
        with patch.dict(sys.modules, {"_bpy": SimpleNamespace(_angelo_checkpoint=native)}):
            restored_document.begin()
            self.assertTrue(restored_document.adopt())
            self.assertTrue(restored_document.is_recovery_copy("/state/checkpoints/s/recoveries/recovered-x.blend"))
        native.begin_restore.assert_called_once_with()
        native.adopt_restored_document.assert_called_once_with()
        import inspect
        self.assertEqual(list(inspect.signature(restored_document.adopt).parameters), [])

    def test_without_the_native_step_nothing_happens(self):
        with patch.dict(sys.modules, {"_bpy": SimpleNamespace()}):
            restored_document.begin()
            self.assertIsNone(restored_document.adopt())
            self.assertFalse(restored_document.is_recovery_copy("/x.blend"))


class RestoreFlowTests(unittest.TestCase):
    setUp_flow, open_scene = restore_case.CheckpointRestoreUITests.setUp, restore_case.CheckpointRestoreUITests.open_scene

    def setUp(self):
        self.setUp_flow()
        self.begin.side_effect = lambda *_root: self.timeline.append(("begin",))

    def test_restore_remembers_the_open_document_before_the_load_and_adopts_it(self):
        self.assertEqual(flow.execute_restore(self.ui, self.operator), {"FINISHED"})
        self.assertLess(self.timeline.index(("begin",)), self.timeline.index(("open",)))
        self.adopt.assert_called_once_with()
        self.assertEqual(self.active.state["messages"][-1]["restore_state"], "complete")

    def test_no_value_from_the_snapshot_record_reaches_adoption(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/checkpoint_restore_ui.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("restored_document.adopt_or_say("), 2, "Restore and the safety reload")
        self.assertNotIn("record['original_path']", source)
        helper = (ROOT / "scripts/addons_core/angelo_agent/restored_document.py").read_text(encoding="utf-8")
        self.assertIn("        adopt()\n", helper, "adopt_or_say passes no argument: nothing a record holds")

    def test_undo_restore_adopts_the_open_document_too(self):
        self.record.update(id="safety-before", purpose="safety")
        self.active.state["messages"].append({"role": "system", "restore_checkpoint": "selected", "restore_state": "complete"})
        self.assertEqual(flow.execute_restore(self.ui, self.operator), {"FINISHED"})
        self.assertLess(self.timeline.index(("begin",)), self.timeline.index(("open",)))
        self.adopt.assert_called_once_with()

    def test_a_refused_adoption_is_an_incomplete_restore_never_a_silent_one(self):
        self.adopt.side_effect = ValueError("The open document is not a Restore's working copy")
        self.assertEqual(flow.execute_restore(self.ui, self.operator), {"FINISHED"})
        notice = self.active.state["messages"][-1]
        self.assertEqual(notice["restore_state"], "partial")
        self.assertIn("open as a recovery copy, not your file", notice["text"])
        self.assertIs(notice["recovery_copy_open"], True)
        from angelo_agent import activity_text
        self.assertEqual(activity_text.notice_view(notice)["headline"], "Your scene is open as a recovery copy")
        self.assertEqual(self.operator.report.call_args.args[0], {"WARNING"})

    def test_a_complete_restore_is_not_a_recovery_copy(self):
        self.assertEqual(flow.execute_restore(self.ui, self.operator), {"FINISHED"})
        self.assertIs(self.active.state["messages"][-1]["recovery_copy_open"], False)


class SafetyReloadTests(unittest.TestCase):
    setUp_flow, open_scene = restore_case.CheckpointRestoreUITests.setUp, restore_case.CheckpointRestoreUITests.open_scene
    bundle = restore_case.CheckpointRestoreUITests.bundle
    failed_after_replacement = restore_case.CheckpointRestoreUITests.failed_after_replacement

    def setUp(self):
        self.setUp_flow()

    def test_a_reloads_refused_adoption_reaches_the_notice(self):
        owner, native = self.bundle()

        def reload(prepared):
            self.ui.bpy.data.filepath = '/private/safety-working.blend'
            return True
        recovery, _safety_owner = self.failed_after_replacement(owner, native, reload)
        self.adopt.side_effect = ValueError("The open document is not a Restore's working copy")
        with recovery, patch.dict(sys.modules, {'_bpy': SimpleNamespace(_angelo_checkpoint=native)}):
            self.assertEqual(flow.execute_restore(self.ui, self.operator), {'FINISHED'})
        notice = self.active.state['messages'][-1]
        self.assertEqual(notice['restore_state'], 'not_applied')
        self.assertIn("open as a recovery copy, not your file", notice['text'])
        self.assertIs(notice['recovery_copy_open'], True)


class UpdateRestartTests(unittest.TestCase):
    """The update card's Save and Restart goes through the question, as File > Save does; it never writes unasked."""

    def setUp(self):
        from notices_fakes import Notices   # update_restart is Blender-side: imported against stand-in modules.
        notices = Notices()
        self.notices = notices.__enter__()
        self.restart = self.notices.update_restart
        self.addCleanup(notices.__exit__, None, None, None)
        self.restart._watch = None
        self.addCleanup(setattr, self.restart, "_watch", None)

    def save(self, pending, copy=False):
        self.notices.bpy.data.filepath = "/work/stool.blend"
        launched = []
        native = SimpleNamespace(_angelo_checkpoint=SimpleNamespace(restored_save_pending=lambda: pending,
                                                                    is_recovery_copy=lambda path: copy))
        with patch.dict(sys.modules, {"_bpy": native}), \
                patch.object(self.restart, "launch", side_effect=lambda *args: launched.append(args)), \
                patch.object(self.restart, "_save_images", return_value=[]):
            self.restart._after_prompt("save", "offer", "ready")
        saves = [(name, args) for name, args, _kwargs in self.notices.record.operators if name.startswith("wm.save")]
        return saves, launched

    def test_a_pending_question_is_asked_and_the_restart_waits(self):
        saves, launched = self.save(pending=True)
        self.assertEqual(saves, [("wm.save_mainfile", ("INVOKE_DEFAULT",))])
        self.assertEqual(launched, [], "the restart waits for the save")
        self.assertTrue(self.restart._watch["asking"])

    def test_a_recovery_copy_says_why_and_never_saves(self):
        notices = []
        with patch.object(self.restart, "_notice", side_effect=lambda *args: notices.append(args)):
            saves, launched = self.save(pending=False, copy=True)
        self.assertEqual((saves, launched), ([], []))
        self.assertEqual(notices, [(self.restart.SAVE_FAILED_TITLE, self.restart.RECOVERY_COPY_BODY)])

    def test_only_a_save_of_the_document_to_its_own_path_continues_the_restart(self):
        """A snapshot capture or a Save Copy elsewhere runs save_post too; neither is the document saved."""
        import os
        import tempfile
        launched = []
        with tempfile.TemporaryDirectory() as directory:
            document, other = os.path.join(directory, "stool.blend"), os.path.join(directory, "copy.blend")
            for path in (document, other):
                open(path, "wb").close()
            self.notices.bpy.data.filepath, self.notices.bpy.data.is_dirty = document, False
            state = os.path.join(directory, "state")
            pending = [True]
            native = SimpleNamespace(_angelo_checkpoint=SimpleNamespace(restored_save_pending=lambda: pending[0]))
            ui = SimpleNamespace(state_directory=lambda: state)
            with patch.dict(sys.modules, {"_bpy": native, "angelo_agent.ui": ui}), \
                    patch.object(self.restart, "launch", side_effect=lambda *args: launched.append(args)), \
                    patch.object(self.restart, "_dirty_images", return_value=[]):
                self.restart._watch = {"offer": "o", "ready": "r", "saved_path": None, "failed": False,
                                       "opened": 0.0 + __import__("time").monotonic(), "asking": True}
                self.restart._on_saved(os.path.join(state, "checkpoints", "x", "scene.blend"))   # A capture.
                self.restart._on_saved(other)                                                # A Save Copy.
                self.assertEqual(self.restart._watch_save(), 0.3)
                self.restart._on_saved(document)   # The document, but its question is still open: not saved.
                self.assertEqual(self.restart._watch_save(), 0.3)
                pending[0] = False
                self.assertIsNone(self.restart._watch_save())
        self.assertEqual(len(launched), 1)

    def test_without_a_question_the_file_is_saved_and_the_restart_goes_on(self):
        saves, launched = self.save(pending=False)
        self.assertEqual(saves, [("wm.save_mainfile", ())])
        self.assertEqual(len(launched), 1)


class ContractTests(unittest.TestCase):
    def test_the_persons_document_path_is_bpy_data_filepath_everywhere(self):
        addon = ROOT / "scripts/addons_core/angelo_agent"
        for path in addon.rglob("*.py"):
            self.assertNotIn("_recovery_original_path", path.read_text(encoding="utf-8"), path.name)
        ui = (addon / "ui.py").read_text(encoding="utf-8")
        self.assertIn("original = bpy.data.filepath", ui)

    def test_the_restore_notice_no_longer_sends_the_person_to_save_as(self):
        for scope, (_question, _note, _restored, done) in _SCOPES.items():
            with self.subTest(scope=scope):
                self.assertNotIn("Save As", done)
                self.assertIn("Nothing replaces your saved file until you save and confirm it.", done)

    def test_the_native_contract_is_in_place(self):
        source = WM_FILES.read_text(encoding="utf-8")
        def body(signature):
            text = source[source.index(signature):]
            return text[:text.index("\n}\n")]
        adopt = body("bool WM_angelo_adopt_restored_document(")
        for fact in ("WM_angelo_recovery_copy(bmain->filepath)", "!S_ISREG(info.st_mode)", "wm->file_saved = 0",
                     "&wm_checkpoint_read_unsaved", "wm_angelo_question_open(document)",
                     "wm_angelo_restore_from.compress", "STRNCPY(G.filepath_last_blend, document)"):
            self.assertIn(fact, adopt)
        # Any name a document had (stool.blend1, no extension): no extension check, and no path argument at all.
        self.assertNotIn("BKE_blendfile_extension_check", adopt)
        self.assertIn("bool WM_angelo_adopt_restored_document(bContext *C, ReportList *reports)", source)
        # The document's path before the relative rewrite: library paths resolve against it, not the working copy.
        self.assertLess(adopt.index("STRNCPY(bmain->filepath, document)"), adopt.index("BKE_bpath_foreach_path_main"))
        self.assertIn("BLI_path_contains(checkpoints, path)", body("bool WM_angelo_recovery_copy("))
        self.assertNotIn("BKE_BPATH_FOREACH_PATH_SKIP_PACKED", body("void WM_angelo_restore_begin("))
        # Aliases are the same file: identity first (POSIX st_dev/st_ino, the Windows file ID).
        question = body("static bool wm_angelo_question_file(")
        self.assertLess(question.index("WM_angelo_file_identity(target)"), question.index("BLI_path_normalize"))
        self.assertIn("WM_angelo_same_file_name(BLI_path_basename(target)", question)   # As the file system compares.
        self.assertNotIn("BLI_strcasecmp", question)
        identity_source = (ROOT / "source/blender/windowmanager/intern/wm_angelo_file_identity.cc").read_text(
            encoding="utf-8")
        # The 128-bit Windows file ID first (ReFS), then the 64-bit index; a zero serial or index is unknown.
        self.assertLess(identity_source.index("FileIdInfo"), identity_source.index("GetFileInformationByHandle(handle"))
        for fact in ("id_info.VolumeSerialNumber != 0 && (low | high) != 0", "info.dwVolumeSerialNumber != 0 && index != 0",
                     "CompareStringOrdinal(a_16, -1, b_16, -1, TRUE)", "kCFCompareCaseInsensitive | kCFCompareNonliteral",
                     "info.st_dev", "info.st_ino"):
            self.assertIn(fact, identity_source)
        # Every path-based .blend write passes one admission, libraries.write included.
        writefile = (ROOT / "source/blender/blenloader/intern/writefile.cc").read_text(encoding="utf-8")
        self.assertIn("if (blo_write_admit && !blo_write_admit(filepath, reports)) {", writefile)
        self.assertIn("BLO_write_admit_set(wm_angelo_write_admit);", body("static void wm_angelo_question_open("))
        # A recovery file's note belongs to that exact file; another file's note is ignored and removed.
        note = body("static void wm_angelo_recovery_note_read(")
        self.assertIn("wm_angelo_recovery_note_stamp(recovery_filepath)", note)
        self.assertIn("BLI_delete(note.c_str(), false, false);", note)
        # Only a successful read that replaced the document ends the question; a preferences read never does.
        self.assertNotIn("wm_angelo_restored_unconfirmed", body("static void wm_file_read_pre(bool use_data"))
        self.assertIn("if (params->use_data && params->success) {\n    /* Angelo",
                      body("static void wm_file_read_post("))
        history = body("static void wm_history_file_update()\n{")
        self.assertIn("WM_angelo_recovery_copy(blendfile_path)", history)
        admit = body("static bool wm_angelo_save_admit(")
        self.assertIn("op->customdata == &wm_angelo_confirmed_save || wm_angelo_dialog_save", admit)
        self.assertIn('RPT_("Save the restored scene from File > Save first")', admit)
        self.assertIn("wm_angelo_dialog_save = true;", body("static void wm_block_file_close_save("))
        self.assertIn("wm_angelo_dialog_save = true;", body("static void save_file_overwrite_confirm("))
        for recovery in ("bool WM_file_recover_last_session(", "static wmOperatorStatus wm_recover_auto_save_exec("):
            self.assertIn("wm_angelo_recovery_note_read(CTX_data_main(C), filepath)", body(recovery))
        self.assertIn("WM_angelo_recovery_note_write(bmain, filepath)", body("bool WM_autosave_write("))
        self.assertIn("WM_angelo_recovery_note_write(bmain, filepath)",
                      (ROOT / "source/blender/windowmanager/intern/wm_init_exit.cc").read_text(encoding="utf-8"))
        invoke = source[source.index("static wmOperatorStatus wm_save_mainfile_invoke("):]
        invoke = invoke[:invoke.index("\n}\n")]
        self.assertIn("wm_angelo_restored_save_unconfirmed(bmain, target)", invoke)
        self.assertIn("op->customdata = &wm_angelo_confirmed_save;", invoke)
        self.assertIn("wm_angelo_display_path(blendfile_path)", invoke)
        self.assertIn('IFACE_("Save the restored scene over \\"{}\\"?")', invoke)
        self.assertIn('IFACE_("The snapshot from before the restore stays available.")', invoke)
        exec_ = body("static wmOperatorStatus wm_save_as_mainfile_exec(")
        self.assertIn("wm_angelo_restored_unconfirmed.clear()", exec_)
        self.assertLess(exec_.index("wm_angelo_save_admit(bmain, op, filepath)"), exec_.index("wm_file_write("))
        self.assertLess(exec_.index("wm_angelo_admitted_write = filepath;"), exec_.index("wm_file_write("))

    def test_the_confirmation_is_interface_text(self):
        from importlib import import_module
        sys.path.insert(0, str(ROOT / "scripts/angelo/i18n"))
        sources = import_module("sources")
        messages = {message for _context, message, _origin in sources.EXTRA_MSGIDS}
        for message in ('Save the restored scene over "{}"?', "The snapshot from before the restore stays available.",
                        "Save the restored scene from File > Save first",
                        "This scene is a recovery copy, not your file: save it with File > Save As",
                        "Recovered scene (not your file)", "Recovered scene.blend"):
            self.assertIn(message, messages)
        self.assertTrue(re.search(r'msgid "Save the restored scene over \\"\{\}\\"\?"',
                                  (ROOT / "locale/angelo/angelo.pot").read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
