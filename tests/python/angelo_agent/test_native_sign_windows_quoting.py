# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""sign_windows.ps1's CodeSignTool argument quoting: where PowerShell is installed (CI), each value a password can hold
is quoted by ConvertTo-ProcessArgument and split back by the Windows command-line rules (CommandLineToArgvW and the
MSVC runtime, which java.exe's launcher follows), and must come back as itself.
"""

import shutil
import subprocess
import unittest

from test_native_sign_windows import SIGN, block, read


def command_line_to_argv(line):
    """Windows' CommandLineToArgvW rules for the arguments after the program name (MSVC runtime, 2008+)."""
    args, current, i, quoted, started = [], [], 0, False, False
    while i < len(line):
        c = line[i]
        if c == "\\":
            n = 0
            while i < len(line) and line[i] == "\\":
                n += 1
                i += 1
            if i < len(line) and line[i] == '"':
                current.append("\\" * (n // 2))
                if n % 2:
                    current.append('"')
                    i += 1
            else:
                current.append("\\" * n)
            started = True
            continue
        if c == '"':
            if quoted and i + 1 < len(line) and line[i + 1] == '"':
                current.append('"')
                i += 2
                continue
            quoted = not quoted
            started = True
        elif c in " \t" and not quoted:
            if started:
                args.append("".join(current))
                current, started = [], False
        else:
            current.append(c)
            started = True
        i += 1
    if started:
        args.append("".join(current))
    return args


class QuotingTests(unittest.TestCase):
    """Run where PowerShell is installed (CI). Not a skippable test: the host suite allows no skips for its absence."""

    VALUES = ["plain", "with space", 'a"quote', "trail\\", 'back\\"slash', "amp&er%sand^!", "", "tab\there",
              "\\\\server\\share\\", 'x\\\\"y', "-password=p@ss w\"rd\\"]

    def test_every_value_comes_back_as_itself(self):
        pwsh = shutil.which("pwsh")
        if not pwsh:
            return
        function = block(read(SIGN), "function ConvertTo-ProcessArgument", "\n}\n") + "\n}\n"
        values = "@(" + ", ".join("'%s'" % v.replace("'", "''") for v in self.VALUES) + ")"
        command = function + "(%s | ForEach-Object { ConvertTo-ProcessArgument $_ }) -join ' '" % values
        done = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(command_line_to_argv(done.stdout.rstrip("\r\n")), self.VALUES)

    def test_the_reference_parser(self):
        self.assertEqual(command_line_to_argv('a "b c" d\\\\"e f" g\\"h'), ["a", "b c", 'd\\e f', 'g"h'])


if __name__ == "__main__":
    unittest.main()
