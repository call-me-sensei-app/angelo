# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Windows build machine's PowerShell scripts, read on any host before they reach the machine.

The release stage parses every ``scripts/angelo/*.ps1`` with PowerShell's own parser and stops when one fails;
host CI has no PowerShell. One mistake has cost a whole machine run: ``"... $commit: run ..."`` inside a
double-quoted string, which PowerShell reads as a drive-qualified variable and refuses ("Variable reference is not
valid. ':' was not followed by a valid variable name character"). ``${commit}:`` is the spelling that works.
"""

import re
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts/angelo"
# A variable followed by a colon that does not start a scope or drive name ($env:, $script:, ${name}: are fine).
DRIVE_QUALIFIED = re.compile(r"\$(?!env:|script:|global:|local:|private:|using:|function:|variable:)"
                             r"[A-Za-z_][A-Za-z0-9_]*:(?![A-Za-z0-9_\\{])")
DOUBLE_QUOTED = re.compile(r'"(?:[^"`]|`.)*"')


class PowerShellTests(unittest.TestCase):
    def test_no_variable_is_followed_by_a_bare_colon_in_a_string(self):
        scripts = sorted(SCRIPTS.glob("*.ps1"))
        self.assertTrue(scripts)
        found = []
        for script in scripts:
            for number, line in enumerate(script.read_text(encoding="utf-8").splitlines(), 1):
                found += ["%s:%d %s" % (script.name, number, text) for text in DOUBLE_QUOTED.findall(line)
                          if DRIVE_QUALIFIED.search(text)]
        self.assertEqual(found, [], "write ${name}: inside a double-quoted string")

    def test_the_pattern_catches_the_mistake_and_allows_the_fix(self):
        self.assertTrue(DRIVE_QUALIFIED.search('"not $commit: run it"'))
        for fine in ('"not ${commit}: run it"', '"$env:TEMP"', '"$script:buildOk"', '"$($a.b): x"', '"C:\\x"'):
            self.assertFalse(DRIVE_QUALIFIED.search(fine), fine)

    def test_the_library_folder_reaches_cmake_with_forward_slashes(self):
        # CMake writes LIBDIR into generated code (cmake_install.cmake), where C:\angelo reads as the escape \a and
        # INSTALL fails after the whole build.
        build = (SCRIPTS / "build_release_windows.ps1").read_text(encoding="utf-8")
        passed = [line.strip() for line in build.splitlines() if "-DLIBDIR=" in line]
        self.assertEqual(passed, ["\"`\"-DLIBDIR=$($LibDir.Replace('\\', '/'))`\"\","])


if __name__ == "__main__":
    unittest.main()
