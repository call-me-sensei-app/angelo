# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Render engine add-ons are lead-only: the background Angelo processes Angelo starts never load one
(``angelo_lead_only_addons``; doc/angelo/render_engines.rst, "Background processes"). No Blender.

Covered: an engine add-on is told without importing it (a legacy add-on's ``bl_info`` category, an extension's
manifest tags), never one that ships with Angelo; in Angelo's children Blender's ``addon_utils.enable`` holds one back
before any of its code runs, and lets every other add-on through; the window and a person's own background run are
unchanged; the startup module installs it only where it applies. Engine names are stand-ins.
"""

import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/modules"))
import angelo_lead_only_addons as lead  # noqa: E402

ENGINE = 'bl_info = {"name": "Lux", "category": "Render"}\nraise SystemExit("ran")\n'
TOOL = 'bl_info = {"name": "Tidy", "category": "Object"}\n'


class Folders(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.user, self.shipped, self.repo = self.root / "user", self.root / "shipped" / "addons_core", self.root / "repo"
        for folder, name, text in ((self.user, "lux", ENGINE), (self.user, "tidy", TOOL),
                                   (self.shipped, "cycles", ENGINE)):
            (folder / name).mkdir(parents=True)
            (folder / name / "__init__.py").write_text(text, encoding="utf-8")
        (self.user / "single.py").write_text(ENGINE, encoding="utf-8")
        for name, tags in (("luxcore", '["Render", "Lighting"]'), ("helper", '["Mesh"]')):
            (self.repo / name).mkdir(parents=True)
            (self.repo / name / "blender_manifest.toml").write_text('id = "%s"\ntags = %s\n' % (name, tags))
        self.paths = [str(self.shipped), str(self.user)]
        self.repos = {"user_default": (str(self.repo), False), "system": (str(self.repo), True)}
        self.roots = [self.root / "shipped"]

    def engine(self, module):
        return lead.is_engine_addon(module, self.paths, self.repos, self.roots)


class DetectionTests(Folders):
    def test_a_persons_engine_add_on_is_told_without_running_it(self):
        self.assertTrue(self.engine("lux"))
        self.assertTrue(self.engine("single"))
        self.assertFalse(self.engine("tidy"), "an Object tool is not held back")
        self.assertFalse(self.engine("missing"))

    def test_add_ons_and_extensions_that_ship_with_angelo_are_never_held(self):
        self.assertFalse(self.engine("cycles"))
        self.assertFalse(self.engine("bl_ext.system.luxcore"), "a system repository ships with the app")

    def test_an_extension_is_an_engine_by_its_manifest_tags(self):
        self.assertTrue(self.engine("bl_ext.user_default.luxcore"))
        self.assertFalse(self.engine("bl_ext.user_default.helper"))
        self.assertFalse(self.engine("bl_ext.unknown_repo.luxcore"))


class HoldTests(Folders):
    def install(self):
        calls = []
        fake = types.ModuleType("addon_utils")
        fake.enable = lambda module, *args, **kwargs: calls.append(module) or types.ModuleType(module)
        patcher = mock.patch.dict(sys.modules, {"addon_utils": fake})
        patcher.start()
        self.addCleanup(patcher.stop)
        context = mock.patch.object(lead, "_context", lambda: (self.paths, self.repos, self.roots))
        context.start()
        self.addCleanup(context.stop)
        lead._held.clear()
        self.addCleanup(lead._held.clear)
        lead.install()
        return fake, calls

    def test_an_engine_add_on_is_held_back_before_any_of_its_code_runs(self):
        fake, calls = self.install()
        self.assertIsNone(fake.enable("lux", default_set=True))
        self.assertEqual(calls, [], "never imported: its SystemExit never ran")
        self.assertEqual(lead.held(), ["lux"])

    def test_every_other_add_on_loads_as_before(self):
        fake, calls = self.install()
        self.assertIsNotNone(fake.enable("tidy"))
        self.assertIsNotNone(fake.enable("cycles"))
        self.assertEqual(calls, ["tidy", "cycles"])

    def test_installing_twice_wraps_once_and_a_failed_check_lets_the_add_on_load(self):
        fake, calls = self.install()
        wrapped = fake.enable
        lead.install()
        self.assertIs(fake.enable, wrapped)
        with mock.patch.object(lead, "is_engine_addon", side_effect=OSError("unreadable")):
            fake.enable("lux")
        self.assertEqual(calls, ["lux"])


class WhereTests(unittest.TestCase):
    def test_only_angelos_own_background_children(self):
        child = {"ANGELO_HEADLESS": "1"}
        bpy = types.ModuleType("bpy")
        bpy.app = SimpleNamespace(background=True)
        with mock.patch.dict(sys.modules, {"bpy": bpy}):
            self.assertTrue(lead.applies(child))
            self.assertFalse(lead.applies({}), "a person's own --background run")
            self.assertFalse(lead.applies(child, background=False), "Angelo's window")

    def test_the_startup_module_installs_it_before_add_ons_load(self):
        source = (ROOT / "scripts/startup/angelo_lead_only.py").read_text(encoding="utf-8")
        self.assertIn("if angelo_lead_only_addons.applies():", source)
        self.assertIn("angelo_lead_only_addons.install()", source)
        loader = (ROOT / "scripts/modules/bpy/utils/__init__.py").read_text(encoding="utf-8")
        self.assertLess(loader.index("def load_scripts("), loader.index("def load_scripts_extensions("),
                        "Blender registers startup modules, then enables add-ons")


if __name__ == "__main__":
    unittest.main()
