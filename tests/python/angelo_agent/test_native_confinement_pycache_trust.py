# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which part of Python's bytecode cache counts as installed code (``script_confinement_blender.app_folders``,
break-it-confine-reparse SHOULD-5).

The whole cache (``sys.pycache_prefix``) stays one a script can't change. But only its mirrors of the installed
folders are trusted: there, an installed module's cached bytecode is installed code, accepted byte for byte as
beside its source. The cache also keeps bytecode for any other source a process imported (a document's folder, a
script's), and that is the source's, never installed code. Both sides are checked on the file names importlib
itself gives (``cache_from_source``): an installed module's cache file is inside a trusted folder, and a
non-installed module's is in none, nor is the prefix itself. The C confinement and the Python mirror both take the
trusted list from here, so a host test of the list covers both. The import-time behaviour (bytes accepted or refused
at the unmarshal, with and without a prefix) is in confinement_loader_cases, run inside Blender.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
import host_test_policy  # noqa: E402
import angelo_agent  # noqa: E402
from angelo_agent import script_confinement_blender as blender  # noqa: E402


def inside(path, folders):
    """Whether ``path`` is one of ``folders`` or below one, by path components (not by text)."""
    path = os.path.normcase(os.path.abspath(path))
    for folder in folders:
        folder = os.path.normcase(os.path.abspath(folder))
        try:
            if os.path.commonpath([path, folder]) == folder:
                return True
        except ValueError:   # Different drives.
            continue
    return False


class PycacheTrustTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="pycache-trust-")).resolve()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.work, ignore_errors=True))
        self.installed = self.work / "app" / "scripts"          # Blender's own scripts folder (LOCAL).
        self.user = self.work / "config" / "scripts"            # The user's add-ons (user SCRIPTS).
        self.document = self.work / "projects" / "shot"         # Not installed: a document's folder.
        for folder in (self.installed, self.user, self.document):
            folder.mkdir(parents=True)

    def folders(self, prefix):
        """app_folders on stand-ins of bpy and the ui module, with ``prefix`` as the bytecode cache."""
        paths = {"LOCAL": str(self.installed), "SYSTEM": "", "SCRIPTS": str(self.user)}
        bpy = SimpleNamespace(
            utils=SimpleNamespace(resource_path=lambda kind: paths.get(kind, ""),
                                  user_resource=lambda kind: paths.get(kind, str(self.work / "config" / kind))),
            context=SimpleNamespace(preferences=SimpleNamespace(filepaths=SimpleNamespace(script_directories=[]))))
        ui = SimpleNamespace(connections=lambda: SimpleNamespace(records=lambda: []),
                             state_directory=lambda: str(self.work / "state"),
                             connection_directory=lambda: str(self.work / "connections"))
        with patch.object(sys, "pycache_prefix", prefix), patch.dict(sys.modules, {"angelo_agent.ui": ui}), \
                patch.object(angelo_agent, "ui", ui, create=True):
            private, write_only, families, trusted = blender.app_folders(bpy)
            cached = {name: importlib.util.cache_from_source(str(folder / "pkg" / "mod.py"))
                      for name, folder in (("installed", self.installed), ("user", self.user),
                                           ("document", self.document))}
        return write_only, trusted, cached

    def test_only_the_installed_folders_mirrors_of_the_cache_are_trusted(self):
        prefix = str(self.work / "cache" / "pycache" / "build")
        write_only, trusted, cached = self.folders(prefix)
        self.assertIn(prefix, write_only, "a script can't change any of the cache")
        self.assertNotIn(prefix, trusted, "the whole cache is not installed code")
        for name in ("installed", "user"):
            with self.subTest(source=name):
                self.assertTrue(cached[name].startswith(prefix), cached[name])
                self.assertTrue(inside(cached[name], trusted),
                                "an installed module's cached bytecode stays installed: %s" % cached[name])
        self.assertTrue(cached["document"].startswith(prefix))
        self.assertFalse(inside(cached["document"], trusted),
                         "a non-installed module's cached bytecode is not installed: %s" % cached["document"])
        self.assertTrue(inside(cached["document"], write_only), "but a script still can't change it")

    def test_a_resolved_spelling_of_an_installed_folder_is_mirrored_too(self):
        if not hasattr(os, "symlink"):
            self.skipTest(host_test_policy.environment_skip("no symbolic links here"))
        link = self.work / "linked-app"
        try:
            link.symlink_to(self.installed.parent, target_is_directory=True)
        except OSError as error:
            self.skipTest(host_test_policy.environment_skip("cannot make a link: %s" % error))
        prefix = str(self.work / "cache")
        with patch.object(sys, "pycache_prefix", prefix):
            mirrors = blender.pycache_mirrors([str(link / "scripts"), "", "relative/folder"])
            written = importlib.util.cache_from_source(str(link / "scripts" / "mod.py"))
            resolved = importlib.util.cache_from_source(str(self.installed / "mod.py"))
        self.assertTrue(inside(written, mirrors) and inside(resolved, mirrors), mirrors)
        self.assertEqual(len(mirrors), 2, "an empty or relative folder mirrors nothing: %s" % mirrors)

    def test_without_a_cache_prefix_nothing_of_the_cache_is_named(self):
        write_only, trusted, cached = self.folders(None)
        with patch.object(sys, "pycache_prefix", None):
            self.assertEqual(blender.pycache_mirrors([str(self.installed)]), [])
        for name in ("installed", "user"):   # Beside the source: inside the installed folder itself.
            self.assertTrue(inside(cached[name], trusted), cached[name])
        self.assertFalse(inside(cached["document"], trusted), cached["document"])


if __name__ == "__main__":
    unittest.main()
