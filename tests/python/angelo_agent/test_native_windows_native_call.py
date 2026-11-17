# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""On the Windows release path, a program's redirected error output is read only through ``Invoke-NativeCapture``.

The path's scripts run under Windows PowerShell 5.1, mostly with ``$ErrorActionPreference = "Stop"``, where each line
a program writes to a redirected stderr (``2>&1``, ``2>$null``, ``2>file``, ``*>&1``) becomes an ErrorRecord and the
first one a terminating NativeCommandError: a Python warning fails the MSI build, and a real refusal surfaces as that
error instead of its own message. #207 fixed it once for the identity stage's add-on prune; gpu_sdk_cache.py and
gpu_sdk_pins.py were still called that way in windows_release_toolchain.ps1 (2026-11-16), and some twenty more calls
on the path each kept their own hand-written Continue scope. ``Invoke-NativeCapture`` (windows_native_call.ps1) is the
one way now, so this test refuses every other redirect on the path without judging which scope a line sits in.

The path is the cloud bootstrap's release stage, the release build and signing (gcp-kit's call and dot-source edges
at 3dc7bcaef4d, plus the scripts those dot-source); a script newly dot-sourced from one of them must join the list.
Scripts off the path are the 2.1.0 backlog (doc/angelo/releasing.rst). Dependency-free; host CI has no PowerShell.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts/angelo"
PATH = (
    "windows_cloud_bootstrap.ps1", "windows_release_inputs.ps1", "windows_bootstrap_stages.ps1",
    "windows_session_start.ps1", "windows_release_validate.ps1", "windows_release_toolchain.ps1",
    "windows_msi_commit.ps1", "windows_source_send.ps1", "build_release_windows.ps1",
    "windows_agent_runtime_check.ps1", "windows_msi_compare.ps1", "sign_windows.ps1", "windows_pe_digest.ps1",
    "windows_sign_bootstrap.ps1", "windows_native_call.ps1")
HELPER = "Invoke-NativeCapture"
STANDALONE = ("windows_cloud_bootstrap.ps1", "windows_sign_bootstrap.ps1")   # Startup scripts: no checkout yet.
REDIRECT = re.compile(r"(?<![\w>])[2*]>(?!>)")   # 2>&1, 2>$null, 2> file, *>&1 (never 2>> or a number like 12>).
BATCH = re.compile(r"WriteAllText\(\$batch|@echo off")   # cmd.exe's own redirects inside a batch file's text.
DOT_SOURCED = re.compile(r'(?:^\s*|[;{(]\s*)\.\s+\(Join-Path\b[^"\n]*"(?:[^"\n]*\\)?([A-Za-z0-9_]+\.ps1)"')
LOADS = '. (Join-Path $PSScriptRoot "windows_native_call.ps1")'


def code_lines(text):
    """``(number, line)`` of code: comment lines and ``<# ... #>`` blocks dropped, a trailing `` # comment`` cut."""
    lines, block = [], False
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if block:
            block = "#>" not in stripped
            continue
        if stripped.startswith("<#"):
            block = "#>" not in stripped
            continue
        if stripped.startswith("#"):
            continue
        lines.append((number, re.sub(r"\s#\s.*$", "", line)))
    return lines


def helper_text(text):
    """The text of ``function Invoke-NativeCapture`` up to its closing brace at the start of a line."""
    start = text.index("function %s(" % HELPER)
    return text[start:text.index("\n}", start) + 2]


def helper_span(text):
    """``(first line, last line)`` of the helper's definition in ``text``, or None."""
    if "function %s(" % HELPER not in text:
        return None
    start = text.index("function %s(" % HELPER)
    first = text.count("\n", 0, start) + 1
    return first, first + helper_text(text).count("\n")


def bare_redirects(name, text):
    """Lines of ``text`` that redirect a program's stderr outside the helper's own body and a batch file's text."""
    span = helper_span(text)
    return ["%s:%d %s" % (name, number, line.strip()) for number, line in code_lines(text)
            if REDIRECT.search(line) and not BATCH.search(line) and not (span and span[0] <= number <= span[1])]


def read(name):
    return (SCRIPTS / name).read_text(encoding="utf-8")


class NativeCallTests(unittest.TestCase):
    def test_every_script_the_path_dot_sources_is_on_it(self):
        sourced = {name: sorted({other for _, line in code_lines(read(name)) for other in DOT_SOURCED.findall(line)})
                   for name in PATH}
        self.assertIn("windows_release_toolchain.ps1", sourced["build_release_windows.ps1"], "the scan finds them")
        missing = ["%s dot-sources %s" % (name, other) for name, others in sourced.items() for other in others
                   if other not in PATH]
        self.assertEqual(missing, [], "a script on the release path: add it to PATH")

    def test_no_script_on_the_path_redirects_a_programs_stderr_bare(self):
        found = []
        for name in PATH:
            found += bare_redirects(name, read(name))
        self.assertEqual(found, [], "run it through Invoke-NativeCapture (windows_native_call.ps1)")

    def test_the_helper_is_one_definition(self):
        canonical = helper_text(read("windows_native_call.ps1"))
        body = canonical.split("\n", 1)[1]
        self.assertIn('$old = $ErrorActionPreference; $ErrorActionPreference = "Continue"', body)
        self.assertIn("finally { $ErrorActionPreference = $old", body)
        self.assertIn("$code = $LASTEXITCODE", body)
        self.assertIn("-isnot [System.Management.Automation.ErrorRecord]", body, "standard output kept apart")
        self.assertIn("Code = -1", body.split("$old =", 1)[0], "a missing program never leaves a stale exit code")
        self.assertIn("[Text.Encoding]::Unicode", body, "-Log writes what Tee-Object wrote")
        self.assertIn("[IO.FileShare]::ReadWrite", body, "the heartbeat reads the log's tail meanwhile")
        for name in PATH:
            text = read(name)
            with self.subTest(script=name):
                if name in STANDALONE:
                    self.assertEqual(helper_text(text), canonical, "byte for byte windows_native_call.ps1's")
                    self.assertIn("$NativeCallDefinition = @'\n" + canonical + "\n'@\n. ([scriptblock]::Create($NativeCallDefinition))", text)
                elif name != "windows_native_call.ps1":
                    self.assertNotIn("function %s(" % HELPER, text, "dot-source windows_native_call.ps1 instead")

    def test_a_script_that_calls_the_helper_loads_it(self):
        callers = {"windows_release_toolchain.ps1", "sign_windows.ps1", "windows_release_inputs.ps1",
                   "windows_bootstrap_stages.ps1", "windows_session_start.ps1", "windows_msi_commit.ps1",
                   "windows_source_send.ps1"}
        for name in PATH:
            text = read(name)
            uses = any(HELPER in line for _, line in code_lines(text))
            with self.subTest(script=name):
                if name in STANDALONE or name == "windows_native_call.ps1":
                    continue
                if name in callers:
                    self.assertIn(LOADS, text)
                elif uses:   # Loaded by the script that runs or dot-sources it.
                    self.assertIn(name, ("build_release_windows.ps1", "windows_release_validate.ps1"), name)
        build = read("build_release_windows.ps1")
        self.assertLess(build.index('. (Join-Path $PSScriptRoot "windows_release_toolchain.ps1")'), build.index(HELPER))
        validate = read("windows_release_validate.ps1")
        self.assertLess(validate.index('. (Join-Path $PSScriptRoot "windows_release_toolchain.ps1")'), validate.index(HELPER))

    def test_the_bootstraps_background_jobs_load_the_helper_in_their_own_runspace(self):
        text = read("windows_cloud_bootstrap.ps1")
        jobs = [part.split("} -ArgumentList", 1) for part in text.split("Start-Job -ScriptBlock {")[1:]]
        self.assertEqual(len(jobs), 2)
        for body, arguments in jobs:
            if HELPER in body:
                self.assertIn(". ([scriptblock]::Create($NativeCallDefinition))", body)
                self.assertIn("$NativeCallDefinition", arguments.split("\n", 1)[0])

    def test_the_check_refuses_the_patterns_it_replaced(self):
        old = ('function Get-Pins {\n    $pinText = & $python -I -B (Join-Path $SourceDir "x.py") pins 2>&1\n}\n'
               '$x = & $tool @a 2>$null\n& $tool > $log 2> $err\n'
               '& powershell.exe -File x.ps1 *>&1 | Tee-Object log.txt\n'
               'function Invoke-Quiet([scriptblock]$Body) {\n    try { & $Body 2>$null } finally { }\n}\n')
        self.assertEqual(len(bare_redirects("old.ps1", old)), 5)
        fine = ('# & $python x.py 2>&1 in a comment\n'
                '[IO.File]::WriteAllText($batch, "@echo off`r`n$Command > `"$Log`" 2>&1")\n'
                '$ran = Invoke-NativeCapture $python @("-I", "x.py")\n$n = 12>3\n')
        self.assertEqual(bare_redirects("fine.ps1", fine), [])


if __name__ == "__main__":
    unittest.main()
