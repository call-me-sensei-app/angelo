# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An update's paths come from Python state a script can change in place (an offer's version, a prepared ``Ready``):
whatever they say, Angelo removes only a version's own folder under the updates folder, and installs only what it
staged there (piece 10, p10c-1-update).

* ``Preparer.work`` takes one folder name that reads as a version: an offer whose version names another folder
  (absolute, ``..``, nested) removes nothing (``update_ready.withdraw`` -> ``forget``). Without it, a version naming
  Angelo's state folder had the removal pass ``may_write`` (a private root) and deleted every chat.
* ``Preparer.still_valid``, checked right before the installer starts, holds a ``Ready`` to the folder of its version,
  this platform, and the one payload staging puts there. Without it, a ``Ready`` changed to a folder the agent can
  write (the open document's) with a matching digest had Windows install the agent's MSI.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import dataclasses
import hashlib
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import update_feed, update_flow, update_stage_mac, update_stage_win  # noqa: E402
from test_native_update_flow import MSI, FlowCase, download, release  # noqa: E402

NOT_ONE_VERSION = ("..", ".", "", "1.0", "1.0.1/", "1.0.1/x", "1.0.1/../../state", "../state", "1.0.1\\..\\x",
                   "1.0.1\0")


class VersionNamesOneFolderTests(FlowCase):
    def test_work_refuses_a_name_that_is_not_one_version(self):
        preparer = self.preparer()
        self.assertEqual(preparer.work("1.0.1"), self.folder / "updates" / "1.0.1")
        for version in NOT_ONE_VERSION + (str(self.folder / "state"), None, 101):
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    preparer.work(version)

    def test_a_version_naming_another_folder_removes_nothing(self):
        preparer = self.preparer()
        preparer.prepare(release(), download())
        chats = self.folder / "state" / "chats"
        chats.mkdir(parents=True)
        (chats / "chat.json").write_text("{}")
        for version in NOT_ONE_VERSION + (str(self.folder / "state"), str(chats)):
            with self.subTest(version=version):
                self.assertEqual(preparer.forget(version), [])
                self.assertTrue((chats / "chat.json").is_file())
        self.assertTrue((self.folder / "updates" / "1.0.1").is_dir())
        self.assertEqual(preparer.forget("1.0.1"), ["1.0.1"])


class MacReadyHeldToItsFolderTests(FlowCase):
    def elsewhere(self, ready, folder):
        """``ready`` changed to a copy of its stage in ``folder``, its fingerprint matching that copy."""
        shutil.copytree(Path(ready.work) / "staged", folder / "staged")
        payload = folder / "staged" / Path(ready.payload).name
        return dataclasses.replace(ready, work=str(folder), payload=str(payload),
                                   fingerprint=update_stage_mac.fingerprint(payload))

    def test_an_unchanged_ready_installs(self):
        preparer = self.preparer()
        self.assertTrue(preparer.still_valid(preparer.prepare(release(), download())))

    def test_a_ready_moved_to_another_folder_is_never_installed(self):
        preparer = self.preparer()
        ready = preparer.prepare(release(), download())
        moved = self.elsewhere(ready, self.folder / "Documents" / "1.0.1")
        self.assertFalse(preparer.still_valid(moved))

    def test_a_payload_elsewhere_in_its_folder_is_never_installed(self):
        preparer = self.preparer()
        ready = preparer.prepare(release(), download())
        other = Path(ready.work) / "staging" / "Angelo.app"
        shutil.copytree(ready.payload, other)
        self.assertFalse(preparer.still_valid(dataclasses.replace(
            ready, payload=str(other), fingerprint=update_stage_mac.fingerprint(other))))

    def test_a_ready_of_another_version_name_or_platform_is_never_installed(self):
        preparer = self.preparer()
        ready = preparer.prepare(release(), download())
        for change in ({"version": "../1.0.1"}, {"version": "1.0.2"}, {"family": "windows"}):
            with self.subTest(change=change):
                self.assertFalse(preparer.still_valid(dataclasses.replace(ready, **change)))


class WindowsReadyHeldToItsFolderTests(FlowCase):
    def windows(self):
        return self.preparer(platform="windows-x64", win=_Win())

    def prepared(self, preparer):
        bytes_written = b"x"   # FlowCase.fetch writes these; the feed's digest is theirs here.
        entry = update_feed.Download(MSI, hashlib.sha256(bytes_written).hexdigest(), 1000, ())
        return preparer.prepare(release(), entry)

    def test_an_unchanged_ready_installs(self):
        preparer = self.windows()
        self.assertTrue(preparer.still_valid(self.prepared(preparer)))

    def test_an_installer_the_agent_put_elsewhere_is_never_run(self):
        preparer = self.windows()
        ready = self.prepared(preparer)
        documents = self.folder / "Documents"
        for folder, name in ((documents, "evil.msi"), (Path(ready.work) / "sub", "evil.msi")):
            with self.subTest(folder=folder):
                folder.mkdir(parents=True, exist_ok=True)
                evil = folder / name
                evil.write_bytes(b"not Angelo")
                work = ready.work if folder.parent == Path(ready.work) else str(folder)
                changed = dataclasses.replace(ready, work=work, payload=str(evil),
                                              fingerprint=hashlib.sha256(b"not Angelo").hexdigest())
                self.assertFalse(preparer.still_valid(changed))

    def test_a_ready_of_another_platform_is_never_installed(self):
        preparer = self.windows()
        self.assertFalse(preparer.still_valid(dataclasses.replace(self.prepared(preparer), family="macos")))


class _Win:
    InstallerError = update_stage_win.InstallerError

    @staticmethod
    def support(binary, code):
        return "", Path("C:/P/Angelo")

    @staticmethod
    def check_installer(msi, binary):
        return None


if __name__ == "__main__":
    unittest.main()
