# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bringing earlier settings into Angelo (``settings_import``, ``blender_folders``), without Blender.

Checked on all three folder layouts (macOS, Windows, Linux; ``platform`` is always pinned): where stock Blender's
folder is, which source wins (an earlier Angelo version, then Blender's same version, then an older one in Blender's
own window, else none; ``BLENDER_USER_*`` folders shared), what is copied and what is left out, and that copying is
copy-only: Blender's tree is byte-identical (contents, sizes, modes, times and links) after an import and after its
undo, and the undo puts Angelo's own tree back exactly. Also: links into the copied folder point at Angelo's copy,
recent files and bookmarks merge, a failed file never stops the rest, an undo touches nothing outside Angelo's
folders, Angelo's add-on is never unloaded while preferences are read, and the confirmation card (any size, both
modes) and the result notice say what they must. The running app is ``test_blender_settings_import``.
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from test_native_notice_cards import FakeView  # noqa: E402
from angelo_agent import blender_folders, card_kit as kit, settings_import as si, settings_import_card as card  # noqa: E402

PLATFORMS = ("darwin", "win32", "linux")


def tree_digest(root, folder_times=True):
    """Every entry under ``root``: path, type, mode, size, modification time, content hash or link target.

    ``folder_times=False`` (Angelo's folder after an undo) leaves out folders' modification times, which adding and
    removing a file changes, and compares files' times to the microsecond, the precision a copy keeps on macOS."""
    found = {}
    for folder, names, files in os.walk(root):
        for name in sorted(names + files):
            path = Path(folder) / name
            info = os.lstat(path)
            if os.path.islink(path):
                found[str(path.relative_to(root))] = ("link", os.readlink(path))
            elif path.is_dir():
                found[str(path.relative_to(root))] = ("dir", info.st_mode) + ((info.st_mtime_ns,) if folder_times else ())
            else:
                mtime = info.st_mtime_ns if folder_times else info.st_mtime_ns // 1000
                found[str(path.relative_to(root))] = ("file", info.st_mode, info.st_size, mtime,
                                                     hashlib.sha256(path.read_bytes()).hexdigest())
    return found


def write(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class Sandbox:
    """A temporary home with a platform's layout; ``blender(version)`` makes a stock Blender folder."""

    def __init__(self, platform):
        self.platform = platform
        self._dir = tempfile.TemporaryDirectory()
        self.home = Path(self._dir.name) / "home"
        self.home.mkdir()
        self.environ = {}
        if platform == "win32":
            self.environ["APPDATA"] = str(self.home / "AppData/Roaming")

    def blender(self, version="5.2", content=True):
        root = blender_folders.version_folder(self.platform, self.home, self.environ, version)
        root.mkdir(parents=True)
        if content:
            write(root / "config/userpref.blend", "prefs " + version)
        return root

    def angelo(self):
        root = Path(self._dir.name) / "angelo" / "5.2"
        places = {part: root / part for part in blender_folders.PARTS}
        places[si.ROOT] = root
        return root, places

    def find(self, angelo_previous=None):
        return si.find_source(self.platform, self.home, self.environ, (5, 2), angelo_previous)

    def close(self):
        self._dir.cleanup()


class FolderTests(unittest.TestCase):
    def test_blenders_folder_on_each_platform(self):
        home = Path("/h")
        self.assertEqual(blender_folders.version_folder("darwin", home, {}, (5, 2)),
                         home / "Library/Application Support/Blender/5.2")
        self.assertEqual(blender_folders.version_folder("win32", home, {"APPDATA": "/r"}, "5.2"),
                         Path("/r/Blender Foundation/Blender/5.2"))
        self.assertEqual(blender_folders.version_folder("win32", home, {}, "5.2"),
                         home / "AppData/Roaming/Blender Foundation/Blender/5.2")
        self.assertEqual(blender_folders.version_folder("linux", home, {}, (4, 5)), home / ".config/blender/4.5")
        self.assertEqual(blender_folders.version_folder("linux", home, {"XDG_CONFIG_HOME": "/x"}, (5, 2)),
                         Path("/x/blender/5.2"))

    def test_the_version_window_is_blenders_own(self):
        found = blender_folders.versions((5, 2))
        self.assertEqual(found[:3], [(5, 2), (5, 1), (5, 0)])
        self.assertEqual(found[3:], [(4, minor) for minor in range(10, -1, -1)])

    def test_the_migration_command_reads_the_same_names(self):
        source = (ROOT / "scripts/angelo/migrate_user_folder.py").read_text(encoding="utf-8")
        self.assertIn("blender_folders.version_folder", source)
        self.assertNotIn('"Blender Foundation"', source, "Blender's folder is named in one place")


class SourceTests(unittest.TestCase):
    def each(self):
        for platform in PLATFORMS:
            with self.subTest(platform=platform):
                box = Sandbox(platform)
                try:
                    yield box
                finally:
                    box.close()

    def test_an_earlier_angelo_wins(self):
        for box in self.each():
            box.blender("5.2")
            source, reason = box.find(angelo_previous=(5, 1))
            self.assertEqual((source.app, source.version, reason), (si.ANGELO, (5, 1), si.FOUND))

    def test_blenders_same_version_first(self):
        for box in self.each():
            box.blender("5.1")
            root = box.blender("5.2")
            source, reason = box.find()
            self.assertEqual((source.app, source.version_label, source.root, reason), (si.BLENDER, "5.2", root, si.FOUND))

    def test_an_older_blender_in_blenders_window(self):
        for box in self.each():
            box.blender("4.5")
            box.blender("5.3")   # Newer than Angelo's own Blender: never brought back.
            source, _ = box.find()
            self.assertEqual(source.version_label, "4.5")

    def test_nothing_to_bring(self):
        for box in self.each():
            self.assertEqual(box.find(), (None, si.NONE))
            box.blender("3.6")   # Outside the window, as for Blender's own import.
            empty = box.blender("5.2", content=False)
            write(empty / "config/angelo-agent/sessions/a.json")   # Only what is left out.
            write(empty / "config/startup.blend")
            self.assertEqual(box.find(), (None, si.NONE))

    def test_a_shared_folder_has_nothing_to_bring(self):
        for box in self.each():
            box.blender("5.2")
            for variable in ("BLENDER_USER_RESOURCES", "BLENDER_USER_CONFIG"):
                self.assertEqual(si.find_source(box.platform, box.home, {variable: "/s"}, (5, 2)), (None, si.SHARED))

    def test_a_shared_part_is_skipped(self):
        for box in self.each():
            root = box.blender("5.2")
            write(root / "scripts/addons/tool.py")
            write(root / "datafiles/studiolights/matcap/m.png")
            box.environ["BLENDER_USER_SCRIPTS"] = "/shared/scripts"
            source, _ = box.find()
            self.assertNotIn("scripts", source.parts)
            _, places = box.angelo()
            targets = {item.target for item in si.plan(source, places)}
            self.assertIn(places["datafiles"] / "studiolights/matcap/m.png", targets)
            self.assertFalse(any("scripts" in target.parts for target in targets))


class CopyTests(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox("darwin")
        self.blender = root = self.box.blender("5.2")
        write(root / "config/bookmarks.txt", "[Bookmarks]\n/b/projects/\n[Recent]\n/b/recent/\n")
        write(root / "config/recent-files.txt", "/b/one.blend\n/b/shared.blend\n")
        write(root / "config/startup.blend", "their default scene")
        write(root / "config/angelo-agent/sessions/early.json", "{}")
        write(root / "config/switcher_tool/settings.json", "{}")
        write(root / "scripts/addons/switcher_tool/__init__.py", "bl_info = {}")
        write(root / "scripts/presets/operator/mesh.primitive_cube_add/Big.py", "op.size = 3")
        write(root / "scripts/startup/bl_app_templates_user/tpl/__init__.py")
        write(root / "scripts/startup/hello.py")
        write(root / "datafiles/studiolights/matcap/m.png")
        write(root / "extensions/user_default/mcp/blender_manifest.toml")
        write(root / "extensions/.user/user_default/mcp/server.json")
        write(root / "extensions/.local/lib/python3.13/site-packages/wheel.py")
        os.symlink("../addons/switcher_tool", root / "scripts/presets/linked_addon")   # Inside the copied folder.
        os.symlink("/opt/dev/addon", root / "scripts/addons/dev_addon")                # Outside it: kept.
        self.angelo, self.places = self.box.angelo()
        write(self.angelo / "config/userpref.blend", "angelo's own")
        write(self.angelo / "config/recent-files.txt", "/a/mine.blend\n/b/shared.blend\n")
        write(self.angelo / "config/angelo-agent/sessions/chat.json", "{}")
        self.backup = self.angelo / "settings-import-backup"
        self.source, _ = self.box.find()

    def tearDown(self):
        self.box.close()

    def run_import(self):
        items = si.plan(self.source, self.places)
        return items, si.copy(items, self.backup, self.source, self.places)

    def test_blenders_folder_is_byte_identical_after_import_and_undo(self):
        before = tree_digest(self.blender)
        _, result = self.run_import()
        self.assertEqual(result.errors, [])
        self.assertEqual(tree_digest(self.blender), before, "The import only reads Blender's folder")
        si.undo(self.backup, self.places)
        self.assertEqual(tree_digest(self.blender), before, "So does its undo")

    def test_everything_is_brought_except_what_is_left_out(self):
        items, result = self.run_import()
        a = self.angelo
        self.assertEqual(result.copied, len(items))
        self.assertEqual((a / "config/userpref.blend").read_text(), "prefs 5.2")
        for relative in ("config/switcher_tool/settings.json", "scripts/addons/switcher_tool/__init__.py",
                         "scripts/presets/operator/mesh.primitive_cube_add/Big.py",
                         "scripts/startup/bl_app_templates_user/tpl/__init__.py", "scripts/startup/hello.py",
                         "datafiles/studiolights/matcap/m.png", "extensions/user_default/mcp/blender_manifest.toml",
                         "extensions/.user/user_default/mcp/server.json",
                         "extensions/.local/lib/python3.13/site-packages/wheel.py"):
            self.assertTrue((a / relative).is_file(), relative)
        self.assertFalse((a / "config/startup.blend").exists(), "Blender's start-up file is left out")
        self.assertFalse((a / "config/angelo-agent/sessions/early.json").exists(), "So is an early Angelo's state")
        self.assertTrue((a / "config/angelo-agent/sessions/chat.json").is_file(), "Angelo's own state is untouched")
        self.assertEqual(os.readlink(a / "scripts/presets/linked_addon"), str(a / "scripts/addons/switcher_tool"),
                         "A link into Blender's folder points at Angelo's copy")
        self.assertEqual(os.readlink(a / "scripts/addons/dev_addon"), "/opt/dev/addon")
        summary = si.summary(items, self.places)
        self.assertEqual({key: summary[key] for key in ("preferences", "addons", "extensions", "presets", "templates",
                                                        "startup_scripts", "lights", "restart")},
                         {"preferences": True, "addons": 2, "extensions": 1, "presets": 2, "templates": 1,
                          "startup_scripts": 1, "lights": 1, "restart": True})

    def test_recent_files_and_bookmarks_merge_after_angelos_own(self):
        self.run_import()
        self.assertEqual((self.angelo / "config/recent-files.txt").read_text(),
                         "/a/mine.blend\n/b/shared.blend\n/b/one.blend\n")
        self.assertEqual((self.angelo / "config/bookmarks.txt").read_text(),
                         "[Bookmarks]\n/b/projects/\n[Recent]\n/b/recent/\n", "Angelo had none: Blender's, as is")
        self.assertEqual(si.merge_lists("[Bookmarks]\n/a/\n[Recent]\n", "[Bookmarks]\n/b/\n/a/\n[Recent]\n/r/\n"),
                         "[Bookmarks]\n/a/\n/b/\n[Recent]\n/r/\n")

    def test_undo_puts_angelos_folder_back_exactly(self):
        before = tree_digest(self.angelo, folder_times=False)
        self.run_import()
        self.assertNotEqual(tree_digest(self.angelo, folder_times=False), before)
        result = si.undo(self.backup, self.places)
        self.assertEqual(result.errors, [])
        self.assertEqual(tree_digest(self.angelo, folder_times=False), before)
        self.assertFalse(self.backup.exists(), "A finished undo removes its backup")

    def test_a_file_that_cannot_be_copied_never_stops_the_rest(self):
        (self.angelo / "scripts/startup/hello.py").mkdir(parents=True)   # A folder in the way.
        items, result = self.run_import()
        self.assertEqual([Path(path).name for path, _ in result.errors], ["hello.py"])
        self.assertEqual(result.copied, len(items) - 1)
        self.assertTrue((self.angelo / "datafiles/studiolights/matcap/m.png").is_file())

    def test_the_manifest_is_written_before_copying_and_bound_to_this_folder(self):
        self.run_import()
        manifest = json.loads((self.backup / si.MANIFEST).read_text())
        self.assertEqual(manifest["source_version"], "5.2")
        self.assertIn(str(self.angelo / "config/userpref.blend"), manifest["replaced"])
        self.assertIsNotNone(si.backup_matches(self.backup, self.places))
        other = dict(self.places, config=self.angelo.parent / "5.3/config")
        self.assertIsNone(si.backup_matches(self.backup, other), "Another version's folder has no undo")
        self.assertEqual(si.undo(self.backup, other).copied, 0)

    def test_undo_never_touches_anything_outside_angelos_folders(self):
        self.run_import()
        outside = write(self.box.home / "elsewhere.txt", "keep")
        manifest = json.loads((self.backup / si.MANIFEST).read_text())
        manifest["added"].append(str(outside))
        (self.backup / si.MANIFEST).write_text(json.dumps(manifest))
        si.undo(self.backup, self.places)
        self.assertEqual(outside.read_text(), "keep")

    def test_a_part_already_shared_by_a_link_is_skipped(self):
        self.places["scripts"].parent.mkdir(parents=True, exist_ok=True)
        os.symlink(self.blender / "scripts", self.places["scripts"])
        items = si.plan(self.source, self.places)
        self.assertFalse(any(item.target.is_relative_to(self.places["scripts"]) for item in items))


class KeepEnabledTests(unittest.TestCase):
    """``angelo_stays_enabled`` through Blender's preference read (``disable_all`` then ``reset_all``)."""

    def test_the_core_addon_is_never_unloaded_and_stays_listed(self):
        events = []

        class Module:
            def __init__(self, name):
                self.__name__, self.__addon_enabled__ = name, True

        class Addons(list):
            def new(self):
                entry = type("Entry", (), {"module": ""})()
                self.append(entry)
                return entry

            def __contains__(self, name):
                return any(entry.module == name for entry in self)

        prefs = type("Prefs", (), {})()
        modules = {"core_addon": Module("core_addon"), "old_tool": Module("old_tool")}   # core: angelo_agent

        def disable_all():
            for module in modules.values():
                if module.__addon_enabled__:
                    events.append(("disable", module.__name__))
                    module.__addon_enabled__ = False

        def reset_all(**_options):
            for name, module in modules.items():
                if (name in prefs.addons) != module.__addon_enabled__:
                    events.append(("enable" if name in prefs.addons else "disable", name))
                    module.__addon_enabled__ = name in prefs.addons

        addon_utils = type(sys)("addon_utils")
        addon_utils.disable_all, addon_utils.reset_all = disable_all, reset_all
        bpy = type(sys)("bpy")
        bpy.context = type("Context", (), {"preferences": prefs})()
        bpy.app = type("App", (), {"handlers": type("Handlers", (), {"exit_pre": [], "persistent": staticmethod(
            lambda function: function)})()})()   # prefs_reload's quit handler (test_native_prefs_reload)
        with mock.patch.dict(sys.modules, dict(modules, bpy=bpy, addon_utils=addon_utils)):
            sys.modules.pop("angelo_agent.settings_import_apply", None)
            from angelo_agent import settings_import_apply as apply
            prefs.addons = Addons()
            with apply.angelo_stays_enabled(("core_addon",)):
                addon_utils.disable_all()
                prefs.addons = Addons()   # Blender's preferences: no core add-on.
                modules["new_tool"] = Module("new_tool")
                modules["new_tool"].__addon_enabled__ = False
                prefs.addons.new().module = "new_tool"
                addon_utils.reset_all()
            self.assertIs(addon_utils.disable_all, disable_all, "Blender's own functions come back")
            sys.modules.pop("angelo_agent.settings_import_apply", None)
        self.assertNotIn(("disable", "core_addon"), events)
        self.assertIn(("disable", "old_tool"), events)
        self.assertIn(("enable", "new_tool"), events)
        self.assertTrue(modules["core_addon"].__addon_enabled__)
        self.assertIn("core_addon", prefs.addons)
        self.assertEqual(apply.CORE_ADDONS, ("angelo_agent",))
        events.clear()
        with mock.patch.dict(sys.modules, dict(modules, bpy=bpy, addon_utils=addon_utils)):
            from angelo_agent import settings_import_apply as apply
            with apply.angelo_stays_enabled(("core_addon",), keep_loaded=False):   # An earlier Angelo's import.
                addon_utils.disable_all()
                prefs.addons = Addons()
                addon_utils.reset_all()
            sys.modules.pop("angelo_agent.settings_import_apply", None)
        self.assertEqual([e for e in events if e[1] == "core_addon"], [("disable", "core_addon"), ("enable", "core_addon")],
                         "It reloads (with the copied agent state) and is listed again")


class Host:
    closed = None

    def close(self, how="done"):
        self.closed = how

    def redraw(self):
        pass


class CardTests(unittest.TestCase):
    def make(self, mode, decided):
        folder = Path(os.path.expanduser("~")) / "Library/Application Support/Blender/5.2"
        return card.ImportCard(mode, si.BLENDER, "5.2", folder if mode == "import" else None, decided.append)

    def test_fits_any_region_with_its_controls_inside(self):
        for mode in ("import", "undo"):
            for w, h, scale in ((1300, 1200, 1.0), (520, 640, 1.0), (1300, 1200, 2.0), (420, 480, 1.0)):
                with self.subTest(mode=mode, size=(w, h, scale)):
                    plan = self.make(mode, []).layout(FakeView(), w, h, scale, kit.UiState())
                    x, y, cw, ch = plan.card
                    self.assertTrue(x >= 0 and x + cw <= w and y >= 0, plan.card)
                    for rect, action, value in plan.hits:
                        self.assertTrue(kit.inside(plan.card, rect[0] + 1, rect[1] + 1), action)
                    self.assertEqual({a for _, a, _ in plan.hits}, {"confirm", "cancel"})

    def test_says_where_from_what_it_replaces_and_the_backup(self):
        plan = self.make("import", []).layout(FakeView(), 1300, 1200, 1.0, kit.UiState())
        drawn = " ".join(op[1].strip() for op in plan.ops if op[0] in {"text", "mono"})
        self.assertIn("From Blender 5.2", drawn)
        self.assertIn("~/Library/Application Support/Blender/5.2", drawn)
        for words in ("Replaces", "Quick Favorites", "Keeps", "Angelo only reads it"):
            self.assertIn(words, drawn)
        notice = next(op for op in plan.ops if op[0] == "kit" and op[1] == "notice")
        self.assertEqual(notice[3]["title"], "Angelo backs up its current settings first")

    def test_only_the_primary_button_acts(self):
        for action, expected in (("cancel", "cancel"), ("close", "cancel"), ("confirm", "confirm")):
            decided, host = [], Host()
            item = self.make("import", decided)
            item.act(action, None, host)
            item.closed(host.closed)
            self.assertEqual(decided, [expected])
        decided = []
        self.make("undo", decided).closed("lost")
        self.assertEqual(decided, ["cancel"], "A card that disappears decides nothing")

    def test_the_notice_is_calm_and_complete(self):
        outcome = {"app": si.BLENDER, "version": "5.2", "failed": [], "errors": [], "restart": False}
        icon, title, body, calm = card.notice_text(outcome)
        self.assertEqual((icon, title, calm), ("check", "Your Blender 5.2 settings are in", True))
        icon, _, body, calm = card.notice_text(dict(outcome, failed=["Broken Tool"], restart=True))
        self.assertEqual((icon, calm), ("alert", False))
        self.assertIn("Did not load: Broken Tool.", body)
        self.assertIn("next time you open Angelo", body)
        self.assertEqual(card.notice_text(dict(outcome, exception="boom"))[1], "Your settings could not be brought over")
        self.assertEqual(card.short_path("/h/x/Blender/5.2", "/h"), "~/x/Blender/5.2")
        self.assertEqual(card.short_path("/other/5.2", "/h"), "/other/5.2")


if __name__ == "__main__":
    unittest.main()
