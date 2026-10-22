# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
<#
.SYNOPSIS
  The agent's Windows checks that need no desktop session, on the unattended validation machine.

.DESCRIPTION
  The 'agent' stage of windows_cloud_bootstrap.ps1 (doc/angelo/app_identity.rst, "Running it on Google Cloud").
  As the machine's administrator, without a signed-in desktop, it:
    1. installs CI's host Python (3.13 with numpy 2.3.4 and jsonschema 4.23.0, as .github/workflows/host-tests.yml)
       and Node.js LTS (for the CLI checks in the desktop session), from Chocolatey and PyPI;
    2. builds the Lite configuration incrementally (make.bat lite 2022b) with the options the macOS development
       build adds (OpenSubdiv, WebP), unless the identity stage already built it;
    3. runs the host suite with that Python (every test_native_*.py; skips are listed, not limited: the POSIX-only
       cases skip on Windows by design);
    4. runs the checkpoint and adversarial storage suites on their own, verbosely;
    5. runs windows_agent_checks.py ntfs: the Windows file backend against real NTFS (and FAT32 on a virtual disk);
    6. runs the BACKGROUND native suites against the built Angelo.exe.
  Each step's output and a summary go to -ResultsDir; agent-results.json lists PASS/FAIL per step.
  No provider, account or credential is involved.
#>
param(
    [string]$SourceDir = "C:\angelo\src",
    [string]$ResultsDir = "C:\angelo\results\agent",
    [switch]$SkipBuild,
    [int]$NativeJobs = 4
)
# Native programs' stderr (a Python warning, npm notices) is output here, never a terminating error: each step is
# judged by exit codes and reports, and every step runs.
$ErrorActionPreference = "Continue"
# A tree a send made must be exactly the commit it was sent (windows_source_send.ps1); elsewhere nothing is checked.
. (Join-Path $SourceDir "scripts\angelo\windows_source_send.ps1"); Assert-SentTree $SourceDir
New-Item -ItemType Directory -Force $ResultsDir | Out-Null
$results = New-Object System.Collections.ArrayList
function Add-Result([string]$Name, [string]$Status, [string]$Detail = "") {
    [void]$results.Add([pscustomobject]@{ Check = $Name; Status = $Status; Detail = $Detail })
    Write-Host ("{0,-5} {1}  {2}" -f $Status, $Name, $Detail)
}
function Test-Check([string]$Name, [scriptblock]$Body) {
    try { $detail = & $Body; Add-Result $Name "PASS" ([string]($detail | Out-String).Trim()) }
    catch { Add-Result $Name "FAIL" $_.Exception.Message }
}
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
# cmd.exe writes the program's own bytes (UTF-8: PYTHONUTF8) to the log, where PowerShell 5 would re-encode them; a
# program's stderr then cannot stop this script either. The log's tail is echoed for the transcript.
# The command goes through a batch file: PowerShell 5 passes embedded quotes to cmd.exe unescaped.
function Invoke-Logged([string]$Log, [string]$Command) {
    $batch = Join-Path $env:TEMP ("angelo-step-{0}.cmd" -f [guid]::NewGuid().ToString("N"))
    [System.IO.File]::WriteAllText($batch, "@echo off`r`n$Command > `"$Log`" 2>&1`r`nexit /b %ERRORLEVEL%`r`n", (New-Object System.Text.UTF8Encoding($false)))
    & cmd.exe /d /c $batch
    $code = $LASTEXITCODE
    Remove-Item $batch -ErrorAction SilentlyContinue
    Get-Content $Log -Tail 15 -Encoding UTF8 -ErrorAction SilentlyContinue | Out-Host
    return $code
}

$python = "C:\Python313\python.exe"
$tests = Join-Path $SourceDir "tests\python\angelo_agent"
$buildRoot = Split-Path $SourceDir -Parent
$env:PYTHONUTF8 = "1"   # Readable logs; the add-on itself never relies on it (child_process names UTF-8 itself).

Test-Check "agent: host Python 3.13 with numpy and jsonschema (as CI)" {
    if (-not (Test-Path $python)) {
        & choco install -y --no-progress python313 --params "/InstallDir:C:\Python313" | Out-Host
        Refresh-Path
    }
    if (-not (Test-Path $python)) { throw "Python 3.13 did not install at $python" }
    & $python -m pip install --disable-pip-version-check -q numpy==2.3.4 jsonschema==4.23.0 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "pip exited with $LASTEXITCODE" }
    (& $python -c "import sys; from importlib.metadata import version; print(sys.version.split()[0], 'numpy', version('numpy'), 'jsonschema', version('jsonschema'))")
}
Test-Check "agent: Node.js LTS (the npm route of the CLI checks)" {
    if (-not (Get-Command node.exe -ErrorAction SilentlyContinue)) {
        & choco install -y --no-progress nodejs-lts | Out-Host
        Refresh-Path
    }
    "node " + (& node.exe --version) + ", npm " + (& npm.cmd --version) + ", git " + (& git.exe --version)
}

$build = Get-ChildItem $buildRoot -Directory -Filter "build_windows_Lite*" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $SkipBuild) {
    # The macOS development build the native suites are measured on adds these to the Lite configuration
    # (scripts/angelo/build_native.sh); without OpenSubdiv the subdivision suites cannot run.
    if ($build -and (Test-Path (Join-Path $build.FullName "CMakeCache.txt")) -and
            -not (Select-String -Path (Join-Path $build.FullName "CMakeCache.txt") -Pattern "^WITH_OPENSUBDIV:BOOL=ON" -Quiet)) {
        Test-Check "agent: Lite with the development build's options (OpenSubdiv, WebP)" {
            $code = Invoke-Logged (Join-Path $ResultsDir "configure.log") "cmake `"$($build.FullName)`" -DWITH_OPENSUBDIV=ON -DWITH_IMAGE_WEBP=ON"
            if ($code -ne 0) { throw "cmake exited with $code (configure.log)" }
            "WITH_OPENSUBDIV=ON WITH_IMAGE_WEBP=ON"
        }
    }
    Test-Check "agent: make.bat lite 2022b (incremental)" {
        Push-Location $SourceDir
        try { $code = Invoke-Logged (Join-Path $ResultsDir "build.log") "call make.bat lite 2022b" } finally { Pop-Location }
        if ($code -ne 0) { throw "make.bat exited with $code (build.log)" }
        "built"
    }
    $build = Get-ChildItem $buildRoot -Directory -Filter "build_windows_Lite*" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
}
$exe = if ($build) { Join-Path $build.FullName "bin\Release\Angelo.exe" } else { "" }
Test-Check "agent: built Angelo.exe carries this source's add-on" {
    if (-not $exe -or -not (Test-Path $exe)) { throw "no Angelo.exe under $buildRoot\build_windows_Lite*\bin\Release" }
    $installed = Join-Path $build.FullName "bin\Release\5.2\scripts\addons_core\angelo_agent\windows_nt.py"
    $source = Join-Path $SourceDir "scripts\addons_core\angelo_agent\windows_nt.py"
    if ((Get-FileHash $installed).Hash -ne (Get-FileHash $source).Hash) { throw "the build's windows_nt.py differs from the source" }
    $exe
}
Set-Content (Join-Path $ResultsDir "binary.txt") $exe

Test-Check "agent: host suite (Windows, CI's Python)" {
    $log = Join-Path $ResultsDir "host-suite.log"
    # A hung test ends the step with every thread's stack (host-suite.log) instead of holding the machine.
    $code = Invoke-Logged $log "`"$python`" `"$(Join-Path $tests 'run_host_tests.py')`" --timeout 5400"
    $text = Get-Content $log -Raw -Encoding UTF8
    $ran = [regex]::Match($text, "Ran (\d+) tests").Groups[1].Value
    $verdict = [regex]::Match($text, "(OK|FAILED)( \([^)]*\))?\s*$", "Multiline").Value
    $skips = ([regex]::Matches($text, "^SKIPPED ", "Multiline")).Count
    if ($code -ne 0) { throw "exit $code; ran $ran; $verdict; $skips skipped (host-suite.log)" }
    "ran $ran; $verdict; $skips skipped"
}
Test-Check "agent: checkpoint and adversarial storage suites" {
    $log = Join-Path $ResultsDir "storage-suites.log"
    $patterns = "test_native_file_anchoring.py", "test_native_files_checkpoints.py", "test_native_checkpoint_policy.py",
                "test_native_recovery_acknowledgements.py", "test_native_ordered_recovery.py", "test_native_snapshot_*.py",
                "test_native_studio_export_checkpoints.py", "test_native_state_migration.py", "test_native_windows_*.py"
    $summary = @()
    $failed = $false
    foreach ($pattern in $patterns) {
        $code = Invoke-Logged ($log + ".part") "`"$python`" -m unittest discover -s `"$tests`" -t `"$tests`" -p $pattern -v"
        Get-Content ($log + ".part") -Encoding UTF8 | Add-Content $log -Encoding UTF8
        $text = Get-Content ($log + ".part") -Raw -Encoding UTF8
        $ran = [regex]::Match($text, "Ran (\d+) test").Groups[1].Value
        $skipped = ([regex]::Matches($text, "\.\.\. skipped")).Count
        $summary += "{0}: ran {1}, {2} skipped, exit {3}" -f $pattern, $ran, $skipped, $code
        if ($code -ne 0) { $failed = $true }
    }
    Remove-Item ($log + ".part") -ErrorAction SilentlyContinue
    if ($failed) { throw ($summary -join "; ") }
    $summary -join "; "
}
Test-Check "agent: Windows file backend on real NTFS" {
    $report = Join-Path $ResultsDir "ntfs.json"
    $env:ANGELO_NTFS_ROOT = "C:\angelo\ntfs"
    New-Item -ItemType Directory -Force $env:ANGELO_NTFS_ROOT | Out-Null
    $code = Invoke-Logged (Join-Path $ResultsDir "ntfs.log") "`"$python`" `"$(Join-Path $tests 'windows_agent_checks.py')`" ntfs --report `"$report`""
    if (-not (Test-Path $report)) { throw "no report (exit $code, ntfs.log)" }
    $data = Get-Content $report -Raw -Encoding UTF8 | ConvertFrom-Json
    $line = "{0} run, {1} failed, {2} errors, {3} skipped" -f $data.run, $data.failures, $data.errors, $data.skipped
    if ($code -ne 0) { throw "$line $($data.error)" }
    $line
}
Test-Check "agent: Windows file backend on real NTFS, inside Angelo.exe (its Python and manifest)" {
    $report = Join-Path $ResultsDir "ntfs-app.json"
    $code = Invoke-Logged (Join-Path $ResultsDir "ntfs-app.log") "`"$exe`" --background --factory-startup --python `"$(Join-Path $tests 'windows_agent_checks.py')`" -- ntfs --report `"$report`""
    if (-not (Test-Path $report)) { throw "no report (exit $code, ntfs-app.log)" }
    $data = Get-Content $report -Raw -Encoding UTF8 | ConvertFrom-Json
    $line = "{0} run, {1} failed, {2} errors, {3} skipped (Python {4})" -f $data.run, $data.failures, $data.errors, $data.skipped, $data.python
    if ($code -ne 0) { throw "$line $($data.error)" }
    $line
}
Test-Check "agent: BACKGROUND native suites on the built Angelo.exe" {
    if (-not $exe -or -not (Test-Path $exe)) { throw "no binary" }
    $output = "C:\angelo\native-background"
    Remove-Item $output -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $output | Out-Null
    $code = Invoke-Logged (Join-Path $ResultsDir "native-background.log") "`"$python`" `"$(Join-Path $tests 'run_native_suites.py')`" --binary `"$exe`" --jobs $NativeJobs --output `"$output`""
    $summary = Get-ChildItem $output -Recurse -Filter "summary.txt" | Select-Object -First 1
    if ($summary) { Copy-Item $summary.FullName (Join-Path $ResultsDir "native-background-summary.txt") -Force }
    # Each failed suite's reports and logs come back with the results, to diagnose without the machine.
    if ($summary) {
        foreach ($line in Get-Content $summary.FullName) {
            if ($line -match "^FAILED\s+\S+\s+\S+\s+(test_blender_\w+)") {
                $into = Join-Path $ResultsDir ("native-failed\" + $Matches[1])
                New-Item -ItemType Directory -Force $into | Out-Null
                $suite = Join-Path $summary.Directory.FullName $Matches[1]
                Copy-Item (Join-Path $suite "suite.log") $into -Force -ErrorAction SilentlyContinue
                Get-ChildItem (Join-Path $suite "qa") -Recurse -File -Include *.json, *.log, *.txt -ErrorAction SilentlyContinue |
                    Where-Object Length -lt 5MB | Copy-Item -Destination $into -Force
            }
        }
    }
    $last = if ($summary) { (Get-Content $summary.FullName | Select-Object -Last 3) -join " " } else { "no summary" }
    if ($code -ne 0) { throw "exit ${code}: $last" }
    $last
}

$json = $results | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path $ResultsDir "agent-results.json"), $json, (New-Object System.Text.UTF8Encoding($true)))
$failed = @($results | Where-Object Status -eq "FAIL").Count
Write-Host ("{0} agent checks: {1} failed" -f $results.Count, $failed)
exit ([int]($failed -gt 0))
