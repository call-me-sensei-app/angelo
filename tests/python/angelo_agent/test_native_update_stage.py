# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether an install can update itself, and the checks on what it would install (update_stage_mac/_win).

Apple's and Microsoft's tools (hdiutil, ditto, codesign, msi.dll, PowerShell) are stood in for by recording
fakes, so every rule runs on any system; the real tools run in the end-to-end updates (releasing.rst).
"""

import ctypes
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import update_stage_mac as mac, update_stage_win as win  # noqa: E402

TEAM = "B876L5D532"


def make_app(path, version="1.0.1", identifier="studio.hyperbond.angelo"):
    (path / "Contents/MacOS").mkdir(parents=True)
    (path / "Contents/Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": identifier, "CFBundleShortVersionString": version, "CFBundleExecutable": "Angelo"}))
    (path / "Contents/MacOS/Angelo").write_bytes(b"binary " + version.encode())
    return path


class FakeTools:
    """Answers the macOS tools as a signed app would; ``fail`` names tools that fail."""

    def __init__(self, team=TEAM, fail=(), disk_app=None, rejected=()):
        self.team, self.fail, self.disk_app, self.calls = team, set(fail), disk_app, []
        self.rejected = {str(path) for path in rejected}   # Bundles Gatekeeper refuses.

    def __call__(self, args, timeout, background=True):
        self.calls.append(list(args))
        tool = Path(args[0]).name
        verb = args[1] if len(args) > 1 else ""
        name = "%s %s" % (tool, verb) if tool == "hdiutil" else tool
        if name in self.fail or (tool == "codesign" and "--verify" in args and "codesign verify" in self.fail):
            return subprocess.CompletedProcess(args, 1, "", "refused")
        if tool == "spctl" and args[-1] in self.rejected:
            return subprocess.CompletedProcess(args, 3, "", "rejected")
        if tool == "codesign" and "-dv" in args:
            info = "Identifier=studio.hyperbond.angelo\n" + ("TeamIdentifier=%s\n" % self.team if self.team else
                                                             "TeamIdentifier=not set\n")
            return subprocess.CompletedProcess(args, 0, "", info)
        if name == "hdiutil attach":
            shutil.copytree(self.disk_app, Path(args[args.index("-mountpoint") + 1]) / self.disk_app.name)
        if tool == "ditto":
            shutil.copytree(args[1], args[2], symlinks=True)
        return subprocess.CompletedProcess(args, 0, "", "")


class MacCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.app = make_app(self.root / "Applications" / "Angelo.app", "1.0.0")
        self.binary = self.app / "Contents/MacOS/Angelo"
        self.work = self.root / "updates" / "1.0.1"


class MacSupportTests(MacCase):
    def test_an_app_the_person_can_replace_can_update_itself(self):
        self.assertEqual(mac.support(self.binary), ("", self.app))

    def test_a_translocated_unbundled_or_unwritable_app_cannot(self):
        moved = make_app(self.root / "AppTranslocation" / "X" / "Angelo.app")
        self.assertEqual(mac.support(moved / "Contents/MacOS/Angelo")[0], "location")
        self.assertEqual(mac.support(self.root / "bin" / "angelo")[0], "location")
        with mock.patch.object(mac.os, "access", return_value=False):
            self.assertEqual(mac.support(self.binary)[0], "permission")
        with mock.patch.object(mac.os, "statvfs", return_value=mock.Mock(f_flag=os.ST_RDONLY)):
            self.assertEqual(mac.support(self.binary)[0], "location")


class MacCheckTests(MacCase):
    def test_the_requirement_follows_the_running_apps_team(self):
        self.assertEqual(mac.requirement(self.app, FakeTools()),
                         'identifier "studio.hyperbond.angelo" and anchor apple generic and '
                         'certificate leaf[subject.OU] = "%s"' % TEAM)
        self.assertEqual(mac.requirement(self.app, FakeTools(team="")), 'identifier "studio.hyperbond.angelo"')

    def test_a_new_bundle_must_be_this_app_at_the_feeds_version_signed_by_its_team(self):
        tools = FakeTools()
        good = make_app(self.root / "new" / "Angelo.app")
        mac.check(good, "1.0.1", self.app, tools)
        verify = next(call for call in tools.calls if "--verify" in call)
        self.assertIn("--deep", verify)
        self.assertIn("--strict", verify)
        self.assertTrue(any(arg.startswith("-R=") and TEAM in arg for arg in verify))
        cases = {"version": (make_app(self.root / "v" / "Angelo.app", "1.0.2"), tools),
                 "bundle_id": (make_app(self.root / "i" / "Angelo.app", identifier="org.example.other"), tools),
                 "signature": (good, FakeTools(fail={"codesign verify"}))}
        for code, (bundle, runner) in cases.items():
            with self.subTest(code=code):
                with self.assertRaises(mac.StageError) as caught:
                    mac.check(bundle, "1.0.1", self.app, runner)
                self.assertEqual(caught.exception.code, code)

    def test_the_disk_image_is_unpacked_verified_and_always_detached(self):
        source = make_app(self.root / "dmg" / "Angelo.app")
        tools = FakeTools(disk_app=source)
        staged = mac.from_disk_image(self.root / "x.dmg", "1.0.1", self.work, self.app, tools)
        self.assertEqual(staged, self.work / "staged" / "Angelo.app")
        self.assertEqual((staged / "Contents/MacOS/Angelo").read_bytes(), b"binary 1.0.1")
        attach = next(call for call in tools.calls if call[:2] == ["/usr/bin/hdiutil", "attach"])
        for flag in ("-nobrowse", "-readonly", "-noautoopen"):
            self.assertIn(flag, attach)
        self.assertTrue(any(call[:2] == ["/usr/bin/hdiutil", "detach"] for call in tools.calls))
        failing = FakeTools(disk_app=source, fail={"ditto"})
        shutil.rmtree(self.work)
        with self.assertRaises(mac.StageError):
            mac.from_disk_image(self.root / "x.dmg", "1.0.1", self.work, self.app, failing)
        self.assertTrue(any(call[:2] == ["/usr/bin/hdiutil", "detach"] for call in failing.calls))
        self.assertFalse((self.work / "staged").exists())

    def test_a_mount_left_by_a_quit_is_detached_first_and_a_renamed_app_is_found_by_its_id(self):
        source = make_app(self.root / "dmg" / "Angelo.app")
        renamed = make_app(self.root / "Applications" / "Angelo 2.app", "1.0.0")
        tools = FakeTools(disk_app=source)
        mount = self.work / "mount"
        with mock.patch.object(mac.os.path, "ismount", lambda path: Path(path) == mount):
            staged = mac.from_disk_image(self.root / "x.dmg", "1.0.1", self.work, renamed, tools)
        self.assertEqual(tools.calls[0][:3], ["/usr/bin/hdiutil", "detach", "-force"])
        self.assertEqual(staged.name, "Angelo 2.app", "staged under the installed app's name")

    def test_gatekeeper_must_approve_the_update_when_it_approves_the_running_app(self):
        new = make_app(self.root / "new" / "Angelo.app")
        notarized = FakeTools(rejected={new})
        with self.assertRaises(mac.StageError) as caught:
            mac.check(new, "1.0.1", self.app, notarized)
        self.assertEqual(caught.exception.code, "gatekeeper")
        development = FakeTools(rejected={self.app, new})   # A build that is not notarized: the signature alone.
        mac.check(new, "1.0.1", self.app, development)
        self.assertEqual([call[-1] for call in development.calls if Path(call[0]).name == "spctl"], [str(self.app)])
        self.assertTrue(mac.approved(new, FakeTools()))

    def test_a_bundle_that_fails_its_check_is_not_left_staged(self):
        source = make_app(self.root / "dmg" / "Angelo.app", "1.0.2")
        with self.assertRaises(mac.StageError):
            mac.from_disk_image(self.root / "x.dmg", "1.0.1", self.work, self.app, FakeTools(disk_app=source))
        self.assertFalse((self.work / "staged").exists())
        self.assertFalse((self.work / "staging").exists())

    def test_the_fingerprint_changes_when_the_stage_does(self):
        staged = make_app(self.root / "staged" / "Angelo.app")
        before = mac.fingerprint(staged)
        self.assertEqual(before, mac.fingerprint(staged))
        os.utime(staged / "Contents/MacOS/Angelo", ns=(1, 1))
        self.assertNotEqual(before, mac.fingerprint(staged))
        after = mac.fingerprint(staged)
        (staged / "Contents/Info.plist").unlink()
        self.assertNotEqual(after, mac.fingerprint(staged))

    def test_other_running_copies_of_this_bundle(self):
        target = str(self.app / "Contents/MacOS/Angelo")
        listing = " 10 %s\n 11 /Applications/Other.app/Contents/MacOS/Angelo\n 12 %s\n" % (target, target)

        def runner(args, timeout, background=True):
            return subprocess.CompletedProcess(args, 0, listing, "")
        self.assertEqual(mac.other_instances(self.app, "Angelo", 12, runner), [10])


class WindowsSupportTests(unittest.TestCase):
    root = r"C:\Users\pat\AppData\Local\Programs\Angelo"
    binary = root + r"\Angelo.exe"

    def products(self, *rows):
        return lambda code: list(rows)

    def setUp(self):
        windows_paths = mock.patch.object(win, "Path", PureWindowsPath)   # These tests read Windows paths anywhere.
        windows_paths.start()
        self.addCleanup(windows_paths.stop)

    def test_only_a_per_user_windows_installer_install_updates_itself(self):
        with mock.patch.object(win.os, "access", return_value=True), \
                mock.patch.object(win.os.path, "normcase", side_effect=str.lower), \
                mock.patch.object(win.os.path, "abspath", side_effect=lambda p: p):
            self.assertEqual(win.support(self.binary, "X", self.products(("{P}", "0", self.root + "\\")))[0], "")
            self.assertEqual(win.support(self.binary, "X", self.products(("{P}", "1", self.root)))[0], "machine")
            self.assertEqual(win.support(self.binary, "X", self.products(("{P}", "0", r"C:\Elsewhere")))[0], "location")
            self.assertEqual(win.support(self.binary, "X", self.products())[0], "location")

            def broken(code):
                raise OSError("no msi.dll")
            self.assertEqual(win.support(self.binary, "X", broken)[0], "location")
        with mock.patch.object(win.os, "access", return_value=False), \
                mock.patch.object(win.os.path, "normcase", side_effect=str.lower), \
                mock.patch.object(win.os.path, "abspath", side_effect=lambda p: p):
            self.assertEqual(win.support(self.binary, "X", self.products(("{P}", "0", self.root)))[0], "permission")

    def test_related_products_are_read_through_msi_dll(self):
        class Msi:
            def MsiEnumRelatedProductsW(self, code, reserved, index, buffer):
                self.code = code
                if index:
                    return win.ERROR_NO_MORE_ITEMS
                buffer.value = "{11111111-2222-3333-4444-555555555555}"
                return win.ERROR_SUCCESS

            def MsiGetProductInfoW(self, product, name, buffer, size):
                buffer.value = {"AssignmentType": "0", "InstallLocation": r"C:\A\\"}[name]
                return win.ERROR_SUCCESS
        msi = Msi()
        self.assertEqual(win.related_products("326840A0-5B07-451C-85F8-D3DCB3F2CD04", msi),
                         [("{11111111-2222-3333-4444-555555555555}", "0", "C:\\A\\\\")])
        self.assertEqual(msi.code, "{326840A0-5B07-451C-85F8-D3DCB3F2CD04}")


class WindowsInstallerTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.msi = self.folder / "Angelo-1.0.1-windows-x64.msi"
        self.msi.write_bytes(b"msi")

    def test_a_signed_install_takes_only_its_own_publishers_installer(self):
        angelo = {"status": "Valid", "subject": "CN=Hyperbond Studio PTE. LTD.", "issuer": "CN=CA"}

        def signatures(table):
            return lambda path: table[Path(path).name]
        win.check_installer(self.msi, "Angelo.exe", signatures({"Angelo.exe": angelo, self.msi.name: angelo}))
        for package in ({"status": "NotSigned", "subject": "", "issuer": ""},
                        dict(angelo, subject="CN=Someone Else"), dict(angelo, status="HashMismatch")):
            with self.subTest(package=package):
                with self.assertRaises(win.InstallerError) as caught:
                    win.check_installer(self.msi, "Angelo.exe", signatures({"Angelo.exe": angelo, self.msi.name: package}))
                self.assertEqual(caught.exception.code, "signature")

    def test_the_publisher_is_the_validated_name_whatever_ca_or_intermediate_issued_it(self):
        # Angelo's Windows certificate may come from Azure Artifact Signing (3-day certificates from Microsoft's
        # intermediates) or an OV CA (SSL.com); a release signed by either must update one signed by the other.
        running = {"status": "Valid", "issuer": "CN=Microsoft ID Verified CS EOC CA 01, O=Microsoft Corporation, C=US",
                   "subject": "CN=Hyperbond Studio Pte. Ltd., O=Hyperbond Studio Pte. Ltd., L=Singapore, C=SG"}
        accepted = (
            dict(running, issuer="CN=Microsoft ID Verified CS EOC CA 02, O=Microsoft Corporation, C=US"),
            {"status": "Valid", "issuer": "CN=SSL.com Code Signing Intermediate CA RSA R1, O=SSL Corporation, C=US",
             "subject": "CN=HYPERBOND STUDIO PTE. LTD., O=HYPERBOND STUDIO PTE. LTD., L=Singapore, S=Singapore, C=SG"},
            dict(running, subject="C=SG, S=Singapore, L=Singapore, O=Hyperbond  Studio Pte. Ltd., "
                                  "CN=Hyperbond Studio Pte. Ltd."),
        )
        refused = (
            dict(running, subject="CN=Hyperbond Studio Pte. Ltd., O=Hyperbond Studio Pte. Ltd., L=Austin, S=Texas, C=US"),
            dict(running, subject="CN=Hyperbond Studio Pte. Ltd., O=Someone Else Ltd., C=SG"),
            dict(running, subject='CN=x, O="Hyperbond Studio Pte. Ltd., C=SG", C=GB'),
            dict(running, subject="CN=x, O=Hyperbond Studio Pte. Ltd. + O=Someone Else Ltd., C=SG"),
            dict(running, subject="CN=Hyperbond Studio Pte. Ltd., O=Hyperbond Studio Pte. Ltd., C=SG, C=US"),
            dict(running, status="NotTrusted"),
            dict(running, status="UnknownError"),
            {"status": "Valid", "subject": "", "issuer": ""},
        )
        tables = lambda package: lambda path: running if str(path) == "Angelo.exe" else package  # noqa: E731
        for package in accepted:
            with self.subTest(accepted=package["subject"]):
                win.check_installer(self.msi, "Angelo.exe", tables(package))
        for package in refused:
            with self.subTest(refused=package):
                with self.assertRaises(win.InstallerError) as caught:
                    win.check_installer(self.msi, "Angelo.exe", tables(package))
                self.assertEqual(caught.exception.code, "signature")
        # A running app whose valid signature names no publisher takes nothing it cannot compare.
        nameless = dict(running, subject="L=Singapore, C=SG")
        with self.assertRaises(win.InstallerError):
            win.check_installer(self.msi, "Angelo.exe", lambda path: nameless)

    def test_only_an_unsigned_running_app_skips_the_comparison(self):
        # Break-it review 2026-10-21: any non-Valid state of the running app used to skip the check, so a blocked or
        # timed-out PowerShell ("Unknown") let an unsigned installer through. Only NotSigned skips it now.
        unsigned_msi = {"status": "NotSigned", "subject": "", "issuer": ""}
        for status in ("Unknown", "UnknownError", "NotTrusted", "HashMismatch", "NotSupportedFileFormat", "Incompatible"):
            running = {"status": status, "subject": "CN=Hyperbond Studio Pte. Ltd., O=Hyperbond Studio Pte. Ltd., C=SG",
                       "issuer": ""}
            with self.subTest(status=status):
                with self.assertRaises(win.InstallerError) as caught:
                    win.check_installer(self.msi, "Angelo.exe",
                                        lambda path, r=running: r if str(path) == "Angelo.exe" else unsigned_msi)
                self.assertEqual(caught.exception.code, "signature")
        blocked = win.authenticode("Angelo.exe", lambda *a, **k: (_ for _ in ()).throw(OSError("blocked by policy")))
        with self.assertRaises(win.InstallerError):
            win.check_installer(self.msi, "Angelo.exe", lambda path: blocked if str(path) == "Angelo.exe" else unsigned_msi)

    def test_folding_is_ascii_only(self):
        ours = win.publisher("CN=Hyperbond Studio Pte. Ltd., O=Hyperbond Studio Pte. Ltd., C=SG")
        self.assertEqual(win.publisher("O=HYPERBOND\tSTUDIO  PTE. LTD., C=sg"), ours)
        self.assertEqual(win.publisher_named("Hyperbond Studio Pte. Ltd.", "SG"), ours)
        for look_alike in ("Hyperbond \u017ftudio Pte. Ltd.",        # long s: casefold() made it "s"
                           "Hyperbond\u00a0Studio Pte. Ltd.",        # no-break space
                           "Hyperbond\u3000Studio Pte. Ltd.",        # ideographic space
                           "Hyperbond\x1fStudio Pte. Ltd.",          # str.split() treated it as space
                           "\u00a0Hyperbond Studio Pte. Ltd.",       # str.strip() removed it
                           "Hyperbond Studio Pte. Ltd.\u200b",       # zero-width space
                           "HYPERBOND STUDİO PTE. LTD."):             # dotted capital I
            with self.subTest(look_alike=look_alike):
                self.assertNotEqual(win.publisher('O="%s", C=SG' % look_alike), ours)

    def test_a_subject_is_read_as_dotnet_writes_it(self):
        self.assertEqual(win.publisher('CN="Acme, Inc.", O="Acme, Inc.", C=US'), ("O", "acme, inc.", "us"))
        self.assertEqual(win.publisher('O="Say ""Hi"" Ltd", C=SG'), ("O", 'say "hi" ltd', "sg"))
        self.assertEqual(win.publisher("CN=Jane Developer, C=CA"), ("CN", "jane developer", "ca"))
        # A person's certificate never matches an organization's of the same name (re-review 2026-10-21).
        self.assertNotEqual(win.publisher("CN=Hyperbond Studio Pte. Ltd., C=SG"),
                            win.publisher("CN=Hyperbond Studio Pte. Ltd., O=Hyperbond Studio Pte. Ltd., C=SG"))
        self.assertIsNone(win.publisher(""))
        self.assertIsNone(win.publisher("O=, C=SG"))

    def test_an_unsigned_build_relies_on_the_feeds_digest_alone(self):
        unsigned = {"status": "NotSigned", "subject": "", "issuer": ""}
        win.check_installer(self.msi, "Angelo.exe", lambda path: unsigned)
        self.msi.unlink()
        with self.assertRaises(win.InstallerError):
            win.check_installer(self.msi, "Angelo.exe", lambda path: unsigned)

    def test_an_unreadable_signature_reads_as_unknown(self):
        def runner(*args, **kwargs):
            raise OSError("no PowerShell")
        self.assertEqual(win.authenticode("x", runner)["status"], "Unknown")
        answer = subprocess.CompletedProcess([], 0, '{"status":"Valid","subject":"CN=A","issuer":"CN=B"}', "")
        self.assertEqual(win.authenticode("x", lambda *a, **k: answer),
                         {"status": "Valid", "subject": "CN=A", "issuer": "CN=B"})


if __name__ == "__main__":
    unittest.main()
