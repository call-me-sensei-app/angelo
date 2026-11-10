# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every engine-link adapter keeps the adapter contract, without Blender (doc/angelo/engine_link.rst, "Adapters").

Covered: an engine with no adapter has none; each listed adapter names its engine and display name, declares what
its editors show by default, refuses a request with every shared refusal code it needs in its own English, admits
only its kinds, exports live scenes that never change the frame, says how a save writes each kind, and ships the
plugin it installs; the plugin installs into one of its projects only, without the files the engine regenerates.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import engine_link_assets as assets, user_copies  # noqa: E402

SHARED_REFUSALS = ("not_project", "outside", "kind", "missing")
EXPORTERS = ("gltf", "fbx")


class RegistryTests(unittest.TestCase):
    def test_an_engine_without_an_adapter_has_none(self):
        for engine in ("", "nothing", None, 3):
            self.assertIsNone(assets.adapter(engine))
        self.assertEqual(assets.display_name("nothing"), "nothing")

    def test_godot_is_listed(self):
        self.assertEqual(assets.adapter("godot").DISPLAY, "Godot")


class AdapterContractTests(unittest.TestCase):
    def each(self):
        for engine in sorted(assets.ADAPTERS):
            with self.subTest(engine=engine):
                yield engine, assets.adapter(engine)

    def test_identity_and_capabilities(self):
        for engine, adapter in self.each():
            self.assertEqual(adapter.ENGINE, engine)
            self.assertTrue(adapter.DISPLAY)
            self.assertIn("durable", adapter.CAPABILITIES, "every editor re-imports what a save writes")
            self.assertLessEqual(set(adapter.CAPABILITIES), {"durable", "live"})

    def test_refusals_are_english_templates_for_every_shared_code(self):
        for _engine, adapter in self.each():
            for code in SHARED_REFUSALS:
                self.assertIn(code, adapter.REFUSED)
                self.assertNotIn("{", adapter.REFUSED[code].replace("{asset}", ""))

    def test_a_request_outside_any_project_is_refused(self):
        folder = tempfile.mkdtemp()
        for _engine, adapter in self.each():
            self.assertFalse(adapter.is_project(folder))
            with self.assertRaises(assets.AssetError) as caught:
                adapter.resolve({"type": "open", "project": folder, "asset": "x", "source": "x"})
            self.assertIn(caught.exception.code, adapter.REFUSED, "a shared code or the adapter's own")
            caught.exception.english()

    def test_live_exports_never_change_the_frame(self):
        for _engine, adapter in self.each():
            options = adapter.live_options({})
            self.assertEqual((options["export_animations"], options["export_current_frame"]), (False, True))

    def test_saving_says_how_each_kind_is_written(self):
        for engine, adapter in self.each():
            for kind in adapter.KINDS:
                asset = assets.Asset(engine, "/p", "a" + kind, "/p/a" + kind, kind, "a" + kind)
                durable = adapter.durable(asset, {})
                if kind == ".blend":
                    self.assertIsNone(durable, "Angelo's save is the asset")
                else:
                    exporter, options = durable
                    self.assertIn(exporter, EXPORTERS)
                    self.assertIsInstance(options, dict)

    def test_each_ships_its_plugin(self):
        for engine, adapter in self.each():
            if adapter.PLUGIN_SOURCE is None:   # The web's plugin is an npm package the developer installs.
                self.assertEqual(engine, "web")
                self.assertTrue(adapter.install_hint())
                continue
            self.assertTrue(adapter.PLUGIN_SOURCE.is_dir(), adapter.PLUGIN_SOURCE)
            self.assertTrue(adapter.PLUGIN_FOLDER)


class WorkingPathTests(unittest.TestCase):
    def test_the_working_file_mirrors_the_assets_project_path(self):
        asset = assets.Asset("any", "/p", "id", "/p/Art/crate.fbx", ".fbx", "Art/crate.fbx")
        self.assertEqual(asset.editable, str(Path("/p", ".angelo", "Art", "crate.fbx.blend")))   # A native path.
        self.assertTrue(assets.previous_path(asset).endswith("crate.fbx.previous.blend"))


class InstallTests(unittest.TestCase):
    def test_a_plugin_installs_only_into_its_engines_projects(self):
        for engine in sorted(assets.ADAPTERS):
            adapter = assets.adapter(engine)
            if adapter.PLUGIN_SOURCE is None:
                continue   # Nothing to install (test_each_ships_its_plugin).
            with self.subTest(engine=engine):
                with self.assertRaises(assets.AssetError) as caught:
                    assets.install_plugin(adapter, tempfile.mkdtemp())
                self.assertEqual(caught.exception.code, "not_project")

    def test_a_read_only_install_still_installs_and_replaces_every_plugin(self):
        """An app whose bundled scripts the person can't write (1.0.8 Mac slot: "Permission denied"): the copy kept the
        bundle's read-only modes, and a read-only folder can't be moved to a new parent on macOS or Linux."""
        if getattr(os, "geteuid", lambda: 1)() == 0:
            self.skipTest("[environment] root ignores permissions")
        for engine in sorted(assets.ADAPTERS):
            adapter = assets.adapter(engine)
            if adapter.PLUGIN_SOURCE is None:
                continue   # Nothing to install (test_each_ships_its_plugin).
            with self.subTest(engine=engine), tempfile.TemporaryDirectory() as root:
                bundle = Path(root) / "bundle" / Path(adapter.PLUGIN_SOURCE).name
                shutil.copytree(adapter.PLUGIN_SOURCE, bundle, ignore=shutil.ignore_patterns(*adapter.PLUGIN_IGNORE))
                read_only(bundle)
                project = Path(root) / "project"
                project.mkdir()
                try:
                    with mock.patch.object(adapter, "PLUGIN_SOURCE", bundle), \
                            mock.patch.object(adapter, "is_project", lambda folder: True):
                        target = Path(assets.install_plugin(adapter, str(project)))
                        read_only(target)   # An older copy installed read-only: replaced all the same.
                        self.assertEqual(Path(assets.install_plugin(adapter, str(project))), target)
                finally:
                    user_copies.own(Path(root))   # So the folder can be removed, whatever the outcome.
                self.assertEqual(sorted(p.relative_to(target) for p in target.rglob("*")),
                                 sorted(p.relative_to(bundle) for p in bundle.rglob("*")))
                for path in [target, *target.rglob("*")]:
                    self.assertTrue(os.access(path, os.W_OK), "the person can't change " + str(path))
                self.assertEqual([p.name for p in target.parent.iterdir() if p.name.startswith(".")], [],
                                 "the staging folder was left behind")


def read_only(folder):
    """Strip every write bit under ``folder`` (and its own), as an install the person can't write has them."""
    for path in [Path(folder), *Path(folder).rglob("*")]:
        if not path.is_symlink():
            os.chmod(path, os.lstat(path).st_mode & 0o7555)

if __name__ == "__main__":
    unittest.main()
