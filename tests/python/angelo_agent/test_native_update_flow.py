# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Preparing an update (update_flow.Preparer): delta first, the full download when the delta fails, never a
downgrade, a ready update that survives a restart, and folders cleaned without touching the installed app.

The platform modules are stood in for (their own rules are test_native_update_stage); downloads write files.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import update_delta, update_feed, update_flow, update_stage_mac, update_stage_win  # noqa: E402
from angelo_agent.update_download import DownloadError  # noqa: E402

DMG = "https://downloads.angelo.studio/1.0.1/Angelo-1.0.1-macos-arm64.dmg"
DELTA = "https://downloads.angelo.studio/1.0.1/Angelo-1.0.0-to-1.0.1-macos-arm64.delta.tar.xz"
MSI = "https://downloads.angelo.studio/1.0.1/Angelo-1.0.1-windows-x64.msi"


def release(version="1.0.1"):
    return SimpleNamespace(version=version)


def download(url=DMG, deltas=()):
    return update_feed.Download(url, "a" * 64, 1000, tuple(deltas))


def delta(source="1.0.0", form=update_delta.FORMAT):
    return update_feed.Delta(source, DELTA, "b" * 64, 100, form)


class FakeMac:
    def __init__(self, root, delta_error=None, reason=""):
        self.root, self.delta_error, self.reason, self.calls = root, delta_error, reason, []
        self.gatekeeper = False

    def support(self, binary):
        return self.reason, self.root / "Applications" / "Angelo.app"

    def _stage(self, work):
        staged = Path(work) / "staged" / "Angelo.app"
        (staged / "Contents").mkdir(parents=True, exist_ok=True)
        (staged / "Contents/Info.plist").write_text("1.0.1")
        return staged

    def from_delta(self, package, source, version, work, app):
        self.calls.append(("delta", Path(package).name))
        if self.delta_error is not None:
            raise self.delta_error
        return self._stage(work)

    def from_disk_image(self, image, version, work, app):
        self.calls.append(("image", Path(image).name))
        return self._stage(work)

    def fingerprint(self, bundle):
        return update_stage_mac.fingerprint(bundle)

    def approved(self, bundle):
        return self.gatekeeper

    def release_mount(self, mount):
        return False


class FlowCase(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.fetched, self.fail = [], {}

    def fetch(self, expected, target, reserve=0, **options):
        self.fetched.append(Path(target).name)
        error = self.fail.get(expected.url)
        if error is not None:
            raise error
        Path(target).write_bytes(b"x")
        return Path(target)

    def preparer(self, mac=None, platform="macos-arm64", current="1.0.0", win=None):
        self.mac = mac or FakeMac(self.folder)
        return update_flow.Preparer(self.folder / "updates", current, platform, "/A/Angelo.app/Contents/MacOS/Angelo",
                                    fetch=self.fetch, mac=self.mac, win=win, log=lambda *a: None)


class MacFlowTests(FlowCase):
    def test_a_delta_from_the_running_version_is_used_and_removed(self):
        ready = self.preparer().prepare(release(), download(deltas=[delta("0.9.0"), delta("1.0.0")]))
        self.assertEqual((ready.method, ready.version, ready.family, ready.gatekeeper), ("delta", "1.0.1", "macos", ""))
        self.assertEqual(self.fetched, ["Angelo-1.0.0-to-1.0.1-macos-arm64.delta.tar.xz"])
        work = self.folder / "updates" / "1.0.1"
        self.assertFalse((work / self.fetched[0]).exists(), "the package is gone once applied")
        stored = json.loads((work / update_flow.READY_FILE).read_text())
        self.assertEqual(stored["payload"], str(work / "staged" / "Angelo.app"))

    def test_a_failed_delta_falls_back_to_the_full_download(self):
        for error in (update_delta.DeltaError("mismatch"), update_stage_mac.StageError("signature")):
            with self.subTest(error=error.code):
                shutil.rmtree(self.folder / "updates", ignore_errors=True)
                self.fetched.clear()
                ready = self.preparer(FakeMac(self.folder, delta_error=error)).prepare(release(),
                                                                                    download(deltas=[delta()]))
                self.assertEqual(ready.method, "full")
                self.assertEqual(self.fetched[-1], "Angelo-1.0.1-macos-arm64.dmg")

    def test_a_delta_that_could_not_be_downloaded_for_a_passing_reason_waits_for_the_next_try(self):
        self.fail[DELTA] = DownloadError("offline")
        with self.assertRaises(update_flow.PrepareError) as caught:
            self.preparer().prepare(release(), download(deltas=[delta()]))
        self.assertEqual((caught.exception.code, caught.exception.transient), ("download:offline", True))
        self.assertEqual(len(self.fetched), 1, "no full download that would fail the same way")

    def test_a_broken_delta_download_falls_back(self):
        self.fail[DELTA] = DownloadError("digest")
        self.assertEqual(self.preparer().prepare(release(), download(deltas=[delta()])).method, "full")

    def test_nothing_is_installed_from_another_host(self):
        with self.assertRaises(update_flow.PrepareError) as caught:
            self.preparer().prepare(release(), download("https://github.com/x/y/releases/download/v1/a.dmg"))
        self.assertEqual(caught.exception.code, "unsupported:host")
        elsewhere = update_feed.Delta("1.0.0", "https://github.com/x/y/releases/download/v1/d.tar.xz", "b" * 64, 1,
                                      update_delta.FORMAT)
        self.assertEqual(self.preparer().prepare(release(), download(deltas=[elsewhere])).method, "full")
        self.assertNotIn("d.tar.xz", self.fetched)

    def test_unknown_formats_and_other_versions_deltas_are_ignored(self):
        self.preparer().prepare(release(), download(deltas=[delta(form="other-format"), delta("0.9.0")]))
        self.assertEqual(self.fetched, ["Angelo-1.0.1-macos-arm64.dmg"])

    def test_never_a_downgrade_or_the_same_version(self):
        for version in ("1.0.0", "0.9.9"):
            with self.assertRaises(update_flow.PrepareError) as caught:
                self.preparer().prepare(release(version), download())
            self.assertEqual(caught.exception.code, "not_newer")
        self.assertEqual(self.fetched, [])

    def test_an_install_that_cannot_replace_itself_downloads_nothing(self):
        with self.assertRaises(update_flow.PrepareError) as caught:
            self.preparer(FakeMac(self.folder, reason="permission")).prepare(release(), download())
        self.assertEqual(caught.exception.code, "unsupported:permission")
        with self.assertRaises(update_flow.PrepareError) as caught:
            self.preparer(platform="linux-x64").prepare(release(), download())
        self.assertEqual(caught.exception.code, "unsupported:platform")
        self.assertEqual(self.fetched, [])

    def test_a_ready_update_survives_a_restart_until_its_stage_changes(self):
        ready = self.preparer().prepare(release(), download())
        again = self.preparer()
        self.assertEqual(again.recall("1.0.1"), ready)
        self.assertEqual(again.prepare(release(), download()), ready)
        self.assertEqual(self.fetched, ["Angelo-1.0.1-macos-arm64.dmg"], "nothing downloaded twice")
        (Path(ready.payload) / "Contents/Info.plist").write_text("tampered or damaged")
        self.assertIsNone(again.recall("1.0.1"))
        self.assertFalse(again.still_valid(ready))

    def test_a_ready_record_pointing_outside_its_folder_is_never_trusted(self):
        ready = self.preparer().prepare(release(), download())
        record = Path(ready.work) / update_flow.READY_FILE
        document = json.loads(record.read_text())
        document["payload"] = str(self.folder / "Applications" / "Angelo.app")
        record.write_text(json.dumps(document))
        self.assertIsNone(self.preparer().recall("1.0.1"))


class ResumeAcrossAttemptsTests(FlowCase):
    def test_a_partial_download_survives_a_failed_attempt(self):
        seen = []

        def fetch(expected, target, reserve=0, **options):
            part = Path(str(target) + ".part")
            seen.append(part.exists())
            if not part.exists():
                part.write_bytes(b"first half")
                raise DownloadError("offline")
            Path(target).write_bytes(b"x")
            return Path(target)
        self.fetch = fetch
        with self.assertRaises(update_flow.PrepareError):
            self.preparer().prepare(release(), download())
        self.preparer().prepare(release(), download())
        self.assertEqual(seen, [False, True], "the second attempt finds the first one's partial file")

    def test_a_ready_update_of_another_copy_is_not_this_ones(self):
        ready = self.preparer().prepare(release(), download())
        other = FakeMac(self.folder)
        other.support = lambda binary: ("", self.folder / "Elsewhere" / "Angelo.app")
        self.assertIsNone(self.preparer(other).recall("1.0.1"))
        self.assertFalse(self.preparer(other).still_valid(ready))

    def test_cleaning_detaches_a_disk_image_left_attached_first(self):
        root = self.folder / "updates"
        (root / "1.0.0" / "mount").mkdir(parents=True)
        released = []
        mac = FakeMac(self.folder)
        mac.release_mount = lambda mount: released.append(Path(mount).parent.name)
        self.preparer(mac).clean()
        self.assertEqual(released, ["1.0.0"])

    def test_only_older_versions_are_cleaned(self):
        root = self.folder / "updates"
        for version in ("1.0.0", "1.0.1", "1.0.2", "junk"):
            (root / version).mkdir(parents=True)
        self.preparer().clean_older("1.0.1")
        self.assertEqual(sorted(p.name for p in root.iterdir()), ["1.0.1", "1.0.2"])


class WindowsFlowTests(FlowCase):
    def win(self, error=None, reason=""):
        def check(installer, binary):
            if error is not None:
                raise update_stage_win.InstallerError(error)
        return SimpleNamespace(support=lambda binary, code: (reason, Path("C:/P/Angelo")), check_installer=check,
                               InstallerError=update_stage_win.InstallerError)

    def test_the_installer_is_downloaded_and_its_signer_checked(self):
        ready = self.preparer(platform="windows-x64", win=self.win()).prepare(release(), download(MSI))
        self.assertEqual((ready.family, ready.method, ready.fingerprint), ("windows", "full", "a" * 64))
        self.assertTrue(Path(ready.payload).name.endswith(".msi"))

    def test_an_installer_from_another_publisher_is_deleted(self):
        with self.assertRaises(update_flow.PrepareError) as caught:
            self.preparer(platform="windows-x64", win=self.win("signature")).prepare(release(), download(MSI))
        self.assertEqual(caught.exception.code, "verify:signature")
        self.assertFalse(any((self.folder / "updates" / "1.0.1").glob("*.msi")))

    def test_an_installer_staged_earlier_is_checked_again_before_it_is_reused(self):
        # An installer an earlier build staged under other rules (re-review 2026-10-21) is never installed unchecked.
        ready = self.preparer(platform="windows-x64", win=self.win()).prepare(release(), download(MSI))

        def later(win):   # a later launch; the stand-in download's bytes are not the feed's digest, so the stage is
            preparer = self.preparer(platform="windows-x64", win=win)   # taken as unchanged here
            preparer.still_valid = lambda staged: True
            return preparer
        self.assertEqual(later(self.win()).recall("1.0.1", download(MSI)), ready)
        self.assertIsNone(later(self.win("signature")).recall("1.0.1", download(MSI)))
        with self.assertRaises(update_flow.PrepareError) as caught:
            self.preparer(platform="windows-x64", win=self.win("signature")).prepare(release(), download(MSI))
        self.assertEqual(caught.exception.code, "verify:signature")

    def test_a_per_machine_install_is_left_to_its_administrator(self):
        with self.assertRaises(update_flow.PrepareError) as caught:
            self.preparer(platform="windows-x64", win=self.win(reason="machine")).prepare(release(), download(MSI))
        self.assertEqual(caught.exception.code, "unsupported:machine")


class FolderTests(FlowCase):
    def test_results_and_cleaning(self):
        root = self.folder / "updates"
        for version in ("1.0.1", "1.0.2", "not-a-version"):
            (root / version).mkdir(parents=True)
        (root / "1.0.1" / "result.txt").write_text("status=installed\nstep=swap\nversion=1.0.1\n")
        preparer = self.preparer()
        self.assertEqual(preparer.results(), [("1.0.1", {"status": "installed", "step": "swap", "version": "1.0.1"})])
        self.assertEqual(preparer.clean(keep={"1.0.2"}), ["1.0.1", "not-a-version"])
        self.assertEqual([p.name for p in root.iterdir()], ["1.0.2"])

    def test_the_previous_app_is_removed_only_where_the_installer_put_it(self):
        work = self.folder / "updates" / "1.0.1"
        app = self.folder / "Applications" / "Angelo.app"
        app.mkdir(parents=True)
        (work / "previous.app").mkdir(parents=True)
        (work / "previous.txt").write_text(str(work / "previous.app"))
        self.assertTrue(update_flow.remove_previous(work, app))
        self.assertFalse((work / "previous.app").exists())
        for named in (app, self.folder / "Documents", app.parent / ".Angelo.app.previous-1" / ".." / "Angelo.app"):
            (work / "previous.txt").write_text(str(named))
            self.assertFalse(update_flow.remove_previous(work, app), named)
        self.assertTrue(app.exists(), "the installed app is never removed")
        beside = app.parent / ".Angelo.app.previous-77"
        beside.mkdir()
        (work / "previous.txt").write_text(str(beside))
        self.assertTrue(update_flow.remove_previous(work, app))


if __name__ == "__main__":
    unittest.main()
