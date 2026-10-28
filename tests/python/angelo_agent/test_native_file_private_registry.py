# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Break-it r2m4c round 6 on the retained native-profile list: concurrent writers and unavailable metadata.

* M-1: every write is a merge under the account store's lock, read after the lock is taken, so workspace set-up and a
  connections save never drop each other's entries.
* M-2: fail closed. An entry is forgotten only when its folder is positively gone; a list that cannot be read is never
  rewritten and makes file tools refuse every operation; a removal whose profile cannot be recorded is not saved.
Synthetic folders only.
"""

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import private_roots  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402

LIST = "retained-native-profiles.json"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-private-registry-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.accounts, self.state = self.base / "accounts", self.base / "state"
        self.accounts.mkdir()

    def profile(self, *parts):
        folder = self.base.joinpath(*parts, "codex-profile", "connection")
        folder.mkdir(parents=True)
        (folder / "synthetic-auth.txt").write_text("SYNTHETIC")
        return folder

    def recorded(self):
        return json.loads((self.accounts / LIST).read_text())

    def unlock_later(self, path):
        self.addCleanup(os.chmod, path, stat.S_IRWXU)


class ConcurrentWriterTests(Fixture):
    def test_two_writers_never_drop_each_others_entries(self):
        """Review probe 1: A (workspace set-up) has read the list and pauses; B (a connections save) records Q; A then
        writes. Both P and Q stay recorded."""
        first, second = self.profile("p"), self.profile("q")
        read, go = threading.Event(), threading.Event()
        real = private_roots.retained_profiles

        def pausing(folder, *rest):
            value = real(folder, *rest)
            if threading.current_thread().name == "A" and not read.is_set():
                read.set()
                go.wait(5)
            return value
        with patch.object(private_roots, "retained_profiles", side_effect=pausing):
            a = threading.Thread(name="A", target=private_roots.remember_profiles,
                                 args=(self.accounts, [{"native_profile": str(first)}]))
            b = threading.Thread(name="B", target=private_roots.remember_profiles,
                                 args=(self.accounts, [{"native_profile": str(second)}]))
            a.start()
            self.assertTrue(read.wait(5))
            b.start()
            time.sleep(0.5)   # B waits for A's lock (or, unlocked, writes now).
            go.set()
            a.join(10)
            b.join(10)
        self.assertEqual(set(self.recorded()), {str(first), str(second)})


class UnavailableMetadataTests(Fixture):
    def test_an_unreadable_or_damaged_list_is_never_rewritten_and_fails_closed_narrowly(self):
        """Round 6 M-2, narrowed in round 7: what the list held stays refused; an ordinary file stays readable."""
        retained = self.base / "retained" / "home"   # Not a profile-family name: only the list protects it.
        retained.mkdir(parents=True)
        (retained / "synthetic-auth.txt").write_text("SYNTHETIC")
        private_roots.remember_profiles(self.accounts, [{"native_profile": str(retained)}])
        private_roots.private_roots(self.state, self.accounts, [], environ={})   # A last good read.
        (self.base / "public.txt").write_text("PUBLIC")
        for damage in ("unreadable", "malformed", "wrong shape"):
            with self.subTest(damage=damage):
                listing = self.accounts / LIST
                os.chmod(listing, stat.S_IRUSR | stat.S_IWUSR)
                listing.write_text({"unreadable": json.dumps([str(retained)]), "malformed": "[not json",
                                    "wrong shape": json.dumps({"profiles": [str(retained)]})}[damage])
                before = listing.read_bytes()
                if damage == "unreadable":
                    os.chmod(listing, 0)
                    self.unlock_later(listing)
                roots = private_roots.private_roots(self.state, self.accounts, [], environ={})
                self.assertFalse(roots.complete)
                files = FileWorkspace(self.base, excluded=roots)
                with self.assertRaisesRegex(ValueError, "Application profiles"):
                    files.execute("angelo_read_file", {"path": "retained/home/synthetic-auth.txt", "start": 0,
                                                       "length": 64})
                self.assertEqual(files.execute("angelo_read_file", {"path": "public.txt", "start": 0,
                                                                    "length": 64})["text"], "PUBLIC")
                os.chmod(listing, stat.S_IRUSR | stat.S_IWUSR)
                self.assertEqual(listing.read_bytes(), before, "never rewritten")

    def test_a_folder_that_cannot_be_looked_up_is_kept(self):
        """Review probe 2: search permission on the retained profile's parent is denied during workspace set-up."""
        retained = self.profile("locked")
        private_roots.remember_profiles(self.accounts, [{"native_profile": str(retained)}])
        os.chmod(self.base / "locked", 0)
        self.unlock_later(self.base / "locked")
        private_roots.private_roots(self.state, self.accounts, [], environ={})
        os.chmod(self.base / "locked", stat.S_IRWXU)
        self.assertEqual(self.recorded(), [str(retained)])
        files = FileWorkspace(self.base, excluded=private_roots.private_roots(self.state, self.accounts, [],
                                                                              environ={}))
        with self.assertRaisesRegex(ValueError, "Application profiles"):
            files.execute("angelo_read_file", {"path": "locked/codex-profile/connection/synthetic-auth.txt",
                                               "start": 0, "length": 64})

    def test_only_a_positively_absent_folder_is_forgotten(self):
        gone, kept = self.profile("gone"), self.profile("kept")
        private_roots.remember_profiles(self.accounts, [{"native_profile": str(gone)}, {"native_profile": str(kept)}])
        for item in sorted(gone.iterdir()):
            item.unlink()
        gone.rmdir()
        private_roots.remember_profiles(self.accounts, [])
        self.assertEqual(self.recorded(), [str(kept)])

    def test_a_removal_whose_profile_cannot_be_recorded_is_not_saved(self):
        from angelo_agent.connections import ConnectionStore
        store = ConnectionStore(self.accounts)
        record = store.add("OPENAI", "SUBSCRIPTION", name="Synthetic")
        retained = self.base / "imported" / "codex-profile" / record["id"]   # The saved form: <family>/<id>.
        retained.mkdir(parents=True)
        store.records[record["id"]]["native_profile"] = str(retained)
        (self.accounts / LIST).write_text("[not json")   # The list is damaged before the profile was recorded.
        store.save()
        self.assertEqual(ConnectionStore(self.accounts).records[record["id"]]["native_profile"], str(retained))
        with self.assertRaisesRegex(OSError, "nothing was changed"):
            store.remove(record["id"])
        self.assertIn(record["id"], ConnectionStore(self.accounts).records)


if __name__ == "__main__":
    unittest.main()
