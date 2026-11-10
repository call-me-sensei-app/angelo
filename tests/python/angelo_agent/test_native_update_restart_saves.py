# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Restart to Update keeps what Blender's own quit keeps, and says what Windows still has to finish (update_restart).

* Asset catalogs with unsaved changes ask the save question as Blender's quit dialog does; Save and Restart writes
  them with the file (Blender's "Save modified asset catalogs" on for that save, whatever the dialog last left, then
  put back), a catalog still unsaved stops the restart, and Don't Save discards them.
* An install Windows finishes only at its next restart (msiexec 3010) is a success, told once at the next launch.

On the stand-in bpy of test_native_app_notices, with the native catalog checks stood in for; the real ones are read by
``test_blender_work_guard_jobs``.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_native_update_ready as ready  # noqa: E402
from angelo_agent import update_install, update_state  # noqa: E402


class Catalogs:
    """``_angelo_jobs``'s catalog checks: ``unsaved`` until a save runs with ``save_with_file`` on (or always)."""

    def __init__(self, unsaved=True, writable=True):
        self.unsaved, self.writable, self.save_with_file, self.seen_at_save = unsaved, writable, False, []

    def catalogs_unsaved(self):
        return self.unsaved

    def catalogs_save_with_file(self, enable=None):
        before = self.save_with_file
        if enable is not None:
            self.save_with_file = bool(enable)
        return before

    def saved(self):
        """The file's save_post: writes the catalogs when the setting says so (asset_library.cc)."""
        self.seen_at_save.append(self.save_with_file)
        if self.save_with_file and self.writable:
            self.unsaved = False


class CatalogRestartTests(ready.RestartCase):
    def native(self, catalogs):
        patch = mock.patch.object(self.restart, "_native_jobs", lambda: catalogs)
        patch.start()
        self.addCleanup(patch.stop)

    def saving(self, catalogs, answer=None):
        saves = []

        def save(*args, **kwargs):
            saves.append(1)
            catalogs.saved()
            return answer or {'FINISHED'}
        self.bpy.ops.wm.save_mainfile = save
        return saves

    def test_unsaved_catalogs_alone_ask_and_save_and_restart_writes_them(self):
        self.bpy.data.filepath = "/work/castle.blend"   # The file itself reads as saved.
        catalogs = Catalogs()
        self.native(catalogs)
        saves = self.saving(catalogs)
        self.press()
        self.assertEqual(self.started, [], "never quits over unsaved catalogs without asking")
        prompt = self.shown("update_save")
        self.assertTrue(prompt.catalogs)
        self.assertFalse(prompt.file_changed, "the file's own line is not said for a saved file")
        prompt.act("save", None, self.host)
        self.assertEqual((saves, catalogs.seen_at_save, catalogs.unsaved), ([1], [True], False))
        self.assertFalse(catalogs.save_with_file, "the quit dialog's setting is put back")
        self.assertEqual(len(self.started), 1)

    def test_a_catalog_that_cannot_be_written_stops_the_restart(self):
        self.bpy.data.is_dirty, self.bpy.data.filepath = True, "/work/castle.blend"
        catalogs = Catalogs(writable=False)
        self.native(catalogs)
        self.saving(catalogs)
        self.press()
        self.shown("update_save").act("save", None, self.host)
        self.assertEqual(self.started, [])
        notice = self.queued("notice")[-1]
        self.assertEqual(notice.title, "Angelo did not restart")
        self.assertIn("asset catalogs could not be saved", notice.body)

    def test_a_failed_save_puts_the_setting_back(self):
        self.bpy.data.is_dirty, self.bpy.data.filepath = True, "/work/castle.blend"
        catalogs = Catalogs()
        catalogs.save_with_file = False
        self.native(catalogs)

        def refuse(*args, **kwargs):
            raise RuntimeError("Cannot write to a read-only folder")
        self.bpy.ops.wm.save_mainfile = refuse
        self.press()
        self.shown("update_save").act("save", None, self.host)
        self.assertEqual((self.started, catalogs.save_with_file), ([], False))

    def test_dont_save_discards_them_as_blenders_quit_does(self):
        self.bpy.data.filepath = "/work/castle.blend"
        catalogs = Catalogs()
        self.native(catalogs)
        saves = self.saving(catalogs)
        self.press()
        self.shown("update_save").act("dont_save", None, self.host)
        self.assertEqual((saves, len(self.started)), ([], 1))

    def test_an_untitled_file_keeps_the_setting_on_until_its_save_ends(self):
        self.bpy.data.is_dirty = True
        catalogs = Catalogs()
        self.native(catalogs)
        self.press()
        self.shown("update_save").act("save", None, self.host)
        self.assertTrue(catalogs.save_with_file, "the File Browser's save writes them")
        self.bpy.data.filepath = "/work/new.blend"
        catalogs.saved()
        for handler in list(self.bpy.app.handlers.save_post):
            handler("/work/new.blend", None)
        self.bpy.data.is_dirty = False
        self.run_timers()
        self.assertEqual((catalogs.seen_at_save, catalogs.save_with_file), ([True], False))
        self.assertEqual(self.started[0].open_file, "/work/new.blend")

    def test_a_build_without_the_native_check_asks_as_before(self):
        self.bpy.data.filepath = "/work/castle.blend"
        self.native(None)
        self.press()
        self.assertEqual((self.queued("update_save"), len(self.started)), ([], 1))


class RebootResultTests(ready.ReadyCase):
    def install(self, result):
        current = self.restart.app_version.current()
        work = self.preparer.root / current
        work.mkdir(parents=True)
        (work / update_install.RESULT_FILE).write_text("status=installed\n")
        self.preparer.result_list = [(current, result)]
        with mock.patch.object(self.restart.update_flow, "remove_previous"):
            self.restart.handle_results(self.preparer, str(self.folder))
        return work

    def test_an_install_windows_finishes_at_its_restart_is_told_once(self):
        # The script records reboot=required; one older than that, installing this version, records only the code.
        for result in ({"status": "installed", "step": "msiexec", "code": "3010", "reboot": "required"},
                       {"status": "installed", "step": "msiexec", "code": "3010"}):
            with self.subTest(result=result):
                self.n.card_host._queue.clear()
                work = self.install(result)
                titles = [card.title for card in self.queued("notice")]
                self.assertEqual(len(titles), 1)
                self.assertRegex(titles[0], r"^Angelo .+ is installed$")
                self.assertIn("Restart Windows", self.queued("notice")[0].body)
                self.assertFalse((work / update_install.RESULT_FILE).exists(), "told once")
                self.assertEqual(update_state.load(self.folder).install_failures, 0, "a success, never a failure")
                __import__("shutil").rmtree(work)

    def test_a_plain_install_says_nothing(self):
        self.install({"status": "installed", "step": "msiexec", "code": "0"})
        self.assertEqual(self.queued(), [])

    def test_the_result_reads_the_reboot_line(self):
        work = self.preparer.root / "1.1.0"
        work.mkdir(parents=True)
        (work / update_install.RESULT_FILE).write_text(
            "status=installed\nstep=msiexec\ncode=3010\nversion=1.1.0\nreboot=required\n")
        result = update_install.read_result(work)
        self.assertEqual(result["reboot"], "required")
        self.assertTrue(update_install.needs_reboot(result))
        self.assertFalse(update_install.needs_reboot({"status": "failed", "step": "msiexec", "code": "3010"}))
        self.assertFalse(update_install.needs_reboot({"status": "installed", "step": "swap"}))


class WindowsScriptRebootTests(unittest.TestCase):
    def test_the_script_records_3010_as_installed_with_a_reboot(self):
        text = (update_install.HELPER_FOLDER / "install_update_windows.cmd").read_text(encoding="ascii")
        self.assertIn('if "%code%"=="3010" (set "status=installed" & set "reboot=required")', text)
        self.assertLess(text.index('set "reboot="'), text.index(":wait"), "never inherited from the environment")
        finish = text[text.index(":finish"):text.index('move /y "%dir%\\result.tmp"')]
        self.assertIn("if defined reboot echo reboot=%reboot%", finish)


if __name__ == "__main__":
    unittest.main()
