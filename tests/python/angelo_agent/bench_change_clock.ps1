# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
<#
.SYNOPSIS
  What the Windows change-clock fix (angelo_change_clock) costs in the operations it touches, before vs after.

.DESCRIPTION
  Runs bench_change_clock.py (its docstring says what each operation drives and how the two arms differ) on the
  unattended Windows validation machine with CI's host Python, no Blender and no desktop session:
    1. refuses a tree that is not exactly the commit sent (windows_source_send.ps1; elsewhere nothing is checked);
    2. runs the bench from the source tree with PYTHONUTF8=1, writing -OutDir\change_clock.json, its
       change_clock.summary.txt and the console log change_clock.log, under a hard timeout of -MaxSeconds plus
       -GraceSeconds (the bench stops itself at -MaxSeconds; the timeout only catches a hang);
    3. prints the summary.
  Writes nothing into the source tree (no bytecode either). No provider, account or credential is involved.
#>
param(
    [string]$SourceDir = "C:\angelo\src",
    [string]$OutDir = "C:\angelo\cmd-out-bench",
    [int]$Pairs = 6,
    [int]$Reps = 30,
    [int]$MaxSeconds = 900,
    [int]$GraceSeconds = 300
)
# Native programs' stderr is output here, never a terminating error: the bench is judged by its exit code and JSON.
$ErrorActionPreference = "Continue"
. (Join-Path $SourceDir "scripts\angelo\windows_source_send.ps1")
$commit = Get-SentCommit $SourceDir   # "" on a tree no send made; refuses a tree that is not the commit sent.
New-Item -ItemType Directory -Force $OutDir | Out-Null
Set-Location $SourceDir
$env:PYTHONUTF8 = "1"                 # Readable logs; the add-on itself never relies on it.
$env:PYTHONDONTWRITEBYTECODE = "1"    # No __pycache__ left in the sent tree.
$python = "C:\Python313\python.exe"
if (-not (Test-Path $python)) { $python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source }
if (-not $python) { Write-Host "FAIL  no Python: install CI's host Python 3.13 (windows_agent_validate.ps1)"; exit 2 }

$out = Join-Path $OutDir "change_clock.json"
$log = Join-Path $OutDir "change_clock.log"
$summary = Join-Path $OutDir "change_clock.summary.txt"
Remove-Item $out, $summary -ErrorAction SilentlyContinue   # A stale result can never read as this run's.
$arguments = "`"tests\python\angelo_agent\bench_change_clock.py`" --out `"$out`" --pairs $Pairs --reps $Reps --max-seconds $MaxSeconds"
if ($commit) { $arguments += " --commit $commit" }
# cmd.exe writes the program's own bytes (UTF-8: PYTHONUTF8) to the log, where PowerShell 5 would re-encode them; the
# command goes through a batch file, as PowerShell 5 passes embedded quotes to cmd.exe unescaped.
$batch = Join-Path $env:TEMP ("angelo-bench-{0}.cmd" -f [guid]::NewGuid().ToString("N"))
[System.IO.File]::WriteAllText($batch, "@echo off`r`ncd /d `"$SourceDir`"`r`n`"$python`" $arguments > `"$log`" 2>&1`r`nexit /b %ERRORLEVEL%`r`n", (New-Object System.Text.UTF8Encoding($false)))
Write-Host ("bench: {0} pairs x {1} reps, cap {2} s (+{3} s hard timeout), commit {4}" -f $Pairs, $Reps, $MaxSeconds, $GraceSeconds, $(if ($commit) { $commit } else { "(not a sent tree)" }))
$process = Start-Process -FilePath "cmd.exe" -ArgumentList @("/d", "/c", "`"$batch`"") -WorkingDirectory $SourceDir -NoNewWindow -PassThru
$null = $process.Handle   # PowerShell 5 reports no ExitCode for a started process unless its handle was taken.
if ($process.WaitForExit(($MaxSeconds + $GraceSeconds) * 1000)) {
    $code = $process.ExitCode
} else {
    # By PID, the whole tree (cmd.exe, the bench and its arm processes): never by image name.
    & taskkill.exe /PID $process.Id /T /F | Out-Host
    $code = 124
    Write-Host ("FAIL  the bench was still running {0} s after its cap; killed" -f $GraceSeconds)
}
Remove-Item $batch -ErrorAction SilentlyContinue

if (Test-Path $summary) {
    Get-Content $summary -Encoding UTF8 | Out-Host
} else {
    Write-Host "no summary; the log's tail:"
    Get-Content $log -Tail 40 -Encoding UTF8 -ErrorAction SilentlyContinue | Out-Host
}
Write-Host ("bench exit {0}; results: {1}" -f $code, $out)
exit $code
