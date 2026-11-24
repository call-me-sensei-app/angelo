# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A bench request's session command (windows_bench_session_command.ps1): the bench's desktop session runs a named
session.ps1 in place of final_runs.py, on the gates' own path (preparation, kept sign-in check, throwaway user and
its cleanup), for a harness that needs a signed-in person's desktop (update_carry_over_gate.py's state receipt on a
gate machine). Pinned from the source: the scripts run on the Windows machines."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import hashlib
import re
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts/angelo"


def bench_inputs():
    """The bench request's named inputs as gcp_windows_validate.sh's bench stage reads them: its sourced helper
    (split out to keep the launcher within 500 lines; the integrator's batch-3 suite, 2026-11-20)."""
    launcher = text("gcp_windows_validate.sh")
    bench = launcher[launcher.index("\nbench() {"):]
    assert 'source "$ROOT/scripts/angelo/gcp_windows_bench_inputs.sh"' in bench[:bench.index("\n}\n")]
    return text("gcp_windows_bench_inputs.sh")


def text(name):
    return (SCRIPTS / name).read_text(encoding="utf-8")


HOOK = re.compile(r"BenchSessionInput|BenchSessionCommand|\$sessionDir|windows_bench_session_command\.ps1")
MOVED = ("function Refresh-Path {", "function Add-MachinePath([string]$Folder) {")
# windows_final_runs.ps1 at 7bc06dcfca9 (the 2.0.1 candidate, before the hook), normalized below.
BEFORE_THE_HOOK = "32cea1a586b11fc2778ef049cbc80bb28a51964685d730ab4c8c01fc59f56b37"
REFRESH_AND_ADD = """function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function Add-MachinePath([string]$Folder) {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    if (($machine.Split(";") | Where-Object { $_ -eq $Folder }).Count -eq 0) {
        [Environment]::SetEnvironmentVariable("Path", "$Folder;$machine", "Machine")
    }
    Refresh-Path
}"""


def without_the_hook(text):
    """windows_final_runs.ps1 without the hook's own lines (each acts only when a request names a session command),
    its if/else wrapper around the campaign, and the two functions moved to windows_bench_tools.ps1; SHA-256."""
    lines, out, wrapped, i = text.replace("\r\n", "\n").split("\n"), [], 0, 0
    while i < len(lines):
        line = lines[i]
        if line in MOVED:
            while lines[i] != "}":
                i += 1
            i += 1
            continue
        if HOOK.search(line):
            if line.strip().startswith("if (Get-BenchSessionInput) {") and re.search(r"\{\s*(#.*)?$", line):
                while lines[i].strip() != "} else {":
                    i += 1
                wrapped += 1
            i += 1
            continue
        if wrapped and line == "        }" and i + 1 < len(lines) and lines[i + 1] == "    } catch {":
            wrapped -= 1
            i += 1
            continue
        out.append(line)
        i += 1
    return hashlib.sha256("\n".join(out).encode()).hexdigest()


class SessionCommand(unittest.TestCase):
    def test_the_launcher_writes_the_input_on_every_send_and_only_from_its_bucket(self):
        launcher = bench_inputs()
        self.assertIn('local session_input="${ANGELO_BENCH_SESSION_INPUT:-}"', launcher)
        self.assertIn(',angelo-bench-session-input=$session_input"', launcher)   # empty when unset: never carried over
        self.assertIn('"$session_input" == "gs://$BUCKET/"*', launcher)

    def test_the_stage_fetches_it_before_the_session_and_publishes_after(self):
        runs, helper = text("windows_final_runs.ps1"), text("windows_bench_session_command.ps1")
        self.assertIn('. (Join-Path $PSScriptRoot "windows_bench_session_command.ps1")', runs)
        stage = runs[runs.index("if (-not $Stage)"):]
        receive = stage.index("Receive-BenchSessionCommand")
        self.assertLess(receive, stage.index("New-LocalUser"), "fetched as SYSTEM before the throwaway user exists")
        self.assertLess(stage.index("Remove-BenchUser $benchUser)"), stage.index("Publish-BenchSessionCommand"))
        self.assertIn('-notlike "gs://$Bucket/*"', helper)   # only this machine's own bucket
        receive = helper[helper.index("function Receive-BenchSessionCommand"):helper.index("function Invoke-BenchSessionCommand")]
        self.assertIn('"in\\prepare.ps1"', receive)   # the optional SYSTEM preparation, failing the request when it fails
        self.assertIn('throw "the session command\'s prepare.ps1 exited with $LASTEXITCODE"', receive)
        self.assertIn('if (Test-Path $sessionDir) { & icacls.exe $sessionDir /grant "${benchGroup}:(OI)(CI)M"', runs)

    def test_the_session_runs_it_in_place_of_final_runs_and_records_its_exit(self):
        runs = text("windows_final_runs.ps1")
        session = runs[runs.index("if ($Session) {"):runs.index("if (-not $Stage)")]
        hook = session.index("if (Get-BenchSessionInput) {")
        self.assertLess(session.index('if ($env:USERNAME -ne $benchUser'), hook, "only the bench user's session")
        self.assertLess(hook, session.index('"init", "--output"'), "in place of final_runs.py's campaign")
        self.assertRegex(session, re.compile(r"\$code = Invoke-BenchSessionCommand .*\$connections"))
        self.assertIn('Set-Content (Join-Path $out "DONE-$Nonce") ("$Mode exit code: $code"', session)   # its exit decides
        helper = text("windows_bench_session_command.ps1")
        invoke = helper[helper.index("function Invoke-BenchSessionCommand"):helper.index("function Publish-BenchSessionCommand")]
        # Only the exit code is the function's result: the command's output goes to the host.
        self.assertRegex(invoke, re.compile(r"-Connections \$Connections \| Out-Host\s+return \$LASTEXITCODE\s*}"))
        self.assertNotRegex(helper, re.compile(r"New-LocalUser|AutoAdminLogon|Remove-Item|DefaultPassword"))

    def test_without_a_session_command_every_bench_path_is_as_before(self):
        """Every gate, smoke and bench request names none: the script is the candidate's but for lines that act only
        when one is named, and the two PATH functions are the very ones it had, now in windows_bench_tools.ps1."""
        runs = text("windows_final_runs.ps1")
        self.assertEqual(without_the_hook(runs), BEFORE_THE_HOOK)
        for line in runs.splitlines():
            if HOOK.search(line) and "function" not in line and not line.lstrip().startswith(". (Join-Path"):
                self.assertRegex(line, r"if \(Get-BenchSessionInput\)|\$sessionDir = Join-Path|if \(Test-Path \$sessionDir\)"
                                       r"|\$code = Invoke-BenchSessionCommand", "an unguarded hook line: " + line)
        self.assertIn(REFRESH_AND_ADD, text("windows_bench_tools.ps1"))
        self.assertNotIn("function Refresh-Path", runs)
        launcher = bench_inputs()   # unset: the key is written empty, which the machine ignores
        self.assertIn('local session_input="${ANGELO_BENCH_SESSION_INPUT:-}"', launcher)
        self.assertIn('function Get-BenchSessionInput { "$(Get-Meta \'angelo-bench-session-input\' \'\')".Trim() }',
                      text("windows_bench_session_command.ps1"))

    def test_only_out_is_uploaded(self):
        helper = text("windows_bench_session_command.ps1")
        publish = helper[helper.index("function Publish-BenchSessionCommand"):]
        self.assertRegex(publish, re.compile(r'Invoke-Gcloud @\("storage", "rsync", "--recursive", \(Join-Path \$Dir "out"\), '
                                             r'"gs://\$Bucket/bench/session-\$Nonce/out"\)'))
        self.assertEqual(publish.count("Invoke-Gcloud"), 1, "nothing else of the session folder (in\\, work) leaves the machine")

    def test_a_later_boot_of_the_request_never_fetches_or_prepares_again(self):
        helper = text("windows_bench_session_command.ps1")
        receive = helper[helper.index("function Receive-BenchSessionCommand"):helper.index("function Invoke-BenchSessionCommand")]
        guard = receive.index('if (Test-Path (Join-Path $Dir "in\\session.ps1")) { return }')
        self.assertLess(guard, receive.index("Invoke-Gcloud"))
        self.assertLess(guard, receive.index("prepare.ps1"))

    def test_the_l5_session_command_is_in_the_repository_and_names_itself(self):
        bench = Path(__file__).resolve().parent / "update_gate_bench"
        session, prepare = (bench / "session.ps1").read_text(encoding="utf-8"), (bench / "prepare.ps1").read_text(encoding="utf-8")
        self.assertNotIn("__EXPECTED_SRC__", session)   # one file, byte for byte what runs
        self.assertIn("$expectSrc = [string]$env:ANGELO_SOURCE_COMMIT", session)   # the commit the session proved
        self.assertIn('if (-not $expectSrc -or $commit -ne $expectSrc)', session)
        self.assertIn('"session.ps1" = $PSCommandPath; "prepare.ps1" = (Join-Path $In "prepare.ps1")', session)
        self.assertIn("$env:ANGELO_GATE_SESSION_TOOLS = ($toolShas | ConvertTo-Json -Compress)", session)
        self.assertLess(session.index("ANGELO_GATE_SESSION_TOOLS ="), session.index("update_carry_over_gate.py"))
        self.assertIn('$work = Join-Path (Split-Path $In -Parent) "work"', session)   # the account copy stays on the machine
        self.assertIn('$gateOut = Join-Path $work "gate"', session)
        for pinned in ("a40aa274fd8598eab8c313ad111b65f5a6a3cc079ceeb2752484b1efc5a3972a",
                       "4947aed6d6c61a6d8da2ef6a57fa2315e0fc58783c7265546ffd7d2e51280883"):
            self.assertIn(pinned, prepare)

    def test_the_session_command_removes_only_its_own_large_folders_after_the_receipt(self):
        session = (Path(__file__).resolve().parent / "update_gate_bench" / "session.ps1").read_text(encoding="utf-8")
        cleanup = session[session.index("$session = [IO.Path]::GetFullPath((Split-Path $In -Parent))"):]
        self.assertLess(session.index('Copy-Item $receipt (Join-Path $Out "gate\\update-gate.json")'), session.index(cleanup))
        self.assertIn('foreach ($own in @((Join-Path $In "apps"), (Join-Path $In "msi"), $work))', cleanup)
        self.assertIn('-not $full.StartsWith($session + "\\", [StringComparison]::OrdinalIgnoreCase)', cleanup)
        self.assertIn('(Split-Path $full -Leaf) -notin @("apps", "msi", "work")', cleanup)
        self.assertIn("[IO.FileAttributes]::ReparsePoint", cleanup)   # never through a link
        self.assertEqual(cleanup.count("Delete("), 1)
        self.assertEqual(len(re.findall(r"Remove-Item|Delete\(|rd /s|rmdir", session)), 1, "the one removal is the guarded one")
        self.assertNotIn("$Connections", cleanup)   # the bench's account folder is never touched
        self.assertIn("while ($step.Length -gt $session.Length)", cleanup)   # no link anywhere below the session folder
        self.assertIn("$step = Split-Path $step -Parent", cleanup)

    def test_a_failed_gate_keeps_its_logs_and_screenshots(self):
        session = (Path(__file__).resolve().parent / "update_gate_bench" / "session.ps1").read_text(encoding="utf-8")
        keep = session.index('if ($code -ne 0 -and (Test-Path -LiteralPath $gateOut))')
        self.assertLess(keep, session.index("$session = [IO.Path]::GetFullPath((Split-Path $In -Parent))"))
        block = session[keep:session.index("# This run's own large folders go")]
        self.assertIn('$keep = Join-Path $Out "failed-gate"', block)
        self.assertIn("-Include *.out, *.log, *.png", block)   # logs and screenshots, never the phases' JSON

    def test_the_bench_run_script_stays_within_its_size(self):
        self.assertLessEqual(len(text("windows_final_runs.ps1").splitlines()), 500)


if __name__ == "__main__":
    unittest.main()
