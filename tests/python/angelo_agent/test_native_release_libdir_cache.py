# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A release build folder drops CMake's find results when its libraries change (scripts/angelo/release_libdir_cache.sh).

The class: a build folder configured against other libraries keeps every find result (USD_LIBRARY, USD_INCLUDE_DIR, ...)
and CMake never looks again when LIBDIR changes. In the 1.1 rehearsal (2026-11-04) Mac 2's release folder, configured
for 5.2's libraries, was built for 5.3: the executable linked 5.2's USD, the bundle installed 5.3's, and dyld refused it.
The macOS helper runs here on temporary folders; the Windows twin (Reset-BuildDirForLibraries) is pinned by source.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from host_test_policy import environment_skip

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "scripts/angelo/release_libdir_cache.sh"
MACOS = ROOT / "scripts/angelo/build_release_macos.sh"
WINDOWS = ROOT / "scripts/angelo/build_release_windows.ps1"
TOOLCHAIN = ROOT / "scripts/angelo/windows_release_toolchain.ps1"


@unittest.skipIf(os.name == "nt", environment_skip(
    "release_libdir_cache.sh is the macOS release build's bash helper; Windows' twin is pinned by WiredInTests"))
@unittest.skipUnless(shutil.which("bash"), "needs bash")
class LibdirCacheTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.build = self.root / "build"
        self.build.mkdir()
        # Two library checkouts, named as Blender names them (Mac 2: angelo-studio/lib/macos_arm64 at 5.2's pin and
        # lib53.noindex/macos_arm64 at 5.3's).
        self.lib52 = self.root / "lib52" / "macos_arm64"
        self.lib53 = self.root / "lib53" / "macos_arm64"
        self.lib52.mkdir(parents=True)
        self.lib53.mkdir(parents=True)

    def run_helper(self, action, libdir, commit, pinned="", check=True):
        result = subprocess.run(["bash", str(HELPER), action, str(self.build), str(libdir), commit, pinned],
                                capture_output=True, text=True, check=check)
        return result.stdout if check else result

    def configure(self, libdir, commit):
        """What a configure leaves: a cache naming LIBDIR and a find result in it, then the record."""
        (self.build / "CMakeCache.txt").write_text(
            "LIBDIR:UNINITIALIZED=%s\nUSD_LIBRARY:FILEPATH=%s/usd/lib/libusd_ms.dylib\n" % (libdir, libdir))
        self.run_helper("record", libdir, commit)

    def cache(self):
        return self.build / "CMakeCache.txt"

    def test_a_new_folder_has_nothing_to_drop(self):
        self.assertEqual(self.run_helper("check", self.lib53, "b" * 40), "")
        self.assertFalse(self.cache().exists())

    def test_the_same_libraries_keep_the_cache(self):
        self.configure(self.lib53, "b" * 40)
        self.assertEqual(self.run_helper("check", self.lib53, "b" * 40), "")
        self.assertTrue(self.cache().exists())

    def test_another_library_folder_drops_the_cache_and_keeps_it_aside(self):
        self.configure(self.lib52, "a" * 40)
        out = self.run_helper("check", self.lib53, "b" * 40)
        self.assertIn("configured with %s" % self.lib52, out)
        self.assertFalse(self.cache().exists())
        aside = self.build / "CMakeCache.txt.libdir-changed"
        self.assertIn("lib52/macos_arm64/usd/lib/libusd_ms.dylib", aside.read_text())

    def test_the_same_folder_at_another_commit_drops_the_cache(self):
        # The libraries updated in place (lib/macos_arm64 checked out at the new pin).
        self.configure(self.lib53, "a" * 40)
        out = self.run_helper("check", self.lib53, "b" * 40)
        self.assertIn("aaaaaaaaaaaa, now bbbbbbbbbbbb", out)
        self.assertFalse(self.cache().exists())

    def test_a_folder_from_before_the_record_compares_the_folder_alone(self):
        self.configure(self.lib52, "a" * 40)
        (self.build / "angelo-libdir.txt").unlink()
        self.run_helper("check", self.lib52, "b" * 40)
        self.assertTrue(self.cache().exists(), "no record: the commit is unknown, the folder is the same")
        self.run_helper("check", self.lib53, "b" * 40)
        self.assertFalse(self.cache().exists())

    def test_a_link_to_the_same_folder_is_the_same_folder(self):
        link = self.root / "libs-link"
        os.symlink(self.lib53, link)
        self.configure(link, "b" * 40)
        self.run_helper("check", self.lib53, "b" * 40)
        self.assertTrue(self.cache().exists())

    def test_a_configure_that_still_names_other_libraries_is_refused(self):
        # What Mac 2's 5.2 folder held after LIBDIR moved to 5.3: LIBDIR new, USD_* (and 130 more) still 5.2's.
        self.cache().write_text("LIBDIR:UNINITIALIZED=%s\nUSD_LIBRARY:FILEPATH=%s/usd/lib/libusd_ms.dylib\n"
                                "OPENVDB_LIBRARIES:STRING=%s/openvdb/lib/libopenvdb.dylib;%s/tbb/lib/libtbb.dylib\n"
                                % (self.lib53, self.lib52, self.lib53, self.lib52))
        result = self.run_helper("record", self.lib53, "b" * 40, "b" * 40, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("USD_LIBRARY = %s/usd/lib/libusd_ms.dylib" % self.lib52, result.stderr)
        self.assertIn("OPENVDB_LIBRARIES = %s/tbb/lib/libtbb.dylib" % self.lib52, result.stderr)
        self.assertNotIn("openvdb", result.stderr)
        self.assertFalse((self.build / "angelo-libdir.txt").exists(), "a refused configure is never recorded")

    def test_bracketed_find_details_are_read_as_paths(self):
        # FIND_PACKAGE_MESSAGE_DETAILS_<pkg> wraps each path in [ ]: a fresh configure's own paths pass, a stale one fails.
        line = "FIND_PACKAGE_MESSAGE_DETAILS_USD:INTERNAL=[%s/usd/lib/libusd_ms.dylib][%s/usd/include][v()]\n"
        self.cache().write_text("LIBDIR:UNINITIALIZED=%s\n" % self.lib53 + line % (self.lib53, self.lib53))
        self.run_helper("record", self.lib53, "b" * 40, "b" * 40)
        self.cache().write_text("LIBDIR:UNINITIALIZED=%s\n" % self.lib53 + line % (self.lib52, self.lib53))
        result = self.run_helper("record", self.lib53, "b" * 40, "b" * 40, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("FIND_PACKAGE_MESSAGE_DETAILS_USD = %s/usd/lib/libusd_ms.dylib\n" % self.lib52, result.stderr)

    def test_libraries_off_the_sources_pin_are_refused(self):
        self.cache().write_text("LIBDIR:UNINITIALIZED=%s\n" % self.lib53)
        result = self.run_helper("record", self.lib53, "a" * 40, "b" * 40, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("this source pins bbbbbbbbbbbb", result.stderr)
        self.run_helper("record", self.lib53, "b" * 40, "b" * 40)   # at the pin: recorded
        self.assertEqual((self.build / "angelo-libdir.txt").read_text().split("\n")[1], "b" * 40)

    def test_an_unknown_action_is_refused(self):
        result = subprocess.run(["bash", str(HELPER), "drop", str(self.build), str(self.lib53), "b" * 40],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)


class WiredInTests(unittest.TestCase):
    def test_macos_checks_before_the_configure_and_records_after_it(self):
        text = MACOS.read_text()
        check = text.index('release_libdir_cache.sh" check "$BUILD" "$LIBDIR" "$ACTUAL"')
        configure = text.index('cmake -S "$SOURCE" -B "$BUILD"')
        record = text.index('release_libdir_cache.sh" record "$BUILD" "$LIBDIR" "$ACTUAL" "$PINNED"')
        self.assertLess(check, configure)
        self.assertLess(configure, record)
        self.assertLess(record, text.index('cmake --build "$BUILD"'))

    def test_windows_checks_before_the_configure_and_records_after_it(self):
        text = WINDOWS.read_text()
        check = text.index("Reset-BuildDirForLibraries $BuildDir $LibDir $libsHead")
        configure = text.index("$code = Invoke-Logged $configureLog")
        record = text.index("Confirm-BuildDirLibraries $BuildDir $LibDir $libsHead $pinned")
        self.assertLess(check, configure)
        self.assertLess(configure, record)

    def test_windows_applies_the_same_rule(self):
        text = TOOLCHAIN.read_text()
        body = text[text.index("function Reset-BuildDirForLibraries"):text.index("function Confirm-BuildDirLibraries")]
        self.assertIn('"^LIBDIR:[A-Z]*=(.*)$"', body)           # the folder the cache was configured with
        self.assertIn("angelo-libdir.txt", body)                 # the recorded commit
        self.assertIn('"CMakeCache.txt.libdir-changed"', body)   # set aside, never deleted
        self.assertIn("Replace('\\', '/').TrimEnd('/').ToLowerInvariant()", body)   # paths compare as Windows does
        confirm = text[text.index("function Confirm-BuildDirLibraries"):]
        confirm = confirm[:confirm.index("\n}\n")]
        self.assertIn('$LibCommit -ne $Pinned) { throw', confirm)             # off the pin: refused
        self.assertIn("This configure still names libraries outside", confirm)  # another checkout's paths: refused
        self.assertIn("-split '[;\\[\\]\"]'", confirm)                         # lists and [path][path] details
        self.assertLess(confirm.index("throw (\"This configure"), confirm.index("angelo-libdir.txt"))


if __name__ == "__main__":
    unittest.main()
