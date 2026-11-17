# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The macOS release build runs on the pinned Xcode and refuses a toolchain that changes under it
(scripts/angelo/release_toolchain_macos.py).

The class: in the 1.1 rehearsal (2026-11-04) Mac 2's Xcode updated itself from 27.1 to 27.2 during a release build; the
configure had chosen MacOSX27.0.sdk, the update removed it, and every later compile failed ('condition_variable' file
not found). Here a stand-in Xcode on disk (version.plist, an SDK with its SDKSettings.json) plays each case.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

from host_test_policy import environment_skip

ROOT = Path(__file__).resolve().parents[3]
TOOL = ROOT / "scripts/angelo/release_toolchain_macos.py"
BUILD_SCRIPT = ROOT / "scripts/angelo/build_release_macos.sh"


class ToolchainTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.developer = self.xcode("Xcode.app", "27.1", "27A400", ("27.0",))
        self.sdk = self.developer / "Platforms/MacOSX.platform/Developer/SDKs/MacOSX27.0.sdk"
        self.build = self.root / "build"
        self.build.mkdir()
        (self.build / "CMakeCache.txt").write_text("CMAKE_OSX_SYSROOT:PATH=%s\n" % self.sdk)

    def xcode(self, name, version, build, sdks):
        contents = self.root / name / "Contents"
        (contents / "Developer").mkdir(parents=True)
        (contents / "version.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": version,
                                                                  "ProductBuildVersion": build}))
        for sdk in sdks:
            folder = contents / ("Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX%s.sdk" % sdk)
            folder.mkdir(parents=True)
            (folder / "SDKSettings.json").write_text(json.dumps({"Version": sdk}))
        return contents / "Developer"

    def run_tool(self, *args, **env):
        environ = dict(os.environ, DEVELOPER_DIR=str(self.developer))
        for key in ("ANGELO_XCODE_PIN", "ANGELO_XCODE_OVERRIDE"):
            environ.pop(key, None)
        environ.update(env)
        return subprocess.run([sys.executable, "-B", str(TOOL)] + [str(a) for a in args], capture_output=True, text=True,
                              env=environ)

    def test_only_the_pinned_xcode_starts_a_build_unless_a_reason_is_given(self):
        self.assertEqual(self.run_tool("pin").returncode, 0, "no pin: the build only records")
        self.assertEqual(self.run_tool("pin", ANGELO_XCODE_PIN="27.1 27A400").returncode, 0)
        refused = self.run_tool("pin", ANGELO_XCODE_PIN="27.2 27B52")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("Xcode 27.1 27A400 is not the pinned 27.2 27B52", refused.stderr)
        allowed = self.run_tool("pin", ANGELO_XCODE_PIN="27.2 27B52", ANGELO_XCODE_OVERRIDE="rehearsal on 27.1")
        self.assertEqual(allowed.returncode, 0)
        self.assertIn("ANGELO_XCODE_OVERRIDE=rehearsal on 27.1", allowed.stdout)

    def test_the_record_names_xcode_and_the_sdk_the_configure_chose(self):
        result = self.run_tool("record", self.build, ANGELO_XCODE_OVERRIDE="why")
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads((self.build / "angelo-toolchain.json").read_text())
        self.assertEqual((record["xcode_version"], record["xcode_build"], record["sdk_version"], record["override"]),
                         ("27.1", "27A400", "27.0", "why"))
        self.assertEqual(record["sdk_path"], str(self.sdk))

    def test_a_configure_on_an_sdk_that_does_not_exist_is_refused(self):
        (self.build / "CMakeCache.txt").write_text("CMAKE_OSX_SYSROOT:PATH=%s\n" % (self.sdk.parent / "MacOSX9.sdk"))
        result = self.run_tool("record", self.build)
        self.assertEqual(result.returncode, 1)
        self.assertIn("which does not exist", result.stderr)

    def test_an_unchanged_toolchain_passes_at_the_end(self):
        self.run_tool("record", self.build)
        self.assertEqual(self.run_tool("verify", self.build, "end").returncode, 0)

    def test_an_xcode_update_mid_build_fails_the_build(self):
        # The rehearsal: Xcode moved to 27.2 and its 27.0 SDK went away while the build ran.
        self.run_tool("record", self.build)
        (self.developer.parent / "version.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": "27.2",
                                                                              "ProductBuildVersion": "27B52"}))
        (self.sdk / "SDKSettings.json").unlink()
        self.sdk.rmdir()
        result = self.run_tool("verify", self.build, "end")
        self.assertEqual(result.returncode, 1)
        self.assertIn("The toolchain changed during the build (end)", result.stderr)
        for field in ("xcode_version", "xcode_build", "sdk_folder", "sdk_version"):
            self.assertIn(field, result.stderr)

    def test_another_developer_folder_mid_build_fails_the_build(self):
        self.run_tool("record", self.build)
        other = self.xcode("Xcode-beta.app", "27.1", "27A400", ("27.0",))
        result = self.run_tool("verify", self.build, "end", DEVELOPER_DIR=str(other))
        self.assertEqual(result.returncode, 1)
        self.assertIn("developer_dir", result.stderr)

    @unittest.skipIf(os.name == "nt", environment_skip(
        "the link launcher replaces itself with the linker (os.execvp), which Windows only emulates; it runs in the "
        "macOS release build"))
    def test_a_link_runs_only_on_the_recorded_toolchain(self):
        self.run_tool("record", self.build)
        marker = self.root / "linked"
        link = ["link", self.build, "--", "/bin/sh", "-c", "echo linked > '%s'" % marker]
        self.assertEqual(self.run_tool(*link).returncode, 0)
        self.assertTrue(marker.exists(), "the linker ran")
        marker.unlink()
        (self.developer.parent / "version.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": "27.2",
                                                                              "ProductBuildVersion": "27B52"}))
        refused = self.run_tool(*link)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("(link)", refused.stderr)
        self.assertFalse(marker.exists(), "no link on a changed toolchain")

    def test_a_cache_from_another_xcode_goes_aside_before_the_configure(self):
        self.run_tool("record", self.build)
        self.assertEqual(self.run_tool("check", self.build).stdout, "")
        self.assertTrue((self.build / "CMakeCache.txt").exists(), "the same toolchain keeps its cache")
        (self.developer.parent / "version.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": "27.2",
                                                                              "ProductBuildVersion": "27B52"}))
        result = self.run_tool("check", self.build)
        self.assertEqual(result.returncode, 0)
        self.assertIn("toolchain changed (configured with Xcode 27.1 (27A400)", result.stdout)
        self.assertFalse((self.build / "CMakeCache.txt").exists())
        self.assertTrue((self.build / "CMakeCache.txt.toolchain-changed").exists())

    def test_a_cache_whose_sdk_vanished_goes_aside(self):
        # The rehearsal: the same Xcode record, but the update removed the SDK the cache names.
        self.run_tool("record", self.build)
        (self.sdk / "SDKSettings.json").unlink()
        self.sdk.rmdir()
        self.assertIn("its SDK %s is gone" % self.sdk, self.run_tool("check", self.build).stdout)
        self.assertFalse((self.build / "CMakeCache.txt").exists())

    def test_a_folder_without_a_record_goes_aside_only_when_its_sysroot_is_missing(self):
        self.assertEqual(self.run_tool("check", self.build).stdout, "")    # the sysroot exists: kept
        self.assertTrue((self.build / "CMakeCache.txt").exists())
        (self.build / "CMakeCache.txt").write_text("CMAKE_OSX_SYSROOT:PATH=%s\n" % (self.sdk.parent / "MacOSX26.0.sdk"))
        self.assertIn("cached CMAKE_OSX_SYSROOT", self.run_tool("check", self.build).stdout)
        self.assertFalse((self.build / "CMakeCache.txt").exists())

    def test_a_build_without_a_record_is_refused(self):
        result = self.run_tool("verify", self.build, "end")
        self.assertEqual(result.returncode, 1)
        self.assertIn("No toolchain record", result.stderr)


class WiredInTests(unittest.TestCase):
    def test_the_release_build_pins_records_checks_each_link_and_the_end(self):
        text = BUILD_SCRIPT.read_text()
        pin = text.index('python3 "$TOOLCHAIN" pin')
        self.assertLess(pin, text.index('python3 "$TOOLCHAIN" check "$BUILD"'))
        self.assertLess(text.index('python3 "$TOOLCHAIN" check "$BUILD"'), text.index('cmake -S "$SOURCE" -B "$BUILD"'))
        configure = text.index('cmake -S "$SOURCE" -B "$BUILD"')
        record = text.index('python3 "$TOOLCHAIN" record "$BUILD"')
        build = text.index('cmake --build "$BUILD"')
        end = text.index('python3 "$TOOLCHAIN" verify "$BUILD" end')
        self.assertLess(pin, configure)
        self.assertLess(configure, record)
        self.assertLess(record, build)
        self.assertLess(build, end)
        self.assertIn('LINK_CHECK="python3;$TOOLCHAIN;link;$BUILD;--"', text)
        for language in ("C", "CXX", "OBJC", "OBJCXX"):
            self.assertIn('-DCMAKE_%s_LINKER_LAUNCHER="$LINK_CHECK"' % language, text)


if __name__ == "__main__":
    unittest.main()
