# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Break-it r2m4c round 2 on file-tool privacy: every application-state folder, by identity, with an exact exception.

* R2-M-1: profiles migration retained in pre-rename folders, and saved native CLI profiles, are refused like the
  current state folder and account store (one registry: ``private_roots``).
* R2-M-2: a case or Unicode spelling of a private folder on a filesystem that treats them as one is that folder.
* R2-M-3: a default workspace that is a link grants no exception.
Synthetic folders only; no real profile or credential is touched.
"""

import os
from pathlib import Path
import sys
import tempfile
import unicodedata
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import storage  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


def read(files, relative):
    return files.execute("angelo_read_file", {"path": relative, "start": 0, "length": 64})


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-private-roots-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()

    def refused(self, files, relative):
        with self.assertRaisesRegex(ValueError, "Application profiles"):
            read(files, relative)

    def synthetic(self, folder):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "synthetic.txt").write_text("SYNTHETIC")


class RetainedProfileTests(Fixture):
    def test_the_registry_lists_every_active_and_retained_root(self):
        from angelo_agent.private_roots import private_roots
        state, accounts = self.base / "config" / "angelo-agent", self.base / "accounts"
        saved = self.base / "elsewhere" / "codex-profile" / "connection-1"
        roots = private_roots(state, accounts, [{"native_profile": str(saved)}, {"native_profile": None}], environ={})
        for root in (state, accounts, self.base / "config" / "toonlab-agent", saved,
                     storage.data_directory({}), storage.data_directory({}, folder=storage.LEGACY_DATA_FOLDER)):
            with self.subTest(root=root):
                self.assertIn(root, roots)

    def test_a_retained_or_saved_profile_is_refused_to_file_tools(self):
        """Review P1/M-1: the project folder holds the state folder's parent, so retained profiles are inside it."""
        from angelo_agent.private_roots import private_roots
        current, accounts = self.base / "angelo-agent", self.base / "accounts"
        retained = self.base / "toonlab-agent" / "codex-profile" / "synthetic"
        saved = self.base / "elsewhere" / "claude-profile" / "connection-1"
        for folder in (current / "workspace", accounts, retained, saved):
            self.synthetic(folder)
        files = FileWorkspace(self.base, own=current / "workspace",
                              excluded=private_roots(current, accounts, [{"native_profile": str(saved)}], environ={}))
        self.refused(files, "toonlab-agent/codex-profile/synthetic/synthetic.txt")
        self.refused(files, "elsewhere/claude-profile/connection-1/synthetic.txt")
        names = [entry["name"] for entry in files.execute("angelo_list_files", {"path": "."})["entries"]]
        self.assertEqual(names, ["elsewhere"], "no state folder is listed")

    def test_the_window_takes_its_exclusions_from_the_registry(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/ui.py").read_text()
        self.assertIn("excluded=private_roots(state_directory(), connection_directory(), connections().records.values())",
                      source)


class AliasTests(Fixture):
    def alias(self, private, spelling):
        alias = self.base / spelling
        if not (alias.exists() and alias.samefile(private)):
            self.skipTest(environment_skip("this filesystem does not treat %r and %r as one folder"
                                           % (private.name, spelling)))
        return alias

    def test_a_case_spelling_of_a_private_folder_is_refused(self):
        """Review P1/M-2 on case-insensitive APFS."""
        private = self.base / "Profile"
        self.synthetic(private)
        self.synthetic(private / "inner")
        self.alias(private, "profile")
        files = FileWorkspace(self.base, excluded=[private])
        for relative in ("profile/synthetic.txt", "PROFILE/inner/synthetic.txt"):
            with self.subTest(relative=relative):
                self.refused(files, relative)
        with self.assertRaisesRegex(ValueError, "Application profiles"):
            files.execute("angelo_list_files", {"path": "profile"})
        self.assertEqual(files.execute("angelo_list_files", {"path": "."})["entries"], [])
        inside = FileWorkspace(self.base / "profile" / "inner", excluded=[private])   # A project inside it, by alias.
        self.refused(inside, "synthetic.txt")

    def test_a_unicode_spelling_of_a_private_folder_is_refused(self):
        composed, decomposed = unicodedata.normalize("NFC", "Café"), unicodedata.normalize("NFD", "Café")
        private = self.base / composed
        self.synthetic(private)
        self.alias(private, decomposed)
        files = FileWorkspace(self.base, excluded=[private])
        self.refused(files, decomposed + "/synthetic.txt")


class LinkedWorkspaceTests(Fixture):
    def test_a_linked_default_workspace_grants_no_exception(self):
        """Review P1/M-3: <profile>/workspace -> <profile> (or -> the account store)."""
        profile, accounts = self.base / "state", self.base / "accounts"
        for target in (profile, accounts):
            with self.subTest(target=target.name):
                self.synthetic(profile)
                self.synthetic(accounts)
                own = profile / "workspace"
                if own.is_symlink():
                    own.unlink()
                own.symlink_to(target, target_is_directory=True)
                files = FileWorkspace(own, excluded=[profile, accounts], own=own)
                self.refused(files, "synthetic.txt")
                with self.assertRaisesRegex(ValueError, "Application profiles"):
                    files.execute("angelo_list_files", {"path": "."})
                self.assertFalse(getattr(files, "own", None))

    def test_the_real_default_workspace_keeps_its_exception_and_nothing_above_or_inside_it(self):
        profile = self.base / "state"
        own = profile / "workspace"
        self.synthetic(own)
        self.synthetic(profile)
        nested = own / "accounts"   # A private folder configured inside the workspace stays private.
        self.synthetic(nested)
        files = FileWorkspace(own, excluded=[profile, nested], own=own)
        self.assertEqual(read(files, "synthetic.txt")["text"], "SYNTHETIC")
        self.refused(files, "accounts/synthetic.txt")
        self.assertEqual([entry["name"] for entry in files.execute("angelo_list_files", {"path": "."})["entries"]],
                         ["synthetic.txt"])
        self.assertTrue(getattr(files, "own", None))
        self.assertFalse(getattr(FileWorkspace(own / "accounts", excluded=[profile], own=own), "own", None),
                         "only the exact folder")


if __name__ == "__main__":
    unittest.main()
